#!/usr/bin/env python3
"""Build the committed playoff box-score totals for Box-RAPTOR estimation.

Two free sources, stitched together:

* NocturneBear's playoff box-score dump, 2010-11 through 2023-24
  (https://github.com/NocturneBear/NBA-Data-2010-2024) — per-game rows
  aggregated to season totals.
* Basketball-Reference playoff totals for everything outside NocturneBear's
  run: 1977 through 2009-10, and 2024-25 onward.

Usage:
    python3 scripts/prepare_po_box.py
"""

import csv
import datetime
import gzip
import html as html_mod
import io
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "above500" / "data" / "nba_po_box.csv.gz"

# --- NocturneBear (per-game playoff dump, 2010-11 .. 2023-24) ----------------
NB_URL = ("https://raw.githubusercontent.com/NocturneBear/NBA-Data-2010-2024/main/"
          "play_off_box_scores_2010_2024.csv")

# --- Basketball-Reference (playoff totals, fills everything else) ------------
BREF_URL = "https://www.basketball-reference.com/playoffs/NBA_{}_totals.html"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
BREF_TEAM_FIX = {"BRK": "BKN", "PHO": "PHX", "CHO": "CHA"}

FIRST_SEASON = 1977          # Box-RAPTOR coverage starts at 1976-77
FIELDS = ["season", "name", "team", "g", "min", "fga", "fg3a", "fta",
          "orb", "drb", "trb", "ast", "stl", "blk", "tov", "pts"]
COUNTS = ["fga", "fg3a", "fta", "orb", "drb", "trb", "ast", "stl", "blk", "tov", "pts"]
NB_COUNTS = {"fga": "fieldGoalsAttempted", "fg3a": "threePointersAttempted",
             "fta": "freeThrowsAttempted", "orb": "reboundsOffensive",
             "drb": "reboundsDefensive", "trb": "reboundsTotal", "ast": "assists",
             "stl": "steals", "blk": "blocks", "tov": "turnovers", "pts": "points"}
BREF_COUNTS = {"fga": "fga", "fg3a": "fg3a", "fta": "fta", "orb": "orb",
               "drb": "drb", "trb": "trb", "ast": "ast", "stl": "stl",
               "blk": "blk", "tov": "tov", "pts": "pts"}


def parse_minutes(s: str) -> float:
    if not s:
        return 0.0
    if ":" in s:
        m, sec = s.split(":")
        return int(m) + int(sec) / 60
    try:
        return float(s)
    except ValueError:
        return 0.0


def season_end_year(season_year: str) -> int:
    start, end2 = season_year.split("-")
    return int(start[:2] + end2)


def current_season() -> int:
    today = datetime.date.today()
    return today.year + (1 if today.month >= 10 else 0)


def _primary_team(team_min: dict[str, float]) -> str:
    return max(team_min, key=team_min.get) if team_min else ""


def fetch_nocturnebear() -> list[dict]:
    """Playoff season totals from NocturneBear's per-game dump."""
    agg: dict[tuple[int, str], dict] = {}
    with urllib.request.urlopen(NB_URL, timeout=120) as resp:
        text = resp.read().decode()
    for r in csv.DictReader(io.StringIO(text)):
        season = season_end_year(r["season_year"])
        mp = parse_minutes(r["minutes"])
        if mp <= 0:
            continue
        a = agg.setdefault((season, r["personName"]),
                           {"g": 0, "min": 0.0, "team_min": {},
                            **{c: 0 for c in COUNTS}})
        a["g"] += 1
        a["min"] += mp
        tri = r.get("teamTricode") or ""
        if tri:
            a["team_min"][tri] = a["team_min"].get(tri, 0.0) + mp
        for col, src in NB_COUNTS.items():
            v = r[src]
            a[col] += int(v) if v not in ("", "None") else 0

    return [{"season": season, "name": name, "team": _primary_team(a["team_min"]),
             "g": a["g"], "min": round(a["min"], 1),
             **{c: a[c] for c in COUNTS}} for (season, name), a in agg.items()]


_CELL = re.compile(r'data-stat="([a-z0-9_]+)"[^>]*>(.*?)</(?:td|th)>', re.S)
_TAG = re.compile(r"<[^>]+>")
_APPEND = re.compile(r'data-append-csv="([^"]+)"')
_COMBINED = re.compile(r"^\d+TM$")


def _cell_text(v: str) -> str:
    return html_mod.unescape(_TAG.sub("", v)).strip()


def _int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return 0


def fetch_bref_season(year: int) -> list[dict]:
    """Playoff totals for one season from Basketball-Reference. [] if absent."""
    req = urllib.request.Request(BREF_URL.format(year), headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            if e.code == 429 and attempt < 2:
                time.sleep(int(e.headers.get("Retry-After", 20)))
                continue
            raise

    m = re.search(r'<table[^>]*\bid="totals_stats"[^>]*>(.*?)</table>', raw, re.S)
    if not m:
        return []

    # BRef playoff pages use "player" / "g" / "team_id" instead of
    # "name_display" / "games" / "team_name_abbr" used on regular-season pages.
    agg: dict[str, dict] = {}
    for row in re.findall(r"<tr[^>]*>.*?</tr>", m.group(1), re.S):
        cells = {k: _cell_text(v) for k, v in _CELL.findall(row)}
        name = cells.get("player", "")
        if not name or name == "Player":
            continue
        name = name.rstrip("*")
        mp = _int(cells.get("mp", ""))
        if mp <= 0:
            continue
        team = cells.get("team_id", "")
        pid_m = _APPEND.search(row)
        a = agg.setdefault(pid_m.group(1) if pid_m else name,
                           {"name": name, "g": 0, "min": 0, "team_min": {},
                            **{c: 0 for c in COUNTS}})
        a["g"] += _int(cells.get("g", ""))
        a["min"] += mp
        if team:
            tri = BREF_TEAM_FIX.get(team, team)
            a["team_min"][tri] = a["team_min"].get(tri, 0) + mp
        for col, src in BREF_COUNTS.items():
            a[col] += _int(cells.get(src, ""))

    return [{"season": year, "name": a["name"], "team": _primary_team(a["team_min"]),
             "g": a["g"], "min": a["min"], **{c: a[c] for c in COUNTS}}
            for a in agg.values()]


def main() -> None:
    print("Fetching NocturneBear playoff box scores...")
    rows = fetch_nocturnebear()
    nb_seasons = sorted({r["season"] for r in rows})
    print(f"  NocturneBear: {len(rows):,} player-seasons ({nb_seasons[0]}-{nb_seasons[-1]})")

    bref_seasons = []
    for year in range(FIRST_SEASON, current_season() + 1):
        if year in nb_seasons:
            continue
        print(f"  Fetching BRef playoff totals for {year - 1}-{str(year)[2:]}...", end="")
        season_rows = fetch_bref_season(year)
        if season_rows:
            rows += season_rows
            bref_seasons.append(year)
            print(f" {len(season_rows)} players")
        else:
            print(" empty")
        time.sleep(3)

    rows.sort(key=lambda r: (r["season"], r["name"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    seasons = sorted({r["season"] for r in rows})
    print(f"Wrote {len(rows):,} player-seasons ({seasons[0]}-{seasons[-1]}) to {OUT} "
          f"({OUT.stat().st_size / 1e3:.0f} KB); "
          f"Basketball-Reference filled {bref_seasons or 'nothing'}")


if __name__ == "__main__":
    main()
