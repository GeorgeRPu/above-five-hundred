"""Roster-strength prior for the World Cup SPI model.

FiveThirtyEight's World Cup SPI blended 75% match-based ratings with 25%
roster-based ratings derived from club football. This module supplies the
roster half and the blend that shifts a team's match-based offence/defence
a quarter of the way toward what its squad implies.

The production prior is the club-match SPI in above500.club_roster (538's
own method). This module's `blend` shifts the match ratings toward any
roster signal; it is gauge-aware and a no-op when coverage is too thin, so
the SPI model degrades cleanly to match-only. An EA-FC squad-overall prior
(scripts/fetch_roster.py -> data/roster_ratings.json, with historical
snapshots in roster_ratings_history.json) is kept as a backtest comparison.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

ROSTER_FILE = Path(__file__).resolve().parent / "data" / "roster_ratings.json"
HISTORY_FILE = Path(__file__).resolve().parent / "data" / "roster_ratings_history.json"
DEFAULT_WEIGHT = 0.25      # 538's roster share
MIN_COVERAGE = 0.35        # blend once a third of the field is rated


def load_roster() -> dict[str, float]:
    """team name -> roster rating (arbitrary scale). Empty if unavailable."""
    try:
        data = json.loads(ROSTER_FILE.read_text())
    except Exception:
        return {}
    return {t: v["rating"] for t, v in data.get("teams", {}).items()
            if isinstance(v, dict) and v.get("rating") is not None}


def load_roster_for_year(year: int) -> dict[str, float]:
    """Historical roster ratings for a specific WC year."""
    try:
        data = json.loads(HISTORY_FILE.read_text())
    except Exception:
        return {}
    edition = data.get("editions", {}).get(str(year), {})
    return {t: v["rating"] for t, v in edition.get("teams", {}).items()
            if isinstance(v, dict) and v.get("rating") is not None}


def blend(off: dict[str, float], dfn: dict[str, float], teams,
          weight: float = DEFAULT_WEIGHT,
          roster_ratings: dict[str, float] | None = None,
          ) -> tuple[dict[str, float], dict[str, float], bool]:
    """Shift each team's overall strength `weight` toward the roster signal.

    Overall strength is off+dfn (log scale). The roster rating is mapped
    onto the field's overall-strength distribution (mean/sd match), so the
    blend is unit-free; the resulting delta is split evenly between offence
    and defence to preserve a team's attack/defence balance. Returns new
    dicts plus a flag for whether the blend was applied.
    """
    source = roster_ratings if roster_ratings is not None else load_roster()
    rated = {t: r for t, r in source.items() if t in teams}
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


def blend_off_def(off: dict[str, float], dfn: dict[str, float], teams,
                  roster: dict[str, tuple[float, float]],
                  weight: float = DEFAULT_WEIGHT,
                  ) -> tuple[dict[str, float], dict[str, float], bool]:
    """Like `blend`, but the roster prior carries its own off/def shape.

    The roster's offensive and defensive components are each mapped onto
    the field's corresponding match-based distribution and blended side by
    side, so a squad drawn from high-scoring clubs shifts a nation's attack
    specifically — 538's structure — instead of splitting the delta evenly.
    """
    rated = {t: r for t, r in roster.items() if t in teams}
    if len(rated) < MIN_COVERAGE * len(teams):
        return off, dfn, False

    off2, dfn2 = dict(off), dict(dfn)
    for side, match_side, out in ((0, off, off2), (1, dfn, dfn2)):
        m_mean = statistics.mean(match_side[t] for t in teams)
        m_sd = statistics.pstdev([match_side[t] for t in teams]) or 1.0
        r_vals = [r[side] for r in rated.values()]
        r_mean = statistics.mean(r_vals)
        r_sd = statistics.pstdev(r_vals) or 1.0
        for t, r in rated.items():
            mapped = m_mean + (r[side] - r_mean) / r_sd * m_sd
            out[t] += weight * (mapped - match_side[t])
    return off2, dfn2, True
