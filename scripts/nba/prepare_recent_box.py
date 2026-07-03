#!/usr/bin/env python3
"""Build the committed floor of recent box scores for above500.raptor_box.

The Box-RAPTOR estimator scores every season from box scores. 538's own
historical file supplies named box stats through 2018-19 (see
prepare_player_box.py); this script supplies every season after that, from two
free sources stitched together:

* NocturneBear's openly-posted nba.com box-score dump, 2010-11 through 2023-24
  (https://github.com/NocturneBear/NBA-Data-2010-2024) — per-game rows we
  aggregate to season totals.
* Basketball-Reference season totals for everything past NocturneBear's run,
  up to the current season (https://www.basketball-reference.com/) — one row
  per player-season already.

Only seasons after 538's last usable per-36 year (2018-19) are kept, so the
committed file picks up exactly where the 538 history leaves off, with no
overlap, and reaches the current season without any paid API. Re-run it each
season (and commit the result) to push the floor forward; balldontlie's
player-stats endpoint is paywalled, so there is no render-time top-up.

Usage:
    uv run python scripts/nba/prepare_recent_box.py
"""

import csv
import datetime
import gzip
import html
import io
import re
import time
import urllib.error
import urllib.request

from above500.nba.raptor_box import RECENT_FILE as OUT

# --- NocturneBear (per-game dump, 2010-11 .. 2023-24) -----------------------
NB_BASE = ("https://raw.githubusercontent.com/NocturneBear/NBA-Data-2010-2024/main/"
           "regular_season_box_scores_2010_2024_part_{}.csv")
NB_PARTS = (1, 2, 3)
AFTER_SEASON = 2019          # 538's last usable per-36 season (2018-19); keep newer

# --- Basketball-Reference (season totals, fills past NocturneBear) -----------
BREF_URL = "https://www.basketball-reference.com/leagues/NBA_{}_totals.html"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
# Basketball-Reference team abbreviations -> nba.com tricodes (logo/career match)
BREF_TEAM_FIX = {"BRK": "BKN", "PHO": "PHX", "CHO": "CHA"}

FIELDS = ["season", "name", "team", "g", "min", "fga", "fg3a", "fta",
          "orb", "drb", "trb", "ast", "stl", "blk", "tov", "pts"]
COUNTS = ["fga", "fg3a", "fta", "orb", "drb", "trb", "ast", "stl", "blk", "tov", "pts"]
# our column -> NocturneBear column
NB_COUNTS = {"fga": "fieldGoalsAttempted", "fg3a": "threePointersAttempted",
             "fta": "freeThrowsAttempted", "orb": "reboundsOffensive",
             "drb": "reboundsDefensive", "trb": "reboundsTotal", "ast": "assists",
             "stl": "steals", "blk": "blocks", "tov": "turnovers", "pts": "points"}
# our column -> Basketball-Reference data-stat
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
    # "2022-23" -> 2023, matching 538's year_id convention
    start, end2 = season_year.split("-")
    return int(start[:2] + end2)


def current_season() -> int:
    """End-year of the latest NBA season (one that has tipped off or finished)."""
    today = datetime.date.today()
    return today.year + (1 if today.month >= 10 else 0)


def _primary_team(team_min: dict[str, float]) -> str:
    # traded players are credited to the team they logged the most minutes for
    return max(team_min, key=team_min.get) if team_min else ""


def fetch_nocturnebear() -> list[dict]:
    """Season totals from NocturneBear's per-game dump (seasons > AFTER_SEASON)."""
    agg: dict[tuple[int, str], dict] = {}
    for n in NB_PARTS:
        with urllib.request.urlopen(NB_BASE.format(n), timeout=120) as resp:
            text = resp.read().decode()
        for r in csv.DictReader(io.StringIO(text)):
            season = season_end_year(r["season_year"])
            if season <= AFTER_SEASON:
                continue
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
    return html.unescape(_TAG.sub("", v)).strip()


def _int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return 0


def fetch_bref_season(year: int) -> list[dict]:
    """Season totals for one season from Basketball-Reference. [] if absent."""
    req = urllib.request.Request(BREF_URL.format(year), headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:                      # season page doesn't exist yet
                return []
            if e.code == 429 and attempt < 2:      # rate limited: back off
                time.sleep(int(e.headers.get("Retry-After", 20)))
                continue
            raise

    m = re.search(r'<table[^>]*\bid="totals_stats"[^>]*>(.*?)</table>', raw, re.S)
    if not m:                                      # regular-season table not found
        return []

    # Traded players appear as per-team component rows plus a combined "2TM"/"3TM"
    # row; skip the combined rows and sum the components, crediting the most-used
    # team — matching the NocturneBear path.
    agg: dict[str, dict] = {}
    for row in re.findall(r"<tr[^>]*>.*?</tr>", m.group(1), re.S):
        cells = {k: _cell_text(v) for k, v in _CELL.findall(row)}
        name = cells.get("name_display", "")
        if not name or name == "Player":          # repeated header rows
            continue
        team = cells.get("team_name_abbr", "")
        if _COMBINED.match(team):
            continue
        mp = _int(cells.get("mp", ""))
        if mp <= 0:
            continue
        pid_m = _APPEND.search(row)
        a = agg.setdefault(pid_m.group(1) if pid_m else name,
                           {"name": name, "g": 0, "min": 0, "team_min": {},
                            **{c: 0 for c in COUNTS}})
        a["g"] += _int(cells.get("games", ""))
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
    rows = fetch_nocturnebear()
    floor = max((r["season"] for r in rows), default=AFTER_SEASON)

    bref_seasons = []
    for year in range(floor + 1, current_season() + 1):
        season_rows = fetch_bref_season(year)
        if season_rows:
            rows += season_rows
            bref_seasons.append(year)
        time.sleep(3)                              # be gentle with the BR rate limit

    rows.sort(key=lambda r: (r["season"], r["name"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    seasons = sorted({r["season"] for r in rows})
    print(f"Wrote {len(rows):,} player-seasons ({seasons}) to {OUT} "
          f"({OUT.stat().st_size / 1e3:.0f} KB); "
          f"Basketball-Reference filled {bref_seasons or 'nothing'}")


if __name__ == "__main__":
    main()
