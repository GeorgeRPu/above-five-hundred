"""FiveThirtyEight's RAPTOR player ratings, plus a projection model on top.

RAPTOR (Robust Algorithm using Player Tracking and On/Off Ratings) was
538's NBA player-value model: a plus-minus rating in points per 100
possessions a player adds above league average, split into offense and
defense, and rolled up into wins above replacement (WAR). 538 published
RAPTOR for every player-season from 1976-77 through 2021-22 and retired it
in 2023; that full run is committed at above500/data/nba_raptor.csv.gz
(trimmed from their CC BY 4.0 dataset by scripts/prepare_raptor_data.py).

The descriptive ratings are 538's. The model this module *adds* is a
next-season projection: a player's coming-season RAPTOR is forecast from a
recency- and possession-weighted blend of their recent seasons, regressed
toward replacement level by an amount that shrinks as the sample grows —
the standard projection recipe (Marcel/CARMELO-style). Its two free
parameters (reversion strength and replacement level) and the recency decay
are fit on a training era (target seasons through 2009) and then evaluated,
untouched, on a held-out era (2010 on). Every projection uses only seasons
played before the one it forecasts, so the backtest is genuinely
out-of-sample.
"""

from __future__ import annotations

import csv
import gzip
import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "nba_raptor.csv.gz"

# projection model -----------------------------------------------------------
MAX_PRIOR_SEASONS = 4        # how many recent seasons feed a projection
TRAIN_THROUGH = 2009         # target seasons <= this fit the parameters
TEST_FROM = 2010             # target seasons >= this are the held-out backtest
MIN_TARGET_POSS = 1000       # only score projections for real rotation seasons
DECAY_GRID = (0.5, 0.6, 0.7, 0.8, 0.9)
K_GRID = tuple(range(200, 2001, 200))      # reversion strength, in possessions
R_GRID = tuple(x / 2 for x in range(-8, 1))  # replacement level: -4.0 .. 0.0

# leaderboard / projection display tiers
TIERS = [
    ("All-NBA caliber (+5)", 5.0, math.inf),
    ("Quality starter (+2 to +5)", 2.0, 5.0),
    ("Rotation (-1 to +2)", -1.0, 2.0),
    ("Replacement (< -1)", -math.inf, -1.0),
]


def _f(x: str):
    return None if x in ("", "NA") else float(x)


@lru_cache(maxsize=1)
def _load_players() -> list[dict]:
    """Every player-season, sorted by player then season."""
    with gzip.open(DATA, "rt", newline="") as f:
        rows = [{
            "player_id": r["player_id"],
            "player_name": r["player_name"],
            "season": int(r["season"]),
            "poss": int(r["poss"]),
            "mp": int(r["mp"]),
            "raptor_offense": _f(r["raptor_offense"]),
            "raptor_defense": _f(r["raptor_defense"]),
            "raptor_total": _f(r["raptor_total"]),
            "war_total": _f(r["war_total"]),
            "predator_total": _f(r["predator_total"]),
        } for r in csv.DictReader(f)]
    rows.sort(key=lambda r: (r["player_id"], r["season"]))
    return rows


@lru_cache(maxsize=1)
def _by_player() -> dict[str, list[dict]]:
    careers: dict[str, list[dict]] = {}
    for r in _load_players():
        careers.setdefault(r["player_id"], []).append(r)
    return careers


# ---------------------------------------------------------------------------
# projection
# ---------------------------------------------------------------------------

def _weighted_prior(prior: list[dict], decay: float) -> tuple[float, float] | None:
    """Recency- and possession-weighted (mean RAPTOR, total weight) of a
    player's most recent seasons. `prior` is oldest->newest, already < target.
    """
    recent = prior[-MAX_PRIOR_SEASONS:]
    num = den = 0.0
    for rank, s in enumerate(reversed(recent)):           # rank 0 == newest
        w = (decay ** rank) * s["poss"]
        num += w * s["raptor_total"]
        den += w
    if den == 0:
        return None
    return num / den, den


def _project(prior: list[dict], decay: float, k: float, r: float) -> float | None:
    wp = _weighted_prior(prior, decay)
    if wp is None:
        return None
    m, w = wp
    return (w * m + k * r) / (w + k)


def _projectable_targets() -> list[tuple[dict, list[dict]]]:
    """(target season, prior seasons) for every season we can both project and
    score: a real rotation season with at least one earlier season on record.
    """
    out = []
    for career in _by_player().values():
        for i, s in enumerate(career):
            if i == 0 or s["poss"] < MIN_TARGET_POSS or s["raptor_total"] is None:
                continue
            out.append((s, career[:i]))
    return out


def _fit() -> dict:
    """Grid-search the projection parameters on the training era (MAE)."""
    train = [(t, p) for t, p in _projectable_targets() if t["season"] <= TRAIN_THROUGH]

    best = None
    for decay in DECAY_GRID:
        # the weighted prior depends only on decay; compute it once per target
        priors = [(_weighted_prior(p, decay), t["raptor_total"]) for t, p in train]
        priors = [(wp, actual) for wp, actual in priors if wp is not None]
        for k in K_GRID:
            for r in R_GRID:
                err = sum(abs((w * m + k * r) / (w + k) - actual)
                          for (m, w), actual in priors)
                mae = err / len(priors)
                if best is None or mae < best["mae"]:
                    best = {"decay": decay, "k": float(k), "r": r, "mae": mae}
    return best


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------

def _regress(pred: list[float], actual: list[float]) -> dict:
    n = len(pred)
    err = [p - a for p, a in zip(pred, actual)]
    mae = sum(abs(e) for e in err) / n
    rmse = math.sqrt(sum(e * e for e in err) / n)
    pm, am = sum(pred) / n, sum(actual) / n
    cov = sum((p - pm) * (a - am) for p, a in zip(pred, actual))
    vp = sum((p - pm) ** 2 for p in pred)
    va = sum((a - am) ** 2 for a in actual)
    corr = cov / math.sqrt(vp * va) if vp > 0 and va > 0 else None
    return {"n": n, "mae": mae, "rmse": rmse, "corr": corr}


def _backtest(params: dict) -> dict:
    decay, k, r = params["decay"], params["k"], params["r"]
    test = [(t, p) for t, p in _projectable_targets() if t["season"] >= TEST_FROM]

    seasons, actual, proj, last_raptor, last_predator, const = [], [], [], [], [], []
    for t, prior in test:
        pr = _project(prior, decay, k, r)
        if pr is None:
            continue
        seasons.append(t["season"])
        actual.append(t["raptor_total"])
        proj.append(pr)
        last_raptor.append(prior[-1]["raptor_total"])
        lp = prior[-1]["predator_total"]
        last_predator.append(lp if lp is not None else prior[-1]["raptor_total"])
        const.append(r)

    models = [
        {"model": "Above .500 projection", **_regress(proj, actual)},
        {"model": "Prior-season PREDATOR", **_regress(last_predator, actual)},
        {"model": "Prior-season RAPTOR", **_regress(last_raptor, actual)},
        {"model": "Replacement level (flat)", **_regress(const, actual)},
    ]

    # predicted vs. actual by projected tier (a calibration analogue)
    tiers = []
    for label, lo, hi in TIERS:
        sel = [(p, a) for p, a in zip(proj, actual) if lo <= p < hi]
        if not sel:
            continue
        tiers.append({
            "tier": label, "n": len(sel),
            "predicted": sum(p for p, _ in sel) / len(sel),
            "actual": sum(a for _, a in sel) / len(sel),
        })

    # MAE by decade of the projected season (season - 1 is the campaign start)
    by_era: dict[int, list[tuple[float, float]]] = {}
    for s, pr, ac in zip(seasons, proj, actual):
        by_era.setdefault((s - 1) // 10 * 10, []).append((pr, ac))
    eras = []
    for start in sorted(by_era):
        pr = [p for p, _ in by_era[start]]
        ac = [a for _, a in by_era[start]]
        eras.append({"decade": f"{start}s", **_regress(pr, ac)})

    return {
        "since": TEST_FROM, "n": len(actual),
        "params": params, "models": models, "tiers": tiers, "eras": eras,
    }


# ---------------------------------------------------------------------------
# site payload
# ---------------------------------------------------------------------------

def _career_history(player_id: str) -> list[float]:
    return [round(s["raptor_total"], 1) for s in _by_player()[player_id]
            if s["raptor_total"] is not None]


@lru_cache(maxsize=1)
def forecast() -> dict:
    players = _load_players()
    last_season = max(s["season"] for s in players)
    season_label = f"{last_season - 1}-{str(last_season)[2:]}"

    params = _fit()
    backtest = _backtest(params)

    # leaderboard: the final RAPTOR season, best by WAR
    leaders = sorted(
        (s for s in players if s["season"] == last_season and s["war_total"] is not None),
        key=lambda s: s["war_total"], reverse=True)[:25]
    leaderboard = [{
        "name": s["player_name"],
        "mp": s["mp"],
        "off": round(s["raptor_offense"], 1) if s["raptor_offense"] is not None else None,
        "dfn": round(s["raptor_defense"], 1) if s["raptor_defense"] is not None else None,
        "raptor": round(s["raptor_total"], 1) if s["raptor_total"] is not None else None,
        "war": round(s["war_total"], 1),
        "history": _career_history(s["player_id"]),
    } for s in leaders]

    # forward projection: every player active in the final season, projected
    # one season ahead from everything through that season
    proj_season = last_season + 1
    proj_label = f"{proj_season - 1}-{str(proj_season)[2:]}"
    projections = []
    for career in _by_player().values():
        if career[-1]["season"] != last_season or career[-1]["poss"] < MIN_TARGET_POSS:
            continue
        pr = _project(career, params["decay"], params["k"], params["r"])
        if pr is None:
            continue
        projections.append({
            "name": career[-1]["player_name"],
            "last": round(career[-1]["raptor_total"], 1),
            "proj": round(pr, 1),
            "history": _career_history(career[-1]["player_id"]),
        })
    projections.sort(key=lambda p: p["proj"], reverse=True)
    projections = projections[:15]

    return {
        "slug": "nba-raptor",
        "name": "NBA RAPTOR Player Ratings",
        "league": "NBA",
        "season": f"{season_label} (final RAPTOR season)",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_through": season_label,
        "description": f"FiveThirtyEight's RAPTOR plus-minus ratings and wins above "
                       f"replacement for every NBA player-season from 1976-77 through "
                       f"{season_label}, with a next-season projection model backtested "
                       f"out-of-sample on {backtest['n']:,} seasons since {TEST_FROM}.",
        "methodology": "RAPTOR is FiveThirtyEight's player plus-minus: points per 100 "
                       "possessions a player adds above league average on offense and "
                       "defense, rolled into wins above replacement. The descriptive "
                       "ratings here are 538's own (their CC BY 4.0 dataset, 1976-77 "
                       "through 2021-22). The projection forecasts a player's next-season "
                       "RAPTOR from a recency- and possession-weighted blend of recent "
                       "seasons, regressed toward replacement level by a shrinkage that "
                       "eases as the sample grows; its parameters were fit on seasons "
                       f"through {TRAIN_THROUGH} and evaluated, fixed, on {TEST_FROM} "
                       "onward. Every projection uses only earlier seasons, so the "
                       "backtest is out-of-sample.",
        "season_label": season_label,
        "proj_label": proj_label,
        "leaderboard": leaderboard,
        "projections": projections,
        "backtest": backtest,
    }
