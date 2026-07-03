#!/usr/bin/env python3
"""Prepare the historical NBA game file used by above500.nba_elo.

Two sources, same lineage:

1. FiveThirtyEight's nbaallelo.csv (CC BY 4.0): every NBA/ABA game from
   1946-47 through 2014-15, keyed by franchise, with 538's pre-game home
   win probability:
   https://github.com/fivethirtyeight/data/tree/master/nba-elo
2. Neil Paine's continuation of the 538 Elo file (season 2016 onward),
   keyed by team abbreviation:
   https://github.com/Neil-Paine-1/NBA-elo

Output is one row per game (home perspective) committed at
data/nba/games.csv.gz so builds never depend on either upstream
file staying available. The model can additionally fetch newer games from
source 2 at render time; this script just refreshes the committed archive.

Usage:
    curl -sLO https://raw.githubusercontent.com/fivethirtyeight/data/master/nba-elo/nbaallelo.csv
    curl -sL -o nba_elo_paine.csv https://raw.githubusercontent.com/Neil-Paine-1/NBA-elo/main/nba_elo.csv
    uv run python scripts/nba/prepare_games.py nbaallelo.csv nba_elo_paine.csv
"""

import csv
import gzip
import sys
from datetime import datetime

from above500.nba.elo import DATA as OUT, ABBR_TO_FRANCHISE


FIELDS = ["season", "date", "playoffs", "league",
          "home", "away", "home_pts", "away_pts", "neutral", "p_ref_home"]


def load_538(src: str) -> list[dict]:
    """1946-47 through 2014-15 from nbaallelo.csv (one row per game)."""
    games = []
    with open(src, newline="") as f:
        for r in csv.DictReader(f):
            if r["_iscopy"] != "0":
                continue
            games.append({
                "season": int(r["year_id"]),
                "date": datetime.strptime(r["date_game"], "%m/%d/%Y").date().isoformat(),
                "playoffs": r["is_playoffs"],
                "league": r["lg_id"],
                "home": r["fran_id"],
                "away": r["opp_fran"],
                "home_pts": r["pts"],
                "away_pts": r["opp_pts"],
                "neutral": "1" if r["game_location"] == "N" else "0",
                "p_ref_home": r["forecast"],
            })
    return games


def load_paine(src: str, min_season: int = 2016) -> list[dict]:
    """Season 2016 onward from Neil Paine's nba_elo.csv (mirrored rows)."""
    games = []
    seen_neutral = set()
    with open(src, newline="") as f:
        for r in csv.DictReader(f):
            if int(r["season"]) < min_season:
                continue
            if r["neutral"] == "1":
                key = (r["date"], frozenset((r["team1"], r["team2"])))
                if key in seen_neutral:
                    continue
                seen_neutral.add(key)
            elif r["is_home"] != "1":
                continue
            games.append({
                "season": int(r["season"]),
                "date": r["date"],
                "playoffs": "1" if r["playoff"] == "TRUE" else "0",
                "league": "NBA",
                "home": ABBR_TO_FRANCHISE[r["team1"]],
                "away": ABBR_TO_FRANCHISE[r["team2"]],
                "home_pts": r["score1"],
                "away_pts": r["score2"],
                "neutral": r["neutral"],
                "p_ref_home": r["elo_prob1"],
            })
    return games


def main(src_538: str, src_paine: str | None = None) -> None:
    games = load_538(src_538)
    if src_paine:
        games += load_paine(src_paine)
    games.sort(key=lambda g: (g["date"], g["home"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(games)
    print(f"Wrote {len(games):,} games through {games[-1]['date']} "
          f"to {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
