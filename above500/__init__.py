"""Above .500 — sports models and the helpers that render them.

Each model module exposes `forecast()` returning a dict in the site's
standard schema (see README). Quarto pages import the model, run it at
render time, and emit HTML via `above500.render`.
"""

MODELS = [
    {
        "slug": "nba-elo",
        "name": "NBA Elo Ratings",
        "league": "NBA",
        "description": "Franchise Elo ratings from 63,157 real games, backtested "
                       "on six decades of results.",
        "color": "#fc4f30",
        "href": "forecasts/nba-elo.qmd",
    },
]
