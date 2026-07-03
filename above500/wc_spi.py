"""Soccer Power Index (SPI) ratings and a 2026 FIFA World Cup forecast.

This follows the FiveThirtyEight / Nate Silver SPI approach rather than a
single-number Elo: every national team carries two ratings —

  * an **offensive** rating: goals it would be expected to score against
    an average team on a neutral field, and
  * a **defensive** rating: goals it would be expected to concede.

Ratings are fit online from goals scored and conceded (stochastic
gradient ascent on a Poisson attack/defence model), with the learning
rate scaled by match importance. Match win/draw/loss probabilities and
simulated scorelines come from the implied expected goals; tournament
odds come from Monte Carlo of the real 48-team bracket.

The headline **SPI** is, as 538 defined it, the share of points a team
would take against an average team over many neutral matches (a win is
worth 3 points, a draw 1), scaled to 0-100.

Difference from 538: their World Cup SPI blended 75% match-based ratings
with 25% roster-based ratings derived from club football. We use only
the match-based component, since the club/roster database isn't openly
available. Data: martj42/international_results (CC0), updated daily.
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

K_BASE = 0.05            # base learning rate for the online attack/defence fit
GOAL_CAP = 6             # cap goals when updating, to limit blowout influence
GOAL_MODEL_FROM = "1990-01-01"   # window for the league-average goal baselines
BACKTEST_FROM = "1994-01-01"     # 3-points-for-a-win era
N_SIMULATIONS = 100_000
WC_START = "2026-06-01"
HISTORICAL_WCS = {
    2014: "2014-06-12",
    2018: "2018-06-14",
    2022: "2022-11-20",
}

# Group letters keyed by an anchor team (groups come from the fixture
# graph; the letters match the official draw)
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

FLAG_ISO2 = {
    "Algeria": "dz", "Argentina": "ar", "Australia": "au", "Austria": "at",
    "Belgium": "be", "Bosnia and Herzegovina": "ba", "Brazil": "br",
    "Canada": "ca", "Cape Verde": "cv", "Colombia": "co", "Croatia": "hr",
    "Curaçao": "cw", "Czech Republic": "cz", "DR Congo": "cd",
    "Ecuador": "ec", "Egypt": "eg", "England": "gb-eng", "France": "fr",
    "Germany": "de", "Ghana": "gh", "Haiti": "ht", "Iran": "ir",
    "Iraq": "iq", "Ivory Coast": "ci", "Japan": "jp", "Jordan": "jo",
    "Mexico": "mx", "Morocco": "ma", "Netherlands": "nl",
    "New Zealand": "nz", "Norway": "no", "Panama": "pa", "Paraguay": "py",
    "Portugal": "pt", "Qatar": "qa", "Saudi Arabia": "sa",
    "Scotland": "gb-sct", "Senegal": "sn", "South Africa": "za",
    "South Korea": "kr", "Spain": "es", "Sweden": "se",
    "Switzerland": "ch", "Tunisia": "tn", "Turkey": "tr",
    "United States": "us", "Uruguay": "uy", "Uzbekistan": "uz",
}

MAJOR_FINALS = ("uefa euro", "copa américa", "african cup of nations",
                "afc asian cup", "concacaf championship", "gold cup")


def _importance(tournament: str) -> float:
    t = tournament.lower()
    if t == "fifa world cup":
        return 1.3
    if "qualification" in t:
        return 1.0
    if any(name in t for name in MAJOR_FINALS):
        return 1.1
    if t == "friendly":
        return 0.6
    return 0.85


# ---------------------------------------------------------------------------
# Poisson goal model
# ---------------------------------------------------------------------------

def _poisson_pmf(lam: float, kmax: int = 10) -> list[float]:
    out, p = [], math.exp(-lam)
    for k in range(kmax + 1):
        out.append(p)
        p *= lam / (k + 1)
    return out


def outcome_probs(lam_h: float, lam_a: float, max_goals: int = 10):
    """(P home win, P draw, P away win) from independent Poisson scores."""
    ph = _poisson_pmf(lam_h, max_goals)
    pa = _poisson_pmf(lam_a, max_goals)
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


def _spi(off: float, dfn: float, neutral: float) -> float:
    """Points share (×100) vs an average team on a neutral field."""
    lam_for = math.exp(neutral + off)
    lam_against = math.exp(neutral - dfn)
    pw, pd, _ = outcome_probs(lam_for, lam_against)
    return (3 * pw + pd) / 3 * 100


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def _parse(text_lines) -> tuple[list[dict], list[dict]]:
    """Split rows into (played matches, upcoming World Cup fixtures)."""
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
        if played:
            return played, fixtures
    except Exception:
        pass
    with gzip.open(DATA, "rt", newline="") as f:
        return _parse(f)


def _group_fixtures() -> list[dict]:
    """The complete group stage from the committed archive.

    Live data is preferred for results and ratings, but once the tournament
    starts its played group games drop out of the live fixture list, leaving
    too few to derive the 12 groups. The committed archive always holds the
    full pre-tournament fixture list, so the bracket structure comes from
    there; live results enter the simulation as fixed scores.
    """
    with gzip.open(DATA, "rt", newline="") as f:
        return _parse(f)[1]


# ---------------------------------------------------------------------------
# ratings: online attack/defence fit
# ---------------------------------------------------------------------------

def _mean(xs: list[int]) -> float:
    return sum(xs) / len(xs)


@lru_cache(maxsize=1)
def _run() -> dict:
    played, fixtures = _load()
    group_fixtures = _group_fixtures()

    # League-average goal baselines (home / away / neutral), modern era.
    hg, ag, ng = [], [], []
    for m in played:
        if m["date"] < GOAL_MODEL_FROM:
            continue
        if m["neutral"]:
            ng += [m["home_goals"], m["away_goals"]]
        else:
            hg.append(m["home_goals"])
            ag.append(m["away_goals"])
    HOME, AWAY, NEUTRAL = math.log(_mean(hg)), math.log(_mean(ag)), math.log(_mean(ng))

    off: dict[str, float] = {}   # log offensive strength
    dfn: dict[str, float] = {}   # log defensive strength
    wc_teams = {m["home"] for m in group_fixtures} | {m["away"] for m in group_fixtures}
    history_raw: dict[str, list[tuple[float, float]]] = {}
    raw_predictions = []         # (lam_home, lam_away, outcome)
    wc_snapshots: dict[int, dict] = {}  # year -> {off, dfn} at WC start
    wc_walk: dict[int, list[dict]] = {}  # year -> pre-match ratings, walk-forward

    for m in played:
        for wc_year, wc_start in HISTORICAL_WCS.items():
            if wc_year not in wc_snapshots and m["date"] >= wc_start:
                wc_snapshots[wc_year] = {"off": dict(off), "dfn": dict(dfn)}
        h, a = m["home"], m["away"]
        ao_h = off.setdefault(h, 0.0); ad_h = dfn.setdefault(h, 0.0)
        ao_a = off.setdefault(a, 0.0); ad_a = dfn.setdefault(a, 0.0)
        base_h = NEUTRAL if m["neutral"] else HOME
        base_a = NEUTRAL if m["neutral"] else AWAY
        lam_h = math.exp(base_h + ao_h - ad_a)
        lam_a = math.exp(base_a + ao_a - ad_h)

        if m["date"] >= BACKTEST_FROM:
            outcome = ("H" if m["home_goals"] > m["away_goals"]
                       else "A" if m["home_goals"] < m["away_goals"] else "D")
            raw_predictions.append((lam_h, lam_a, outcome))
            if m["tournament"] == "FIFA World Cup":
                y = int(m["date"][:4])
                if y in HISTORICAL_WCS:
                    # pre-game ratings; the WC backtest re-predicts from these
                    # so ratings walk forward through the tournament
                    wc_walk.setdefault(y, []).append({
                        "home": h, "away": a, "neutral": m["neutral"],
                        "off_h": ao_h, "dfn_h": ad_h,
                        "off_a": ao_a, "dfn_a": ad_a,
                        "outcome": outcome,
                    })

        # stochastic gradient step on the Poisson log-likelihood
        gh, ga = min(m["home_goals"], GOAL_CAP), min(m["away_goals"], GOAL_CAP)
        k = K_BASE * _importance(m["tournament"])
        eh, ea = gh - lam_h, ga - lam_a
        off[h] = ao_h + k * eh
        dfn[a] = ad_a - k * eh
        off[a] = ao_a + k * ea
        dfn[h] = ad_h - k * ea

        if m["date"] >= "2024-06-01":
            for t in (h, a):
                if t in wc_teams:
                    history_raw.setdefault(t, []).append((off[t], dfn[t]))

    # Forward-looking roster prior: blend the match-based ratings 25% toward
    # what each squad implies (FiveThirtyEight's roster share). No-op unless a
    # current roster snapshot is present. Applied to the final ratings only,
    # so the historical backtest below stays a pure match-based evaluation.
    from . import roster, club_roster
    off, dfn, roster_blended = roster.blend_off_def(
        off, dfn, wc_teams, club_roster.roster_off_def(2026))

    # Historical WC roster backtest. Ratings walk forward through each
    # tournament (each match predicted with everything learned up to it, as
    # the nightly production re-fit does and as 538's published forecasts
    # did); the roster prior is fixed at the tournament's opening day and
    # enters as a constant per-team adjustment. Three prediction sets:
    # match-only, EA-FC roster blend, and club-SPI roster blend.
    wc_backtest_data: dict[int, dict] = {}
    for wc_year in sorted(wc_walk):
        snap = wc_snapshots.get(wc_year)
        walk = wc_walk[wc_year]
        if not snap or not walk:
            continue
        hist_teams = {w["home"] for w in walk} | {w["away"] for w in walk}

        def _deltas(blended):
            b_off, b_dfn, ok = blended
            if not ok:
                return None
            return ({t: b_off[t] - snap["off"].get(t, 0.0) for t in hist_teams},
                    {t: b_dfn[t] - snap["dfn"].get(t, 0.0) for t in hist_teams})

        def _preds(deltas):
            d_off, d_dfn = deltas if deltas else ({}, {})
            preds = []
            for w in walk:
                base_h = NEUTRAL if w["neutral"] else HOME
                base_a = NEUTRAL if w["neutral"] else AWAY
                lh = math.exp(base_h + w["off_h"] + d_off.get(w["home"], 0.0)
                              - w["dfn_a"] - d_dfn.get(w["away"], 0.0))
                la = math.exp(base_a + w["off_a"] + d_off.get(w["away"], 0.0)
                              - w["dfn_h"] - d_dfn.get(w["home"], 0.0))
                preds.append((lh, la, w["outcome"]))
            return preds

        ea_deltas = _deltas(roster.blend(
            snap["off"], snap["dfn"], hist_teams,
            roster_ratings=roster.load_roster_for_year(wc_year)))
        club_deltas = _deltas(roster.blend_off_def(
            snap["off"], snap["dfn"], hist_teams,
            club_roster.roster_off_def(wc_year)))
        club_flat_deltas = _deltas(roster.blend(
            snap["off"], snap["dfn"], hist_teams,
            roster_ratings=club_roster.roster_ratings(wc_year)))

        wc_backtest_data[wc_year] = {
            "n_teams": len(hist_teams),
            "n_matches": len(walk),
            "match_only": _preds(None),
            "ea": _preds(ea_deltas) if ea_deltas else [],
            "club": _preds(club_deltas) if club_deltas else [],
            "club_flat": _preds(club_flat_deltas) if club_flat_deltas else [],
        }

    # The fit only identifies rating *differences*, so the absolute zero is a
    # free gauge. Anchor it on the World Cup field: SPI/Off/Def are expressed
    # relative to an average team in this tournament. This is display-only —
    # match probabilities and the simulation use the raw (gauge-invariant)
    # rating differences and are unaffected.
    gauge = sum((off[t] + dfn[t]) / 2 for t in wc_teams) / len(wc_teams)
    history = {t: [round(_spi(o - gauge, d - gauge, NEUTRAL), 1) for o, d in snaps]
               for t, snaps in history_raw.items()}

    return {
        "off": off, "dfn": dfn, "gauge": gauge,
        "roster_blended": roster_blended,
        "HOME": HOME, "AWAY": AWAY, "NEUTRAL": NEUTRAL,
        "fixtures": fixtures,
        "group_fixtures": group_fixtures,
        "wc_walk": wc_walk,
        "wc_snapshots": wc_snapshots,
        "history": history,
        "raw_predictions": raw_predictions,
        "n_played": len(played),
        "data_through": played[-1]["date"],
        "wc_results": [m for m in played
                       if m["tournament"] == "FIFA World Cup" and m["date"] >= WC_START],
        "wc_backtest_data": wc_backtest_data,
    }


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------

def _score3(rows) -> dict:
    n = len(rows)
    classes = "HDA"
    correct = brier = logloss = 0.0
    for probs, outcome in rows:
        if classes[max(range(3), key=lambda i: probs[i])] == outcome:
            correct += 1
        for i, c in enumerate(classes):
            brier += (probs[i] - (1.0 if outcome == c else 0.0)) ** 2
        logloss -= math.log(max(probs[classes.index(outcome)], 1e-12))
    return {"n": n, "accuracy": correct / n, "brier": brier / n, "logloss": logloss / n}


def _backtest(run: dict) -> dict:
    ours = [(outcome_probs(lh, la), o) for lh, la, o in run["raw_predictions"]]
    counts = {"H": 0, "D": 0, "A": 0}
    for _, o in ours:
        counts[o] += 1
    n = len(ours)
    base = (counts["H"] / n, counts["D"] / n, counts["A"] / n)

    models = [
        {"model": "Above .500 SPI", **_score3(ours)},
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


def _wc_backtest(run: dict) -> dict | None:
    """Score match-only, EA-FC blend and club-SPI blend on historical WCs."""
    wc_data = run.get("wc_backtest_data", {})
    if not wc_data:
        return None

    keys = ("match_only", "ea", "club", "club_flat")
    totals: dict[str, list] = {k: [] for k in keys}
    per_wc = []

    for year in sorted(wc_data):
        d = wc_data[year]
        entry = {"year": year, "n": d["n_matches"]}
        for k in keys:
            if not d.get(k):
                continue
            scored = [(outcome_probs(lh, la), o) for lh, la, o in d[k]]
            entry[k] = _score3(scored)
            totals[k].extend(scored)
        per_wc.append(entry)

    result: dict = {"per_wc": per_wc}
    for k in keys:
        if totals[k]:
            result[f"{k}_total"] = {**_score3(totals[k]), "n": len(totals[k])}
    return result


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
    off, dfn = run["off"], run["dfn"]
    NEUTRAL, HOME, AWAY = run["NEUTRAL"], run["HOME"], run["AWAY"]
    fixtures = run["group_fixtures"]
    groups = _derive_groups(fixtures)
    group_of = {t: g for g, members in groups.items() for t in members}
    teams = sorted(group_of)

    def xg(home, away, neutral):
        bh = NEUTRAL if neutral else HOME
        ba = NEUTRAL if neutral else AWAY
        return (math.exp(bh + off[home] - dfn[away]),
                math.exp(ba + off[away] - dfn[home]))

    def advance(t1, t2, rng):
        """Knockout: sample a neutral scoreline; resolve draws by win share."""
        l1, l2 = xg(t1, t2, True)
        g1, g2 = _poisson_sample(rng, l1), _poisson_sample(rng, l2)
        if g1 > g2:
            return t1
        if g2 > g1:
            return t2
        pw, _, pl = outcome_probs(l1, l2)
        return t1 if rng.random() < pw / (pw + pl) else t2

    # completed WC matches feed the sim as fixed results
    fixed = {(m["home"], m["away"]): (m["home_goals"], m["away_goals"])
             for m in run["wc_results"]}

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
                lh, la = xg(m["home"], m["away"], m["neutral"])
                hg, ag = _poisson_sample(rng, lh), _poisson_sample(rng, la)
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
            thirds.append(order[2])
        thirds.sort(key=table_key, reverse=True)
        best_thirds = thirds[:8]

        for t in set(winners.values()) | set(runners.values()) | set(best_thirds):
            tally[t]["r32"] += 1

        # Round of 32. Real third-place slotting follows FIFA's 495-scenario
        # table; we keep the fixed structure (which winners face thirds vs
        # runners-up) and randomize identities within it, avoiding same-group
        # rematches.
        third_pool = best_thirds[:]
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

        alive = [advance(t1, t2, rng) for t1, t2 in matches]
        for stage in ["qf", "sf", "final", "title"]:
            alive = [advance(alive[i], alive[i + 1], rng)
                     for i in range(0, len(alive), 2)]
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
    off, dfn, NEUTRAL, gauge = run["off"], run["dfn"], run["NEUTRAL"], run["gauge"]
    group_of = {t: g for g, members in sim["groups"].items() for t in members}
    through = datetime.strptime(run["data_through"], "%Y-%m-%d").strftime("%b %-d, %Y")

    def _flag(name):
        iso2 = FLAG_ISO2.get(name)
        return f"/assets/logos/flags/{iso2}.svg" if iso2 else None

    standings = []
    for team, group in group_of.items():
        odds = sim["odds"][team]
        o, d = off[team] - gauge, dfn[team] - gauge
        standings.append({
            "abbr": FIFA_CODES.get(team, team[:3].upper()),
            "name": team,
            "logo": _flag(team),
            "spi": round(_spi(o, d, NEUTRAL), 1),
            "attack": round(math.exp(NEUTRAL + o), 2),
            "defense": round(math.exp(NEUTRAL - d), 2),
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
        bh = NEUTRAL if m["neutral"] else run["HOME"]
        ba = NEUTRAL if m["neutral"] else run["AWAY"]
        lam_h = math.exp(bh + off[m["home"]] - dfn[m["away"]])
        lam_a = math.exp(ba + off[m["away"]] - dfn[m["home"]])
        ph, pd, pa = outcome_probs(lam_h, lam_a)
        upcoming.append({
            "date": m["date"],
            "group": f"Group {group_of[m['home']]}",
            "home": m["home"], "away": m["away"],
            "home_abbr": FIFA_CODES.get(m["home"], m["home"][:3].upper()),
            "away_abbr": FIFA_CODES.get(m["away"], m["away"][:3].upper()),
            "home_logo": _flag(m["home"]),
            "away_logo": _flag(m["away"]),
            "p_home": ph, "p_draw": pd, "p_away": pa,
        })

    return {
        "slug": "world-cup-2026",
        "name": "2026 World Cup Forecast",
        "league": "FIFA World Cup",
        "season": "Canada/Mexico/USA 2026",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_through": through,
        "description": f"Soccer Power Index ratings for every national team from "
                       f"{run['n_played']:,} internationals since 1872, with advancement "
                       f"and title odds from {N_SIMULATIONS:,} simulations of the real "
                       f"48-team bracket.",
        "methodology": "Soccer Power Index gives each team an offensive rating (goals it "
                       "would score against an average team on a neutral field) and a "
                       "defensive rating (goals it would concede), fit from goals scored "
                       "and conceded across every international, with the update weighted "
                       "by match importance (World Cup highest, friendlies lowest). The "
                       "SPI shown (0-100) is the share of points a team would take against "
                       "an average team in this World Cup field, and the offensive and "
                       "defensive numbers are goals scored and conceded against that same "
                       "reference. Win/draw/loss probabilities and simulated scorelines "
                       "come from a Poisson model on those ratings plus home advantage. "
                       "The simulation plays the remaining real fixtures, ranks groups on "
                       "points and goal difference, advances the twelve winners, twelve "
                       "runners-up and eight best thirds, and uses the official bracket "
                       "with third-place slots randomized within FIFA's allocation rules. "
                       + ("Following FiveThirtyEight, the ratings then blend 25% toward a "
                          "club-match roster prior — 538's own method. We rate club teams "
                          "with the same engine (from domestic leagues plus continental cups "
                          "that calibrate league against league), score each squad player by "
                          "his club's SPI weighted by minutes played, and composite over the "
                          "national squad. The overall backtest above reflects the match-only "
                          "model. " if run["roster_blended"] else
                          "A club-match roster blend (FiveThirtyEight used 25%) is wired in "
                          "but inactive until enough of the field is covered. ")
                       + "Match data: martj42/international_results (CC0), updated daily. "
                       + "Club results: openfootball; squads: Transfermarkt.",
        "standings": standings,
        "upcoming": upcoming,
        "backtest": _backtest(run),
        "wc_backtest": _wc_backtest(run),
    }
