---
# HuggingFace-style model card metadata
# spec: https://github.com/huggingface/hub-docs/blob/main/modelcard.md
tags:
  - sports
  - forecasting
  - nba
  - elo
metrics:
  - accuracy
  - brier_score
---

# Model Card for NBA Elo

Franchise Elo ratings for every NBA/ABA team, 1946-47 to the present,
with Monte Carlo playoff odds.

## Model Details

### Model Description

Franchise Elo ratings computed from 75,705 real NBA/ABA games (1946-47
to the present), following FiveThirtyEight's published methodology:
1300 starting rating, 25% between-season reversion toward 1505, +100
Elo home-court advantage, K=20 with a margin-of-victory multiplier.
Playoff and title odds come from Monte Carlo simulation of the
remaining season.

- **Developed by:** [@GeorgeRPu](https://github.com/GeorgeRPu)
- **Model type:** online Elo rating system with a Monte Carlo season
  simulation, re-fit nightly from the full game history
- **License:** no license chosen for the code yet; game data is
  CC BY 4.0 (see [Training Data](#training-data))

#### Limitations

- The model sees only final scores, dates, and home/away venues. It
  cannot anticipate injuries, trades, rest days, or lineup changes;
  news is absorbed only as it shows up in results.
- Ratings carry over between seasons with a fixed 25% reversion toward
  the mean, so large offseason roster turnover is reflected slowly.
- Home-court advantage is a single +100 Elo constant across all teams
  and eras, though its true size has varied over league history.
- Ratings are only as current as the upstream game feed; if the fetch
  fails, the build falls back to the committed archive.

Treat the probabilities as team-strength estimates from results alone;
combine with roster and injury news before drawing conclusions about
any specific upcoming game.

### Model Sources

- **Repository:** https://github.com/GeorgeRPu/above-five-hundred
  (`src/above500/nba/elo.py`, page `forecasts/nba-elo.qmd`, slug `nba-elo`)
- **Demo:** https://georgerpu.github.io/above-five-hundred/forecasts/nba-elo.html
- **Paper:** FiveThirtyEight's published NBA Elo methodology

## Uses

### Direct Use

Editorial and entertainment forecasts: pre-game win probabilities,
team power ratings, and playoff/title odds, published on the Above
.500 site.

### Out-of-Scope Use

Probabilities are model estimates, not betting advice; wagering
decisions are out of scope.

## How to Get Started with the Model

```python
from above500.nba.elo import forecast

payload = forecast()          # ratings, games, playoff odds
```

## Training Details

### Training Data

[FiveThirtyEight's nbaallelo dataset](https://github.com/fivethirtyeight/data/tree/master/nba-elo)
(CC BY 4.0) through 2014-15, continued from 2015-16 onward by
[Neil Paine's maintained NBA-elo dataset](https://github.com/Neil-Paine-1/NBA-elo).
Both are merged by `scripts/nba/prepare_games.py` into a committed
`data/nba/games.csv.gz` (~1 MB). At render time the model also
fetches any games newer than the archive from Paine's repo (falling
back to the archive if offline), so the nightly build keeps ratings as
current as the upstream data allows.

### Training Procedure

The ratings are fit online: games are replayed in chronological order
and each result updates the two teams' ratings (K=20, scaled by a
margin-of-victory multiplier), with 25% reversion toward 1505 between
seasons. There is no offline training step; every nightly render
replays the full history.

## Evaluation

### Testing Data, Factors & Metrics

#### Testing Data

All 72,711 games since 1955, evaluated walk-forward: every prediction
uses only pre-game information, exactly as the nightly production
re-fit works.

#### Metrics

Accuracy (did the higher-probability team win) and Brier score
(mean squared error of the win probability, lower is better), the
standard measure of probabilistic forecast calibration.

### Results

**67.5% accuracy, 0.2068 Brier score**, versus 0.2374 for always
picking the home team and 0.2500 for a coin flip. Per-game
probabilities reproduce the 538-lineage forecasts stored in the data
to a mean absolute difference of ~3e-6, a strong independent check on
the implementation.

#### Summary

The implementation matches FiveThirtyEight's published Elo results to
numerical precision and comfortably beats naive baselines over seven
decades of walk-forward evaluation.

## Environmental Impact

- **Hardware Type:** CPU only (GitHub Actions runner, nightly render)
- **Hours used:** minutes per nightly build; no GPU training
- **Carbon Emitted:** negligible

## Model Card Contact

[Open an issue](https://github.com/GeorgeRPu/above-five-hundred/issues)
