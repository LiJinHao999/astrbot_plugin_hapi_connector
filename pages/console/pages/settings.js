/**
 * 设置页 — 字段结构来自 meta.config_schema（_conf_schema + overlay）
 * 值来自 state.data.config（插件真实配置 / mock）
 */
import { RENDER_KIND_LABELS } from "../constants.js?v=3.0.1";
import { state } from "../state.js?v=3.0.1";
import { $, $$, esc, attr } from "../utils.js?v=3.0.1";
import { renderTopConn, renderAlert } from "../ui.js?v=3.0.1";
import { syncSettingsSaveStatus } from "../data.js?v=3.0.1";
import { CONFIG_SCHEMA_FALLBACK } from "../settings_schema_fallback.js?v=3.0.1";

/** 当前生效的设置 schema（live meta 优先，否则 fallback） */
export function getConfigSchema() {
  const fromMeta = state.meta?.config_schema;
  if (fromMeta?.groups?.length) return fromMeta;
  return CONFIG_SCHEMA_FALLBACK;
}

function settingsGroups() {
  return getConfigSchema().groups || [];
}

function parseKindsDraft(d) {
  if (Array.isArray(d.render_kinds_list) && d.render_kinds_list.length) {
    return d.render_kinds_list.map((x) => String(x).trim()).filter(Boolean);
  }
  return String(d.render_kinds || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** 与 data.js 脏检查一致：兼容 true/"true"/1 与 false/"false"/0 */
function coerceFieldBool(v) {
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v !== 0 && !Number.isNaN(v);
  const s = String(v ?? "")
    .trim()
    .toLowerCase();
  if (["1", "true", "yes", "on"].includes(s)) return true;
  if (["0", "false", "no", "off", ""].includes(s)) return false;
  return Boolean(v);
}

function fieldControl(f, d) {
  if (f.type === "enum_cards") {
    return `<div class="enum-cards">${(f.options || [])
      .map(
        (o) => `<label class="enum-card">
        <input type="radio" name="${f.key}" value="${o.value}" ${d[f.key] === o.value ? "checked" : ""} />
        <div class="t">${esc(o.title)}</div>
        <div class="d">${esc(o.desc || "")}</div>
      </label>`,
      )
      .join("")}</div>`;
  }
  if (f.type === "kind_checks") {
    const on = new Set(parseKindsDraft(d));
    return `<div class="chk-grid" data-kind-checks="${attr(f.key)}">${Object.keys(RENDER_KIND_LABELS)
      .map((k) => {
        const checked = on.has(k);
        return `<label class="chk"><input type="checkbox" data-settings-kind value="${k}" ${
          checked ? "checked" : ""
        }/> ${esc(RENDER_KIND_LABELS[k])}</label>`;
      })
      .join("")}</div>`;
  }
  if (f.type === "bool") {
    const [offL, onL] = f.boolLabels || ["关闭", "开启"];
    // 兼容 AstrBot 偶发字符串 "true"/"false"（Boolean("false")===true 会假脏/错显）
    const on = coerceFieldBool(d[f.key]);
    let html = `<label class="switch">
      <input type="checkbox" name="${f.key}" ${on ? "checked" : ""} />
      <span class="switch-track" aria-hidden="true"></span>
      <span class="switch-text">${on ? onL : offL}</span>
    </label>`;
    if (f.warn && on) {
      html += `<div class="field-warn">⚠ ${esc(f.warn)}</div>`;
    }
    return html;
  }
  // 仅真正敏感项（如 CF secret）：永远空 value，留空=不改
  if (f.sensitive || f.type === "password") {
    return `<input type="password" class="ctrl" name="${f.key}" value="" placeholder="${attr(
      "输入新值；留空不修改",
    )}" autocomplete="off" />`;
  }
  if (f.type === "enum") {
    return `<select class="ctrl" name="${f.key}">${(f.options || [])
      .map(
        (o) =>
          `<option value="${attr(o.value)}" ${d[f.key] === o.value ? "selected" : ""}>${esc(
            o.title || o.value,
          )}</option>`,
      )
      .join("")}</select>`;
  }
  // time 用整段文本输入（HH:MM），避免 type=time 分栏点选反人类
  if (f.type === "time") {
    return `<input type="text" class="ctrl mono" name="${f.key}" inputmode="numeric" autocomplete="off"
      spellcheck="false" maxlength="5" placeholder="${attr(f.placeholder || "23:00")}"
      value="${attr(d[f.key] ?? "")}" title="整段输入 HH:MM，如 23:00" />`;
  }
  const t = f.type === "number" ? "number" : "text";
  return `<input type="${t}" class="ctrl" name="${f.key}" value="${attr(d[f.key] ?? "")}" ${
    f.placeholder ? `placeholder="${attr(f.placeholder)}"` : ""
  } />`;
}

/** 单条 showIf 条件是否满足（布尔 eq 与 AstrBot 字符串 true/false 对齐） */
function matchShowIfClause(clause, d) {
  if (!clause || clause.key == null) return true;
  const cur = d[clause.key];
  const eq = clause.eq;
  if (typeof eq === "boolean") return coerceFieldBool(cur) === eq;
  return cur === eq;
}

/**
 * showIf：单条件 object，或 array 表示 AND（如 silent 开 且 push=定点 才显示时间）。
 */
function fieldVisible(f, d) {
  if (!f.showIf) return true;
  if (Array.isArray(f.showIf)) {
    return f.showIf.every((clause) => matchShowIfClause(clause, d));
  }
  return matchShowIfClause(f.showIf, d);
}

function renderField(f, d) {
  if (!fieldVisible(f, d)) return "";
  return `<div class="field">
    <div class="field-label-row">
      <div class="field-label">${esc(f.label)}</div>
      ${f.need ? `<span class="field-need">建议先填</span>` : ""}
    </div>
    ${f.help ? `<p class="field-help">${esc(f.help)}</p>` : ""}
    ${fieldControl(f, d)}
  </div>`;
}

function renderSettings() {
  if (!state.data) return;
  if (!state.draft) state.draft = structuredClone(state.data.config || {});
  renderTopConn();
  renderAlert();

  const groups = settingsGroups();
  if (!groups.length) {
    $("#settings-nav").innerHTML = "";
    $("#settings-form").innerHTML =
      `<p class="desc">设置表单没加载出来，请刷新页面重试。</p>`;
    return;
  }

  if (!groups.some((b) => b.id === state.settingsSection)) {
    state.settingsSection = groups[0].id;
  }

  $("#settings-nav").innerHTML = groups
    .map(
      (b) =>
        `<button type="button" data-sec="${b.id}" class="${
          state.settingsSection === b.id ? "is-active" : ""
        }">${esc(b.nav)}</button>`,
    )
    .join("");

  $$("#settings-nav button").forEach((b) => {
    b.onclick = () => {
      state.settingsSection = b.dataset.sec;
      renderSettings();
      document.getElementById(`sec-${b.dataset.sec}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    };
  });

  const d = state.draft;
  $("#settings-form").innerHTML = groups
    .map((b) => {
      const main = (b.fields || []).map((f) => renderField(f, d)).join("");
      const adv = b.advanced
        ? `<details class="advanced">
          <summary>
            <span class="adv-chevron" aria-hidden="true">▸</span>
            <span class="adv-summary-body">
              <span class="adv-title">${esc(b.advanced.title)}</span>
              <span class="adv-hint">点击展开</span>
            </span>
          </summary>
          <div class="adv-body">
            <p class="note">${esc(b.advanced.note || "")}</p>
            ${(b.advanced.fields || []).map((f) => renderField(f, d)).join("")}
          </div>
        </details>`
        : "";
      return `<section id="sec-${b.id}" class="settings-section">
      <h2>${esc(b.title)}</h2>
      <p class="desc">${esc(b.desc)}</p>
      ${main}${adv}
    </section>`;
    })
    .join("");

  const onFieldChange = (input) => {
    if (input.dataset.settingsKind != null) {
      const kinds = [...document.querySelectorAll("[data-settings-kind]")]
        .filter((el) => el.checked)
        .map((el) => el.value);
      state.draft.render_kinds = kinds.join(",") || "session_list,pending,message";
      state.draft.render_kinds_list = kinds;
      syncSettingsSaveStatus();
      return;
    }
    if (input.type === "checkbox") {
      state.draft[input.name] = input.checked;
      const sw = input.closest(".switch");
      const txt = sw?.querySelector(".switch-text");
      if (txt) {
        const f = allSettingsFields().find((x) => x.key === input.name);
        const [offL, onL] = f?.boolLabels || ["关闭", "开启"];
        txt.textContent = input.checked ? onL : offL;
      }
    } else if (input.type === "radio") state.draft[input.name] = input.value;
    else if (input.type === "number") state.draft[input.name] = Number(input.value);
    else state.draft[input.name] = input.value;
    if (
      input.name === "auto_approve_enabled" ||
      input.name === "auto_approve_silent" ||
      input.name === "output_level" ||
      input.name === "remind_pending" ||
      input.name === "render_mode" ||
      // 决定下方「固定推送时间」字段显隐，选中后需重渲染
      input.name === "auto_approve_summary_push"
    ) {
      renderSettings();
      return;
    }
    syncSettingsSaveStatus();
  };

  $$("#settings-form input, #settings-form select").forEach((input) => {
    input.onchange = () => onFieldChange(input);
    // 文本/密码边输边标脏
    if (input.type === "text" || input.type === "password" || input.type === "number") {
      input.oninput = () => onFieldChange(input);
    }
  });

  syncSettingsSaveStatus();
}

function allSettingsFields() {
  const list = [];
  for (const b of settingsGroups()) {
    list.push(...(b.fields || []));
    if (b.advanced?.fields) list.push(...b.advanced.fields);
  }
  return list;
}

export { renderSettings, allSettingsFields };
