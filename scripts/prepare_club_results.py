#!/usr/bin/env python3
"""Prepare a club-match results archive for the club-SPI roster prior.

FiveThirtyEight's roster-based World Cup SPI is built from "SPI ratings
for thousands of club teams across the globe" — a club version of the
same attack/defence model. We reproduce the club side here from free,
fetchable match results, pulling three kinds of source so the ratings are
both broad and *cross-league calibrated*:

  * Domestic leagues — the big European top flights (openfootball) plus
    non-European leagues where many World Cup players play (MLS, Liga MX,
    Brazil, Argentina, Japan, Scotland, Turkey, Belgium, Greece, via
    football-data.co.uk).
  * Continental cups — UEFA Champions League / Europa / Conference and
    Copa Libertadores / Sudamericana (openfootball). These are the key
    addition: inter-league matches let the model tell that (say) the
    English league is stronger than the Dutch one, instead of each league
    being an isolated zero-sum system.

Everything lands in one schema (date, league, home, away, goals) in
above500/data/club_results.csv.gz, which feeds above500/club_spi.py.
Club names are reconciled across sources at fit time by above500.club_names.

    python3 scripts/prepare_club_results.py
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "above500" / "data" / "club_results.csv.gz"
FIELDS = ["date", "league", "home", "away", "home_goals", "away_goals"]

OF_API = "https://api.github.com/repos/openfootball/football.json/contents"
OF_RAW = "https://raw.githubusercontent.com/openfootball/football.json/master"
CL_RAW = "https://raw.githubusercontent.com/openfootball/champions-league/master"
SA_RAW = "https://raw.githubusercontent.com/openfootball/south-america/master"
CWC_RAW = "https://raw.githubusercontent.com/openfootball/club-worldcup/master"
FD = "https://www.football-data.co.uk"

# Big European top flights from openfootball football.json (consistent coverage)
OF_LEAGUES = ["en.1", "es.1", "de.1", "it.1", "fr.1", "nl.1", "pt.1", "at.1"]

# Continental cups (openfootball .txt). Seasons span 2011-12 .. 2025-26.
CL_FILES = ["cl", "el", "conf"]
# Copa Libertadores / Sudamericana and the Club World Cup, by calendar year.
# The Club World Cup is the only inter-continental club competition, so it is
# what links the European and South American rating islands together.
SA_YEARS = list(range(2012, 2027))
CWC_YEARS = list(range(2012, 2027))

# football-data.co.uk extra leagues (one all-time file each, new/<code>.csv)
FD_EXTRA = {"USA": "MLS", "MEX": "LigaMX", "BRA": "BrazilA",
            "ARG": "ArgentinaP", "JPN": "JLeague"}
# football-data.co.uk main files (mmz4281/<season>/<div>.csv) — leagues not in
# openfootball; we avoid the big-5 divisions there to prevent double counting.
FD_MAIN = {"SC0": "Scotland", "T1": "Turkey", "B1": "Belgium", "G1": "Greece"}
FD_SEASONS = [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(2010, 2026)]

_WEEKDAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_MATCH_RE = re.compile(
    r"^(?:\d{1,2}:\d{2}\s+)?(.+?)\s+\(\w{2,4}\)\s+v\s+(.+?)\s+\(\w{2,4}\)\s+(\d+)-(\d+)")


def _get(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=90) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception:
        return None


def _iso(dmy: str) -> str | None:
    """football-data.co.uk dd/mm/yyyy (or dd/mm/yy) -> yyyy-mm-dd."""
    parts = dmy.split("/")
    if len(parts) != 3:
        return None
    d, m, y = parts
    if len(y) == 2:
        y = "20" + y
    return f"{y}-{int(m):02d}-{int(d):02d}"


# --- openfootball football.json (domestic, JSON) ---------------------------

def _seasons() -> list[str]:
    items = json.loads(_get(OF_API) or "[]")
    return sorted(i["name"] for i in items
                  if i["type"] == "dir" and "-" in i["name"])


def _fetch_of_json(season: str, league: str) -> list[dict]:
    data = _get(f"{OF_RAW}/{season}/{league}.json")
    if not data:
        return []
    rows = []
    for m in json.loads(data).get("matches", []):
        score = m.get("score")
        ft = score.get("ft") if isinstance(score, dict) else m.get("score_ft")
        if not (isinstance(ft, list) and len(ft) == 2) or not m.get("date"):
            continue
        try:
            rows.append({"date": m["date"], "league": league,
                         "home": m["team1"], "away": m["team2"],
                         "home_goals": int(ft[0]), "away_goals": int(ft[1])})
        except (TypeError, ValueError):
            continue
    return rows


# --- openfootball .txt (continental cups) ----------------------------------

def _parse_txt(text: str, league: str) -> list[dict]:
    rows, year, date = [], None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "=#▪":
            continue
        toks = line.split()
        if toks[0] in _WEEKDAYS and len(toks) >= 3 and toks[1] in _MONTHS:
            day = int(toks[2])
            if len(toks) >= 4 and toks[3].isdigit():
                year = int(toks[3])
            if year:
                date = f"{year}-{_MONTHS[toks[1]]:02d}-{day:02d}"
            continue
        m = _MATCH_RE.match(line)
        if m and date:
            rows.append({"date": date, "league": league,
                         "home": m.group(1).strip(), "away": m.group(2).strip(),
                         "home_goals": int(m.group(3)), "away_goals": int(m.group(4))})
    return rows


# --- football-data.co.uk (CSV) ---------------------------------------------

def _fetch_fd(url: str, league: str, home_k: str, away_k: str,
              hg_k: str, ag_k: str) -> list[dict]:
    text = _get(url)
    if not text:
        return []
    rows = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        date, hg, ag = _iso(r.get("Date", "")), r.get(hg_k), r.get(ag_k)
        if not date or not hg or not ag:
            continue
        try:
            rows.append({"date": date, "league": league,
                         "home": r[home_k], "away": r[away_k],
                         "home_goals": int(hg), "away_goals": int(ag)})
        except (TypeError, ValueError, KeyError):
            continue
    return rows


def main() -> None:
    rows: list[dict] = []

    seasons = _seasons()
    for season in seasons:
        n = len(rows)
        for league in OF_LEAGUES:
            rows.extend(_fetch_of_json(season, league))
        if len(rows) > n:
            print(f"  domestic {season}: {len(rows) - n:,}")

    n = len(rows)
    for season in seasons:
        for code in CL_FILES:
            txt = _get(f"{CL_RAW}/{season}/{code}.txt")
            if txt:
                rows.extend(_parse_txt(txt, f"uefa.{code}"))
    print(f"  UEFA continental cups: {len(rows) - n:,}")

    n = len(rows)
    for year in SA_YEARS:
        for code, league in (("copal", "libertadores"), ("copas", "sudamericana")):
            txt = _get(f"{SA_RAW}/copa-libertadores/{year}_{code}.txt")
            if txt:
                rows.extend(_parse_txt(txt, league))
    print(f"  South American cups: {len(rows) - n:,}")

    n = len(rows)
    for year in CWC_YEARS:
        txt = _get(f"{CWC_RAW}/{year}/clubworldcup.txt")
        if txt:
            rows.extend(_parse_txt(txt, "cwc"))
    print(f"  Club World Cup (inter-continental): {len(rows) - n:,}")

    n = len(rows)
    for code, league in FD_EXTRA.items():
        rows.extend(_fetch_fd(f"{FD}/new/{code}.csv", league,
                              "Home", "Away", "HG", "AG"))
    print(f"  extra leagues (MLS/LigaMX/Brazil/Argentina/Japan): {len(rows) - n:,}")

    n = len(rows)
    for season in FD_SEASONS:
        for code, league in FD_MAIN.items():
            rows.extend(_fetch_fd(f"{FD}/mmz4281/{season}/{code}.csv", league,
                                  "HomeTeam", "AwayTeam", "FTHG", "FTAG"))
    print(f"  extra leagues (Scotland/Turkey/Belgium/Greece): {len(rows) - n:,}")

    rows.sort(key=lambda r: r["date"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {len(rows):,} club matches ({rows[0]['date']} … "
          f"{rows[-1]['date']}) to {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
