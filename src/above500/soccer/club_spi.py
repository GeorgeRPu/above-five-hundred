"""Club-team SPI ratings, the engine behind the roster-based prior.

FiveThirtyEight's roster-based World Cup SPI rests on "SPI ratings for
thousands of club teams." This module reproduces the club side with the
same online attack/defence fit the international model uses
(above500.soccer.wc_spi): every club carries an offensive and a defensive
rating, updated game by game from club results.

We fit chronologically over data/soccer/club_results.csv.gz and
snapshot each club's rating at the opening day of every World Cup we
forecast or backtest, so the roster prior for a given tournament uses
only club form available at that time. Ratings are returned as a
points-share SPI (0-100) versus an average club, the scale 538 used for
its player ratings.
"""

from __future__ import annotations

import csv
import gzip
import math
from functools import lru_cache

from .. import DATA_DIR

from .club_names import normalize
from .wc_spi import _spi  # points-share from off/def vs a neutral baseline

DATA = DATA_DIR / "soccer" / "club_results.csv.gz"

K_BASE = 0.07           # learning rate for club offence/defence
K_LEAGUE = 0.05         # learning rate for the per-league strength offset
GOAL_CAP = 6            # cap goals when updating, to limit blowouts
ACTIVE_WINDOW_DAYS = 730   # clubs counted in the gauge must be recently active

# Competitions that pit clubs from different leagues against each other.
# These don't update club ratings (that would reintroduce weak-league noise);
# they calibrate a per-league strength offset so the model learns that, e.g.,
# the English league is stronger than the Greek one.
CONTINENTAL = {"uefa.cl", "uefa.el", "uefa.conf", "libertadores",
               "sudamericana", "cwc"}

# World Cup opening days we snapshot club form at (plus the live edition).
SNAPSHOT_DATES = {
    2014: "2014-06-12",
    2018: "2018-06-14",
    2022: "2022-11-20",
    2026: "9999-12-31",   # use all available club form for the live forecast
}


def _load() -> list[dict]:
    with gzip.open(DATA, "rt", newline="") as f:
        rows = [
            {"date": r["date"], "league": r["league"],
             "home": normalize(r["home"]), "away": normalize(r["away"]),
             "hg": int(r["home_goals"]), "ag": int(r["away_goals"])}
            for r in csv.DictReader(f)
        ]
    rows.sort(key=lambda r: r["date"])
    return rows


def _days_before(date: str, ref: str) -> bool:
    """Crude date-difference test using ISO strings (yyyy-mm-dd)."""
    from datetime import date as _d
    a = _d.fromisoformat(date)
    b = _d.fromisoformat(ref if ref != "9999-12-31" else "2100-01-01")
    return 0 <= (b - a).days <= ACTIVE_WINDOW_DAYS


def _club_leagues(matches: list[dict]) -> dict[str, str]:
    """Each club's domestic league (the non-continental one it plays most)."""
    counts: dict[str, dict[str, int]] = {}
    for m in matches:
        if m["league"] in CONTINENTAL:
            continue
        for t in (m["home"], m["away"]):
            counts.setdefault(t, {})
            counts[t][m["league"]] = counts[t].get(m["league"], 0) + 1
    return {t: max(c, key=c.get) for t, c in counts.items()}


@lru_cache(maxsize=1)
def _fit() -> dict:
    matches = _load()
    hg = [m["hg"] for m in matches]
    ag = [m["ag"] for m in matches]
    HOME = math.log(sum(hg) / len(hg))
    AWAY = math.log(sum(ag) / len(ag))
    NEUTRAL = math.log((sum(hg) + sum(ag)) / (2 * len(matches)))

    club_league = _club_leagues(matches)
    off: dict[str, float] = {}      # within-league offensive rating
    dfn: dict[str, float] = {}      # within-league defensive rating
    lg: dict[str, float] = {}       # per-league strength offset
    last_seen: dict[str, str] = {}
    snaps = {y: None for y in SNAPSHOT_DATES}
    pending = sorted(SNAPSHOT_DATES.items(), key=lambda kv: kv[1])

    def snapshot(when):
        return (dict(off), dict(dfn), dict(lg), dict(last_seen), when)

    for m in matches:
        while pending and m["date"] >= pending[0][1]:
            year, when = pending.pop(0)
            snaps[year] = snapshot(when)

        h, a = m["home"], m["away"]
        ao_h = off.setdefault(h, 0.0); ad_h = dfn.setdefault(h, 0.0)
        ao_a = off.setdefault(a, 0.0); ad_a = dfn.setdefault(a, 0.0)
        gh, ga = min(m["hg"], GOAL_CAP), min(m["ag"], GOAL_CAP)
        last_seen[h] = last_seen[a] = m["date"]

        if m["league"] in CONTINENTAL:
            # cross-league game: hold club ratings, calibrate league offsets
            lh_, la_ = club_league.get(h), club_league.get(a)
            if lh_ is None or la_ is None or lh_ == la_:
                continue
            gap_h = lg.setdefault(lh_, 0.0); gap_a = lg.setdefault(la_, 0.0)
            lam_h = math.exp(HOME + (ao_h + gap_h) - (ad_a + gap_a))
            lam_a = math.exp(AWAY + (ao_a + gap_a) - (ad_h + gap_h))
            eh, ea = gh - lam_h, ga - lam_a
            lg[lh_] = gap_h + K_LEAGUE * (eh - ea)
            lg[la_] = gap_a + K_LEAGUE * (ea - eh)
        else:
            # domestic game: league offset cancels; update club ratings
            lam_h = math.exp(HOME + ao_h - ad_a)
            lam_a = math.exp(AWAY + ao_a - ad_h)
            eh, ea = gh - lam_h, ga - lam_a
            off[h] = ao_h + K_BASE * eh
            dfn[a] = ad_a - K_BASE * eh
            off[a] = ao_a + K_BASE * ea
            dfn[h] = ad_h - K_BASE * ea

    for year, when in pending:
        snaps[year] = snapshot(when)

    return {"snaps": snaps, "NEUTRAL": NEUTRAL, "club_league": club_league}


@lru_cache(maxsize=8)
def club_spi(year: int) -> dict[str, float]:
    """Points-share SPI (0-100) per club, as of `year`'s World Cup opening.

    A club's effective strength adds its league's calibrated offset to its
    within-league offence and defence, so clubs from stronger leagues rank
    above domestic dominators of weaker ones.
    """
    fit = _fit()
    snap = fit["snaps"].get(year) or fit["snaps"][2026]
    off, dfn, lg, last_seen, when = snap
    NEUTRAL, club_league = fit["NEUTRAL"], fit["club_league"]

    active = [t for t in off if _days_before(last_seen[t], when)]
    if not active:
        active = list(off)
    eff_off = {t: off[t] + lg.get(club_league.get(t), 0.0) for t in active}
    eff_dfn = {t: dfn[t] + lg.get(club_league.get(t), 0.0) for t in active}
    gauge = sum((eff_off[t] + eff_dfn[t]) / 2 for t in active) / len(active)
    return {t: round(_spi(eff_off[t] - gauge, eff_dfn[t] - gauge, NEUTRAL), 1)
            for t in active}


@lru_cache(maxsize=8)
def club_off_def(year: int) -> dict[str, tuple[float, float]]:
    """Gauge-centred log offensive/defensive ratings per club, as of `year`.

    Same effective ratings as `club_spi` (within-league rating plus the
    league's calibrated offset), but kept as the raw off/def pair so the
    roster prior can preserve a squad's attack/defence shape, as 538's
    player ratings did.
    """
    fit = _fit()
    snap = fit["snaps"].get(year) or fit["snaps"][2026]
    off, dfn, lg, last_seen, when = snap
    club_league = fit["club_league"]

    active = [t for t in off if _days_before(last_seen[t], when)]
    if not active:
        active = list(off)
    eff_off = {t: off[t] + lg.get(club_league.get(t), 0.0) for t in active}
    eff_dfn = {t: dfn[t] + lg.get(club_league.get(t), 0.0) for t in active}
    gauge = sum((eff_off[t] + eff_dfn[t]) / 2 for t in active) / len(active)
    return {t: (eff_off[t] - gauge, eff_dfn[t] - gauge) for t in active}
