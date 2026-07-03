"""Roster-strength prior from club-match SPI — FiveThirtyEight's method.

538's roster-based World Cup SPI rated each player by his club team's SPI
and minutes, then composited those over the national squad. We reproduce
that here:

    player_SPI = club_SPI x (0.75 + 0.25 x minutes_fraction)
    nation_rating = mean(player_SPI over the squad's covered players)

Club SPI comes from above500.soccer.club_spi (openfootball results); squads,
clubs and minutes from data/soccer/wc_squads.json (Transfermarkt, built
by scripts/soccer/prepare_wc_squads.py). A player whose club is outside the
openfootball league set has no club SPI and is skipped; nations with too
few covered players get no club-roster rating and fall back to the EA-FC
prior at blend time.
"""

from __future__ import annotations

import json
from functools import lru_cache

from .. import DATA_DIR

from . import club_spi
from .club_names import normalize

SQUADS_FILE = DATA_DIR / "soccer" / "wc_squads.json"
SQUAD_CREDIT_FLOOR = 0.75   # 538: 75% just for being on the club squad
MIN_COVERED = 8             # need this many rated players to trust a nation


@lru_cache(maxsize=1)
def _squads() -> dict:
    try:
        return json.loads(SQUADS_FILE.read_text()).get("squads", {})
    except Exception:
        return {}


def _nation_rating(squad: list[dict], clubs: dict[str, float]) -> tuple[float, int]:
    """538 composite for one squad; returns (rating, n_covered)."""
    vals = []
    for p in squad:
        if not p.get("club"):
            continue
        spi = clubs.get(normalize(p["club"]))
        if spi is None:
            continue
        credit = SQUAD_CREDIT_FLOOR + (1 - SQUAD_CREDIT_FLOOR) * p["minutes_fraction"]
        vals.append(spi * credit)
    if not vals:
        return 0.0, 0
    return sum(vals) / len(vals), len(vals)


@lru_cache(maxsize=8)
def roster_ratings(year: int) -> dict[str, float]:
    """{nation: club-SPI roster rating} for the given World Cup year."""
    squads = _squads().get(str(year), {})
    clubs = club_spi.club_spi(year)
    out = {}
    for nation, squad in squads.items():
        rating, n = _nation_rating(squad, clubs)
        if n >= MIN_COVERED:
            out[nation] = round(rating, 2)
    return out


@lru_cache(maxsize=8)
def roster_off_def(year: int) -> dict[str, tuple[float, float]]:
    """{nation: (off, def)} roster prior, preserving attack/defence shape.

    538's player ratings inherited the club's offensive and defensive
    ratings separately; a squad of players from high-scoring clubs reads
    as an attacking side, not just a strong one. Each covered player
    contributes his club's gauge-centred log off/def scaled by the 538
    squad credit; the nation's rating is the mean over covered players.
    """
    squads = _squads().get(str(year), {})
    clubs = club_spi.club_off_def(year)
    out = {}
    for nation, squad in squads.items():
        offs, dfns = [], []
        for p in squad:
            if not p.get("club"):
                continue
            od = clubs.get(normalize(p["club"]))
            if od is None:
                continue
            credit = SQUAD_CREDIT_FLOOR + (1 - SQUAD_CREDIT_FLOOR) * p["minutes_fraction"]
            offs.append(od[0] * credit)
            dfns.append(od[1] * credit)
        if len(offs) >= MIN_COVERED:
            out[nation] = (sum(offs) / len(offs), sum(dfns) / len(dfns))
    return out


def coverage(year: int) -> dict:
    """Diagnostics: per-nation covered counts and the biggest unmatched clubs."""
    squads = _squads().get(str(year), {})
    clubs = club_spi.club_spi(year)
    per_nation, unmatched = {}, {}
    for nation, squad in squads.items():
        covered = 0
        for p in squad:
            if not p.get("club"):
                continue
            if normalize(p["club"]) in clubs:
                covered += 1
            else:
                unmatched[p["club"]] = unmatched.get(p["club"], 0) + 1
        per_nation[nation] = (covered, len(squad))
    return {"per_nation": per_nation,
            "top_unmatched": sorted(unmatched.items(), key=lambda kv: -kv[1])[:25]}
