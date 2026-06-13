#!/usr/bin/env python3
"""Build the committed floor of recent box scores for above500.raptor_box.

538's RAPTOR stops after 2021-22. To extend the model, the box estimator
needs raw box scores for newer seasons. This script aggregates per-player
season totals from NocturneBear's openly-posted NBA box-score dump (sourced
from nba.com), which runs through 2023-24:
https://github.com/NocturneBear/NBA-Data-2010-2024

Only seasons after 538's last RAPTOR year are kept, so the committed file
covers exactly the gap the estimator fills. At render time the model adds
any newer seasons live from balldontlie; this archive is the offline floor.

Usage:
    python3 scripts/prepare_recent_box.py
"""

import csv
import gzip
import io
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "above500" / "data" / "nba_recent_box.csv.gz"
BASE = ("https://raw.githubusercontent.com/NocturneBear/NBA-Data-2010-2024/main/"
        "regular_season_box_scores_2010_2024_part_{}.csv")
PARTS = (1, 2, 3)
AFTER_SEASON = 2022          # 538's last RAPTOR season (2021-22); keep newer

FIELDS = ["season", "name", "g", "min", "fga", "fg3a", "fta",
          "trb", "ast", "stl", "blk", "tov", "pts"]
# our column -> upstream box-score column
COUNTS = {"fga": "fieldGoalsAttempted", "fg3a": "threePointersAttempted",
          "fta": "freeThrowsAttempted", "trb": "reboundsTotal", "ast": "assists",
          "stl": "steals", "blk": "blocks", "tov": "turnovers", "pts": "points"}


def parse_minutes(s: str) -> float:
    if not s:
        return 0.0
    if ":" in s:
        m, sec = s.split(":")
        return int(m) + int(sec) / 60
    try:
        return float(s)
    except ValueError:
        return 0.0


def season_end_year(season_year: str) -> int:
    # "2022-23" -> 2023, matching 538's year_id convention
    start, end2 = season_year.split("-")
    return int(start[:2] + end2)


def main() -> None:
    agg: dict[tuple[int, str], dict] = {}
    for n in PARTS:
        with urllib.request.urlopen(BASE.format(n), timeout=120) as resp:
            text = resp.read().decode()
        for r in csv.DictReader(io.StringIO(text)):
            season = season_end_year(r["season_year"])
            if season <= AFTER_SEASON:
                continue
            mp = parse_minutes(r["minutes"])
            if mp <= 0:
                continue
            key = (season, r["personName"])
            a = agg.setdefault(key, {"g": 0, "min": 0.0, **{c: 0 for c in COUNTS}})
            a["g"] += 1
            a["min"] += mp
            for col, src in COUNTS.items():
                v = r[src]
                a[col] += int(v) if v not in ("", "None") else 0

    rows = []
    for (season, name), a in agg.items():
        rows.append({"season": season, "name": name, "g": a["g"],
                     "min": round(a["min"], 1),
                     **{c: a[c] for c in COUNTS}})
    rows.sort(key=lambda r: (r["season"], r["name"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    seasons = sorted({r["season"] for r in rows})
    print(f"Wrote {len(rows):,} player-seasons ({seasons}) "
          f"to {OUT} ({OUT.stat().st_size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
