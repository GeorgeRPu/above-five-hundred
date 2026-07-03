"""Box-RAPTOR: a reconstruction of RAPTOR from box scores, for every season.

538's full RAPTOR needs play-by-play and player-tracking inputs that were
never published, and 538 retired the metric after 2021-22. Its *box*
component, though, is just a function of standard box-score rate stats — and
that we can rebuild. This module learns the box→RAPTOR mapping from 538's own
box-component RAPTOR (the box half of the published box + on/off decomposition,
available for 2014-2019 in their modern RAPTOR file) and then scores *every*
season from box scores alone, producing one calibrated Box-RAPTOR rating per
player per year — for the seasons 538 published and the ones it never reached
alike. Nothing here copies 538's published composite RAPTOR; every rating the
site shows is the model's own estimate.

Two wrinkles make the estimate honest across eras:

* Box-score rates live on different scales across eras (and between 538's
  pace-adjusted inputs and raw nba.com totals), so each season's features are
  expressed relative to that season's own distribution before scoring, and the
  resulting ratings are recentred so the minutes-weighted league average is
  zero (RAPTOR is an above-average rating).
* The ridge regression is trained on the box component only, so it learns the
  signal that box scores can actually explain rather than trying to approximate
  the on/off half it structurally cannot see.

Box scores come from free sources, stitched into one continuous history:
538's historical file supplies named box stats through 2018-19, and the
committed floor (NocturneBear's nba.com dump through 2023-24, then Basketball-
Reference season totals) carries it to the current season. The floor is built
offline by scripts/nba/prepare_recent_box.py, so rendering makes no API calls;
re-run that script and commit to push coverage forward each season.
"""

from __future__ import annotations

import csv
import gzip
import math
from functools import lru_cache

from .. import DATA_DIR

TRAIN_FILE = DATA_DIR / "nba" / "player_box.csv.gz"
RECENT_FILE = DATA_DIR / "nba" / "recent_box.csv.gz"
PO_FILE = DATA_DIR / "nba" / "po_box.csv.gz"

# 538's historical box file supplies named features through this season; the
# committed recent floor covers every season after it.
LAST_HISTORICAL_SEASON = 2019
FEATURES = ["p36", "orb36", "drb36", "a36", "stl36", "blk36", "to36",
            "ts", "fg3ar", "ftar", "mpg"]
RIDGE_LAMBDA = 15.0
MIN_TRAIN_MIN = 200            # ignore deep-bench noise when fitting
MIN_RATE_MIN = 500            # minutes a recent player needs to be rated
MIN_RATE_MIN_PO = 150         # lower threshold for playoffs (~4+ games)


# ---------------------------------------------------------------------------
# pure-Python ridge regression (the repo avoids a numpy dependency)
# ---------------------------------------------------------------------------

def _solve(A: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting for the small normal system."""
    n = len(b)
    M = [A[i][:] + [b[i]] for i in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        piv = M[c][c]
        for j in range(c, n + 1):
            M[c][j] /= piv
        for r in range(n):
            if r != c:
                fac = M[r][c]
                for j in range(c, n + 1):
                    M[r][j] -= fac * M[c][j]
    return [M[i][n] for i in range(n)]


def _ridge(rows: list[tuple[list[float], float]], nf: int) -> list[float]:
    """Fit standardized-input ridge; returns [intercept, *weights]."""
    A = [[0.0] * (nf + 1) for _ in range(nf + 1)]
    b = [0.0] * (nf + 1)
    for zx, y in rows:
        z = [1.0] + zx
        for i in range(nf + 1):
            zi = z[i]
            Ai = A[i]
            for j in range(nf + 1):
                Ai[j] += zi * z[j]
            b[i] += zi * y
    for i in range(1, nf + 1):
        A[i][i] += RIDGE_LAMBDA
    return _solve(A, b)


# ---------------------------------------------------------------------------
# training
# ---------------------------------------------------------------------------

def _fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def _train_rows() -> list[dict]:
    rows = []
    with gzip.open(TRAIN_FILE, "rt", newline="") as f:
        for r in csv.DictReader(f):
            if r["type"] != "RS":
                continue
            xs = [_fnum(r[k]) for k in FEATURES]
            o, d = _fnum(r["raptor_off"]), _fnum(r["raptor_def"])
            mn, war = _fnum(r["min"]), _fnum(r["raptor_war"])
            if any(v is None for v in xs) or o is None or d is None or mn is None:
                continue
            if mn < MIN_TRAIN_MIN:
                continue
            bo, bd = _fnum(r.get("raptor_box_off")), _fnum(r.get("raptor_box_def"))
            rows.append({"season": int(r["season"]),
                         "player_id": r["player_id"], "name": r["player_name"],
                         "team": r.get("team", ""),
                         "x": xs, "o": o, "d": d, "bo": bo, "bd": bd,
                         "min": mn, "war": war})
    return rows


@lru_cache(maxsize=1)
def _model() -> dict:
    """Fit the offense/defense/WAR estimators and cache the training moments.

    The ridge regressions are trained only on player-seasons that carry the
    box-component RAPTOR split from 538's modern file (2014-2019 overlap).
    Standardisation moments come from the same subset so the z-scores are
    consistent.  The WAR mapping is fit on all rows with composite RAPTOR
    (which is available for the full 1977-2019 span).
    """
    all_rows = _train_rows()
    box_rows = [r for r in all_rows if r["bo"] is not None and r["bd"] is not None]

    cols = list(zip(*[r["x"] for r in box_rows]))
    mean = [sum(c) / len(c) for c in cols]
    sd = [(sum((v - m) ** 2 for v in c) / len(c)) ** 0.5 or 1.0
          for c, m in zip(cols, mean)]

    def z(xs):
        return [(x - m) / s for x, m, s in zip(xs, mean, sd)]

    nf = len(FEATURES)
    coef_o = _ridge([(z(r["x"]), r["bo"]) for r in box_rows], nf)
    coef_d = _ridge([(z(r["x"]), r["bd"]) for r in box_rows], nf)

    # WAR is a counting stat: war ≈ minutes * (a·raptor_total + b). Fit a, b.
    # Uses composite RAPTOR (full history) since WAR was only published as a
    # composite and the box/on-off split doesn't affect this linear mapping.
    Aw = [[0.0, 0.0], [0.0, 0.0]]
    bw = [0.0, 0.0]
    for r in all_rows:
        if r["war"] is None:
            continue
        feats = [(r["o"] + r["d"]) * r["min"], r["min"]]
        for i in range(2):
            for j in range(2):
                Aw[i][j] += feats[i] * feats[j]
            bw[i] += feats[i] * r["war"]
    war_a, war_b = _solve(Aw, bw)

    return {"mean": mean, "sd": sd, "coef_o": coef_o, "coef_d": coef_d,
            "war_a": war_a, "war_b": war_b}


def _predict_z(zx: list[float], coef: list[float]) -> float:
    return coef[0] + sum(c * v for c, v in zip(coef[1:], zx))


# ---------------------------------------------------------------------------
# features from box-score totals
# ---------------------------------------------------------------------------

def _features(t: dict) -> list[float] | None:
    mn = t["min"]
    if mn <= 0 or t["g"] <= 0:
        return None
    shots = t["fga"] + 0.44 * t["fta"]
    return [
        t["pts"] / mn * 36,
        t["orb"] / mn * 36,
        t["drb"] / mn * 36,
        t["ast"] / mn * 36,
        t["stl"] / mn * 36,
        t["blk"] / mn * 36,
        t["tov"] / mn * 36,
        t["pts"] / (2 * shots) if shots > 0 else 0.0,
        t["fg3a"] / t["fga"] if t["fga"] else 0.0,
        t["fta"] / t["fga"] if t["fga"] else 0.0,
        mn / t["g"],
    ]


def _rate_season(players: list[dict]) -> list[dict]:
    """Calibrated Box-RAPTOR for one season's players (each has x, min, name).

    Features are standardized by this season's own minutes-weighted moments,
    scored, and the totals recentred so the minutes-weighted average is zero.
    """
    m = _model()
    nf = len(FEATURES)
    W = sum(p["min"] for p in players)
    smean = [sum(p["x"][i] * p["min"] for p in players) / W for i in range(nf)]
    ssd = [((sum((p["x"][i] - smean[i]) ** 2 * p["min"] for p in players) / W) ** 0.5)
           or 1.0 for i in range(nf)]

    out = []
    for p in players:
        zx = [(p["x"][i] - smean[i]) / ssd[i] for i in range(nf)]
        o = _predict_z(zx, m["coef_o"])
        d = _predict_z(zx, m["coef_d"])
        out.append({**p, "raptor_off": o, "raptor_def": d, "raptor_total": o + d})

    centre = sum(r["raptor_total"] * r["min"] for r in out) / W
    for r in out:
        r["raptor_off"] -= centre / 2
        r["raptor_def"] -= centre / 2
        r["raptor_total"] -= centre
        r["war"] = r["min"] * (m["war_a"] * r["raptor_total"] + m["war_b"])
    return out


# ---------------------------------------------------------------------------
# recent box scores: the committed floor (scripts/nba/prepare_recent_box.py)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _recent_totals() -> dict[int, dict[str, dict]]:
    """{season: {name: totals}} from the committed floor.

    The floor is built offline from free sources (NocturneBear + Basketball-
    Reference) and reaches the current season, so there is no render-time API
    call here — re-run scripts/nba/prepare_recent_box.py to push it forward.
    """
    seasons: dict[int, dict[str, dict]] = {}
    with gzip.open(RECENT_FILE, "rt", newline="") as f:
        for r in csv.DictReader(f):
            s = int(r["season"])
            seasons.setdefault(s, {})[r["name"]] = {
                "g": int(r["g"]), "min": float(r["min"]),
                "team": r.get("team", ""),
                **{k: int(r[k]) for k in
                   ("fga", "fg3a", "fta", "orb", "drb", "trb",
                    "ast", "stl", "blk", "tov", "pts")},
            }
    return seasons


def _rate_player_seasons(players: list[dict],
                         min_minutes: int = MIN_RATE_MIN) -> list[dict]:
    """Calibrate Box-RAPTOR for player dicts carrying season/name/x/min.

    Players are grouped by season and each season is standardized and recentred
    on its own (>=20 rated players, each above *min_minutes*). player_id, when
    present, passes through. Returns flat estimate dicts, season order.
    """
    by_season: dict[int, list[dict]] = {}
    for p in players:
        if p["min"] < min_minutes:
            continue
        by_season.setdefault(p["season"], []).append(p)

    out = []
    for season in sorted(by_season):
        group = by_season[season]
        if len(group) < 20:          # too thin to calibrate a season
            continue
        for r in _rate_season(group):
            out.append({"season": r["season"], "name": r["name"],
                        "player_id": r.get("player_id"), "team": r.get("team", ""),
                        "raptor_off": r["raptor_off"], "raptor_def": r["raptor_def"],
                        "raptor_total": r["raptor_total"], "war": r["war"],
                        "min": r["min"], "est": True})
    return out


@lru_cache(maxsize=1)
def _estimate_historical() -> list[dict]:
    """Box-RAPTOR for the seasons 538's historical box file covers (<=2018-19)."""
    players = [{"season": r["season"], "name": r["name"],
                "player_id": r["player_id"], "team": r["team"],
                "x": r["x"], "min": r["min"]}
               for r in _train_rows()]
    return _rate_player_seasons(players)


@lru_cache(maxsize=1)
def estimate_recent() -> list[dict]:
    """Box-RAPTOR for seasons after 538's box history (recent floor + live).

    Empty list if the recent data can't be loaded.
    """
    try:
        seasons = _recent_totals()
    except Exception:
        return []
    players = []
    for season, totals in seasons.items():
        for name, t in totals.items():
            x = _features(t)
            if x is None:
                continue
            players.append({"season": season, "name": name, "x": x,
                            "min": t["min"], "team": t.get("team", "")})
    return _rate_player_seasons(players)


@lru_cache(maxsize=1)
def estimate_all() -> list[dict]:
    """Calibrated Box-RAPTOR for every rated player-season, oldest to newest.

    Stitches the historical estimate (538's box file, <=2018-19) and the recent
    estimate (committed floor + live top-up, 2019-20 on), which cover disjoint
    season ranges. Each dict: season, name, player_id (None for recent seasons),
    raptor_off, raptor_def, raptor_total, war, min, est=True.
    """
    historical = _estimate_historical()
    seen = {e["season"] for e in historical}
    out = historical + [e for e in estimate_recent() if e["season"] not in seen]
    out.sort(key=lambda e: (e["season"], -e["war"]))
    return out


# ---------------------------------------------------------------------------
# playoff Box-RAPTOR (from committed PO box scores)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _po_totals() -> dict[int, dict[str, dict]]:
    """{season: {name: totals}} from the committed playoff box-score file."""
    seasons: dict[int, dict[str, dict]] = {}
    try:
        f = gzip.open(PO_FILE, "rt", newline="")
    except FileNotFoundError:
        return seasons
    with f:
        for r in csv.DictReader(f):
            s = int(r["season"])
            seasons.setdefault(s, {})[r["name"]] = {
                "g": int(r["g"]), "min": float(r["min"]),
                "team": r.get("team", ""),
                **{k: int(r[k]) for k in
                   ("fga", "fg3a", "fta", "orb", "drb", "trb", "ast",
                    "stl", "blk", "tov", "pts")},
            }
    return seasons


@lru_cache(maxsize=1)
def estimate_all_po() -> list[dict]:
    """Calibrated Box-RAPTOR for every rated playoff player-season."""
    seasons = _po_totals()
    players = []
    for season, totals in seasons.items():
        for name, t in totals.items():
            x = _features(t)
            if x is None:
                continue
            players.append({"season": season, "name": name, "x": x,
                            "min": t["min"], "team": t.get("team", "")})
    out = _rate_player_seasons(players, min_minutes=MIN_RATE_MIN_PO)
    out.sort(key=lambda e: (e["season"], -e["war"]))
    return out


# ---------------------------------------------------------------------------
# fidelity backtest: how well box stats reproduce 538's real RAPTOR
# ---------------------------------------------------------------------------

def _regress_scores(pred: list[float], actual: list[float]) -> dict:
    n = len(pred)
    err = [p - a for p, a in zip(pred, actual)]
    mae = sum(abs(e) for e in err) / n
    am = sum(actual) / n
    ss_res = sum(e * e for e in err)
    ss_tot = sum((a - am) ** 2 for a in actual) or 1.0
    pm = sum(pred) / n
    cov = sum((p - pm) * (a - am) for p, a in zip(pred, actual))
    vp = sum((p - pm) ** 2 for p in pred)
    corr = cov / math.sqrt(vp * ss_tot) if vp > 0 else None
    return {"n": n, "mae": mae, "r2": 1 - ss_res / ss_tot, "corr": corr}


@lru_cache(maxsize=1)
def fidelity_backtest(test_from: int = 2014) -> dict:
    """Score the estimator against 538's box-component RAPTOR on the overlap
    seasons (2014-2019), using the same per-season calibration the live
    estimates use.  Only player-seasons with box RAPTOR targets are scored.
    """
    rows = [r for r in _train_rows()
            if r["min"] >= MIN_RATE_MIN
            and r["bo"] is not None and r["bd"] is not None
            and r["season"] >= test_from]
    pred, act = [], []
    seasons = sorted({r["season"] for r in rows})
    for season in seasons:
        season_rows = [r for r in rows if r["season"] == season]
        players = [{"name": str(i), "x": r["x"], "min": r["min"]}
                   for i, r in enumerate(season_rows)]
        truth = [r["bo"] + r["bd"] for r in season_rows]
        if len(players) < 20:
            continue
        for r, y in zip(_rate_season(players), truth):
            pred.append(r["raptor_total"])
            act.append(y)

    scores = _regress_scores(pred, act)
    baseline = _regress_scores([0.0] * len(act), act)   # predict league average
    return {"since": test_from, **scores,
            "baseline_mae": baseline["mae"]}
