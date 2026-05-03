from datetime import date

import pandas as pd
import pytest

from predictor.data_sources import CsvSource
from predictor.model import fit_hedonic


@pytest.fixture
def sample_df():
    return CsvSource("sample_data/sample_sales.csv").all_data()


def _typical_lot(date_=date(2024, 6, 1)):
    return {
        "medium": "oil",
        "height_cm": 120.0,
        "width_cm": 100.0,
        "depth_cm": 0.0,
        "year_created": 2010,
        "sale_date": pd.Timestamp(date_),
        "auction_house": "Christie's",
        "sale_type": "evening",
        "location": "new york",
        "provenance_score": 1,
        "exhibited": False,
        "published": False,
        "condition": 4,
        "signed": True,
        "series": "",
    }


def test_fit_requires_min_rows(sample_df):
    with pytest.raises(ValueError):
        fit_hedonic(sample_df.head(3), sample_df["artist"].iloc[0])


def test_predict_returns_positive_price(sample_df):
    artist = sample_df["artist"].iloc[0]
    model = fit_hedonic(sample_df, artist)
    pred = model.predict(_typical_lot())
    assert pred["point_usd"] > 0
    assert pred["low_usd"] < pred["point_usd"] < pred["high_usd"]


def test_prediction_within_historical_range(sample_df):
    artist = sample_df["artist"].iloc[0]
    model = fit_hedonic(sample_df, artist)
    pred = model.predict(_typical_lot())
    sub = sample_df[sample_df["artist"] == artist]
    assert sub["sale_price_usd"].min() / 100 < pred["point_usd"] < sub["sale_price_usd"].max() * 100


def test_projection_grows_when_trend_positive(sample_df):
    artist = "Bo Marquez"
    model = fit_hedonic(sample_df, artist)
    assert model.annual_trend > 0
    proj = model.project(_typical_lot(), years=10)
    assert proj["point_usd"].iloc[-1] > proj["point_usd"].iloc[0]


def test_projection_ci_widens_with_horizon(sample_df):
    artist = sample_df["artist"].iloc[0]
    model = fit_hedonic(sample_df, artist)
    proj = model.project(_typical_lot(), years=10)
    width_first = proj["high_usd"].iloc[1] / proj["low_usd"].iloc[1]
    width_last = proj["high_usd"].iloc[-1] / proj["low_usd"].iloc[-1]
    assert width_last > width_first


def test_market_drift_increases_projection(sample_df):
    artist = sample_df["artist"].iloc[0]
    model = fit_hedonic(sample_df, artist)
    base = model.project(_typical_lot(), years=10, market_drift=0.0)
    higher = model.project(_typical_lot(), years=10, market_drift=0.05)
    assert higher["point_usd"].iloc[-1] > base["point_usd"].iloc[-1]


def test_comparables_returns_k_rows(sample_df):
    artist = sample_df["artist"].iloc[0]
    model = fit_hedonic(sample_df, artist)
    comps = model.comparables(_typical_lot(), k=5)
    assert len(comps) == 5
    assert "distance" in comps.columns
    assert (comps["distance"].diff().dropna() >= -1e-9).all()


def test_summary_keys(sample_df):
    artist = sample_df["artist"].iloc[0]
    model = fit_hedonic(sample_df, artist)
    s = model.summary()
    for key in [
        "artist", "n_lots", "date_range", "median_price_usd",
        "min_price_usd", "max_price_usd", "top_media", "top_houses",
        "r_squared", "annual_trend_pct", "residual_sigma",
    ]:
        assert key in s
