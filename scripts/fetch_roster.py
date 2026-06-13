#!/usr/bin/env python3
"""Fetch national-team roster strength from API-Football (free tier).

API-Football's free plan only covers seasons 2022-2024, so we use the
2024 season: for each of the 48 nations we read its 2024 player pool and
take each player's best season match rating (club form where the API
returns it), peak-age weighted. 2024 club form already reflects the
current picture far better than older datasets, and squads are ~80%
stable into 2026.

Writes above500/data/roster_ratings.json. The free tier allows only 100
requests/day, so the fetch is INCREMENTAL and idempotent: national-team
ids and completed nations are cached in the snapshot, each run fills as
many remaining nations as the budget allows and commits progress, and a
later run continues. Run by .github/workflows/refresh-roster.yml weekly
or on demand; trigger it a couple of times to fully populate.

Requires APIFOOTBALL_KEY and egress to v3.football.api-sports.io.
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
SEASON = 2024            # freshest season in the free plan's coverage
SEED_LEAGUE, SEED_SEASON = 1, 2022   # World Cup 2022: cheap source of nation ids
TOP_N = 16               # squad players that define roster strength
MAX_REQUESTS = 90        # leave headroom under the 100/day free limit
REQUEST_PAUSE = 7.0      # seconds; free tier allows 10 requests/minute

# API-Football country names that differ from ours.
NAME_FIXUPS = {
    "Korea Republic": "South Korea", "USA": "United States",
    "Czechia": "Czech Republic", "Czech-Republic": "Czech Republic",
    "Cote d'Ivoire": "Ivory Coast", "Côte d'Ivoire": "Ivory Coast",
    "IR Iran": "Iran", "Türkiye": "Turkey", "Saudi-Arabia": "Saudi Arabia",
    "Congo DR": "DR Congo", "Cape Verde Islands": "Cape Verde",
    "Cabo Verde": "Cape Verde", "Curacao": "Curaçao",
}
# Search queries for nations whose name needs a hint (>=3 chars, fuzzy).
SEARCH_QUERY = {
    "South Korea": "Korea Republic", "United States": "USA",
    "DR Congo": "Congo", "Ivory Coast": "Ivory Coast", "Iran": "Iran",
    "Curaçao": "Curacao", "Czech Republic": "Czechia",
    "Bosnia and Herzegovina": "Bosnia",
}

_requests = 0
_stop = False     # set when the daily quota / rate limit is hit


def _api(path: str, **params) -> dict:
    global _requests, _stop
    _requests += 1
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print("  hit rate/daily limit (429) — stopping; progress is saved.")
            _stop = True
        else:
            print(f"  HTTP {e.code} for {path}({params})")
        return {}
    except Exception as e:  # noqa: BLE001
        print(f"  request failed for {path}({params}): {e}")
        return {}
    errs = data.get("errors")
    if errs:
        print(f"  API errors for {path}({params}): {errs}")
        # API-Football also reports quota exhaustion in the body
        if isinstance(errs, dict) and any(k in errs for k in ("rateLimit", "requests")):
            _stop = True
    time.sleep(REQUEST_PAUSE)
    return data


def _budget_left() -> bool:
    return not _stop and _requests < MAX_REQUESTS


def _age_weight(age: int | None) -> float:
    if not age:
        return 0.9
    if age <= 21:
        return 0.85 + 0.03 * (age - 18)
    if age <= 29:
        return 1.0
    return max(0.5, 1.0 - 0.06 * (age - 29))


def _player_rating(stats: list) -> float | None:
    """Best season match rating across a player's competitions."""
    best = None
    for s in stats or []:
        games = s.get("games") or {}
        if (games.get("appearences") or 0) < 3 or games.get("rating") is None:
            continue
        try:
            r = float(games["rating"])
        except (TypeError, ValueError):
            continue
        best = r if best is None else max(best, r)
    return best


def seed_team_ids(ids: dict) -> None:
    """Seed nation ids cheaply from the World Cup 2022 team list."""
    data = _api("teams", league=SEED_LEAGUE, season=SEED_SEASON)
    for item in data.get("response", []):
        team = item.get("team") or {}
        name = NAME_FIXUPS.get(team.get("name"), team.get("name"))
        if name in FIFA_CODES and name not in ids:
            ids[name] = team["id"]


def search_team_id(name: str) -> int | None:
    """Find a nation's team id via team search (national teams only)."""
    data = _api("teams", search=SEARCH_QUERY.get(name, name))
    for item in data.get("response", []):
        team = item.get("team") or {}
        if team.get("national"):
            return team["id"]
    return None


def fetch_roster(team_id: int) -> dict:
    ratings, page, pages = [], 1, 1
    while page <= pages and _budget_left():
        data = _api("players", team=team_id, season=SEASON, page=page)
        pages = (data.get("paging") or {}).get("total", 1)
        for item in data.get("response", []):
            r = _player_rating(item.get("statistics"))
            if r is not None:
                ratings.append(r * _age_weight((item.get("player") or {}).get("age")))
        page += 1
    if not ratings:
        return {"rating": None, "n_players": 0}
    ratings.sort(reverse=True)
    top = ratings[:TOP_N]
    return {"rating": round(sum(top) / len(top), 4), "n_players": len(top)}


def load_snapshot() -> dict:
    try:
        return json.loads(OUT.read_text())
    except Exception:
        return {}


def save_snapshot(snap: dict) -> None:
    snap["source"] = "API-Football"
    snap["club_season"] = SEASON
    snap["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snap, indent=2) + "\n")


def main() -> None:
    global MAX_REQUESTS
    status = _api("status").get("response", {}) or {}
    reqs = status.get("requests", {}) or {}
    current, limit = reqs.get("current") or 0, reqs.get("limit_day") or 100
    print(f"account: plan={status.get('subscription', {}).get('plan')} "
          f"requests today={current}/{limit}")
    # Respect the daily quota across runs: never start more than today's remainder.
    MAX_REQUESTS = max(0, min(MAX_REQUESTS, limit - current - 2))
    print(f"this run will make up to ~{MAX_REQUESTS} requests")

    snap = load_snapshot()
    ids = snap.setdefault("team_ids", {})
    teams = snap.setdefault("teams", {})

    # Discover any missing nation ids (cheap seed first, then search).
    if len(ids) < len(FIFA_CODES) and _budget_left():
        seed_team_ids(ids)
    for name in FIFA_CODES:
        if name not in ids and _budget_left():
            tid = search_team_id(name)
            if tid:
                ids[name] = tid
    save_snapshot(snap)
    print(f"ids: {len(ids)}/48  (unmapped: {sorted(set(FIFA_CODES) - set(ids))})")

    # Fill rosters for nations not done yet, until the budget runs out.
    todo = [n for n in FIFA_CODES if n in ids and n not in teams]
    for name in todo:
        if not _budget_left():
            break
        teams[name] = fetch_roster(ids[name])
        save_snapshot(snap)  # persist after each nation so progress survives
        print(f"  {name:24s} rating={teams[name]['rating']} n={teams[name]['n_players']}")

    done = sum(1 for v in teams.values() if v.get("rating") is not None)
    print(f"done {done}/48 nations | requests used ~{_requests} | "
          f"{'COMPLETE' if done >= len(FIFA_CODES) else 'run again to continue'}")
    if done == 0:
        sys.exit("No rosters fetched — see errors above.")


if __name__ == "__main__":
    API_KEY = os.environ.get("APIFOOTBALL_KEY", "").strip()
    if not API_KEY:
        sys.exit("APIFOOTBALL_KEY not set")
    main()
