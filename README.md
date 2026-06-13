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

Franchise Elo ratings computed from 75,705 real NBA/ABA games (1946-47
to the present), following FiveThirtyEight's published methodology:
1300 starting rating, 25% between-season reversion toward 1505, +100
Elo home-court advantage, K=20 with a margin-of-victory multiplier.

The model is backtested walk-forward over all 72,711 games since 1955
(every prediction uses only pre-game information): **67.5% accuracy,
0.2068 Brier score**, versus 0.2374 for always picking the home team
and 0.2500 for a coin flip. Its per-game probabilities reproduce the
538-lineage forecasts stored in the data to a mean absolute difference
of ~3e-6, a strong independent check on the implementation.

**Data**: [FiveThirtyEight's nbaallelo dataset](https://github.com/fivethirtyeight/data/tree/master/nba-elo)
(CC BY 4.0) through 2014-15, continued from 2015-16 onward by
[Neil Paine's maintained NBA-elo dataset](https://github.com/Neil-Paine-1/NBA-elo).
Both are merged by `scripts/prepare_nba_data.py` into a committed
`above500/data/nba_games.csv.gz` (~1 MB). At render time the model also
fetches any games newer than the archive from Paine's repo (falling
back to the archive if offline), so the nightly build keeps ratings as
current as the upstream data allows.

### 2026 World Cup Forecast (`above500/wc_spi.py`)

A Soccer Power Index (SPI) model in the style of FiveThirtyEight: every
national team carries an **offensive** rating (goals it would score
against an average team) and a **defensive** rating (goals it would
concede), fit online from goals scored and conceded across 49,000+
men's internationals since 1872, with the update weighted by match
importance. The headline SPI is the share of points a team would take
against an average team in the field. Win/draw/loss probabilities come
from a Poisson goal model on those ratings; backtested on ~30,000
matches since 1994 (59.1% three-way accuracy, 0.521 multiclass Brier vs
0.631 for base rates).

Unlike 538's World Cup SPI, this uses only the match-based component —
their model blended in 25% roster-based ratings derived from club
football, which needs club data that isn't openly available.

Tournament odds come from 10,000 Monte Carlo runs of the real 2026
bracket: the actual group fixtures (groups are derived from the fixture
graph and match the official draw), points/GD/GF ranking, twelve
winners + twelve runners-up + eight best thirds, and the official
round-of-32 structure with third-place slots randomized within FIFA's
allocation rules. Completed matches enter the simulation as fixed
results, so odds sharpen as the tournament is played.

**Data**: [martj42/international_results](https://github.com/martj42/international_results)
(CC0), updated daily, re-fetched at render time with the committed
archive as offline fallback.

## Layout

```
_quarto.yml              site config (nav, theme, execution)
index.qmd                home: model index
about.qmd                methodology
forecasts/                one page per model
  nba-elo.qmd
  world-cup-2026.qmd
above500/                Python package: models + HTML renderers
  nba_elo.py             NBA Elo ratings + 1955+ backtest
  wc_spi.py              international football SPI + World Cup sim
  render.py              payload -> HTML (tables, matchups, sparklines)
  data/                  committed data archives (CC BY 4.0 / CC0)
scripts/                 regenerate the data archives
styles/above500.scss     538-inspired Quarto theme
.github/workflows/deploy.yml   render + deploy, nightly cron
```
