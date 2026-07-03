#!/usr/bin/env python3
"""Build historical national-team roster strength from EA FIFA player ratings.

Source: ZaidGhazal/FIFA-Qatar-2022-WorldCup-Predictions on GitHub, which
has sofifa-format player CSVs for FIFA 15 through FIFA 22.  For each
World Cup edition (2014, 2018, 2022) we pick the closest pre-tournament
FIFA release, apply the same age-weighted top-23 aggregation used by
scripts/fetch_roster.py, and write the results to
above500/data/roster_ratings_history.json.

    python3 scripts/prepare_roster_history.py
"""

from __future__ import annotations

import csv
import io
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "above500" / "data" / "roster_ratings_history.json"
TOP_N = 23

BASE_URL = (
    "https://raw.githubusercontent.com/ZaidGhazal/"
    "FIFA-Qatar-2022-WorldCup-Predictions/main/data/players_data"
)

EDITIONS = {
    "2014": {"fifa_version": 15, "wc_start": "2014-06-12", "file": "players_15.csv"},
    "2018": {"fifa_version": 18, "wc_start": "2018-06-14", "file": "players_18.csv"},
    "2022": {"fifa_version": 22, "wc_start": "2022-11-20", "file": "players_22.csv"},
}

NAME_FIXUPS = {
    "Korea Republic": "South Korea",
    "Côte d'Ivoire": "Ivory Coast",
    "Congo DR": "DR Congo",
    "Curacao": "Curaçao",
    "Cape Verde Islands": "Cape Verde",
    "Cabo Verde": "Cape Verde",
    "Czechia": "Czech Republic",
    "Türkiye": "Turkey",
    "China PR": "China",
}


def _age_weight(age: int) -> float:
    """Peak around 24-29; discount the raw young and the ageing."""
    if age <= 21:
        return 0.85 + 0.03 * (age - 18)
    if age <= 29:
        return 1.0
    return max(0.5, 1.0 - 0.06 * (age - 29))


def _fetch_and_aggregate(filename: str) -> dict[str, dict]:
    url = f"{BASE_URL}/{filename}"
    print(f"  fetching {url} …")
    with urllib.request.urlopen(url, timeout=120) as resp:
        text = resp.read().decode("utf-8", "replace")

    by_nation: dict[str, list[float]] = {}
    for r in csv.DictReader(io.StringIO(text)):
        nation = NAME_FIXUPS.get(r["nationality_name"], r["nationality_name"])
        try:
            overall, age = int(r["overall"]), int(r["age"])
        except (TypeError, ValueError, KeyError):
            continue
        by_nation.setdefault(nation, []).append(overall * _age_weight(age))

    teams = {}
    for nation, scores in by_nation.items():
        top = sorted(scores, reverse=True)[:TOP_N]
        teams[nation] = {"rating": round(sum(top) / len(top), 3), "n_players": len(top)}
    return teams


def main() -> None:
    editions = {}
    for wc_year, info in EDITIONS.items():
        print(f"FIFA {info['fifa_version']} → {wc_year} World Cup")
        teams = _fetch_and_aggregate(info["file"])
        print(f"  rated {len(teams)} nations")
        if len(teams) < 24:
            sys.exit(f"Too few nations for {wc_year} — source format may have changed.")
        editions[wc_year] = {
            "fifa_version": info["fifa_version"],
            "wc_start": info["wc_start"],
            "teams": dict(sorted(teams.items())),
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "stefanoleone992/fifa-players-dataset via ZaidGhazal (sofifa.com)",
        "metric": "age-weighted mean of top-23 overall ratings",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "editions": editions,
    }, indent=2) + "\n")
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
