# Above .500

A FiveThirtyEight-style home for sports models.

## Quick start

```bash
# uv: https://docs.astral.sh/uv/getting-started/installation/
# Quarto CLI: https://quarto.org/docs/get-started/
uv sync               # create .venv from pyproject.toml / uv.lock

uv run quarto preview # live-reloading local preview
uv run quarto render  # full build into _site/
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

### NBA Elo (`src/above500/nba/elo.py`)

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
Both are merged by `scripts/nba/prepare_games.py` into a committed
`data/nba/games.csv.gz` (~1 MB). At render time the model also
fetches any games newer than the archive from Paine's repo (falling
back to the archive if offline), so the nightly build keeps ratings as
current as the upstream data allows.

### NBA RAPTOR Player Ratings (`src/above500/nba/raptor.py`)

FiveThirtyEight's RAPTOR was their NBA *player*-value model: a plus-minus
rating in points per 100 possessions a player adds above league average
(split into offense and defense) rolled up into wins above replacement
(WAR). 538 published RAPTOR for 1976-77 through 2021-22, retired it in 2023,
and never released the full algorithm — it needs play-by-play and tracking
inputs that aren't public.

So this page doesn't use 538's published ratings at all. Every number is
**Box-RAPTOR**, computed two ways.

**1. Box-RAPTOR, a from-box-scores reconstruction for every season.** RAPTOR's
*box* component is just a function of box-score rate stats.
`src/above500/nba/raptor_box.py` learns that mapping with a pure-Python ridge
regression trained on 538's box-component RAPTOR — the box half of 538's
published box + on/off decomposition (available for 2014-2019 in their
[modern RAPTOR file](https://github.com/fivethirtyeight/data/tree/master/nba-raptor))
— then scores **every** season from box scores alone, 1976-77 through the
current season, producing one self-computed rating per player per year on a
single scale with no dependence on 538's published numbers. The eleven
features are per-36 scoring, offensive and defensive rebounds, assists, steals,
blocks and turnovers, plus true-shooting, three-point and free-throw rates and
minutes — rebounds and stocks kept split by side, since 538 fits offensive
boards to offense and defensive boards and blocks to defense. Because rates
live on different scales across eras, each season's features are expressed
relative to that season's own distribution and the ratings recentred so the
league average is zero. Against 538's box-component RAPTOR the reconstruction
achieves a **0.66 R² and 0.82 correlation** (1.27 MAE vs 2.16 for a
league-average guess); the fit is strong on offense (0.83 R²) and weaker on
defense (0.49), which box scores can only see through steals, blocks and
defensive boards.

**2. A next-season projection.** A player's coming-season Box-RAPTOR is
forecast from a recency- and minutes-weighted blend of their recent seasons,
regressed toward replacement level by a shrinkage that eases as the sample
grows (a Marcel/CARMELO-style recipe). Its parameters are fit on target
seasons through 2009 and evaluated, untouched, on 2010 onward; every
projection uses only earlier seasons. Walk-forward over **5,115 out-of-sample
player-seasons since 2010** it lands a mean absolute error of **1.28**
Box-RAPTOR points (0.73 correlation), versus 1.41 for carrying the prior
season forward and 1.95 for a flat baseline.

**Data**:
- Box-RAPTOR training and the 1976-77→2018-19 history —
  [fivethirtyeight/nba-player-advanced-metrics](https://github.com/fivethirtyeight/nba-player-advanced-metrics)
  (CC BY 4.0) for box-score rate stats, joined with the box-component RAPTOR
  from [fivethirtyeight/data/nba-raptor](https://github.com/fivethirtyeight/data/tree/master/nba-raptor)
  (the training target, available for 2014-2019), aggregated to one row per
  player-season by `scripts/nba/prepare_player_box.py` into
  `data/nba/player_box.csv.gz`.
- Recent regular-season box scores (the committed floor, 2019-20 to the
  current season) —
  [NocturneBear/NBA-Data-2010-2024](https://github.com/NocturneBear/NBA-Data-2010-2024)
  through 2023-24, then [Basketball-Reference](https://www.basketball-reference.com/)
  season totals for everything after, aggregated by
  `scripts/nba/prepare_recent_box.py` into `data/nba/recent_box.csv.gz`.
- Playoff box scores (1976-77 to the current season) — NocturneBear's playoff
  dump (2010-11 through 2023-24) plus Basketball-Reference playoff totals for
  all other seasons, aggregated by `scripts/nba/prepare_po_box.py` into
  `data/nba/po_box.csv.gz`.
- Both floor files are free, so rendering makes no API calls; re-run the
  scripts and commit to push coverage forward each season.

### 2026 World Cup Forecast (`src/above500/soccer/wc_spi.py`)

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

Following 538, the ratings blend 25% toward a **club-match roster
prior** — FiveThirtyEight's actual roster method, not a video-game proxy.
It is built in three steps:

1. **Club SPI.** We rate club teams with the same online attack/defence
   engine the international model uses (`src/above500/soccer/club_spi.py`, fit from
   `data/soccer/club_results.csv.gz`). The archive (84k matches, built
   by `scripts/soccer/prepare_club_results.py`) combines domestic leagues —
   the big European top flights from
   [openfootball](https://github.com/openfootball/football.json) plus MLS,
   Liga MX, Brazil, Argentina, Japan, Scotland, Turkey, Belgium and Greece
   from [football-data.co.uk](https://www.football-data.co.uk/) — with
   **continental cups** (UEFA Champions/Europa/Conference, Copa
   Libertadores/Sudamericana, Club World Cup). The cups are essential: a
   two-level fit learns each league a strength offset from these
   inter-league games, so a club isn't trapped in its league's own
   zero-sum scale.

2. **Player ratings.** Each squad player is scored by his club's SPI
   weighted by minutes played, exactly as 538 did:
   `player_SPI = club_SPI × (0.75 + 0.25 × minutes_fraction)`.

3. **Squad composite** (`src/above500/soccer/club_roster.py`). Each player carries
   his club's offensive *and* defensive rating (so a squad from
   high-scoring clubs reads as an attacking side, 538's structure); a
   nation's roster prior is the mean over its squad. Squads are the
   **real World Cup rosters** from
   [openfootball/worldcup](https://github.com/openfootball/worldcup),
   joined by name and birthdate to a
   [Transfermarkt dump](https://github.com/salimt/football-datasets) for
   each player's club and minutes (the live 2026 edition, whose roster
   files don't exist yet, uses each nation's 26 most valuable citizens);
   built by `scripts/soccer/prepare_wc_squads.py` →
   `data/soccer/wc_squads.json`, with club names reconciled by
   `src/above500/soccer/club_names.py`. Nations with too little covered squad fall
   back cleanly to match-only.

The blend pulls Argentina toward the field and lifts
England/Germany/Spain/France, closer to observer consensus.

**Backtest.** Over 192 World Cup matches (2014/2018/2022), ratings walk
forward through each tournament — every match predicted with only
pre-game information, exactly as the nightly production re-fit works and
as FiveThirtyEight's published forecasts did — with the roster prior
fixed at each opening day. The club-SPI blend edges match-only
(0.5881 vs 0.5878 Brier is a wash; 52.1% accuracy both) and an EA-FC
video-game prior scores best (54.2%, 0.5772): cross-*continental* club
form is still thinly linked (the only Europe–South America club matches
are the Club World Cup), and club-name resolution across sources adds
noise. For reference, 538's own published forecasts scored 0.5772 (2018)
and 0.6379 (2022) Brier on the same matches — on that 128-match subset
our EA-blend variant (0.5980) beats them and the shipped club blend
(0.6108) roughly ties them, though 538 leads on accuracy (56.3% vs
~52%). The EA-FC prior remains available (`src/above500/soccer/ea_roster.py`,
`scripts/soccer/fetch_ea_ratings.py` → `ea_ratings.json`, historical snapshots
from `scripts/soccer/prepare_ea_history.py`) and switching production to it
is a one-line change in `wc_spi.py`.

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
src/above500/            Python package: models + HTML renderers
  render.py              payload -> HTML (tables, matchups, sparklines)
  nba/                   NBA models
    elo.py               NBA Elo ratings + 1955+ backtest
    raptor.py            NBA RAPTOR ratings + next-season projection
    raptor_box.py        Box-RAPTOR: RAPTOR rebuilt from box scores, to ~2026
  soccer/                soccer models
    wc_spi.py            international football SPI + World Cup sim
    club_spi.py          club-team SPI engine (openfootball results)
    club_roster.py       538 club-SPI roster prior (squads × club SPI × minutes)
    ea_roster.py         EA-FC roster prior + EA/club ensemble blend
    club_names.py        club-name normalization across data sources
data/                    committed data archives (CC BY 4.0 / CC0)
  nba/                   NBA game and box-score archives
  soccer/                soccer results, squads, and roster priors
scripts/                 regenerate the data archives
  nba/
    prepare_games.py     merge the NBA/ABA game archives (538 + Paine)
    prepare_player_box.py    build player box-score history + RAPTOR target
    prepare_recent_box.py    build recent box-score floor (weekly CI job)
    prepare_po_box.py    build playoff box-score floor (NocturneBear + BRef)
  soccer/
    prepare_intl_results.py  build international results archive (martj42)
    prepare_club_results.py  build club-match archive (openfootball)
    prepare_wc_squads.py     build WC squads + clubs + minutes (Transfermarkt)
    fetch_ea_ratings.py      fetch EA-FC national ratings (weekly CI job)
    prepare_ea_history.py    build historical WC roster priors (FIFA 15/18/22)
styles/above500.scss     538-inspired Quarto theme
.github/workflows/deploy.yml   render + deploy, nightly cron
```
