#!/usr/bin/env python3
"""Prepare the Box-RAPTOR training/history file used by above500.raptor_box.

Sources (both CC BY 4.0):
  * FiveThirtyEight's `nba-data-historical.csv` — box-score rate stats plus
    composite RAPTOR for every player-season 1976-77 through 2018-19.
    https://github.com/fivethirtyeight/nba-player-advanced-metrics
  * FiveThirtyEight's `modern_RAPTOR_by_player.csv` — the box/on-off
    decomposition of RAPTOR for 2013-14 through 2021-22.
    https://github.com/fivethirtyeight/data/tree/master/nba-raptor

We keep the box rate stats (the estimator's features) and, as training labels,
the *box component* of RAPTOR from the modern file (available for 2014-2019,
the overlap period). The composite RAPTOR O/D from the historical file is
retained for seasons outside the overlap. We also split the combined rebound
and stock rates (R/36, SB/36) into offensive/defensive boards and steals/blocks
(orb36, drb36, stl36, blk36), so the model can weight each by side the way 538
does. The result is committed at above500/data/nba_player_box.csv.gz.

Usage:
    curl -sLO https://raw.githubusercontent.com/fivethirtyeight/nba-player-advanced-metrics/master/nba-data-historical.csv
    curl -sLO https://raw.githubusercontent.com/fivethirtyeight/data/master/nba-raptor/modern_RAPTOR_by_player.csv
    python3 scripts/prepare_player_box.py nba-data-historical.csv modern_RAPTOR_by_player.csv
"""

import csv
import gzip
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "above500" / "data" / "nba_player_box.csv.gz"

FIELDS = ["season", "player_id", "player_name", "type", "team", "g", "min", "mpg",
          "p36", "r36", "a36", "sb36", "to36", "ts", "fg3ar", "ftar",
          "orb36", "drb36", "stl36", "blk36",
          "raptor_off", "raptor_def", "raptor_war",
          "raptor_box_off", "raptor_box_def"]

# 538's team_id is the Basketball-Reference abbreviation of the moment; map a
# player's franchise to its current abbreviation (matching the site's logos and
# the franchise-continuity Elo model) so even old seasons show a logo.
TEAM_FIX = {"BRK": "BKN", "PHO": "PHX", "CHO": "CHA"}

# our column -> upstream column. Rate stats are minutes-weighted across stints;
# RAPTOR O/D likewise; WAR is a counting stat and is summed.
RATES = {"p36": "P/36", "r36": "R/36", "a36": "A/36", "sb36": "SB/36",
         "to36": "TO/36", "ts": "TS%", "fg3ar": "3PAr", "ftar": "FTAr",
         "raptor_off": "Raptor O", "raptor_def": "Raptor D"}

# 538's historical file carries rebounds and stocks only as combined per-36
# rates (R/36, SB/36), but it also has the offensive/defensive and steal/block
# splits as percentages (ORB%, DRB%, STL%, BLK%). We apportion the combined
# per-36 by those percentage pairs, which conserves the per-36 total and
# captures how a player's boards/stocks divide — the split signal the model
# wants. The percentages are minutes-weighted across stints like the rates.
SPLIT_PCTS = {"orb_pct": "ORB%", "drb_pct": "DRB%",
              "stl_pct": "STL%", "blk_pct": "BLK%"}


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _load_modern_box(path: str) -> dict[tuple[str, int], tuple[float, float]]:
    """Load {(player_id, season): (box_off, box_def)} from the modern RAPTOR file."""
    lookup: dict[tuple[str, int], tuple[float, float]] = {}
    for r in csv.DictReader(open(path, newline="")):
        bo, bd = _f(r["raptor_box_offense"]), _f(r["raptor_box_defense"])
        if bo is None or bd is None:
            continue
        lookup[(r["player_id"], int(r["season"]))] = (bo, bd)
    return lookup


def main(src: str, modern_src: str | None = None) -> None:
    modern = _load_modern_box(modern_src) if modern_src else {}
    src_rows = list(csv.DictReader(open(src, newline="")))

    # Each franchise's current abbreviation = the team_id it last used, so a
    # relocated/renamed franchise (Sonics -> Thunder, etc.) maps to one logo.
    franch_abbr: dict[str, tuple[int, str]] = {}
    for r in src_rows:
        fid, year = r["franch_id"], int(r["year_id"])
        if fid not in franch_abbr or year > franch_abbr[fid][0]:
            franch_abbr[fid] = (year, r["team_id"])

    # (player_id, season, type) -> accumulator across the player's stints
    agg: dict[tuple, dict] = {}
    for r in src_rows:
        mn = _f(r["Min"])
        if not mn or mn <= 0:
            continue
        key = (r["player_id"], int(r["year_id"]), r["type"])
        a = agg.get(key)
        if a is None:
            a = agg[key] = {"name": r["name_common"], "min": 0.0, "g": 0,
                            "war": 0.0, "war_seen": False, "franch_min": {},
                            **{c: [0.0, 0.0] for c in RATES},
                            **{c: [0.0, 0.0] for c in SPLIT_PCTS}}  # [wsum, wmin]
        a["min"] += mn
        a["franch_min"][r["franch_id"]] = a["franch_min"].get(r["franch_id"], 0.0) + mn
        a["g"] += int(_f(r["G"]) or 0)
        war = _f(r["Raptor WAR"])
        if war is not None:
            a["war"] += war
            a["war_seen"] = True
        for col, up in {**RATES, **SPLIT_PCTS}.items():
            v = _f(r[up])
            if v is not None:
                a[col][0] += v * mn
                a[col][1] += mn

    rows = []
    matched = 0
    for (pid, season, typ), a in agg.items():
        # primary franchise = the one the player logged the most minutes for
        franch = max(a["franch_min"], key=a["franch_min"].get) if a["franch_min"] else ""
        abbr = franch_abbr.get(franch, (0, ""))[1]
        row = {"season": season, "player_id": pid, "player_name": a["name"],
               "type": typ, "team": TEAM_FIX.get(abbr, abbr),
               "g": a["g"], "min": round(a["min"], 1),
               "mpg": round(a["min"] / a["g"], 2) if a["g"] else "",
               "raptor_war": round(a["war"], 2) if a["war_seen"] else ""}
        for col in RATES:
            wsum, wmin = a[col]
            row[col] = round(wsum / wmin, 4) if wmin else ""

        # split combined rebounds (R/36) and stocks (SB/36) by the matching
        # percentage pair; an even split if a pair is missing so the per-36
        # total is always conserved.
        def _split(combined_col, pct_a, pct_b):
            tot = _f(row[combined_col])
            wa, wmn_a = a[pct_a]
            wb, wmn_b = a[pct_b]
            pa = wa / wmn_a if wmn_a else None
            pb = wb / wmn_b if wmn_b else None
            if tot is None:
                return "", ""
            if pa is None or pb is None or (pa + pb) <= 0:
                return round(tot / 2, 4), round(tot / 2, 4)
            return round(tot * pa / (pa + pb), 4), round(tot * pb / (pa + pb), 4)

        row["orb36"], row["drb36"] = _split("r36", "orb_pct", "drb_pct")
        row["stl36"], row["blk36"] = _split("sb36", "stl_pct", "blk_pct")
        box = modern.get((pid, season))
        if box is not None and typ == "RS":
            row["raptor_box_off"] = round(box[0], 4)
            row["raptor_box_def"] = round(box[1], 4)
            matched += 1
        else:
            row["raptor_box_off"] = ""
            row["raptor_box_def"] = ""
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
    if modern:
        print(f"Matched {matched:,} RS player-seasons with box RAPTOR from the modern file")


if __name__ == "__main__":
    modern = sys.argv[2] if len(sys.argv) > 2 else None
    main(sys.argv[1], modern)
