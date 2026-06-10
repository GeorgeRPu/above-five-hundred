/* Shared helpers for Above .500 pages. */

const A5H = (() => {
  const ACCENTS = ["#30a2da", "#ed713a", "#77ab43", "#8b62a8", "#d63b3b", "#e3ba22"];

  async function fetchJSON(path) {
    const res = await fetch(path, { cache: "no-store" });
    if (!res.ok) throw new Error(`Failed to load ${path} (${res.status})`);
    return res.json();
  }

  function fmtPct(p, decimals = 0) {
    if (p == null || Number.isNaN(p)) return "—";
    const pct = p * 100;
    if (pct > 99 && pct < 100) return ">99%";
    if (pct < 1 && pct > 0) return "<1%";
    return `${pct.toFixed(decimals)}%`;
  }

  function fmtSigned(n) {
    if (n == null) return "—";
    const r = Math.round(n);
    return r > 0 ? `+${r}` : `${r}`;
  }

  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, {
      weekday: "short", month: "short", day: "numeric",
    });
  }

  function fmtUpdated(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric", year: "numeric",
      hour: "numeric", minute: "2-digit",
    });
  }

  /* 538-style heat shading: white at 0% through saturated green at 100%. */
  function probShade(p) {
    if (p == null) return "transparent";
    const t = Math.max(0, Math.min(1, p));
    const light = 100 - t * 42;          // 100% (white) -> 58%
    return `hsl(88, 45%, ${light}%)`;
  }

  function probCell(p, decimals = 0) {
    const td = document.createElement("td");
    td.className = "prob";
    td.textContent = fmtPct(p, decimals);
    td.style.background = probShade(p);
    if (p != null && p > 0.75) td.style.color = "#fff";
    return td;
  }

  function accentFor(seed) {
    let h = 0;
    for (const c of String(seed)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return ACCENTS[h % ACCENTS.length];
  }

  function teamCell(team, { sub } = {}) {
    const div = document.createElement("div");
    div.className = "team-cell";
    const dot = document.createElement("span");
    dot.className = "team-dot";
    dot.style.background = team.color || accentFor(team.abbr || team.name);
    dot.textContent = team.abbr || (team.name || "?").slice(0, 3).toUpperCase();
    const label = document.createElement("span");
    const name = document.createElement("span");
    name.className = "team-name";
    name.textContent = team.name || team.abbr;
    label.appendChild(name);
    if (sub) {
      const s = document.createElement("span");
      s.className = "team-sub";
      s.textContent = ` ${sub}`;
      label.appendChild(s);
    }
    div.append(dot, label);
    return div;
  }

  /* Inline SVG sparkline of a numeric series; highlights the latest point. */
  function sparkline(series, { width = 110, height = 28 } = {}) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "spark");
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    if (!series || series.length < 2) return svg;

    const min = Math.min(...series);
    const max = Math.max(...series);
    const span = max - min || 1;
    const pad = 3;
    const pts = series.map((v, i) => {
      const x = pad + (i / (series.length - 1)) * (width - 2 * pad);
      const y = height - pad - ((v - min) / span) * (height - 2 * pad);
      return [x, y];
    });

    const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    line.setAttribute("points", pts.map((p) => p.map((n) => n.toFixed(1)).join(",")).join(" "));
    svg.appendChild(line);

    const [lx, ly] = pts[pts.length - 1];
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("class", "spark-dot");
    dot.setAttribute("cx", lx.toFixed(1));
    dot.setAttribute("cy", ly.toFixed(1));
    dot.setAttribute("r", 2.5);
    svg.appendChild(dot);
    return svg;
  }

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    }
    for (const child of children) {
      if (child == null) continue;
      node.append(child);
    }
    return node;
  }

  return { fetchJSON, fmtPct, fmtSigned, fmtDate, fmtUpdated, probShade, probCell, accentFor, teamCell, sparkline, el };
})();
