"""Canonical sales-record schema. All data sources normalize to this shape."""
from __future__ import annotations

import pandas as pd

ARTIST = "artist"
TITLE = "title"
MEDIUM = "medium"
HEIGHT_CM = "height_cm"
WIDTH_CM = "width_cm"
DEPTH_CM = "depth_cm"
YEAR_CREATED = "year_created"
SALE_DATE = "sale_date"
SALE_PRICE_USD = "sale_price_usd"
AUCTION_HOUSE = "auction_house"
SALE_TYPE = "sale_type"
LOCATION = "location"
LOT_NUMBER = "lot_number"
PROVENANCE_SCORE = "provenance_score"
EXHIBITED = "exhibited"
PUBLISHED = "published"
CONDITION = "condition"
SIGNED = "signed"
SERIES = "series"

REQUIRED_COLUMNS = [
    ARTIST,
    MEDIUM,
    HEIGHT_CM,
    WIDTH_CM,
    YEAR_CREATED,
    SALE_DATE,
    SALE_PRICE_USD,
    AUCTION_HOUSE,
    SALE_TYPE,
]

OPTIONAL_COLUMNS = [
    TITLE,
    DEPTH_CM,
    LOCATION,
    LOT_NUMBER,
    PROVENANCE_SCORE,
    EXHIBITED,
    PUBLISHED,
    CONDITION,
    SIGNED,
    SERIES,
]

ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS

MEDIUM_VALUES = {
    "oil",
    "acrylic",
    "mixed_media",
    "works_on_paper",
    "print",
    "sculpture",
    "photograph",
    "other",
}

SALE_TYPE_VALUES = {"evening", "day", "online", "regional"}

OPTIONAL_DEFAULTS = {
    TITLE: "",
    DEPTH_CM: 0.0,
    LOCATION: "other",
    LOT_NUMBER: "",
    PROVENANCE_SCORE: 0,
    EXHIBITED: False,
    PUBLISHED: False,
    CONDITION: 4,
    SIGNED: False,
    SERIES: "",
}


class SchemaError(ValueError):
    pass


def normalize_medium(value: str) -> str:
    if value is None:
        return "other"
    v = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "oil_on_canvas": "oil",
        "oil_on_panel": "oil",
        "oils": "oil",
        "acrylic_on_canvas": "acrylic",
        "acrylics": "acrylic",
        "mixed": "mixed_media",
        "mixed_media_on_canvas": "mixed_media",
        "paper": "works_on_paper",
        "watercolor": "works_on_paper",
        "watercolour": "works_on_paper",
        "drawing": "works_on_paper",
        "ink_on_paper": "works_on_paper",
        "lithograph": "print",
        "screenprint": "print",
        "etching": "print",
        "silkscreen": "print",
        "photo": "photograph",
        "photography": "photograph",
        "bronze": "sculpture",
        "marble": "sculpture",
    }
    v = aliases.get(v, v)
    return v if v in MEDIUM_VALUES else "other"


def normalize_sale_type(value: str) -> str:
    if value is None:
        return "day"
    v = str(value).strip().lower()
    if "evening" in v:
        return "evening"
    if "online" in v or "digital" in v:
        return "online"
    if "regional" in v:
        return "regional"
    return "day"


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure required columns exist, coerce types, fill optional defaults.

    Raises SchemaError if a required column is missing or all-null.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SchemaError(f"Missing required columns: {missing}")

    out = df.copy()
    for col, default in OPTIONAL_DEFAULTS.items():
        if col not in out.columns:
            out[col] = default
        else:
            out[col] = out[col].fillna(default)

    out[ARTIST] = out[ARTIST].astype(str).str.strip()
    out[MEDIUM] = out[MEDIUM].apply(normalize_medium)
    out[SALE_TYPE] = out[SALE_TYPE].apply(normalize_sale_type)
    out[AUCTION_HOUSE] = out[AUCTION_HOUSE].astype(str).str.strip()
    out[LOCATION] = out[LOCATION].astype(str).str.strip().str.lower().replace("", "other")
    out[SERIES] = out[SERIES].astype(str).fillna("")
    out[LOT_NUMBER] = out[LOT_NUMBER].astype(str).str.strip()

    for col in [HEIGHT_CM, WIDTH_CM, DEPTH_CM, SALE_PRICE_USD]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out[YEAR_CREATED] = pd.to_numeric(out[YEAR_CREATED], errors="coerce").astype("Int64")
    out[PROVENANCE_SCORE] = pd.to_numeric(out[PROVENANCE_SCORE], errors="coerce").fillna(0).astype(int).clip(0, 3)
    out[CONDITION] = pd.to_numeric(out[CONDITION], errors="coerce").fillna(4).astype(int).clip(1, 5)
    out[EXHIBITED] = out[EXHIBITED].astype(bool)
    out[PUBLISHED] = out[PUBLISHED].astype(bool)
    out[SIGNED] = out[SIGNED].astype(bool)

    out[SALE_DATE] = pd.to_datetime(out[SALE_DATE], errors="coerce")

    bad = out[
        out[SALE_PRICE_USD].isna()
        | (out[SALE_PRICE_USD] <= 0)
        | out[HEIGHT_CM].isna()
        | out[WIDTH_CM].isna()
        | out[SALE_DATE].isna()
    ]
    if not bad.empty:
        raise SchemaError(
            f"Found {len(bad)} rows with invalid price/dimensions/date. "
            "Drop or correct them before loading."
        )

    return out[[c for c in ALL_COLUMNS if c in out.columns]].reset_index(drop=True)
