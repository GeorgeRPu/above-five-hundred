"""Shared club-name normalization for joining data sources.

Club names differ across providers (openfootball renames clubs between
seasons; Transfermarkt uses its own forms), so both the club-SPI engine
and the roster bridge canonicalize names through `normalize()` before
matching. `ALIASES` pins the handful of big clubs whose normalized forms
still disagree across sources.
"""

from __future__ import annotations

import re
import unicodedata

# Generic club-type tokens to drop, so "FC Bayern München" and
# "Bayern München" collapse to the same key.
_NOISE = {
    "fc", "cf", "afc", "sc", "ac", "ssc", "ss", "as", "rc", "cd", "ud",
    "sd", "ce", "sv", "vfb", "vfl", "tsg", "fsv", "bsc", "rcd", "ogc",
    "rsc", "club", "calcio", "1", "the",
}


def normalize(name: str) -> str:
    """Canonical key for a club name: accent-folded, lowercased, noise dropped.

    Pure-digit tokens (founding years like "1901"/"1907", "Bayer 04") are
    dropped so a club's name variants across data sources collapse to one key.
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    tokens = [t for t in s.split() if t not in _NOISE and not t.isdigit()]
    key = " ".join(tokens)
    return ALIASES.get(key, key)


# Normalized-form aliases: map a source's canonical key to the openfootball
# canonical key. Keys/values are already noise-stripped + accent-folded.
ALIASES: dict[str, str] = {
    "bayern": "bayern munchen",
    "bayern munich": "bayern munchen",
    "inter": "internazionale milano",
    "inter milan": "internazionale milano",
    "internazionale": "internazionale milano",
    "manchester utd": "manchester united",
    "man city": "manchester city",
    "man utd": "manchester united",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "atletico": "atletico madrid",
    "atletico de madrid": "atletico madrid",
    "atl madrid": "atletico madrid",
    "wolves": "wolverhampton wanderers",
    "wolverhampton": "wolverhampton wanderers",
    "dortmund": "borussia dortmund",
    "gladbach": "bor monchengladbach",
    "monchengladbach": "bor monchengladbach",
    "borussia monchengladbach": "bor monchengladbach",
    "leverkusen": "bayer leverkusen",
    "leipzig": "rb leipzig",
    "napoli": "ssc napoli",
    "roma": "as roma",
    "milan": "ac milan",
    "newcastle": "newcastle united",
    "benfica": "sl benfica",
    "porto": "fc porto",
    "sporting": "sporting cp",
    "sporting lisbon": "sporting cp",
    "ajax": "ajax amsterdam",
    "psv": "psv eindhoven",
    "feyenoord": "feyenoord rotterdam",
    # in-league clubs whose source forms differ from openfootball's
    "losc lille": "lille",
    "lille osc": "lille",
    "real sociedad de futbol": "real sociedad",
    "athletic bilbao": "athletic",
    "real betis balompie": "real betis",
    "lazio": "lazio roma",
    "zenit saint petersburg": "zenit st petersburg",
    "red bull salzburg": "rb salzburg",
    "fc red bull salzburg": "rb salzburg",
    "eintracht frankfurt": "eintracht frankfurt",
    "olympique lyon": "olympique lyonnais",
    "olympique marseille": "olympique de marseille",
    "vfl wolfsburg": "wolfsburg",
    "sporting clube de portugal": "sporting cp",
    "sc braga": "braga",
    "sporting braga": "braga",
    "vitoria guimaraes": "vitoria sc",
}
