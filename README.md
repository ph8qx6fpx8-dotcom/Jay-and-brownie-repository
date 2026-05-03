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

The app supports five sources, configurable from the sidebar:

| Source | Status | Setup |
|---|---|---|
| **CSV upload** | Always works | Upload a CSV matching the schema in `predictor/schema.py` |
| **Artnet partner API** | Stub adapter, needs your endpoint | Set `ARTNET_API_KEY` and `ARTNET_BASE_URL` in `.env` |
| **MutualArt partner API** | Stub adapter, needs your endpoint | Set `MUTUALART_API_KEY` and `MUTUALART_BASE_URL` in `.env` |
| **Christie's scraper** | Skeleton; selectors need verification | Run the CLI once with `--dump-html` to inspect markup, then update SELECTORS at top of `predictor/scrapers/christies.py` |
| **Apify actor** | Works as-is for any actor returning the right shape | Set `APIFY_API_TOKEN` in `.env`. Default actor is `parseforge/artsy-scraper` (asking prices, NOT auction prices) |

Both Artnet and MutualArt are partner-only — exact endpoint paths and field
names vary by contract. The adapters in `predictor/data_sources.py` assume
the most common shape and map the typical response fields. If your partner
docs differ, edit `_map_lot` and the path/params in `fetch()` to match.

When more than one source is selected, results are deduplicated by
`(artist, sale_date, auction_house, lot_number)`, keeping the row with the
highest provenance score on collisions.

### Christie's scraper

```bash
# First time: dump one search page so you can see the live HTML and tweak
# the SELECTORS block at the top of predictor/scrapers/christies.py
python -m predictor.scrapers.christies --artist "Gerhard Richter" --dump-html debug.html

# Then a real scrape (1 req/sec, 200-request session cap by default):
python -m predictor.scrapers.christies --artist "Gerhard Richter" --pages 5 \
    --out data/richter.csv -v

# If Cloudflare blocks you: open christies.com in a real browser, solve the
# challenge, copy the cf_clearance cookie value, and pass it via:
python -m predictor.scrapers.christies --artist "Gerhard Richter" --pages 5 \
    --cookie cf_clearance=YOUR_VALUE --out data/richter.csv
```

Scraping christies.com is against their TOS — keep volume low, use it for
personal research only, and don't redistribute the data.

### Apify actor

```python
from predictor.scrapers.apify import ApifySource
src = ApifySource.from_env()                   # default: parseforge/artsy-scraper
df = src.fetch("René Portocarrero")
```

Or via CLI through the Streamlit sidebar. **Important caveat**: the default
Artsy actor returns *gallery listings (asking prices)*, not realized auction
sales. The hedonic predictor was built around realized auction prices, so
results from Artsy data will skew high. For realized data, search the Apify
Store for actors targeting Christie's, Sotheby's, MutualArt, or Heritage
auction-result pages and pass their `actor_id` to `ApifySource`. You can
also pass an `ApifyMapping` to handle non-default field names.

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
