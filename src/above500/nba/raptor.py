"""Box-RAPTOR player ratings and a next-season projection on top.

RAPTOR (Robust Algorithm using Player Tracking and On/Off Ratings) was
538's NBA player-value model: a plus-minus rating in points per 100
possessions a player adds above league average, split into offense and
defense, and rolled up into wins above replacement (WAR). 538 published it
for 1976-77 through 2021-22 and retired it in 2023, and the full rating needs
play-by-play and tracking inputs that were never released.

So this page doesn't use 538's published ratings. Every number here is
*Box-RAPTOR* — a reconstruction of RAPTOR's box-score component, learned from
538's own box-stats-to-RAPTOR data and applied to box scores from 1976-77
right through the current season (see above500.nba.raptor_box). That gives one
self-computed rating per player per year across the whole history, on a single
consistent scale, with no dependence on 538's published numbers; the fidelity
backtest reports how closely it tracks the real thing on held-out seasons.

The model this module *adds* on top is a next-season projection: a player's
coming-season Box-RAPTOR is forecast from a recency- and minutes-weighted
blend of their recent seasons, regressed toward replacement level by an amount
that shrinks as the sample grows — the standard projection recipe
(Marcel/CARMELO-style). Its two free parameters (reversion strength and
replacement level) and the recency decay are fit on a training era (target
seasons through 2009) and then evaluated, untouched, on a held-out era (2010
on). Every projection uses only seasons before the one it forecasts, so the
backtest is genuinely out-of-sample.
"""

from __future__ import annotations

import math
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache

from . import raptor_box

# projection model -----------------------------------------------------------
MAX_PRIOR_SEASONS = 4        # how many recent seasons feed a projection
TRAIN_THROUGH = 2009         # target seasons <= this fit the parameters
TEST_FROM = 2010             # target seasons >= this are the held-out backtest
MIN_TARGET_POSS = 1000       # only score projections for real rotation seasons
POSS_PER_MIN = 2.1           # rough possessions per player-minute, for weighting
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


def _norm_name(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    n = n.replace(".", "").replace("'", "")
    drop = {"jr", "sr", "ii", "iii", "iv", "v"}
    return " ".join(t for t in n.replace("-", " ").split() if t not in drop)


def _label(season: int) -> str:
    return f"{season - 1}-{str(season)[2:]}"


# ---------------------------------------------------------------------------
# careers: one Box-RAPTOR rating per player per season, keyed by name
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _careers() -> dict[str, list[dict]]:
    """{normalized name: [season dicts oldest->newest]} from Box-RAPTOR.

    Names are the only identifier shared across the historical (538 box file)
    and recent (nba.com / balldontlie) sources, so careers are stitched by
    normalized name. Each season carries `poss` (minutes * POSS_PER_MIN) so the
    projection can weight by playing time on a single scale.
    """
    careers: dict[str, list[dict]] = {}
    for e in raptor_box.estimate_all():
        careers.setdefault(_norm_name(e["name"]), []).append({
            "season": e["season"], "name": e["name"], "team": e.get("team", ""),
            "raptor_off": e["raptor_off"], "raptor_def": e["raptor_def"],
            "raptor_total": e["raptor_total"], "war": e["war"],
            "min": e["min"], "poss": e["min"] * POSS_PER_MIN,
        })
    for c in careers.values():
        c.sort(key=lambda s: s["season"])
    return careers


def _history(name: str) -> list[float]:
    return [round(s["raptor_total"], 1) for s in _careers().get(_norm_name(name), [])]


# ---------------------------------------------------------------------------
# projection
# ---------------------------------------------------------------------------

def _weighted_prior(prior: list[dict], decay: float) -> tuple[float, float] | None:
    """Recency- and minutes-weighted (mean RAPTOR, total weight) of a player's
    most recent seasons. `prior` is oldest->newest, already < target.
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
    for career in _careers().values():
        for i, s in enumerate(career):
            if i == 0 or s["poss"] < MIN_TARGET_POSS:
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

    seasons, actual, proj, last_raptor, const = [], [], [], [], []
    for t, prior in test:
        pr = _project(prior, decay, k, r)
        if pr is None:
            continue
        seasons.append(t["season"])
        actual.append(t["raptor_total"])
        proj.append(pr)
        last_raptor.append(prior[-1]["raptor_total"])
        const.append(r)

    models = [
        {"model": "Above .500 projection", **_regress(proj, actual)},
        {"model": "Prior-season Box-RAPTOR", **_regress(last_raptor, actual)},
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

def _team_logo(abbr: str | None) -> str | None:
    """Path to a player's team logo, or None when the team is unknown."""
    from .elo import TEAM_META
    valid = {a for a, _ in TEAM_META.values()}
    return f"/assets/logos/nba/{abbr.lower()}.png" if abbr in valid else None


def _format_entry(s: dict) -> dict:
    career = _careers().get(_norm_name(s["name"]), [])
    history = [round(c["raptor_total"], 1) for c in career]
    # which point in the career sparkline is the season being shown
    idx = next((i for i, c in enumerate(career) if c["season"] == s["season"]),
               len(history) - 1)
    return {
        "name": s["name"], "abbr": s.get("team"), "mp": round(s["min"]),
        "off": round(s["raptor_off"], 1), "dfn": round(s["raptor_def"], 1),
        "raptor": round(s["raptor_total"], 1), "war": round(s["war"], 1),
        "history": history, "history_idx": idx,
        "logo": _team_logo(s.get("team")),
    }


def _all_leaderboards() -> tuple[dict[int, dict], list[int]]:
    """Per-season leaderboards for RS and PO, newest first."""
    rs_by_season: dict[int, list] = {}
    for e in raptor_box.estimate_all():
        rs_by_season.setdefault(e["season"], []).append(e)

    po_by_season: dict[int, list] = {}
    for e in raptor_box.estimate_all_po():
        po_by_season.setdefault(e["season"], []).append(e)

    all_seasons = sorted(set(rs_by_season) | set(po_by_season), reverse=True)

    leaderboards: dict[int, dict] = {}
    for season in all_seasons:
        rs = sorted(rs_by_season.get(season, []),
                    key=lambda s: s["war"], reverse=True)[:25]
        po = sorted(po_by_season.get(season, []),
                    key=lambda s: s["war"], reverse=True)[:25]
        leaderboards[season] = {
            "rs": [_format_entry(s) for s in rs],
            "po": [_format_entry(s) for s in po],
        }

    return leaderboards, all_seasons


@lru_cache(maxsize=1)
def forecast() -> dict:
    careers = _careers()
    last_season = max(s["season"] for c in careers.values() for s in c)
    season_label = _label(last_season)
    proj_season = last_season + 1

    params = _fit()
    backtest = _backtest(params)
    fidelity = raptor_box.fidelity_backtest()

    leaderboards, all_seasons = _all_leaderboards()
    projections = _projections(last_season, params)

    return {
        "slug": "nba-raptor",
        "name": "NBA RAPTOR Player Ratings",
        "league": "NBA",
        "season": f"through {season_label}",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_through": season_label,
        "description": "Box-RAPTOR plus-minus ratings and wins above replacement for "
                       "every NBA player since 1976-77.",
        "methodology": "RAPTOR was FiveThirtyEight's player plus-minus: points per 100 "
                       "possessions a player adds above league average on offense and "
                       "defense, rolled into wins above replacement. 538 retired it and "
                       "never released the full algorithm, so every rating here is "
                       "Box-RAPTOR — a ridge regression trained on 538's own box-component "
                       "RAPTOR (the box half of the box + on/off decomposition, 2014-2019) "
                       "and applied to box scores from 1976-77 to today, recentred per "
                       "season so the league average is zero. The projection "
                       "forecasts a player's next-season Box-RAPTOR from a recency- and "
                       "minutes-weighted blend regressed toward replacement level, fit on "
                       f"seasons through {TRAIN_THROUGH} and evaluated out-of-sample on "
                       f"{TEST_FROM} onward.",
        "season_label": season_label,
        "proj_label": _label(proj_season),
        "last_season": last_season,
        "all_seasons": all_seasons,
        "leaderboards": leaderboards,
        "projections": projections,
        "backtest": backtest,
        "fidelity": fidelity,
    }


def _projections(last_season: int, params: dict) -> list[dict]:
    """Project the season after `last_season` for everyone active in it."""
    rows = []
    for career in _careers().values():
        if career[-1]["season"] != last_season or career[-1]["poss"] < MIN_TARGET_POSS:
            continue
        pr = _project(career, params["decay"], params["k"], params["r"])
        if pr is None:
            continue
        rows.append({"name": career[-1]["name"], "abbr": career[-1].get("team"),
                     "last": round(career[-1]["raptor_total"], 1),
                     "proj": round(pr, 1),
                     "history": _history(career[-1]["name"]),
                     "logo": _team_logo(career[-1].get("team"))})
    rows.sort(key=lambda p: p["proj"], reverse=True)
    return rows[:15]
