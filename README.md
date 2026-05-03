# Art Auction Price Predictor

Estimate what an artist's upcoming auction lot should sell for, plus a 10-year
projection — based on a hedonic regression fit to that artist's past 5 years
(or however much you have) of auction history.

## What it does

- **Inputs**: artist, medium, size (height × width × depth), year created,
  auction date, auction house, sale type, location, provenance/exhibition/
  publication, condition, signed, series.
- **Output**: a current-day USD point estimate with an 80% confidence
  interval, a year-by-year projection over the next 10 years (configurable),
  and the 5 most-comparable past sales so you can sanity-check.

The model is a per-artist Ridge regression on `log(sale_price_usd)` over the
hedonic features above. The fitted `sale_year` coefficient is the artist's
estimated annual price trend; combined with a user-tunable broader market
drift, that's what drives the projection. Confidence intervals widen with
horizon (`σ × √(1 + h/T)`).

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

The bundled sample CSV (`sample_data/sample_sales.csv`) covers three
synthetic artists so you can try the full flow immediately. Pick one,
fill in the upcoming-lot form, click **Estimate price**.

## Data sources

The app supports three sources, configurable from the sidebar:

| Source | Status | Setup |
|---|---|---|
| **CSV upload** | Always works | Upload a CSV matching the schema in `predictor/schema.py` |
| **Artnet partner API** | Stub adapter, needs your endpoint | Set `ARTNET_API_KEY` and `ARTNET_BASE_URL` in `.env` |
| **MutualArt partner API** | Stub adapter, needs your endpoint | Set `MUTUALART_API_KEY` and `MUTUALART_BASE_URL` in `.env` |

Both Artnet and MutualArt are partner-only — exact endpoint paths and field
names vary by contract. The adapters in `predictor/data_sources.py` assume
the most common shape (`/price-database/lots` for Artnet,
`/auction-results` for MutualArt) and map the typical response fields. If
your partner docs differ, edit `_map_lot` and the path/params in
`fetch()` to match.

When more than one source is selected, results are deduplicated by
`(artist, sale_date, auction_house, lot_number)`, keeping the row with the
highest provenance score on collisions.

## CSV schema

Required columns: `artist, medium, height_cm, width_cm, year_created,
sale_date, sale_price_usd, auction_house, sale_type`.

Optional but recommended: `title, depth_cm, location, lot_number,
provenance_score (0–3), exhibited (bool), published (bool), condition
(1–5), signed (bool), series`.

`predictor/schema.py` normalizes media (`oil_on_canvas` → `oil`,
`watercolor` → `works_on_paper`, etc.) and sale types
(`Evening Sale` → `evening`).

## Project layout

```
app.py                    # Streamlit UI
predictor/
  schema.py               # Canonical columns + validators
  features.py             # area, age_at_sale, tier mapping, dummies
  model.py                # ArtistPriceModel: fit, predict, project, comparables
  data_sources.py         # Csv/Artnet/MutualArt/Merged adapters
sample_data/
  sample_sales.csv        # 96 synthetic lots across 3 artists
  _generate.py            # How sample_sales.csv was produced (re-run to regenerate)
tests/
  test_features.py
  test_model.py
  test_data_sources.py
```

## Running the tests

```bash
pytest -q
```

## Limitations

- One regression per artist — no cross-artist transfer. Need ≥6 sales to fit.
- The "next 10 years" projection extrapolates the in-sample trend plus your
  market-drift slider; it does **not** ingest a real macro art-market index.
  For a serious forecast, override `market_drift` with a reasoned macro
  scenario (Mei Moses, Artnet Index) rather than trusting the default.
- The confidence interval widening (`√(1 + h/T)`) is a heuristic, not a
  proper Bayesian posterior — it tells you "the further out, the less you
  should trust the point estimate" but isn't formally calibrated.
- Buyer's premium is only handled correctly if your input prices already
  include it. If the source is hammer-only, gross it up before loading.
