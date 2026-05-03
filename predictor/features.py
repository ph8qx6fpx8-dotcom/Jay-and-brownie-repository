"""Feature engineering: area, age_at_sale, house tier, career period, encoding."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from predictor import schema as S

TIER1_HOUSES = {"christie's", "sotheby's", "phillips"}
TIER2_HOUSES = {"bonhams", "heritage", "doyle", "lyon & turnbull", "swann"}

CAREER_PERIODS = ["early", "mid", "peak", "late"]
HOUSE_TIERS = ["tier1", "tier2", "tier3"]


def house_tier(house: str, sale_type: str) -> str:
    h = (house or "").strip().lower()
    if h in TIER1_HOUSES and sale_type == "evening":
        return "tier1"
    if h in TIER1_HOUSES or h in TIER2_HOUSES:
        return "tier2"
    return "tier3"


def career_period_bins(years_created: pd.Series) -> np.ndarray:
    """Return quartile cut points covering the artist's working span."""
    valid = years_created.dropna().astype(int)
    if len(valid) < 4 or valid.min() == valid.max():
        if len(valid) == 0:
            return np.array([0, 1, 2, 3, 4])
        y = int(valid.iloc[0]) if len(valid) == 1 else int(valid.median())
        return np.array([y - 2, y - 1, y, y + 1, y + 2])
    qs = np.quantile(valid, [0.0, 0.25, 0.5, 0.75, 1.0])
    qs = np.unique(qs)
    if len(qs) < 5:
        qs = np.linspace(valid.min(), valid.max(), 5)
    return qs


def assign_career_period(year_created: int, bins: np.ndarray) -> str:
    if pd.isna(year_created):
        return "mid"
    y = int(year_created)
    edges = list(bins)
    if y <= edges[1]:
        return "early"
    if y <= edges[2]:
        return "mid"
    if y <= edges[3]:
        return "peak"
    return "late"


@dataclass
class FeatureSpec:
    """Captures categorical levels seen at fit time so prediction matches."""

    medium_levels: list
    house_tier_levels: list
    sale_type_levels: list
    location_levels: list
    career_period_levels: list
    series_levels: list
    career_bins: np.ndarray
    feature_columns: list

    def to_dict(self) -> dict:
        return {
            "medium": self.medium_levels,
            "house_tier": self.house_tier_levels,
            "sale_type": self.sale_type_levels,
            "location": self.location_levels,
            "career_period": self.career_period_levels,
            "series": self.series_levels,
        }


NUMERIC_FEATURES = [
    "log_area",
    "has_depth",
    "log_depth",
    "age_at_sale",
    "sale_year",
    "provenance_score",
    "exhibited",
    "published",
    "condition",
    "signed",
]


def _base_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["log_area"] = np.log(out[S.HEIGHT_CM].clip(lower=1) * out[S.WIDTH_CM].clip(lower=1))
    depth = out[S.DEPTH_CM].fillna(0.0).clip(lower=0)
    out["has_depth"] = (depth > 0).astype(int)
    out["log_depth"] = np.log1p(depth)
    out["sale_year"] = out[S.SALE_DATE].dt.year + (out[S.SALE_DATE].dt.dayofyear - 1) / 365.25
    out["age_at_sale"] = out["sale_year"] - out[S.YEAR_CREATED].astype(float)
    out["exhibited"] = out[S.EXHIBITED].astype(int)
    out["published"] = out[S.PUBLISHED].astype(int)
    out["signed"] = out[S.SIGNED].astype(int)
    out["condition"] = out[S.CONDITION].astype(int)
    out["provenance_score"] = out[S.PROVENANCE_SCORE].astype(int)
    out["house_tier"] = [
        house_tier(h, t) for h, t in zip(out[S.AUCTION_HOUSE], out[S.SALE_TYPE])
    ]
    return out


def fit_transform(df: pd.DataFrame) -> tuple[pd.DataFrame, FeatureSpec]:
    """Build the design matrix and remember category levels for predict-time."""
    base = _base_frame(df)
    bins = career_period_bins(df[S.YEAR_CREATED])
    base["career_period"] = base[S.YEAR_CREATED].apply(lambda y: assign_career_period(y, bins))

    categorical_cols = {
        "medium": S.MEDIUM,
        "house_tier": "house_tier",
        "sale_type": S.SALE_TYPE,
        "location": S.LOCATION,
        "career_period": "career_period",
        "series": S.SERIES,
    }
    levels = {
        name: sorted(base[col].astype(str).unique().tolist())
        for name, col in categorical_cols.items()
    }

    matrix = base[NUMERIC_FEATURES].astype(float).copy()
    for name, col in categorical_cols.items():
        for lvl in levels[name]:
            matrix[f"{name}__{lvl}"] = (base[col].astype(str) == lvl).astype(int)

    spec = FeatureSpec(
        medium_levels=levels["medium"],
        house_tier_levels=levels["house_tier"],
        sale_type_levels=levels["sale_type"],
        location_levels=levels["location"],
        career_period_levels=levels["career_period"],
        series_levels=levels["series"],
        career_bins=bins,
        feature_columns=list(matrix.columns),
    )
    return matrix, spec


def transform(df: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    base = _base_frame(df)
    base["career_period"] = base[S.YEAR_CREATED].apply(
        lambda y: assign_career_period(y, spec.career_bins)
    )

    matrix = base[NUMERIC_FEATURES].astype(float).copy()
    cat_map = {
        "medium": (S.MEDIUM, spec.medium_levels),
        "house_tier": ("house_tier", spec.house_tier_levels),
        "sale_type": (S.SALE_TYPE, spec.sale_type_levels),
        "location": (S.LOCATION, spec.location_levels),
        "career_period": ("career_period", spec.career_period_levels),
        "series": (S.SERIES, spec.series_levels),
    }
    for name, (col, lvls) in cat_map.items():
        for lvl in lvls:
            matrix[f"{name}__{lvl}"] = (base[col].astype(str) == lvl).astype(int)

    return matrix.reindex(columns=spec.feature_columns, fill_value=0)
