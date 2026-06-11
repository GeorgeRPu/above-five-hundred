#!/usr/bin/env python3
"""Prepare the historical NBA game file used by above500.nba_elo.

Source: FiveThirtyEight's nbaallelo.csv (CC BY 4.0), every NBA/ABA game
from 1946-47 through 2014-15:
https://github.com/fivethirtyeight/data/tree/master/nba-elo

We keep one row per game (home-team perspective), only the columns the
model needs, plus 538's own pre-game win probability (`p538`) so the
backtest can benchmark against it. Output is committed at
above500/data/nba_games.csv.gz (~1 MB) so builds never depend on the
upstream file staying available.

Usage:
    curl -sLO https://raw.githubusercontent.com/fivethirtyeight/data/master/nba-elo/nbaallelo.csv
    python3 scripts/prepare_nba_data.py nbaallelo.csv
"""

import csv
import gzip
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "above500" / "data" / "nba_games.csv.gz"

FIELDS = ["gameorder", "season", "date", "playoffs", "league",
          "home", "away", "home_pts", "away_pts", "neutral", "p538_home"]


def main(src: str) -> None:
    games = []
    with open(src, newline="") as f:
        for r in csv.DictReader(f):
            if r["_iscopy"] != "0":
                continue
            games.append({
                "gameorder": int(r["gameorder"]),
                "season": int(r["year_id"]),
                "date": r["date_game"],
                "playoffs": r["is_playoffs"],
                "league": r["lg_id"],
                "home": r["fran_id"],
                "away": r["opp_fran"],
                "home_pts": r["pts"],
                "away_pts": r["opp_pts"],
                "neutral": "1" if r["game_location"] == "N" else "0",
                "p538_home": r["forecast"],
            })

    games.sort(key=lambda g: g["gameorder"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(games)
    print(f"Wrote {len(games):,} games to {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "nbaallelo.csv")
