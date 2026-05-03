import numpy as np
import pandas as pd
import pytest

from predictor import features as F
from predictor import schema as S
from predictor.data_sources import CsvSource


@pytest.fixture
def sample_df():
    return CsvSource("sample_data/sample_sales.csv").all_data()


def test_house_tier_mapping():
    assert F.house_tier("Christie's", "evening") == "tier1"
    assert F.house_tier("Christie's", "day") == "tier2"
    assert F.house_tier("Bonhams", "day") == "tier2"
    assert F.house_tier("Tiny Regional Auctions", "day") == "tier3"


def test_fit_transform_shape(sample_df):
    X, spec = F.fit_transform(sample_df)
    assert len(X) == len(sample_df)
    assert X.shape[1] == len(spec.feature_columns)
    for col in F.NUMERIC_FEATURES:
        assert col in X.columns
    for prefix in ["medium__", "house_tier__", "sale_type__", "career_period__"]:
        assert any(c.startswith(prefix) for c in X.columns)


def test_transform_matches_fit_columns(sample_df):
    X_fit, spec = F.fit_transform(sample_df)
    X_pred = F.transform(sample_df.head(3), spec)
    assert list(X_pred.columns) == list(X_fit.columns)


def test_transform_unseen_category_falls_back(sample_df):
    X_fit, spec = F.fit_transform(sample_df)
    novel = sample_df.head(1).copy()
    novel.loc[:, S.AUCTION_HOUSE] = "Unknown House XYZ"
    novel.loc[:, S.SALE_TYPE] = "evening"
    X_pred = F.transform(novel, spec)
    assert list(X_pred.columns) == list(X_fit.columns)
    assert X_pred.shape == (1, X_fit.shape[1])


def test_log_area_doubles_with_area():
    df = pd.DataFrame(
        {
            S.ARTIST: ["X"] * 2,
            S.MEDIUM: ["oil", "oil"],
            S.HEIGHT_CM: [100.0, 100.0],
            S.WIDTH_CM: [100.0, 200.0],
            S.YEAR_CREATED: [2010, 2010],
            S.SALE_DATE: pd.to_datetime(["2022-01-01", "2022-01-01"]),
            S.SALE_PRICE_USD: [10000.0, 10000.0],
            S.AUCTION_HOUSE: ["Christie's", "Christie's"],
            S.SALE_TYPE: ["day", "day"],
        }
    )
    df = S.validate(df)
    X, _ = F.fit_transform(df)
    delta = X.iloc[1]["log_area"] - X.iloc[0]["log_area"]
    assert np.isclose(delta, np.log(2), atol=1e-6)
