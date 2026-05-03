"""Streamlit UI: Art Auction Price Predictor."""
from __future__ import annotations

import os
from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from predictor import schema as S
from predictor.data_sources import (
    ArtnetClient,
    CsvSource,
    MergedSource,
    MutualArtClient,
)
from predictor.model import fit_hedonic
from predictor.scrapers.apify import DEFAULT_ACTOR_ID, ApifySource
from predictor.scrapers.christies import ChristiesSource, CloudflareBlocked

load_dotenv()


def _hydrate_secrets() -> None:
    """Streamlit Cloud injects credentials via st.secrets, not .env.
    Mirror them into os.environ so the existing data-source clients work unchanged.
    """
    try:
        for key, value in st.secrets.items():
            if isinstance(value, str) and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


_hydrate_secrets()

st.set_page_config(page_title="Art Auction Price Predictor", layout="wide")
st.title("Art Auction Price Predictor")
st.caption(
    "Hedonic regression on an artist's past sales → price estimate for an upcoming lot, "
    "plus a 10-year projection."
)

SAMPLE_PATH = Path(__file__).parent / "sample_data" / "sample_sales.csv"


def _load_csv_dataframe(uploaded_file, use_sample: bool) -> pd.DataFrame | None:
    if use_sample and SAMPLE_PATH.exists():
        return CsvSource(SAMPLE_PATH).all_data()
    if uploaded_file is not None:
        text = uploaded_file.read().decode("utf-8")
        df = pd.read_csv(StringIO(text))
        return S.validate(df)
    return None


def _api_dataframe(
    use_artnet: bool,
    use_mutualart: bool,
    use_christies: bool,
    christies_pages: int,
    use_apify: bool,
    apify_actor: str,
    apify_max_items: int,
    artist: str,
) -> pd.DataFrame:
    sources = []
    if use_artnet:
        sources.append(ArtnetClient.from_env())
    if use_mutualart:
        sources.append(MutualArtClient.from_env())
    if use_christies:
        sources.append(ChristiesSource(pages=christies_pages))
    if use_apify:
        sources.append(ApifySource.from_env(actor_id=apify_actor, max_items=apify_max_items))
    if not sources:
        return pd.DataFrame()
    src = sources[0] if len(sources) == 1 else MergedSource(*sources)
    return src.fetch(artist)


with st.sidebar:
    st.header("Data sources")
    use_sample = st.checkbox("Use bundled sample CSV", value=True)
    uploaded = st.file_uploader("Or upload your own CSV", type=["csv"]) if not use_sample else None
    use_artnet = st.checkbox("Artnet API", value=False, help="Reads ARTNET_API_KEY/ARTNET_BASE_URL")
    use_mutualart = st.checkbox(
        "MutualArt API", value=False, help="Reads MUTUALART_API_KEY/MUTUALART_BASE_URL"
    )
    use_christies = st.checkbox(
        "Christie's scraper",
        value=False,
        help="Polite scrape of christies.com past results. TOS-restricted; personal use only.",
    )
    christies_pages = st.slider("Christie's pages to scrape", 1, 20, 5) if use_christies else 5
    use_apify = st.checkbox(
        "Apify actor",
        value=False,
        help="Run an Apify scraper actor (handles anti-bot for you). Reads APIFY_API_TOKEN.",
    )
    if use_apify:
        apify_actor = st.text_input(
            "Apify actor ID",
            value=DEFAULT_ACTOR_ID,
            help="Default targets Artsy gallery LISTINGS (asking prices, not realized sales). "
                 "For realized auction data, swap in an auction-results actor from the Apify Store.",
        )
        apify_max_items = st.number_input("Max items per fetch", 10, 1000, 100, step=10)
        if "artsy" in apify_actor.lower():
            st.caption(
                "⚠️ Artsy actors return gallery asking prices, not realized auction prices — "
                "the predictor will skew high."
            )
    else:
        apify_actor = DEFAULT_ACTOR_ID
        apify_max_items = 100
    api_artist = st.text_input(
        "Artist name (for API/scraper fetch)", value="", help="Used when an API or scraper source is enabled"
    )

    st.divider()
    st.header("Projection settings")
    market_drift_pct = st.slider(
        "Annual market drift (% on top of artist trend)",
        min_value=-5.0, max_value=10.0, value=0.0, step=0.5,
    )
    horizon_years = st.slider("Horizon (years)", 1, 20, 10)
    ridge_alpha = st.slider("Ridge regularization (α)", 0.1, 10.0, 1.0, 0.1)


df: pd.DataFrame | None = None
errors = []
try:
    df = _load_csv_dataframe(uploaded, use_sample)
except Exception as e:
    errors.append(f"CSV load error: {e}")

if (use_artnet or use_mutualart or use_christies or use_apify) and api_artist:
    try:
        with st.spinner(f"Fetching auction data for {api_artist}..."):
            api_df = _api_dataframe(
                use_artnet,
                use_mutualart,
                use_christies,
                christies_pages,
                use_apify,
                apify_actor,
                apify_max_items,
                api_artist.strip(),
            )
        if df is None or df.empty:
            df = api_df
        else:
            df = pd.concat([df, api_df], ignore_index=True)
        if api_df is not None and not api_df.empty:
            st.success(f"Fetched {len(api_df)} lots for {api_artist}.")
    except CloudflareBlocked as e:
        errors.append(
            f"Christie's blocked the scrape (Cloudflare). Open christies.com in a browser, "
            f"solve the challenge, and supply the cf_clearance cookie via the CLI for now: {e}"
        )
    except Exception as e:
        errors.append(f"API fetch error: {e}")

for err in errors:
    st.error(err)

if df is None or df.empty:
    st.info("Load data from the sidebar to get started (sample CSV is on by default).")
    st.stop()

artists = sorted(df[S.ARTIST].dropna().unique().tolist())
artist = st.selectbox("Artist", artists)

artist_df = df[df[S.ARTIST] == artist]
if len(artist_df) < 6:
    st.warning(
        f"Only {len(artist_df)} sales for {artist}. Need at least 6 to fit a model."
    )
    st.stop()

try:
    model = fit_hedonic(df, artist, alpha=ridge_alpha)
except Exception as e:
    st.error(f"Model fit failed: {e}")
    st.stop()

summary = model.summary()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lots in training set", summary["n_lots"])
c2.metric("R²", f"{summary['r_squared']:.2f}")
c3.metric("Annual price trend", f"{summary['annual_trend_pct']:+.1f}%")
c4.metric("Median historical price", f"${summary['median_price_usd']:,.0f}")
st.caption(
    f"Date range: {summary['date_range'][0]} → {summary['date_range'][1]} · "
    f"Top media: {', '.join(summary['top_media'].keys())} · "
    f"Top houses: {', '.join(summary['top_houses'].keys())}"
)

st.divider()
st.subheader("Upcoming lot")

with st.form("lot_form"):
    f1, f2, f3 = st.columns(3)
    medium = f1.selectbox("Medium", sorted(S.MEDIUM_VALUES), index=sorted(S.MEDIUM_VALUES).index("oil"))
    height = f2.number_input("Height (cm)", min_value=1.0, max_value=2000.0, value=120.0)
    width = f3.number_input("Width (cm)", min_value=1.0, max_value=2000.0, value=100.0)

    f4, f5, f6 = st.columns(3)
    depth = f4.number_input("Depth (cm, sculpture only)", min_value=0.0, max_value=2000.0, value=0.0)
    year_created = f5.number_input("Year created", min_value=1500, max_value=2100, value=2010)
    sale_date_input = f6.date_input("Auction date", value=date.today())

    f7, f8, f9 = st.columns(3)
    house_options = sorted(artist_df[S.AUCTION_HOUSE].dropna().unique().tolist()) or ["Christie's"]
    auction_house = f7.selectbox("Auction house", house_options)
    sale_type = f8.selectbox("Sale type", ["evening", "day", "online", "regional"])
    location_options = sorted(artist_df[S.LOCATION].dropna().unique().tolist()) or ["new york"]
    location = f9.selectbox("Location", location_options)

    f10, f11, f12 = st.columns(3)
    provenance = f10.slider(
        "Provenance score (0=none, 3=museum/blue-chip)", 0, 3, 1
    )
    condition = f11.slider("Condition (1=poor, 5=excellent)", 1, 5, 4)
    series_options = sorted(artist_df[S.SERIES].fillna("").unique().tolist())
    series = f12.selectbox("Series (optional)", series_options or [""])

    f13, f14, f15 = st.columns(3)
    exhibited = f13.checkbox("Exhibited (major show)", value=False)
    published = f14.checkbox("Published (catalogue/monograph)", value=False)
    signed = f15.checkbox("Signed", value=True)

    submit = st.form_submit_button("Estimate price")

if submit:
    lot = {
        "medium": medium,
        "height_cm": height,
        "width_cm": width,
        "depth_cm": depth,
        "year_created": int(year_created),
        "sale_date": pd.Timestamp(sale_date_input),
        "auction_house": auction_house,
        "sale_type": sale_type,
        "location": location,
        "provenance_score": int(provenance),
        "condition": int(condition),
        "exhibited": exhibited,
        "published": published,
        "signed": signed,
        "series": series,
    }

    pred = model.predict(lot, ci=0.80)
    drift = market_drift_pct / 100.0
    proj = model.project(lot, years=horizon_years, market_drift=drift, ci=0.80)

    st.subheader("Estimate")
    e1, e2, e3 = st.columns(3)
    e1.metric("Point estimate", f"${pred['point_usd']:,.0f}")
    e2.metric("80% CI low", f"${pred['low_usd']:,.0f}")
    e3.metric("80% CI high", f"${pred['high_usd']:,.0f}")

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=proj["year"], y=proj["high_usd"],
            mode="lines", line=dict(width=0), showlegend=False,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=proj["year"], y=proj["low_usd"],
            mode="lines", line=dict(width=0),
            fill="tonexty", name="80% CI",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=proj["year"], y=proj["point_usd"],
            mode="lines+markers", name="Point estimate",
        )
    )
    fig.update_layout(
        title=f"{artist}: projected price for this lot, {proj['year'].min()}–{proj['year'].max()}",
        xaxis_title="Year",
        yaxis_title="USD",
        yaxis_tickformat="$,.0f",
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Year-by-year projection")
    show = proj[["year", "point_usd", "low_usd", "high_usd"]].copy()
    for col in ["point_usd", "low_usd", "high_usd"]:
        show[col] = show[col].map(lambda v: f"${v:,.0f}")
    st.dataframe(show, use_container_width=True, hide_index=True)

    st.subheader("Top 5 most-comparable past sales")
    comps = model.comparables(lot, k=5)
    st.dataframe(comps, use_container_width=True, hide_index=True)

    with st.expander("Caveats"):
        st.markdown(
            "- The model fits one Ridge regression on this artist's history; "
            "outliers and macro shocks are smoothed out.\n"
            "- The projection extrapolates the artist's trend plus your market-drift "
            "slider. Confidence widens with horizon.\n"
            "- Categorical levels not seen in training (e.g. a brand-new auction "
            "house or sale type) fall back to the trained dummies = 0 baseline.\n"
            "- Estimates exclude buyer's premium adjustments beyond what the data "
            "already encode (`sale_price_usd` should be hammer + premium)."
        )
