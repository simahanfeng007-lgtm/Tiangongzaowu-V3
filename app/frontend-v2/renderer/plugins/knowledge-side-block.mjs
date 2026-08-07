function escHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatDate(value) {
  const raw = String(value || "");
  if (!raw) return "未记录";
  return raw.replace("T", " ").slice(0, 10);
}

function typeName(doc) {
  const suffix = String(doc?.suffix || "").toLowerCase();
  const type = String(doc?.file_type || "").toLowerCase();
  if (suffix === ".pdf" || type === "pdf") return "PDF";
  if (suffix === ".docx" || type === "docx") return "Word";
  if (suffix === ".xlsx" || type === "xlsx") return "表格";
  if (suffix === ".pptx" || type === "pptx") return "演示";
  if (type.includes("text")) return "文本";
  return "文件";
}

export const knowledgeSideBlockPlugin = {
  id: "knowledge-side-block",
  slot: "context",
  order: 113,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section id="knowledgeSidePanel" class="side-section knowledge-side-panel" hidden>
          <div class="section-heading">
            <span>知识库概览</span>
            <button id="knowledgeSideRefresh" class="small-command" type="button">刷新</button>
          </div>
          <div id="knowledgeSideState" class="mini-pill">未读取</div>
          <div class="knowledge-side-summary">
            <div><span>文档</span><strong id="knowledgeSideDocCount">0</strong></div>
            <div><span>引用片段</span><strong id="knowledgeSideCitationCount">0</strong></div>
            <div><span>最近入库</span><strong id="knowledgeSideLatest">—</strong></div>
          </div>
          <div id="knowledgeSideFormats" class="knowledge-side-formats"></div>
          <div class="knowledge-side-root">
            <span>知识库根目录</span>
            <strong id="knowledgeSideRoot">使用默认位置</strong>
          </div>
          <div class="knowledge-side-recent-title">最近文档</div>
          <div id="knowledgeSideRecent" class="knowledge-side-recent"></div>
        </section>
      `
    );

    const panel = slot.querySelector("#knowledgeSidePanel");
    const statePill = panel.querySelector("#knowledgeSideState");
    const refreshButton = panel.querySelector("#knowledgeSideRefresh");
    const docCountEl = panel.querySelector("#knowledgeSideDocCount");
    const citationCountEl = panel.querySelector("#knowledgeSideCitationCount");
    const latestEl = panel.querySelector("#knowledgeSideLatest");
    const formatsEl = panel.querySelector("#knowledgeSideFormats");
    const rootEl = panel.querySelector("#knowledgeSideRoot");
    const recentEl = panel.querySelector("#knowledgeSideRecent");
    let documents = [];
    let loaded = false;
    let loading = false;
    let lastLoadedAt = 0;

    function setState(text, ok = null) {
      statePill.textContent = text;
      statePill.className = `mini-pill ${ok === true ? "ok" : ok === false ? "failed" : ""}`;
    }

    function render() {
      docCountEl.textContent = String(documents.length);
      citationCountEl.textContent = String(documents.reduce((sum, doc) => sum + Number(doc.citation_count || 0), 0));
      const dates = documents.map((doc) => doc.created_at).filter(Boolean).sort().reverse();
      latestEl.textContent = dates.length ? String(dates[0]).replace("T", " ").slice(0, 10) : "—";

      const counts = {};
      for (const doc of documents) {
        const name = typeName(doc);
        counts[name] = (counts[name] || 0) + 1;
      }
      formatsEl.innerHTML = Object.entries(counts)
        .sort((a, b) => b[1] - a[1])
        .map(([name, count]) => `<span>${escHtml(name)} ${count}</span>`)
        .join("") || `<span class="knowledge-side-empty">暂无文档</span>`;

      const root = String(state.snapshot().settings.knowledgeRoot || "").trim();
      rootEl.textContent = root || "使用默认位置";
      rootEl.title = root;

      const recent = [...documents]
        .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))
        .slice(0, 5);
      recentEl.innerHTML = recent.map((doc) => `
        <button class="knowledge-side-doc" type="button" data-knowledge-doc-id="${escHtml(doc.document_id)}">
          <span>${escHtml(doc.file_name || doc.document_id)}</span>
          <small>${escHtml(typeName(doc))} · ${escHtml(formatDate(doc.created_at))}</small>
        </button>
      `).join("") || `<div class="knowledge-side-empty">暂无文档</div>`;
    }

    async function refresh(force = false) {
      if (loading) return;
      if (!force && loaded && Date.now() - lastLoadedAt < 30000) return;
      loading = true;
      setState("读取中");
      try {
        const result = await actions.listKnowledge();
        documents = Array.isArray(result?.documents) ? result.documents : [];
        loaded = true;
        lastLoadedAt = Date.now();
        setState(result?.ok ? "已同步" : (result?.error || "读取失败"), result?.ok === true);
        render();
      } catch (error) {
        setState(error?.message || "读取失败", false);
      } finally {
        loading = false;
      }
    }

    refreshButton.addEventListener("click", () => refresh(true));
    panel.addEventListener("click", (event) => {
      const button = event.target.closest("[data-knowledge-doc-id]");
      if (!button) return;
      try {
        window.dispatchEvent(new CustomEvent("knowledge:select-document", {
          detail: { documentId: button.dataset.knowledgeDocId }
        }));
      } catch {}
    });

    function renderPage(page) {
      panel.hidden = page !== "knowledge";
      if (page === "knowledge") void refresh();
    }
    state.on("page", renderPage);
    state.on("settings", () => {
      if (!panel.hidden) render();
    });
    window.addEventListener("knowledge:list-changed", (event) => {
      if (!Array.isArray(event?.detail?.documents)) return;
      documents = event.detail.documents;
      loaded = true;
      lastLoadedAt = Date.now();
      setState("已同步", true);
      render();
    });
    renderPage(state.snapshot().activePage);
  }
};
