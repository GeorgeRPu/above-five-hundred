---
# HuggingFace-style model card metadata
# spec: https://github.com/huggingface/hub-docs/blob/main/modelcard.md
tags:
  - sports
  - nba
  - player-ratings
  - ridge-regression
metrics:
  - r_squared
  - mae
  - pearsonr
---

# Model Card for NBA RAPTOR Player Ratings (Box-RAPTOR)

Box-RAPTOR: FiveThirtyEight's RAPTOR player-value metric rebuilt from
box scores for every season since 1976-77, plus a next-season
projection.

## Model Details

### Model Description

FiveThirtyEight's RAPTOR was their NBA *player*-value model: a plus-minus
rating in points per 100 possessions a player adds above league average
(split into offense and defense) rolled up into wins above replacement
(WAR). 538 published RAPTOR for 1976-77 through 2021-22, retired it in 2023,
and never released the full algorithm — it needs play-by-play and tracking
inputs that aren't public.

So this page doesn't use 538's published ratings at all. Every number is
**Box-RAPTOR**, computed two ways:

**1. Box-RAPTOR, a from-box-scores reconstruction for every season.** RAPTOR's
*box* component is just a function of box-score rate stats.
`src/above500/nba/raptor_box.py` learns that mapping with a pure-Python ridge
regression trained on 538's box-component RAPTOR, then scores **every**
season from box scores alone, 1976-77 through the current season,
producing one self-computed rating per player per year on a single
scale with no dependence on 538's published numbers. The eleven
features are per-36 scoring, offensive and defensive rebounds, assists, steals,
blocks and turnovers, plus true-shooting, three-point and free-throw rates and
minutes — rebounds and stocks kept split by side, since 538 fits offensive
boards to offense and defensive boards and blocks to defense.

**2. A next-season projection.** A player's coming-season Box-RAPTOR is
forecast from a recency- and minutes-weighted blend of their recent seasons,
regressed toward replacement level by a shrinkage that eases as the sample
grows (a Marcel/CARMELO-style recipe).

- **Developed by:** [@GeorgeRPu](https://github.com/GeorgeRPu)
- **Model type:** ridge regression from box-score rate stats to 538's
  box-component RAPTOR (pure Python), plus a Marcel/CARMELO-style
  weighted-blend projection for the coming season
- **License:** no license chosen for the code yet; source data is
  CC BY 4.0 (see [Training Data](#training-data))

#### Limitations

- These are reconstructions, not 538's actual published RAPTOR
  numbers; only RAPTOR's box component is rebuilt, and the on/off
  half of the decomposition (which needs non-public play-by-play and
  tracking inputs) is not modeled at all.
- Defense is the weak side of the fit (0.49 R² vs 0.83 on offense):
  box scores can only see defense through steals, blocks and
  defensive rebounds, so off-ball and positional defense is largely
  invisible.
- The ridge regression is trained on the 2014-2019 seasons for which
  538 published box-component RAPTOR; applying that mapping to
  earlier eras assumes the box-stats-to-value relationship is stable
  once features are normalized within each season.
- Ratings are recentred so each season's league average is zero, so
  they measure value relative to that season's league, not absolute
  quality across eras.

Read defensive ratings with particular caution, and treat historical
(pre-2014) ratings as extrapolations of a modern fit rather than
period-accurate measurements.

### Model Sources

- **Repository:** https://github.com/GeorgeRPu/above-five-hundred
  (`src/above500/nba/raptor.py`, `src/above500/nba/raptor_box.py`,
  page `forecasts/nba-raptor.qmd`, slug `nba-raptor`)
- **Demo:** https://georgerpu.github.io/above-five-hundred/forecasts/nba-raptor.html
- **Paper:** FiveThirtyEight's RAPTOR methodology and their published
  box + on/off RAPTOR decomposition

## Uses

### Direct Use

Editorial and entertainment player-value ratings and next-season
projections, published on the Above .500 site.

### Out-of-Scope Use

Not intended for real-world player valuation, contract, or wagering
decisions.

## How to Get Started with the Model

```python
from above500.nba.raptor import forecast

payload = forecast()          # player ratings + next-season projections
```

## Training Details

### Training Data

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

### Training Procedure

#### Preprocessing

Because rates live on different scales across eras, each season's
features are expressed relative to that season's own distribution,
and the resulting ratings are recentred so the league average is
zero.

#### Training Hyperparameters

- **Reconstruction:** pure-Python ridge regression on eleven per-36
  and rate features, trained on 538's box-component RAPTOR for
  2014-2019.
- **Projection:** recency- and minutes-weighted blend of recent
  seasons with shrinkage toward replacement level; parameters fit on
  target seasons through 2009 only.

## Evaluation

### Testing Data, Factors & Metrics

#### Testing Data

- Reconstruction: scored against 538's published box-component RAPTOR.
- Projection: 5,115 out-of-sample player-seasons, target seasons 2010
  onward, evaluated walk-forward — every projection uses only earlier
  seasons, and the parameters were never fit on this period.

#### Factors

Reconstruction quality is reported split by offense and defense, since
box scores carry very different signal for each side.

#### Metrics

R², Pearson correlation, and mean absolute error (MAE) in Box-RAPTOR
points, compared against naive baselines.

### Results

- **Reconstruction:** 0.66 R² and 0.82 correlation against 538's
  box-component RAPTOR (1.27 MAE vs 2.16 for a league-average guess);
  0.83 R² on offense, 0.49 on defense.
- **Projection:** 1.28 MAE (0.73 correlation) over the 5,115
  out-of-sample player-seasons, versus 1.41 for carrying the prior
  season forward and 1.95 for a flat baseline.

#### Summary

The box-only reconstruction tracks 538's box-component RAPTOR well —
strongly on offense, more loosely on defense — and the projection
beats both persistence and flat baselines out of sample.

## Environmental Impact

- **Hardware Type:** CPU only (GitHub Actions runner, nightly render)
- **Hours used:** minutes per nightly build; no GPU training
- **Carbon Emitted:** negligible

## Model Card Contact

[Open an issue](https://github.com/GeorgeRPu/above-five-hundred/issues)
