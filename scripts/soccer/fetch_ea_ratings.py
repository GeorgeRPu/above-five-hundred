#!/usr/bin/env python3
"""Build national-team roster strength from the EA Sports FC 26 ratings.

Source: EAFC26-DataHub (https://github.com/ismailoksuz/EAFC26-DataHub),
which commits the full ~18k-player FC 26 database to GitHub. We read it
straight from raw GitHub — no API key, no rate limit, one fetch — and
for each nation compute an age-weighted mean of its best players'
overall ratings. FC 26 (the 2025-26 edition) reflects current squads and
club-level quality, which is the roster signal FiveThirtyEight blended
into its World Cup SPI.

Writes the derived per-nation aggregate to
data/soccer/ea_ratings.json (we keep only the 48 numbers, not EA's
player rows). Run by .github/workflows/refresh-roster.yml or directly:

    uv run python scripts/soccer/fetch_ea_ratings.py
"""

from __future__ import annotations

import csv
import io
import sys
import urllib.request
from datetime import datetime, timezone

from above500.soccer.wc_spi import FIFA_CODES  # the 48 nations, in our naming

from above500.soccer.ea_roster import ROSTER_FILE as OUT
SOURCE = "https://raw.githubusercontent.com/ismailoksuz/EAFC26-DataHub/main/data/players.csv"
TOP_N = 23   # a full matchday squad

# EA FC nationality_name -> our naming, where they differ.
NAME_FIXUPS = {
    "Korea Republic": "South Korea", "Czechia": "Czech Republic",
    "Côte d'Ivoire": "Ivory Coast", "Türkiye": "Turkey",
    "Cabo Verde": "Cape Verde", "Congo DR": "DR Congo", "Curacao": "Curaçao",
}


def _age_weight(age: int) -> float:
    """Peak around 24-29; discount the raw young and the ageing."""
    if age <= 21:
        return 0.85 + 0.03 * (age - 18)
    if age <= 29:
        return 1.0
    return max(0.5, 1.0 - 0.06 * (age - 29))


def main() -> None:
    with urllib.request.urlopen(SOURCE, timeout=120) as resp:
        text = resp.read().decode("utf-8", "replace")

    by_nation: dict[str, list[float]] = {}
    for r in csv.DictReader(io.StringIO(text)):
        nation = NAME_FIXUPS.get(r["nationality_name"], r["nationality_name"])
        if nation not in FIFA_CODES:
            continue
        try:
            overall, age = int(r["overall"]), int(r["age"])
        except (TypeError, ValueError):
            continue
        by_nation.setdefault(nation, []).append(overall * _age_weight(age))

    teams = {}
    for nation, scores in by_nation.items():
        top = sorted(scores, reverse=True)[:TOP_N]
        teams[nation] = {"rating": round(sum(top) / len(top), 3), "n_players": len(top)}

    missing = sorted(set(FIFA_CODES) - set(teams))
    print(f"rated {len(teams)}/48 nations" + (f" | missing: {missing}" if missing else ""))
    if len(teams) < 24:
        sys.exit("Too few nations rated — source format may have changed.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(__import__("json").dumps({
        "source": "EA Sports FC 26 (EAFC26-DataHub)",
        "metric": "age-weighted mean of top-23 overall ratings",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "teams": dict(sorted(teams.items())),
    }, indent=2) + "\n")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
