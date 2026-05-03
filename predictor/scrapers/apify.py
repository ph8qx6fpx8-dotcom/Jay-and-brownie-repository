"""Apify-actor data source.

Apify (apify.com) hosts community-maintained scrapers (called "actors") that
handle anti-bot bypass, IP rotation, and Cloudflare for you. You pay per
compute unit. This adapter calls one actor with whatever input it expects,
waits for it to finish, and maps the output dataset into our canonical
schema.

Default config targets `parseforge/artsy-scraper` mode=artworks. IMPORTANT:
that actor returns Artsy GALLERY LISTINGS (asking prices), not realized
auction prices. The hedonic predictor was designed for realized prices —
results from asking-price data will skew high and miss auction dynamics
(evening vs day premium, hammer-vs-premium, etc.).

For realized auction data via Apify, swap `actor_id` and `field_map` to point
at a different actor (search the Apify Store for "auction results",
"Christie's", "Sotheby's", etc.).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from predictor import schema as S
from predictor.data_sources import AuctionDataSource, _parse_dimensions

DEFAULT_ACTOR_ID = "parseforge/artsy-scraper"


def _get(item: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in item and item[k] not in (None, ""):
            return item[k]
    return default


def _parse_price_any(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    import re

    nums = re.findall(r"[\d,]+(?:\.\d+)?", text)
    if not nums:
        return 0.0
    candidate = max(nums, key=len).replace(",", "")
    try:
        return float(candidate)
    except ValueError:
        return 0.0


@dataclass
class ApifyMapping:
    """Field map from one Apify actor's output to our canonical schema.

    Each field is a callable(item) -> value, so you can compute or fall back.
    Defaults below match `parseforge/artsy-scraper` (Artsy gallery listings).
    """

    artist: Callable[[dict], str] = lambda i: _get(i, "artist", "artistName", default="") or ""
    title: Callable[[dict], str] = lambda i: _get(i, "title", "name", default="") or ""
    medium: Callable[[dict], str] = lambda i: S.normalize_medium(
        _get(i, "medium", "category", "mediumCategory", default="other") or "other"
    )
    dimensions_text: Callable[[dict], str] = lambda i: _get(
        i, "dimensions", "size", "dimensionsCm", default=""
    ) or ""
    year_created: Callable[[dict], int] = lambda i: int(
        _get(i, "yearCreated", "year", "creationYear", default=0) or 0
    )
    sale_date: Callable[[dict], str] = lambda i: _get(
        i, "saleDate", "auctionDate", "scrapedAt", default=""
    ) or ""
    price_usd: Callable[[dict], float] = lambda i: _parse_price_any(
        _get(i, "priceUSD", "salePriceUSD", "price", "askingPrice")
    )
    auction_house: Callable[[dict], str] = lambda i: _get(
        i, "auctionHouse", "house", "gallery", default=""
    ) or ""
    sale_type: Callable[[dict], str] = lambda i: S.normalize_sale_type(
        _get(i, "saleType", "auctionType", default="day") or "day"
    )
    location: Callable[[dict], str] = lambda i: (
        _get(i, "location", "city", default="other") or "other"
    ).lower()


class ApifySource(AuctionDataSource):
    """Run an Apify actor and map its dataset into canonical schema."""

    name = "apify"

    def __init__(
        self,
        api_token: str,
        actor_id: str = DEFAULT_ACTOR_ID,
        mode: str = "artworks",
        max_items: int = 100,
        for_sale: bool = True,
        extra_input: dict | None = None,
        mapping: ApifyMapping | None = None,
    ):
        self.api_token = api_token
        self.actor_id = actor_id
        self.mode = mode
        self.max_items = max_items
        self.for_sale = for_sale
        self.extra_input = extra_input or {}
        self.mapping = mapping or ApifyMapping()

    @classmethod
    def from_env(
        cls,
        actor_id: str = DEFAULT_ACTOR_ID,
        max_items: int = 100,
    ) -> "ApifySource":
        token = os.environ.get("APIFY_API_TOKEN")
        if not token:
            raise RuntimeError("Set APIFY_API_TOKEN in environment")
        return cls(api_token=token, actor_id=actor_id, max_items=max_items)

    def _build_input(self, artist: str) -> dict:
        return {
            "mode": self.mode,
            "keyword": artist,
            "maxItems": self.max_items,
            "forSale": self.for_sale,
            **self.extra_input,
        }

    def _run_actor(self, run_input: dict) -> list[dict]:
        # Imported lazily so the rest of the package doesn't require apify-client.
        from apify_client import ApifyClient

        client = ApifyClient(self.api_token)
        run = client.actor(self.actor_id).call(run_input=run_input)
        if not run or "defaultDatasetId" not in run:
            return []
        dataset = client.dataset(run["defaultDatasetId"])
        return list(dataset.iterate_items())

    def fetch(self, artist: str) -> pd.DataFrame:
        items = self._run_actor(self._build_input(artist))
        rows: list[dict] = []
        for item in items:
            rows.append(self._map_item(item, artist))
        if not rows:
            return pd.DataFrame(columns=S.ALL_COLUMNS)
        df = pd.DataFrame(rows)
        # Drop rows the schema would reject (no price / no dimensions / no date).
        df = df[
            (df[S.SALE_PRICE_USD] > 0)
            & (df[S.HEIGHT_CM] > 0)
            & (df[S.WIDTH_CM] > 0)
            & (df[S.SALE_DATE].astype(str).str.len() > 0)
        ]
        if df.empty:
            return pd.DataFrame(columns=S.ALL_COLUMNS)
        return S.validate(df)

    def _map_item(self, item: dict, artist_query: str) -> dict:
        m = self.mapping
        h, w, d = _parse_dimensions(m.dimensions_text(item))
        return {
            S.ARTIST: m.artist(item).strip() or artist_query,
            S.TITLE: m.title(item).strip(),
            S.MEDIUM: m.medium(item),
            S.HEIGHT_CM: h,
            S.WIDTH_CM: w,
            S.DEPTH_CM: d,
            S.YEAR_CREATED: m.year_created(item),
            S.SALE_DATE: m.sale_date(item),
            S.SALE_PRICE_USD: m.price_usd(item),
            S.AUCTION_HOUSE: m.auction_house(item).strip() or "Unknown",
            S.SALE_TYPE: m.sale_type(item),
            S.LOCATION: m.location(item),
            S.LOT_NUMBER: "",
            S.PROVENANCE_SCORE: 0,
            S.EXHIBITED: False,
            S.PUBLISHED: False,
            S.CONDITION: 4,
            S.SIGNED: False,
            S.SERIES: "",
        }
