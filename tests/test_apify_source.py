from unittest.mock import MagicMock, patch

import pandas as pd

from predictor import schema as S
from predictor.scrapers.apify import ApifyMapping, ApifySource, _parse_price_any


def test_parse_price_any_handles_strings_and_numbers():
    assert _parse_price_any("$2,500,000") == 2_500_000.0
    assert _parse_price_any("USD 1,234.56") == 1234.56
    assert _parse_price_any(450000) == 450000.0
    assert _parse_price_any(None) == 0.0
    assert _parse_price_any("Price on request") == 0.0


def _fake_artsy_items():
    return [
        {
            "title": "Abstrakt 1",
            "artist": "Test Artist",
            "medium": "Oil on canvas",
            "dimensions": "120 x 100 cm",
            "yearCreated": 2018,
            "scrapedAt": "2024-09-01",
            "price": "$45,000",
            "gallery": "Sample Gallery",
            "city": "New York",
        },
        {
            "title": "No Price",
            "artist": "Test Artist",
            "medium": "Oil",
            "dimensions": "80 x 60 cm",
            "yearCreated": 2020,
            "scrapedAt": "2024-09-15",
            "price": "Price on request",
            "gallery": "Gallery X",
            "city": "London",
        },
        {
            "title": "Bad Dimensions",
            "artist": "Test Artist",
            "medium": "Oil",
            "dimensions": "",
            "yearCreated": 2019,
            "scrapedAt": "2024-08-01",
            "price": "$10,000",
            "gallery": "Gallery Y",
            "city": "Paris",
        },
    ]


def test_apify_source_builds_input():
    src = ApifySource(api_token="x", actor_id="parseforge/artsy-scraper", max_items=50)
    payload = src._build_input("René Portocarrero")
    assert payload["mode"] == "artworks"
    assert payload["keyword"] == "René Portocarrero"
    assert payload["maxItems"] == 50
    assert payload["forSale"] is True


def test_apify_source_maps_and_filters():
    src = ApifySource(api_token="x")
    with patch.object(ApifySource, "_run_actor", return_value=_fake_artsy_items()):
        df = src.fetch("Test Artist")
    # Of three items, only the first has price + dimensions + date.
    assert len(df) == 1
    row = df.iloc[0]
    assert row[S.MEDIUM] == "oil"
    assert row[S.HEIGHT_CM] == 120
    assert row[S.WIDTH_CM] == 100
    assert row[S.SALE_PRICE_USD] == 45_000
    assert row[S.AUCTION_HOUSE] == "Sample Gallery"


def test_apify_custom_mapping():
    custom = ApifyMapping(
        artist=lambda i: i["a"],
        title=lambda i: i["t"],
        medium=lambda i: S.normalize_medium(i["med"]),
        dimensions_text=lambda i: i["dim"],
        year_created=lambda i: int(i["y"]),
        sale_date=lambda i: i["d"],
        price_usd=lambda i: float(i["p"]),
        auction_house=lambda i: i["h"],
        sale_type=lambda i: S.normalize_sale_type(i["st"]),
        location=lambda i: i["l"].lower(),
    )
    src = ApifySource(api_token="x", actor_id="custom/auction-actor", mapping=custom)
    fake = [
        {
            "a": "Test Artist", "t": "Lot 1", "med": "oil_on_canvas",
            "dim": "150 x 120 cm", "y": 2015, "d": "2024-05-01",
            "p": 350000, "h": "Christie's", "st": "Evening Sale", "l": "New York",
        }
    ]
    with patch.object(ApifySource, "_run_actor", return_value=fake):
        df = src.fetch("Test Artist")
    assert len(df) == 1
    assert df.iloc[0][S.SALE_TYPE] == "evening"
    assert df.iloc[0][S.AUCTION_HOUSE] == "Christie's"


def test_apify_empty_dataset_returns_empty_df():
    src = ApifySource(api_token="x")
    with patch.object(ApifySource, "_run_actor", return_value=[]):
        df = src.fetch("Test Artist")
    assert df.empty


def test_apify_run_actor_uses_client():
    """Smoke test for the apify-client wiring (mocked)."""
    src = ApifySource(api_token="x", actor_id="parseforge/artsy-scraper")

    fake_dataset = MagicMock()
    fake_dataset.iterate_items.return_value = iter([{"title": "x"}])
    fake_run = {"defaultDatasetId": "ds123"}

    fake_client = MagicMock()
    fake_client.actor.return_value.call.return_value = fake_run
    fake_client.dataset.return_value = fake_dataset

    with patch("apify_client.ApifyClient", return_value=fake_client):
        items = src._run_actor({"keyword": "x"})
    assert items == [{"title": "x"}]
    fake_client.actor.assert_called_once_with("parseforge/artsy-scraper")
    fake_client.dataset.assert_called_once_with("ds123")
