export const brandBlockPlugin = {
  id: "brand-block",
  slot: "context",
  order: 100,
  mount({ slot, state }) {
    slot.insertAdjacentHTML("beforeend", `
      <header class="brand-block">
        <div class="brand-text">
          <h1>天工造物</h1>
          <p id="brandPersona">起源 · 生命后台</p>
        </div>
      </header>
    `);

    const persona = slot.querySelector("#brandPersona");
    function render(settings) {
      const name = String(settings?.personaName || "起源").trim() || "起源";
      persona.textContent = `${name} · 生命后台`;
    }

    state.on("settings", render);
    render(state.snapshot().settings);
  }
};
