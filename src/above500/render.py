"""HTML renderers for forecast payloads, matching the site's 538-style CSS.

Quarto pages call these from Python cells and emit the result with
`output: asis`. Everything returns plain HTML strings.
"""

from __future__ import annotations

import json
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
    logo = team.get("logo")
    sub_html = f' <span class="team-sub">{escape(str(sub))}</span>' if sub else ""
    if logo:
        marker = f'<img class="team-logo" src="{escape(logo)}" alt="{escape(abbr)}">'
    else:
        marker = f'<span class="team-sym" style="color:{color}">{escape(abbr)}</span>'
    return (
        f'<div class="team-cell">'
        f'{marker}'
        f'<span><span class="team-name">{name}</span>{sub_html}</span>'
        f"</div>"
    )


def sparkline(series, width: int = 140, height: int = 28) -> str:
    if not series or len(series) < 2:
        return ""
    lo, hi = min(series), max(series)
    span = (hi - lo) or 1.0
    pad = 3
    label_w = 30
    chart_w = width - label_w
    pts = []
    for i, v in enumerate(series):
        x = pad + (i / (len(series) - 1)) * (chart_w - 2 * pad)
        y = height - pad - ((v - lo) / span) * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    lx, ly = pts[-1].split(",")
    hi_r, lo_r = round(hi), round(lo)
    lx_label = chart_w + 4
    if hi_r != lo_r:
        labels = (
            f'<text class="spark-label" x="{lx_label}" y="{pad + 7}">{hi_r}</text>'
            f'<text class="spark-label" x="{lx_label}" y="{height - pad + 1}">{lo_r}</text>'
        )
    else:
        labels = f'<text class="spark-label" x="{lx_label}" y="{height / 2 + 3:.0f}">{hi_r}</text>'
    return (
        f'<svg class="spark" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<polyline points="{" ".join(pts)}"></polyline>'
        f'<circle class="spark-dot" cx="{lx}" cy="{ly}" r="2.5"></circle>'
        f'{labels}'
        f"</svg>"
    )


def _prob_td(p, hide_sm: bool = False) -> str:
    color = ";color:#fff" if (p is not None and p > 0.75) else ""
    cls = "prob hide-sm" if hide_sm else "prob"
    return f'<td class="{cls}" style="background:{prob_shade(p)}{color}">{fmt_pct(p)}</td>'


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
              "trend": "Trend",
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
        f'<th class="hide-sm">{labels["trend"]}</th>',
        f'<th>{labels["record"]}</th>',
    ]
    if has_probs:
        head.append(f'<th>{labels["playoff_prob"]}</th>')
        head.append(f'<th>{labels["title_prob"]}</th>')

    return (
        section_head(forecast.get("standings_title", "Ratings & odds"),
                     f"{len(rows)} teams")
        + '<div class="fte-wrap"><table class="fte"><thead><tr>'
        + "".join(head)
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _odds_table_html(rows: list[dict], columns: list[dict],
                     sort_key: str | None = None) -> str:
    """Render a <table class="fte"> element for odds-style data (no header)."""
    if sort_key:
        rows = sorted(rows, key=lambda r: r.get(sort_key) or 0, reverse=True)

    head = []
    for c in columns:
        classes = []
        if c["kind"] == "team":
            classes.append("l")
        elif c["kind"] == "prob":
            classes.append("prob-h")   # narrow column, header wraps
        if c["kind"] != "team" and c.get("hide_sm"):
            classes.append("hide-sm")
        cls = f' class="{" ".join(classes)}"' if classes else ""
        head.append(f'<th{cls}>{escape(c["label"])}</th>')

    body = []
    for r in rows:
        cells = []
        for c in columns:
            v = r.get(c["key"])
            if c["kind"] == "team":
                cells.append(f'<td class="l">{team_cell(r, sub=r.get("sub"))}</td>')
            elif c["kind"] == "prob":
                cells.append(_prob_td(v, hide_sm=c.get("hide_sm", False)))
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
        '<div class="fte-wrap"><table class="fte"><thead><tr>'
        + "".join(head)
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def odds_table(rows: list[dict], columns: list[dict], title: str, note: str = "",
               sort_key: str | None = None) -> str:
    """Generic ratings/odds table. Column kinds: team, num, text, spark, prob."""
    return section_head(title, note) + _odds_table_html(rows, columns, sort_key)


def season_picker_table(leaderboards: dict, seasons: list[int],
                        default_season: int, columns: list[dict],
                        title: str, note: str = "", *,
                        widget_id: str = "raptor",
                        show_type_toggle: bool = True,
                        default_sort_key: str = "war",
                        note_prefix: str = "Box-RAPTOR estimate") -> str:
    """Leaderboard table with a season dropdown and optional RS/playoff toggle.

    The data for every season is emitted once as a JSON island and the visible
    table is built client-side on demand.
    """

    def _label(s):
        return f"{s - 1}-{str(s)[2:]}"

    options = []
    for s in seasons:
        selected = " selected" if s == default_season else ""
        has_po = "true" if leaderboards.get(s, {}).get("po") else "false"
        options.append(
            f'<option value="{s}" data-has-po="{has_po}"{selected}>'
            f'{escape(_label(s))}</option>')

    wid = widget_id
    header = (
        f'<div class="section-head">'
        f'<h2>{escape(title)}</h2>'
        f'<span class="note" id="{wid}-note">{escape(note)}</span>'
        f'</div>')

    toggle_html = ""
    if show_type_toggle:
        toggle_html = (
            f'<div class="type-toggle" id="{wid}-type-toggle">'
            f'<button class="toggle-btn active" data-type="rs">Regular Season</button>'
            f'<button class="toggle-btn" data-type="po">Playoffs</button>'
            f'</div>')

    picker = (
        f'<div class="season-controls">'
        f'<select class="season-select" id="{wid}-season">'
        f'{"".join(options)}</select>'
        f'{toggle_html}</div>')

    payload = {
        "accents": ACCENTS,
        "columns": [{"key": c["key"], "kind": c["kind"], "label": c["label"],
                     "hide_sm": bool(c.get("hide_sm"))} for c in columns],
        "data": {str(s): {t: leaderboards.get(s, {}).get(t, [])
                          for t in ("rs", "po")} for s in seasons},
        "showTypeToggle": show_type_toggle,
        "defaultSortKey": default_sort_key,
        "notePrefix": note_prefix,
    }
    data_json = json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")
    data = f'<script type="application/json" id="{wid}-data">{data_json}</script>'
    container = f'<div id="{wid}-table" class="fte-wrap"></div>'

    return header + picker + data + container + _season_picker_js(wid)


def _season_picker_js(wid: str) -> str:
    """Client-side renderer for season_picker_table, parameterised by widget id."""
    return f"""\
<script>
(function(){{
var cfg=JSON.parse(document.getElementById("{wid}-data").textContent),
    cols=cfg.columns,accents=cfg.accents,
    sel=document.getElementById("{wid}-season"),
    toggleEl=document.getElementById("{wid}-type-toggle"),
    note=document.getElementById("{wid}-note"),
    out=document.getElementById("{wid}-table"),
    btns=toggleEl?toggleEl.querySelectorAll(".toggle-btn"):[],
    cur="rs",sortKey=cfg.defaultSortKey||"war",sortDir=-1;
function esc(s){{return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");}}
function accent(seed){{var h=0,s=String(seed);
    for(var i=0;i<s.length;i++){{h=(Math.imul(h,31)+s.charCodeAt(i))>>>0;}}
    return accents[h%accents.length];}}
function teamCell(r){{
    var abbr=r.abbr||(r.name||"?").slice(0,3).toUpperCase(),name=esc(r.name||abbr),mk;
    if(r.logo){{mk='<img class="team-logo" src="'+esc(r.logo)+'" alt="'+esc(abbr)+'">';}}
    else{{mk='<span class="team-sym" style="color:'+accent(abbr)+'">'+esc(abbr)+'</span>';}}
    return '<div class="team-cell">'+mk+'<span><span class="team-name">'+name+
        '</span></span></div>';}}
function spark(series,idx){{
    if(!series||series.length<2)return"";
    var w=140,h=28,lo=Math.min.apply(null,series),hi=Math.max.apply(null,series),
        span=(hi-lo)||1,pad=3,chartW=w-30,pts=[];
    for(var i=0;i<series.length;i++){{
        var x=pad+(i/(series.length-1))*(chartW-2*pad),
            y=h-pad-((series[i]-lo)/span)*(h-2*pad);
        pts.push(x.toFixed(1)+","+y.toFixed(1));}}
    if(idx==null||idx<0||idx>=series.length)idx=series.length-1;
    var dot=pts[idx].split(","),hiR=Math.round(hi),loR=Math.round(lo),
        lxL=chartW+4,labels;
    if(hiR!==loR){{labels='<text class="spark-label" x="'+lxL+'" y="'+(pad+7)+'">'+hiR+
        '</text><text class="spark-label" x="'+lxL+'" y="'+(h-pad+1)+'">'+loR+'</text>';}}
    else{{labels='<text class="spark-label" x="'+lxL+'" y="'+(h/2+3).toFixed(0)+'">'+
        hiR+'</text>';}}
    return '<svg class="spark" width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+
        '"><polyline points="'+pts.join(" ")+'"></polyline><circle class="spark-dot" cx="'+
        dot[0]+'" cy="'+dot[1]+'" r="2.5"></circle>'+labels+'</svg>';}}
function cell(r,c){{var v=r[c.key],hide=c.hide_sm?" hide-sm":"";
    if(c.kind==="team")return '<td class="l">'+teamCell(r)+'</td>';
    if(c.kind==="spark")return '<td class="'+(c.hide_sm?"hide-sm":"")+'">'+
        spark(v||[],r.history_idx)+'</td>';
    if(c.kind==="num")return '<td class="num'+hide+'">'+(v!=null?Math.round(v):"\\u2014")+
        '</td>';
    if(c.kind==="dec")return '<td class="num'+hide+'">'+(v!=null?v.toFixed(1):"\\u2014")+
        '</td>';
    if(c.kind==="signed"){{var txt=v!=null?(v>0?"+"+Math.round(v):String(Math.round(v))):"\\u2014",
        clr=v>0?"var(--accent-green)":v<0?"var(--accent-red)":"inherit";
        return '<td class="num'+hide+'" style="color:'+clr+'">'+txt+'</td>';}}
    return '<td class="num'+hide+'">'+esc(v!=null?v:"\\u2014")+'</td>';}}
function header(){{
    return cols.map(function(c){{
        var sortable=c.kind!=="spark",classes=[];
        if(c.kind==="team")classes.push("l");
        if(c.hide_sm)classes.push("hide-sm");
        if(sortable)classes.push("sortable");
        var active=sortable&&c.key===sortKey,
            arrow=active?(sortDir<0?" \\u25be":" \\u25b4"):"",
            attrs=(classes.length?' class="'+classes.join(" ")+'"':"")+
                (sortable?' data-sort="'+c.key+'"':"");
        return '<th'+attrs+'>'+esc(c.label)+arrow+'</th>';}}).join("");}}
function sortRows(rows){{
    var col=cols.filter(function(c){{return c.key===sortKey;}})[0],arr=rows.slice();
    arr.sort(function(a,b){{
        if(col&&col.kind==="team"){{
            var x=(a.name||"").toLowerCase(),y=(b.name||"").toLowerCase();
            return (x<y?-1:x>y?1:0)*sortDir;}}
        var u=a[sortKey],v=b[sortKey];
        if(col&&col.kind==="text"){{
            var x=String(u||""),y=String(v||""),
                nx=parseFloat(x),ny=parseFloat(y);
            if(!isNaN(nx)&&!isNaN(ny))return (nx-ny)*sortDir;
            return (x<y?-1:x>y?1:0)*sortDir;}}
        u=u==null?-Infinity:u;v=v==null?-Infinity:v;
        return (u-v)*sortDir;}});
    return arr;}}
function table(rows){{
    var body=rows.map(function(r){{
        return '<tr>'+cols.map(function(c){{return cell(r,c);}}).join("")+'</tr>';}}).join("");
    return '<'+'table class="fte"><thead><tr>'+header()+'</tr></thead><tbody>'+body+
        '</tbody></'+'table>';}}
function render(){{
    var s=sel.value,rows;
    if(cfg.showTypeToggle){{
        var po=sel.selectedOptions[0].dataset.hasPo==="true";
        btns[1].style.display=po?"":"none";
        if(!po&&cur==="po"){{cur="rs";btns[0].classList.add("active");
            btns[1].classList.remove("active");}}
        rows=(cfg.data[s]&&cfg.data[s][cur])||[];
        var lbl=cur==="po"?"playoffs":"regular season";
        note.textContent=cfg.notePrefix+" \\u00b7 top "+rows.length+" by WAR \\u00b7 "+lbl;
    }}else{{
        rows=(cfg.data[s]&&cfg.data[s]["rs"])||[];
        note.textContent=cfg.notePrefix+" \\u00b7 "+rows.length+" teams";
    }}
    out.innerHTML=table(sortRows(rows));}}
out.addEventListener("click",function(ev){{
    var th=ev.target.closest&&ev.target.closest("th[data-sort]");
    if(!th)return;
    var k=th.dataset.sort;
    if(sortKey===k){{sortDir=-sortDir;}}else{{sortKey=k;sortDir=k==="name"?1:-1;}}
    render();}});
sel.addEventListener("change",render);
btns.forEach(function(b){{b.addEventListener("click",function(){{
    cur=b.dataset.type;btns.forEach(function(x){{x.classList.remove("active");}});
    b.classList.add("active");render();}});}});
render();
}})();
</script>"""


def _flag_img(src: str | None, abbr: str) -> str:
    if src:
        return f'<img class="team-flag" src="{escape(src)}" alt="{escape(abbr)}">'
    return ""


def fixtures_table(fixtures: list[dict], title: str,
                   note: str | None = None) -> str:
    """Upcoming matches with win/draw/win probabilities.

    Fixtures with ``p_draw`` set to None (knockout matches, where a draw is
    impossible) omit the draw column when no listed fixture can end level.
    """
    if not fixtures:
        return ""
    has_draw = any(m.get("p_draw") is not None for m in fixtures)
    if note is None:
        note = ("Win/draw/win probabilities" if has_draw
                else "Chance to advance — knockout matches cannot end in a draw")
    body = []
    for m in fixtures:
        h_flag = _flag_img(m.get("home_logo"), m.get("home_abbr", ""))
        a_flag = _flag_img(m.get("away_logo"), m.get("away_abbr", ""))
        body.append(
            "<tr>"
            f'<td class="l num">{escape(fmt_date(m["date"]))}</td>'
            f'<td class="num hide-sm">{escape(m.get("group", ""))}</td>'
            f'<td class="l">{h_flag}<span class="team-name">{escape(m["home"])}</span>'
            f' <span class="team-sub">v</span> '
            f'{a_flag}<span class="team-name">{escape(m["away"])}</span></td>'
            f'{_prob_td(m["p_home"])}'
            + (_prob_td(m.get("p_draw")) if has_draw else "")
            + f'{_prob_td(m["p_away"])}'
            "</tr>"
        )
    home_label = escape(fixtures[0].get("home_label", "Home win"))
    stage_label = escape(fixtures[0].get("stage_label", "Group"))
    return (
        section_head(title, note)
        + '<div class="fte-wrap"><table class="fte"><thead><tr>'
        + f'<th class="l">Date</th><th class="hide-sm">{stage_label}</th>'
        + f'<th class="l">Match</th><th>{home_label}</th>'
        + ('<th>Draw</th>' if has_draw else '')
        + '<th>Away win</th>'
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
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
        + '<div class="fte-wrap"><table class="fte"><thead><tr>'
        + '<th class="l">Model</th><th>Games</th><th>Accuracy</th>'
        + '<th>Brier score</th><th>Log loss</th>'
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        + '<p class="table-note">Accuracy is the share of games where the favorite won. '
        + "Brier score and log loss measure probability quality; lower is better. A coin "
        + "flip has a Brier score of 0.2500. The reference Elo is scored only on games "
        + "where it published a forecast.</p>"
    )


def wc_backtest_table(wc_backtest: dict) -> str:
    """Match-only vs. the two roster priors on historical World Cup matches."""
    if not wc_backtest:
        return ""

    specs = [
        ("match_only_total", "Match-only SPI"),
        ("ea_total", "+ EA-FC roster blend"),
        ("club_total", "+ club-SPI roster blend"),
    ]
    rows = []
    for key, label in specs:
        m = wc_backtest.get(key)
        if not m:
            continue
        rows.append(
            "<tr>"
            f'<td class="l"><strong>{escape(label)}</strong></td>'
            f'<td class="num">{m["n"]:,}</td>'
            f'<td class="num">{m["accuracy"]:.1%}</td>'
            f'<td class="num">{m["brier"]:.4f}</td>'
            f'<td class="num">{m["logloss"]:.4f}</td>'
            "</tr>"
        )
    if not rows:
        return ""

    years = [e["year"] for e in wc_backtest.get("per_wc", [])]
    note = f"World Cup matches only ({', '.join(str(y) for y in years)})"

    return (
        section_head("World Cup roster-blend backtest", note)
        + '<div class="fte-wrap"><table class="fte"><thead><tr>'
        + '<th class="l">Model</th><th>Games</th><th>Accuracy</th>'
        + '<th>Brier score</th><th>Log loss</th>'
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        + '<p class="table-note">Ratings update walk-forward through each '
        + "tournament (as the nightly production re-fit does); the roster prior "
        + "is fixed at opening day and shifts ratings 25% toward it. The model "
        + "uses club-match SPI — 538’s own method: real World Cup rosters, each "
        + "player rated by his club’s offensive/defensive SPI weighted by "
        + "minutes played. The EA-FC video-game prior is shown for comparison "
        + "and currently scores better — open club data links continents too "
        + "thinly to calibrate club form across them. For reference, "
        + "FiveThirtyEight’s own published forecasts scored 0.577 (2018) and "
        + "0.638 (2022) Brier on these matches. Lower Brier/log loss is "
        + "better.</p>"
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
        + '<div class="fte-wrap"><table class="fte"><thead><tr>'
        + '<th class="l">Home win forecast</th><th>Games</th>'
        + '<th>Avg. forecast</th><th>Actual win rate</th><th>Gap</th>'
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
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
        + '<div class="fte-wrap"><table class="fte"><thead><tr>'
        + '<th class="l">Decade</th><th>Games</th><th>Accuracy</th><th>Brier score</th>'
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
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
        + '<div class="fte-wrap"><table class="fte"><thead><tr>'
        + '<th class="l">Projection</th><th>Seasons</th><th>MAE</th>'
        + '<th>RMSE</th><th>Correlation</th>'
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        + '<p class="table-note">Each projection is scored against the player\'s actual '
        + "next-season Box-RAPTOR. Mean absolute error and RMSE measure how far off the "
        + "forecasts are (lower is better); correlation rewards ranking players correctly "
        + "(higher is better). The baselines simply carry a player's prior-season rating "
        + "forward.</p>"
    )


def fidelity_table(fidelity: dict) -> str:
    """How well the Box-RAPTOR reconstruction reproduces 538's real RAPTOR."""
    if not fidelity:
        return ""
    corr = f'{fidelity["corr"]:.3f}' if fidelity.get("corr") is not None else "—"
    return (
        section_head("Box-RAPTOR fidelity",
                     f"held-out 538 seasons since {fidelity['since']}")
        + '<div class="fte-wrap"><table class="fte"><thead><tr>'
        + '<th class="l">Estimator</th><th>Seasons</th><th>MAE</th>'
        + '<th>R²</th><th>Correlation</th>'
        + "</tr></thead><tbody>"
        + "<tr>"
        + '<td class="l"><strong>Box-RAPTOR estimate</strong></td>'
        + f'<td class="num">{fidelity["n"]:,}</td>'
        + f'<td class="num">{fidelity["mae"]:.3f}</td>'
        + f'<td class="num">{fidelity["r2"]:.3f}</td>'
        + f'<td class="num">{corr}</td>'
        + "</tr>"
        + "<tr>"
        + '<td class="l">League average (flat)</td>'
        + f'<td class="num">{fidelity["n"]:,}</td>'
        + f'<td class="num">{fidelity["baseline_mae"]:.3f}</td>'
        + '<td class="num">0.000</td><td class="num">—</td>'
        + "</tr>"
        + "</tbody></table></div>"
        + '<p class="table-note">Box scores reproduce a player\'s RAPTOR to within '
        + f'{fidelity["mae"]:.2f} points on seasons the estimator never trained on. The '
        + "rest is RAPTOR's on/off component, which play-by-play sees and box scores "
        + "can't — so estimates are accurate in the middle and conservative for the "
        + "superstars and the replacement-level fringe.</p>"
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
        + '<div class="fte-wrap"><table class="fte"><thead><tr>'
        + '<th class="l">Projected tier</th><th>Seasons</th>'
        + '<th>Avg. projection</th><th>Actual RAPTOR</th><th>Gap</th>'
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
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
        + '<div class="fte-wrap"><table class="fte"><thead><tr>'
        + '<th class="l">Decade</th><th>Seasons</th><th>MAE</th><th>Correlation</th>'
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
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
