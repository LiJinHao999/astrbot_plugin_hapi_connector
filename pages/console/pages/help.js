/**
 * 命令帮助页（与 /hapi help 同源）
 */
import { HELP_TOPICS, HELP_COMMANDS } from "../constants.js?v=3.0.1";
import { state } from "../state.js?v=3.0.1";
import { $, $$, esc, attr } from "../utils.js?v=3.0.1";
import { renderTopConn, renderAlert } from "../ui.js?v=3.0.1";

function matchHelpCmd(c, q) {
  if (!q) return true;
  const topicName = helpTopics().find((t) => t.id === c.topic)?.name || "";
  const blob = [c.usage, c.summary, c.example || "", topicName, c.topic].join("\n").toLowerCase();
  const raw = q.trim().toLowerCase();
  if (!raw) return true;
  if (blob.includes(raw)) return true;
  return raw.split(/\s+/).filter(Boolean).every((tok) => blob.includes(tok));
}

function helpCmdCard(c, { showTopic = false } = {}) {
  const params = [];
  const m = c.usage.match(/[<\[][^>\]]+[>\]]/g);
  if (m) params.push(...m);
  const topicName = helpTopics().find((t) => t.id === c.topic)?.name || c.topic;
  return `<article class="help-cmd">
    <div class="help-cmd-top">
      <code class="help-usage">${esc(c.usage)}</code>
      ${showTopic ? `<span class="tag tag-muted">${esc(topicName)}</span>` : ""}
      ${c.home ? `<span class="tag tag-ok">常用</span>` : ""}
    </div>
    <p class="help-summary">${esc(c.summary)}</p>
    ${
      params.length
        ? `<div class="help-params"><span class="help-params-label">参数</span> ${params
            .map((x) => `<code>${esc(x)}</code>`)
            .join(" ")}</div>`
        : ""
    }
    ${c.example ? `<div class="help-example"><span class="help-params-label">示例</span> <code>${esc(c.example)}</code></div>` : ""}
  </article>`;
}

export function helpTopics() {
  return state._helpTopics || HELP_TOPICS;
}
export function helpCommands() {
  return state._helpCommands || HELP_COMMANDS;
}

function getHelpFiltered() {
  const q = (state.helpQuery || "").trim().toLowerCase();
  const searching = Boolean(q);
  const topic = state.helpTopic || "session";
  const topics = helpTopics();
  const commands = helpCommands();
  const topicMeta = topics.find((t) => t.id === topic) || topics[0];
  const matched = commands.filter((c) => matchHelpCmd(c, q));
  const cmds = matched.filter((c) => c.topic === topic);
  return { q, searching, topic, topicMeta, matched, cmds, topics, commands };
}

/** 只刷新 tabs / 结果 / 清除按钮，绝不重建搜索框（避免打断中文 IME） */
function updateHelpResults() {
  const root = $("#view-help");
  if (!root || !root.querySelector("#help-q")) {
    renderHelp();
    return;
  }
  const { searching, topic, topicMeta, matched, cmds } = getHelpFiltered();

  const allTopics = helpTopics();
  const allCmds = helpCommands();
  const tabs = allTopics
    .map((t) => {
      const n = searching
        ? matched.filter((c) => c.topic === t.id).length
        : allCmds.filter((c) => c.topic === t.id).length;
      return `<button type="button" class="help-tab ${t.id === topic ? "is-active" : ""}" data-topic="${t.id}">${esc(
        t.name,
      )}<span class="help-tab-sub">${esc(t.desc)} · ${n}</span></button>`;
    })
    .join("");

  const tabsEl = $("#help-tabs");
  if (tabsEl) tabsEl.innerHTML = tabs;

  const clearHost = $("#help-clear-host");
  if (clearHost) {
    clearHost.innerHTML = searching
      ? `<button type="button" class="btn btn-sm" id="help-clear">清除</button>`
      : "";
    $("#help-clear")?.addEventListener("click", () => {
      state.helpQuery = "";
      const input = $("#help-q");
      if (input) input.value = "";
      updateHelpResults();
      input?.focus();
    });
  }

  const hint = $("#help-search-hint");
  if (hint) {
    hint.hidden = !searching;
    if (searching) {
      hint.textContent = `搜索中：先匹配全部命令，再按上方分类筛选（当前分类 ${cmds.length} / 共 ${matched.length} 条命中）`;
    }
  }

  const headTitle = searching
    ? `${esc(topicMeta.name)} · 搜索「${esc((state.helpQuery || "").trim())}」`
    : `${esc(topicMeta.name)} · ${esc(topicMeta.desc)}`;
  const headSub = searching
    ? `本分类 ${cmds.length} 条 · 全部分类共 ${matched.length} 条命中`
    : `${cmds.length} 条指令`;
  const titleEl = $("#help-result-title");
  const subEl = $("#help-result-sub");
  if (titleEl) titleEl.innerHTML = headTitle;
  if (subEl) subEl.textContent = headSub;

  const list = $("#help-list");
  if (list) {
    const rows = cmds.map((c) => helpCmdCard(c, { showTopic: false })).join("");
    list.innerHTML =
      rows ||
      `<div class="empty">${
        searching
          ? matched.length
            ? "当前分类下没有匹配，点上方其它分类看看"
            : "没有匹配的命令，试试更短的关键词"
          : "该分类暂无命令"
      }</div>`;
  }

  $$("#help-tabs .help-tab").forEach((b) => {
    b.onclick = () => {
      state.helpTopic = b.dataset.topic;
      updateHelpResults();
    };
  });
}

function wireHelpSearchInput(input) {
  if (!input || input.dataset.wired === "1") return;
  input.dataset.wired = "1";

  let composing = false;
  let debounce;

  const apply = () => {
    state.helpQuery = input.value;
    updateHelpResults();
  };

  input.addEventListener("compositionstart", () => {
    composing = true;
  });
  input.addEventListener("compositionend", () => {
    composing = false;
    clearTimeout(debounce);
    apply();
  });
  input.addEventListener("input", () => {
    if (composing || input.isComposing) return;
    clearTimeout(debounce);
    debounce = setTimeout(apply, 120);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      state.helpQuery = "";
      input.value = "";
      updateHelpResults();
      input.focus();
    }
  });
}

function renderHelp() {
  if (!state.data) return;
  renderTopConn();
  renderAlert();

  const existing = $("#help-q");
  if (existing && $("#view-help") && !$("#view-help").hidden) {
    if (document.activeElement === existing) {
      updateHelpResults();
      return;
    }
  }

  const { searching } = getHelpFiltered();

  $("#view-help").innerHTML = `
    <div class="card">
      <div class="card-head">
        <div>
          <h2>命令帮助</h2>
          <p class="sub">在聊天里发这些指令来操控 AI 会话（仅管理员账号有效）· 和 /hapi help 内容一致</p>
        </div>
      </div>
      <div class="help-search-row">
        <input id="help-q" class="ctrl help-search" type="text" inputmode="search" enterkeyhint="search"
          placeholder="搜索命令、说明、参数… 如 resume / 审批 / bind" value="${attr(state.helpQuery || "")}"
          autocomplete="off" spellcheck="false" />
        <span id="help-clear-host"></span>
      </div>
      <div id="help-tabs" class="help-tabs"></div>
      <p id="help-search-hint" class="help-search-hint" ${searching ? "" : "hidden"}>搜索中：关键词匹配后仍可点分类筛选；清除或 Esc 退出搜索</p>
    </div>

    <div class="card">
      <div class="card-head">
        <div>
          <h2 id="help-result-title">—</h2>
          <p class="sub" id="help-result-sub"></p>
        </div>
      </div>
      <div id="help-list" class="help-list"></div>
    </div>
  `;

  wireHelpSearchInput($("#help-q"));
  updateHelpResults();
}

export { renderHelp };
