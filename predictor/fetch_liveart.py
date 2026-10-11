"""Fetch one artist's sales from LiveArt AI and dump to CSV.

Usage:
    export LIVEART_API_KEY=your_key
    python -m predictor.fetch_liveart --artist "René Portocarrero" --out data/portocarrero.csv

Appending to an existing file is the default — rerun with new artists to
build up a multi-artist CSV. --overwrite replaces the file instead.

The resulting CSV uses the canonical schema and can be uploaded directly
to the standalone index.html or dropped into sample_data/ for the
Streamlit app.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from predictor import schema as S
from predictor.data_sources import LiveArtClient

log = logging.getLogger(__name__)


def fetch_to_csv(
    artist: str,
    out_path: Path,
    overwrite: bool = False,
    client: LiveArtClient | None = None,
) -> int:
    """Fetch sales for one artist, write to CSV. Returns rows in the final file."""
    client = client or LiveArtClient.from_env()
    df = client.fetch(artist)
    if df.empty:
        return _row_count(out_path)

    if out_path.exists() and not overwrite:
        existing = S.validate(pd.read_csv(out_path))
        combined = pd.concat([existing, df], ignore_index=True, sort=False)
        combined = combined.drop_duplicates(
            subset=[S.ARTIST, S.SALE_DATE, S.AUCTION_HOUSE, S.LOT_NUMBER],
            keep="first",
        )
    else:
        combined = df

    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(out_path, index=False)
    return len(combined)


def _row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(pd.read_csv(path))
    except Exception:
        return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Fetch one artist's sales from LiveArt AI into a CSV.")
    p.add_argument("--artist", required=True, help="Artist name as it appears on LiveArt.")
    p.add_argument("--out", default="data/liveart.csv", help="Output CSV path (default: data/liveart.csv).")
    p.add_argument("--overwrite", action="store_true", help="Replace the file instead of appending + deduping.")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    try:
        total = fetch_to_csv(args.artist, Path(args.out), overwrite=args.overwrite)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Set LIVEART_API_KEY in .env or your shell before running.", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Fetch failed: {e}", file=sys.stderr)
        return 1

    print(f"Wrote {total} total rows to {args.out}")
    if total == 0:
        print(
            "0 rows — the artist probably has no sales under that exact name on "
            "LiveArt, or LIVEART_API_KEY is invalid. Verify at console.liveart.ai.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
