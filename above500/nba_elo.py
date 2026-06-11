"""NBA franchise Elo ratings computed from real games, 1946-47 to 2014-15.

Data: FiveThirtyEight's nbaallelo dataset (CC BY 4.0), trimmed by
scripts/prepare_nba_data.py into above500/data/nba_games.csv.gz — 63,157
NBA and ABA games, each carrying 538's own pre-game home win probability
so the backtest can benchmark against it.

The rating system follows 538's published NBA Elo methodology:
new franchises start at 1300, ratings revert 25% toward 1505 between
seasons, home court is worth 100 Elo points, and updates use K=20 with
a margin-of-victory multiplier.
"""

from __future__ import annotations

import csv
import gzip
import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "nba_games.csv.gz"

INITIAL_RATING = 1300.0
MEAN_RATING = 1505.0
SEASON_REVERSION = 0.25
HOME_ADVANTAGE = 100.0  # Elo points
K_FACTOR = 20.0
BACKTEST_FROM = 1955    # season end-year: the shot-clock era onward

# abbr/color for the 30 franchises active in 2014-15 (keyed by fran_id)
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


def elo_win_prob(diff: float) -> float:
    """Win probability for the side whose rating advantage is `diff`."""
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


def _mov_multiplier(margin: int, winner_diff: float) -> float:
    """538's margin-of-victory multiplier (dampened for blowout favorites)."""
    return ((margin + 3) ** 0.8) / (7.5 + 0.006 * winner_diff)


def _load_games() -> list[dict]:
    with gzip.open(DATA, "rt", newline="") as f:
        games = []
        for r in csv.DictReader(f):
            games.append({
                "season": int(r["season"]),
                "date": datetime.strptime(r["date"], "%m/%d/%Y").date(),
                "playoffs": r["playoffs"] == "1",
                "home": r["home"],
                "away": r["away"],
                "home_pts": int(r["home_pts"]),
                "away_pts": int(r["away_pts"]),
                "neutral": r["neutral"] == "1",
                "p538_home": float(r["p538_home"]),
            })
    return games


@lru_cache(maxsize=1)
def _run() -> dict:
    """Walk forward through every game: rate, predict, score, record."""
    games = _load_games()
    final_season = games[-1]["season"]

    ratings: dict[str, float] = {}
    season_now = games[0]["season"]
    predictions = []          # (season, p_home, home_won, p538_home)
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
            predictions.append((g["season"], p_home, home_won, g["p538_home"]))

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
                "date": g["date"].isoformat(),
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
    p538 = [(p, won) for _, _, won, p in predictions]
    home_rate = sum(won for _, won in ours) / len(ours)
    naive = [(home_rate, won) for _, won in ours]
    coin = [(0.5, won) for _, won in ours]

    models = [
        {"model": "Above .500 Elo", **_score(ours)},
        {"model": "FiveThirtyEight Elo", **_score(p538)},
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
    for start in range(1950, 2020, 10):
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

    def team_blob(side: dict) -> dict:
        abbr, color = TEAM_META.get(side["name"], (None, None))
        if abbr:
            side.update(abbr=abbr, color=color)
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
            "rating": round(run["ratings"][team], 1),
            "rating_change_7d": round(history[-1] - history[0], 1),
            "record": f"{w}-{l}",
            "history": history,
        })

    return {
        "slug": "nba-elo",
        "name": "NBA Elo Ratings",
        "league": "NBA",
        "season": f"1946–{final_season} archive",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "description": f"Franchise Elo ratings computed from {run['n_games']:,} real NBA and "
                       f"ABA games, walk-forward backtested over every game since "
                       f"{BACKTEST_FROM} and benchmarked against FiveThirtyEight's own "
                       f"forecasts.",
        "methodology": "Ratings follow FiveThirtyEight's published NBA Elo method: new "
                       "franchises start at 1300, ratings revert 25% toward 1505 between "
                       "seasons, home court is worth 100 Elo points, and games update "
                       "ratings with K=20 scaled by a margin-of-victory multiplier. Every "
                       "prediction in the backtest uses only information available before "
                       "tip-off. Game data is FiveThirtyEight's nbaallelo dataset "
                       "(CC BY 4.0), covering 1946-47 through 2014-15.",
        "games": [
            {**g, "home": team_blob(g["home"]), "away": team_blob(g["away"])}
            for g in run["last_games"]
        ],
        "standings": standings,
        "standings_title": f"Final Elo ratings, {season_label}",
        "column_labels": {"rating": "Elo", "change": f"{season_label} Δ",
                          "record": season_label},
        "backtest": _backtest(run["predictions"]),
    }
