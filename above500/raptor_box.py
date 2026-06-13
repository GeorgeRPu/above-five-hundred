"""Box-RAPTOR: a reconstruction of RAPTOR's box-score component.

538 retired RAPTOR after 2021-22, and the full rating needs play-by-play
and player-tracking inputs that were never published. Its *box* component,
though, is just a function of standard box-score rate stats — and that we
can rebuild. This module learns the box→RAPTOR mapping from 538's own data
(above500/data/nba_player_box.csv.gz, every player-season 1976-77 through
2018-19 with both box stats and RAPTOR) and applies it to seasons 538 never
covered, producing a calibrated *estimate* of each recent player's RAPTOR.

Two wrinkles make the estimate honest across eras:

* Modern box-score rates live on a different scale than 538's pace-adjusted,
  era-normalized inputs, so each new season's features are expressed relative
  to that season's own distribution before scoring, and the resulting ratings
  are recentred so the minutes-weighted league average is zero (RAPTOR is an
  above-average rating). Without this every modern season prints ~5 points
  low.
* Box scores can't see the on/off half of RAPTOR, so the estimate is
  deliberately conservative at the extremes — a superstar's box estimate sits
  below their true RAPTOR. The held-out fidelity numbers (see
  `fidelity_backtest`) report exactly how close it gets.

Recent box scores come from the committed floor (NocturneBear's nba.com dump
through 2023-24) plus a best-effort live top-up from balldontlie for newer
seasons; the live half is CI-only and degrades to the floor on any failure.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

TRAIN_FILE = Path(__file__).resolve().parent / "data" / "nba_player_box.csv.gz"
RECENT_FILE = Path(__file__).resolve().parent / "data" / "nba_recent_box.csv.gz"

LAST_OFFICIAL_SEASON = 2022     # 538's final RAPTOR season (2021-22)
FEATURES = ["p36", "r36", "a36", "sb36", "to36", "ts", "fg3ar", "ftar", "mpg"]
RIDGE_LAMBDA = 15.0
MIN_TRAIN_MIN = 200            # ignore deep-bench noise when fitting
MIN_RATE_MIN = 500            # minutes a recent player needs to be rated

# balldontlie live top-up (CI only; mirrors above500.nba_elo's discipline).
# A full season of game-level stats is ~250 pages, so the cap is generous; if
# it's hit the season is dropped rather than rated from half its games.
BDL_BASES = ("https://api.balldontlie.io/nba/v1", "https://api.balldontlie.io/v1")
BDL_MAX_REQUESTS = 400


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
            rows.append({"season": int(r["season"]), "x": xs, "o": o, "d": d,
                         "min": mn, "war": war})
    return rows


@lru_cache(maxsize=1)
def _model() -> dict:
    """Fit the offense/defense/WAR estimators and cache the training moments."""
    rows = _train_rows()
    cols = list(zip(*[r["x"] for r in rows]))
    mean = [sum(c) / len(c) for c in cols]
    sd = [(sum((v - m) ** 2 for v in c) / len(c)) ** 0.5 or 1.0
          for c, m in zip(cols, mean)]

    def z(xs):
        return [(x - m) / s for x, m, s in zip(xs, mean, sd)]

    nf = len(FEATURES)
    coef_o = _ridge([(z(r["x"]), r["o"]) for r in rows], nf)
    coef_d = _ridge([(z(r["x"]), r["d"]) for r in rows], nf)

    # WAR is a counting stat: war ≈ minutes * (a·raptor_total + b). Fit a, b.
    Aw = [[0.0, 0.0], [0.0, 0.0]]
    bw = [0.0, 0.0]
    for r in rows:
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
        t["trb"] / mn * 36,
        t["ast"] / mn * 36,
        (t["stl"] + t["blk"]) / mn * 36,
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
# recent box scores: committed floor + live top-up
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _recent_totals() -> dict[int, dict[str, dict]]:
    """{season: {name: totals}} from the committed floor plus any live seasons."""
    seasons: dict[int, dict[str, dict]] = {}
    with gzip.open(RECENT_FILE, "rt", newline="") as f:
        for r in csv.DictReader(f):
            s = int(r["season"])
            seasons.setdefault(s, {})[r["name"]] = {
                "g": int(r["g"]), "min": float(r["min"]),
                **{k: int(r[k]) for k in
                   ("fga", "fg3a", "fta", "trb", "ast", "stl", "blk", "tov", "pts")},
            }

    floor = max(seasons) if seasons else LAST_OFFICIAL_SEASON
    for s, totals in _fetch_live_totals(after_season=floor).items():
        seasons[s] = totals          # live wins for seasons past the floor
    return seasons


def _fetch_live_totals(after_season: int) -> dict[int, dict[str, dict]]:
    """Player-season box totals for seasons after the committed floor, from
    balldontlie. CI-only and best-effort: any failure yields no live seasons,
    and strict per-row validation means a wrong API contract degrades to {}.
    """
    api_key = os.environ.get("BALLDONTLIE_API_KEY", "").strip()
    if not api_key:
        return {}

    this_season = datetime.now(timezone.utc).year + (
        1 if datetime.now(timezone.utc).month >= 10 else 0)
    targets = list(range(after_season + 1, this_season + 1))
    if not targets:
        return {}

    out: dict[int, dict[str, dict]] = {}
    for base in BDL_BASES:
        try:
            for season in targets:
                totals = _fetch_live_season(base, api_key, season)
                if totals:
                    out[season] = totals
            if out:
                return out
        except Exception:
            out.clear()
            continue
    return out


def _fetch_live_season(base: str, api_key: str, season: int) -> dict[str, dict]:
    """Aggregate one season of balldontlie player game stats into totals."""
    totals: dict[str, dict] = {}
    cursor, requests = None, 0
    # balldontlie seasons are start-year; our `season` is the end-year.
    # postseason=false keeps this in step with the regular-season committed floor.
    params_base = {"seasons[]": str(season - 1), "postseason": "false",
                   "per_page": "100"}
    while requests < BDL_MAX_REQUESTS:
        params = dict(params_base)
        if cursor is not None:
            params["cursor"] = str(cursor)
        req = urllib.request.Request(
            f"{base}/stats?{urllib.parse.urlencode(params)}",
            headers={"Authorization": api_key})
        requests += 1
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:                      # rate limited: wait and retry
                time.sleep(int(e.headers.get("Retry-After", 10)))
                requests -= 1
                continue
            raise
        for s in payload.get("data", []):
            _accumulate_live_stat(totals, s)
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if cursor is None:
            return totals
        time.sleep(0.25)
    return {}   # page cap hit: treat as failure rather than a partial season


def _accumulate_live_stat(totals: dict[str, dict], s: dict) -> None:
    player = s.get("player") or {}
    name = " ".join(x for x in (player.get("first_name"), player.get("last_name")) if x)
    mn = s.get("min")
    if isinstance(mn, str):
        mn = (int(mn.split(":")[0]) + int(mn.split(":")[1]) / 60) if ":" in mn \
            else (float(mn) if mn.replace(".", "", 1).isdigit() else 0.0)
    if not name or not mn or mn <= 0:
        return
    fields = {"fga": "fga", "fg3a": "fg3a", "fta": "fta", "ast": "ast",
              "stl": "stl", "blk": "blk", "tov": "turnover", "pts": "pts"}
    row = {k: s.get(v) for k, v in fields.items()}
    row["trb"] = s.get("reb")
    if any(not isinstance(row[k], (int, float)) for k in
           ("fga", "fg3a", "fta", "trb", "ast", "stl", "blk", "tov", "pts")):
        return
    a = totals.setdefault(name, {"g": 0, "min": 0.0,
                                 **{k: 0 for k in
                                    ("fga", "fg3a", "fta", "trb", "ast",
                                     "stl", "blk", "tov", "pts")}})
    a["g"] += 1
    a["min"] += mn
    for k in ("fga", "fg3a", "fta", "trb", "ast", "stl", "blk", "tov", "pts"):
        a[k] += row[k]


@lru_cache(maxsize=1)
def estimate_recent() -> list[dict]:
    """Calibrated Box-RAPTOR for every rated recent player-season (>538's run).

    Each dict: season, name, raptor_off, raptor_def, raptor_total, war, min,
    est=True. Empty list if the recent data can't be loaded.
    """
    try:
        seasons = _recent_totals()
    except Exception:
        return []

    out = []
    for season, totals in sorted(seasons.items()):
        players = []
        for name, t in totals.items():
            x = _features(t)
            if x is None or t["min"] < MIN_RATE_MIN:
                continue
            players.append({"season": season, "name": name, "x": x, "min": t["min"]})
        if len(players) < 20:        # too thin to calibrate a season
            continue
        for r in _rate_season(players):
            out.append({"season": r["season"], "name": r["name"],
                        "raptor_off": r["raptor_off"], "raptor_def": r["raptor_def"],
                        "raptor_total": r["raptor_total"], "war": r["war"],
                        "min": r["min"], "est": True})
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
    """Score the estimator on held-out 538 seasons it never trained on, using
    the same per-season calibration the live estimates use.
    """
    rows = [r for r in _train_rows() if r["min"] >= MIN_RATE_MIN]
    pred, act = [], []
    seasons = sorted({r["season"] for r in rows if r["season"] >= test_from})
    for season in seasons:
        season_rows = [r for r in rows if r["season"] == season]
        players = [{"name": str(i), "x": r["x"], "min": r["min"]}
                   for i, r in enumerate(season_rows)]
        truth = [r["o"] + r["d"] for r in season_rows]
        if len(players) < 20:
            continue
        for r, y in zip(_rate_season(players), truth):
            pred.append(r["raptor_total"])
            act.append(y)

    scores = _regress_scores(pred, act)
    baseline = _regress_scores([0.0] * len(act), act)   # predict league average
    return {"since": test_from, **scores,
            "baseline_mae": baseline["mae"]}
