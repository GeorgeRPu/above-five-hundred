#!/usr/bin/env python3
"""Fetch 2026 World Cup squads from API-Football and write a roster snapshot.

Produces above500/data/roster_ratings.json: one roster-strength score per
nation, built from each squad's players' best season match ratings, with a
peak-age weighting. Run by .github/workflows/refresh-roster.yml (weekly /
on demand); the committed snapshot is read offline at render time, so the
free tier's 100-requests/day limit only matters for this occasional pull.

Requires:
  * APIFOOTBALL_KEY in the environment (free key from dashboard.api-football.com)
  * network egress to v3.football.api-sports.io

Usage:
    APIFOOTBALL_KEY=... python3 scripts/fetch_roster.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from above500.wc_spi import FIFA_CODES  # the 48 nations, in our naming  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "above500" / "data" / "roster_ratings.json"
BASE = "https://v3.football.api-sports.io"
WC_LEAGUE = 1          # API-Football league id for the FIFA World Cup
WC_SEASON = 2026
CLUB_SEASON = 2025     # season whose club ratings describe current form
TOP_N = 16             # squad players that define roster strength
REQUEST_PAUSE = 7.0    # seconds; free tier allows 10 requests/minute

# API-Football uses its own country names; map the ones that differ from ours.
NAME_FIXUPS = {
    "Korea Republic": "South Korea", "USA": "United States",
    "Czechia": "Czech Republic", "Cote d'Ivoire": "Ivory Coast",
    "Côte d'Ivoire": "Ivory Coast", "IR Iran": "Iran", "Iran ": "Iran",
    "Türkiye": "Turkey", "Saudi-Arabia": "Saudi Arabia",
    "DR Congo": "DR Congo", "Congo DR": "DR Congo",
    "Cape Verde Islands": "Cape Verde", "Cabo Verde": "Cape Verde",
    "Curacao": "Curaçao", "Bosnia": "Bosnia and Herzegovina",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
}


def _api(path: str, **params) -> dict:
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    errors = data.get("errors")
    if errors:
        print(f"  API errors for {path}({params}): {errors}")
    return data


def _age_weight(age: int | None) -> float:
    """Peak around 24-29; discount the young/raw and the ageing."""
    if not age:
        return 0.9
    if age <= 21:
        return 0.85 + 0.03 * (age - 18)      # 0.85 .. 0.94
    if age <= 29:
        return 1.0
    return max(0.5, 1.0 - 0.06 * (age - 29))  # decline past 29


def _player_rating(stats: list) -> tuple[float | None, str | None, int | None]:
    """Best season match rating across a player's competitions, with club."""
    best, club, apps = None, None, 0
    for s in stats or []:
        games = s.get("games") or {}
        rating = games.get("rating")
        played = games.get("appearences") or 0
        if rating is None or played < 3:
            continue
        try:
            r = float(rating)
        except (TypeError, ValueError):
            continue
        if best is None or r > best:
            best, club, apps = r, (s.get("team") or {}).get("name"), played
    return best, club, apps


def fetch_team_ids() -> dict[str, int]:
    """Our-name -> API-Football team id, for the 48 qualified nations."""
    ids: dict[str, int] = {}
    data = _api("teams", league=WC_LEAGUE, season=WC_SEASON)
    for item in data.get("response", []):
        team = item.get("team") or {}
        name = NAME_FIXUPS.get(team.get("name"), team.get("name"))
        if name in FIFA_CODES:
            ids[name] = team["id"]
    return ids


def fetch_roster(team_id: int) -> dict:
    """Roster strength for one nation from its players' club-season ratings."""
    ratings, page, pages = [], 1, 1
    while page <= pages:
        data = _api("players", team=team_id, season=CLUB_SEASON, page=page)
        pages = (data.get("paging") or {}).get("total", 1)
        for item in data.get("response", []):
            player = item.get("player") or {}
            rating, club, _ = _player_rating(item.get("statistics"))
            if rating is not None:
                ratings.append((rating * _age_weight(player.get("age")), club))
        page += 1
        time.sleep(REQUEST_PAUSE)
    if not ratings:
        return {"rating": None, "n_players": 0}
    ratings.sort(key=lambda x: x[0], reverse=True)
    top = ratings[:TOP_N]
    return {
        "rating": round(sum(r for r, _ in top) / len(top), 4),
        "n_players": len(top),
    }


def main() -> None:
    # Diagnostics: confirm the key works and the World Cup is in this plan's coverage.
    status = _api("status").get("response", {})
    sub = (status or {}).get("subscription", {})
    reqs = (status or {}).get("requests", {})
    print(f"account: plan={sub.get('plan')} active={sub.get('active')} "
          f"requests={reqs.get('current')}/{reqs.get('limit_day')}/day")
    wc = _api("leagues", id=WC_LEAGUE, season=WC_SEASON).get("response", [])
    print(f"World Cup league lookup (id={WC_LEAGUE}, season={WC_SEASON}): "
          f"{len(wc)} result(s)" + (f" -> {wc[0]['league']['name']}" if wc else " (not in plan?)"))

    ids = fetch_team_ids()
    print(f"matched {len(ids)}/48 nations")
    if not ids:
        sys.exit("No teams returned — see API errors / coverage above; not writing snapshot.")
    missing = sorted(set(FIFA_CODES) - set(ids))
    if missing:
        print("UNMATCHED (add to NAME_FIXUPS):", missing)

    teams = {}
    for name, tid in sorted(ids.items()):
        teams[name] = fetch_roster(tid)
        print(f"  {name:24s} rating={teams[name]['rating']} n={teams[name]['n_players']}")
        time.sleep(REQUEST_PAUSE)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "API-Football",
        "club_season": CLUB_SEASON,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "teams": teams,
    }, indent=2) + "\n")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    API_KEY = os.environ.get("APIFOOTBALL_KEY", "").strip()
    if not API_KEY:
        sys.exit("APIFOOTBALL_KEY not set")
    main()
