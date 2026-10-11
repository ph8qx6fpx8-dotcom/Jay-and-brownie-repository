from unittest.mock import patch

import pandas as pd
import pytest

from predictor import schema as S
from predictor.data_sources import (
    ArtnetClient,
    CsvSource,
    LiveArtClient,
    MergedSource,
    MutualArtClient,
    _parse_dimensions,
)


def test_parse_dimensions_cm():
    assert _parse_dimensions("120 x 100 cm") == (120.0, 100.0, 0.0)
    assert _parse_dimensions("80 × 60 × 40 cm") == (80.0, 60.0, 40.0)


def test_parse_dimensions_inches_converted():
    h, w, d = _parse_dimensions("48 x 36 inches")
    assert abs(h - 48 * 2.54) < 1e-6
    assert abs(w - 36 * 2.54) < 1e-6
    assert d == 0.0


def test_csv_source_loads_sample():
    src = CsvSource("sample_data/sample_sales.csv")
    df = src.all_data()
    assert len(df) > 0
    assert (df[S.SALE_PRICE_USD] > 0).all()


def test_csv_source_rejects_missing_required(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("artist,sale_price_usd\nFoo,1000\n")
    with pytest.raises(S.SchemaError):
        CsvSource(bad)


def test_artnet_client_maps_response():
    fake = {
        "results": [
            {
                "artist_name": "Ada Quill",
                "title": "Lot A",
                "medium": "oil_on_canvas",
                "height_cm": 120, "width_cm": 100,
                "year_created": 2010,
                "sale_date": "2023-05-01",
                "price_realized_usd": 250000,
                "auction_house": "Christie's",
                "sale_type": "evening",
                "location": "New York",
                "lot_number": "12",
                "provenance_score": 2,
                "exhibited": True, "published": False,
                "condition": 5, "signed": True,
                "series": "",
            }
        ]
    }
    client = ArtnetClient(api_key="x", base_url="https://example.test")
    with patch.object(ArtnetClient, "_get", return_value=fake):
        df = client.fetch("Ada Quill")
    assert len(df) == 1
    assert df.iloc[0][S.MEDIUM] == "oil"
    assert df.iloc[0][S.SALE_TYPE] == "evening"
    assert df.iloc[0][S.SALE_PRICE_USD] == 250000


def test_mutualart_client_maps_response_and_dimensions_string():
    fake = {
        "items": [
            {
                "artistName": "Bo Marquez",
                "title": "Lot B",
                "mediumCategory": "Oil on canvas",
                "dimensions": "150 × 120 cm",
                "creationYear": 2018,
                "saleDate": "2024-02-14",
                "salePriceUSD": 800000,
                "auctionHouse": "Sotheby's",
                "saleType": "Evening Sale",
                "city": "London",
                "lotNumber": "7",
                "provenanceScore": 3,
                "exhibited": True, "published": True,
                "condition": 5, "signed": True,
            }
        ]
    }
    client = MutualArtClient(api_key="x", base_url="https://example.test")
    with patch.object(MutualArtClient, "_get", return_value=fake):
        df = client.fetch("Bo Marquez")
    assert len(df) == 1
    row = df.iloc[0]
    assert row[S.HEIGHT_CM] == 150
    assert row[S.WIDTH_CM] == 120
    assert row[S.MEDIUM] == "oil"
    assert row[S.SALE_TYPE] == "evening"
    assert row[S.LOCATION] == "london"


def test_liveart_client_two_step_fetch():
    """Resolves artist by name, then fetches sales using its ID."""
    client = LiveArtClient(api_key="x", base_url="https://example.test")

    responses = {}
    def fake_get(path, params):
        if path == "artists":
            return {"artists": [{"id": "a_42", "name": "René Portocarrero"}]}
        if path == "sales":
            responses.setdefault("sales_params", params)
            return {"sales": [
                {
                    "artist_name": "René Portocarrero",
                    "title": "Figuras",
                    "category": "oil",
                    "height_cm": 100, "width_cm": 80,
                    "created_year": 1965,
                    "sold_at": "2024-06-10",
                    "price_usd": 85000,
                    "auction_house_name": "Christie's",
                    "sale_type": "Evening Sale",
                    "city": "New York",
                    "lot_number": 42,
                }
            ]}
        return {}

    with patch.object(LiveArtClient, "_get", side_effect=fake_get):
        df = client.fetch("René Portocarrero")

    assert len(df) == 1
    row = df.iloc[0]
    assert row[S.MEDIUM] == "oil"
    assert row[S.SALE_PRICE_USD] == 85000
    assert row[S.SALE_TYPE] == "evening"
    assert row[S.AUCTION_HOUSE] == "Christie's"
    assert responses["sales_params"]["artist_id"] == "a_42"


def test_liveart_client_returns_empty_when_artist_not_found():
    client = LiveArtClient(api_key="x", base_url="https://example.test")
    with patch.object(LiveArtClient, "_get", return_value={"artists": []}):
        df = client.fetch("Nobody Here")
    assert df.empty


def test_merged_source_dedupes_overlapping_rows():
    overlap = {
        S.ARTIST: "Ada Quill",
        S.TITLE: "Lot A",
        S.MEDIUM: "oil",
        S.HEIGHT_CM: 120, S.WIDTH_CM: 100, S.DEPTH_CM: 0,
        S.YEAR_CREATED: 2010,
        S.SALE_DATE: "2023-05-01",
        S.SALE_PRICE_USD: 250000,
        S.AUCTION_HOUSE: "Christie's",
        S.SALE_TYPE: "evening",
        S.LOCATION: "new york",
        S.LOT_NUMBER: "12",
        S.PROVENANCE_SCORE: 2,
        S.EXHIBITED: True, S.PUBLISHED: False,
        S.CONDITION: 5, S.SIGNED: True,
        S.SERIES: "",
    }
    unique = {**overlap, S.LOT_NUMBER: "13", S.SALE_PRICE_USD: 260000}
    df_artnet = S.validate(pd.DataFrame([overlap]))
    df_mutualart = S.validate(pd.DataFrame([overlap, unique]))

    class Fake:
        def __init__(self, df):
            self._df = df
        def fetch(self, artist):
            return self._df

    merged = MergedSource(Fake(df_artnet), Fake(df_mutualart))
    out = merged.fetch("Ada Quill")
    assert len(out) == 2
    assert set(out[S.LOT_NUMBER]) == {"12", "13"}
