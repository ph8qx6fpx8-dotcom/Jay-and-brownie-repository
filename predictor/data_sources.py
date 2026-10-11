"""Auction data sources. All implementations return the canonical schema."""
from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable

import httpx
import pandas as pd

from predictor import schema as S


class AuctionDataSource(ABC):
    name: str = "abstract"

    @abstractmethod
    def fetch(self, artist: str) -> pd.DataFrame:
        """Return a DataFrame of sales for `artist`, in canonical schema."""


class CsvSource(AuctionDataSource):
    name = "csv"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self._df = S.validate(pd.read_csv(self.path))

    def fetch(self, artist: str) -> pd.DataFrame:
        return self._df[self._df[S.ARTIST] == artist].reset_index(drop=True)

    def all_artists(self) -> list[str]:
        return sorted(self._df[S.ARTIST].dropna().unique().tolist())

    def all_data(self) -> pd.DataFrame:
        return self._df.copy()


_DIM_RE = re.compile(
    r"(?P<h>\d+(?:[.,]\d+)?)\s*[x×]\s*(?P<w>\d+(?:[.,]\d+)?)"
    r"(?:\s*[x×]\s*(?P<d>\d+(?:[.,]\d+)?))?\s*(?P<unit>cm|in|inches)?",
    re.IGNORECASE,
)


def _parse_dimensions(text: str) -> tuple[float, float, float]:
    if not text:
        return (0.0, 0.0, 0.0)
    m = _DIM_RE.search(str(text))
    if not m:
        return (0.0, 0.0, 0.0)
    h = float(m.group("h").replace(",", "."))
    w = float(m.group("w").replace(",", "."))
    d = float(m.group("d").replace(",", ".")) if m.group("d") else 0.0
    if (m.group("unit") or "cm").lower() in ("in", "inches"):
        h, w, d = h * 2.54, w * 2.54, d * 2.54
    return (h, w, d)


class _HttpClient(AuctionDataSource):
    """Shared HTTP machinery for partner-API clients."""

    base_url: str
    api_key: str
    timeout: float = 30.0

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        with httpx.Client(timeout=self.timeout) as client:
            r = client.get(url, params=params, headers=headers)
            r.raise_for_status()
            return r.json()

    @staticmethod
    def _to_dataframe(rows: Iterable[dict]) -> pd.DataFrame:
        df = pd.DataFrame(list(rows), columns=S.ALL_COLUMNS)
        if df.empty:
            return df
        return S.validate(df)


class ArtnetClient(_HttpClient):
    """Adapter for Artnet's partner Price Database API.

    Artnet is partner-only; the request shape varies by contract. The endpoint
    path and field names below are the most commonly documented; if your
    partner docs differ, adjust `path`, `params`, and `_map_lot`.
    """

    name = "artnet"

    def __init__(self, api_key: str, base_url: str, timeout: float = 30.0):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "ArtnetClient":
        key = os.environ.get("ARTNET_API_KEY")
        url = os.environ.get("ARTNET_BASE_URL")
        if not key or not url:
            raise RuntimeError("Set ARTNET_API_KEY and ARTNET_BASE_URL in environment")
        return cls(api_key=key, base_url=url)

    def fetch(self, artist: str) -> pd.DataFrame:
        rows: list[dict] = []
        page = 1
        while True:
            data = self._get(
                "price-database/lots",
                {"artist": artist, "page": page, "page_size": 100},
            )
            lots = data.get("results") or data.get("lots") or []
            if not lots:
                break
            rows.extend(self._map_lot(l, artist) for l in lots)
            if not data.get("next") and len(lots) < 100:
                break
            page += 1
        return self._to_dataframe(rows)

    @staticmethod
    def _map_lot(lot: dict, artist: str) -> dict:
        h = float(lot.get("height_cm") or 0)
        w = float(lot.get("width_cm") or 0)
        d = float(lot.get("depth_cm") or 0)
        if not (h and w):
            h, w, d = _parse_dimensions(lot.get("dimensions") or "")
        return {
            S.ARTIST: lot.get("artist_name") or artist,
            S.TITLE: lot.get("title") or "",
            S.MEDIUM: S.normalize_medium(lot.get("medium") or lot.get("category") or "other"),
            S.HEIGHT_CM: h,
            S.WIDTH_CM: w,
            S.DEPTH_CM: d,
            S.YEAR_CREATED: lot.get("year_created") or lot.get("year"),
            S.SALE_DATE: lot.get("sale_date"),
            S.SALE_PRICE_USD: lot.get("price_realized_usd")
            or lot.get("hammer_price_usd")
            or 0,
            S.AUCTION_HOUSE: lot.get("auction_house") or "",
            S.SALE_TYPE: S.normalize_sale_type(lot.get("sale_type") or "day"),
            S.LOCATION: (lot.get("location") or "other").lower(),
            S.LOT_NUMBER: lot.get("lot_number") or "",
            S.PROVENANCE_SCORE: int(lot.get("provenance_score") or 0),
            S.EXHIBITED: bool(lot.get("exhibited")),
            S.PUBLISHED: bool(lot.get("published")),
            S.CONDITION: int(lot.get("condition") or 4),
            S.SIGNED: bool(lot.get("signed")),
            S.SERIES: lot.get("series") or "",
        }


class MutualArtClient(_HttpClient):
    """Adapter for MutualArt's partner auction-results endpoint.

    Like Artnet, MutualArt's API is partner-only and field names vary by
    contract. The mapping below handles their common payload shape:
    `dimensions` as a single string, `mediumCategory`, `salePriceUSD`, etc.
    """

    name = "mutualart"

    def __init__(self, api_key: str, base_url: str, timeout: float = 30.0):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "MutualArtClient":
        key = os.environ.get("MUTUALART_API_KEY")
        url = os.environ.get("MUTUALART_BASE_URL")
        if not key or not url:
            raise RuntimeError(
                "Set MUTUALART_API_KEY and MUTUALART_BASE_URL in environment"
            )
        return cls(api_key=key, base_url=url)

    def fetch(self, artist: str) -> pd.DataFrame:
        rows: list[dict] = []
        offset = 0
        page_size = 100
        while True:
            data = self._get(
                "auction-results",
                {"artistName": artist, "offset": offset, "limit": page_size},
            )
            lots = data.get("items") or data.get("results") or []
            if not lots:
                break
            rows.extend(self._map_lot(l, artist) for l in lots)
            if len(lots) < page_size:
                break
            offset += page_size
        return self._to_dataframe(rows)

    @staticmethod
    def _map_lot(lot: dict, artist: str) -> dict:
        h, w, d = _parse_dimensions(lot.get("dimensions") or "")
        return {
            S.ARTIST: lot.get("artistName") or artist,
            S.TITLE: lot.get("title") or "",
            S.MEDIUM: S.normalize_medium(
                lot.get("mediumCategory") or lot.get("medium") or "other"
            ),
            S.HEIGHT_CM: h,
            S.WIDTH_CM: w,
            S.DEPTH_CM: d,
            S.YEAR_CREATED: lot.get("yearCreated") or lot.get("creationYear"),
            S.SALE_DATE: lot.get("saleDate") or lot.get("auctionDate"),
            S.SALE_PRICE_USD: lot.get("salePriceUSD")
            or lot.get("hammerPriceUSD")
            or 0,
            S.AUCTION_HOUSE: lot.get("auctionHouse") or lot.get("house") or "",
            S.SALE_TYPE: S.normalize_sale_type(
                lot.get("saleType") or lot.get("category") or "day"
            ),
            S.LOCATION: (lot.get("city") or lot.get("location") or "other").lower(),
            S.LOT_NUMBER: lot.get("lotNumber") or "",
            S.PROVENANCE_SCORE: int(lot.get("provenanceScore") or 0),
            S.EXHIBITED: bool(lot.get("exhibited")),
            S.PUBLISHED: bool(lot.get("published")),
            S.CONDITION: int(lot.get("condition") or 4),
            S.SIGNED: bool(lot.get("signed")),
            S.SERIES: lot.get("series") or "",
        }


class LiveArtClient(_HttpClient):
    """Adapter for LiveArt AI's auction-results API (api.liveart.ai).

    Two-step fetch: look up the artist's ID via /v1/artists?q=<name>, then
    pull their sales via /v1/sales?artist_id=<id>&limit=... with pagination.
    Field names below follow LiveArt's documented sale object; if your plan's
    response shape differs, edit `_map_lot` to match.
    """

    name = "liveart"

    def __init__(self, api_key: str, base_url: str = "https://api.liveart.ai/v1",
                 timeout: float = 30.0):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "LiveArtClient":
        key = os.environ.get("LIVEART_API_KEY")
        if not key:
            raise RuntimeError("Set LIVEART_API_KEY in environment")
        url = os.environ.get("LIVEART_BASE_URL", "https://api.liveart.ai/v1")
        return cls(api_key=key, base_url=url)

    def _resolve_artist_id(self, artist: str) -> str | None:
        """Find a LiveArt artist ID by name (best match on first page)."""
        data = self._get("artists", {"q": artist, "limit": 5})
        artists = data.get("artists") or data.get("results") or []
        if not artists:
            return None
        target = artist.strip().lower()
        for a in artists:
            name = (a.get("name") or "").strip().lower()
            if name == target:
                return str(a.get("id") or a.get("artist_id") or "")
        a = artists[0]
        return str(a.get("id") or a.get("artist_id") or "") or None

    def fetch(self, artist: str) -> pd.DataFrame:
        artist_id = self._resolve_artist_id(artist)
        if not artist_id:
            return pd.DataFrame(columns=S.ALL_COLUMNS)
        rows: list[dict] = []
        offset, limit = 0, 100
        while True:
            data = self._get(
                "sales",
                {"artist_id": artist_id, "offset": offset, "limit": limit},
            )
            lots = data.get("sales") or data.get("results") or data.get("lots") or []
            if not lots:
                break
            rows.extend(self._map_lot(l, artist) for l in lots)
            if len(lots) < limit:
                break
            offset += limit
        return self._to_dataframe(rows)

    @staticmethod
    def _map_lot(lot: dict, artist: str) -> dict:
        h = float(lot.get("height_cm") or lot.get("height") or 0)
        w = float(lot.get("width_cm") or lot.get("width") or 0)
        d = float(lot.get("depth_cm") or lot.get("depth") or 0)
        if not (h and w):
            h, w, d = _parse_dimensions(lot.get("dimensions") or "")
        return {
            S.ARTIST: lot.get("artist_name") or artist,
            S.TITLE: lot.get("title") or lot.get("artwork_title") or "",
            S.MEDIUM: S.normalize_medium(
                lot.get("category") or lot.get("medium") or "other"
            ),
            S.HEIGHT_CM: h,
            S.WIDTH_CM: w,
            S.DEPTH_CM: d,
            S.YEAR_CREATED: lot.get("created_year") or lot.get("year_created"),
            S.SALE_DATE: lot.get("sold_at") or lot.get("sale_date"),
            S.SALE_PRICE_USD: lot.get("price_usd")
            or lot.get("hammer_price_usd")
            or lot.get("final_price_usd")
            or 0,
            S.AUCTION_HOUSE: lot.get("auction_house_name") or lot.get("house") or "",
            S.SALE_TYPE: S.normalize_sale_type(
                lot.get("sale_type") or lot.get("auction_name") or "day"
            ),
            S.LOCATION: (lot.get("city") or lot.get("location") or "other").lower(),
            S.LOT_NUMBER: str(lot.get("lot_number") or lot.get("lot_id") or ""),
            S.PROVENANCE_SCORE: int(lot.get("provenance_score") or 0),
            S.EXHIBITED: bool(lot.get("exhibited")),
            S.PUBLISHED: bool(lot.get("published")),
            S.CONDITION: int(lot.get("condition") or 4),
            S.SIGNED: bool(lot.get("signed")),
            S.SERIES: lot.get("series") or "",
        }


class MergedSource(AuctionDataSource):
    """Concatenate multiple sources, dedupe overlapping rows."""

    name = "merged"

    def __init__(self, *sources: AuctionDataSource):
        if not sources:
            raise ValueError("MergedSource requires at least one source")
        self.sources = list(sources)

    def fetch(self, artist: str) -> pd.DataFrame:
        frames = [src.fetch(artist) for src in self.sources]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame(columns=S.ALL_COLUMNS)
        df = pd.concat(frames, ignore_index=True, sort=False)
        df["_dedup_key"] = (
            df[S.ARTIST].astype(str)
            + "|"
            + df[S.SALE_DATE].astype(str)
            + "|"
            + df[S.AUCTION_HOUSE].astype(str).str.lower()
            + "|"
            + df[S.LOT_NUMBER].astype(str)
        )
        df = df.sort_values(S.PROVENANCE_SCORE, ascending=False)
        df = df.drop_duplicates(subset="_dedup_key", keep="first")
        df = df.drop(columns="_dedup_key").sort_values(S.SALE_DATE).reset_index(drop=True)
        return df
