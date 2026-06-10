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

    body = []
    for r in sorted(rows, key=lambda r: r.get("rating") or 0, reverse=True):
        change = r.get("rating_change_7d")
        change_color = ("var(--accent-green)" if (change or 0) > 0
                        else "var(--accent-red)" if (change or 0) < 0 else "inherit")
        rating = r.get("rating")
        body.append(
            "<tr>"
            f'<td class="l">{team_cell(r, sub=r.get("sub"))}</td>'
            f'<td class="num">{round(rating) if rating is not None else "—"}</td>'
            f'<td class="num hide-sm" style="color:{change_color}">{fmt_signed(change)}</td>'
            f'<td class="hide-sm">{sparkline(r.get("history") or [])}</td>'
            f'<td class="num">{escape(r.get("record") or "—")}</td>'
            f'{_prob_td(r.get("playoff_prob"))}'
            f'{_prob_td(r.get("title_prob"))}'
            "</tr>"
        )

    return (
        section_head("Ratings & odds", f"{len(rows)} teams")
        + '<table class="fte"><thead><tr>'
        + '<th class="l">Team</th>'
        + f'<th>{labels["rating"]}</th>'
        + f'<th class="hide-sm">{labels["change"]}</th>'
        + '<th class="hide-sm">Trend</th>'
        + f'<th>{labels["record"]}</th>'
        + f'<th>{labels["playoff_prob"]}</th>'
        + f'<th>{labels["title_prob"]}</th>'
        + "</tr></thead><tbody>"
        + "".join(body)
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


def model_card(model: dict, updated: str | None = None) -> str:
    accent = model.get("color") or accent_for(model.get("league") or model["slug"])
    href = model["href"].replace(".qmd", ".html")
    meta_right = f"Updated {fmt_updated(updated)}" if updated else ""
    return (
        f'<a class="model-card" href="{href}" style="--card-accent:{accent}">'
        f'<span class="league">{escape(model.get("league", ""))}</span>'
        f'<h3>{escape(model["name"])}</h3>'
        f'<p>{escape(model.get("description", ""))}</p>'
        f'<div class="meta"><span>{escape(model.get("season", ""))}</span>'
        f"<span>{meta_right}</span></div>"
        f"</a>"
    )
