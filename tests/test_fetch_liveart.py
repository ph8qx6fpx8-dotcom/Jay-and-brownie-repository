from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from predictor import schema as S
from predictor.data_sources import LiveArtClient
from predictor.fetch_liveart import fetch_to_csv, main


def _one_row_df(lot_number="42", price=85000):
    return S.validate(pd.DataFrame([{
        S.ARTIST: "Test Artist",
        S.TITLE: "Lot",
        S.MEDIUM: "oil",
        S.HEIGHT_CM: 100, S.WIDTH_CM: 80, S.DEPTH_CM: 0,
        S.YEAR_CREATED: 1965,
        S.SALE_DATE: "2024-06-10",
        S.SALE_PRICE_USD: price,
        S.AUCTION_HOUSE: "Christie's",
        S.SALE_TYPE: "evening",
        S.LOCATION: "new york",
        S.LOT_NUMBER: lot_number,
        S.PROVENANCE_SCORE: 0,
        S.EXHIBITED: False, S.PUBLISHED: False,
        S.CONDITION: 4, S.SIGNED: True,
        S.SERIES: "",
    }]))


def test_fetch_to_csv_writes_new_file(tmp_path):
    client = LiveArtClient(api_key="x", base_url="https://example.test")
    out = tmp_path / "out.csv"
    with patch.object(LiveArtClient, "fetch", return_value=_one_row_df()):
        n = fetch_to_csv("Test Artist", out, client=client)
    assert n == 1
    assert out.exists()
    assert len(pd.read_csv(out)) == 1


def test_fetch_to_csv_appends_and_dedupes(tmp_path):
    client = LiveArtClient(api_key="x", base_url="https://example.test")
    out = tmp_path / "out.csv"
    with patch.object(LiveArtClient, "fetch", return_value=_one_row_df("42", 85000)):
        fetch_to_csv("Test Artist", out, client=client)
    with patch.object(LiveArtClient, "fetch", return_value=_one_row_df("42", 85000)):
        n_same = fetch_to_csv("Test Artist", out, client=client)
    assert n_same == 1  # duplicate dropped
    with patch.object(LiveArtClient, "fetch", return_value=_one_row_df("43", 90000)):
        n_added = fetch_to_csv("Test Artist", out, client=client)
    assert n_added == 2


def test_fetch_to_csv_overwrite(tmp_path):
    client = LiveArtClient(api_key="x", base_url="https://example.test")
    out = tmp_path / "out.csv"
    with patch.object(LiveArtClient, "fetch", return_value=_one_row_df("42")):
        fetch_to_csv("Test Artist", out, client=client)
    with patch.object(LiveArtClient, "fetch", return_value=_one_row_df("99")):
        n = fetch_to_csv("Test Artist", out, overwrite=True, client=client)
    assert n == 1
    assert pd.read_csv(out).iloc[0]["lot_number"] == 99


def test_main_exits_cleanly_without_api_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LIVEART_API_KEY", raising=False)
    rc = main(["--artist", "X", "--out", str(tmp_path / "out.csv")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "LIVEART_API_KEY" in err


def test_main_runs_fetch(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LIVEART_API_KEY", "fake")
    with patch("predictor.fetch_liveart.LiveArtClient.fetch", return_value=_one_row_df()):
        rc = main(["--artist", "Test", "--out", str(tmp_path / "out.csv")])
    assert rc == 0
    assert "Wrote 1 total rows" in capsys.readouterr().out
