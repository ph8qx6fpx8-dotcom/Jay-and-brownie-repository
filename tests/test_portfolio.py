import math
from pathlib import Path

import pandas as pd
import pytest

from predictor import schema as S
from predictor.data_sources import CsvSource
from predictor.portfolio import (
    ACQUIRED_PRICE_USD,
    EST_HIGH,
    EST_LOW,
    EST_MID,
    artist_scenario,
    main,
    portfolio_totals,
    validate_portfolio,
    value_portfolio,
)

SALES = Path("sample_data/sample_sales.csv")
PORTFOLIO = Path("sample_data/sample_portfolio.csv")
VALUATION_DATE = "2026-10-11"


@pytest.fixture
def sales_df():
    return CsvSource(SALES).all_data()


@pytest.fixture
def portfolio_df():
    return pd.read_csv(PORTFOLIO)


def test_validate_portfolio_requires_columns():
    with pytest.raises(S.SchemaError):
        validate_portfolio(pd.DataFrame({"artist": ["X"], "medium": ["oil"]}))


def test_rows_with_missing_dimensions_or_year_are_listed_not_valued(sales_df):
    pf = pd.DataFrame([
        {"artist": "Ada Quill", "medium": "oil", "height_cm": 0, "width_cm": 50, "year_created": 2000,
         "acquired_price_usd": 1000, "est_value_mid_usd": 1500},
        {"artist": "Ada Quill", "medium": "oil", "height_cm": 100, "width_cm": 50, "year_created": 0},
        {"artist": "Ada Quill", "medium": "oil", "height_cm": 100, "width_cm": 50, "year_created": 2000},
    ])
    res = value_portfolio(sales_df, pf, valuation_date=VALUATION_DATE)
    assert list(res["status"]) == ["missing dimensions", "missing year_created", "ok"]
    # The owner's estimate and its gain are still reported for unvalued rows.
    first = res.iloc[0]
    assert first[EST_MID] == 1500
    assert first["est_gain_usd"] == pytest.approx(500)
    assert first["est_gain_pct"] == pytest.approx(50.0)


def test_estimate_columns_flow_into_totals(sales_df, portfolio_df):
    pf = portfolio_df.copy()
    pf[EST_LOW] = [100.0, 200.0, 0, 0, 0, 0, 50.0]
    pf[EST_MID] = [150.0, 300.0, 0, 0, 0, 0, 75.0]
    pf[EST_HIGH] = [200.0, 400.0, 0, 0, 0, 0, 100.0]
    res = value_portfolio(sales_df, pf, valuation_date=VALUATION_DATE)
    t = portfolio_totals(res)
    assert t["n_with_estimate"] == 3
    assert t["est_mid_usd"] == pytest.approx(525.0)
    assert t["est_low_usd"] == pytest.approx(350.0) and t["est_high_usd"] == pytest.approx(700.0)
    # Unknown Painter (skipped by the model) still contributes its estimate and cost.
    assert t["est_cost_usd"] == pytest.approx(95000 + 140000 + 15000)
    assert t["est_gain_usd"] == pytest.approx(525.0 - 250000)
    # Model-side totals are unaffected by the estimate columns.
    assert t["n_valued"] == 6


def test_totals_without_estimates_report_zero(sales_df, portfolio_df):
    t = portfolio_totals(value_portfolio(sales_df, portfolio_df, valuation_date=VALUATION_DATE))
    assert t["n_with_estimate"] == 0 and t["est_mid_usd"] == 0.0
    assert math.isnan(t["est_gain_usd"])


def test_validate_portfolio_fills_defaults_and_normalizes():
    df = pd.DataFrame([{"artist": " X ", "medium": "Oil on canvas", "height_cm": "100", "width_cm": 80, "year_created": "1999"}])
    out = validate_portfolio(df)
    row = out.iloc[0]
    assert row[S.ARTIST] == "X"
    assert row[S.MEDIUM] == "oil"
    assert row[S.YEAR_CREATED] == 1999
    assert row[ACQUIRED_PRICE_USD] == 0.0
    assert row[S.CONDITION] == 4
    assert row[S.SIGNED] is False or row[S.SIGNED] == False  # noqa: E712


def test_artist_scenario_uses_modal_house_and_location(sales_df):
    sc = artist_scenario(sales_df, "Ada Quill")
    sub = sales_df[sales_df[S.ARTIST] == "Ada Quill"]
    assert sc["auction_house"] == sub[S.AUCTION_HOUSE].mode().iloc[0]
    assert sc["location"] == sub[S.LOCATION].mode().iloc[0]


def test_artist_scenario_breaks_ties_alphabetically():
    df = S.validate(pd.DataFrame([
        {S.ARTIST: "T", S.MEDIUM: "oil", S.HEIGHT_CM: 10, S.WIDTH_CM: 10, S.YEAR_CREATED: 2000,
         S.SALE_DATE: "2020-01-01", S.SALE_PRICE_USD: 100, S.AUCTION_HOUSE: h, S.SALE_TYPE: "day", S.LOCATION: loc}
        for h, loc in [("Phillips", "paris"), ("Bonhams", "london")]
    ]))
    sc = artist_scenario(df, "T")
    assert sc["auction_house"] == "Bonhams"
    assert sc["location"] == "london"


def test_value_portfolio_sample(sales_df, portfolio_df):
    res = value_portfolio(sales_df, portfolio_df, sale_type="day", valuation_date=VALUATION_DATE)
    assert len(res) == 7
    assert (res["status"] == "ok").sum() == 6
    skipped = res[res["status"] != "ok"].iloc[0]
    assert skipped[S.ARTIST] == "Unknown Painter"
    assert "no sales data" in skipped["status"]

    ok = res[res["status"] == "ok"]
    assert (ok["now_low_usd"] < ok["now_point_usd"]).all()
    assert (ok["now_point_usd"] < ok["now_high_usd"]).all()
    assert (ok["horizon_year"] == 2036).all()
    assert (ok["scenario_sale_type"] == "day").all()

    # Bo Marquez has a strongly positive trend in the sample data.
    bo = ok[ok[S.ARTIST] == "Bo Marquez"]
    assert (bo["horizon_point_usd"] > bo["now_point_usd"]).all()

    # No acquisition price → no gain figures.
    ink = ok[ok[S.TITLE] == "Study in Ink"].iloc[0]
    assert math.isnan(ink["gain_usd"]) and math.isnan(ink["gain_pct"])
    harbor = ok[ok[S.TITLE] == "Harbor Study"].iloc[0]
    assert harbor["gain_usd"] == pytest.approx(harbor["now_point_usd"] - 95000)


def test_value_portfolio_pins_reference_numbers(sales_df, portfolio_df):
    """Guards against silent drift; the JS build is checked against the same figures."""
    res = value_portfolio(sales_df, portfolio_df, sale_type="day", valuation_date=VALUATION_DATE)
    t = portfolio_totals(res)
    assert t["now_point_usd"] == pytest.approx(5_744_623, abs=1)
    assert t["horizon_point_usd"] == pytest.approx(8_856_937, abs=1)
    assert t["acquired_total_usd"] == pytest.approx(3_865_000, abs=1)
    assert t["gain_usd"] == pytest.approx(1_496_289, abs=1)
    assert t["gain_pct"] == pytest.approx(38.7, abs=0.05)


def test_value_portfolio_skips_thin_artists(sales_df):
    thin = sales_df[sales_df[S.ARTIST] == "Ada Quill"].head(3)
    rest = sales_df[sales_df[S.ARTIST] != "Ada Quill"]
    sales = pd.concat([thin, rest], ignore_index=True)
    pf = pd.DataFrame([{"artist": "Ada Quill", "medium": "oil", "height_cm": 100, "width_cm": 80, "year_created": 2000}])
    res = value_portfolio(sales, pf, valuation_date=VALUATION_DATE)
    assert res.iloc[0]["status"] == "only 3 sales; need at least 6"
    assert res.iloc[0]["n_sales"] == 3


def test_value_portfolio_scenario_changes_price(sales_df, portfolio_df):
    day = value_portfolio(sales_df, portfolio_df, sale_type="day", valuation_date=VALUATION_DATE)
    evening = value_portfolio(sales_df, portfolio_df, sale_type="evening", valuation_date=VALUATION_DATE)
    assert portfolio_totals(evening)["now_point_usd"] != portfolio_totals(day)["now_point_usd"]


def test_portfolio_totals_counts_and_cost_basis(sales_df, portfolio_df):
    res = value_portfolio(sales_df, portfolio_df, valuation_date=VALUATION_DATE)
    t = portfolio_totals(res)
    assert t["n_works"] == 7 and t["n_valued"] == 6 and t["n_skipped"] == 1
    ok = res[res["status"] == "ok"]
    assert t["now_point_usd"] == pytest.approx(ok["now_point_usd"].sum())
    # Cost basis only counts valued works that have a purchase price (not the skipped Unknown Painter).
    assert t["acquired_total_usd"] == pytest.approx(95000 + 140000 + 310000 + 520000 + 2800000)


def test_portfolio_totals_empty():
    t = portfolio_totals(pd.DataFrame(columns=["status", ACQUIRED_PRICE_USD, "now_point_usd"]))
    assert t["n_works"] == 0 and t["now_point_usd"] == 0.0
    assert math.isnan(t["gain_usd"])


def test_cli_writes_output(tmp_path, capsys):
    out = tmp_path / "valued.csv"
    rc = main(["--sales", str(SALES), "--portfolio", str(PORTFOLIO), "--out", str(out), "--date", VALUATION_DATE])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Valued 6 of 7 works" in captured.out
    assert "Unknown Painter" in captured.err
    assert len(pd.read_csv(out)) == 7


def test_cli_bad_input_returns_error(tmp_path, capsys):
    rc = main(["--sales", str(SALES), "--portfolio", str(tmp_path / "missing.csv")])
    assert rc == 1
    assert "ERROR" in capsys.readouterr().err
