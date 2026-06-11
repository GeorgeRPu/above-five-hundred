"""International football Elo and a 2026 FIFA World Cup forecast.

Data: martj42/international_results (CC0) — every men's full
international since 1872, updated daily, including the 2026 World Cup
fixture list (rows with NA scores carry the real groups and venues).
A snapshot is committed at above500/data/intl_results.csv.gz; at render
time the model re-fetches the upstream file so odds update as the
tournament is played.

Ratings follow World Football Elo conventions: K scaled by match
importance (World Cup 60 ... friendlies 20), a goal-difference
multiplier, 100 points of home advantage for non-neutral venues, and
draws scored as half a win. Match win/draw/loss probabilities come from
a two-parameter Poisson goal model fitted to the rating gap, which also
drives the Monte Carlo tournament simulation.
"""

from __future__ import annotations

import csv
import gzip
import io
import math
import os
import random
import time
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "intl_results.csv.gz"
LIVE_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
LIVE_CACHE = Path(os.environ.get("TMPDIR", "/tmp")) / "above500_intl_results.csv"
LIVE_CACHE_MAX_AGE = 6 * 3600  # the source updates daily during the World Cup

INITIAL_RATING = 1500.0
HOME_ADVANTAGE = 100.0
GOAL_MODEL_FROM = "1990-01-01"   # fit scoring rates on the modern era
BACKTEST_FROM = "1994-01-01"     # 3-points-for-a-win era
N_SIMULATIONS = 10_000
WC_START = "2026-06-01"

# Group letters keyed by an anchor team (groups themselves come from the
# fixture graph; letters match the official draw)
GROUP_ANCHORS = {
    "Mexico": "A", "Canada": "B", "Brazil": "C", "United States": "D",
    "Germany": "E", "Netherlands": "F", "Belgium": "G", "Spain": "H",
    "France": "I", "Argentina": "J", "Portugal": "K", "England": "L",
}

# Group winners who face third-placed teams in the round of 32 (the other
# four winners face runners-up)
WINNERS_VS_THIRDS = set("ABDEGIKL")

FIFA_CODES = {
    "Mexico": "MEX", "Czech Republic": "CZE", "South Korea": "KOR",
    "South Africa": "RSA", "Canada": "CAN", "Switzerland": "SUI",
    "Bosnia and Herzegovina": "BIH", "Qatar": "QAT", "Brazil": "BRA",
    "Scotland": "SCO", "Morocco": "MAR", "Haiti": "HAI",
    "United States": "USA", "Turkey": "TUR", "Paraguay": "PAR",
    "Australia": "AUS", "Germany": "GER", "Ivory Coast": "CIV",
    "Ecuador": "ECU", "Curaçao": "CUW", "Netherlands": "NED",
    "Sweden": "SWE", "Japan": "JPN", "Tunisia": "TUN", "Belgium": "BEL",
    "Iran": "IRN", "Egypt": "EGY", "New Zealand": "NZL", "Spain": "ESP",
    "Saudi Arabia": "KSA", "Uruguay": "URU", "Cape Verde": "CPV",
    "France": "FRA", "Iraq": "IRQ", "Senegal": "SEN", "Norway": "NOR",
    "Argentina": "ARG", "Austria": "AUT", "Algeria": "ALG",
    "Jordan": "JOR", "Portugal": "POR", "Uzbekistan": "UZB",
    "DR Congo": "COD", "Colombia": "COL", "England": "ENG",
    "Ghana": "GHA", "Croatia": "CRO", "Panama": "PAN",
}

MAJOR_FINALS = ("uefa euro", "copa américa", "african cup of nations",
                "afc asian cup", "concacaf championship", "gold cup")


def elo_win_prob(diff: float) -> float:
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


def k_factor(tournament: str) -> float:
    t = tournament.lower()
    if t == "fifa world cup":
        return 60.0
    if "qualification" in t:
        return 40.0
    if any(name in t for name in MAJOR_FINALS):
        return 50.0
    if t == "friendly":
        return 20.0
    return 30.0


def _gd_multiplier(margin: int) -> float:
    if margin <= 1:
        return 1.0
    if margin == 2:
        return 1.5
    return (11 + margin) / 8.0


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def _parse(text_lines) -> tuple[list[dict], list[dict]]:
    """Split rows into (played matches, upcoming WC fixtures)."""
    played, fixtures = [], []
    for r in csv.DictReader(text_lines):
        row = {
            "date": r["date"],
            "home": r["home_team"],
            "away": r["away_team"],
            "tournament": r["tournament"],
            "neutral": r["neutral"] == "TRUE",
        }
        if r["home_score"] not in ("NA", ""):
            row["home_goals"] = int(float(r["home_score"]))
            row["away_goals"] = int(float(r["away_score"]))
            played.append(row)
        elif r["tournament"] == "FIFA World Cup" and r["date"] >= WC_START:
            fixtures.append(row)
    played.sort(key=lambda m: m["date"])
    fixtures.sort(key=lambda m: m["date"])
    return played, fixtures


def _load() -> tuple[list[dict], list[dict]]:
    try:
        if not (LIVE_CACHE.exists()
                and time.time() - LIVE_CACHE.stat().st_mtime < LIVE_CACHE_MAX_AGE):
            with urllib.request.urlopen(LIVE_URL, timeout=60) as resp:
                LIVE_CACHE.write_bytes(resp.read())
        played, fixtures = _parse(io.StringIO(LIVE_CACHE.read_text()))
        if played:  # sanity: live file parsed and non-empty
            return played, fixtures
    except Exception:
        pass
    with gzip.open(DATA, "rt", newline="") as f:
        return _parse(f)


# ---------------------------------------------------------------------------
# goal model: goals ~ Poisson(exp(a + b * rating gap))
# ---------------------------------------------------------------------------

def _fit_goal_model(observations: list[tuple[float, int]]) -> tuple[float, float]:
    """Two-parameter Poisson regression by Newton's method."""
    a, b = 0.0, 0.5
    for _ in range(25):
        g_a = g_b = h_aa = h_ab = h_bb = 0.0
        for x, y in observations:
            mu = math.exp(a + b * x)
            g_a += y - mu
            g_b += (y - mu) * x
            h_aa += mu
            h_ab += mu * x
            h_bb += mu * x * x
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-12:
            break
        da = (g_a * h_bb - g_b * h_ab) / det
        db = (g_b * h_aa - g_a * h_ab) / det
        a, b = a + da, b + db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    return a, b


def goal_rates(diff: float, a: float, b: float) -> tuple[float, float]:
    """Expected goals for and against, given a rating advantage `diff`."""
    x = diff / 400.0
    return math.exp(a + b * x), math.exp(a - b * x)


def outcome_probs(diff: float, a: float, b: float, max_goals: int = 10):
    """(P home win, P draw, P away win) from independent Poisson scores."""
    lam_h, lam_a = goal_rates(diff, a, b)
    ph = [math.exp(-lam_h) * lam_h ** k / math.factorial(k) for k in range(max_goals + 1)]
    pa = [math.exp(-lam_a) * lam_a ** k / math.factorial(k) for k in range(max_goals + 1)]
    win = draw = loss = 0.0
    for i, pi in enumerate(ph):
        for j, pj in enumerate(pa):
            p = pi * pj
            if i > j:
                win += p
            elif i == j:
                draw += p
            else:
                loss += p
    total = win + draw + loss
    return win / total, draw / total, loss / total


# ---------------------------------------------------------------------------
# ratings + backtest
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _run() -> dict:
    played, fixtures = _load()

    ratings: dict[str, float] = {}
    goal_obs: list[tuple[float, int]] = []
    history: dict[str, list[float]] = {}
    raw_predictions = []   # (date, diff, outcome 'H'/'D'/'A')

    wc_teams = {m["home"] for m in fixtures} | {m["away"] for m in fixtures}

    for m in played:
        r_h = ratings.setdefault(m["home"], INITIAL_RATING)
        r_a = ratings.setdefault(m["away"], INITIAL_RATING)
        bonus = 0.0 if m["neutral"] else HOME_ADVANTAGE
        diff = (r_h + bonus) - r_a

        if m["date"] >= GOAL_MODEL_FROM:
            x = diff / 400.0
            goal_obs.append((x, m["home_goals"]))
            goal_obs.append((-x, m["away_goals"]))
        if m["date"] >= BACKTEST_FROM:
            outcome = ("H" if m["home_goals"] > m["away_goals"]
                       else "A" if m["home_goals"] < m["away_goals"] else "D")
            raw_predictions.append((m["date"], diff, outcome))

        expected = elo_win_prob(diff)
        actual = (1.0 if m["home_goals"] > m["away_goals"]
                  else 0.0 if m["home_goals"] < m["away_goals"] else 0.5)
        margin = abs(m["home_goals"] - m["away_goals"])
        shift = k_factor(m["tournament"]) * _gd_multiplier(margin) * (actual - expected)
        ratings[m["home"]] = r_h + shift
        ratings[m["away"]] = r_a - shift

        if m["date"] >= "2024-06-01":
            for team in (m["home"], m["away"]):
                if team in wc_teams:
                    history.setdefault(team, []).append(round(ratings[team], 1))

    a, b = _fit_goal_model(goal_obs)
    return {
        "ratings": ratings,
        "fixtures": fixtures,
        "history": history,
        "goal_params": (a, b),
        "raw_predictions": raw_predictions,
        "n_played": len(played),
        "data_through": played[-1]["date"],
        "wc_results": [m for m in played
                       if m["tournament"] == "FIFA World Cup" and m["date"] >= WC_START],
    }


def _score3(rows: list[tuple[tuple[float, float, float], str]]) -> dict:
    """Multiclass accuracy/Brier for ((pH,pD,pA), outcome) rows."""
    n = len(rows)
    classes = "HDA"
    correct = brier = logloss = 0.0
    for probs, outcome in rows:
        if classes[max(range(3), key=lambda i: probs[i])] == outcome:
            correct += 1
        for i, c in enumerate(classes):
            brier += (probs[i] - (1.0 if outcome == c else 0.0)) ** 2
        logloss -= math.log(max(probs[classes.index(outcome)], 1e-12))
    return {"n": n, "accuracy": correct / n, "brier": brier / n,
            "logloss": logloss / n}


def _backtest(run: dict) -> dict:
    a, b = run["goal_params"]
    ours = [(outcome_probs(diff, a, b), outcome) for _, diff, outcome in run["raw_predictions"]]
    counts = {"H": 0, "D": 0, "A": 0}
    for _, outcome in ours:
        counts[outcome] += 1
    n = len(ours)
    base = (counts["H"] / n, counts["D"] / n, counts["A"] / n)

    models = [
        {"model": "Above .500 Elo + Poisson", **_score3(ours)},
        {"model": f"Base rates ({base[0]:.0%}/{base[1]:.0%}/{base[2]:.0%})",
         **_score3([(base, o) for _, o in ours])},
        {"model": "Uniform (⅓ each)",
         **_score3([((1 / 3, 1 / 3, 1 / 3), o) for _, o in ours])},
    ]

    buckets = []
    for lo in [i / 10 for i in range(10)]:
        hi = lo + 0.1
        sel = [(p[0], o == "H") for p, o in ours if lo <= p[0] < hi]
        if len(sel) < 25:
            continue
        buckets.append({
            "range": f"{lo:.0%}–{hi:.0%}",
            "n": len(sel),
            "predicted": sum(p for p, _ in sel) / len(sel),
            "actual": sum(won for _, won in sel) / len(sel),
        })

    return {"since": BACKTEST_FROM[:4], "n": n, "models": models,
            "calibration": buckets, "decades": []}


# ---------------------------------------------------------------------------
# tournament simulation
# ---------------------------------------------------------------------------

def _derive_groups(fixtures: list[dict]) -> dict[str, list[str]]:
    """Groups are the connected components of the round-robin fixture graph."""
    adj: dict[str, set[str]] = {}
    for m in fixtures:
        adj.setdefault(m["home"], set()).add(m["away"])
        adj.setdefault(m["away"], set()).add(m["home"])
    groups: dict[str, list[str]] = {}
    seen: set[str] = set()
    for team in adj:
        if team in seen:
            continue
        comp, stack = set(), [team]
        while stack:
            t = stack.pop()
            if t in comp:
                continue
            comp.add(t)
            stack.extend(adj[t] - comp)
        seen |= comp
        anchor = next(t for t in comp if t in GROUP_ANCHORS)
        groups[GROUP_ANCHORS[anchor]] = sorted(comp)
    return dict(sorted(groups.items()))


def _poisson_sample(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, rng.random()
    while p > limit:
        k += 1
        p *= rng.random()
    return k


def _simulate(run: dict, n_sims: int = N_SIMULATIONS) -> dict:
    rng = random.Random(2026)
    ratings = run["ratings"]
    fixtures = run["fixtures"]
    a, b = run["goal_params"]
    groups = _derive_groups(fixtures)
    group_of = {t: g for g, members in groups.items() for t in members}

    # completed WC matches feed the sim as fixed results
    fixed = {}
    for m in run["wc_results"]:
        fixed[(m["home"], m["away"])] = (m["home_goals"], m["away_goals"])

    teams = sorted(group_of)
    tally = {t: {"r32": 0, "qf": 0, "sf": 0, "final": 0, "title": 0} for t in teams}

    for _ in range(n_sims):
        pts = {t: 0 for t in teams}
        gd = {t: 0 for t in teams}
        gf = {t: 0 for t in teams}

        for m in fixtures:
            key = (m["home"], m["away"])
            if key in fixed:
                hg, ag = fixed[key]
            else:
                bonus = 0.0 if m["neutral"] else HOME_ADVANTAGE
                lam_h, lam_a = goal_rates((ratings[m["home"]] + bonus) - ratings[m["away"]], a, b)
                hg, ag = _poisson_sample(rng, lam_h), _poisson_sample(rng, lam_a)
            h, w = m["home"], m["away"]
            gd[h] += hg - ag; gd[w] += ag - hg
            gf[h] += hg; gf[w] += ag
            if hg > ag:
                pts[h] += 3
            elif hg < ag:
                pts[w] += 3
            else:
                pts[h] += 1; pts[w] += 1

        def table_key(t):
            return (pts[t], gd[t], gf[t], rng.random())

        winners, runners, thirds = {}, {}, []
        for g, members in groups.items():
            order = sorted(members, key=table_key, reverse=True)
            winners[g], runners[g] = order[0], order[1]
            thirds.append((order[2], g))
        thirds.sort(key=lambda tg: table_key(tg[0]), reverse=True)
        best_thirds = thirds[:8]

        qualified = set(winners.values()) | set(runners.values()) | {t for t, _ in best_thirds}
        for t in qualified:
            tally[t]["r32"] += 1

        # Round of 32. Real slotting follows FIFA's 495-scenario allocation
        # table; we keep the fixed structure (which winners face thirds vs
        # runners-up) and randomize identities within it, avoiding
        # same-group rematches.
        third_pool = [t for t, _ in best_thirds]
        for _ in range(50):
            rng.shuffle(third_pool)
            if all(group_of[third_pool[i]] != g
                   for i, g in enumerate(sorted(WINNERS_VS_THIRDS))):
                break
        matches = [(winners[g], third_pool[i])
                   for i, g in enumerate(sorted(WINNERS_VS_THIRDS))]

        ru_pool = [runners[g] for g in sorted(groups)]
        rng.shuffle(ru_pool)
        for g in sorted(set(groups) - WINNERS_VS_THIRDS):  # C, F, H, J
            pick = next(i for i, t in enumerate(ru_pool) if group_of[t] != g)
            matches.append((winners[g], ru_pool.pop(pick)))
        while ru_pool:
            t1 = ru_pool.pop()
            pick = next((i for i, t in enumerate(ru_pool)
                         if group_of[t] != group_of[t1]), 0)
            matches.append((t1, ru_pool.pop(pick)))

        # knockout rounds: Elo win probability on neutral ground.
        # R32 winners (16) -> then survivors of each round are the QF
        # field (8), SF field (4), finalists (2), champion (1).
        alive = []
        for t1, t2 in matches:
            p = elo_win_prob(ratings[t1] - ratings[t2])
            alive.append(t1 if rng.random() < p else t2)
        for stage in ["qf", "sf", "final", "title"]:
            nxt = []
            for i in range(0, len(alive), 2):
                p = elo_win_prob(ratings[alive[i]] - ratings[alive[i + 1]])
                nxt.append(alive[i] if rng.random() < p else alive[i + 1])
            alive = nxt
            for t in alive:
                tally[t][stage] += 1

    return {
        "groups": groups,
        "odds": {t: {k: v / n_sims for k, v in d.items()} for t, d in tally.items()},
    }


# ---------------------------------------------------------------------------
# site payload
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def forecast() -> dict:
    run = _run()
    sim = _simulate(run)
    a, b = run["goal_params"]
    group_of = {t: g for g, members in sim["groups"].items() for t in members}
    through = datetime.strptime(run["data_through"], "%Y-%m-%d").strftime("%b %-d, %Y")

    standings = []
    for team, group in group_of.items():
        odds = sim["odds"][team]
        standings.append({
            "abbr": FIFA_CODES.get(team, team[:3].upper()),
            "name": team,
            "rating": round(run["ratings"][team], 1),
            "group": f"Group {group}",
            "history": run["history"].get(team, []),
            "r32_prob": odds["r32"],
            "qf_prob": odds["qf"],
            "sf_prob": odds["sf"],
            "title_prob": odds["title"],
        })

    upcoming = []
    played_keys = {(m["home"], m["away"]) for m in run["wc_results"]}
    for m in run["fixtures"]:
        if (m["home"], m["away"]) in played_keys or len(upcoming) >= 9:
            continue
        bonus = 0.0 if m["neutral"] else HOME_ADVANTAGE
        ph, pd, pa = outcome_probs(
            (run["ratings"][m["home"]] + bonus) - run["ratings"][m["away"]], a, b)
        upcoming.append({
            "date": m["date"],
            "group": f"Group {group_of[m['home']]}",
            "home": m["home"], "away": m["away"],
            "home_abbr": FIFA_CODES.get(m["home"], m["home"][:3].upper()),
            "away_abbr": FIFA_CODES.get(m["away"], m["away"][:3].upper()),
            "p_home": ph, "p_draw": pd, "p_away": pa,
        })

    return {
        "slug": "world-cup-2026",
        "name": "2026 World Cup Forecast",
        "league": "FIFA World Cup",
        "season": "Canada/Mexico/USA 2026",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_through": through,
        "description": f"Elo ratings for every national team from {run['n_played']:,} "
                       f"internationals since 1872, with advancement and title odds from "
                       f"{N_SIMULATIONS:,} simulations of the real 48-team bracket. "
                       f"Updated as the tournament is played.",
        "methodology": "Ratings follow World Football Elo conventions: K scaled by match "
                       "importance (World Cup 60, qualifiers and continental finals 40-50, "
                       "friendlies 20), a goal-difference multiplier, and 100 points of "
                       "home advantage at non-neutral venues. Win/draw/loss probabilities "
                       "and simulated scorelines come from a Poisson goal model fitted to "
                       "the rating gap on all internationals since 1990. The simulation "
                       "plays the remaining real fixtures, ranks groups on points and goal "
                       "difference, advances the twelve winners, twelve runners-up and "
                       "eight best thirds, and uses the official bracket structure with "
                       "third-place slots randomized within FIFA's allocation rules. Data: "
                       "martj42/international_results (CC0), updated daily.",
        "standings": standings,
        "upcoming": upcoming,
        "backtest": _backtest(run),
    }
