from pathlib import Path

import pandas as pd
import pytest

from predictor import schema as S
from predictor.import_collection import (
    HEADER_ROW,
    convert,
    infer_medium,
    main,
    parse_date,
    parse_dims_cm,
    parse_year,
)
from predictor.portfolio import ACQUIRED_DATE, ACQUIRED_PRICE_USD, EST_MID, validate_portfolio, value_portfolio

MASTER = Path("data/art_collection_master_v10_3.xlsx")


@pytest.mark.parametrize("text,expected", [
    ("7 x 6.5", (17.78, 16.51, 0)),
    ("47 dia (tondo)", (119.38, 119.38, 0)),
    ("8 in diameter", (20.32, 20.32, 0)),
    ("14.25 w x 11.25 h", (28.575, 36.195, 0)),
    ("7.5 h x 6 l x 4 w", (19.05, 15.24, 10.16)),
    ("11.75 x 8.125 x 2.375 (29.9 x 20.3 x 6 cm)", (29.9, 20.3, 6)),
    ("39.25 x 31.25 (100 x 79 cm) per catalogue; framed 40 x 30", (100, 79, 0)),
    ("60 x 56 (framed 61.75 x 57.75)", (152.4, 142.24, 0)),
    ("about 14 x 20 (confirm)", (35.56, 50.8, 0)),
    ("shipping size 30.25 x 23.25", (0, 0, 0)),
    ("-", (0, 0, 0)),
    ("TBD", (0, 0, 0)),
    ("", (0, 0, 0)),
])
def test_parse_dims_cm(text, expected):
    got = parse_dims_cm(text)
    assert got == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("text,expected", [
    ("1963", 1963), ("1950s", 1950), ("c. 1932-34", 1932), ("c.1956", 1956),
    ("1988-89", 1988), ("by 2015", 2015), ("1999 + 2002", 1999),
    ("1967 per invoice (bidding notes read 1964 - confirm)", 1967),
    ("n.d.", 0), ("-", 0), ("", 0), (None, 0),
])
def test_parse_year(text, expected):
    assert parse_year(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Rago, Jul 2026, lot 110", "2026-07-01"),
    ("Freeman's, Sale 6472, 26 Feb 2026, lot 93", "2026-02-26"),
    ("Sotheby's New York (online), Sale N12255, 1-8 Oct 2026, lot 1261", "2026-10-01"),
    ("Modern Discoveries, Sale N12258, closed 9 Oct 2026, lot 2180", "2026-10-09"),
    ("Cheryl Hazan Contemporary Art, NY - Invoice IN-363, 9 Nov 2024", "2024-11-09"),
    ("Artsy Winter Capsule, Dec 2024", "2024-12-01"),
    ("Phillips London", ""),
    ("", ""),
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("title,expected", [
    ("Untitled (watercolor)", "works_on_paper"),
    ("Study for Rockin' Dopsie (mixed media/collage on paper)", "works_on_paper"),
    ("Red Mini-Tondo (acrylic on wood)", "acrylic"),
    ("The Watchers (oil on panel)", "oil"),
    ("Cubist Mouth II (acrylic and oil stick on canvas)", "oil"),
    ("Rilievo (silvered bronze, plexi, chrome brass)", "sculpture"),
    ("A Miniature Hand Chair (giltwood)", "sculpture"),
    ("The Earring (UV pigment print/acrylic panels, ed. set)", "print"),
    ("Untitled (mixed media collage)", "mixed_media"),
    ("Abduction by Boat", "other"),
])
def test_infer_medium(title, expected):
    assert infer_medium(title) == expected


def _synthetic_master(path: Path) -> None:
    """A tiny workbook with the same layout as the real master (headers on row 4)."""
    header = ["#", "Artist", "Title / Work", "Year", "Dims (in)", "Source / Channel",
              "Total Cost All-In ($)", "Est Value Low ($ gross)", "Est Value High ($ gross)",
              "Mid ($ gross)", "Net Realizable ($)", "Unrealized G/L vs Net ($)",
              "Liquidity", "Confidence", "Recommendation", "Cost Evidence", "Notes / Corrections"]
    rows = [
        ["ART COLLECTION - MASTER"] + [None] * 16,
        ["v10"] + [None] * 16,
        [None] * 17,
        header,
        [1, "Ada Quill", "Harbor Study (oil on canvas)", "2012", "59 x 47.25 (150 x 120 cm)",
         "Rago, Jul 2026, lot 1", 95000, 120000, 160000, 140000, 95200, 200, "Medium", "Moderate", "HOLD", "INVOICE", "fine"],
        [2, "Nobody", "Mystery (signed)", "n.d.", "-", "Unknown - send invoice",
         None, 1000, 2000, 1500, 1020, None, None, None, None, None, None],
        [None, "TOTAL", None, None, None, None, 95000] + [None] * 10,
    ]
    pd.DataFrame(rows).to_excel(path, sheet_name="Collection", header=False, index=False)


def test_convert_synthetic_workbook(tmp_path):
    xlsx = tmp_path / "master.xlsx"
    _synthetic_master(xlsx)
    df = convert(xlsx)
    assert len(df) == 2  # the TOTAL row (no '#') is dropped
    first, second = df.iloc[0], df.iloc[1]
    assert first[S.ARTIST] == "Ada Quill" and first[S.MEDIUM] == "oil"
    assert (first[S.HEIGHT_CM], first[S.WIDTH_CM]) == (150.0, 120.0)
    assert first[S.YEAR_CREATED] == 2012
    assert first[ACQUIRED_PRICE_USD] == 95000 and first[ACQUIRED_DATE] == "2026-07-01"
    assert first[EST_MID] == 140000 and first["recommendation"] == "HOLD"
    assert second[S.HEIGHT_CM] == 0 and second[S.YEAR_CREATED] == 0
    assert second[ACQUIRED_PRICE_USD] == 0 and second[ACQUIRED_DATE] == ""
    assert bool(second[S.SIGNED]) is True

    # The output is a valid portfolio CSV that the valuer accepts end to end.
    validate_portfolio(df)
    sales = S.validate(pd.read_csv("sample_data/sample_sales.csv"))
    res = value_portfolio(sales, df, valuation_date="2026-10-11")
    assert list(res["status"]) == ["ok", "missing dimensions"]


def test_cli_roundtrip(tmp_path, capsys):
    xlsx = tmp_path / "master.xlsx"
    _synthetic_master(xlsx)
    out = tmp_path / "portfolio.csv"
    assert main(["--xlsx", str(xlsx), "--out", str(out)]) == 0
    assert "2 works" in capsys.readouterr().out
    assert len(pd.read_csv(out)) == 2


def test_cli_missing_file(tmp_path, capsys):
    assert main(["--xlsx", str(tmp_path / "nope.xlsx"), "--out", str(tmp_path / "o.csv")]) == 1
    assert "ERROR" in capsys.readouterr().err


@pytest.mark.skipif(not MASTER.exists(), reason="private master spreadsheet not present")
def test_convert_real_master():
    """Sanity check against the private workbook when it is available locally."""
    df = convert(MASTER)
    assert len(df) > 0
    assert set(df.columns) >= {S.ARTIST, S.MEDIUM, S.HEIGHT_CM, S.WIDTH_CM, S.YEAR_CREATED, ACQUIRED_PRICE_USD, EST_MID}
    assert (df[S.HEIGHT_CM] > 0).mean() > 0.5
    assert (df[S.YEAR_CREATED] > 0).mean() > 0.5
    assert (df[EST_MID] > 0).mean() > 0.5
    validate_portfolio(df)
