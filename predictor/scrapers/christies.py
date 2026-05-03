"""Christie's past-results scraper.

WARNING: scraping christies.com is against their TOS. This module is for
personal-use research only. Keep request volume low (the defaults are
1 req/sec with jitter and a hard per-session cap). Do NOT run this
unattended on a schedule, do NOT scrape on behalf of others, and do NOT
redistribute the data.

Usage (CLI):
    python -m predictor.scrapers.christies \\
        --artist "Gerhard Richter" --pages 5 \\
        --out data/richter.csv

    python -m predictor.scrapers.christies \\
        --artist "Gerhard Richter" --dump-html debug.html

The selectors at the top of this file are best-guess. If your scrape
returns 0 rows, run with --dump-html, open the file, and update the
SELECTORS block below to match Christie's current markup.
"""
from __future__ import annotations

import argparse
import csv
import logging
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus

import httpx
import pandas as pd
from bs4 import BeautifulSoup, Tag

from predictor import schema as S
from predictor.data_sources import AuctionDataSource, _parse_dimensions

log = logging.getLogger(__name__)

BASE = "https://www.christies.com"
SEARCH_PATH = "/en/results"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# SELECTORS  ←  if the parser returns 0 rows, edit these to match the live
# markup (use --dump-html to inspect what Christie's actually returns).
# ---------------------------------------------------------------------------
SELECTORS = {
    # cards on the search results page
    "card": "article.chr-lot-tile, div.chr-lot-tile, li.chr-lot-tile, [data-test='lot-tile']",
    "card_link": "a[href*='/lot/']",
    "card_title": ".chr-lot-tile__title, [data-test='lot-title'], h3, h4",
    "card_artist": ".chr-lot-tile__artist, [data-test='artist-name']",
    "card_price": ".chr-lot-tile__price, [data-test='lot-price'], .price-realized",
    "card_estimate": ".chr-lot-tile__estimate, [data-test='lot-estimate'], .estimate",
    "card_sale_info": ".chr-lot-tile__sale-info, [data-test='sale-info'], .sale-info",
    # detail page (one lot)
    "detail_title": "h1, .chr-lot-detail__title",
    "detail_artist": ".chr-lot-detail__artist, [data-test='artist-name']",
    "detail_medium_year": ".chr-lot-detail__medium, .lot-description",
    "detail_dimensions": ".chr-lot-detail__dimensions, .dimensions",
    "detail_price": ".chr-lot-detail__price-realized, .price-realized, [data-test='price-realized']",
    "detail_estimate": ".chr-lot-detail__estimate, .estimate, [data-test='estimate']",
    "detail_sale_title": ".chr-lot-detail__sale-title, .sale-title",
    "detail_sale_date": ".chr-lot-detail__sale-date, .sale-date, time",
    "detail_sale_location": ".chr-lot-detail__location, .sale-location",
    "detail_provenance": ".chr-lot-detail__provenance, .provenance",
    "detail_exhibited": ".chr-lot-detail__exhibited, .exhibited",
    "detail_literature": ".chr-lot-detail__literature, .literature",
}

PRICE_RE = re.compile(r"(?:USD|US\$|\$)\s*([\d,]+)", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")
DATE_RE = re.compile(
    r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})"  # 14 May 2023
)
MONTHS = {
    m: i for i, m in enumerate(
        ["january","february","march","april","may","june",
         "july","august","september","october","november","december"], start=1
    )
}


@dataclass
class LotSummary:
    url: str
    title: str | None
    artist: str | None
    price_text: str | None
    sale_info: str | None


class CloudflareBlocked(RuntimeError):
    pass


class ChristiesScraper:
    def __init__(
        self,
        rate_limit_seconds: float = 1.0,
        jitter: float = 0.5,
        max_requests: int = 200,
        timeout: float = 30.0,
        cookies: dict[str, str] | None = None,
    ):
        self.rate_limit_seconds = rate_limit_seconds
        self.jitter = jitter
        self.max_requests = max_requests
        self._req_count = 0
        self._last_req_at = 0.0
        self.client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
            follow_redirects=True,
            cookies=cookies or {},
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "ChristiesScraper":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- HTTP ----------------------------------------------------------

    def _sleep_for_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_req_at
        wait = self.rate_limit_seconds - elapsed
        wait += random.uniform(0, self.jitter)
        if wait > 0:
            time.sleep(wait)

    def _get(self, url: str) -> str:
        if self._req_count >= self.max_requests:
            raise RuntimeError(
                f"Hit per-session request cap ({self.max_requests}). "
                "Bump --max-requests if you really mean to."
            )
        self._sleep_for_rate_limit()
        for attempt in range(4):
            r = self.client.get(url)
            self._last_req_at = time.monotonic()
            self._req_count += 1
            if r.status_code in (429, 503):
                wait = 2 ** attempt
                log.warning("HTTP %s on %s — backing off %ds", r.status_code, url, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            text = r.text
            if _looks_like_cloudflare_challenge(text):
                raise CloudflareBlocked(
                    "Christie's returned a Cloudflare challenge. Open the URL "
                    "in a browser, complete the challenge, copy the cf_clearance "
                    "cookie, and pass it via --cookie cf_clearance=..."
                )
            return text
        raise RuntimeError(f"Repeated 429/503 for {url}")

    # ---- Search --------------------------------------------------------

    def search_url(self, artist: str, page: int) -> str:
        # Christie's results endpoint. Param names are the most common; if
        # the page returns generic results, check the live URL on a manual
        # search and adjust the keys here.
        return (
            f"{BASE}{SEARCH_PATH}"
            f"?keywords={quote_plus(artist)}"
            f"&filterspagenumber={page}"
            f"&sortby=lotsalesdate-desc"
        )

    def fetch_search_page(self, artist: str, page: int) -> list[LotSummary]:
        html = self._get(self.search_url(artist, page))
        return parse_search_page(html)

    def fetch_lot_detail(self, lot_url: str) -> dict:
        if lot_url.startswith("/"):
            lot_url = BASE + lot_url
        html = self._get(lot_url)
        return parse_lot_detail(html, lot_url)

    def scrape(
        self,
        artist: str,
        pages: int = 5,
        with_details: bool = True,
    ) -> list[dict]:
        rows: list[dict] = []
        for page in range(1, pages + 1):
            try:
                summaries = self.fetch_search_page(artist, page)
            except CloudflareBlocked:
                raise
            except Exception as e:
                log.warning("Search page %d failed: %s", page, e)
                break
            if not summaries:
                log.info("No more results at page %d, stopping.", page)
                break
            log.info("Page %d: %d lots", page, len(summaries))
            for summary in summaries:
                if with_details and summary.url:
                    try:
                        detail = self.fetch_lot_detail(summary.url)
                    except Exception as e:
                        log.warning("Detail fetch failed for %s: %s", summary.url, e)
                        detail = {}
                else:
                    detail = {}
                row = _summary_and_detail_to_canonical(summary, detail, artist_query=artist)
                if row:
                    rows.append(row)
        return rows


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _looks_like_cloudflare_challenge(html: str) -> bool:
    snippet = html[:5000].lower()
    return (
        "just a moment" in snippet
        or "cf-challenge" in snippet
        or "checking your browser" in snippet
    )


def _select_text(node: Tag | None, selector: str) -> str | None:
    if node is None:
        return None
    el = node.select_one(selector)
    if el is None:
        return None
    return el.get_text(" ", strip=True) or None


def _try_selectors(node: Tag | BeautifulSoup, key: str) -> Tag | None:
    """Selectors are comma-separated lists; return first match."""
    sel = SELECTORS.get(key, "")
    if not sel:
        return None
    return node.select_one(sel)


def parse_search_page(html: str) -> list[LotSummary]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(SELECTORS["card"])
    out: list[LotSummary] = []
    for card in cards:
        link = card.select_one(SELECTORS["card_link"])
        href = link["href"] if link and link.has_attr("href") else None
        if href and href.startswith("/"):
            href = BASE + href
        title = _select_text(card, SELECTORS["card_title"])
        artist = _select_text(card, SELECTORS["card_artist"])
        price = _select_text(card, SELECTORS["card_price"])
        sale_info = _select_text(card, SELECTORS["card_sale_info"])
        if href is None and title is None:
            continue
        out.append(
            LotSummary(
                url=href or "",
                title=title,
                artist=artist,
                price_text=price,
                sale_info=sale_info,
            )
        )
    return out


def parse_lot_detail(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = _select_text(soup, SELECTORS["detail_title"])
    artist = _select_text(soup, SELECTORS["detail_artist"])
    medium_year = _select_text(soup, SELECTORS["detail_medium_year"])
    dimensions = _select_text(soup, SELECTORS["detail_dimensions"])
    price_text = _select_text(soup, SELECTORS["detail_price"])
    sale_title = _select_text(soup, SELECTORS["detail_sale_title"])
    sale_date_text = _select_text(soup, SELECTORS["detail_sale_date"])
    sale_location = _select_text(soup, SELECTORS["detail_sale_location"])
    provenance = _select_text(soup, SELECTORS["detail_provenance"])
    exhibited_text = _select_text(soup, SELECTORS["detail_exhibited"])
    literature_text = _select_text(soup, SELECTORS["detail_literature"])
    return {
        "url": url,
        "title": title,
        "artist": artist,
        "medium_year_text": medium_year,
        "dimensions_text": dimensions,
        "price_text": price_text,
        "sale_title": sale_title,
        "sale_date_text": sale_date_text,
        "sale_location": sale_location,
        "provenance_text": provenance,
        "exhibited_text": exhibited_text,
        "literature_text": literature_text,
    }


def _parse_price_usd(text: str | None) -> float:
    if not text:
        return 0.0
    m = PRICE_RE.search(text.replace(",", ","))
    if not m:
        # Try a more permissive number pull
        nums = re.findall(r"\$\s*([\d,]+)", text)
        if not nums:
            return 0.0
        m_str = nums[0]
    else:
        m_str = m.group(1)
    try:
        return float(m_str.replace(",", ""))
    except ValueError:
        return 0.0


def _parse_date(text: str | None) -> str | None:
    if not text:
        return None
    m = DATE_RE.search(text)
    if not m:
        # ISO 8601 fallback
        iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        return iso.group(1) if iso else None
    day, month, year = m.group(1), m.group(2).lower(), m.group(3)
    mn = MONTHS.get(month)
    if not mn:
        return None
    return f"{year}-{mn:02d}-{int(day):02d}"


def _parse_year(text: str | None) -> int | None:
    if not text:
        return None
    m = YEAR_RE.search(text)
    return int(m.group(1)) if m else None


def _parse_medium(text: str | None) -> str:
    """Scan free-text medium descriptions for known tokens.

    Christie's lot text looks like 'oil on canvas, 2010' — too noisy for the
    strict normalize_medium aliases, so we look for substrings.
    """
    if not text:
        return "other"
    t = text.lower()
    if "oil" in t:
        return "oil"
    if "acrylic" in t:
        return "acrylic"
    if "watercolor" in t or "watercolour" in t or "ink on paper" in t or "drawing" in t:
        return "works_on_paper"
    if "lithograph" in t or "screenprint" in t or "etching" in t or "silkscreen" in t or "print" in t:
        return "print"
    if "bronze" in t or "marble" in t or "sculpture" in t:
        return "sculpture"
    if "photograph" in t:
        return "photograph"
    if "mixed media" in t:
        return "mixed_media"
    return S.normalize_medium(text)


def _infer_sale_type(sale_title: str | None) -> str:
    if not sale_title:
        return "day"
    return S.normalize_sale_type(sale_title)


def _provenance_score(text: str | None) -> int:
    if not text:
        return 0
    t = text.lower()
    if "museum" in t or "estate of" in t:
        return 3
    if "collection of" in t or "private collection" in t:
        return 2
    if "gallery" in t or "acquired from" in t:
        return 1
    return 0


def _summary_and_detail_to_canonical(
    summary: LotSummary,
    detail: dict,
    artist_query: str,
) -> dict | None:
    title = detail.get("title") or summary.title or ""
    artist = detail.get("artist") or summary.artist or artist_query
    price_usd = _parse_price_usd(detail.get("price_text") or summary.price_text)
    if price_usd <= 0:
        return None
    medium_year_text = detail.get("medium_year_text") or ""
    dimensions_text = detail.get("dimensions_text") or ""
    h, w, d = _parse_dimensions(dimensions_text)
    if h == 0 or w == 0:
        return None
    sale_date = _parse_date(detail.get("sale_date_text") or summary.sale_info)
    if not sale_date:
        return None
    return {
        S.ARTIST: artist.strip(),
        S.TITLE: title.strip(),
        S.MEDIUM: _parse_medium(medium_year_text),
        S.HEIGHT_CM: h,
        S.WIDTH_CM: w,
        S.DEPTH_CM: d,
        S.YEAR_CREATED: _parse_year(medium_year_text) or 0,
        S.SALE_DATE: sale_date,
        S.SALE_PRICE_USD: price_usd,
        S.AUCTION_HOUSE: "Christie's",
        S.SALE_TYPE: _infer_sale_type(detail.get("sale_title")),
        S.LOCATION: (detail.get("sale_location") or "").lower() or "other",
        S.LOT_NUMBER: "",
        S.PROVENANCE_SCORE: _provenance_score(detail.get("provenance_text")),
        S.EXHIBITED: bool(detail.get("exhibited_text")),
        S.PUBLISHED: bool(detail.get("literature_text")),
        S.CONDITION: 4,
        S.SIGNED: False,
        S.SERIES: "",
    }


# ---------------------------------------------------------------------------
# AuctionDataSource adapter — lets the Streamlit app use the scraper too.
# ---------------------------------------------------------------------------

class ChristiesSource(AuctionDataSource):
    name = "christies"

    def __init__(self, pages: int = 5, with_details: bool = True, **scraper_kwargs):
        self.pages = pages
        self.with_details = with_details
        self.scraper_kwargs = scraper_kwargs

    def fetch(self, artist: str) -> pd.DataFrame:
        with ChristiesScraper(**self.scraper_kwargs) as sc:
            rows = sc.scrape(artist, pages=self.pages, with_details=self.with_details)
        if not rows:
            return pd.DataFrame(columns=S.ALL_COLUMNS)
        return S.validate(pd.DataFrame(rows))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _write_csv(rows: Iterable[dict], path: Path) -> int:
    rows = list(rows)
    if not rows:
        return 0
    df = S.validate(pd.DataFrame(rows))
    if path.exists():
        existing = pd.read_csv(path)
        df = pd.concat([existing, df], ignore_index=True).drop_duplicates()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return len(df)


def _parse_cookie_args(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Scrape Christie's past results for one artist.")
    p.add_argument("--artist", required=True, help="Artist name, e.g. 'Gerhard Richter'")
    p.add_argument("--pages", type=int, default=5, help="Number of result pages to scrape")
    p.add_argument("--out", default="data/scraped.csv", help="Output CSV path")
    p.add_argument("--no-details", action="store_true", help="Skip per-lot detail page fetches (faster, less data)")
    p.add_argument("--rate", type=float, default=1.0, help="Min seconds between requests")
    p.add_argument("--max-requests", type=int, default=200, help="Hard cap per run")
    p.add_argument(
        "--cookie",
        action="append",
        help="Cookie 'name=value' (repeatable). Use to pass cf_clearance after solving a Cloudflare challenge.",
    )
    p.add_argument("--dump-html", help="Fetch ONE search page and save its HTML here, then exit.")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    cookies = _parse_cookie_args(args.cookie)

    with ChristiesScraper(
        rate_limit_seconds=args.rate,
        max_requests=args.max_requests,
        cookies=cookies,
    ) as sc:
        if args.dump_html:
            html = sc._get(sc.search_url(args.artist, 1))
            Path(args.dump_html).write_text(html, encoding="utf-8")
            print(f"Wrote {len(html):,} bytes to {args.dump_html}")
            return 0

        try:
            rows = sc.scrape(args.artist, pages=args.pages, with_details=not args.no_details)
        except CloudflareBlocked as e:
            print(f"BLOCKED: {e}", file=sys.stderr)
            return 2

    n = _write_csv(rows, Path(args.out))
    print(f"Wrote {n} total rows to {args.out} (this run scraped {len(rows)} new lots)")
    if n == 0:
        print(
            "Got 0 rows — the SELECTORS in predictor/scrapers/christies.py probably "
            "don't match Christie's current markup. Re-run with --dump-html debug.html, "
            "open the file, and update SELECTORS to match.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
