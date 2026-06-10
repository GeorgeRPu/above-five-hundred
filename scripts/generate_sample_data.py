#!/usr/bin/env python3
"""Generate sample forecast data for the Above .500 site.

This is a working end-to-end example of the publishing pipeline: an Elo
model over a fictional NBA-style season, with playoff/title odds from
Monte Carlo simulation. Replace the simulation with your real model and
keep the `write_model()` call — the site renders whatever lands in
site/data/<slug>/latest.json.

Usage:
    python3 scripts/generate_sample_data.py
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "site" / "data"

HOME_ADVANTAGE = 70.0   # Elo points
K_FACTOR = 20.0
N_SIMULATIONS = 5000
PLAYOFF_SPOTS = 8

TEAMS = [
    # (abbr, name, color, true strength used to simulate the fake season)
    ("BOS", "Celtics", "#007a33", 1720),
    ("DEN", "Nuggets", "#0e2240", 1690),
    ("OKC", "Thunder", "#007ac1", 1685),
    ("MIL", "Bucks", "#00471b", 1640),
    ("MIN", "Wolves", "#236192", 1635),
    ("NYK", "Knicks", "#f58426", 1620),
    ("PHX", "Suns", "#e56020", 1600),
    ("DAL", "Mavericks", "#00538c", 1595),
    ("LAL", "Lakers", "#552583", 1575),
    ("CLE", "Cavaliers", "#860038", 1570),
    ("SAC", "Kings", "#5a2d81", 1545),
    ("MIA", "Heat", "#98002e", 1540),
    ("GSW", "Warriors", "#1d428a", 1535),
    ("PHI", "76ers", "#006bb6", 1530),
    ("ORL", "Magic", "#0077c0", 1510),
    ("HOU", "Rockets", "#ce1141", 1495),
]


def elo_win_prob(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def simulate_season(rng: random.Random, today: datetime):
    """Play a fake partial season so the demo has history, results and a slate."""
    ratings = {abbr: 1500.0 for abbr, *_ in TEAMS}
    strength = {abbr: s for abbr, _, _, s in TEAMS}
    wins = {abbr: 0 for abbr in ratings}
    losses = {abbr: 0 for abbr in ratings}
    history = {abbr: [1500.0] for abbr in ratings}
    recent_games = []

    abbrs = list(ratings)
    n_days = 120
    for day in range(n_days):
        date = today - timedelta(days=n_days - day)
        order = abbrs[:]
        rng.shuffle(order)
        for i in range(0, len(order) - 1, 2):
            home, away = order[i], order[i + 1]
            p_home_true = elo_win_prob(strength[home] + HOME_ADVANTAGE, strength[away])
            home_won = rng.random() < p_home_true

            p_home_model = elo_win_prob(ratings[home] + HOME_ADVANTAGE, ratings[away])
            delta = K_FACTOR * ((1.0 if home_won else 0.0) - p_home_model)
            ratings[home] += delta
            ratings[away] -= delta
            wins[home if home_won else away] += 1
            losses[away if home_won else home] += 1

            if day >= n_days - 2:  # keep the last couple of days as "recent results"
                margin = max(1, round(abs(rng.gauss(6, 4))))
                base = rng.randint(98, 118)
                recent_games.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "status": "final",
                    "home": {"abbr": home, "win_prob": round(p_home_model, 3),
                             "score": base + (margin if home_won else 0)},
                    "away": {"abbr": away, "win_prob": round(1 - p_home_model, 3),
                             "score": base + (0 if home_won else margin)},
                })
        if day % 7 == 0:
            for abbr in abbrs:
                history[abbr].append(round(ratings[abbr], 1))
    for abbr in abbrs:
        history[abbr].append(round(ratings[abbr], 1))

    return ratings, wins, losses, history, recent_games


def upcoming_slate(rng: random.Random, ratings: dict, today: datetime, n_games: int = 5):
    games = []
    pool = sorted(ratings, key=ratings.get, reverse=True)[: n_games * 2]
    rng.shuffle(pool)
    for i in range(0, n_games * 2 - 1, 2):
        home, away = pool[i], pool[i + 1]
        p_home = elo_win_prob(ratings[home] + HOME_ADVANTAGE, ratings[away])
        games.append({
            "date": (today + timedelta(days=1 + i // 4)).strftime("%Y-%m-%d"),
            "status": "upcoming",
            "home": {"abbr": home, "rating": round(ratings[home]), "win_prob": round(p_home, 3)},
            "away": {"abbr": away, "rating": round(ratings[away]), "win_prob": round(1 - p_home, 3)},
        })
    return games


def playoff_odds(rng: random.Random, ratings: dict, wins: dict, losses: dict,
                 games_left: int = 20):
    """Monte Carlo the rest of the season + a single-elimination bracket."""
    abbrs = list(ratings)
    playoff_count = {a: 0 for a in abbrs}
    title_count = {a: 0 for a in abbrs}

    for _ in range(N_SIMULATIONS):
        sim_wins = dict(wins)
        for _ in range(games_left):
            order = abbrs[:]
            rng.shuffle(order)
            for i in range(0, len(order) - 1, 2):
                home, away = order[i], order[i + 1]
                p = elo_win_prob(ratings[home] + HOME_ADVANTAGE, ratings[away])
                sim_wins[home if rng.random() < p else away] += 1

        seeds = sorted(abbrs, key=lambda a: (sim_wins[a], ratings[a]), reverse=True)
        field = seeds[:PLAYOFF_SPOTS]
        for a in field:
            playoff_count[a] += 1
        while len(field) > 1:
            nxt = []
            for i in range(0, len(field), 2):
                hi, lo = field[i], field[i + 1]
                p = elo_win_prob(ratings[hi], ratings[lo])
                nxt.append(hi if rng.random() < p else lo)
            field = nxt
        title_count[field[0]] += 1

    return (
        {a: playoff_count[a] / N_SIMULATIONS for a in abbrs},
        {a: title_count[a] / N_SIMULATIONS for a in abbrs},
    )


def write_model(slug: str, payload: dict) -> Path:
    out_dir = DATA_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "latest.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")
    return out


def write_registry(models: list[dict]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / "models.json"
    out.write_text(json.dumps({"models": models}, indent=2) + "\n")
    return out


def main() -> None:
    rng = random.Random(538)
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    updated = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    ratings, wins, losses, history, recent = simulate_season(rng, today)
    p_playoff, p_title = playoff_odds(rng, ratings, wins, losses)

    meta = {(abbr): (name, color) for abbr, name, color, _ in TEAMS}

    def team_blob(g):
        for side in ("home", "away"):
            abbr = g[side]["abbr"]
            name, color = meta[abbr]
            g[side].update(name=name, color=color, rating=g[side].get("rating", round(ratings[abbr])))
        return g

    season_label = f"{today.year - 1}-{str(today.year)[2:]} season"
    payload = {
        "slug": "nba-elo",
        "name": "NBA Elo Forecast",
        "league": "NBA",
        "season": season_label,
        "updated": updated,
        "description": "Elo power ratings for every team, with playoff and title odds from "
                       f"{N_SIMULATIONS:,} simulations of the rest of the season.",
        "methodology": "Teams start at 1500 Elo and gain or lose rating after every game based "
                       "on the result and how surprising it was (K=20, home advantage worth 70 "
                       "points). Playoff and championship odds come from Monte Carlo simulation "
                       "of the remaining schedule and a single-elimination bracket.",
        "games": [team_blob(g) for g in upcoming_slate(rng, ratings, today)]
                 + [team_blob(g) for g in sorted(recent, key=lambda g: g["date"], reverse=True)[:6]],
        "standings": [
            {
                "abbr": abbr,
                "name": meta[abbr][0],
                "color": meta[abbr][1],
                "rating": round(ratings[abbr], 1),
                "rating_change_7d": round(history[abbr][-1] - history[abbr][-2], 1),
                "record": f"{wins[abbr]}-{losses[abbr]}",
                "playoff_prob": round(p_playoff[abbr], 4),
                "title_prob": round(p_title[abbr], 4),
                "history": history[abbr],
            }
            for abbr in ratings
        ],
    }

    model_path = write_model("nba-elo", payload)
    registry_path = write_registry([
        {
            "slug": "nba-elo",
            "name": "NBA Elo Forecast",
            "league": "NBA",
            "season": season_label,
            "description": "Game-by-game win probabilities, power ratings and title odds.",
            "color": "#ed713a",
            "updated": updated,
        },
    ])
    print(f"Wrote {model_path}")
    print(f"Wrote {registry_path}")


if __name__ == "__main__":
    main()
