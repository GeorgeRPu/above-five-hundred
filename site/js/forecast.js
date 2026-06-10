/* Forecast page: renders one model's latest.json (chosen via ?model=<slug>). */

(async () => {
  const { el, fmtPct, fmtSigned, fmtDate, fmtUpdated, probCell, teamCell, sparkline } = A5H;
  const main = document.getElementById("forecast-main");

  const slug = new URLSearchParams(location.search).get("model");
  if (!slug) {
    main.replaceChildren(el("div", { class: "error-state", text: "No model specified. Pick one from the home page." }));
    return;
  }

  let data;
  try {
    data = await A5H.fetchJSON(`data/${slug}/latest.json`);
  } catch (err) {
    main.replaceChildren(el("div", { class: "error-state", text: `Could not load forecast for “${slug}”: ${err.message}` }));
    return;
  }

  document.title = `${data.name} | Above .500`;
  main.replaceChildren();

  /* ---- header ---- */
  const header = el(
    "header",
    {},
    el("span", { class: "kicker", text: data.league || "Forecast" }),
    el("h1", { class: "headline", text: data.name }),
    data.description ? el("p", { class: "dek", text: data.description }) : null,
    el(
      "p",
      { class: "byline" },
      data.season ? el("span", {}, el("strong", { text: data.season }), " · ") : null,
      el("span", { class: "updated-stamp", text: `Updated ${fmtUpdated(data.updated)}` })
    )
  );
  main.appendChild(header);

  /* ---- upcoming games ---- */
  const upcoming = (data.games || []).filter((g) => g.status !== "final");
  if (upcoming.length) {
    main.appendChild(sectionHead("Upcoming games", "Win probabilities"));
    const list = el("div", { style: "margin-top:20px" });
    for (const game of upcoming) list.appendChild(renderMatchup(game));
    main.appendChild(list);
  }

  /* ---- recent results ---- */
  const finals = (data.games || []).filter((g) => g.status === "final");
  if (finals.length) {
    main.appendChild(sectionHead("Recent results", "Model probability vs. outcome"));
    const list = el("div", { style: "margin-top:20px" });
    for (const game of finals) list.appendChild(renderMatchup(game));
    main.appendChild(list);
  }

  /* ---- standings / ratings table ---- */
  const standings = data.standings || [];
  if (standings.length) {
    const labels = Object.assign(
      { rating: "Rating", change: "7-day", record: "Record", playoff_prob: "Make playoffs", title_prob: "Win title" },
      data.column_labels || {}
    );
    main.appendChild(sectionHead("Ratings & odds", `${standings.length} teams`));

    const table = el("table", { class: "fte" });
    table.appendChild(
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          el("th", { class: "l", text: "Team" }),
          el("th", { text: labels.rating }),
          el("th", { class: "hide-sm", text: labels.change }),
          el("th", { class: "hide-sm", text: "Trend" }),
          el("th", { text: labels.record }),
          el("th", { text: labels.playoff_prob }),
          el("th", { text: labels.title_prob })
        )
      )
    );

    const tbody = el("tbody");
    const sorted = [...standings].sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0));
    for (const row of sorted) {
      const tr = el("tr");

      const teamTd = el("td", { class: "l" });
      teamTd.appendChild(teamCell(row, { sub: row.sub }));
      tr.appendChild(teamTd);

      tr.appendChild(el("td", { class: "num", text: row.rating != null ? Math.round(row.rating) : "—" }));

      const changeTd = el("td", { class: "num hide-sm", text: fmtSigned(row.rating_change_7d) });
      if (row.rating_change_7d > 0) changeTd.style.color = "var(--accent-green)";
      if (row.rating_change_7d < 0) changeTd.style.color = "var(--accent-red)";
      tr.appendChild(changeTd);

      const sparkTd = el("td", { class: "hide-sm" });
      if (row.history && row.history.length > 1) sparkTd.appendChild(sparkline(row.history));
      tr.appendChild(sparkTd);

      tr.appendChild(el("td", { class: "num", text: row.record || "—" }));
      tr.appendChild(probCell(row.playoff_prob));
      tr.appendChild(probCell(row.title_prob));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    main.appendChild(table);
  }

  /* ---- methodology ---- */
  if (data.methodology) {
    const box = el("div", { class: "methodology" });
    box.appendChild(el("h3", { text: "How this works" }));
    box.appendChild(el("p", { text: data.methodology }));
    main.appendChild(box);
  }

  /* ---- helpers ---- */

  function sectionHead(title, note) {
    return el(
      "div",
      { class: "section-head" },
      el("h2", { text: title }),
      note ? el("span", { class: "note", text: note }) : null
    );
  }

  function renderMatchup(game) {
    const box = el("div", { class: "matchup" });
    const dateLabel = [fmtDate(game.date), game.label].filter(Boolean).join(" · ");
    const head = el("div", { class: "matchup-date", text: dateLabel });
    if (game.status === "final") head.appendChild(el("span", { class: "final-tag", text: "Final" }));
    box.appendChild(head);
    box.appendChild(teamRow(game.away, game.home, game.status));
    box.appendChild(teamRow(game.home, game.away, game.status));
    return box;
  }

  function teamRow(team, opponent, status) {
    const isFinal = status === "final";
    const winning = isFinal
      ? (team.score ?? 0) > (opponent.score ?? 0)
      : (team.win_prob ?? 0) >= (opponent.win_prob ?? 0);

    const row = el("div", { class: `matchup-row ${winning ? "winner" : "loser"}` });
    row.appendChild(teamCell(team, { sub: team.rating != null ? `${Math.round(team.rating)}` : undefined }));

    if (isFinal) {
      row.appendChild(el("span", { class: "pct", text: fmtPct(team.win_prob) }));
      row.appendChild(el("span", { class: "score", text: team.score != null ? String(team.score) : "—" }));
    } else {
      row.appendChild(el("span", { class: "pct", text: fmtPct(team.win_prob) }));
      const track = el("div", { class: "bar-track" });
      const fill = el("div", { class: "bar-fill" });
      fill.style.width = `${Math.round((team.win_prob ?? 0) * 100)}%`;
      if (!winning) fill.style.background = "var(--ink-faint)";
      track.appendChild(fill);
      row.appendChild(track);
    }
    return row;
  }
})();
