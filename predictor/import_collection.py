"""Convert the 'Collection' sheet of the art-collection master spreadsheet
into the app's portfolio CSV.

    python -m predictor.import_collection \\
        --xlsx data/art_collection_master_v10_3.xlsx \\
        --out data/collection_portfolio.csv

The spreadsheet is the source of truth; this script only reshapes it.
Dimensions are in inches (converted to cm unless a "(... cm)" note is
present), the medium is inferred from the title, and the spreadsheet's
own low/mid/high estimates are carried through as est_value_*_usd so the
app can show them next to the model's figures.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

from predictor import schema as S
from predictor.portfolio import (
    ACQUIRED_DATE,
    ACQUIRED_PRICE_USD,
    EST_HIGH,
    EST_LOW,
    EST_MID,
)

SHEET = "Collection"
HEADER_ROW = 3  # zero-based row index of the column headers in the sheet

COLS = {
    "#": "sheet_row",
    "Artist": S.ARTIST,
    "Title / Work": S.TITLE,
    "Year": "year_original",
    "Dims (in)": "dims_original",
    "Source / Channel": "source",
    "Total Cost All-In ($)": ACQUIRED_PRICE_USD,
    "Est Value Low ($ gross)": EST_LOW,
    "Est Value High ($ gross)": EST_HIGH,
    "Mid ($ gross)": EST_MID,
    "Net Realizable ($)": "net_realizable_usd",
    "Liquidity": "liquidity",
    "Confidence": "confidence",
    "Recommendation": "recommendation",
    "Cost Evidence": "cost_evidence",
    "Notes / Corrections": "notes",
}

OUTPUT_COLUMNS = [
    S.ARTIST, S.TITLE, S.MEDIUM, S.HEIGHT_CM, S.WIDTH_CM, S.DEPTH_CM, S.YEAR_CREATED,
    ACQUIRED_PRICE_USD, ACQUIRED_DATE,
    S.PROVENANCE_SCORE, S.EXHIBITED, S.PUBLISHED, S.CONDITION, S.SIGNED, S.SERIES,
    EST_LOW, EST_MID, EST_HIGH, "net_realizable_usd",
    "source", "recommendation", "liquidity", "confidence",
    "dims_original", "year_original", "sheet_row", "cost_evidence", "notes",
]

NUM = r"\d+(?:\.\d+)?"
CM_RE = re.compile(rf"\(\s*({NUM})\s*x\s*({NUM})(?:\s*x\s*({NUM}))?\s*cm\s*\)", re.I)
DIA_RE = re.compile(rf"({NUM})\s*(?:in\s*)?(?:dia\b|diameter)", re.I)
DIM_RE = re.compile(
    rf"({NUM})\s*(?:in\s*)?([hwl])?\s*x\s*({NUM})\s*(?:in\s*)?([hwl])?(?:\s*x\s*({NUM})\s*(?:in\s*)?([hwl])?)?",
    re.I,
)
YEAR_RE = re.compile(r"(?<!\d)(1[89]\d{2}|20\d{2})(?!\d)")  # also matches '1950s', 'c.1956'
DATE_RE = re.compile(
    r"(?:\b(\d{1,2})(?:-\d{1,2})?\s+)?"
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{4})\b",
    re.I,
)
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_dims_cm(text) -> tuple[float, float, float]:
    """Return (height, width, depth) in cm; zeros when the sheet has no usable size."""
    s = str(text or "").strip()
    if not s or s == "-" or "shipping" in s.lower():
        return 0.0, 0.0, 0.0
    m = CM_RE.search(s)
    if m:
        return float(m[1]), float(m[2]), float(m[3] or 0)
    m = DIA_RE.search(s)
    if m:
        v = float(m[1]) * 2.54
        return v, v, 0.0
    core = re.sub(r"\([^)]*\)", " ", s)
    m = DIM_RE.search(core)
    if not m:
        return 0.0, 0.0, 0.0
    a, la, b, lb, c, _ = m.groups()
    h, w, d = float(a), float(b), float(c) if c else 0.0
    if (la or "").lower() == "w" and (lb or "").lower() == "h":
        h, w = w, h
    return h * 2.54, w * 2.54, d * 2.54


def parse_year(text) -> int:
    m = YEAR_RE.search(str(text or ""))
    return int(m[1]) if m else 0


def parse_date(text) -> str:
    m = DATE_RE.search(str(text or ""))
    if not m:
        return ""
    day = int(m[1]) if m[1] else 1
    return f"{int(m[3]):04d}-{MONTHS[m[2].lower()[:3]]:02d}-{min(max(day, 1), 28):02d}"


PAPER = ("on paper", "work on paper", "watercolor", "watercolour", "gouache", "ink", "charcoal",
         "graphite", "pencil", "pastel", "conte", "mylar", "cardboard", "cut-paper")
PRINT = ("print", "inkjet", "lithograph", "screenprint", "etching", "edition", "ed.")
SCULPT = ("bronze", "ceramic", "giltwood", "relief", "vase", "bowl", "chair", "gold leaf",
          "wood blocks", "tacos de madera", "sculpture", "plexi", "object")


def infer_medium(title) -> str:
    t = str(title or "").lower()
    if any(k in t for k in PAPER):
        return "works_on_paper"
    if any(k in t for k in PRINT):
        return "print"
    if "photograph" in t or "photo " in t:
        return "photograph"
    if "oil" in t:
        return "oil"
    if any(k in t for k in ("acrylic", "liquitex", "latex", "flashe", "enamel", "batik")):
        return "acrylic"
    if any(k in t for k in SCULPT):
        return "sculpture"
    if "mixed media" in t or "collage" in t:
        return "mixed_media"
    return "other"


def convert(xlsx: Path, sheet: str = SHEET) -> pd.DataFrame:
    raw = pd.read_excel(xlsx, sheet_name=sheet, header=HEADER_ROW)
    missing = [c for c in COLS if c not in raw.columns]
    if missing:
        raise S.SchemaError(f"Sheet '{sheet}' is missing expected columns: {missing}")
    raw = raw[pd.to_numeric(raw["#"], errors="coerce").notna()].rename(columns=COLS)

    out = pd.DataFrame()
    out[S.ARTIST] = raw[S.ARTIST].astype(str).str.strip()
    out[S.TITLE] = raw[S.TITLE].fillna("").astype(str).str.strip()
    out[S.MEDIUM] = out[S.TITLE].apply(infer_medium)
    dims = raw["dims_original"].apply(parse_dims_cm)
    out[S.HEIGHT_CM] = [round(d[0], 1) for d in dims]
    out[S.WIDTH_CM] = [round(d[1], 1) for d in dims]
    out[S.DEPTH_CM] = [round(d[2], 1) if m == "sculpture" else 0.0 for d, m in zip(dims, out[S.MEDIUM])]
    out[S.YEAR_CREATED] = raw["year_original"].apply(parse_year)
    out[ACQUIRED_PRICE_USD] = pd.to_numeric(raw[ACQUIRED_PRICE_USD], errors="coerce").fillna(0.0).round(2)
    out[ACQUIRED_DATE] = raw["source"].apply(parse_date)
    out[S.PROVENANCE_SCORE] = 0
    out[S.EXHIBITED] = False
    out[S.PUBLISHED] = False
    out[S.CONDITION] = 4
    out[S.SIGNED] = out[S.TITLE].str.lower().str.contains("signed")
    out[S.SERIES] = ""
    for col in (EST_LOW, EST_MID, EST_HIGH, "net_realizable_usd"):
        out[col] = pd.to_numeric(raw[col], errors="coerce").fillna(0.0).round(2)
    for col in ("source", "recommendation", "liquidity", "confidence",
                "dims_original", "year_original", "cost_evidence", "notes"):
        out[col] = raw[col].fillna("").astype(str).str.strip()
    out["sheet_row"] = raw["sheet_row"].astype(int)
    return out[OUTPUT_COLUMNS].reset_index(drop=True)


def report(df: pd.DataFrame) -> str:
    n = len(df)
    lines = [
        f"{n} works, {df[S.ARTIST].nunique()} artists",
        f"  with dimensions: {(df[S.HEIGHT_CM] > 0).sum()}/{n}",
        f"  with year:       {(df[S.YEAR_CREATED] > 0).sum()}/{n}",
        f"  with cost:       {(df[ACQUIRED_PRICE_USD] > 0).sum()}/{n}",
        f"  with estimate:   {(df[EST_MID] > 0).sum()}/{n}",
        f"  with acq. date:  {(df[ACQUIRED_DATE] != '').sum()}/{n}",
        "  medium: " + ", ".join(f"{k}={v}" for k, v in df[S.MEDIUM].value_counts().items()),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Convert the collection master spreadsheet to a portfolio CSV.")
    p.add_argument("--xlsx", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--sheet", default=SHEET)
    args = p.parse_args(argv)
    try:
        df = convert(Path(args.xlsx), args.sheet)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(report(df))
    print(f"Wrote {len(df)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
