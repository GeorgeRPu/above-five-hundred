"""Above .500 — sports models and the helpers that render them.

Each model module exposes `forecast()` returning a dict in the site's
standard schema (see README). Quarto pages import the model, run it at
render time, and emit HTML via `above500.render`.
"""

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # src/above500/ -> repo root

# Committed data archives live outside the package so src/ stays code-only;
# models resolve their files from here (data/nba/..., data/soccer/...).
DATA_DIR = _ROOT / "data"


def _load_dotenv() -> None:
    """Populate os.environ from a gitignored .env at the repo root.

    Lets local renders pick up API keys (BALLDONTLIE_API_KEY, …) without
    exporting them each session. Existing variables win, so CI secrets are
    never overridden. Pure stdlib — no python-dotenv dependency.
    """
    env_path = _ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


MODELS = [
    {
        "slug": "world-cup-2026",
        "module": "soccer.wc_spi",
        "name": "2026 World Cup Forecast",
        "league": "FIFA World Cup",
        "description": "Advancement and title odds for all 48 teams, simulated "
                       "from Soccer Power Index ratings.",
        "color": "#6d904f",
        "href": "forecasts/world-cup-2026.qmd",
    },
    {
        "slug": "nba-elo",
        "module": "nba.elo",
        "name": "NBA Elo Ratings",
        "league": "NBA",
        "description": "Franchise Elo ratings from 75,000+ real games, backtested "
                       "on seven decades of results.",
        "color": "#fc4f30",
        "href": "forecasts/nba-elo.qmd",
    },
    {
        "slug": "nba-raptor",
        "module": "nba.raptor",
        "name": "NBA RAPTOR Player Ratings",
        "league": "NBA",
        "description": "Box-RAPTOR plus-minus and WAR for every player since 1977 — a "
                       "from-box-scores reconstruction of 538's RAPTOR, on one scale, "
                       "with a next-season projection.",
        "color": "#30a2da",
        "href": "forecasts/nba-raptor.qmd",
    },
]
