"""HTML renderers for forecast payloads, matching the site's 538-style CSS.

Quarto pages call these from Python cells and emit the result with
`output: asis`. Everything returns plain HTML strings.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

ACCENTS = ["#30a2da", "#ed713a", "#77ab43", "#8b62a8", "#d63b3b", "#e3ba22"]


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------

def fmt_pct(p, decimals: int = 0) -> str:
    if p is None:
        return "—"
    pct = p * 100
    if 99 < pct < 100:
        return ">99%"
    if 0 < pct < 1:
        return "<1%"
    return f"{pct:.{decimals}f}%"


def fmt_signed(n) -> str:
    if n is None:
        return "—"
    r = round(n)
    return f"+{r}" if r > 0 else f"{r}"


def fmt_date(iso: str) -> str:
    if not iso:
        return ""
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%a, %b %-d")


def fmt_updated(iso: str) -> str:
    if not iso:
        return ""
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").strftime("%b %-d, %Y, %H:%M UTC")


def prob_shade(p) -> str:
    """538-style heat shading: white at 0% through saturated green at 100%."""
    if p is None:
        return "transparent"
    t = max(0.0, min(1.0, p))
    light = 100 - t * 42  # 100% (white) -> 58%
    return f"hsl(88, 45%, {light:.0f}%)"


def accent_for(seed: str) -> str:
    h = 0
    for c in str(seed):
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return ACCENTS[h % len(ACCENTS)]


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------

def team_cell(team: dict, sub: str | None = None) -> str:
    abbr = team.get("abbr") or (team.get("name", "?")[:3].upper())
    color = team.get("color") or accent_for(abbr)
    name = escape(team.get("name") or abbr)
    sub_html = f' <span class="team-sub">{escape(str(sub))}</span>' if sub else ""
    return (
        f'<div class="team-cell">'
        f'<span class="team-dot" style="background:{color}">{escape(abbr)}</span>'
        f'<span><span class="team-name">{name}</span>{sub_html}</span>'
        f"</div>"
    )


def sparkline(series, width: int = 110, height: int = 28) -> str:
    if not series or len(series) < 2:
        return ""
    lo, hi = min(series), max(series)
    span = (hi - lo) or 1.0
    pad = 3
    pts = []
    for i, v in enumerate(series):
        x = pad + (i / (len(series) - 1)) * (width - 2 * pad)
        y = height - pad - ((v - lo) / span) * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    lx, ly = pts[-1].split(",")
    return (
        f'<svg class="spark" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<polyline points="{" ".join(pts)}"></polyline>'
        f'<circle class="spark-dot" cx="{lx}" cy="{ly}" r="2.5"></circle>'
        f"</svg>"
    )


def _prob_td(p) -> str:
    color = ";color:#fff" if (p is not None and p > 0.75) else ""
    return f'<td class="prob" style="background:{prob_shade(p)}{color}">{fmt_pct(p)}</td>'


def _team_row(team: dict, opponent: dict, status: str) -> str:
    is_final = status == "final"
    if is_final:
        winning = (team.get("score") or 0) > (opponent.get("score") or 0)
    else:
        winning = (team.get("win_prob") or 0) >= (opponent.get("win_prob") or 0)
    cls = "winner" if winning else "loser"

    rating = team.get("rating")
    cell = team_cell(team, sub=str(round(rating)) if rating is not None else None)
    pct = f'<span class="pct">{fmt_pct(team.get("win_prob"))}</span>'

    if is_final:
        score = team.get("score")
        tail = f'<span class="score">{score if score is not None else "—"}</span>'
    else:
        width = round((team.get("win_prob") or 0) * 100)
        fill_style = f"width:{width}%" + ("" if winning else ";background:var(--ink-faint)")
        tail = f'<div class="bar-track"><div class="bar-fill" style="{fill_style}"></div></div>'

    return f'<div class="matchup-row {cls}">{cell}{pct}{tail}</div>'


# ---------------------------------------------------------------------------
# page-level sections
# ---------------------------------------------------------------------------

def byline(forecast: dict) -> str:
    season = forecast.get("season")
    season_html = f"<strong>{escape(season)}</strong> · " if season else ""
    return (
        f'<p class="byline">{season_html}'
        f'<span class="updated-stamp">Updated {fmt_updated(forecast.get("updated"))}'
        f"</span></p>"
    )


def section_head(title: str, note: str = "") -> str:
    note_html = f'<span class="note">{escape(note)}</span>' if note else ""
    return f'<div class="section-head"><h2>{escape(title)}</h2>{note_html}</div>'


def matchup(game: dict) -> str:
    label = " · ".join(x for x in (fmt_date(game.get("date")), game.get("label")) if x)
    final_tag = '<span class="final-tag">Final</span>' if game.get("status") == "final" else ""
    return (
        f'<div class="matchup">'
        f'<div class="matchup-date">{escape(label)}{final_tag}</div>'
        f'{_team_row(game["away"], game["home"], game.get("status", ""))}'
        f'{_team_row(game["home"], game["away"], game.get("status", ""))}'
        f"</div>"
    )


def games_section(forecast: dict, status: str, title: str, note: str = "") -> str:
    games = [g for g in forecast.get("games", [])
             if (g.get("status") == "final") == (status == "final")]
    if not games:
        return ""
    cards = "".join(matchup(g) for g in games)
    return section_head(title, note) + f'<div style="margin-top:20px">{cards}</div>'


def standings_table(forecast: dict) -> str:
    rows = forecast.get("standings", [])
    if not rows:
        return ""
    labels = {"rating": "Rating", "change": "7-day", "record": "Record",
              "playoff_prob": "Make playoffs", "title_prob": "Win title"}
    labels.update(forecast.get("column_labels", {}))
    has_probs = any(r.get("playoff_prob") is not None or r.get("title_prob") is not None
                    for r in rows)

    body = []
    for r in sorted(rows, key=lambda r: r.get("rating") or 0, reverse=True):
        change = r.get("rating_change_7d")
        change_color = ("var(--accent-green)" if (change or 0) > 0
                        else "var(--accent-red)" if (change or 0) < 0 else "inherit")
        rating = r.get("rating")
        cells = [
            f'<td class="l">{team_cell(r, sub=r.get("sub"))}</td>',
            f'<td class="num">{round(rating) if rating is not None else "—"}</td>',
            f'<td class="num hide-sm" style="color:{change_color}">{fmt_signed(change)}</td>',
            f'<td class="hide-sm">{sparkline(r.get("history") or [])}</td>',
            f'<td class="num">{escape(r.get("record") or "—")}</td>',
        ]
        if has_probs:
            cells.append(_prob_td(r.get("playoff_prob")))
            cells.append(_prob_td(r.get("title_prob")))
        body.append("<tr>" + "".join(cells) + "</tr>")

    head = [
        '<th class="l">Team</th>',
        f'<th>{labels["rating"]}</th>',
        f'<th class="hide-sm">{labels["change"]}</th>',
        '<th class="hide-sm">Trend</th>',
        f'<th>{labels["record"]}</th>',
    ]
    if has_probs:
        head.append(f'<th>{labels["playoff_prob"]}</th>')
        head.append(f'<th>{labels["title_prob"]}</th>')

    return (
        section_head(forecast.get("standings_title", "Ratings & odds"),
                     f"{len(rows)} teams")
        + '<table class="fte"><thead><tr>'
        + "".join(head)
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def odds_table(rows: list[dict], columns: list[dict], title: str, note: str = "",
               sort_key: str | None = None) -> str:
    """Generic ratings/odds table. Column kinds: team, num, text, spark, prob."""
    if sort_key:
        rows = sorted(rows, key=lambda r: r.get(sort_key) or 0, reverse=True)

    head = []
    for c in columns:
        cls = ' class="l"' if c["kind"] == "team" else (
            ' class="hide-sm"' if c.get("hide_sm") else "")
        head.append(f'<th{cls}>{escape(c["label"])}</th>')

    body = []
    for r in rows:
        cells = []
        for c in columns:
            v = r.get(c["key"])
            if c["kind"] == "team":
                cells.append(f'<td class="l">{team_cell(r, sub=r.get("sub"))}</td>')
            elif c["kind"] == "prob":
                cells.append(_prob_td(v))
            elif c["kind"] == "spark":
                hide = " hide-sm" if c.get("hide_sm") else ""
                cells.append(f'<td class="{hide}">{sparkline(v or [])}</td>')
            elif c["kind"] == "num":
                hide = " hide-sm" if c.get("hide_sm") else ""
                cells.append(f'<td class="num{hide}">'
                             f'{round(v) if v is not None else "—"}</td>')
            elif c["kind"] == "dec":
                hide = " hide-sm" if c.get("hide_sm") else ""
                cells.append(f'<td class="num{hide}">'
                             f'{v:.1f}</td>' if v is not None else
                             f'<td class="num{hide}">—</td>')
            else:
                hide = " hide-sm" if c.get("hide_sm") else ""
                cells.append(f'<td class="num{hide}">{escape(str(v or "—"))}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")

    return (
        section_head(title, note)
        + '<table class="fte"><thead><tr>'
        + "".join(head)
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def fixtures_table(fixtures: list[dict], title: str, note: str = "") -> str:
    """Upcoming matches with win/draw/win probabilities."""
    if not fixtures:
        return ""
    body = []
    for m in fixtures:
        body.append(
            "<tr>"
            f'<td class="l num">{escape(fmt_date(m["date"]))}</td>'
            f'<td class="num hide-sm">{escape(m.get("group", ""))}</td>'
            f'<td class="l"><span class="team-name">{escape(m["home"])}</span>'
            f' <span class="team-sub">v</span> '
            f'<span class="team-name">{escape(m["away"])}</span></td>'
            f'{_prob_td(m["p_home"])}'
            f'{_prob_td(m["p_draw"])}'
            f'{_prob_td(m["p_away"])}'
            "</tr>"
        )
    home_label = escape(fixtures[0].get("home_label", "Home win"))
    return (
        section_head(title, note)
        + '<table class="fte"><thead><tr>'
        + '<th class="l">Date</th><th class="hide-sm">Group</th>'
        + f'<th class="l">Match</th><th>{home_label}</th><th>Draw</th><th>Away win</th>'
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def backtest_models_table(backtest: dict) -> str:
    """Model comparison: ours vs. benchmarks on the same games."""
    rows = []
    for i, m in enumerate(backtest["models"]):
        name = escape(m["model"])
        if i == 0:
            name = f"<strong>{name}</strong>"
        rows.append(
            "<tr>"
            f'<td class="l">{name}</td>'
            f'<td class="num">{m["n"]:,}</td>'
            f'<td class="num">{m["accuracy"]:.1%}</td>'
            f'<td class="num">{m["brier"]:.4f}</td>'
            f'<td class="num">{m["logloss"]:.4f}</td>'
            "</tr>"
        )
    return (
        section_head(f"Backtest since {backtest['since']}",
                     f"{backtest['n']:,} games, walk-forward")
        + '<table class="fte"><thead><tr>'
        + '<th class="l">Model</th><th>Games</th><th>Accuracy</th>'
        + '<th>Brier score</th><th>Log loss</th>'
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + '<p class="table-note">Accuracy is the share of games where the favorite won. '
        + "Brier score and log loss measure probability quality; lower is better. A coin "
        + "flip has a Brier score of 0.2500. The reference Elo is scored only on games "
        + "where it published a forecast.</p>"
    )


def calibration_table(backtest: dict) -> str:
    """Predicted vs. actual home win rate by forecast bucket."""
    rows = []
    for b in backtest["calibration"]:
        gap = b["actual"] - b["predicted"]
        rows.append(
            "<tr>"
            f'<td class="l num">{escape(b["range"])}</td>'
            f'<td class="num">{b["n"]:,}</td>'
            f'<td class="num">{b["predicted"]:.1%}</td>'
            f'<td class="num">{b["actual"]:.1%}</td>'
            f'<td class="num">{gap:+.1%}</td>'
            "</tr>"
        )
    return (
        section_head("Calibration", "Forecast vs. reality")
        + '<table class="fte"><thead><tr>'
        + '<th class="l">Home win forecast</th><th>Games</th>'
        + '<th>Avg. forecast</th><th>Actual win rate</th><th>Gap</th>'
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + '<p class="table-note">A well-calibrated model\'s forecasts match observed '
        + "frequencies: games it calls 70-30 should be won by the favorite about 70% of "
        + "the time.</p>"
    )


def decades_table(backtest: dict) -> str:
    if not backtest.get("decades"):
        return ""
    rows = []
    for d in backtest["decades"]:
        rows.append(
            "<tr>"
            f'<td class="l">{escape(d["decade"])}</td>'
            f'<td class="num">{d["n"]:,}</td>'
            f'<td class="num">{d["accuracy"]:.1%}</td>'
            f'<td class="num">{d["brier"]:.4f}</td>'
            "</tr>"
        )
    return (
        section_head("Era by era", "Predictability over time")
        + '<table class="fte"><thead><tr>'
        + '<th class="l">Decade</th><th>Games</th><th>Accuracy</th><th>Brier score</th>'
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _corr_cell(c) -> str:
    return f'<td class="num">{c:.3f}</td>' if c is not None else '<td class="num">—</td>'


def regression_backtest_table(backtest: dict) -> str:
    """Projection accuracy: ours vs. carry-forward baselines on the same seasons."""
    rows = []
    for i, m in enumerate(backtest["models"]):
        name = escape(m["model"])
        if i == 0:
            name = f"<strong>{name}</strong>"
        rows.append(
            "<tr>"
            f'<td class="l">{name}</td>'
            f'<td class="num">{m["n"]:,}</td>'
            f'<td class="num">{m["mae"]:.3f}</td>'
            f'<td class="num">{m["rmse"]:.3f}</td>'
            f'{_corr_cell(m["corr"])}'
            "</tr>"
        )
    return (
        section_head(f"Backtest since {backtest['since']}",
                     f"{backtest['n']:,} player-seasons, out-of-sample")
        + '<table class="fte"><thead><tr>'
        + '<th class="l">Projection</th><th>Seasons</th><th>MAE</th>'
        + '<th>RMSE</th><th>Correlation</th>'
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + '<p class="table-note">Each projection is scored against the player\'s actual '
        + "next-season RAPTOR. Mean absolute error and RMSE measure how far off the "
        + "forecasts are (lower is better); correlation rewards ranking players correctly "
        + "(higher is better). The baselines simply carry a player's prior-season rating "
        + "forward.</p>"
    )


def projection_tiers_table(backtest: dict) -> str:
    """Projected vs. actual RAPTOR by tier — a calibration analogue."""
    rows = []
    for t in backtest["tiers"]:
        gap = t["actual"] - t["predicted"]
        rows.append(
            "<tr>"
            f'<td class="l">{escape(t["tier"])}</td>'
            f'<td class="num">{t["n"]:,}</td>'
            f'<td class="num">{t["predicted"]:+.2f}</td>'
            f'<td class="num">{t["actual"]:+.2f}</td>'
            f'<td class="num">{gap:+.2f}</td>'
            "</tr>"
        )
    return (
        section_head("Calibration", "Projection vs. reality")
        + '<table class="fte"><thead><tr>'
        + '<th class="l">Projected tier</th><th>Seasons</th>'
        + '<th>Avg. projection</th><th>Actual RAPTOR</th><th>Gap</th>'
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + '<p class="table-note">A well-calibrated projection lands near the truth in '
        + "each tier: the players it pegs as stars should average star production, and "
        + "those it writes off should average replacement level.</p>"
    )


def regression_eras_table(backtest: dict) -> str:
    if not backtest.get("eras"):
        return ""
    rows = []
    for e in backtest["eras"]:
        rows.append(
            "<tr>"
            f'<td class="l">{escape(e["decade"])}</td>'
            f'<td class="num">{e["n"]:,}</td>'
            f'<td class="num">{e["mae"]:.3f}</td>'
            f'{_corr_cell(e["corr"])}'
            "</tr>"
        )
    return (
        section_head("Era by era", "Projectability over time")
        + '<table class="fte"><thead><tr>'
        + '<th class="l">Decade</th><th>Seasons</th><th>MAE</th><th>Correlation</th>'
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def methodology_box(forecast: dict) -> str:
    text = forecast.get("methodology")
    if not text:
        return ""
    return (
        f'<div class="methodology"><h3>How this works</h3>'
        f"<p>{escape(text)}</p></div>"
    )


def model_row(model: dict, updated: str | None = None, season: str | None = None) -> str:
    """One row of the home-page index, data.fivethirtyeight.com style."""
    href = model["href"].replace(".qmd", ".html")
    meta = " · ".join(x for x in (season, f"Updated {fmt_updated(updated)}" if updated else None) if x)
    return (
        f'<a class="index-row" href="{href}">'
        f'<div class="index-main">'
        f'<span class="league">{escape(model.get("league", ""))}</span>'
        f'<h3>{escape(model["name"])}</h3>'
        f'<p>{escape(model.get("description", ""))}</p>'
        f"</div>"
        f'<div class="index-side">'
        f'<span class="index-updated">{escape(meta)}</span>'
        f'<span class="btn-outline">See forecast</span>'
        f"</div>"
        f"</a>"
    )
