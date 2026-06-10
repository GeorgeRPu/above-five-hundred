# Above .500

A FiveThirtyEight-style home for my own sports models: win probabilities,
power ratings, playoff and title odds — published as plain JSON and rendered
by a fully static site.

## How it works

```
your model (Python, R, anything)
        │  writes JSON
        ▼
site/data/<model-slug>/latest.json     ← one file per model
site/data/models.json                  ← registry shown on the home page
        │  fetched in the browser
        ▼
site/index.html · forecast.html        ← static pages, no build step
```

The site has no build step and no dependencies. Push new JSON, and the
forecast updates. A GitHub Actions workflow deploys `site/` to GitHub Pages
on every push to `main`.

## Quick start

```bash
# Generate the sample NBA Elo forecast (demo of the full pipeline)
python3 scripts/generate_sample_data.py

# Preview locally
cd site && python3 -m http.server 8000
# open http://localhost:8000
```

To publish, enable GitHub Pages for this repository (Settings → Pages →
Source: **GitHub Actions**) and push to `main`.

## Publishing a model

1. Have your model write `site/data/<slug>/latest.json` (schema below).
2. Add the model to `site/data/models.json`.
3. Commit and push. The model card appears on the home page and its
   forecast renders at `forecast.html?model=<slug>`.

`scripts/generate_sample_data.py` is a complete working example (Elo
ratings plus Monte Carlo playoff odds).

### Registry — `site/data/models.json`

```json
{
  "models": [
    {
      "slug": "nba-elo",
      "name": "NBA Elo Forecast",
      "league": "NBA",
      "season": "2025-26 season",
      "description": "Shown on the model card.",
      "color": "#ed713a",
      "updated": "2026-06-10T21:00:00Z"
    }
  ]
}
```

### Forecast — `site/data/<slug>/latest.json`

```json
{
  "slug": "nba-elo",
  "name": "NBA Elo Forecast",
  "league": "NBA",
  "season": "2025-26 season",
  "updated": "2026-06-10T21:00:00Z",
  "description": "Dek shown under the headline.",
  "methodology": "Optional 'How this works' box.",

  "games": [
    {
      "date": "2026-06-11",
      "status": "upcoming",            // or "final"
      "home": { "abbr": "MIL", "name": "Bucks", "color": "#00471b",
                "rating": 1583, "win_prob": 0.715, "score": null },
      "away": { "abbr": "LAL", "name": "Lakers", "color": "#552583",
                "rating": 1493, "win_prob": 0.285, "score": null }
    }
  ],

  "standings": [
    {
      "abbr": "BOS", "name": "Celtics", "color": "#007a33",
      "rating": 1716.8,
      "rating_change_7d": 4.2,
      "record": "91-29",
      "playoff_prob": 1.0,
      "title_prob": 0.34,
      "history": [1500, 1512.3, 1538.9]   // sparkline series
    }
  ],

  "column_labels": {                       // optional, per-sport overrides
    "rating": "Rating", "record": "Record",
    "playoff_prob": "Make playoffs", "title_prob": "Win title"
  }
}
```

Notes:

- `games` with `status: "upcoming"` render as win-probability bars;
  `"final"` games render as results with the model's pre-game probability.
- Every section is optional — a model with only `standings` (or only
  `games`) renders fine.
- Probabilities are fractions in `[0, 1]`; the table shades cells by value.

## Layout

```
site/                  static site (deployed as-is)
  index.html           home: model cards
  forecast.html        one model's forecast (?model=<slug>)
  about.html           methodology
  css/style.css        538-inspired design system
  js/                  vanilla JS rendering (no framework)
  data/                model outputs (JSON)
scripts/
  generate_sample_data.py   sample Elo model + publishing example
.github/workflows/deploy.yml  GitHub Pages deploy
```
