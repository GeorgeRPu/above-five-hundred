"""Roster-strength prior for the World Cup SPI model.

FiveThirtyEight's World Cup SPI blended 75% match-based ratings with 25%
roster-based ratings derived from club football. This module supplies the
roster half: a per-nation strength score built from the current squads
(via API-Football, fetched by scripts/fetch_roster.py and committed to
data/roster_ratings.json), and a blend that shifts a team's match-based
offence/defence a quarter of the way toward what its squad implies.

The blend is gauge-aware and a no-op when the snapshot is missing or
covers too few teams, so the SPI model degrades cleanly to match-only.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

ROSTER_FILE = Path(__file__).resolve().parent / "data" / "roster_ratings.json"
DEFAULT_WEIGHT = 0.25      # 538's roster share
MIN_COVERAGE = 0.5         # need ratings for at least half the field to blend


def load_roster() -> dict[str, float]:
    """team name -> roster rating (arbitrary scale). Empty if unavailable."""
    try:
        data = json.loads(ROSTER_FILE.read_text())
    except Exception:
        return {}
    return {t: v["rating"] for t, v in data.get("teams", {}).items()
            if isinstance(v, dict) and v.get("rating") is not None}


def blend(off: dict[str, float], dfn: dict[str, float], teams,
          weight: float = DEFAULT_WEIGHT) -> tuple[dict[str, float], dict[str, float], bool]:
    """Shift each team's overall strength `weight` toward the roster signal.

    Overall strength is off+dfn (log scale). The roster rating is mapped
    onto the field's overall-strength distribution (mean/sd match), so the
    blend is unit-free; the resulting delta is split evenly between offence
    and defence to preserve a team's attack/defence balance. Returns new
    dicts plus a flag for whether the blend was applied.
    """
    rated = {t: r for t, r in load_roster().items() if t in teams}
    if len(rated) < MIN_COVERAGE * len(teams):
        return off, dfn, False

    overall = {t: off[t] + dfn[t] for t in teams}
    o_mean = statistics.mean(overall.values())
    o_sd = statistics.pstdev(overall.values()) or 1.0
    r_mean = statistics.mean(rated.values())
    r_sd = statistics.pstdev(rated.values()) or 1.0

    off2, dfn2 = dict(off), dict(dfn)
    for t, r in rated.items():
        roster_overall = o_mean + (r - r_mean) / r_sd * o_sd
        delta = weight * (roster_overall - overall[t])
        off2[t] += delta / 2
        dfn2[t] += delta / 2
    return off2, dfn2, True
