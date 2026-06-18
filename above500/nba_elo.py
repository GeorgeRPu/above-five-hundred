"""NBA franchise Elo ratings computed from real games, 1946-47 to today.

Data lineage (both CC BY 4.0 / openly licensed):
- FiveThirtyEight's nbaallelo dataset: every NBA/ABA game 1946-47 through
  2014-15, with 538's own pre-game forecasts for benchmarking.
- Neil Paine's maintained continuation of the 538 Elo file for seasons
  2016 onward: https://github.com/Neil-Paine-1/NBA-elo

A trimmed merge of both is committed at above500/data/nba_games.csv.gz.
At render time the model additionally tries to fetch games newer than the
archive from Paine's repo, so the nightly build picks up fresh results
automatically; if the fetch fails the committed archive is used alone.

The rating system follows 538's published NBA Elo methodology:
new franchises start at 1300, ratings revert 25% toward 1505 between
seasons, home court is worth 100 Elo points, and updates use K=20 with
a margin-of-victory multiplier.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "nba_games.csv.gz"
LIVE_URL = "https://raw.githubusercontent.com/Neil-Paine-1/NBA-elo/main/nba_elo.csv"
LIVE_CACHE = Path(os.environ.get("TMPDIR", "/tmp")) / "above500_nba_elo_remote.csv"
LIVE_CACHE_MAX_AGE = 12 * 3600  # seconds

# balldontlie.io tops up games newer than every other source. Requires an
# API key in BALLDONTLIE_API_KEY (set as a GitHub Actions secret in CI);
# without one this source is skipped silently.
BDL_URLS = ("https://api.balldontlie.io/nba/v1/games",
            "https://api.balldontlie.io/v1/games")
BDL_CACHE = Path(os.environ.get("TMPDIR", "/tmp")) / "above500_bdl_games.json"
BDL_SCHED_CACHE = Path(os.environ.get("TMPDIR", "/tmp")) / "above500_bdl_schedule.json"
BDL_MAX_REQUESTS = 60
SCHEDULE_DAYS = 7       # how far ahead to look for upcoming games
SCHEDULE_LIMIT = 12     # most upcoming games to forecast on the homepage

INITIAL_RATING = 1300.0
MEAN_RATING = 1505.0
SEASON_REVERSION = 0.25
HOME_ADVANTAGE = 100.0  # Elo points
K_FACTOR = 20.0
BACKTEST_FROM = 1955    # season end-year: the shot-clock era onward

# abbr/color for the 30 current franchises (fran_id naming from nbaallelo)
TEAM_META = {
    "Hawks": ("ATL", "#e03a3e"), "Celtics": ("BOS", "#007a33"),
    "Nets": ("BKN", "#222222"), "Hornets": ("CHA", "#00788c"),
    "Bulls": ("CHI", "#ce1141"), "Cavaliers": ("CLE", "#860038"),
    "Mavericks": ("DAL", "#00538c"), "Nuggets": ("DEN", "#0e2240"),
    "Pistons": ("DET", "#c8102e"), "Warriors": ("GSW", "#1d428a"),
    "Rockets": ("HOU", "#ce1141"), "Pacers": ("IND", "#002d62"),
    "Clippers": ("LAC", "#c8102e"), "Lakers": ("LAL", "#552583"),
    "Grizzlies": ("MEM", "#5d76a9"), "Heat": ("MIA", "#98002e"),
    "Bucks": ("MIL", "#00471b"), "Timberwolves": ("MIN", "#236192"),
    "Pelicans": ("NOP", "#0c2340"), "Knicks": ("NYK", "#f58426"),
    "Thunder": ("OKC", "#007ac1"), "Magic": ("ORL", "#0077c0"),
    "Sixers": ("PHI", "#006bb6"), "Suns": ("PHX", "#e56020"),
    "Trailblazers": ("POR", "#e03a3e"), "Kings": ("SAC", "#5a2d81"),
    "Spurs": ("SAS", "#c4ced4"), "Raptors": ("TOR", "#ce1141"),
    "Jazz": ("UTA", "#002b5c"), "Wizards": ("WAS", "#002b5c"),
}

# Paine's file uses basketball-reference abbreviations
ABBR_TO_FRANCHISE = {
    "ATL": "Hawks", "BOS": "Celtics", "BRK": "Nets", "CHI": "Bulls",
    "CHO": "Hornets", "CLE": "Cavaliers", "DAL": "Mavericks",
    "DEN": "Nuggets", "DET": "Pistons", "GSW": "Warriors",
    "HOU": "Rockets", "IND": "Pacers", "LAC": "Clippers", "LAL": "Lakers",
    "MEM": "Grizzlies", "MIA": "Heat", "MIL": "Bucks",
    "MIN": "Timberwolves", "NOP": "Pelicans", "NYK": "Knicks",
    "OKC": "Thunder", "ORL": "Magic", "PHI": "Sixers", "PHO": "Suns",
    "POR": "Trailblazers", "SAC": "Kings", "SAS": "Spurs",
    "TOR": "Raptors", "UTA": "Jazz", "WAS": "Wizards",
}

# balldontlie uses the NBA's own abbreviations where they differ
BDL_ABBR_TO_FRANCHISE = dict(ABBR_TO_FRANCHISE,
                             BKN="Nets", CHA="Hornets", PHX="Suns")
for _bref_only in ("BRK", "CHO", "PHO"):
    del BDL_ABBR_TO_FRANCHISE[_bref_only]


def elo_win_prob(diff: float) -> float:
    """Win probability for the side whose rating advantage is `diff`."""
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


def _mov_multiplier(margin: int, winner_diff: float) -> float:
    """538's margin-of-victory multiplier (dampened for blowout favorites)."""
    return ((margin + 3) ** 0.8) / (7.5 + 0.006 * winner_diff)


def _parse_archive_row(r: dict) -> dict:
    return {
        "season": int(r["season"]),
        "date": r["date"],
        "playoffs": r["playoffs"] == "1",
        "home": r["home"],
        "away": r["away"],
        "home_pts": int(r["home_pts"]),
        "away_pts": int(r["away_pts"]),
        "neutral": r["neutral"] == "1",
        "p_ref_home": float(r["p_ref_home"]),
    }


def _fetch_live_games(after_date: str) -> list[dict]:
    """Games newer than the archive, from Paine's repo. Empty list on any failure."""
    try:
        if not (LIVE_CACHE.exists()
                and time.time() - LIVE_CACHE.stat().st_mtime < LIVE_CACHE_MAX_AGE):
            with urllib.request.urlopen(LIVE_URL, timeout=60) as resp:
                LIVE_CACHE.write_bytes(resp.read())
        text = LIVE_CACHE.read_text()
    except Exception:
        return []

    games, seen_neutral = [], set()
    try:
        for r in csv.DictReader(io.StringIO(text)):
            if r["date"] <= after_date or not r.get("score1") or r["score1"] == "NA":
                continue
            if r["neutral"] == "1":
                key = (r["date"], frozenset((r["team1"], r["team2"])))
                if key in seen_neutral:
                    continue
                seen_neutral.add(key)
            elif r["is_home"] != "1":
                continue
            games.append({
                "season": int(r["season"]),
                "date": r["date"],
                "playoffs": r["playoff"] == "TRUE",
                "home": ABBR_TO_FRANCHISE[r["team1"]],
                "away": ABBR_TO_FRANCHISE[r["team2"]],
                "home_pts": int(r["score1"]),
                "away_pts": int(r["score2"]),
                "neutral": r["neutral"] == "1",
                "p_ref_home": float(r["elo_prob1"]),
            })
    except Exception:
        return []
    games.sort(key=lambda g: (g["date"], g["home"]))
    return games


def _parse_bdl_games(payloads: list[dict]) -> list[dict]:
    """Convert balldontlie /games pages to our row format (finished games only)."""
    games = []
    for payload in payloads:
        for g in payload.get("data", []):
            if g.get("status") != "Final":
                continue
            home = BDL_ABBR_TO_FRANCHISE.get(g["home_team"]["abbreviation"])
            away = BDL_ABBR_TO_FRANCHISE.get(g["visitor_team"]["abbreviation"])
            if not home or not away:
                continue
            games.append({
                "season": int(g["season"]) + 1,   # bdl 2025 == our 2026 (2025-26)
                "date": g["date"],
                "playoffs": bool(g.get("postseason")),
                "home": home,
                "away": away,
                "home_pts": int(g["home_team_score"]),
                "away_pts": int(g["visitor_team_score"]),
                # the NBA Cup final is played on a neutral court
                "neutral": g.get("ist_stage") == "Championship",
                "p_ref_home": None,
            })
    games.sort(key=lambda g: (g["date"], g["home"]))
    return games


def _bdl_paginate(start, end, api_key: str) -> list[dict] | None:
    """Fetch every /games page in [start, end]. None on any failure or page-cap hit."""
    for base_url in BDL_URLS:
        payloads: list[dict] = []
        cursor, requests_made = None, 0
        try:
            while requests_made < BDL_MAX_REQUESTS:
                params = {"start_date": start.isoformat(), "end_date": end.isoformat(),
                          "per_page": "100"}
                if cursor is not None:
                    params["cursor"] = str(cursor)
                req = urllib.request.Request(
                    f"{base_url}?{urllib.parse.urlencode(params)}",
                    headers={"Authorization": api_key},
                )
                requests_made += 1
                try:
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        payload = json.loads(resp.read())
                except urllib.error.HTTPError as e:
                    if e.code == 429:  # rate limited: wait and retry the same page
                        time.sleep(int(e.headers.get("Retry-After", 15)))
                        requests_made -= 1
                        continue
                    raise
                payloads.append(payload)
                cursor = (payload.get("meta") or {}).get("next_cursor")
                if cursor is None:
                    return payloads
                time.sleep(0.5)
            return None  # page cap hit: treat as failure rather than half a season
        except Exception:
            continue  # try the legacy URL
    return None


def _fetch_bdl_games(after_date: str) -> list[dict]:
    """Finished games newer than `after_date` from balldontlie. [] on any failure."""
    api_key = os.environ.get("BALLDONTLIE_API_KEY", "").strip()
    if not api_key:
        return []

    if BDL_CACHE.exists() and time.time() - BDL_CACHE.stat().st_mtime < LIVE_CACHE_MAX_AGE:
        try:
            cached = json.loads(BDL_CACHE.read_text())
            return [g for g in cached if g["date"] > after_date]
        except Exception:
            pass

    start = (datetime.strptime(after_date, "%Y-%m-%d") + timedelta(days=1)).date()
    end = datetime.now(timezone.utc).date()
    payloads = _bdl_paginate(start, end, api_key)
    if payloads is None:
        return []
    games = _parse_bdl_games(payloads)
    try:
        BDL_CACHE.write_text(json.dumps(games))
    except Exception:
        pass
    return games


def _parse_bdl_schedule(payloads: list[dict]) -> list[dict]:
    """Convert balldontlie /games pages to scheduled (not-yet-final) matchups."""
    games = []
    for payload in payloads:
        for g in payload.get("data", []):
            if g.get("status") == "Final":
                continue
            home = BDL_ABBR_TO_FRANCHISE.get(g["home_team"]["abbreviation"])
            away = BDL_ABBR_TO_FRANCHISE.get(g["visitor_team"]["abbreviation"])
            if not home or not away:
                continue
            games.append({
                "season": int(g["season"]) + 1,   # bdl 2025 == our 2026 (2025-26)
                "date": g["date"],
                "playoffs": bool(g.get("postseason")),
                "home": home,
                "away": away,
                "neutral": g.get("ist_stage") == "Championship",
            })
    games.sort(key=lambda g: (g["date"], g["home"]))
    return games


def _fetch_bdl_schedule(days: int = SCHEDULE_DAYS) -> list[dict]:
    """Upcoming (unplayed) games over the next `days` from balldontlie. [] on failure."""
    api_key = os.environ.get("BALLDONTLIE_API_KEY", "").strip()
    if not api_key:
        return []

    if (BDL_SCHED_CACHE.exists()
            and time.time() - BDL_SCHED_CACHE.stat().st_mtime < LIVE_CACHE_MAX_AGE):
        try:
            return json.loads(BDL_SCHED_CACHE.read_text())
        except Exception:
            pass

    start = datetime.now(timezone.utc).date()
    end = start + timedelta(days=days)
    payloads = _bdl_paginate(start, end, api_key)
    if payloads is None:
        return []
    games = _parse_bdl_schedule(payloads)
    try:
        BDL_SCHED_CACHE.write_text(json.dumps(games))
    except Exception:
        pass
    return games


def _load_games() -> list[dict]:
    with gzip.open(DATA, "rt", newline="") as f:
        games = [_parse_archive_row(r) for r in csv.DictReader(f)]
    games += _fetch_live_games(after_date=games[-1]["date"])
    games += _fetch_bdl_games(after_date=games[-1]["date"])
    return games


@lru_cache(maxsize=1)
def _run() -> dict:
    """Walk forward through every game: rate, predict, score, record."""
    games = _load_games()
    final_season = games[-1]["season"]

    ratings: dict[str, float] = {}
    season_now = games[0]["season"]
    predictions = []          # (season, p_home, home_won, p_ref_home)
    final_records: dict[str, list[int]] = {}      # team -> [w, l] in final season
    final_history: dict[str, list[float]] = {}    # team -> rating after each game
    last_games: list[dict] = []

    for g in games:
        if g["season"] != season_now:
            season_now = g["season"]
            for t in ratings:
                ratings[t] += SEASON_REVERSION * (MEAN_RATING - ratings[t])

        home, away = g["home"], g["away"]
        r_home = ratings.setdefault(home, INITIAL_RATING)
        r_away = ratings.setdefault(away, INITIAL_RATING)

        bonus = 0.0 if g["neutral"] else HOME_ADVANTAGE
        diff = (r_home + bonus) - r_away
        p_home = elo_win_prob(diff)

        home_won = g["home_pts"] > g["away_pts"]
        if g["season"] >= BACKTEST_FROM:
            predictions.append((g["season"], p_home, home_won, g["p_ref_home"]))

        margin = abs(g["home_pts"] - g["away_pts"])
        winner_diff = diff if home_won else -diff
        shift = K_FACTOR * _mov_multiplier(margin, winner_diff) * ((1.0 if home_won else 0.0) - p_home)
        ratings[home] = r_home + shift
        ratings[away] = r_away - shift

        if g["season"] == final_season:
            final_records.setdefault(home, [0, 0])
            final_records.setdefault(away, [0, 0])
            final_records[home if home_won else away][0] += 1
            final_records[away if home_won else home][1] += 1
            final_history.setdefault(home, []).append(round(ratings[home], 1))
            final_history.setdefault(away, []).append(round(ratings[away], 1))
            last_games.append({
                "date": g["date"],
                "status": "final",
                "label": "Playoffs" if g["playoffs"] else None,
                "home": {"name": home, "rating": round(r_home),
                         "win_prob": round(p_home, 3), "score": g["home_pts"]},
                "away": {"name": away, "rating": round(r_away),
                         "win_prob": round(1 - p_home, 3), "score": g["away_pts"]},
            })

    return {
        "ratings": ratings,
        "final_season": final_season,
        "data_through": games[-1]["date"],
        "predictions": predictions,
        "final_records": final_records,
        "final_history": final_history,
        "last_games": last_games[-6:],
        "n_games": len(games),
    }


# ---------------------------------------------------------------------------
# backtest scoring
# ---------------------------------------------------------------------------

def _score(pairs: list[tuple[float, bool]]) -> dict:
    """Accuracy / Brier / log loss for (probability, outcome) pairs."""
    n = len(pairs)
    correct = sum(1 for p, won in pairs if (p >= 0.5) == won)
    brier = sum((p - won) ** 2 for p, won in pairs) / n
    eps = 1e-12
    logloss = -sum(math.log(max(p if won else 1 - p, eps)) for p, won in pairs) / n
    return {"n": n, "accuracy": correct / n, "brier": brier, "logloss": logloss}


def _backtest(predictions) -> dict:
    ours = [(p, won) for _, p, won, _ in predictions]
    # the reference Elo is scored only on games where it published a forecast
    ref = [(p, won) for _, _, won, p in predictions if p is not None]
    home_rate = sum(won for _, won in ours) / len(ours)
    naive = [(home_rate, won) for _, won in ours]
    coin = [(0.5, won) for _, won in ours]

    models = [
        {"model": "Above .500 Elo", **_score(ours)},
        {"model": "FiveThirtyEight / Neil Paine Elo", **_score(ref)},
        {"model": f"Home team always ({home_rate:.0%})", **_score(naive)},
        {"model": "Coin flip", **_score(coin)},
    ]

    buckets = []
    for lo in [i / 10 for i in range(10)]:
        hi = lo + 0.1
        sel = [(p, won) for p, won in ours if lo <= p < hi or (hi == 1.0 and p == 1.0)]
        if not sel:
            continue
        buckets.append({
            "range": f"{lo:.0%}–{hi:.0%}",
            "n": len(sel),
            "predicted": sum(p for p, _ in sel) / len(sel),
            "actual": sum(won for _, won in sel) / len(sel),
        })

    decades = []
    for start in range(1950, 2030, 10):
        sel = [(p, won) for season, p, won, _ in predictions
               if start <= season - 1 < start + 10]
        if not sel:
            continue
        s = _score(sel)
        decades.append({"decade": f"{start}s", **s})

    return {
        "since": BACKTEST_FROM,
        "n": len(ours),
        "models": models,
        "calibration": buckets,
        "decades": decades,
    }


# ---------------------------------------------------------------------------
# site payload
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def forecast() -> dict:
    run = _run()
    final_season = run["final_season"]
    season_label = f"{final_season - 1}-{str(final_season)[2:]}"
    through = datetime.strptime(run["data_through"], "%Y-%m-%d").strftime("%b %-d, %Y")

    def team_blob(side: dict) -> dict:
        abbr, color = TEAM_META.get(side["name"], (None, None))
        if abbr:
            side.update(abbr=abbr, color=color,
                        logo=f"/assets/logos/nba/{abbr.lower()}.png")
        return side

    standings = []
    for team, (abbr, color) in TEAM_META.items():
        if team not in run["final_records"]:
            continue
        w, l = run["final_records"][team]
        history = run["final_history"][team]
        standings.append({
            "abbr": abbr,
            "name": team,
            "color": color,
            "logo": f"/assets/logos/nba/{abbr.lower()}.png",
            "rating": round(run["ratings"][team], 1),
            "rating_change_7d": round(history[-1] - history[0], 1),
            "record": f"{w}-{l}",
            "history": history,
        })

    # upcoming games: pre-tip win probabilities from the current ratings.
    # Games in a later season get the same 25% reversion the model applies
    # between seasons, so an offseason schedule isn't forecast on stale ratings.
    ratings = run["ratings"]
    reverted = {t: r + SEASON_REVERSION * (MEAN_RATING - r) for t, r in ratings.items()}
    upcoming_games = []
    for g in _fetch_bdl_schedule()[:SCHEDULE_LIMIT]:
        rr = reverted if g["season"] > final_season else ratings
        r_home = rr.get(g["home"], INITIAL_RATING)
        r_away = rr.get(g["away"], INITIAL_RATING)
        bonus = 0.0 if g["neutral"] else HOME_ADVANTAGE
        p_home = elo_win_prob((r_home + bonus) - r_away)
        upcoming_games.append({
            "date": g["date"],
            "status": "upcoming",
            "label": "Playoffs" if g["playoffs"] else None,
            "home": team_blob({"name": g["home"], "rating": round(r_home),
                               "win_prob": round(p_home, 3)}),
            "away": team_blob({"name": g["away"], "rating": round(r_away),
                               "win_prob": round(1 - p_home, 3)}),
        })

    return {
        "slug": "nba-elo",
        "name": "NBA Elo Ratings",
        "league": "NBA",
        "season": f"{season_label} season",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_through": through,
        "description": f"Franchise Elo ratings computed from {run['n_games']:,} real NBA and "
                       f"ABA games since 1946.",
        "methodology": "Ratings follow FiveThirtyEight's published NBA Elo method: new "
                       "franchises start at 1300, ratings revert 25% toward 1505 between "
                       "seasons, home court is worth 100 Elo points, and games update "
                       "ratings with K=20 scaled by a margin-of-victory multiplier. Every "
                       "prediction in the backtest uses only information available before "
                       "tip-off. Game data: FiveThirtyEight's nbaallelo dataset (CC BY "
                       "4.0) through 2014-15, continued by Neil Paine's maintained "
                       "NBA-elo dataset, topped up with the latest results from the "
                       "balldontlie API.",
        "games": [
            {**g, "home": team_blob(g["home"]), "away": team_blob(g["away"])}
            for g in run["last_games"]
        ] + upcoming_games,
        "standings": standings,
        "standings_title": f"Current Elo ratings (through {through})",
        "column_labels": {"rating": "Elo", "change": f"{season_label} Δ",
                          "record": season_label, "trend": "Season"},
        "backtest": _backtest(run["predictions"]),
    }
