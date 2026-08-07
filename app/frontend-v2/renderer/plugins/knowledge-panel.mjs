import { concisePath } from "../core/formatters.mjs";

function escHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return "0 字节";
  if (size < 1024) return `${size} 字节`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} 千字节`;
  return `${(size / 1024 / 1024).toFixed(1)} 兆字节`;
}

function formatDate(value) {
  const raw = String(value || "");
  if (!raw) return "未记录";
  return raw.replace("T", " ").slice(0, 19);
}

function typeName(doc) {
  const suffix = String(doc?.suffix || "").toLowerCase();
  const type = String(doc?.file_type || "").toLowerCase();
  if (suffix === ".pdf" || type === "pdf") return "PDF 文档";
  if (suffix === ".docx" || type === "docx") return "Word 文档";
  if (suffix === ".xlsx" || type === "xlsx") return "表格";
  if (suffix === ".pptx" || type === "pptx") return "演示文稿";
  if (type.includes("text")) return "文本";
  return "文件";
}

function statusClass(ok) {
  if (ok === true) return "ok";
  if (ok === false) return "failed";
  return "";
}

function metaRow(label, value, title = "") {
  return `
    <div class="kv-row">
      <span class="kv-key">${escHtml(label)}</span>
      <span class="kv-value" title="${escHtml(title || value)}">${escHtml(value)}</span>
    </div>
  `;
}

export const knowledgePanelPlugin = {
  id: "knowledge-panel",
  slot: "conversation",
  order: 215,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section class="page-panel knowledge-page" data-page-panel="knowledge">
          <header class="page-header">
            <div class="title-group">
              <span class="caption">知识库</span>
              <h2>文件知识库管理</h2>
            </div>
            <div class="commandbar-meta">
              <span id="knowledgeState" class="mini-pill">未读取</span>
              <button id="knowledgeImport" class="small-command" type="button">添加文件</button>
              <button id="knowledgeRefresh" class="small-command" type="button">刷新</button>
            </div>
          </header>

          <section class="page-body knowledge-body">
            <aside class="knowledge-sidebar">
              <section class="panel-card knowledge-manage-card">
                <div class="panel-title">
                  <span>知识库管理</span>
                  <span id="knowledgeManageState" class="mini-pill">就绪</span>
                </div>
                <div class="knowledge-manage-stack">
                  <label class="knowledge-manage-field">
                    <span>知识库定位</span>
                    <div class="knowledge-root-row">
                      <input id="knowledgeRootPath" type="text" readonly spellcheck="false" />
                      <button id="knowledgeChooseRoot" class="small-command" type="button">定位</button>
                      <button id="knowledgeOpenRoot" class="small-command" type="button">打开</button>
                    </div>
                  </label>

                  <div class="knowledge-manage-field">
                    <span>知识库整理</span>
                    <button id="knowledgeOrganize" class="small-command" type="button">自动整理</button>
                  </div>

                  <div class="knowledge-manage-field">
                    <span>内容搜索</span>
                    <div class="knowledge-search-row">
                      <input id="knowledgeSearchInput" type="text" maxlength="800" placeholder="搜索知识库内容、标题或引用片段" aria-label="搜索知识库" />
                      <button id="knowledgeSearchButton" class="small-command" type="button">搜索</button>
                    </div>
                    <div id="knowledgeSearchCards" class="knowledge-search-cards"></div>
                  </div>
                </div>
              </section>

              <section class="panel-card knowledge-list-card">
                <div class="panel-title">
                  <span>文档列表</span>
                  <span id="knowledgeCount" class="mini-pill">0 个</span>
                </div>
                <div id="knowledgeList" class="knowledge-list"></div>
              </section>
            </aside>

            <section class="knowledge-detail">
              <section class="panel-card knowledge-detail-card">
                <div class="panel-title">
                  <span id="knowledgeDetailTitle">文档详情</span>
                  <span id="knowledgeType" class="mini-pill">未选择</span>
                </div>
                <div id="knowledgeMetaRows" class="kv-list"></div>
                <div id="knowledgeExtractedCard" class="knowledge-extracted-card"></div>
                <div class="knowledge-actions">
                  <button id="knowledgeOpenSource" class="small-command" type="button">打开源文件</button>
                  <select id="knowledgeExportFormat" class="knowledge-select" aria-label="导出格式">
                    <option value="md">摘要文档</option>
                    <option value="txt">纯文本</option>
                    <option value="json">结构数据</option>
                  </select>
                  <button id="knowledgeExport" class="small-command" type="button">导出</button>
                  <button id="knowledgeOpenExport" class="small-command" type="button" disabled>打开导出</button>
                  <button id="knowledgeRemove" class="small-command muted-command" type="button">删除索引</button>
                </div>
              </section>

              <section class="panel-card knowledge-query-card">
                <div class="panel-title">
                  <span>文档追问</span>
                  <button id="knowledgeQueryButton" class="small-command" type="button">查询</button>
                </div>
                <div class="knowledge-query-row">
                  <input id="knowledgeQueryInput" type="text" maxlength="800" placeholder="输入关键词、页码、标题或引用片段" aria-label="知识查询" />
                  <select id="knowledgeTopK" class="knowledge-select" aria-label="命中数量">
                    <option value="4">4 条</option>
                    <option value="6" selected>6 条</option>
                    <option value="10">10 条</option>
                  </select>
                </div>
                <div id="knowledgeQueryResult" class="knowledge-result"></div>
              </section>
            </section>
          </section>
        </section>
      `
    );

    const panel = slot.querySelector('[data-page-panel="knowledge"]');
    const statePill = panel.querySelector("#knowledgeState");
    const importButton = panel.querySelector("#knowledgeImport");
    const refreshButton = panel.querySelector("#knowledgeRefresh");
    const countPill = panel.querySelector("#knowledgeCount");
    const listEl = panel.querySelector("#knowledgeList");
    const detailTitle = panel.querySelector("#knowledgeDetailTitle");
    const typePill = panel.querySelector("#knowledgeType");
    const metaRows = panel.querySelector("#knowledgeMetaRows");
    const extractedCardEl = panel.querySelector("#knowledgeExtractedCard");
    const openSource = panel.querySelector("#knowledgeOpenSource");
    const exportFormat = panel.querySelector("#knowledgeExportFormat");
    const exportButton = panel.querySelector("#knowledgeExport");
    const openExport = panel.querySelector("#knowledgeOpenExport");
    const removeButton = panel.querySelector("#knowledgeRemove");
    const queryInput = panel.querySelector("#knowledgeQueryInput");
    const topKInput = panel.querySelector("#knowledgeTopK");
    const queryButton = panel.querySelector("#knowledgeQueryButton");
    const queryResultEl = panel.querySelector("#knowledgeQueryResult");
    const manageStatePill = panel.querySelector("#knowledgeManageState");
    const knowledgeRootPath = panel.querySelector("#knowledgeRootPath");
    const chooseRootButton = panel.querySelector("#knowledgeChooseRoot");
    const openRootButton = panel.querySelector("#knowledgeOpenRoot");
    const organizeButton = panel.querySelector("#knowledgeOrganize");
    const searchInput = panel.querySelector("#knowledgeSearchInput");
    const searchButton = panel.querySelector("#knowledgeSearchButton");
    const searchCardsEl = panel.querySelector("#knowledgeSearchCards");

    let documents = [];
    let selectedId = "";
    let loaded = false;
    let busy = false;
    let manageBusy = false;
    let lastExportPath = "";
    let queryResult = null;
    let searchResult = null;
    let workspaceKey = String(state.snapshot().settings.workspace || "");
    let knowledgeRootKey = String(state.snapshot().settings.knowledgeRoot || "");

    function selectedDoc() {
      return documents.find((item) => item.document_id === selectedId) || null;
    }

    function setState(text, ok = null) {
      statePill.textContent = text;
      statePill.className = `mini-pill ${statusClass(ok)}`;
    }

    function setManageState(text, ok = null) {
      manageStatePill.textContent = text;
      manageStatePill.className = `mini-pill ${statusClass(ok)}`;
    }

    function setBusy(next) {
      busy = Boolean(next);
      for (const button of [importButton, refreshButton, openSource, exportButton, removeButton, queryButton]) {
        button.disabled = busy;
      }
      renderButtons();
    }

    function setManageBusy(next) {
      manageBusy = Boolean(next);
      renderButtons();
    }

    function renderButtons() {
      const doc = selectedDoc();
      const hasDoc = Boolean(doc);
      openSource.disabled = busy || !hasDoc || !doc.file_path;
      exportButton.disabled = busy || !hasDoc;
      removeButton.disabled = busy || !hasDoc;
      queryButton.disabled = busy || !hasDoc;
      queryInput.disabled = busy || !hasDoc;
      topKInput.disabled = busy || !hasDoc;
      openExport.disabled = busy || !lastExportPath;
      chooseRootButton.disabled = busy || manageBusy;
      openRootButton.disabled = busy || manageBusy || !knowledgeRootPath.dataset.root;
      organizeButton.disabled = busy || manageBusy;
      searchButton.disabled = busy || manageBusy;
      searchInput.disabled = busy || manageBusy;
    }

    function renderKnowledgeRoot(settings = state.snapshot().settings) {
      const root = String(settings?.knowledgeRoot || "").trim();
      knowledgeRootPath.dataset.root = root;
      knowledgeRootPath.value = root || "使用默认知识库位置";
      knowledgeRootPath.title = root || "使用默认知识库位置";
      renderButtons();
    }

    function renderList() {
      countPill.textContent = `${documents.length} 个`;
      if (!documents.length) {
        listEl.innerHTML = `<div class="knowledge-empty">暂无文档</div>`;
        return;
      }
      listEl.innerHTML = documents.map((doc) => {
        const active = doc.document_id === selectedId ? " active" : "";
        return `
          <button class="knowledge-item${active}" type="button" data-doc-id="${escHtml(doc.document_id)}">
            <span class="knowledge-item-name">${escHtml(doc.file_name || doc.document_id)}</span>
            <span class="knowledge-item-summary">${escHtml(doc.summary || "等待内容萃取")}</span>
            <span class="knowledge-item-meta">
              <span>${escHtml(typeName(doc))}</span>
              <span>${Number(doc.citation_count || 0)} 段</span>
              <span>${escHtml(formatDate(doc.created_at))}</span>
            </span>
          </button>
        `;
      }).join("");
    }

    function renderDetail() {
      const doc = selectedDoc();
      if (!doc) {
        detailTitle.textContent = "文档详情";
        typePill.textContent = "未选择";
        metaRows.innerHTML = `
          ${metaRow("状态", documents.length ? "请选择文档" : "暂无文档")}
          ${metaRow("边界", "只保存安全解析上下文，不保存原始字节")}
        `;
        extractedCardEl.innerHTML = "";
        queryResultEl.innerHTML = `<div class="knowledge-empty">等待查询</div>`;
        renderButtons();
        return;
      }
      detailTitle.textContent = doc.file_name || doc.document_id;
      typePill.textContent = typeName(doc);
      metaRows.innerHTML = [
        metaRow("文档编号", doc.document_id),
        metaRow("来源", concisePath(doc.file_path || "未记录"), doc.file_path || ""),
        metaRow("大小", formatBytes(doc.size_bytes)),
        metaRow("解析器", doc.parser || "未记录"),
        metaRow("状态", doc.status || "已索引"),
        metaRow("引用片段", `${Number(doc.citation_count || 0)} 段`),
        metaRow("解析时间", formatDate(doc.created_at)),
        metaRow("摘要", doc.summary || "无摘要", doc.summary || "")
      ].join("");
      const card = doc.card && typeof doc.card === "object" ? doc.card : {};
      const keyPoints = Array.isArray(card.key_points || doc.key_points) ? (card.key_points || doc.key_points) : [];
      const keywords = Array.isArray(card.keywords || doc.keywords) ? (card.keywords || doc.keywords) : [];
      const outline = Array.isArray(card.outline) ? card.outline : [];
      const status = String(card.extraction_status || doc.extraction_status || "legacy");
      extractedCardEl.innerHTML = `
        <article class="knowledge-card-content">
          <div class="knowledge-card-head">
            <strong>${escHtml(card.title || doc.card_title || doc.file_name || "知识卡片")}</strong>
            <span class="mini-pill ${status === "completed" ? "ok" : ""}">${status === "completed" ? "LLM 已萃取" : status === "fallback" ? "基础萃取" : "兼容数据"}</span>
          </div>
          <p class="knowledge-card-intro">${escHtml(card.summary || doc.summary || "暂无简介")}</p>
          ${keyPoints.length ? `<div class="knowledge-card-section"><span>核心要点</span><ul>${keyPoints.map((item) => `<li>${escHtml(item)}</li>`).join("")}</ul></div>` : ""}
          ${outline.length ? `<div class="knowledge-card-section"><span>内容脉络</span><div class="knowledge-card-tags">${outline.map((item) => `<em>${escHtml(item)}</em>`).join("")}</div></div>` : ""}
          ${keywords.length ? `<div class="knowledge-card-section"><span>关键词</span><div class="knowledge-card-tags">${keywords.map((item) => `<em>${escHtml(item)}</em>`).join("")}</div></div>` : ""}
          <div class="knowledge-card-section">
            <span>内容萃取</span>
            <p>${escHtml(card.content_extract || doc.content_extract || "暂无内容摘录")}</p>
          </div>
        </article>
      `;
      renderQueryResult();
      renderButtons();
    }

    function renderSearchCards() {
      if (!searchResult) {
        searchCardsEl.innerHTML = `<div class="knowledge-search-empty">等待搜索</div>`;
        return;
      }
      if (!searchResult.ok) {
        searchCardsEl.innerHTML = `<div class="knowledge-search-empty failed">${escHtml(searchResult.error || "搜索失败")}</div>`;
        return;
      }
      const cards = Array.isArray(searchResult.cards) ? searchResult.cards : [];
      if (!cards.length) {
        searchCardsEl.innerHTML = `<div class="knowledge-search-empty">没有命中内容</div>`;
        return;
      }
      searchCardsEl.innerHTML = cards.map((card) => {
        const docId = String(card.document_id || "");
        return `
          <button class="knowledge-search-card" type="button" data-search-doc-id="${escHtml(docId)}">
            <span class="knowledge-search-card-title">${escHtml(card.title || card.file_name || docId || "知识卡")}</span>
            <span class="knowledge-search-card-summary">${escHtml(card.summary || "暂无简要介绍")}</span>
            <span class="knowledge-search-card-meta">${Number(card.citation_count || 0)} 段引用 · 命中 ${Number(card.score || 0)}</span>
          </button>
        `;
      }).join("");
    }

    function renderQueryResult() {
      if (!queryResult) {
        queryResultEl.innerHTML = `<div class="knowledge-empty">等待查询</div>`;
        return;
      }
      if (!queryResult.ok) {
        queryResultEl.innerHTML = `<div class="knowledge-empty failed">${escHtml(queryResult.error || "查询失败")}</div>`;
        return;
      }
      const result = queryResult.result || {};
      const matches = Array.isArray(result.matches) ? result.matches : [];
      const summary = result.answer_summary || "已完成查询";
      queryResultEl.innerHTML = `
        <div class="knowledge-answer">${escHtml(summary)}</div>
        <div class="knowledge-matches">
          ${matches.length ? matches.map((match) => `
            <article class="knowledge-match">
              <div class="knowledge-match-head">
                <span>${escHtml(match.title || match.local_id || "片段")}</span>
                <span>${escHtml(match.citation_id || "")}</span>
              </div>
              <p>${escHtml(match.text || "")}</p>
            </article>
          `).join("") : `<div class="knowledge-empty">未命中片段</div>`}
        </div>
      `;
    }

    function renderAll() {
      renderList();
      renderDetail();
      renderSearchCards();
      renderKnowledgeRoot();
    }

    function applyList(result) {
      documents = Array.isArray(result?.documents) ? result.documents : [];
      if (selectedId && !documents.some((item) => item.document_id === selectedId)) {
        selectedId = "";
      }
      if (!selectedId && documents.length) {
        selectedId = documents[0].document_id;
      }
      loaded = true;
      renderAll();
      try {
        window.dispatchEvent(new CustomEvent("knowledge:list-changed", { detail: { documents } }));
      } catch {}
    }

    async function refreshKnowledge(quiet = false) {
      if (!quiet) setState("读取中");
      setBusy(true);
      try {
        const result = await actions.listKnowledge();
        applyList(result);
        setState(result.ok ? "已同步" : (result.error || "读取失败"), result.ok);
      } catch (error) {
        setState(error.message || "读取失败", false);
      } finally {
        setBusy(false);
      }
    }

    async function chooseKnowledgeRoot() {
      setManageState("选择中");
      setManageBusy(true);
      try {
        const settings = await actions.chooseKnowledgeRoot?.();
        renderKnowledgeRoot(settings || state.snapshot().settings);
        if (settings?.canceled) {
          setManageState("已取消", false);
          return;
        }
        loaded = false;
        searchResult = null;
        queryResult = null;
        lastExportPath = "";
        await refreshKnowledge(true);
        setManageState(settings?.changed === false ? "位置未变化" : "已定位", true);
      } catch (error) {
        setManageState(error?.message || "定位失败", false);
      } finally {
        setManageBusy(false);
      }
    }

    async function organizeKnowledge() {
      if (!actions.organizeKnowledge) {
        setManageState("整理接口未接入", false);
        return;
      }
      setManageState("整理中");
      setManageBusy(true);
      try {
        const result = await actions.organizeKnowledge();
        if (Array.isArray(result?.documents)) applyList(result);
        setManageState(result?.ok ? "整理完成" : (result?.error || "整理失败"), result?.ok);
      } catch (error) {
        setManageState(error?.message || "整理失败", false);
      } finally {
        setManageBusy(false);
      }
    }

    async function searchKnowledge() {
      const query = searchInput.value.trim();
      if (!query) {
        searchResult = null;
        renderSearchCards();
        setManageState("请输入搜索词", null);
        return;
      }
      if (!actions.searchKnowledge) {
        setManageState("搜索接口未接入", false);
        return;
      }
      setManageState("搜索中");
      setManageBusy(true);
      try {
        searchResult = await actions.searchKnowledge(query, 8);
        renderSearchCards();
        setManageState(searchResult?.ok ? "搜索完成" : (searchResult?.error || "搜索失败"), searchResult?.ok);
      } catch (error) {
        searchResult = { ok: false, error: error?.message || "搜索失败", cards: [] };
        renderSearchCards();
        setManageState(searchResult.error, false);
      } finally {
        setManageBusy(false);
      }
    }

    async function importFiles() {
      setState("导入中");
      setBusy(true);
      try {
        const result = await actions.importKnowledgeFiles();
        applyList(result);
        const imported = Array.isArray(result.imported) ? result.imported.length : 0;
        const failed = Array.isArray(result.failed) ? result.failed.length : 0;
        if (result.imported?.[0]?.document_id) selectedId = result.imported[0].document_id;
        queryResult = null;
        renderAll();
        setState(failed ? `导入 ${imported} 个，失败 ${failed} 个` : `导入 ${imported} 个`, result.ok);
      } catch (error) {
        setState(error.message || "导入失败", false);
      } finally {
        setBusy(false);
      }
    }

    async function runQuery() {
      const doc = selectedDoc();
      const query = queryInput.value.trim();
      if (!doc || !query) return;
      setState("查询中");
      setBusy(true);
      try {
        queryResult = await actions.queryKnowledge(doc.document_id, query, Number(topKInput.value || 6));
        renderQueryResult();
        setState(queryResult.ok ? "查询完成" : (queryResult.error || "查询失败"), queryResult.ok);
      } catch (error) {
        queryResult = { ok: false, error: error.message || "查询失败" };
        renderQueryResult();
        setState(queryResult.error, false);
      } finally {
        setBusy(false);
      }
    }

    async function exportDoc() {
      const doc = selectedDoc();
      if (!doc) return;
      setState("导出中");
      setBusy(true);
      try {
        const result = await actions.exportKnowledge(doc.document_id, exportFormat.value);
        lastExportPath = result.ok ? String(result.target || "") : "";
        setState(result.ok ? "导出完成" : (result.error || "导出失败"), result.ok);
      } catch (error) {
        lastExportPath = "";
        setState(error.message || "导出失败", false);
      } finally {
        setBusy(false);
      }
    }

    async function removeDoc() {
      const doc = selectedDoc();
      if (!doc) return;
      const confirmed = window.confirm("只删除知识库索引和解析上下文，不删除源文件。继续？");
      if (!confirmed) return;
      setState("删除中");
      setBusy(true);
      try {
        const result = await actions.removeKnowledge(doc.document_id);
        selectedId = "";
        queryResult = null;
        lastExportPath = "";
        applyList(result);
        setState(result.ok ? "已删除" : (result.error || "删除失败"), result.ok);
      } catch (error) {
        setState(error.message || "删除失败", false);
      } finally {
        setBusy(false);
      }
    }

    function renderPage(page) {
      const active = page === "knowledge";
      panel.classList.toggle("active", active);
      if (active && !loaded) refreshKnowledge();
    }

    listEl.addEventListener("click", (event) => {
      const button = event.target.closest("[data-doc-id]");
      if (!button) return;
      selectedId = button.dataset.docId;
      queryResult = null;
      lastExportPath = "";
      renderAll();
    });
    window.addEventListener("knowledge:select-document", (event) => {
      const id = String(event?.detail?.documentId || "");
      if (!id || !documents.some((item) => item.document_id === id)) return;
      selectedId = id;
      queryResult = null;
      lastExportPath = "";
      renderAll();
    });
    importButton.addEventListener("click", importFiles);
    refreshButton.addEventListener("click", () => refreshKnowledge());
    chooseRootButton.addEventListener("click", chooseKnowledgeRoot);
    openRootButton.addEventListener("click", () => {
      const root = knowledgeRootPath.dataset.root || "";
      if (root) actions.openPath(root);
    });
    organizeButton.addEventListener("click", organizeKnowledge);
    searchButton.addEventListener("click", searchKnowledge);
    searchInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") searchKnowledge();
    });
    searchCardsEl.addEventListener("click", (event) => {
      const button = event.target.closest("[data-search-doc-id]");
      if (!button) return;
      const docId = button.dataset.searchDocId || "";
      if (!docId) return;
      selectedId = docId;
      queryResult = null;
      lastExportPath = "";
      renderAll();
    });
    queryButton.addEventListener("click", runQuery);
    queryInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") runQuery();
    });
    openSource.addEventListener("click", () => {
      const doc = selectedDoc();
      if (doc?.file_path) actions.openPath(doc.file_path);
    });
    exportButton.addEventListener("click", exportDoc);
    openExport.addEventListener("click", () => {
      if (lastExportPath) actions.openPath(lastExportPath);
    });
    removeButton.addEventListener("click", removeDoc);

    state.on("page", renderPage);
    state.on("settings", (settings) => {
      const nextWorkspace = String(settings.workspace || "");
      const nextKnowledgeRoot = String(settings.knowledgeRoot || "");
      renderKnowledgeRoot(settings);
      if (nextWorkspace === workspaceKey && nextKnowledgeRoot === knowledgeRootKey) return;
      workspaceKey = nextWorkspace;
      knowledgeRootKey = nextKnowledgeRoot;
      loaded = false;
      documents = [];
      selectedId = "";
      queryResult = null;
      searchResult = null;
      lastExportPath = "";
      if (state.snapshot().activePage === "knowledge") refreshKnowledge(true);
    });
    renderPage(state.snapshot().activePage);
    renderKnowledgeRoot();
    renderSearchCards();
    renderAll();
  }
};
