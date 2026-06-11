# Above .500

A FiveThirtyEight-style home for my own sports models: win probabilities,
power ratings, playoff and title odds — built with [Quarto](https://quarto.org),
with every model re-run at render time.

## How it works

```
above500/<model>.py          your model: exposes forecast() -> dict
        │  imported & run at render time
        ▼
forecasts/<model>.qmd        Quarto page: Python cells render HTML
        │  quarto render
        ▼
_site/                       static site, deployed to GitHub Pages
```

There is no committed data. Pages execute their models when the site is
rendered, and a scheduled GitHub Action re-renders daily (10:30 UTC), so
forecasts refresh automatically — push model code, not JSON.

## Quick start

```bash
# Quarto CLI: https://quarto.org/docs/get-started/
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

quarto preview        # live-reloading local preview
quarto render         # full build into _site/
```

To publish, enable GitHub Pages for this repository (Settings → Pages →
Source: **GitHub Actions**) and push to `main`.

## Publishing a model

1. Add a module to the `above500` package exposing `forecast()` that
   returns the payload below.
2. Register it in `above500.MODELS` (slug, name, league, card text, page).
3. Add a page under `forecasts/` that renders it — copy
   `forecasts/nba-elo.qmd`, which is a complete working example (Elo
   ratings plus Monte Carlo playoff odds).

### Forecast payload

```python
{
  "slug": "nba-elo",
  "name": "NBA Elo Forecast",
  "league": "NBA",
  "season": "2025-26 season",
  "updated": "2026-06-10T21:00:00Z",        # ISO 8601 UTC
  "description": "Dek shown under the headline.",
  "methodology": "Optional 'How this works' box.",

  "games": [
    {
      "date": "2026-06-11",
      "status": "upcoming",                  # or "final"
      "home": {"abbr": "MIL", "name": "Bucks", "color": "#00471b",
               "rating": 1583, "win_prob": 0.715, "score": None},
      "away": {"abbr": "LAL", "name": "Lakers", "color": "#552583",
               "rating": 1493, "win_prob": 0.285, "score": None},
    },
  ],

  "standings": [
    {
      "abbr": "BOS", "name": "Celtics", "color": "#007a33",
      "rating": 1716.8,
      "rating_change_7d": 4.2,
      "record": "91-29",
      "playoff_prob": 1.0,
      "title_prob": 0.34,
      "history": [1500, 1512.3, 1538.9],     # sparkline series
    },
  ],

  # optional, per-sport column header overrides
  "column_labels": {"rating": "Rating", "record": "Record",
                    "playoff_prob": "Make playoffs", "title_prob": "Win title"},
}
```

Notes:

- `games` with `status: "upcoming"` render as win-probability bars;
  `"final"` games render as results with the model's pre-game probability.
- Every section is optional — a model with only `standings` (or only
  `games`) renders fine.
- Probabilities are fractions in `[0, 1]`; the table shades cells by value.
- Helpers in `above500.render` turn the payload into HTML
  (`games_section`, `standings_table`, `byline`, `model_row`, …).

## Models

### NBA Elo (`above500/nba_elo.py`)

Franchise Elo ratings computed from 63,157 real NBA/ABA games (1946-47
through 2014-15), following FiveThirtyEight's published methodology:
1300 starting rating, 25% between-season reversion toward 1505, +100
Elo home-court advantage, K=20 with a margin-of-victory multiplier.

The model is backtested walk-forward over all 60,163 games since 1955
(every prediction uses only pre-game information): **68.0% accuracy,
0.2041 Brier score**, versus 0.2353 for always picking the home team
and 0.2500 for a coin flip. Its per-game probabilities reproduce
FiveThirtyEight's stored forecasts to a mean absolute difference of
3e-6, which is a strong independent check on the implementation.

**Data**: [FiveThirtyEight's nbaallelo dataset](https://github.com/fivethirtyeight/data/tree/master/nba-elo)
(CC BY 4.0), trimmed by `scripts/prepare_nba_data.py` into
`above500/data/nba_games.csv.gz` (~1 MB) so builds don't depend on the
upstream file.

## Layout

```
_quarto.yml              site config (nav, theme, execution)
index.qmd                home: model index
about.qmd                methodology
forecasts/nba-elo.qmd    one page per model
above500/                Python package: models + HTML renderers
  nba_elo.py             NBA Elo ratings + 1955-2015 backtest
  render.py              payload -> HTML (tables, matchups, sparklines)
  data/nba_games.csv.gz  historical game results (CC BY 4.0, 538)
scripts/prepare_nba_data.py    regenerates the game archive
styles/above500.scss     538-inspired Quarto theme
.github/workflows/deploy.yml   render + deploy, nightly cron
```
