#!/usr/bin/env python3
"""Prepare the RAPTOR player-rating file used by above500.nba_raptor.

Source: FiveThirtyEight's published RAPTOR dataset (CC BY 4.0), every
player-season from 1976-77 through 2021-22 — the model's full run before
538 retired it in 2023:
https://github.com/fivethirtyeight/data/tree/master/nba-raptor

`historical_RAPTOR_by_player.csv` carries, per player-season, the
descriptive RAPTOR plus-minus (offense/defense/total, points per 100
possessions above average), wins above replacement, and 538's *predictive*
sibling rating PREDATOR. We keep just those columns and commit a gzip at
above500/data/nba_raptor.csv.gz so builds never depend on the upstream file
staying available.

Usage:
    curl -sLO https://raw.githubusercontent.com/fivethirtyeight/data/master/nba-raptor/historical_RAPTOR_by_player.csv
    python3 scripts/prepare_raptor_data.py historical_RAPTOR_by_player.csv
"""

import csv
import gzip
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "above500" / "data" / "nba_raptor.csv.gz"

FIELDS = ["player_name", "player_id", "season", "poss", "mp",
          "raptor_offense", "raptor_defense", "raptor_total",
          "war_total", "predator_total"]


def main(src: str) -> None:
    rows = []
    with open(src, newline="") as f:
        for r in csv.DictReader(f):
            rows.append({k: r[k] for k in FIELDS})
    rows.sort(key=lambda r: (int(r["season"]), r["player_name"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    seasons = sorted({int(r["season"]) for r in rows})
    print(f"Wrote {len(rows):,} player-seasons ({seasons[0]}-{seasons[-1]}) "
          f"to {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(sys.argv[1])
