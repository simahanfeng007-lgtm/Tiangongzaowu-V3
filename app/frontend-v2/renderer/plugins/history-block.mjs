export const historyBlockPlugin = {
  id: "history-block",
  slot: "context",
  order: 140,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section class="side-section recent-section default-context-section">
          <div class="section-heading">
            <span>最近会话</span>
          </div>
          <div id="historyList" class="history-list"></div>
        </section>
      `
    );

    const list = slot.querySelector("#historyList");
    const section = list.closest(".side-section");

    function formatTime(ts) {
      if (!ts) return "";
      try {
        return new Date(ts).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
      } catch {
        return "";
      }
    }

    function render(sessions) {
      const snapshot = state.snapshot();
      const items = Array.isArray(sessions) ? sessions : snapshot.sessions;
      list.innerHTML = "";

      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "history-empty";
        empty.textContent = "暂无会话";
        list.appendChild(empty);
        return;
      }

      for (const item of items) {
        const row = document.createElement("div");
        row.className = `history-item-row${item.active ? " active" : ""}`;

        const node = document.createElement("button");
        node.className = "history-item";
        node.type = "button";
        node.title = item.title || "新对话";
        node.innerHTML = `
          <span class="history-title"></span>
          <span class="history-meta"></span>
        `;
        node.querySelector(".history-title").textContent = item.title || "新对话";
        node.querySelector(".history-meta").textContent = [
          formatTime(item.updatedAt),
          item.count ? `${item.count} 条` : "空会话"
        ].filter(Boolean).join(" · ");
        node.addEventListener("click", () => actions.switchConversation(item.id));

        const remove = document.createElement("button");
        remove.className = "history-delete";
        remove.type = "button";
        remove.title = `删除会话：${item.title || "新对话"}`;
        remove.setAttribute("aria-label", "删除会话");
        remove.disabled = Boolean(snapshot.busy && item.active);
        remove.innerHTML = `
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M3 6h18"></path>
            <path d="M8 6V4h8v2"></path>
            <path d="M19 6l-1 14H6L5 6"></path>
            <path d="M10 11v5"></path>
            <path d="M14 11v5"></path>
          </svg>
        `;
        remove.addEventListener("click", (event) => {
          event.stopPropagation();
          if (!remove.disabled) actions.deleteConversation(item.id);
        });

        row.append(node, remove);
        list.appendChild(row);
      }
    }

    state.on("sessions", render);
    state.on("page", (page) => {
      section.hidden = page !== "chat";
    });
    state.on("busy", () => render());
    section.hidden = state.snapshot().activePage !== "chat";
    render(state.snapshot().sessions);
  },
};
