/**
 * 交互优化页：戳一戳 / 快捷前缀 / 推送卡片样式
 */
import { RENDER_KIND_LABELS } from "../constants.js?v=3.0.1";
import { state, store } from "../state.js?v=3.0.1";
import { $, $$, esc, attr } from "../utils.js?v=3.0.1";
import { renderTopConn, renderAlert, toast, paintSaveStatus } from "../ui.js?v=3.0.1";
import { refresh } from "../data.js?v=3.0.1";
import { isLive, getApi } from "../live.js?v=3.0.1";
import { helpTopics, helpCommands } from "./help.js?v=3.0.1";
import { renderMarkdown } from "../md.js?v=3.0.1";


/** 当前生效的完整 CSS 文本（默认或已保存自定义） */
function defaultCssText() {
  return (
    (state.meta && state.meta.render && state.meta.render.default_css) ||
    DEFAULT_CARD_CSS_FALLBACK
  );
}

/**
 * 简易模式可调的 --card-* 变量（出图真正读这些）。
 * 高级模式写完整 CSS；切换时双向同步。
 */
const CSS_SIMPLE_FIELDS = [
  {
    group: "颜色",
    items: [
      { key: "--card-bg", label: "背景", type: "color", fallback: "#f7f4ea" },
      { key: "--card-fg", label: "正文", type: "color", fallback: "#14120f" },
      { key: "--card-accent", label: "强调", type: "color", fallback: "#0f6b3c" },
      { key: "--card-muted", label: "次要", type: "color", fallback: "#3a362e" },
      { key: "--card-border", label: "边框", type: "color", fallback: "#c9c2b0" },
      { key: "--card-code-bg", label: "代码底", type: "color", fallback: "#ebe4d0" },
    ],
  },
  {
    group: "尺寸",
    items: [
      { key: "--card-width", label: "宽度", type: "text", fallback: "720px", hint: "如 720px" },
      { key: "--card-radius", label: "圆角", type: "text", fallback: "12px" },
      { key: "--card-pad", label: "内边距", type: "text", fallback: "28px" },
      { key: "--card-font-scale", label: "字号倍率", type: "text", fallback: "1.12", hint: "1 = 100%" },
    ],
  },
  {
    group: "字号",
    items: [
      { key: "--card-title-size", label: "标题", type: "text", fallback: "24px" },
      { key: "--card-body-size", label: "正文", type: "text", fallback: "16.5px" },
      { key: "--card-sub-size", label: "副标题", type: "text", fallback: "14.5px" },
      { key: "--card-meta-size", label: "元信息", type: "text", fallback: "13.5px" },
    ],
  },
  {
    group: "列表 / 间距",
    items: [
      { key: "--card-idx-w", label: "序号宽", type: "text", fallback: "46px" },
      { key: "--card-idx-h", label: "序号高", type: "text", fallback: "32px" },
      { key: "--card-row-gap", label: "行间距", type: "text", fallback: "10px" },
      { key: "--card-section-gap", label: "分组间距", type: "text", fallback: "16px" },
    ],
  },
  {
    group: "Tool 条 / Edit diff",
    items: [
      { key: "--card-tool-bg", label: "Tool 背景", type: "color", fallback: "#e8f0e9" },
      { key: "--card-tool-fg", label: "Tool 文字", type: "color", fallback: "#14120f" },
      { key: "--card-tool-accent", label: "Tool 强调色", type: "color", fallback: "#0f6b3c" },
      { key: "--card-tool-border", label: "Tool 边框", type: "color", fallback: "#c9c2b0" },
      { key: "--card-ask-bg", label: "Ask 背景", type: "color", fallback: "#eef3e8" },
      {
        key: "--card-diff-add",
        label: "Edit diff +",
        type: "color",
        fallback: "#0f6b3c",
        hint: "新增行颜色",
      },
      {
        key: "--card-diff-del",
        label: "Edit diff -",
        type: "color",
        fallback: "#ed333b",
        hint: "删除行颜色",
      },
      { key: "--card-tool-radius", label: "Tool 圆角", type: "text", fallback: "8px" },
      { key: "--card-tool-pad-y", label: "Tool 上下内边距", type: "text", fallback: "10px" },
      { key: "--card-tool-pad-x", label: "Tool 左右内边距", type: "text", fallback: "14px" },
      { key: "--card-tool-gap", label: "Tool 间距", type: "text", fallback: "12px" },
      { key: "--card-tool-bar-w", label: "Tool 左侧条宽", type: "text", fallback: "4px", hint: "0=关闭" },
    ],
  },
];

function extractCssVars(css) {
  const map = {};
  const re = /(--card-[\w-]+)\s*:\s*([^;]+);/g;
  let m;
  while ((m = re.exec(String(css || "")))) {
    map[m[1]] = m[2].trim();
  }
  return map;
}

/** 在完整 CSS 上覆盖/写入 :root 里的若干变量（保留其余内容与选择器） */
function upsertCssVars(css, overrides) {
  let text = String(css || "").trim() || defaultCssText();
  const keys = Object.keys(overrides || {}).filter(
    (k) => overrides[k] != null && overrides[k] !== "",
  );
  if (!keys.length) return text;

  for (const key of keys) {
    const val = String(overrides[key]).trim();
    const re = new RegExp(
      "(" + key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*:\\s*)([^;]+)(;)",
    );
    if (re.test(text)) {
      text = text.replace(re, "$1" + val + "$3");
    } else {
      const root = text.indexOf(":root");
      if (root >= 0) {
        const brace = text.indexOf("{", root);
        if (brace >= 0) {
          text =
            text.slice(0, brace + 1) +
            "\n  " +
            key +
            ": " +
            val +
            ";" +
            text.slice(brace + 1);
          continue;
        }
      }
      text = ":root {\n  " + key + ": " + val + ";\n}\n\n" + text;
    }
  }
  return text;
}

function getCssEditorMode() {
  return state._cssEditorMode === "advanced" ? "advanced" : "simple";
}

function setCssEditorMode(mode) {
  state._cssEditorMode = mode === "advanced" ? "advanced" : "simple";
}

/** 简易表单 → 变量 map */
function readSimpleCssForm() {
  const out = {};
  for (const g of CSS_SIMPLE_FIELDS) {
    for (const it of g.items) {
      const el = document.querySelector('[data-css-var="' + it.key + '"]');
      if (!el) continue;
      let v = String(el.value || "").trim();
      if (
        it.type === "color" &&
        v &&
        !v.startsWith("#") &&
        /^[0-9a-fA-F]{3,8}$/.test(v)
      ) {
        v = "#" + v;
      }
      if (v) out[it.key] = v;
    }
  }
  return out;
}

/** 把 CSS 变量填进简易表单 */
function fillSimpleCssForm(css) {
  const vars = extractCssVars(css || defaultCssText());
  const fallback = extractCssVars(defaultCssText());
  for (const g of CSS_SIMPLE_FIELDS) {
    for (const it of g.items) {
      const el = document.querySelector('[data-css-var="' + it.key + '"]');
      if (!el) continue;
      const val = vars[it.key] ?? fallback[it.key] ?? it.fallback ?? "";
      el.value = val;
      const pair = document.querySelector(
        '[data-css-color-for="' + it.key + '"]',
      );
      if (pair && /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(val)) {
        let hex = val;
        if (hex.length === 4) {
          hex = "#" + [...hex.slice(1)].map((c) => c + c).join("");
        } else if (hex.length === 9) {
          hex = hex.slice(0, 7);
        }
        try {
          pair.value = hex;
        } catch (_) {
          /* ignore invalid */
        }
      }
    }
  }
}

/**
 * 当前应保存/预览的完整 CSS。
 * 简易模式：把表单写回 textarea 再读；高级模式：直接读 textarea。
 */
function currentCssFromEditor() {
  const ta = $("#ix-css");
  if (!ta) {
    const cfg = state.data?.config || {};
    const custom = (cfg.card_custom_css || "").trim();
    return custom || defaultCssText();
  }
  if (getCssEditorMode() === "simple") {
    const merged = upsertCssVars(
      ta.value || defaultCssText(),
      readSimpleCssForm(),
    );
    ta.value = merged;
  }
  return ta.value || defaultCssText();
}

function applyCssEditorModeUI() {
  const mode = getCssEditorMode();
  const simple = $("#ix-css-simple");
  const advanced = $("#ix-css-advanced");
  if (simple) simple.hidden = mode !== "simple";
  if (advanced) advanced.hidden = mode !== "advanced";
  $$("#ix-css-mode-tabs [data-css-mode]").forEach((btn) => {
    btn.classList.toggle("is-on", btn.dataset.cssMode === mode);
  });
  const hint = $("#ix-css-mode-hint");
  if (hint) {
    hint.textContent =
      mode === "simple"
        ? "改常用 --card-* 变量即可出图；需要完整 CSS / 选择器时切「高级」。"
        : "完整可编辑 CSS。出图只读 :root 的 --card-*；选择器仅影响左侧网页预览。";
  }
}

/** 切换简易/高级：先把当前侧写回完整 CSS，再刷新另一侧 */
function switchCssEditorMode(next) {
  next = next === "advanced" ? "advanced" : "simple";
  const cur = getCssEditorMode();
  const ta = $("#ix-css");
  if (!ta) {
    setCssEditorMode(next);
    applyCssEditorModeUI();
    return;
  }
  if (cur === "simple" && next === "advanced") {
    ta.value = upsertCssVars(ta.value || defaultCssText(), readSimpleCssForm());
  } else if (cur === "advanced" && next === "simple") {
    fillSimpleCssForm(ta.value || defaultCssText());
  }
  setCssEditorMode(next);
  applyCssEditorModeUI();
}

function wireCssEditorMode() {
  $$("#ix-css-mode-tabs [data-css-mode]").forEach((btn) => {
    btn.onclick = () => switchCssEditorMode(btn.dataset.cssMode);
  });
  $$("[data-css-color-for]").forEach((picker) => {
    picker.oninput = () => {
      const key = picker.dataset.cssColorFor;
      const text = document.querySelector('[data-css-var="' + key + '"]');
      if (text) text.value = picker.value;
      syncInteractSaveStatus();
    };
  });
  $$('[data-css-var][data-css-kind="color"]').forEach((text) => {
    text.oninput = () => {
      const key = text.dataset.cssVar;
      const picker = document.querySelector(
        '[data-css-color-for="' + key + '"]',
      );
      if (!picker) return;
      let v = String(text.value || "").trim();
      if (v && !v.startsWith("#") && /^[0-9a-fA-F]{3,8}$/.test(v)) v = "#" + v;
      if (/^#([0-9a-fA-F]{6})$/.test(v)) picker.value = v;
      else if (/^#([0-9a-fA-F]{3})$/.test(v)) {
        picker.value = "#" + [...v.slice(1)].map((c) => c + c).join("");
      }
      syncInteractSaveStatus();
    };
  });
  applyCssEditorModeUI();
}

function renderSimpleCssFormHtml() {
  const groupsHtml = CSS_SIMPLE_FIELDS.map((g) => {
    const cells = g.items
      .map((it) => {
        if (it.type === "color") {
          return (
            '<label class="css-simple-item">' +
            '<span class="css-simple-label">' +
            esc(it.label) +
            "</span>" +
            '<span class="css-simple-color-row">' +
            '<input type="color" data-css-color-for="' +
            attr(it.key) +
            '" value="' +
            attr(it.fallback) +
            '" title="' +
            attr(it.key) +
            '" />' +
            '<input type="text" class="ctrl" data-css-var="' +
            attr(it.key) +
            '" data-css-kind="color" value="' +
            attr(it.fallback) +
            '" spellcheck="false" />' +
            "</span></label>"
          );
        }
        return (
          '<label class="css-simple-item">' +
          '<span class="css-simple-label">' +
          esc(it.label) +
          (it.hint
            ? ' <span class="muted">· ' + esc(it.hint) + "</span>"
            : "") +
          "</span>" +
          '<input type="text" class="ctrl" data-css-var="' +
          attr(it.key) +
          '" value="' +
          attr(it.fallback) +
          '" spellcheck="false" />' +
          "</label>"
        );
      })
      .join("");
    return (
      '<div class="css-simple-group">' +
      '<div class="css-simple-group-title">' +
      esc(g.group) +
      "</div>" +
      '<div class="css-simple-grid">' +
      cells +
      "</div></div>"
    );
  }).join("");
  // 折叠：颜色/尺寸/字号/间距 装进 details；高级 CSS 在面板外，不装这里
  return (
    '<details class="css-simple-fold" id="ix-css-simple-fold">' +
    '<summary class="css-simple-fold-summary">' +
    '<span class="adv-chevron" aria-hidden="true">▸</span>' +
    '<span class="css-simple-fold-title">常用样式变量</span>' +
    '<span class="css-simple-fold-hint">颜色 · 尺寸 · 字号 · 间距 · Tool/Edit diff · 点击展开</span>' +
    "</summary>" +
    '<div class="css-simple-fold-body">' +
    groupsHtml +
    "</div></details>"
  );
}

function normalizeRenderMode(m) {
  return String(m || "text").toLowerCase() === "card" ? "card" : "text";
}

const DEFAULT_TEXT_PREVIEW_MARKDOWN = `# Markdown 测试

这是普通文字，下面用于检查纯文本发送效果。

**粗体文字**  *斜体文字*  ~~删除线~~

- 第一项
- 第二项
- 第三项

> 这是一段引用内容。

\`\`\`python
print("Hello from HAPI Connector")
\`\`\`

[示例链接](https://example.com)`;

function textWindowOptionsHtml() {
  const options = Array.isArray(state.data?.window_options)
    ? state.data.window_options
    : [];
  const rows = ['<option value="">请选择窗口</option>'];
  for (const item of options) {
    if (!item || !item.umo) continue;
    rows.push(
      `<option value="${attr(item.umo)}">${esc(item.title || item.name || item.umo)}</option>`,
    );
  }
  return rows.join("");
}

function textSessionOptionsHtml() {
  const sessions = Array.isArray(state.data?.sessions) ? state.data.sessions : [];
  const rows = ['<option value="">请选择 HAPI Session</option>'];
  for (const item of sessions) {
    if (!item || !item.id) continue;
    const route = item.effective_umo ? ` · 路由 ${item.effective_umo}` : " · 暂无路由";
    rows.push(
      `<option value="${attr(item.id)}">${esc(`[${item.flavor || "unknown"}] ${item.title || item.id_short || item.id}${route}`)}</option>`,
    );
  }
  return rows.join("");
}

const TEXT_TEST_MODE_HINTS = {
  command_reply: "模拟你发指令后机器人「回复你」的方式，和 /hapi msg 的显示效果一致。需要先在目标窗口里发过一条消息。",
  direct_window: "机器人「主动发消息」到窗口，用来和上面的回复方式对比。普通 QQ 里主动消息通常会原样显示 # 和 ** 这类 Markdown 符号。",
  bound_plain: "走正式的通知推送流程（AI 有新消息时就是这条路），按你的推送窗口设置投递纯文本。",
  sse_message: "完整模拟「AI 说了句话 → 推送到聊天」的全过程，会应用你设置的文字 / 图片渲染方式，最接近日常实际效果。",
};

function syncTextTestControls() {
  const mode = $("#ix-text-test-mode")?.value || "command_reply";
  const windowField = $("#ix-text-window-field");
  const sessionField = $("#ix-text-session-field");
  const hint = $("#ix-text-mode-hint");
  const needsWindow = mode === "command_reply" || mode === "direct_window";
  if (windowField) windowField.hidden = !needsWindow;
  if (sessionField) sessionField.hidden = needsWindow;
  if (hint) hint.textContent = TEXT_TEST_MODE_HINTS[mode] || "";
}

function paintTextMarkdownPreview() {
  const input = $("#ix-text-markdown");
  const preview = $("#ix-text-markdown-preview");
  const raw = $("#ix-text-raw-preview");
  if (!input) return;
  const text = String(input.value || "");
  if (preview) {
    preview.innerHTML = text.trim()
      ? renderMarkdown(text)
      : '<p class="field-help">输入 Markdown 后将在这里显示解析预览。</p>';
  }
  if (raw) raw.textContent = text;
}

function syncCardPanelVisibility(mode) {
  mode = normalizeRenderMode(mode);
  const panel = $("#ix-card-panel");
  const preview = $("#ix-preview-pane");
  const textPreview = $("#ix-text-preview-pane");
  const textInner = $("#ix-text-preview-inner");
  const cardInner = $("#ix-card-preview-inner");
  if (panel) panel.hidden = mode !== "card";
  if (preview) preview.hidden = false; // 右栏始终显示
  if (textPreview) textPreview.hidden = mode !== "text";
  if (textInner) textInner.hidden = mode !== "text";
  if (cardInner) cardInner.hidden = mode !== "card";
  // enum-card 高亮
  $$('#ix-rmode-cards input[name="ix-rmode"]').forEach((inp) => {
    const card = inp.closest(".enum-card");
    if (card) card.classList.toggle("is-on", normalizeRenderMode(inp.value) === mode);
  });
}

const DEFAULT_CARD_CSS_FALLBACK = `/* ============================================
 * 推送图片样式
 *
 * ① :root 里的 --card-*  —— 出图真正读这些
 *    颜色 / 宽度 / 字号 / 徽章 / 序号框 / 行距
 * ② 下面的 .card / .row 等 —— 只给左侧网页预览
 *    聊天出图不读选择器，只认上面的变量
 * ============================================ */
:root {
  /* —— 颜色 —— */
  --card-bg: #f7f4ea;
  --card-fg: #14120f;
  --card-accent: #0f6b3c;
  --card-muted: #3a362e;
  --card-border: #c9c2b0;
  --card-code-bg: #ebe4d0;

  /* —— 整体尺寸 —— */
  --card-radius: 12px;
  --card-pad: 28px;
  --card-width: 720px;
  --card-font-scale: 1.12;

  /* —— 字号 —— */
  --card-title-size: 24px;
  --card-sub-size: 14.5px;
  --card-body-size: 16.5px;
  --card-meta-size: 13.5px;
  --card-foot-size: 13px;
  --card-mono: 0;

  /* —— status 状态徽章 —— */
  --card-badge-h: 40px;
  --card-badge-pad-x: 20px;
  --card-badge-font: 16.5px;
  --card-badge-dot: 6px;

  /* —— list 序号框 —— */
  --card-idx-w: 46px;
  --card-idx-h: 32px;
  --card-idx-font: 14px;
  --card-idx-radius: 7px;
  --card-idx-top: 6px;

  /* —— 行距 / 间距 —— */
  --card-row-pad-y: 13px;
  --card-row-pad-x: 14px;
  --card-row-gap: 10px;
  --card-section-gap: 16px;
}

/* —— 以下仅 DOM 预览用 —— */
.card {
  width: var(--card-width);
  background: var(--card-bg);
  color: var(--card-fg);
  border: 2px solid var(--card-border);
  border-radius: var(--card-radius);
  padding: var(--card-pad);
  font-size: calc(var(--card-body-size) * var(--card-font-scale));
  line-height: 1.55;
}
.card-title { font-size: calc(var(--card-title-size) * var(--card-font-scale)); font-weight: 700; margin-bottom: 6px; }
.card-sub { color: var(--card-muted); font-size: 0.92em; margin-bottom: 12px; }
.card-bar { width: 120px; height: 3px; background: var(--card-accent); margin-bottom: 14px; }
.row { margin-bottom: 12px; }
.row-detail { color: var(--card-muted); padding-left: 12px; }
.card-foot { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--card-border); color: var(--card-accent); }
.md pre, .md code { background: var(--card-code-bg); }
`;

const FALLBACK_INSTALLABLE = [
  {
    id: "font_noto_sc",
    group: "font",
    label: "中文字体 Noto Sans SC",
    desc: "下载到插件 assets/fonts/（约 8MB，SIL OFL）",
    installed: false,
    approx_mb: 8,
    approx_label: "约 8MB",
  },
  {
    id: "dep_pillow",
    group: "dep",
    label: "Pillow（出图引擎）",
    desc: "pip install Pillow — 低延迟出图，不依赖浏览器（约 3MB）",
    installed: false,
    approx_mb: 3,
    approx_label: "约 3MB",
  },
  {
    id: "dep_matplotlib",
    group: "dep",
    label: "matplotlib（公式）",
    desc: "pip install matplotlib — 含 numpy 等依赖；公式用它渲染（可选）（约 40MB）",
    installed: false,
    approx_mb: 40,
    approx_label: "约 40MB",
  },
];


/** 命令目录：优先 meta.command_catalog（formatters.HELP_*），否则用 help 接口 / 本地兜底 */
function commandCatalog() {
  const fromMeta = state.meta?.command_catalog;
  if (fromMeta?.commands?.length) return fromMeta;
  // 从 help 数据派生（与 formatters.export_help_data 同源）
  const topics = helpTopics().map((t) => ({ id: t.id, name: t.name, desc: t.desc }));
  const seen = new Set();
  const commands = [];
  for (const item of helpCommands()) {
    const usage = String(item.usage || "").trim();
    if (!usage.startsWith("/hapi")) continue;
    const rest = usage.slice("/hapi".length).trim();
    if (!rest) continue;
    const token = rest.split(/\s+/)[0];
    const id = token.replace(/[\[\]<>]/g, "").toLowerCase();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    // takes_arg：本地兜底根据 usage 是否含空格后参数位粗判；真值以后端 catalog 为准
    const takes_arg = /\s/.test(rest.replace(token, "").trim()) || /[\[<]/.test(usage);
    commands.push({
      id,
      topic: item.topic || "",
      usage,
      summary: item.summary || "",
      takes_arg,
    });
  }
  return { topics, commands };
}

function topicNameMap() {
  const m = {};
  for (const t of commandCatalog().topics || []) m[t.id] = t.name || t.id;
  // 兜底中文名
  Object.assign(m, {
    session: m.session || "会话",
    chat: m.chat || "对话",
    approve: m.approve || "审批",
    push: m.push || "通知",
    files: m.files || "文件",
    config: m.config || "配置",
  });
  return m;
}

function findCmdMeta(cmdId) {
  const id = String(cmdId || "").toLowerCase();
  return (commandCatalog().commands || []).find((c) => c.id === id) || null;
}

function cmdDisplayLabel(cmdId) {
  const c = findCmdMeta(cmdId);
  if (!c) return cmdId ? `/hapi ${cmdId}` : "";
  const argHint = c.takes_arg ? "关键词后可接参数" : "需整句完全匹配";
  return `${c.usage} — ${c.summary || c.id} · ${argHint}`;
}

/** 可输入过滤的命令下拉（combobox） */
function cmdSelectHtml(selected, rowIdx) {
  const label = selected ? cmdDisplayLabel(selected) : "";
  return `<div class="cmd-combo" data-idx="${rowIdx}">
    <input type="text" class="ctrl ctrl-sm js-kw-cmd-input" data-idx="${rowIdx}"
      value="${attr(label)}" placeholder="输入过滤命令…" autocomplete="off" spellcheck="false" />
    <input type="hidden" class="js-kw-cmd" data-idx="${rowIdx}" value="${attr(selected || "")}" />
    <div class="cmd-combo-panel" hidden></div>
  </div>`;
}

function filterCommands(query) {
  const q = String(query || "").trim().toLowerCase();
  const cat = commandCatalog();
  const names = topicNameMap();
  const list = cat.commands || [];
  if (!q) return list;
  return list.filter((c) => {
    const blob = [
      c.id,
      c.usage,
      c.summary,
      names[c.topic] || c.topic,
      `/hapi ${c.id}`,
    ]
      .join(" ")
      .toLowerCase();
    return blob.includes(q);
  });
}

function renderCmdComboPanel(combo, query, selectedId) {
  const panel = combo.querySelector(".cmd-combo-panel");
  if (!panel) return;
  const names = topicNameMap();
  const list = filterCommands(query);
  if (!list.length) {
    panel.innerHTML = `<div class="cmd-combo-empty">无匹配命令</div>`;
    panel.hidden = false;
    return;
  }
  // 按 topic 分组
  const byTopic = new Map();
  for (const c of list) {
    const tid = c.topic || "_";
    if (!byTopic.has(tid)) byTopic.set(tid, []);
    byTopic.get(tid).push(c);
  }
  let html = "";
  for (const [tid, cmds] of byTopic) {
    html += `<div class="cmd-combo-group">${esc(names[tid] || tid)}</div>`;
    for (const c of cmds) {
      const on = c.id === selectedId ? " is-on" : "";
      const argHint = c.takes_arg ? "关键词后可接参数" : "需整句完全匹配";
      html += `<button type="button" class="cmd-combo-item${on}" data-cmd="${attr(c.id)}">
        <span class="cmd-combo-usage mono">${esc(c.usage)}</span>
        <span class="cmd-combo-sum">${esc(c.summary || c.id)} · ${argHint}</span>
      </button>`;
    }
  }
  panel.innerHTML = html;
  panel.hidden = false;
}

function bindCmdCombos(host) {
  const root = host || document;
  const closeAll = (except) => {
    $$(".cmd-combo-panel", root).forEach((p) => {
      if (except && p === except) return;
      p.hidden = true;
    });
  };

  $$(".cmd-combo", root).forEach((combo) => {
    const idx = Number(combo.dataset.idx);
    const input = combo.querySelector(".js-kw-cmd-input");
    const hidden = combo.querySelector(".js-kw-cmd");
    const panel = combo.querySelector(".cmd-combo-panel");
    if (!input || !hidden || !panel) return;

    const pick = (cmdId) => {
      hidden.value = cmdId || "";
      input.value = cmdId ? cmdDisplayLabel(cmdId) : "";
      panel.hidden = true;
      if (state._ixKwMaps?.[idx]) {
        const prev = state._ixKwMaps[idx].command;
        state._ixKwMaps[idx].command = cmdId || "";
        if (cmdId !== "to") state._ixKwMaps[idx].args = "";
        if (prev !== cmdId) {
          paintKwMapList();
          syncInteractSaveStatus();
        }
      }
    };

    input.onfocus = () => {
      renderCmdComboPanel(combo, "", hidden.value);
    };
    input.oninput = () => {
      // 输入只做过滤；未点选前不改 hidden（避免半成品 id）
      renderCmdComboPanel(combo, input.value, hidden.value);
    };
    input.onkeydown = (e) => {
      if (e.key === "Escape") {
        panel.hidden = true;
        input.blur();
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const first = panel.querySelector(".cmd-combo-item");
        if (first) pick(first.dataset.cmd);
      }
    };
    panel.onclick = (e) => {
      const btn = e.target.closest(".cmd-combo-item");
      if (!btn) return;
      e.preventDefault();
      pick(btn.dataset.cmd);
    };
  });

  // 点外部关闭
  if (!state._cmdComboDocBound) {
    state._cmdComboDocBound = true;
    document.addEventListener("click", (e) => {
      if (e.target.closest(".cmd-combo")) return;
      $$(".cmd-combo-panel").forEach((p) => {
        p.hidden = true;
      });
    });
  }
  closeAll();
}

function paintKwMapList() {
  const host = $("#ix-kw-list");
  if (!host) return;
  const rows = Array.isArray(state._ixKwMaps) ? state._ixKwMaps : [];
  if (!rows.length) {
    host.innerHTML = `<div class="empty-inline">还没有映射。点「添加映射」：填关键词，再选对应 /hapi 命令。</div>`;
    return;
  }
  host.innerHTML = rows
    .map((row, i) => {
      const kws = Array.isArray(row.keywords) ? row.keywords.join("，") : "";
      const args = row.args || "";
      // 可带固定参数的命令显示参数输入框（send/to=发送内容；focus 等=命令参数）
      const cmd = String(row.command || "");
      const hasArgs = ["to", "send", "focus"].includes(cmd) || Boolean(args);
      const argsLabel = ["to", "send"].includes(cmd) ? "发送消息" : "固定参数";
      return `<div class="kw-map-row ${hasArgs ? "has-msg" : ""}" data-idx="${i}">
        <label class="kw-map-field">
          <span class="kw-map-label">关键词</span>
          <input type="text" class="ctrl js-kw-keys" data-idx="${i}" value="${attr(kws)}" placeholder="stop，停（逗号分隔，可多个）" />
        </label>
        <label class="kw-map-field kw-map-cmd">
          <span class="kw-map-label">映射命令</span>
          ${cmdSelectHtml(row.command || "", i)}
        </label>
        ${
          hasArgs
            ? `<label class="kw-map-field kw-map-args">
          <span class="kw-map-label">${argsLabel}</span>
          <input type="text" class="ctrl js-kw-args" data-idx="${i}" value="${attr(args)}" placeholder="如 /clear（send=发到当前会话）" />
        </label>`
            : ""
        }
        <button type="button" class="btn btn-sm btn-danger js-kw-del" data-idx="${i}" title="删除">删</button>
      </div>`;
    })
    .join("");

  $$("#ix-kw-list .js-kw-keys").forEach((inp) => {
    inp.oninput = () => {
      const i = Number(inp.dataset.idx);
      if (!state._ixKwMaps?.[i]) return;
      state._ixKwMaps[i].keywords = String(inp.value || "")
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean);
      syncInteractSaveStatus();
    };
  });
  bindCmdCombos($("#ix-kw-list"));
  $$("#ix-kw-list .js-kw-args").forEach((inp) => {
    inp.oninput = () => {
      const i = Number(inp.dataset.idx);
      if (!state._ixKwMaps?.[i]) return;
      state._ixKwMaps[i].args = String(inp.value || "").trim();
      syncInteractSaveStatus();
    };
  });
  $$("#ix-kw-list .js-kw-del").forEach((btn) => {
    btn.onclick = () => {
      const i = Number(btn.dataset.idx);
      if (!Array.isArray(state._ixKwMaps)) return;
      state._ixKwMaps.splice(i, 1);
      paintKwMapList();
      syncInteractSaveStatus();
    };
  });
}

/* ── 会话创建模板 ── */

const TPL_AGENTS = ["claude", "codex", "cursor", "grok", "kimi", "opencode", "pi"];
const TPL_REASONING_AGENTS = ["codex", "opencode"];
const TPL_EFFORTS = ["", "none", "minimal", "low", "medium", "high", "xhigh", "max"];

function tplMachineOptions(selected) {
  // snapshot.machines 是 normalize_machine 视图：顶层 label / host / platform_label
  const machines = state.data?.machines || [];
  const opts = [`<option value="">自动（只有一台机器时可留空）</option>`];
  for (const m of machines) {
    const name = m.label || m.host || (m.id ? m.id.slice(0, 8) : "?");
    const plat = m.platform_label || m.platform || "";
    opts.push(
      `<option value="${attr(m.id)}" ${selected === m.id ? "selected" : ""}>${esc(plat ? `${name} · ${plat}` : name)}</option>`,
    );
  }
  // 模板存的机器已不在线时仍显示，避免静默丢配置
  if (selected && !machines.some((m) => m.id === selected)) {
    opts.push(`<option value="${attr(selected)}" selected>已离线机器 (${esc(selected.slice(0, 8))}...)</option>`);
  }
  return opts.join("");
}

/** 每个模板一张卡片：首行 名称+删除；代理单选段；目录整行；机器/类型/思考深度/YOLO 一行 */
function paintTplList() {
  const host = $("#ix-tpl-list");
  if (!host) return;
  const rows = Array.isArray(state._ixTemplates) ? state._ixTemplates : [];
  if (!rows.length) {
    host.innerHTML = `<div class="empty-inline">还没有模板。点「添加模板」：起个名、选代理、填默认目录（可留空）。</div>`;
    return;
  }
  host.innerHTML = rows
    .map((t, i) => {
      const showReasoning = TPL_REASONING_AGENTS.includes(t.agent || "");
      const agentSeg = TPL_AGENTS.map(
        (a) =>
          `<label class="tpl-agent-opt ${t.agent === a ? "is-on" : ""}">
            <input type="radio" name="tpl-agent-${i}" class="js-tpl-agent" data-idx="${i}" value="${a}" ${t.agent === a ? "checked" : ""} />${a}</label>`,
      ).join("");
      const effortOpts = TPL_EFFORTS.map(
        (v) =>
          `<option value="${v}" ${(t.model_reasoning_effort || "") === v ? "selected" : ""}>${v || "继承默认"}</option>`,
      ).join("");
      return `<div class="tpl-card" data-idx="${i}">
        <div class="tpl-card-head">
          <input type="text" class="ctrl tpl-name-input js-tpl-name" data-idx="${i}" value="${attr(t.name || "")}"
            placeholder="模板名，如：主项目" />
          <span class="tpl-usage mono">/hapi create ${esc(t.name || "…")}</span>
          <button type="button" class="btn btn-sm btn-danger js-tpl-del" data-idx="${i}">删除</button>
        </div>
        <div class="tpl-field">
          <span class="kw-map-label">AI 代理</span>
          <div class="tpl-agent-seg">${agentSeg}</div>
        </div>
        <div class="tpl-field">
          <span class="kw-map-label">默认工作目录</span>
          <input type="text" class="ctrl mono js-tpl-dir" data-idx="${i}" value="${attr(t.directory || "")}"
            placeholder="如 /home/me/proj；留空则创建时在命令里传：/hapi create ${attr(t.name || "模板名")} <目录>" />
        </div>
        <div class="tpl-grid">
          <label class="kw-map-field">
            <span class="kw-map-label">机器</span>
            <select class="ctrl js-tpl-machine" data-idx="${i}">${tplMachineOptions(t.machine_id || "")}</select>
          </label>
          <label class="kw-map-field">
            <span class="kw-map-label">会话类型</span>
            <select class="ctrl js-tpl-type" data-idx="${i}">
              <option value="simple" ${t.session_type !== "worktree" ? "selected" : ""}>simple — 直接用该目录</option>
              <option value="worktree" ${t.session_type === "worktree" ? "selected" : ""}>worktree — 建新工作树</option>
            </select>
          </label>
          ${
            showReasoning
              ? `<label class="kw-map-field">
            <span class="kw-map-label">思考深度</span>
            <select class="ctrl js-tpl-effort" data-idx="${i}">${effortOpts}</select>
          </label>`
              : ""
          }
          <label class="kw-map-field tpl-yolo-field">
            <span class="kw-map-label">YOLO（跳过审批，危险）</span>
            <label class="switch">
              <input type="checkbox" class="js-tpl-yolo" data-idx="${i}" ${t.yolo ? "checked" : ""} />
              <span class="switch-track" aria-hidden="true"></span>
              <span class="switch-text">${t.yolo ? "开启" : "关闭"}</span>
            </label>
          </label>
        </div>
      </div>`;
    })
    .join("");

  const bind = (sel, key) => {
    $$(sel).forEach((el) => {
      const handler = () => {
        const i = Number(el.dataset.idx);
        if (!state._ixTemplates?.[i]) return;
        const val = el.type === "checkbox" ? el.checked : String(el.value || "").trim();
        state._ixTemplates[i][key] = val;
        // 代理 / 名称 / YOLO 变化影响卡片内联展示，需要重绘
        if (key === "agent" || key === "yolo") paintTplList();
        markTplDirty();
      };
      if (el.tagName === "SELECT" || el.type === "checkbox" || el.type === "radio") el.onchange = handler;
      else el.oninput = handler;
    });
  };
  bind("#ix-tpl-list .js-tpl-name", "name");
  bind("#ix-tpl-list .js-tpl-agent", "agent");
  bind("#ix-tpl-list .js-tpl-dir", "directory");
  bind("#ix-tpl-list .js-tpl-machine", "machine_id");
  bind("#ix-tpl-list .js-tpl-type", "session_type");
  bind("#ix-tpl-list .js-tpl-effort", "model_reasoning_effort");
  bind("#ix-tpl-list .js-tpl-yolo", "yolo");

  // 名称改动时同步内联 usage 提示（不整卡重绘，避免打断输入）
  $$("#ix-tpl-list .js-tpl-name").forEach((inp) => {
    inp.addEventListener("input", () => {
      const usage = inp.closest(".tpl-card-head")?.querySelector(".tpl-usage");
      if (usage) usage.textContent = `/hapi create ${String(inp.value || "").trim() || "…"}`;
    });
  });

  $$("#ix-tpl-list .js-tpl-del").forEach((btn) => {
    btn.onclick = () => {
      const i = Number(btn.dataset.idx);
      if (!Array.isArray(state._ixTemplates)) return;
      state._ixTemplates.splice(i, 1);
      paintTplList();
      markTplDirty();
    };
  });
}

/** 脏基线：进入页面 / 保存成功时记录，切页时比较 */
function tplSnapshot() {
  return stableJson(
    (state._ixTemplates || []).map((t) => ({
      name: t.name || "",
      agent: t.agent || "",
      directory: t.directory || "",
      machine_id: t.machine_id || "",
      session_type: t.session_type || "simple",
      worktree_name: t.worktree_name || "",
      yolo: Boolean(t.yolo),
      model_reasoning_effort: t.model_reasoning_effort || "",
    })),
  );
}

function isTplDirty() {
  if (!Array.isArray(state._ixTemplates)) return false;
  return tplSnapshot() !== (state._ixTplBaseline || tplSnapshot());
}

function markTplDirty() {
  paintSaveStatus($("#ix-tpl-save-status"), isTplDirty() ? "dirty" : "");
}

async function loadTemplates() {
  // 有未保存编辑时不从远端覆盖（重绘场景），只重画现有内存数据
  if (Array.isArray(state._ixTemplates) && isTplDirty()) {
    paintTplList();
    markTplDirty();
    return;
  }
  if (isLive() && getApi()) {
    try {
      const res = await getApi().getSessionTemplates();
      state._ixTemplates = Array.isArray(res?.templates) ? res.templates : [];
    } catch (err) {
      state._ixTemplates = [];
    }
  } else {
    state._ixTemplates = Array.isArray(state._mockTemplates) ? state._mockTemplates : [];
  }
  state._ixTplBaseline = tplSnapshot();
  paintTplList();
}

async function saveTemplates() {
  const templates = (state._ixTemplates || []).filter(
    (t) => String(t.name || "").trim() && String(t.agent || "").trim(),
  );
  try {
    if (isLive() && getApi()) {
      const res = await getApi().setSessionTemplates(templates);
      state._ixTemplates = Array.isArray(res?.templates) ? res.templates : templates;
      toast(res?.message || "模板已保存");
    } else {
      state._mockTemplates = templates.map((t) => ({ ...t }));
      state._ixTemplates = state._mockTemplates;
      toast("模板已保存（本地预览）");
    }
    state._ixTplBaseline = tplSnapshot();
    paintTplList();
    paintSaveStatus($("#ix-tpl-save-status"), "saved");
    return true;
  } catch (err) {
    toast("保存模板失败: " + (err.message || err));
    paintSaveStatus($("#ix-tpl-save-status"), "error");
    return false;
  }
}

function collectQuickOpsPatchFromForm() {
  const pokeOn = Boolean($("#ix-poke")?.checked);
  const pokeAction =
    document.querySelector("#ix-poke-actions input[name='ix-poke-action']:checked")?.value ||
    "approve";
  const prefix = ($("#ix-prefix")?.value || ">").trim() || ">";
  // 再从 DOM 同步一遍关键词，避免漏 input 事件
  $$("#ix-kw-list .js-kw-keys").forEach((inp) => {
    const i = Number(inp.dataset.idx);
    if (!state._ixKwMaps?.[i]) return;
    state._ixKwMaps[i].keywords = String(inp.value || "")
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean);
  });
  $$("#ix-kw-list .js-kw-cmd").forEach((inp) => {
    const i = Number(inp.dataset.idx);
    if (!state._ixKwMaps?.[i]) return;
    // hidden input 存 command id
    state._ixKwMaps[i].command = String(inp.value || "").trim().toLowerCase();
  });
  $$("#ix-kw-list .js-kw-args").forEach((inp) => {
    const i = Number(inp.dataset.idx);
    if (!state._ixKwMaps?.[i]) return;
    state._ixKwMaps[i].args = String(inp.value || "").trim();
  });
  const maps = (state._ixKwMaps || [])
    .map((m) => {
      const cmd = String(m.command || "").trim().toLowerCase();
      const entry = {
        keywords: [...(m.keywords || [])].filter(Boolean),
        command: cmd,
      };
      // 保留固定参数（send/to=发送内容；focus 等=命令参数），后端 normalize 兜底
      const args = String(m.args || "").trim();
      if (args) entry.args = args;
      return entry;
    })
    .filter((m) => m.keywords.length && m.command);
  return {
    poke_approve: pokeOn,
    poke_action: pokeAction,
    quick_prefix: prefix,
    cmd_keyword_maps: maps,
    cmd_keyword_maps_list: maps,
  };
}

function stableJson(v) {
  return JSON.stringify(v ?? null);
}

/** 用于脏比较的归一化快照（相对进入页/保存后的 baseline） */
function quickOpsSnapshot() {
  const patch = collectQuickOpsPatchFromForm();
  const maps = (patch.cmd_keyword_maps_list || []).map((m) => ({
    keywords: [...(m.keywords || [])].map(String).filter(Boolean),
    command: String(m.command || "").trim().toLowerCase(),
    args: String(m.args || "").trim(),
  }));
  return {
    poke_approve: Boolean(patch.poke_approve),
    poke_action: String(patch.poke_action || "approve"),
    quick_prefix: String(patch.quick_prefix || ">"),
    maps,
  };
}

function renderSnapshot() {
  const patch = collectRenderPatchFromForm();
  const kinds = String(patch.render_kinds || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .sort();
  return {
    render_mode: normalizeRenderMode(patch.render_mode),
    formula_mode: String(patch.formula_mode || "off"),
    card_font_path: String(patch.card_font_path || ""),
    card_custom_css: String(patch.card_custom_css || "").trim(),
    kinds: normalizeRenderMode(patch.render_mode) === "card" ? kinds : [],
  };
}

function captureInteractBaseline() {
  try {
    state._ixBaselineQuick = quickOpsSnapshot();
    state._ixBaselineRender = renderSnapshot();
  } catch (_) {
    state._ixBaselineQuick = null;
    state._ixBaselineRender = null;
  }
}

function isQuickOpsDirty() {
  if (!$("#ix-save-quick")) return false;
  if (!state._ixBaselineQuick) return false;
  try {
    return stableJson(quickOpsSnapshot()) !== stableJson(state._ixBaselineQuick);
  } catch (_) {
    return false;
  }
}

function isRenderDirty() {
  if (!$("#ix-save-render")) return false;
  if (!state._ixBaselineRender) return false;
  try {
    return stableJson(renderSnapshot()) !== stableJson(state._ixBaselineRender);
  } catch (_) {
    return false;
  }
}

function isInteractDirty() {
  if (state.page !== "interact") return false;
  try {
    return isQuickOpsDirty() || isRenderDirty() || isTplDirty();
  } catch (_) {
    return false;
  }
}

function paintQuickSaveStatus(status) {
  paintSaveStatus($("#ix-save-quick-status"), status);
}

function paintRenderSaveStatus(status) {
  paintSaveStatus($("#ix-save-render-status"), status);
}

function syncInteractSaveStatus() {
  if (state.page !== "interact") return;
  const qEl = $("#ix-save-quick-status");
  const rEl = $("#ix-save-render-status");
  const qState = qEl?.dataset.state || "";
  const rState = rEl?.dataset.state || "";
  // 保存中不打断
  if (qState !== "saving") {
    if (isQuickOpsDirty()) paintQuickSaveStatus("dirty");
    else if (qState === "saved") paintQuickSaveStatus("saved");
    else paintQuickSaveStatus("");
  }
  if (rState !== "saving") {
    if (isRenderDirty()) paintRenderSaveStatus("dirty");
    else if (rState === "saved") paintRenderSaveStatus("saved");
    else paintRenderSaveStatus("");
  }
}

async function saveQuickOps() {
  const patch = collectQuickOpsPatchFromForm();
  paintQuickSaveStatus("saving");
  try {
    let res = null;
    if (isLive() && getApi()) {
      res = await getApi().saveConfig(patch);
    } else {
      store.saveConfig({
        ...patch,
        cmd_keyword_maps_list: patch.cmd_keyword_maps_list || [],
      });
    }
    if (res?.config && state.data) state.data.config = { ...state.data.config, ...res.config };
    else if (state.data?.config) Object.assign(state.data.config, patch);
    if (state.draft) Object.assign(state.draft, patch);
    if (Array.isArray(patch.cmd_keyword_maps_list)) {
      state._ixKwMaps = patch.cmd_keyword_maps_list.map((m) => ({
        keywords: [...(m.keywords || [])],
        command: m.command || "",
        args: m.args || "",
      }));
    }
    await refresh({ silent: true });
    try {
      state._ixBaselineQuick = quickOpsSnapshot();
    } catch (_) {
      /* ignore */
    }
    paintQuickSaveStatus("saved");
    return true;
  } catch (e) {
    paintQuickSaveStatus("error");
    toast("保存失败: " + (e.message || e));
    return false;
  }
}

async function saveRenderSettings() {
  const patch = collectRenderPatchFromForm();
  paintRenderSaveStatus("saving");
  try {
    let res = null;
    if (isLive() && getApi()) {
      res = await getApi().saveConfig(patch);
    } else {
      const engine = state.data?.config?.render_engine || {};
      store.saveConfig({
        ...patch,
        render_kinds_list: patch.render_kinds.split(","),
        render_engine: engine,
      });
    }
    if (res?.config && state.data) state.data.config = { ...state.data.config, ...res.config };
    else if (state.data?.config) Object.assign(state.data.config, patch);
    if (state.draft) Object.assign(state.draft, patch);
    await refresh({ silent: true });
    syncCardPanelVisibility(patch.render_mode);
    try {
      state._ixBaselineRender = renderSnapshot();
    } catch (_) {
      /* ignore */
    }
    paintRenderSaveStatus("saved");
    return true;
  } catch (e) {
    paintRenderSaveStatus("error");
    toast("保存失败: " + (e.message || e));
    return false;
  }
}

/** 离开交互页前保存全部脏分区 */
async function saveAllInteractDirty() {
  let ok = true;
  if (isQuickOpsDirty()) {
    if (!(await saveQuickOps())) ok = false;
  }
  if (isRenderDirty()) {
    if (!(await saveRenderSettings())) ok = false;
  }
  if (isTplDirty()) {
    if (!(await saveTemplates())) ok = false;
  }
  return ok;
}

function discardInteractDraft() {
  // 重绘会从 state.data.config 重建表单；模板下次进入页面时从 API/mock 重新加载
  state._ixTemplates = null;
  state._ixTplBaseline = null;
  paintQuickSaveStatus("");
  paintRenderSaveStatus("");
  paintSaveStatus($("#ix-tpl-save-status"), "");
}

function interactRenderState(cfg) {
  const kinds = Array.isArray(cfg.render_kinds_list)
    ? cfg.render_kinds_list
    : String(cfg.render_kinds || "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
  const metaCss =
    (state.meta && state.meta.render && state.meta.render.default_css) ||
    DEFAULT_CARD_CSS_FALLBACK;
  const custom = (cfg.card_custom_css || "").trim();
  return {
    render_mode: normalizeRenderMode(cfg.render_mode),
    formula_mode: cfg.formula_mode || "off",
    kinds,
    card_font_path: cfg.card_font_path || "",
    default_css: metaCss,
    // 编辑器里直接显示「当前生效」的 CSS：有自定义用自定义，否则默认
    effective_css: custom || metaCss,
    using_default_css: !custom,
  };
}

function sampleTitle(kind) {
  return (
    {
      session_list: "Session 列表",
      pending: "待审批",
      status: "Session 状态",
      permission: "权限请求",
      routes: "推送路由",
      // 真实 message 卡：title = 会话标题
      message: "重构鉴权中间件",
    }[kind] || kind
  );
}

function sampleSub(kind) {
  return (
    {
      session_list: "当前窗口 · 3 个 · 思考 1 / 运行 1 / 关闭 1",
      pending: "当前窗口 2 项 · 全局 3 项",
      status: "claude · a1b2c3d4 · 思考中",
      permission: "序号 1 · claude · auth-mw",
      routes: "绑定 1 · 有默认窗口 · Agent 1",
      // 真实 message 卡：subtitle = Agent 消息 · 路径 · flavor · sid
      message: "Agent 消息 · claude · auth-mw · a1b2c3d4",
    }[kind] || ""
  );
}

function sampleFooter(kind) {
  return (
    {
      session_list: "",
      pending: "",
      status: "",
      permission: "",
      routes: "",
      message: "",
    }[kind] || ""
  );
}

/** 按真实出图结构生成 DOM 预览 body */
function sampleDomBody(kind) {
  if (kind === "message") {
    return `<div class="rpc-md">
      <div class="rpc-md-h2">修复摘要</div>
      <div class="rpc-md-p">已完成鉴权中间件重构，补充单测。</div>
      <div class="rpc-md-p">主要改动：</div>
      <div class="rpc-md-li">· 统一 JWT 刷新路径</div>
      <div class="rpc-md-li">· 抽出 session 绑定校验</div>
      <div class="rpc-md-pre"><code>pytest -q tests/test_auth.py</code></div>
    </div>`;
  }
  if (kind === "pending") {
    return [
      { i: 1, a: "claude · auth-mw", b: "Bash · npm test" },
      { i: 2, a: "claude · auth-mw", b: "Edit · src/auth.ts" },
    ]
      .map(
        (r) =>
          `<div class="rpc-row"><div class="rpc-head">[${r.i}] ${esc(r.a)}</div><div class="rpc-detail">${esc(r.b)}</div></div>`,
      )
      .join("");
  }
  if (kind === "permission") {
    return [
      { a: "工具", b: "Bash" },
      { a: "命令", b: "pytest -q tests/test_auth.py" },
    ]
      .map(
        (r) =>
          `<div class="rpc-row"><div class="rpc-head">${esc(r.a)}</div><div class="rpc-detail">${esc(r.b)}</div></div>`,
      )
      .join("");
  }
  if (kind === "status") {
    return [
      { a: "状态", b: "思考中" },
      { a: "模型", b: "opus · effort high" },
      { a: "路径", b: "/home/dev/proj-auth" },
    ]
      .map(
        (r) =>
          `<div class="rpc-row"><div class="rpc-head">${esc(r.a)}</div><div class="rpc-detail">${esc(r.b)}</div></div>`,
      )
      .join("");
  }
  if (kind === "routes") {
    return [
      { i: 1, a: "会话绑定", b: "群 A · 20001" },
      { i: 2, a: "Agent 窗口", b: "claude → 私聊" },
      { i: 3, a: "默认窗口", b: "私聊 · 10001" },
    ]
      .map(
        (r) =>
          `<div class="rpc-row"><div class="rpc-head">[${r.i}] ${esc(r.a)}</div><div class="rpc-detail">${esc(r.b)}</div></div>`,
      )
      .join("");
  }
  // session_list：对齐真实图片（分组条 + 序号块 + 状态点 + sid）
  const sessions = [
    {
      section: "…/dev/proj-auth",
      count: 2,
      items: [
        {
          i: 1,
          title: "重构鉴权中间件",
          status: "思考中",
          sk: "thinking",
          meta: "claude:opus · 当前",
          sid: "a1b2c3d4",
          cur: true,
        },
        {
          i: 2,
          title: "补 session 列表单测",
          status: "已关闭",
          sk: "closed",
          meta: "claude:sonnet",
          sid: "e5f6g7h8",
          cur: false,
        },
      ],
    },
    {
      section: "…/dev/docs",
      count: 1,
      items: [
        {
          i: 3,
          title: "API 文档生成",
          status: "运行中",
          sk: "active",
          meta: "codex:default · 待审 1",
          sid: "i9j0k1l2",
          cur: false,
        },
      ],
    },
  ];
  return sessions
    .map((g) => {
      const rows = g.items
        .map((s) => {
          const curCls = s.cur ? " is-current" : "";
          return `<div class="rpc-sess${curCls}">
            <span class="rpc-idx">${s.i}</span>
            <div class="rpc-sess-main">
              <div class="rpc-sess-title">${esc(s.title)}</div>
              <div class="rpc-sess-meta">
                <span class="rpc-dot rpc-dot-${s.sk}"></span>
                <span>${esc(s.status)} · ${esc(s.meta)}</span>
              </div>
            </div>
            <span class="rpc-sid mono">${esc(s.sid)}</span>
          </div>`;
        })
        .join("");
      return `<div class="rpc-section">
        <div class="rpc-section-head">
          <span class="rpc-section-path">${esc(g.section)}</span>
          <span class="rpc-section-count">${g.count} 个</span>
        </div>
        ${rows}
      </div>`;
    })
    .join("");
}

function paintDomCardPreview() {
  const root = $("#ix-dom-preview");
  if (!root) return;
  const kind = $("#ix-sample")?.value || "session_list";
  const foot = sampleFooter(kind);
  root.innerHTML = `
    <div class="render-preview-card">
      <div class="rpc-title">${esc(sampleTitle(kind))}</div>
      <div class="rpc-sub">${esc(sampleSub(kind))}</div>
      <div class="rpc-bar"></div>
      <div class="rpc-body">${sampleDomBody(kind)}</div>
      ${foot ? `<div class="rpc-foot">${esc(foot)}</div>` : ""}
      </div>
    <p class="field-help" style="margin-top:8px">DOM 仅示意结构。样式以自定义 CSS +「生成实图」为准。</p>`;
}

function collectRenderPatchFromForm() {
  const kindBoxes = [...document.querySelectorAll("[data-rkind]")];
  let kinds = kindBoxes.filter((el) => el.checked).map((el) => el.value);
  const modeRadio = document.querySelector('input[name="ix-rmode"]:checked');
  const render_mode = normalizeRenderMode(modeRadio?.value || $("#ix-rmode")?.value || "text");
  // 面板隐藏时 checkbox 未渲染/未勾：沿用已保存 kinds，避免误清空 message
  if (render_mode === "card" && !kinds.length) {
    const prev =
      state.data?.config?.render_kinds_list ||
      String(state.data?.config?.render_kinds || "").split(",");
    kinds = (Array.isArray(prev) ? prev : [])
      .map((x) => String(x).trim())
      .filter(Boolean);
  }
  if (render_mode === "card" && !kinds.length) {
    kinds = ["session_list", "pending", "status", "permission", "message"];
  }
  const defaultCss = defaultCssText();
  let css = currentCssFromEditor();
  if (css.trim() === String(defaultCss).trim()) css = "";

  let fontPath = "";
  const sel = $("#ix-font-select")?.value || "";
  if (sel === "__custom__") {
    fontPath = ($("#ix-font-path")?.value || "").trim();
  } else if (sel) {
    fontPath = sel;
  } else {
    fontPath = "";
  }

  const fmode =
    $("#ix-fmode")?.value ||
    state.data?.config?.formula_mode ||
    "off";

  return {
    render_mode,
    formula_mode: ["off", "detect", "formula_only", "plain", "always"].includes(fmode)
      ? fmode === "always"
        ? "plain"
        : fmode
      : "off",
    render_kinds:
      render_mode === "card"
        ? kinds.join(",") || "session_list,pending,status,permission,message"
        : String(
            state.data?.config?.render_kinds ||
              kinds.join(",") ||
              "session_list,pending,status,permission,message",
          ),
    card_custom_css: css,
    card_font_path: fontPath,
  };
}


function renderInteract() {
  if (!state.data) return;
  renderTopConn();
  renderAlert();
  const cfg = state.data.config;
  const rs = interactRenderState(cfg);
  const engine = { ...(cfg.render_engine || {}) };
  // installable：engine → meta → 前端兜底（避免「加载安装选项失败」）
  if (!Array.isArray(engine.installable) || !engine.installable.length) {
    engine.installable =
      state.meta?.render?.installable ||
      state.meta?.render?.engine?.installable ||
      FALLBACK_INSTALLABLE;
  }
  const pillowOk = Boolean(engine.pillow);
  const fonts = (engine.fonts || {});
  const fontOk = Boolean(fonts.sans || fonts.user_font);
  const engineTag = pillowOk
    ? "Pillow 可用"
    : "未装 Pillow · 回退文本";
  const engineTagCls = pillowOk ? "tag-ok" : "tag-muted";
  const kindChecks = Object.keys(RENDER_KIND_LABELS)
    .map((k) => {
      const on = rs.kinds.includes(k);
      return `<label class="chk"><input type="checkbox" data-rkind value="${k}" ${on ? "checked" : ""}/> ${esc(
        RENDER_KIND_LABELS[k],
      )}</label>`;
    })
    .join("");

  const kwMaps = Array.isArray(cfg.cmd_keyword_maps_list)
    ? cfg.cmd_keyword_maps_list
    : [];
  state._ixKwMaps = kwMaps.map((m) => ({
    keywords: [...(m.keywords || [])],
    command: m.command || "",
    args: m.args || "",
  }));
  if (!state._ixKwMaps.length) {
    // 与后端 DEFAULT_KEYWORD_MAPS 对齐
    state._ixKwMaps = [
      { keywords: ["stop", "停"], command: "stop", args: "" },
      { keywords: ["sw"], command: "sw", args: "" },
      { keywords: ["cl"], command: "send", args: "/clear" },
      { keywords: ["继续"], command: "send", args: "继续" },
      { keywords: ["专注"], command: "focus", args: "on" },
      { keywords: ["退出专注"], command: "focus", args: "off" },
    ];
  }

  // 会话模板：live 从专用 API 拉，mock 用本地
  if (!Array.isArray(state._ixTemplates)) state._ixTemplates = [];

  $("#view-interact").innerHTML = `
    <div class="card card-section">
      <div class="card-head">
        <div>
          <h2>快捷操作</h2>
          <p class="sub">让聊天里的操作更省事：发消息的快捷前缀、戳一戳快速批准、指令短别名。改完记得点右下角保存。</p>
        </div>
      </div>

      <div class="field">
        <div class="field-label-row">
          <div class="field-label">启用戳一戳快捷操作</div>
        </div>
        <p class="field-help">戳一下机器人就能执行下面选的动作（默认是批准全部待审批），批操作不用打字。仅 QQ NapCat 等支持戳一戳的平台可用。</p>
        <label class="switch">
          <input id="ix-poke" type="checkbox" ${cfg.poke_approve ? "checked" : ""} />
          <span class="switch-track" aria-hidden="true"></span>
          <span class="switch-text">${cfg.poke_approve ? "开启" : "关闭"}</span>
        </label>
      </div>

      <div class="field" id="ix-poke-action-wrap" ${cfg.poke_approve ? "" : "hidden"}>
        <div class="field-label-row">
          <div class="field-label">戳一戳映射动作</div>
        </div>
        <p class="field-help">默认一键批准。</p>
        <div class="poke-action-grid" id="ix-poke-actions">
          ${(cfg.poke_actions || [])
            .map((a) => {
              const on = (cfg.poke_action || "approve") === a.id;
              const cmd = a.cmd
                ? `<span class="pa-cmd mono">${esc(a.cmd)}</span>`
                : `<span class="pa-cmd pa-cmd-empty"></span>`;
              return `<label class="poke-action-card ${on ? "is-on" : ""}">
                <input type="radio" name="ix-poke-action" value="${attr(a.id)}" ${on ? "checked" : ""} />
                <span class="pa-emoji" aria-hidden="true">${esc(a.emoji || "·")}</span>
                <span class="pa-label">${esc(a.label || a.id)}</span>
                ${cmd}
                <span class="pa-desc">${esc(a.desc || "")}</span>
              </label>`;
            })
            .join("")}
        </div>
      </div>

      <div class="field">
        <div class="field-label-row">
          <div class="field-label">快捷发送前缀</div>
        </div>
        <p class="field-help">聊天时在消息前加这个符号（默认 <code>&gt;</code>），就会发给当前 AI 会话而不是机器人本身。比如「<code>&gt; 帮我修个 bug</code>」。嫌加前缀麻烦可以试试 <code>/hapi focus on</code> 专注模式。</p>
        <input id="ix-prefix" class="ctrl" type="text" value="${attr(cfg.quick_prefix)}" style="max-width:220px" />
      </div>

      <div class="field">
        <div class="field-label-row">
          <div class="field-label">快捷关键词映射</div>
        </div>
        <p class="field-help">给常用指令起短别名，不用每次打全 <code>/hapi ...</code>。比如发 <code>stop</code> 就是中断会话、发 <code>cl</code> 就是清空 AI 上下文。只在窗口里有活跃会话时生效，日常聊天不会误触。</p>
        <div id="ix-kw-list" class="kw-map-list"></div>
        <div class="kw-map-toolbar">
          <button type="button" class="btn btn-sm" id="ix-kw-add">添加映射</button>
        </div>
      </div>

      <div class="section-actions">
        <span class="save-status" id="ix-save-quick-status" data-state="" aria-live="polite"></span>
        <button type="button" class="btn btn-primary" id="ix-save-quick">保存快捷操作</button>
      </div>
    </div>

    <div class="card card-section">
      <div class="card-head">
        <div>
          <h2>会话模板</h2>
          <p class="sub">把常用的创建组合存成模板，聊天里 <code>/hapi create 模板名 [目录]</code> 一步创建，跳过向导。目录留空则创建时必须在命令里传。</p>
        </div>
      </div>
      <div id="ix-tpl-list" class="kw-map-list"></div>
      <div class="kw-map-toolbar">
        <button type="button" class="btn btn-sm" id="ix-tpl-add">添加模板</button>
      </div>
      <div class="section-actions">
        <span class="save-status" id="ix-tpl-save-status" data-state="" aria-live="polite"></span>
        <button type="button" class="btn btn-primary" id="ix-tpl-save">保存模板</button>
      </div>
    </div>

    <div class="card card-section">
      <div class="card-head">
        <div>
          <h2>推送呈现</h2>
          <p class="sub">AI 的回复推到聊天里时，是发纯文字还是渲染成图片。代码块、表格、公式多的话，图片更好看；推送级别配合「摘要」使用效果最佳。</p>
        </div>
        <span class="tag ${engineTagCls}">${engineTag}</span>
      </div>

      ${
        pillowOk
          ? ""
          : `<div class="alert-inline">出图需要 Pillow。可在下方勾选安装，或手动 <code>pip install Pillow</code>。未安装时配置可保存，运行时回退纯文本。</div>`
      }

      <div class="render-layout">
        <div class="render-form">
          <div class="field">
            <div class="field-label">渲染模式</div>
            <div class="enum-cards" id="ix-rmode-cards">
              ${[
                { value: "text", title: "纯文本", desc: "全部走文字推送。" },
                { value: "card", title: "图片", desc: "下方勾选的类型渲成图片。" },
              ].map((o) => `<label class="enum-card">
                <input type="radio" name="ix-rmode" value="${o.value}" ${rs.render_mode === o.value ? "checked" : ""} />
                <div class="t">${esc(o.title)}</div>
                <div class="d">${esc(o.desc)}</div>
              </label>`).join("")}
            </div>
          </div>

          <div id="ix-card-panel" ${rs.render_mode === "card" ? "" : "hidden"}>
          <div class="field">
            <div class="field-label">以下类型渲成图片</div>
            <div class="chk-grid">${kindChecks}</div>
          </div>

          <div class="field" id="ix-fmode-wrap" ${rs.kinds.includes("message") ? "" : "hidden"}>
            <div class="field-label">公式（仅 Agent 对话）</div>
            <select id="ix-fmode" class="ctrl" style="max-width:420px">
              ${[
                { value: "off", title: "关闭公式渲染" },
                { value: "detect", title: "公式用 matplotlib 渲染" },
                {
                  value: "formula_only",
                  title: "仅含公式消息渲染为图片（其他消息只发送文字）",
                },
                { value: "plain", title: "消息含公式时只发文字" },
              ]
                .map(
                  (o) =>
                    `<option value="${o.value}" ${
                      (rs.formula_mode || "off") === o.value ? "selected" : ""
                    }>${esc(o.title)}</option>`,
                )
                .join("")}
            </select>
          </div>

          <div class="field">
            <div class="field-label-row" style="margin-bottom:6px">
              <div class="field-label">图片样式</div>
              <div class="css-mode-tabs" id="ix-css-mode-tabs" role="tablist">
                <button type="button" class="css-mode-tab is-on" data-css-mode="simple" role="tab">简易</button>
                <button type="button" class="css-mode-tab" data-css-mode="advanced" role="tab">高级</button>
              </div>
            </div>
            <p class="field-help" id="ix-css-mode-hint" style="margin:0 0 8px">
              ${rs.using_default_css ? "当前为内置默认。" : "当前为已保存的自定义样式。"}
              改常用变量即可；完整 CSS 切「高级」。
            </p>
            <div id="ix-css-simple" class="css-simple-panel">
              ${renderSimpleCssFormHtml()}
            </div>
            <div id="ix-css-advanced" class="css-advanced-panel" hidden>
              <p class="field-help" style="margin:0 0 6px">
                完整可编辑 CSS。聊天出图只读 <code>:root</code> 的 <code>--card-*</code>；
                <code>.card</code> 等选择器仅影响左侧网页预览。与默认一致时保存为空（走内置）。
              </p>
              <textarea id="ix-css" class="ctrl render-css-editor" rows="18" spellcheck="false" placeholder="粘贴或编辑完整 CSS…"></textarea>
            </div>
            <div class="render-actions" style="margin-top:10px">
              <button type="button" class="btn" id="ix-reset-style">恢复默认样式</button>
            </div>
          </div>

          <div class="field">
            <div class="field-label">图片字体</div>
            <p class="field-help">会在下列路径扫描可选字体。</p>
            <ul class="font-scan-locs">
              ${(fonts.scan_locations || [
                {
                  label: "插件目录",
                  path: fonts.bundled_dir || "assets/fonts",
                  hint: "你可以把 .ttf/.otf/.ttc 等字体文件放在此目录，或点击下方安装 Noto（请注意：卸载插件时会将此目录字体文件全部清理）",
                },
                { label: "系统常见路径", path: null, hint: "Linux Noto/文泉驿、macOS PingFang、Windows 雅黑 等" },
              ]).map((loc) => `<li><strong>${esc(loc.label)}</strong>${loc.path ? ` · <code class="mono">${esc(loc.path)}</code>` : ""}
                ${loc.hint ? `<span class="muted"> — ${esc(loc.hint)}</span>` : ""}</li>`).join("")}
            </ul>
            <select id="ix-font-select" class="ctrl" style="margin-top:8px">
              <option value="">不指定（用扫描到的可用字体；都没有则回退文本）</option>
              ${(fonts.fonts || []).map((f) => {
                const cur = (rs.card_font_path || "").replace(/\\\\/g, "/");
                const fp = String(f.path || "").replace(/\\\\/g, "/");
                const sel = cur && (cur === fp || cur.endsWith("/" + f.name) || cur === f.name);
                return `<option value="${attr(f.path)}" ${sel ? "selected" : ""}>${esc(f.label || f.name)} · ${f.kb || "?"}KB</option>`;
              }).join("")}
              <option value="__custom__" ${rs.card_font_path && !(fonts.fonts || []).some((f) => f.path === rs.card_font_path) ? "selected" : ""}>自定义路径…</option>
            </select>
            <div id="ix-font-custom-wrap" style="margin-top:8px" ${rs.card_font_path && !(fonts.fonts || []).some((f) => f.path === rs.card_font_path) ? "" : "hidden"}>
              <input id="ix-font-path" class="ctrl" type="text" value="${attr(rs.card_font_path)}" placeholder="绝对路径或相对插件根，如 assets/fonts/xxx.otf" />
            </div>
            ${!(fonts.fonts || []).length ? `<p class="field-help" style="margin-top:6px">未扫到字体。你可以把 .ttf/.otf/.ttc 等字体文件放在插件目录，或点击下方安装 Noto（请注意：卸载插件时会将此目录字体文件全部清理）；也可填自定义路径。</p>` : ""}
          </div>

          <div class="field">
            <div class="field-label">可选生图依赖</div>
            <div class="chk-grid" id="ix-install-grid">
              ${(Array.isArray(engine.installable) && engine.installable.length
                  ? engine.installable
                  : FALLBACK_INSTALLABLE
                )
                .map((it) => {
                  const mark = it.installed ? "已就绪" : "未安装";
                  const detail = it.detail ? ` · ${it.detail}` : "";
                  const size = it.approx_label || (it.approx_mb ? `约 ${it.approx_mb}MB` : "");
                  const sizeBit = size ? ` · ${size}` : "";
                  return `<label class="chk install-opt ${it.installed ? "is-ready" : ""}">
                    <input type="checkbox" data-install-id value="${attr(it.id)}"/>
                    <span><strong>${esc(it.label || it.id)}</strong>
                    <span class="install-meta">${esc(mark)}${esc(detail)}${esc(sizeBit)}</span>
                    <span class="install-desc">${esc(it.desc || "")}</span></span>
                  </label>`;
                })
                .join("")}
            </div>
            <div class="render-actions" style="margin-top:10px">
              <button type="button" class="btn" id="ix-install-selected">安装所选</button>
            </div>
            <div id="ix-install-log" class="field-help" style="margin-top:8px;white-space:pre-wrap"></div>
          </div>

          </div><!-- /ix-card-panel -->

          <div id="ix-text-preview-pane" ${rs.render_mode === "text" ? "" : "hidden"}>
            <div class="field-label-row" style="margin-bottom:8px">
              <div>
                <div class="field-label">纯文本 Markdown 测试</div>
                <p class="field-help" style="margin:4px 0 0">发送到聊天窗口时仍按纯文本发送原始 Markdown。</p>
              </div>
            </div>

            <div class="field">
              <div class="field-label">Markdown 内容</div>
              <textarea id="ix-text-markdown" class="ctrl ix-text-markdown-input" rows="14" spellcheck="false" placeholder="输入要测试的 Markdown…">${esc(DEFAULT_TEXT_PREVIEW_MARKDOWN)}</textarea>
            </div>

            <div class="field">
              <div class="field-label">消息输出链路</div>
              <select id="ix-text-test-mode" class="ctrl" style="width:100%">
                <option value="command_reply">① 指令回复方式（同 /hapi msg）</option>
                <option value="direct_window">② 主动发送方式（对照用）</option>
                <option value="bound_plain">③ 通知推送・纯文本</option>
                <option value="sse_message">④ 通知推送・完整模拟（含图片渲染）</option>
              </select>
              <p class="field-help" id="ix-text-mode-hint"></p>
            </div>

            <div class="field" id="ix-text-window-field">
              <div class="field-label">目标窗口</div>
              <select id="ix-text-target-window" class="ctrl" style="width:100%">
                ${textWindowOptionsHtml()}
              </select>
            </div>

            <div class="field" id="ix-text-session-field" hidden>
              <div class="field-label">目标 HAPI Session</div>
              <select id="ix-text-target-session" class="ctrl" style="width:100%">
                ${textSessionOptionsHtml()}
              </select>
              <p class="field-help">实际窗口由该 Session 当前绑定、Agent 默认路由或全局默认窗口决定。</p>
            </div>
          </div>
        </div><!-- /render-form -->

        <div class="render-preview-pane" id="ix-preview-pane" ${rs.render_mode === "card" ? "" : "hidden"}>

          <!-- 纯文本模式预览 -->
          <div id="ix-text-preview-inner" ${rs.render_mode === "text" ? "" : "hidden"}>
            <div class="field-label" style="margin-bottom:8px">Markdown 解析预览</div>
            <div id="ix-text-markdown-preview" class="md-body render-dom-host text-markdown-preview"></div>
            <details class="text-raw-details" style="margin-top:12px">
              <summary>查看窗口实际接收的原始文本</summary>
              <pre id="ix-text-raw-preview" class="render-fallback text-raw-preview"></pre>
            </details>
            <div class="render-actions" style="margin-top:16px">
              <button type="button" class="btn" id="ix-text-refresh-preview">仅刷新预览</button>
              <button type="button" class="btn btn-primary" id="ix-text-send">执行消息链路测试</button>
            </div>
            <div id="ix-text-send-status" class="field-help" style="margin-top:8px"></div>
          </div>

          <!-- 卡片模式预览 -->
          <div id="ix-card-preview-inner" ${rs.render_mode === "card" ? "" : "hidden"}>
            <div class="field-label-row" style="margin-bottom:8px">
              <div class="field-label">预览</div>
              <select id="ix-sample" class="ctrl" style="max-width:160px">
                ${Object.keys(RENDER_KIND_LABELS)
                  .map((k) => `<option value="${k}">${esc(RENDER_KIND_LABELS[k])}</option>`)
                  .join("")}
              </select>
            </div>
            <p class="field-help">左侧 DOM 示意结构；点「生成实图」走服务端 Pillow（读自定义 CSS 变量），与聊天发出一致。</p>
            <div id="ix-dom-preview" class="render-dom-host"></div>
            <div class="render-actions" style="margin-top:12px">
              <button type="button" class="btn btn-primary" id="ix-gen-card">生成实图预览</button>
            </div>
            <div id="ix-real-meta" class="field-help" style="margin-top:8px"></div>
            <div id="ix-real-preview" class="render-real-host"></div>
          </div><!-- /ix-card-preview-inner -->
        </div>
      </div>

      <div class="section-actions">
        <span class="save-status" id="ix-save-render-status" data-state="" aria-live="polite"></span>
        <button type="button" class="btn btn-primary" id="ix-save-render">保存推送设置</button>
      </div>
    </div>
  `;

  // 戳一戳开关：只改 UI 显隐，点保存才落盘
  $("#ix-poke") &&
    ($("#ix-poke").onchange = () => {
      const on = $("#ix-poke").checked;
      const txt = $("#ix-poke").closest(".switch")?.querySelector(".switch-text");
      if (txt) txt.textContent = on ? "开启" : "关闭";
      const wrap = $("#ix-poke-action-wrap");
      if (wrap) wrap.hidden = !on;
      syncInteractSaveStatus();
    });
  $$("#ix-poke-actions input[name='ix-poke-action']").forEach((inp) => {
    inp.onchange = () => {
      $$("#ix-poke-actions .poke-action-card").forEach((c) => c.classList.remove("is-on"));
      inp.closest(".poke-action-card")?.classList.add("is-on");
      syncInteractSaveStatus();
    };
  });

  $("#ix-prefix") &&
    ($("#ix-prefix").oninput = () => {
      syncInteractSaveStatus();
    });

  paintKwMapList();
  $("#ix-kw-add") &&
    ($("#ix-kw-add").onclick = () => {
      if (!Array.isArray(state._ixKwMaps)) state._ixKwMaps = [];
      state._ixKwMaps.push({ keywords: [], command: "", args: "" });
      paintKwMapList();
      syncInteractSaveStatus();
    });

  $("#ix-save-quick") &&
    ($("#ix-save-quick").onclick = async () => {
      await saveQuickOps();
    });

  // 会话模板
  loadTemplates();
  $("#ix-tpl-add") &&
    ($("#ix-tpl-add").onclick = () => {
      if (!Array.isArray(state._ixTemplates)) state._ixTemplates = [];
      state._ixTemplates.push({
        name: "",
        agent: "claude",
        directory: "",
        machine_id: "",
        session_type: "simple",
        worktree_name: "",
        yolo: false,
        model_reasoning_effort: "",
      });
      paintTplList();
      markTplDirty();
    });
  $("#ix-tpl-save") &&
    ($("#ix-tpl-save").onclick = async () => {
      paintSaveStatus($("#ix-tpl-save-status"), "saving");
      await saveTemplates();
    });

  // 渲染模式：本地立即显隐，不必先保存
  $$('#ix-rmode-cards input[name="ix-rmode"]').forEach((inp) => {
    inp.onchange = () => {
      const mode = normalizeRenderMode(inp.value);
      syncCardPanelVisibility(mode);
      syncInteractSaveStatus();
    };
  });
  syncCardPanelVisibility(rs.render_mode);

  // 勾选「Agent 对话」才显示公式渲染
  const syncFormulaWrap = () => {
    const wrap = $("#ix-fmode-wrap");
    if (!wrap) return;
    const msgOn = [...document.querySelectorAll("[data-rkind]")].some(
      (el) => el.value === "message" && el.checked,
    );
    wrap.hidden = !msgOn;
  };
  $$("[data-rkind]").forEach((el) => {
    el.addEventListener("change", () => {
      syncFormulaWrap();
      syncInteractSaveStatus();
    });
  });
  syncFormulaWrap();

  $("#ix-fmode") &&
    ($("#ix-fmode").onchange = () => {
      syncInteractSaveStatus();
    });

  const bindPaint = () => {
    paintDomCardPreview();
  };
  ["ix-sample"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("input", bindPaint);
    el.addEventListener("change", bindPaint);
  });

  const cssTa = $("#ix-css");
  const initCss = rs.effective_css || rs.default_css || DEFAULT_CARD_CSS_FALLBACK;
  if (cssTa) cssTa.value = initCss;
  // 默认简易；已保存完整自定义仍默认简易（变量已可从表单改），用户可切高级看全文
  if (!state._cssEditorMode) state._cssEditorMode = "simple";
  fillSimpleCssForm(initCss);
  wireCssEditorMode();

  // CSS / 简易变量编辑 → 标脏
  if (cssTa) {
    cssTa.oninput = () => syncInteractSaveStatus();
  }
  $$("[data-css-var]").forEach((el) => {
    el.addEventListener("input", () => syncInteractSaveStatus());
    el.addEventListener("change", () => syncInteractSaveStatus());
  });
  $$("[data-css-color-for]").forEach((el) => {
    el.addEventListener("input", () => syncInteractSaveStatus());
  });

  $("#ix-reset-style") &&
    ($("#ix-reset-style").onclick = () => {
      const def = rs.default_css || DEFAULT_CARD_CSS_FALLBACK;
      if (cssTa) cssTa.value = def;
      fillSimpleCssForm(def);
      if ($("#ix-font-select")) $("#ix-font-select").value = "";
      if ($("#ix-font-path")) $("#ix-font-path").value = "";
      if ($("#ix-font-custom-wrap")) $("#ix-font-custom-wrap").hidden = true;
      bindPaint();
      syncInteractSaveStatus();
    });

  // 字体下拉：选「自定义」时显示路径框
  const fontSel = $("#ix-font-select");
  const fontCustom = $("#ix-font-custom-wrap");
  if (fontSel) {
    fontSel.onchange = () => {
      if (fontCustom) fontCustom.hidden = fontSel.value !== "__custom__";
      if (fontSel.value && fontSel.value !== "__custom__" && $("#ix-font-path")) {
        $("#ix-font-path").value = fontSel.value;
      }
      syncInteractSaveStatus();
    };
  }
  $("#ix-font-path") &&
    ($("#ix-font-path").oninput = () => {
      syncInteractSaveStatus();
    });

  $("#ix-install-selected") &&
    ($("#ix-install-selected").onclick = async () => {
      const boxes = [...document.querySelectorAll("[data-install-id]")];
      const ids = boxes.filter((el) => el.checked).map((el) => el.value);
      const logEl = $("#ix-install-log");
      const btn = $("#ix-install-selected");
      if (!ids.length) {
        if (logEl) logEl.textContent = "请先勾选：中文字体 和/或 Pillow。";
        return;
      }
      const setLog = (s) => {
        if (logEl) logEl.textContent = s;
      };
      setLog("安装中…\n" + ids.map((id) => "· " + id).join("\n"));
      if (btn) {
        btn.disabled = true;
        btn.classList.add("is-busy");
        btn.dataset._old = btn.textContent || "";
        btn.textContent = "安装中…";
      }
      try {
        // 与 self_learning 一样：直接 bridge.apiPost，不绕花活封装
        const bridge = window.AstrBotPluginPage;
        if (!isLive() || !bridge || typeof bridge.apiPost !== "function") {
          throw new Error("不在 AstrBot 插件面板内，或 bridge 不可用");
        }
        let raw = await bridge.apiPost("render/install", { ids, force: false });
        // 解包 { code, data } 若有
        if (
          raw &&
          typeof raw === "object" &&
          raw.data != null &&
          (raw.code === 0 || raw.code === 200 || raw.success === true)
        ) {
          raw = raw.data;
        }
        const res = raw || {};
        const lines = [];
        lines.push(res.message || (res.ok || res.success ? "完成" : "失败"));
        if (res.output) lines.push(String(res.output));
        else if (Array.isArray(res.log)) lines.push(res.log.join("\n"));
        else {
          for (const r of res.results || []) {
            lines.push(`· ${r.id}: ${r.message || (r.ok ? "ok" : r.error || "fail")}`);
            if (Array.isArray(r.log)) lines.push(r.log.join("\n"));
          }
        }
        setLog(lines.filter(Boolean).join("\n"));
        toast(res.ok || res.success ? "安装完成" : "安装有失败，见下方日志");
        await refresh({ silent: true, repaint: true });
      } catch (e) {
        setLog("安装失败: " + (e.message || e));
        toast("安装失败: " + (e.message || e));
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.classList.remove("is-busy");
          if (btn.dataset._old) btn.textContent = btn.dataset._old;
        }
      }
    });

  $("#ix-save-render") &&
    ($("#ix-save-render").onclick = async () => {
      await saveRenderSettings();
    });

  const textMarkdownInput = $("#ix-text-markdown");
  if (textMarkdownInput) textMarkdownInput.oninput = paintTextMarkdownPreview;
  const textModeSelect = $("#ix-text-test-mode");
  if (textModeSelect) textModeSelect.onchange = syncTextTestControls;
  syncTextTestControls();

  $("#ix-text-refresh-preview") &&
    ($("#ix-text-refresh-preview").onclick = () => {
      paintTextMarkdownPreview();
      const status = $("#ix-text-send-status");
      if (status) status.textContent = "预览已更新，没有发送消息。";
    });

  $("#ix-text-send") &&
    ($("#ix-text-send").onclick = async () => {
      paintTextMarkdownPreview();
      const button = $("#ix-text-send");
      const status = $("#ix-text-send-status");
      const mode = $("#ix-text-test-mode")?.value || "command_reply";
      const target = $("#ix-text-target-window")?.value || "";
      const sid = $("#ix-text-target-session")?.value || "";
      const text = $("#ix-text-markdown")?.value || "";
      const needsWindow = mode === "command_reply" || mode === "direct_window";

      if (!text.trim()) {
        if (status) status.textContent = "请输入测试内容。";
        toast("测试内容不能为空");
        return;
      }
      if (needsWindow && !target) {
        if (status) status.textContent = "请选择目标窗口。";
        toast("请选择目标窗口");
        return;
      }
      if (!needsWindow && !sid) {
        if (status) status.textContent = "请选择目标 HAPI Session。";
        toast("请选择目标 Session");
        return;
      }
      if (!isLive() || !getApi()) {
        if (status) status.textContent = "本地静态预览模式无法发送，请在 AstrBot 插件面板中操作。";
        return;
      }

      const oldText = button?.textContent || "执行消息链路测试";
      try {
        if (button) {
          button.disabled = true;
          button.classList.add("is-busy");
          button.textContent = "测试中…";
        }
        if (status) status.textContent = "正在执行所选消息输出链路…";
        const res = await getApi().renderTextTest({ mode, umo: target, sid, text });
        if (!res?.ok) throw new Error(res?.error || res?.message || "发送失败");
        if (status) {
          status.textContent = `${res.mode_label || mode} · 成功 · ${res.chars || text.length} 字符 · 目标 ${res.target || "已按路由选择"}`;
        }
        toast("消息链路测试已执行");
      } catch (e) {
        const message = e?.message || String(e);
        if (status) status.textContent = "测试失败：" + message;
        toast("测试失败：" + message);
      } finally {
        if (button) {
          button.disabled = false;
          button.classList.remove("is-busy");
          button.textContent = oldText;
        }
      }
    });

  $("#ix-gen-card") &&
    ($("#ix-gen-card").onclick = async () => {
      const kind = $("#ix-sample")?.value || "session_list";
      const style = collectRenderPatchFromForm();
      const meta = $("#ix-real-meta");
      const host = $("#ix-real-preview");
      if (meta) meta.textContent = "生成中…";
      if (host) host.innerHTML = "";
      try {
        let res;
        if (isLive() && getApi()) {
          res = await getApi().renderPreview({
            kind,
            style,
            formula_mode: style.formula_mode || $("#ix-fmode")?.value || "off",
          });
        } else {
          // mock：无服务端时只提示用 DOM 预览
          res = {
            ok: false,
            error:
              "本地预览模式无 Pillow 后端；请在 AstrBot 插件面板内生成实图，或安装依赖后重试。",
            ms: 0,
            engine: "none",
            fallback_text: sampleTitle(kind) + "\n" + sampleSub(kind),
          };
        }
        if (res?.ok && res.png_base64) {
          if (meta) {
            const fontHint = res.font_path ? ` · font=${res.font_path}` : "";
            meta.textContent = `实图 · ${res.engine} · ${res.ms}ms · ${res.bytes || "?"}B · ${res.width}×${res.height}${fontHint}`;
          }
          if (host) {
            host.innerHTML = `<img class="render-real-img" alt="card preview" src="data:${res.mime || "image/png"};base64,${res.png_base64}" />`;
          }
        } else {
          if (meta) {
            meta.textContent = `未能生成实图（${res?.engine || "none"} · ${res?.ms ?? "?"}ms）：${res?.error || "unknown"}`;
          }
          if (host && res?.fallback_text) {
            host.innerHTML = `<pre class="render-fallback">${esc(res.fallback_text)}</pre>`;
          }
        }
      } catch (e) {
        if (meta) meta.textContent = "预览失败: " + (e.message || e);
      }
    });

  paintDomCardPreview();
  paintTextMarkdownPreview();
  // 以当前表单为基线：未改动时状态区留空
  captureInteractBaseline();
  paintQuickSaveStatus("");
  paintRenderSaveStatus("");
}

export {
  renderInteract,
  isInteractDirty,
  saveAllInteractDirty,
  discardInteractDraft,
  saveQuickOps,
  saveRenderSettings,
};
