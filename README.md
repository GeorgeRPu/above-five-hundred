# Above .500

[![standard-readme compliant](https://img.shields.io/badge/readme%20style-standard-brightgreen.svg?style=flat-square)](https://github.com/RichardLitt/standard-readme)

> A FiveThirtyEight-style home for sports models.

Above .500 is a static Quarto site that publishes sports forecasts —
Elo ratings, player-value metrics, tournament simulations — in the
visual style of FiveThirtyEight. Each model lives in the `above500`
Python package, renders to a page under `forecasts/`, and is rebuilt
nightly by GitHub Actions so the numbers stay current.

## Table of Contents

- [Background](#background)
- [Install](#install)
- [Usage](#usage)
- [Models](#models)
- [Package Structure](#package-structure)
- [Contributing](#contributing)
  - [Publishing a model](#publishing-a-model)

## Background

FiveThirtyEight shuttered its sports desk in 2023 and closed for good
in 2025. This project re-creates its sports forecasts from public data and
published methodology: every model is implemented from scratch,
backtested walk-forward (each prediction uses only pre-game
information), and documented on the site alongside its numbers. The
site is built with [Quarto](https://quarto.org/) and deployed to
GitHub Pages.

## Install

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/)
and the [Quarto CLI](https://quarto.org/docs/get-started/).

```bash
uv sync               # create .venv from pyproject.toml / uv.lock
```

## Usage

```bash
uv run quarto preview # live-reloading local preview
uv run quarto render  # full build into _site/
```

To publish, enable GitHub Pages for this repository (Settings → Pages →
Source: **GitHub Actions**) and push to `main`.

## Models

Each model has a full model card under [docs/models/](docs/models/) —
following the
[Hugging Face model card template](https://huggingface.co/docs/hub/model-cards)
— covering its intended use, methodology, walk-forward backtest, data
sources, and limitations.

## Package Structure

```
_quarto.yml              site config (nav, theme, execution)
index.qmd                home: model index
about.qmd                methodology
forecasts/                one page per model
  nba-elo.qmd
  nba-raptor.qmd
  world-cup-2026.qmd
docs/models/             model cards: methodology, backtest, data sources
  nba-elo.md
  nba-raptor.md
  world-cup-2026.md
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

## Contributing

### Publishing a model

1. Add a module to the `above500` package exposing `forecast()` that
   returns the payload below.
2. Register it in `above500.MODELS` (slug, name, league, card text, page).
3. Add a page under `forecasts/` that renders it — copy
   `forecasts/nba-elo.qmd`, which is a complete working example (Elo
   ratings plus Monte Carlo playoff odds).
