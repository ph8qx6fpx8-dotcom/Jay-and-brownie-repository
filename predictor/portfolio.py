"""Value a portfolio of owned artworks against an artist sales history.

Each portfolio row is an artwork you hold. For every row we fit (or reuse)
that artist's hedonic model from the sales data, then ask: what would this
work fetch at auction on the valuation date under the chosen sale scenario,
and what does the trend imply N years out?

Usage (CLI):
    python -m predictor.portfolio --sales data/sales.csv \\
        --portfolio my_portfolio.csv --out valued.csv \\
        --sale-type day --horizon 10 --drift 0.0
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from predictor import schema as S
from predictor.model import ArtistPriceModel, fit_hedonic

ACQUIRED_PRICE_USD = "acquired_price_usd"
ACQUIRED_DATE = "acquired_date"

PORTFOLIO_REQUIRED = [S.ARTIST, S.MEDIUM, S.HEIGHT_CM, S.WIDTH_CM, S.YEAR_CREATED]
PORTFOLIO_OPTIONAL_DEFAULTS = {
    S.TITLE: "",
    S.DEPTH_CM: 0.0,
    ACQUIRED_PRICE_USD: 0.0,
    ACQUIRED_DATE: "",
    S.PROVENANCE_SCORE: 0,
    S.EXHIBITED: False,
    S.PUBLISHED: False,
    S.CONDITION: 4,
    S.SIGNED: False,
    S.SERIES: "",
}
MIN_SALES = 6


def validate_portfolio(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in PORTFOLIO_REQUIRED if c not in df.columns]
    if missing:
        raise S.SchemaError(f"Portfolio is missing required columns: {missing}")
    out = df.copy()
    for col, default in PORTFOLIO_OPTIONAL_DEFAULTS.items():
        if col not in out.columns:
            out[col] = default
        else:
            out[col] = out[col].fillna(default)

    out[S.ARTIST] = out[S.ARTIST].astype(str).str.strip()
    out[S.TITLE] = out[S.TITLE].astype(str)
    out[S.MEDIUM] = out[S.MEDIUM].apply(S.normalize_medium)
    out[S.SERIES] = out[S.SERIES].astype(str)
    out[ACQUIRED_DATE] = out[ACQUIRED_DATE].astype(str)
    for col in [S.HEIGHT_CM, S.WIDTH_CM, S.DEPTH_CM, ACQUIRED_PRICE_USD]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    out[S.YEAR_CREATED] = pd.to_numeric(out[S.YEAR_CREATED], errors="coerce").fillna(0).astype(int)
    out[S.PROVENANCE_SCORE] = (
        pd.to_numeric(out[S.PROVENANCE_SCORE], errors="coerce").fillna(0).astype(int).clip(0, 3)
    )
    out[S.CONDITION] = pd.to_numeric(out[S.CONDITION], errors="coerce").fillna(4).astype(int).clip(1, 5)
    for col in [S.EXHIBITED, S.PUBLISHED, S.SIGNED]:
        out[col] = out[col].apply(_to_bool)

    bad = out[(out[S.HEIGHT_CM] <= 0) | (out[S.WIDTH_CM] <= 0)]
    if not bad.empty:
        raise S.SchemaError(f"{len(bad)} portfolio rows have missing/zero height or width.")
    return out.reset_index(drop=True)


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


def _mode_first(series: pd.Series, fallback: str) -> str:
    """Most common value; ties broken alphabetically (matches pandas mode()[0])."""
    vals = series.dropna().astype(str)
    vals = vals[vals != ""]
    if vals.empty:
        return fallback
    return str(vals.mode().iloc[0])


def artist_scenario(sales_df: pd.DataFrame, artist: str) -> dict:
    """Default auction context for valuing an artist's work: their modal house/location."""
    sub = sales_df[sales_df[S.ARTIST] == artist]
    return {
        "auction_house": _mode_first(sub[S.AUCTION_HOUSE], "Christie's"),
        "location": _mode_first(sub[S.LOCATION], "other"),
    }


def value_portfolio(
    sales_df: pd.DataFrame,
    portfolio_df: pd.DataFrame,
    sale_type: str = "day",
    valuation_date: str | pd.Timestamp | None = None,
    horizon_years: int = 10,
    market_drift: float = 0.0,
    alpha: float = 1.0,
    ci: float = 0.80,
) -> pd.DataFrame:
    """One output row per portfolio row, with current + horizon estimates."""
    portfolio = validate_portfolio(portfolio_df)
    vdate = pd.Timestamp(valuation_date) if valuation_date else pd.Timestamp.today().normalize()
    sale_type = S.normalize_sale_type(sale_type)

    models: dict[str, ArtistPriceModel | str] = {}
    sales_counts = sales_df[S.ARTIST].value_counts()

    def model_for(artist: str) -> ArtistPriceModel | str:
        if artist in models:
            return models[artist]
        n = int(sales_counts.get(artist, 0))
        if n == 0:
            models[artist] = "no sales data for this artist"
        elif n < MIN_SALES:
            models[artist] = f"only {n} sales; need at least {MIN_SALES}"
        else:
            models[artist] = fit_hedonic(sales_df, artist, alpha=alpha)
        return models[artist]

    rows = []
    for _, w in portfolio.iterrows():
        base = {
            S.ARTIST: w[S.ARTIST],
            S.TITLE: w[S.TITLE],
            S.MEDIUM: w[S.MEDIUM],
            S.HEIGHT_CM: w[S.HEIGHT_CM],
            S.WIDTH_CM: w[S.WIDTH_CM],
            S.YEAR_CREATED: w[S.YEAR_CREATED],
            ACQUIRED_PRICE_USD: w[ACQUIRED_PRICE_USD],
            ACQUIRED_DATE: w[ACQUIRED_DATE],
        }
        m = model_for(w[S.ARTIST])
        if isinstance(m, str):
            rows.append({**base, "status": m, "n_sales": int(sales_counts.get(w[S.ARTIST], 0))})
            continue

        scenario = artist_scenario(sales_df, w[S.ARTIST])
        lot = {
            "medium": w[S.MEDIUM],
            "height_cm": w[S.HEIGHT_CM],
            "width_cm": w[S.WIDTH_CM],
            "depth_cm": w[S.DEPTH_CM],
            "year_created": w[S.YEAR_CREATED],
            "sale_date": vdate,
            "auction_house": scenario["auction_house"],
            "sale_type": sale_type,
            "location": scenario["location"],
            "provenance_score": w[S.PROVENANCE_SCORE],
            "exhibited": w[S.EXHIBITED],
            "published": w[S.PUBLISHED],
            "condition": w[S.CONDITION],
            "signed": w[S.SIGNED],
            "series": w[S.SERIES],
        }
        now = m.predict(lot, ci=ci)
        proj = m.project(lot, years=horizon_years, market_drift=market_drift, ci=ci)
        last = proj.iloc[-1]
        acquired = float(w[ACQUIRED_PRICE_USD] or 0)
        gain = now["point_usd"] - acquired if acquired > 0 else float("nan")
        rows.append({
            **base,
            "status": "ok",
            "n_sales": m.n_lots,
            "scenario_house": scenario["auction_house"],
            "scenario_location": scenario["location"],
            "scenario_sale_type": sale_type,
            "valuation_date": vdate.date().isoformat(),
            "now_point_usd": now["point_usd"],
            "now_low_usd": now["low_usd"],
            "now_high_usd": now["high_usd"],
            "horizon_year": int(last["year"]),
            "horizon_point_usd": float(last["point_usd"]),
            "horizon_low_usd": float(last["low_usd"]),
            "horizon_high_usd": float(last["high_usd"]),
            "gain_usd": gain,
            "gain_pct": (gain / acquired * 100.0) if acquired > 0 else float("nan"),
        })
    return pd.DataFrame(rows)


def portfolio_totals(results: pd.DataFrame) -> dict:
    ok = results[results["status"] == "ok"] if "status" in results.columns else results.iloc[0:0]
    with_cost = ok[ok[ACQUIRED_PRICE_USD] > 0] if not ok.empty else ok
    total_cost = float(with_cost[ACQUIRED_PRICE_USD].sum()) if not with_cost.empty else 0.0
    now_on_cost = float(with_cost["now_point_usd"].sum()) if not with_cost.empty else 0.0
    return {
        "n_works": int(len(results)),
        "n_valued": int(len(ok)),
        "n_skipped": int(len(results) - len(ok)),
        "now_point_usd": float(ok["now_point_usd"].sum()) if not ok.empty else 0.0,
        "now_low_usd": float(ok["now_low_usd"].sum()) if not ok.empty else 0.0,
        "now_high_usd": float(ok["now_high_usd"].sum()) if not ok.empty else 0.0,
        "horizon_point_usd": float(ok["horizon_point_usd"].sum()) if not ok.empty else 0.0,
        "horizon_low_usd": float(ok["horizon_low_usd"].sum()) if not ok.empty else 0.0,
        "horizon_high_usd": float(ok["horizon_high_usd"].sum()) if not ok.empty else 0.0,
        "acquired_total_usd": total_cost,
        "gain_usd": (now_on_cost - total_cost) if total_cost > 0 else float("nan"),
        "gain_pct": ((now_on_cost - total_cost) / total_cost * 100.0) if total_cost > 0 else float("nan"),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Value a portfolio of artworks against auction sales history.")
    p.add_argument("--sales", required=True, help="Sales CSV in the canonical schema.")
    p.add_argument("--portfolio", required=True, help="Portfolio CSV (artist, medium, height_cm, width_cm, year_created, ...).")
    p.add_argument("--out", help="Write per-work results to this CSV.")
    p.add_argument("--sale-type", default="day", choices=["evening", "day", "online", "regional"])
    p.add_argument("--date", help="Valuation date YYYY-MM-DD (default: today).")
    p.add_argument("--horizon", type=int, default=10)
    p.add_argument("--drift", type=float, default=0.0, help="Extra annual market drift as a decimal, e.g. 0.02.")
    args = p.parse_args(argv)

    try:
        sales = S.validate(pd.read_csv(args.sales))
        portfolio = pd.read_csv(args.portfolio)
        results = value_portfolio(
            sales, portfolio,
            sale_type=args.sale_type, valuation_date=args.date,
            horizon_years=args.horizon, market_drift=args.drift,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        results.to_csv(args.out, index=False)

    t = portfolio_totals(results)
    fmt = lambda v: f"${v:,.0f}"
    print(f"Valued {t['n_valued']} of {t['n_works']} works ({t['n_skipped']} skipped).")
    print(f"Now:      {fmt(t['now_point_usd'])}  (range {fmt(t['now_low_usd'])} – {fmt(t['now_high_usd'])})")
    print(f"Horizon:  {fmt(t['horizon_point_usd'])}  (range {fmt(t['horizon_low_usd'])} – {fmt(t['horizon_high_usd'])})")
    if t["acquired_total_usd"] > 0:
        print(f"Cost:     {fmt(t['acquired_total_usd'])}  → gain {fmt(t['gain_usd'])} ({t['gain_pct']:+.1f}%)")
    skipped = results[results["status"] != "ok"]
    for _, r in skipped.iterrows():
        print(f"  skipped: {r[S.ARTIST]} — {r[S.TITLE] or '(untitled)'}: {r['status']}", file=sys.stderr)
    if args.out:
        print(f"Wrote {len(results)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
