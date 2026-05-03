from pathlib import Path

import pytest

from predictor import schema as S
from predictor.scrapers.christies import (
    LotSummary,
    _looks_like_cloudflare_challenge,
    _parse_date,
    _parse_price_usd,
    _parse_year,
    _provenance_score,
    _summary_and_detail_to_canonical,
    parse_lot_detail,
    parse_search_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_search_page_extracts_cards():
    html = (FIXTURES / "christies_search_sample.html").read_text()
    summaries = parse_search_page(html)
    assert len(summaries) == 2
    s = summaries[0]
    assert s.url.endswith("/lot/abstract-1-1234")
    assert s.title == "Abstraktes Bild (No. 1)"
    assert s.artist == "Gerhard Richter"
    assert "USD 2,500,000" in (s.price_text or "")
    assert "Evening Sale" in (s.sale_info or "")


def test_parse_lot_detail_extracts_fields():
    html = (FIXTURES / "christies_lot_sample.html").read_text()
    detail = parse_lot_detail(html, "https://www.christies.com/lot/abstract-1-1234")
    assert detail["title"] == "Abstraktes Bild (No. 1)"
    assert "Gerhard Richter" in detail["artist"]
    assert "oil on canvas" in detail["medium_year_text"]
    assert "200" in detail["dimensions_text"] and "180" in detail["dimensions_text"]
    assert "USD 2,500,000" in detail["price_text"]
    assert "Evening Sale" in detail["sale_title"]
    assert "14 May 2023" in detail["sale_date_text"]
    assert "Marian Goodman" in detail["provenance_text"]
    assert "Tate Modern" in detail["exhibited_text"]


def test_parse_helpers():
    assert _parse_price_usd("Price realized: USD 2,500,000") == 2_500_000.0
    assert _parse_price_usd("$1,234") == 1234.0
    assert _parse_price_usd("Withdrawn") == 0.0
    assert _parse_date("Sold 14 May 2023") == "2023-05-14"
    assert _parse_date("2024-02-14") == "2024-02-14"
    assert _parse_date("no date here") is None
    assert _parse_year("oil on canvas, 2010") == 2010
    assert _parse_year("painted in 1987 and signed") == 1987
    assert _provenance_score("Museum of Modern Art, New York") == 3
    assert _provenance_score("Private Collection, London") == 2
    assert _provenance_score("Marian Goodman Gallery") == 1
    assert _provenance_score("") == 0


def test_summary_and_detail_to_canonical_full_pipeline():
    summary_html = (FIXTURES / "christies_search_sample.html").read_text()
    lot_html = (FIXTURES / "christies_lot_sample.html").read_text()
    summaries = parse_search_page(summary_html)
    detail = parse_lot_detail(lot_html, summaries[0].url)
    row = _summary_and_detail_to_canonical(summaries[0], detail, artist_query="Gerhard Richter")
    assert row is not None
    assert row[S.ARTIST].startswith("Gerhard Richter")
    assert row[S.MEDIUM] == "oil"
    assert row[S.HEIGHT_CM] == 200
    assert row[S.WIDTH_CM] == 180
    assert row[S.YEAR_CREATED] == 2010
    assert row[S.SALE_DATE] == "2023-05-14"
    assert row[S.SALE_PRICE_USD] == 2_500_000
    assert row[S.AUCTION_HOUSE] == "Christie's"
    assert row[S.SALE_TYPE] == "evening"
    assert row[S.PROVENANCE_SCORE] == 1  # gallery
    assert row[S.EXHIBITED] is True
    assert row[S.PUBLISHED] is True


def test_drops_lots_without_price():
    s = LotSummary(url="/lot/x", title="x", artist="x", price_text=None, sale_info=None)
    detail = {
        "title": "x", "artist": "x", "medium_year_text": "oil, 2010",
        "dimensions_text": "100 x 100 cm", "price_text": None,
        "sale_title": "Evening Sale", "sale_date_text": "14 May 2023",
        "sale_location": "New York", "provenance_text": "",
        "exhibited_text": "", "literature_text": "",
    }
    assert _summary_and_detail_to_canonical(s, detail, "x") is None


def test_cloudflare_detection():
    page = "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>"
    assert _looks_like_cloudflare_challenge(page)
    normal = "<html><head><title>Search results</title></head></html>"
    assert not _looks_like_cloudflare_challenge(normal)
