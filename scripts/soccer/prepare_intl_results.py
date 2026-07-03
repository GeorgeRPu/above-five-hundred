#!/usr/bin/env python3
"""Prepare the international football results file used by above500.soccer.wc_spi.

Source: martj42/international_results (CC0), every men's full
international since 1872, updated daily — including scheduled fixtures
(rows with NA scores) for the 2026 World Cup:
https://github.com/martj42/international_results

We keep played matches plus upcoming World Cup fixtures (the fixture
rows carry the real groups and venue/neutral flags used by the
tournament simulation). The model also re-fetches this file at render
time, so the committed archive is only the offline fallback.

Usage:
    curl -sLO https://raw.githubusercontent.com/martj42/international_results/master/results.csv
    uv run python scripts/soccer/prepare_intl_results.py results.csv
"""

import csv
import gzip
import sys

from above500.soccer.wc_spi import DATA as OUT

FIELDS = ["date", "home_team", "away_team", "home_score", "away_score",
          "tournament", "neutral"]


def main(src: str) -> None:
    rows = []
    with open(src, newline="") as f:
        for r in csv.DictReader(f):
            played = r["home_score"] not in ("NA", "")
            future_wc = r["tournament"] == "FIFA World Cup" and not played
            if played or future_wc:
                rows.append({k: r[k] for k in FIELDS})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows):,} rows through {max(r['date'] for r in rows)} "
          f"to {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results.csv")
