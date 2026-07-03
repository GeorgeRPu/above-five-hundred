---
# HuggingFace-style model card metadata
# spec: https://github.com/huggingface/hub-docs/blob/main/modelcard.md
tags:
  - sports
  - forecasting
  - soccer
  - world-cup
  - spi
  - monte-carlo
metrics:
  - accuracy
  - brier_score
---

# Model Card for 2026 World Cup Forecast

A Soccer Power Index (SPI) model for men's international football with
a club-based roster prior and a Monte Carlo simulation of the real 2026
World Cup bracket.

## Model Details

### Model Description

A Soccer Power Index (SPI) model in the style of FiveThirtyEight: every
national team carries an **offensive** rating (goals it would score
against an average team) and a **defensive** rating (goals it would
concede), fit online from goals scored and conceded across 49,000+
men's internationals since 1872, with the update weighted by match
importance. The headline SPI is the share of points a team would take
against an average team in the field. Win/draw/loss probabilities come
from a Poisson goal model on those ratings.

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

Tournament odds come from 10,000 Monte Carlo runs of the real 2026
bracket: the actual group fixtures (groups are derived from the fixture
graph and match the official draw), points/GD/GF ranking, twelve
winners + twelve runners-up + eight best thirds, and the official
round-of-32 structure with third-place slots randomized within FIFA's
allocation rules. Completed matches enter the simulation as fixed
results, so odds sharpen as the tournament is played.

- **Developed by:** [@GeorgeRPu](https://github.com/GeorgeRPu)
- **Model type:** online attack/defence (SPI) rating engine with a
  Poisson goal model for match probabilities, a club-SPI roster prior
  blended at 25%, and a 10,000-run Monte Carlo tournament simulation
- **License:** no license chosen for the code yet; match data is CC0
  (see [Training Data](#training-data))

#### Limitations

- Cross-*continental* club form is thinly linked: the only
  Europe–South America club matches in the archive are the Club World
  Cup, so inter-confederation league offsets rest on few games.
- Club-name resolution across data sources adds noise to the roster
  prior, and nations with too little covered squad fall back to
  match-only ratings.
- The live 2026 edition has no published roster files yet, so squads
  are proxied by each nation's 26 most valuable citizens until real
  rosters are released.
- In the backtest the EA-FC video-game prior outperforms the shipped
  club-SPI prior (0.5772 vs 0.5881 Brier); the club prior is kept for
  methodological fidelity to 538, and switching is a one-line change
  in `wc_spi.py` (see `src/above500/soccer/ea_roster.py`).
- International sides play few matches, so ratings move slowly and
  the model cannot see injuries or squad news beyond what the roster
  prior captures.

Treat tournament odds as sharpening over time: they are most
informative once real rosters are announced and group play begins,
when completed matches enter the simulation as fixed results.

### Model Sources

- **Repository:** https://github.com/GeorgeRPu/above-five-hundred
  (`src/above500/soccer/wc_spi.py`, page `forecasts/world-cup-2026.qmd`,
  slug `world-cup-2026`)
- **Demo:** https://georgerpu.github.io/above-five-hundred/forecasts/world-cup-2026.html
- **Paper:** FiveThirtyEight's Soccer Power Index and World Cup
  forecast methodology, including their club-based roster ratings

## Uses

### Direct Use

Editorial and entertainment forecasts: match win/draw/loss
probabilities and tournament advancement odds for the 2026 World Cup,
published on the Above .500 site.

### Out-of-Scope Use

Probabilities are model estimates, not betting advice; wagering
decisions are out of scope.

## How to Get Started with the Model

```python
from above500.soccer.wc_spi import forecast

payload = forecast()          # team SPIs, match probabilities, bracket odds
```

## Training Details

### Training Data

- International results:
  [martj42/international_results](https://github.com/martj42/international_results)
  (CC0), updated daily, re-fetched at render time with the committed
  archive (`data/soccer/intl_results.csv.gz`, built by
  `scripts/soccer/prepare_intl_results.py`) as offline fallback.
- Club results for the roster prior: assembled from
  [openfootball](https://github.com/openfootball/football.json) and
  [football-data.co.uk](https://www.football-data.co.uk/) into
  `data/soccer/club_results.csv.gz` by
  `scripts/soccer/prepare_club_results.py`.
- Squads and player clubs/minutes:
  [openfootball/worldcup](https://github.com/openfootball/worldcup)
  rosters joined to a
  [Transfermarkt dump](https://github.com/salimt/football-datasets) by
  `scripts/soccer/prepare_wc_squads.py` → `data/soccer/wc_squads.json`.

### Training Procedure

Ratings are fit online: matches are replayed in chronological order,
each result updating the two teams' attack and defence ratings with a
weight tied to match importance. The club engine adds a two-level fit
that learns each league a strength offset from inter-league cup games.
There is no offline training step; the nightly render replays the full
history and blends in the roster prior at 25%.

## Evaluation

### Testing Data, Factors & Metrics

#### Testing Data

- Match model: ~30,000 internationals since 1994.
- Tournament forecast: 192 World Cup matches (2014/2018/2022), with
  ratings walking forward through each tournament — every match
  predicted with only pre-game information, exactly as the nightly
  production re-fit works and as FiveThirtyEight's published
  forecasts did — and the roster prior fixed at each opening day.

#### Factors

Roster-prior variants are compared head-to-head (match-only, club-SPI
blend, EA-FC blend), and against 538's own published forecasts on the
128 matches (2018/2022) where those are available.

#### Metrics

Three-way (win/draw/loss) accuracy and multiclass Brier score, the
standard calibration measure for probabilistic match forecasts.

### Results

- Match model since 1994: **59.1% three-way accuracy, 0.521
  multiclass Brier** vs 0.631 for base rates.
- World Cup backtest: the club-SPI blend edges match-only (0.5881 vs
  0.5878 Brier is a wash; 52.1% accuracy both) and the EA-FC prior
  scores best (54.2%, 0.5772).
- Against 538's published forecasts (0.5772 in 2018, 0.6379 in 2022)
  on the same 128 matches: the EA-blend variant (0.5980) beats them
  and the shipped club blend (0.6108) roughly ties them, though 538
  leads on accuracy (56.3% vs ~52%).

#### Summary

The SPI engine clearly beats base rates on three decades of
internationals; at the World Cup level the club-roster blend is
competitive with 538's own published forecasts, while the EA-FC
variant (kept available but not shipped) scores best.

## Environmental Impact

- **Hardware Type:** CPU only (GitHub Actions runner, nightly render)
- **Hours used:** minutes per nightly build; no GPU training
- **Carbon Emitted:** negligible

## Model Card Contact

[Open an issue](https://github.com/GeorgeRPu/above-five-hundred/issues)
