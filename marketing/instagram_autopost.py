#!/usr/bin/env python3
"""
instagram_autopost.py — publish one scheduled Instagram post per day.

Reads marketing/posts.csv and publishes the row whose `publish_date` is today
(America-agnostic: uses your machine's local date) to a connected Instagram
**Business or Creator** account via the Instagram Graph API. Hashtags in the
`first_comment` column are posted as the first comment on the new post.

This handles single-image feed posts — the most reliable Graph API flow. Reels
and multi-image carousels use different, heavier endpoints; rows whose format
isn't a plain image are published as a single image using `image_url` (point it
at one representative frame), and the script prints a note. See README.md.

--------------------------------------------------------------------------------
PREREQUISITES (one-time, see marketing/README.md for the full walkthrough):
  1. An Instagram **Business/Creator** account linked to a Facebook Page.
  2. A Meta app with the Instagram Graph API; a long-lived access token with
     instagram_basic, instagram_content_publish, pages_read_engagement.
  3. Your Instagram user id (the numeric IG id, not the @handle).
  4. Each image_url must be a PUBLIC https URL (Instagram fetches it server-side).

ENVIRONMENT VARIABLES (never hard-code secrets):
  IG_USER_ID         - numeric Instagram user id        (required)
  IG_ACCESS_TOKEN    - long-lived access token          (required)
  GRAPH_API_VERSION  - e.g. v21.0                        (optional, default below)

USAGE:
  python instagram_autopost.py                 # publish today's post
  python instagram_autopost.py --day 1         # publish the row with day == 1
  python instagram_autopost.py --date 2026-10-12
  python instagram_autopost.py --dry-run       # show what WOULD post, call nothing
  python instagram_autopost.py --csv path.csv  # use a different calendar file
--------------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install -r marketing/requirements.txt")

DEFAULT_GRAPH_VERSION = "v21.0"
HERE = Path(__file__).resolve().parent
DEFAULT_CSV = HERE / "posts.csv"
STATE_FILE = HERE / ".autopost_state.json"  # remembers what we already published

log = logging.getLogger("autopost")


# --------------------------------------------------------------------------- #
# State: avoid double-posting if the scheduler fires twice in one day.
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"published": {}}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError as e:
        log.warning("Could not write state file %s: %s", STATE_FILE, e)


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
def read_posts(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"No rows found in {csv_path}")
    return rows


def pick_row(rows: list[dict], *, day: str | None, on_date: str | None) -> dict | None:
    if day is not None:
        return next((r for r in rows if r.get("day", "").strip() == str(day).strip()), None)
    target = on_date or date.today().isoformat()
    return next((r for r in rows if r.get("publish_date", "").strip() == target), None)


def validate_row(row: dict) -> None:
    missing = [k for k in ("image_url", "caption") if not row.get(k, "").strip()]
    if missing:
        sys.exit(f"Row (day {row.get('day')}) missing required field(s): {', '.join(missing)}")
    url = row["image_url"].strip()
    if url.startswith("[") or not url.lower().startswith("https://"):
        sys.exit(
            f"image_url for day {row.get('day')} is still a placeholder or not an https URL: "
            f"{url!r}. Replace it with a PUBLIC https image URL before publishing."
        )
    caption = row["caption"]
    if "[" in caption and "]" in caption:
        log.warning(
            "Caption for day %s still contains [placeholders] — publishing anyway. "
            "Fill them in if that wasn't intended.", row.get("day"),
        )


# --------------------------------------------------------------------------- #
# Instagram Graph API
# --------------------------------------------------------------------------- #
class GraphError(RuntimeError):
    pass


def _graph_base(version: str) -> str:
    return f"https://graph.facebook.com/{version}"


def _post(url: str, data: dict, *, retries: int = 4) -> dict:
    """POST with exponential backoff on network/5xx errors."""
    delay = 2.0
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, data=data, timeout=60)
            if resp.status_code >= 500:
                raise GraphError(f"HTTP {resp.status_code}: {resp.text}")
            payload = resp.json()
            if "error" in payload:
                # 4xx (bad token, perms, placeholder url, etc.) — don't retry.
                raise GraphError(json.dumps(payload["error"]))
            return payload
        except (requests.RequestException, GraphError) as e:
            last_exc = e
            # Only retry transient conditions (network / 5xx), not API 4xx errors.
            transient = isinstance(e, requests.RequestException) or "HTTP 5" in str(e)
            if not transient or attempt == retries:
                raise
            log.warning("Attempt %d failed (%s); retrying in %.0fs", attempt, e, delay)
            time.sleep(delay)
            delay *= 2
    raise GraphError(str(last_exc))  # pragma: no cover


def create_media_container(base: str, ig_user_id: str, token: str,
                           image_url: str, caption: str) -> str:
    log.info("Creating media container…")
    payload = _post(
        f"{base}/{ig_user_id}/media",
        {"image_url": image_url, "caption": caption, "access_token": token},
    )
    creation_id = payload.get("id")
    if not creation_id:
        raise GraphError(f"No creation id returned: {payload}")
    return creation_id


def publish_media(base: str, ig_user_id: str, token: str, creation_id: str) -> str:
    log.info("Publishing media container %s…", creation_id)
    payload = _post(
        f"{base}/{ig_user_id}/media_publish",
        {"creation_id": creation_id, "access_token": token},
    )
    media_id = payload.get("id")
    if not media_id:
        raise GraphError(f"No media id returned on publish: {payload}")
    return media_id


def post_first_comment(base: str, media_id: str, token: str, message: str) -> None:
    if not message.strip():
        return
    log.info("Posting first comment (hashtags) on %s…", media_id)
    try:
        _post(f"{base}/{media_id}/comments", {"message": message, "access_token": token})
    except GraphError as e:
        # Non-fatal: the post is already live; a failed comment shouldn't abort.
        log.warning("Could not post first comment: %s", e)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Publish one scheduled Instagram post.")
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Path to posts.csv")
    ap.add_argument("--day", help="Publish the row whose 'day' column equals this.")
    ap.add_argument("--date", dest="on_date", help="Publish the row for YYYY-MM-DD.")
    ap.add_argument("--dry-run", action="store_true", help="Show the post; call no API.")
    ap.add_argument("--force", action="store_true", help="Publish even if already marked done.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    rows = read_posts(args.csv)
    row = pick_row(rows, day=args.day, on_date=args.on_date)
    if row is None:
        where = f"day={args.day}" if args.day else f"date={args.on_date or date.today().isoformat()}"
        log.info("No post scheduled for %s. Nothing to do.", where)
        return 0

    validate_row(row)
    key = f"{row.get('publish_date','')}#{row.get('day','')}"

    state = load_state()
    if not args.force and state["published"].get(key):
        log.info("Already published %s on %s — skipping. Use --force to repost.",
                 key, state["published"][key])
        return 0

    fmt = row.get("format", "").strip()
    if fmt and "image" not in fmt and "carousel" not in fmt:
        log.warning(
            "Row format is %r. This script publishes a SINGLE IMAGE; it will use "
            "image_url as one frame. For a true Reel/carousel, post that one manually "
            "or extend the script (see README).", fmt,
        )

    caption = row["caption"]
    first_comment = row.get("first_comment", "")

    print("\n" + "=" * 70)
    print(f"DAY {row.get('day')}  |  {row.get('publish_date')} {row.get('publish_time','')}  |  {fmt}")
    print("-" * 70)
    print(f"image_url     : {row['image_url']}")
    print(f"caption       :\n{caption}")
    print(f"first_comment : {first_comment}")
    print("=" * 70 + "\n")

    if args.dry_run:
        log.info("--dry-run set: no API calls made.")
        return 0

    ig_user_id = os.environ.get("IG_USER_ID")
    token = os.environ.get("IG_ACCESS_TOKEN")
    version = os.environ.get("GRAPH_API_VERSION", DEFAULT_GRAPH_VERSION)
    if not ig_user_id or not token:
        sys.exit("Set IG_USER_ID and IG_ACCESS_TOKEN environment variables (see README.md).")

    base = _graph_base(version)
    try:
        creation_id = create_media_container(base, ig_user_id, token, row["image_url"], caption)
        media_id = publish_media(base, ig_user_id, token, creation_id)
        post_first_comment(base, media_id, token, first_comment)
    except GraphError as e:
        log.error("Instagram publish failed: %s", e)
        return 1

    log.info("✅ Published! media_id=%s", media_id)
    state["published"][key] = date.today().isoformat()
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
