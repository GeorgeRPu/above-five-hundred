"""Above .500 — sports models and the helpers that render them.

Each model module exposes `forecast()` returning a dict in the site's
standard schema (see README). Quarto pages import the model, run it at
render time, and emit HTML via `above500.render`.
"""

MODELS = [
    {
        "slug": "nba-elo",
        "name": "NBA Elo Forecast",
        "league": "NBA",
        "description": "Game-by-game win probabilities, power ratings and title odds.",
        "color": "#ed713a",
        "href": "forecasts/nba-elo.qmd",
    },
]
