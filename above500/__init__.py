"""Above .500 — sports models and the helpers that render them.

Each model module exposes `forecast()` returning a dict in the site's
standard schema (see README). Quarto pages import the model, run it at
render time, and emit HTML via `above500.render`.
"""

MODELS = [
    {
        "slug": "world-cup-2026",
        "module": "wc_spi",
        "name": "2026 World Cup Forecast",
        "league": "FIFA World Cup",
        "description": "Advancement and title odds for all 48 teams, simulated "
                       "from Soccer Power Index ratings.",
        "color": "#6d904f",
        "href": "forecasts/world-cup-2026.qmd",
    },
    {
        "slug": "nba-elo",
        "module": "nba_elo",
        "name": "NBA Elo Ratings",
        "league": "NBA",
        "description": "Franchise Elo ratings from 75,000+ real games, backtested "
                       "on seven decades of results.",
        "color": "#fc4f30",
        "href": "forecasts/nba-elo.qmd",
    },
    {
        "slug": "nba-raptor",
        "module": "nba_raptor",
        "name": "NBA RAPTOR Player Ratings",
        "league": "NBA",
        "description": "FiveThirtyEight's RAPTOR plus-minus and WAR for every player "
                       "since 1977, extended past 538's run with a calibrated "
                       "Box-RAPTOR estimate and a next-season projection.",
        "color": "#30a2da",
        "href": "forecasts/nba-raptor.qmd",
    },
]
