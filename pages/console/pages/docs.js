/**
 * 部署文档页：渲染仓库 docs/*.md
 * 标题取自 md 首个 H1；默认 install
 */
import { state } from "../state.js?v=3.0.1";
import { $, $$, esc, attr } from "../utils.js?v=3.0.1";
import { renderTopConn, renderAlert } from "../ui.js?v=3.0.1";
import { renderMarkdown, wireMdZoom } from "../md.js?v=3.0.1";
import { isLive, getApi } from "../live.js?v=3.0.1";

const FALLBACK_DOCS = [
  { id: "install", file: "install.md", title: "HAPI 安装与启动指南" },
  { id: "usage-guide", file: "usage-guide.md", title: "插件使用指南" },
  { id: "session-isolation", file: "session-isolation.md", title: "多窗口会话隔离特性说明" },
  { id: "cf-access", file: "cf_access_guide.md", title: "Cloudflare Zero Trust Access 配置指南" },
  { id: "changelog", file: "CHANGELOG.md", title: "更新日志" },
];

/** tab 副标题：一句话说明这篇文档解决什么问题（比显示文件名有用） */
const DOC_TAB_SUBS = {
  install: "第一次用？从这里开始部署",
  "usage-guide": "指令用法与日常操作技巧",
  "session-isolation": "多个群聊/私聊怎么互不干扰",
  "cf-access": "HAPI 暴露公网时的安全认证",
  changelog: "每个版本改了什么、新功能怎么用",
};

const FALLBACK_MD = {
  install: `本文档说明如何安装并启动 [HAPI](https://github.com/tiann/hapi) 服务，以便配合本插件使用。

本地预览模式下无法读取仓库 docs/。请通过 AstrBot 插件页打开管理面板，或直接查看仓库 \`docs/install.md\`。`,
  "usage-guide": `从上手流程、Focus 模式、审批到通知路由的完整使用说明。

本地预览模式下无法读取仓库 docs/。请通过 AstrBot 打开管理面板，或查看仓库 \`docs/usage-guide.md\`。`,
  "session-isolation": `**在不同 AstrBot 会话中管理的不同 session 将会互相独立。**

本地预览模式下无法读取完整文档与截图。请通过 AstrBot 打开管理面板，或查看仓库 \`docs/session-isolation.md\`。`,
  "cf-access": `本文档记录如何为 HAPI 服务配置 Cloudflare Zero Trust Access 认证。

本地预览模式下无法读取完整文档与截图。请通过 AstrBot 打开管理面板，或查看仓库 \`docs/cf_access_guide.md\`。`,
  changelog: `每个版本的功能变化与修复记录。

本地预览模式下无法读取仓库文件。请通过 AstrBot 打开管理面板，或查看仓库 \`CHANGELOG.md\`。`,
};

function docsCatalog() {
  return state._docsList?.length ? state._docsList : FALLBACK_DOCS;
}

function ensureDocId() {
  if (!state.docsDocId) {
    state.docsDocId = state._docsDefault || "install";
  }
}

async function loadDocBody(docId) {
  if (state._docsCache?.[docId]) return state._docsCache[docId];

  if (isLive() && getApi()) {
    try {
      const doc = await getApi().docsGet(docId);
      if (doc?.markdown != null) {
        state._docsCache = state._docsCache || {};
        state._docsCache[docId] = doc;
        return doc;
      }
    } catch (e) {
      console.warn("docs load failed", docId, e);
      return {
        id: docId,
        title: docsCatalog().find((d) => d.id === docId)?.title || docId,
        markdown: `> 文档加载失败：${e.message || e}\n\n请确认插件已重载，且 \`docs/\` 目录随插件一并安装。`,
        error: true,
      };
    }
  }

  const meta = docsCatalog().find((d) => d.id === docId) || FALLBACK_DOCS[0];
  return {
    id: docId,
    title: meta.title,
    file: meta.file,
    markdown: FALLBACK_MD[docId] || FALLBACK_MD.install,
    offline: true,
  };
}

function paintDocTabs() {
  const docs = docsCatalog();
  const active = state.docsDocId || docs[0]?.id;
  const el = $("#docs-tabs");
  if (!el) return;
  el.innerHTML = docs
    .map(
      (d) =>
        `<button type="button" class="help-tab ${d.id === active ? "is-active" : ""}" data-doc="${attr(d.id)}">${esc(
          d.title,
        )}<span class="help-tab-sub">${esc(DOC_TAB_SUBS[d.id] || d.file)}</span></button>`,
    )
    .join("");
  $$("#docs-tabs .help-tab").forEach((b) => {
    b.onclick = () => {
      if (b.dataset.doc === state.docsDocId) return;
      state.docsDocId = b.dataset.doc;
      void paintDocBody();
    };
  });
}

async function paintDocBody() {
  paintDocTabs();
  const host = $("#docs-body");
  const titleEl = $("#docs-title");
  const subEl = $("#docs-sub");
  if (!host) return;

  const docId = state.docsDocId || "install";
  host.innerHTML = `<div class="empty">加载文档中…</div>`;
  if (titleEl) titleEl.textContent = "…";
  if (subEl) subEl.textContent = "";

  const doc = await loadDocBody(docId);
  if ((state.docsDocId || "install") !== docId) return;

  if (titleEl) titleEl.textContent = doc.title || docId;
  if (subEl) {
    subEl.textContent = doc.offline
      ? `预览占位 · ${doc.file || docId}`
      : `来源 docs/${doc.file || docId} · 点击图片可放大`;
  }
  host.innerHTML = `<div class="md-body">${renderMarkdown(doc.markdown || "")}</div>`;
  wireMdZoom(host);

  host.querySelectorAll("a.md-doc-link[data-doc]").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const id = a.getAttribute("data-doc");
      if (!id || id === state.docsDocId) return;
      state.docsDocId = id;
      void paintDocBody();
      $("#docs-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}

function renderDocs() {
  if (!state.data) return;
  renderTopConn();
  renderAlert();
  ensureDocId();

  $("#view-docs").innerHTML = `
    <div class="card docs-card" id="docs-card">
      <div class="card-head">
        <div>
          <h2 id="docs-title">—</h2>
          <p class="sub" id="docs-sub"></p>
        </div>
      </div>
      <div id="docs-tabs" class="help-tabs docs-tabs"></div>
      <div id="docs-body" class="docs-body"></div>
    </div>
  `;

  void paintDocBody();
}

export { renderDocs };
