"""Generate a deterministic synthetic sample dataset of ~90 lots across 3 artists.

Run once: python sample_data/_generate.py
The output is committed; this file exists only to make the data reproducible.
"""
from __future__ import annotations

import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "sample_sales.csv"

ARTISTS = [
    {
        "name": "Ada Quill",
        "year_active": (1985, 2024),
        "base_log_price": math.log(45_000),
        "annual_trend": 0.07,
        "media_premia": {"oil": 0.30, "acrylic": 0.10, "works_on_paper": -0.40, "print": -0.90},
        "size_elasticity": 0.65,
    },
    {
        "name": "Bo Marquez",
        "year_active": (1995, 2024),
        "base_log_price": math.log(180_000),
        "annual_trend": 0.11,
        "media_premia": {"oil": 0.25, "mixed_media": 0.10, "sculpture": 0.40, "print": -1.10},
        "size_elasticity": 0.55,
    },
    {
        "name": "Chen Lin",
        "year_active": (1978, 2010),
        "base_log_price": math.log(900_000),
        "annual_trend": 0.04,
        "media_premia": {"oil": 0.40, "works_on_paper": -0.50, "print": -1.30},
        "size_elasticity": 0.70,
    },
]

HOUSES_TIER1 = ["Christie's", "Sotheby's", "Phillips"]
HOUSES_TIER2 = ["Bonhams", "Heritage", "Doyle"]
LOCATIONS_TIER1 = ["New York", "London", "Hong Kong"]
LOCATIONS_TIER2 = ["Paris", "Los Angeles", "Chicago"]
SALE_TYPES = ["evening", "day", "online"]


def gen_for_artist(artist: dict, n: int, rng: random.Random) -> list[dict]:
    rows = []
    media = list(artist["media_premia"].keys())
    start = date(2019, 1, 15)
    for _ in range(n):
        days = rng.randint(0, 365 * 5 - 1)
        sale_date = start + timedelta(days=days)
        sale_year_frac = sale_date.year + (sale_date.timetuple().tm_yday - 1) / 365.25

        medium = rng.choices(media, weights=[3, 2, 1, 1][: len(media)])[0]
        if medium == "sculpture":
            h = rng.uniform(40, 220)
            w = rng.uniform(30, 200)
            d = rng.uniform(20, 150)
        elif medium == "print" or medium == "works_on_paper":
            h = rng.uniform(20, 90)
            w = rng.uniform(20, 120)
            d = 0
        else:
            h = rng.uniform(50, 250)
            w = rng.uniform(40, 300)
            d = 0

        year_created = rng.randint(*artist["year_active"])
        is_evening = rng.random() < 0.35
        sale_type = "evening" if is_evening else rng.choices(SALE_TYPES, weights=[0, 4, 1])[0]
        if is_evening:
            house = rng.choice(HOUSES_TIER1)
            location = rng.choice(LOCATIONS_TIER1)
        else:
            house = rng.choice(HOUSES_TIER1 + HOUSES_TIER2)
            location = rng.choice(LOCATIONS_TIER1 + LOCATIONS_TIER2)

        provenance_score = rng.choices([0, 1, 2, 3], weights=[3, 4, 2, 1])[0]
        exhibited = rng.random() < 0.30
        published = rng.random() < 0.25
        condition = rng.choices([3, 4, 5], weights=[1, 5, 4])[0]
        signed = rng.random() < 0.85
        series = rng.choices(["", "blue period", "studies"], weights=[7, 2, 1])[0]

        log_p = artist["base_log_price"]
        log_p += artist["size_elasticity"] * (math.log(h * w) - math.log(120 * 100))
        log_p += artist["media_premia"].get(medium, 0.0)
        log_p += artist["annual_trend"] * (sale_year_frac - 2021.5)
        if sale_type == "evening":
            log_p += 0.45
        elif sale_type == "online":
            log_p -= 0.25
        if house in HOUSES_TIER1:
            log_p += 0.25
        elif house not in HOUSES_TIER2:
            log_p -= 0.15
        log_p += 0.18 * provenance_score
        log_p += 0.10 if exhibited else 0.0
        log_p += 0.07 if published else 0.0
        log_p += 0.08 * (condition - 4)
        log_p += 0.05 if signed else -0.10
        if series == "blue period":
            log_p += 0.20
        log_p += rng.gauss(0, 0.18)

        price = max(500.0, math.exp(log_p))

        rows.append(
            {
                "artist": artist["name"],
                "title": f"Untitled ({rng.randint(1, 9999)})",
                "medium": medium,
                "height_cm": round(h, 1),
                "width_cm": round(w, 1),
                "depth_cm": round(d, 1),
                "year_created": year_created,
                "sale_date": sale_date.isoformat(),
                "sale_price_usd": round(price, 2),
                "auction_house": house,
                "sale_type": sale_type,
                "location": location,
                "lot_number": str(rng.randint(1, 600)),
                "provenance_score": provenance_score,
                "exhibited": exhibited,
                "published": published,
                "condition": condition,
                "signed": signed,
                "series": series,
            }
        )
    return rows


def main() -> None:
    rng = random.Random(42)
    rows: list[dict] = []
    for artist in ARTISTS:
        rows.extend(gen_for_artist(artist, n=32, rng=rng))
    rows.sort(key=lambda r: (r["artist"], r["sale_date"]))
    cols = list(rows[0].keys())
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
