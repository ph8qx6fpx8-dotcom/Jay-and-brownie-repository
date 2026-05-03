"""Per-artist hedonic price model."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from predictor import features as F
from predictor import schema as S


@dataclass
class ArtistPriceModel:
    artist: str
    ridge: Ridge
    spec: F.FeatureSpec
    sigma: float
    r_squared: float
    n_lots: int
    historical_span_years: float
    annual_trend: float  # log-price slope per calendar year (artist CAGR proxy)
    training_data: pd.DataFrame = field(repr=False)

    def _row_from_lot(self, lot: dict) -> pd.DataFrame:
        sale_date = lot.get("sale_date")
        if isinstance(sale_date, (date,)) and not isinstance(sale_date, pd.Timestamp):
            sale_date = pd.Timestamp(sale_date)
        elif sale_date is None:
            sale_date = pd.Timestamp.today().normalize()
        else:
            sale_date = pd.Timestamp(sale_date)

        row = {
            S.ARTIST: self.artist,
            S.TITLE: lot.get("title", ""),
            S.MEDIUM: S.normalize_medium(lot.get("medium", "other")),
            S.HEIGHT_CM: float(lot.get("height_cm", 0) or 0),
            S.WIDTH_CM: float(lot.get("width_cm", 0) or 0),
            S.DEPTH_CM: float(lot.get("depth_cm", 0) or 0),
            S.YEAR_CREATED: int(lot.get("year_created", 0) or 0),
            S.SALE_DATE: sale_date,
            S.SALE_PRICE_USD: 0.0,
            S.AUCTION_HOUSE: lot.get("auction_house", ""),
            S.SALE_TYPE: S.normalize_sale_type(lot.get("sale_type", "day")),
            S.LOCATION: str(lot.get("location", "other")).lower(),
            S.LOT_NUMBER: lot.get("lot_number", ""),
            S.PROVENANCE_SCORE: int(lot.get("provenance_score", 0) or 0),
            S.EXHIBITED: bool(lot.get("exhibited", False)),
            S.PUBLISHED: bool(lot.get("published", False)),
            S.CONDITION: int(lot.get("condition", 4) or 4),
            S.SIGNED: bool(lot.get("signed", False)),
            S.SERIES: str(lot.get("series", "") or ""),
        }
        return pd.DataFrame([row])

    def predict(self, lot: dict, ci: float = 0.80) -> dict:
        z = _z_score(ci)
        df_lot = self._row_from_lot(lot)
        X = F.transform(df_lot, self.spec)
        log_yhat = float(self.ridge.predict(X.values)[0])
        return {
            "point_usd": float(np.exp(log_yhat)),
            "low_usd": float(np.exp(log_yhat - z * self.sigma)),
            "high_usd": float(np.exp(log_yhat + z * self.sigma)),
            "log_yhat": log_yhat,
            "sigma": self.sigma,
            "ci": ci,
        }

    def project(
        self,
        lot: dict,
        years: int = 10,
        market_drift: float = 0.0,
        ci: float = 0.80,
    ) -> pd.DataFrame:
        """Year-by-year projection. `market_drift` is added to the artist trend."""
        z = _z_score(ci)
        base_date = pd.Timestamp(lot.get("sale_date") or pd.Timestamp.today().normalize())
        rows = []
        for offset in range(0, years + 1):
            try:
                fdate = base_date.replace(year=base_date.year + offset)
            except ValueError:
                # leap-day edge case
                fdate = base_date + pd.DateOffset(years=offset)
            future_lot = {**lot, "sale_date": fdate}
            df_lot = self._row_from_lot(future_lot)
            X = F.transform(df_lot, self.spec)
            log_yhat = float(self.ridge.predict(X.values)[0])
            log_yhat += market_drift * offset
            T = max(self.historical_span_years, 1.0)
            sigma_h = self.sigma * np.sqrt(1.0 + offset / T)
            rows.append(
                {
                    "year": fdate.year,
                    "date": fdate,
                    "point_usd": float(np.exp(log_yhat)),
                    "low_usd": float(np.exp(log_yhat - z * sigma_h)),
                    "high_usd": float(np.exp(log_yhat + z * sigma_h)),
                    "sigma_horizon": float(sigma_h),
                }
            )
        return pd.DataFrame(rows)

    def comparables(self, lot: dict, k: int = 5) -> pd.DataFrame:
        df_lot = self._row_from_lot(lot)
        X_lot = F.transform(df_lot, self.spec).iloc[0].values
        X_train = F.transform(self.training_data, self.spec).values

        ranges = X_train.max(axis=0) - X_train.min(axis=0)
        ranges[ranges == 0] = 1.0
        diffs = np.abs(X_train - X_lot) / ranges
        dist = diffs.mean(axis=1)

        idx = np.argsort(dist)[:k]
        cols = [
            S.SALE_DATE,
            S.TITLE,
            S.MEDIUM,
            S.HEIGHT_CM,
            S.WIDTH_CM,
            S.YEAR_CREATED,
            S.AUCTION_HOUSE,
            S.SALE_TYPE,
            S.LOCATION,
            S.SALE_PRICE_USD,
        ]
        present = [c for c in cols if c in self.training_data.columns]
        out = self.training_data.iloc[idx][present].copy()
        out.insert(0, "distance", dist[idx])
        return out.reset_index(drop=True)

    def summary(self) -> dict[str, Any]:
        prices = self.training_data[S.SALE_PRICE_USD]
        media = self.training_data[S.MEDIUM].value_counts().head(3).to_dict()
        houses = self.training_data[S.AUCTION_HOUSE].value_counts().head(3).to_dict()
        return {
            "artist": self.artist,
            "n_lots": int(self.n_lots),
            "date_range": (
                self.training_data[S.SALE_DATE].min().date(),
                self.training_data[S.SALE_DATE].max().date(),
            ),
            "median_price_usd": float(prices.median()),
            "min_price_usd": float(prices.min()),
            "max_price_usd": float(prices.max()),
            "top_media": media,
            "top_houses": houses,
            "r_squared": float(self.r_squared),
            "annual_trend_pct": float(np.expm1(self.annual_trend) * 100.0),
            "residual_sigma": float(self.sigma),
        }


def _z_score(ci: float) -> float:
    table = {0.50: 0.674, 0.68: 0.994, 0.80: 1.282, 0.90: 1.645, 0.95: 1.960}
    return table.get(round(ci, 2), 1.282)


def fit_hedonic(df: pd.DataFrame, artist: str, alpha: float = 1.0) -> ArtistPriceModel:
    """Fit a Ridge log-linear hedonic model on one artist's sales.

    Requires at least 6 sales for that artist; otherwise raises ValueError.
    """
    sub = df[df[S.ARTIST] == artist].copy()
    if len(sub) < 6:
        raise ValueError(
            f"Need at least 6 sales for '{artist}', have {len(sub)}. "
            "Add more rows to fit a meaningful model."
        )
    sub = sub.sort_values(S.SALE_DATE).reset_index(drop=True)

    X, spec = F.fit_transform(sub)
    y = np.log(sub[S.SALE_PRICE_USD].astype(float).values)

    ridge = Ridge(alpha=alpha)
    ridge.fit(X.values, y)
    yhat = ridge.predict(X.values)
    resid = y - yhat
    sigma = float(np.std(resid, ddof=1)) if len(resid) > 1 else 0.5
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    sale_year_idx = X.columns.get_loc("sale_year")
    annual_trend = float(ridge.coef_[sale_year_idx])

    span = (sub[S.SALE_DATE].max() - sub[S.SALE_DATE].min()).days / 365.25
    return ArtistPriceModel(
        artist=artist,
        ridge=ridge,
        spec=spec,
        sigma=sigma,
        r_squared=r_squared,
        n_lots=len(sub),
        historical_span_years=max(span, 1.0),
        annual_trend=annual_trend,
        training_data=sub,
    )
