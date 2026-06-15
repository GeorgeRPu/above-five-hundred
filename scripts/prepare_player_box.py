#!/usr/bin/env python3
"""Prepare the Box-RAPTOR training/history file used by above500.raptor_box.

Source: FiveThirtyEight's own `nba-data-historical.csv` (CC BY 4.0), which
pairs each player-season's box-score rate stats with their RAPTOR rating:
https://github.com/fivethirtyeight/nba-player-advanced-metrics

We keep the box rate stats (the estimator's features), 538's RAPTOR
offense/defense/WAR (the labels the mapping is trained on), and the player's
name and id so Box-RAPTOR can be attributed back to people for every season
538 covered. Traded players appear once per team in the upstream file; we
combine those stints into a single player-season (rate stats minutes-weighted,
WAR summed) so each row is one player-season. The result is committed at
above500/data/nba_player_box.csv.gz.

The estimator learns the box->RAPTOR mapping from this file and also scores
these same seasons from it, so the site shows a Box-RAPTOR rating for every
player-season from 1976-77 onward. (538 dropped the per-36 columns for
2019-20, so usable rows run 1976-77 through 2018-19; later seasons come from
above500/data/nba_recent_box.csv.gz and a live top-up.)

Usage:
    curl -sLO https://raw.githubusercontent.com/fivethirtyeight/nba-player-advanced-metrics/master/nba-data-historical.csv
    python3 scripts/prepare_player_box.py nba-data-historical.csv
"""

import csv
import gzip
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "above500" / "data" / "nba_player_box.csv.gz"

FIELDS = ["season", "player_id", "player_name", "type", "g", "min", "mpg",
          "p36", "r36", "a36", "sb36", "to36", "ts", "fg3ar", "ftar",
          "raptor_off", "raptor_def", "raptor_war"]

# our column -> upstream column. Rate stats are minutes-weighted across stints;
# RAPTOR O/D likewise; WAR is a counting stat and is summed.
RATES = {"p36": "P/36", "r36": "R/36", "a36": "A/36", "sb36": "SB/36",
         "to36": "TO/36", "ts": "TS%", "fg3ar": "3PAr", "ftar": "FTAr",
         "raptor_off": "Raptor O", "raptor_def": "Raptor D"}


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def main(src: str) -> None:
    # (player_id, season, type) -> accumulator across the player's stints
    agg: dict[tuple, dict] = {}
    for r in csv.DictReader(open(src, newline="")):
        mn = _f(r["Min"])
        if not mn or mn <= 0:
            continue
        key = (r["player_id"], int(r["year_id"]), r["type"])
        a = agg.get(key)
        if a is None:
            a = agg[key] = {"name": r["name_common"], "min": 0.0, "g": 0,
                            "war": 0.0, "war_seen": False,
                            **{c: [0.0, 0.0] for c in RATES}}  # [wsum, wmin]
        a["min"] += mn
        a["g"] += int(_f(r["G"]) or 0)
        war = _f(r["Raptor WAR"])
        if war is not None:
            a["war"] += war
            a["war_seen"] = True
        for col, up in RATES.items():
            v = _f(r[up])
            if v is not None:
                a[col][0] += v * mn
                a[col][1] += mn

    rows = []
    for (pid, season, typ), a in agg.items():
        row = {"season": season, "player_id": pid, "player_name": a["name"],
               "type": typ, "g": a["g"], "min": round(a["min"], 1),
               "mpg": round(a["min"] / a["g"], 2) if a["g"] else "",
               "raptor_war": round(a["war"], 2) if a["war_seen"] else ""}
        for col in RATES:
            wsum, wmin = a[col]
            row[col] = round(wsum / wmin, 4) if wmin else ""
        rows.append(row)
    rows.sort(key=lambda r: (r["season"], r["type"], r["player_name"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    seasons = sorted({r["season"] for r in rows})
    print(f"Wrote {len(rows):,} player-seasons ({seasons[0]}-{seasons[-1]}) "
          f"to {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(sys.argv[1])
