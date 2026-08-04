const ICONS = {
  chat: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
  execute: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>`,
  knowledge: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5z"/><path d="M8 7h8"/><path d="M8 11h6"/></svg>`,
  skills: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.1-3.1a6 6 0 0 1-7.9 7.9l-5.6 5.6a2.1 2.1 0 0 1-3-3l5.6-5.6a6 6 0 0 1 7.9-7.9z"/></svg>`,
  persona: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>`,
  body: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a4 4 0 0 1 4 4v2a4 4 0 0 1-8 0V6a4 4 0 0 1 4-4z"/><path d="M6 22v-4a6 6 0 0 1 12 0v4"/><path d="M8 12h8"/><path d="M18 10c2 1 3 3 3 6"/><path d="M6 10c-2 1-3 3-3 6"/></svg>`,
  lifecycle: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4"/><path d="M12 18v4"/><path d="m4.93 4.93 2.83 2.83"/><path d="m16.24 16.24 2.83 2.83"/><path d="M2 12h4"/><path d="M18 12h4"/><circle cx="12" cy="12" r="4"/></svg>`,
  settings: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>`
};

const NAV_ITEMS = [
  { id: "chat", icon: "chat", label: "对话", title: "对话" },
  { id: "execute", icon: "execute", label: "运行", title: "运行状态" },
  { id: "knowledge", icon: "knowledge", label: "知识", title: "文件知识库" },
  { id: "skills", icon: "skills", label: "技能", title: "技能资产" },
  { id: "body", icon: "body", label: "身体", title: "身体与角色" },
  { id: "lifecycle", icon: "lifecycle", label: "生命", title: "生命状态" },
  { id: "settings", icon: "settings", label: "设置", title: "后台设置" }
];

export const navRailPlugin = {
  id: "nav-rail",
  slot: "nav",
  order: 10,
  mount({ slot, state, actions }) {
    const shell = document.querySelector(".app-shell");

    slot.innerHTML = `
      <div class="rail-mark" title="天工造物">
        <img src="../assets/tiangong-logo-icon.png" alt="天工造物" />
      </div>
      ${NAV_ITEMS.map((item) => `
        <button class="rail-item" type="button" data-nav="${item.id}" title="${item.title}" aria-label="${item.title}">
          ${ICONS[item.icon]}
          <span class="rail-label">${item.label}</span>
        </button>
      `).join("")}
      <div class="nav-spacer"></div>
    `;

    const buttons = [...slot.querySelectorAll("[data-nav]")];

    function render(page) {
      const activePage = NAV_ITEMS.some((item) => item.id === page) ? page : "chat";
      shell.dataset.page = activePage;
      for (const button of buttons) {
        const isActive = button.dataset.nav === activePage;
        // P2-13: expose the active navigation state to assistive technology.
        if (isActive) {
          button.setAttribute("aria-current", "page");
        } else {
          button.removeAttribute("aria-current");
        }
        button.classList.toggle("active", isActive);
      }
    }

    for (const button of buttons) {
      button.addEventListener("click", () => actions.setActivePage(button.dataset.nav));
    }

    state.on("page", render);
    render(state.snapshot().activePage);
  }
};
