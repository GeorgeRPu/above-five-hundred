#!/usr/bin/env python3
"""Prepare the Box-RAPTOR training file used by above500.raptor_box.

Source: FiveThirtyEight's own `nba-data-historical.csv` (CC BY 4.0), which
pairs each player-season's box-score rate stats with their RAPTOR rating:
https://github.com/fivethirtyeight/nba-player-advanced-metrics

We keep only the columns the box estimator needs — a handful of box rate
stats as features and 538's RAPTOR offense/defense/WAR as labels — and
commit a gzip at above500/data/nba_player_box.csv.gz. The estimator learns
the box→RAPTOR mapping from this file so it can score seasons 538 never
published. (538 dropped the per-36 columns for 2019-20, so usable training
rows run 1976-77 through 2018-19.)

Usage:
    curl -sLO https://raw.githubusercontent.com/fivethirtyeight/nba-player-advanced-metrics/master/nba-data-historical.csv
    python3 scripts/prepare_player_box.py nba-data-historical.csv
"""

import csv
import gzip
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "above500" / "data" / "nba_player_box.csv.gz"

# upstream column -> our column
COLS = {
    "year_id": "season", "type": "type", "Min": "min", "MPG": "mpg",
    "P/36": "p36", "R/36": "r36", "A/36": "a36", "SB/36": "sb36", "TO/36": "to36",
    "TS%": "ts", "3PAr": "fg3ar", "FTAr": "ftar",
    "Raptor O": "raptor_off", "Raptor D": "raptor_def", "Raptor WAR": "raptor_war",
}


def main(src: str) -> None:
    rows = []
    with open(src, newline="") as f:
        for r in csv.DictReader(f):
            rows.append({dst: r.get(src_col, "") for src_col, dst in COLS.items()})
    rows.sort(key=lambda r: (int(r["season"]), r["type"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(COLS.values()))
        w.writeheader()
        w.writerows(rows)
    seasons = sorted({int(r["season"]) for r in rows})
    print(f"Wrote {len(rows):,} player-seasons ({seasons[0]}-{seasons[-1]}) "
          f"to {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(sys.argv[1])
