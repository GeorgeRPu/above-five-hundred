/* Home page: render the model registry as a card grid. */

(async () => {
  const grid = document.getElementById("model-grid");
  const { el, accentFor, fmtUpdated } = A5H;

  let registry;
  try {
    registry = await A5H.fetchJSON("data/models.json");
  } catch (err) {
    grid.replaceChildren(el("div", { class: "error-state", text: `Could not load model registry: ${err.message}` }));
    return;
  }

  const models = registry.models || [];
  if (models.length === 0) {
    grid.replaceChildren(el("div", { class: "empty-state", text: "No models published yet. Add one to site/data/models.json." }));
    return;
  }

  grid.replaceChildren(
    ...models.map((m) => {
      const card = el("a", { class: "model-card", href: `forecast.html?model=${encodeURIComponent(m.slug)}` });
      card.style.setProperty("--card-accent", m.color || accentFor(m.league || m.slug));
      card.append(
        el("span", { class: "league", text: m.league || "" }),
        el("h3", { text: m.name }),
        el("p", { text: m.description || "" }),
        el(
          "div",
          { class: "meta" },
          el("span", { text: m.season || "" }),
          el("span", { text: m.updated ? `Updated ${fmtUpdated(m.updated)}` : "" })
        )
      );
      return card;
    })
  );
})();
