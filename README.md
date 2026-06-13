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

### NBA RAPTOR Player Ratings (`above500/nba_raptor.py`)

FiveThirtyEight's RAPTOR was their NBA *player*-value model: a plus-minus
rating in points per 100 possessions a player adds above league average
(split into offense and defense) rolled up into wins above replacement
(WAR). 538 published RAPTOR for all 19,159 player-seasons from 1976-77
through 2021-22 and retired it in 2023; that full run is committed at
`above500/data/nba_raptor.csv.gz`.

The descriptive ratings shown in the leaderboard are 538's own. The model
this module **adds** is a next-season projection: a player's coming-season
RAPTOR is forecast from a recency- and possession-weighted blend of their
recent seasons, regressed toward replacement level by a shrinkage that eases
as the sample grows (a Marcel/CARMELO-style recipe). Its two free parameters
(reversion strength and replacement level) plus the recency decay are fit on
target seasons through 2009 and then evaluated, untouched, on 2010 onward.
Every projection uses only seasons played before the one it forecasts.

Walk-forward over **4,112 out-of-sample player-seasons since 2010** the
projection lands a mean absolute error of **1.65** RAPTOR points (0.673
correlation with actual), versus 2.02 for carrying 538's predictive PREDATOR
rating forward, 2.02 for prior-season RAPTOR, and 2.40 for a flat
replacement-level guess — i.e. the blend beats every carry-forward baseline,
including 538's own forward-looking rating.

**Data**: [FiveThirtyEight's nba-raptor dataset](https://github.com/fivethirtyeight/data/tree/master/nba-raptor)
(CC BY 4.0), trimmed to the ten columns the model needs by
`scripts/prepare_raptor_data.py`. RAPTOR is a retired model with no live
feed, so the archive is the complete 1976-77 to 2021-22 record; the nightly
build re-runs the projection and backtest from it.

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

Following 538, the ratings blend 25% toward a **roster-strength prior**
built from EA Sports FC 26 player ratings (an age-weighted mean of each
nation's best 23 overalls). This nudges ageing squads down and young,
deep squads up — the correction a results-only model misses. With the
blend, Argentina drops from 22% to 16% and Spain/England/France/Germany
all rise, matching observer consensus far better.

The prior comes from [EAFC26-DataHub](https://github.com/ismailoksuz/EAFC26-DataHub),
which commits the full FC 26 database to GitHub — so `scripts/fetch_roster.py`
builds the snapshot from one unauthenticated fetch (no API key, no rate
limit). It writes the derived per-nation aggregate to
`above500/data/roster_ratings.json` (just the 48 numbers, not EA's player
rows), which the SPI model reads at render time. The **Refresh World Cup
roster snapshot** workflow rebuilds it weekly. The blend is a no-op until
the snapshot covers at least half the field, so the model degrades
cleanly to match-only.

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
  nba-raptor.qmd
  world-cup-2026.qmd
above500/                Python package: models + HTML renderers
  nba_elo.py             NBA Elo ratings + 1955+ backtest
  nba_raptor.py          NBA RAPTOR ratings + next-season projection
  wc_spi.py              international football SPI + World Cup sim
  render.py              payload -> HTML (tables, matchups, sparklines)
  data/                  committed data archives (CC BY 4.0 / CC0)
scripts/                 regenerate the data archives
styles/above500.scss     538-inspired Quarto theme
.github/workflows/deploy.yml   render + deploy, nightly cron
```
