/**
 * live / mock 数据层：snapshot、refresh、轮询、保存设置
 * 不直接 import pages/*，由 nav/app 注入 repaint，避免循环依赖
 */
import { hasBridge, initBridge, createApi } from "./api.js?v=3.0.1";
import { store, state } from "./state.js?v=3.0.1";
import { $ } from "./utils.js?v=3.0.1";
import { getApi, isLive, setLiveApi } from "./live.js?v=3.0.1";
import {
  renderTopConn,
  renderAlert,
  showAlert,
  toast,
  paintSaveStatus,
  setRefresh,
} from "./ui.js?v=3.0.1";

/** 当前页重绘（由 nav.js 注入） */
let _repaintPage = (_page) => {};
export function setRepaintPage(fn) {
  _repaintPage = typeof fn === "function" ? fn : () => {};
}

/** 设置页字段枚举（由 settings 模块注入，供 saveSettings 用） */
let _allSettingsFields = () => [];
export function setAllSettingsFields(fn) {
  _allSettingsFields = typeof fn === "function" ? fn : () => [];
}

function applySnapFromResult(res) {
  if (res && res.snapshot) {
    state.data = res.snapshot;
    return true;
  }
  return false;
}

async function fetchSnapshot(opts = {}) {
  if (!isLive() || !getApi()) {
    const snap = store.snap();
    if (Array.isArray(snap.hidden_windows)) {
      state.hiddenWindows = snap.hidden_windows
        .map((x) => String(x || "").trim())
        .filter(Boolean);
    }
    return snap;
  }
  const api = getApi();
  const snap = await api.sessionsSnapshot(opts);
  if (!snap || typeof snap !== "object") {
    throw new Error("sessions/snapshot 返回空或非对象");
  }
  if (!snap.columns) snap.columns = [];
  if (!snap.window_options) snap.window_options = [];
  if (!Array.isArray(snap.machines)) snap.machines = [];
  if (!Array.isArray(snap.hidden_windows)) snap.hidden_windows = [];
  // 同步到 state（可见窗口过滤只读这里，不碰 localStorage）
  state.hiddenWindows = snap.hidden_windows
    .map((x) => String(x || "").trim())
    .filter(Boolean);
  if (!snap.defaults) snap.defaults = { primary: null, flavor: {}, writable: false };
  if (!snap.connection) {
    snap.connection = {
      sse_status: "disconnected",
      endpoint_host: "—",
      conn_fail_count: 0,
      conn_error: "snapshot 未包含 connection",
    };
  }
  if (!snap.config || typeof snap.config !== "object" || snap.config._error) {
    try {
      const cfgRes = await api.config();
      const cfg = cfgRes?.config || cfgRes;
      if (cfg && typeof cfg === "object") snap.config = cfg;
    } catch (e) {
      console.warn("config fallback failed", e);
      if (!snap.config) snap.config = state.data?.config || {};
      snap.config_error = e.message || String(e);
    }
  }
  return snap;
}

/**
 * @param {{fresh?: boolean, silent?: boolean, repaint?: boolean}} opts
 * silent: 轮询/后台刷新——只更新数据与顶栏，不重绘表单
 * repaint: 强制按当前页重绘
 */
async function refresh(opts = {}) {
  try {
    state.data = await fetchSnapshot(opts);
  } catch (e) {
    console.error(e);
    if (!opts.silent) showAlert(e.message || String(e));
    return;
  }
  const live = new Set((state.data.sessions || []).map((s) => s.id));
  for (const id of [...state.selected]) if (!live.has(id)) state.selected.delete(id);
  renderTopConn();
  renderAlert();

  if (opts.silent && !opts.repaint) {
    // 概览页只刷机器负载区，避免整页重绘打断常用设置
    if (state.page === "overview") {
      import("./pages/overview.js?v=3.0.1")
        .then((m) => m.patchOverviewMachines?.())
        .catch(() => {});
    }
    return;
  }

  _repaintPage(state.page);
}

// 注入 ui 的唤醒按钮等路径
setRefresh(refresh);

const POLL_MS = 12000;
let pollTimer = null;
let pollInFlight = false;

function stopPolling() {
  if (pollTimer != null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startPolling() {
  stopPolling();
  if (!isLive()) return;
  pollTimer = setInterval(async () => {
    if (document.hidden) return;
    if (pollInFlight) return;
    if ($("#dlg")?.open) return;
    pollInFlight = true;
    try {
      await refresh({ silent: true, fresh: false });
    } catch (_) {
      /* 静默 */
    } finally {
      pollInFlight = false;
    }
  }, POLL_MS);
}

function onVisibility() {
  if (document.hidden) {
    stopPolling();
  } else if (isLive()) {
    refresh({ silent: true, fresh: false }).finally(() => startPolling());
  }
}

function wireLiveMutations() {
  if (!isLive() || !getApi()) return;
  const api = getApi();
  store.saveConfig = async (patch) => api.saveConfig(patch);
  store.wake = async () => {
    await api.wake();
  };
}

/** render_kinds 比较：忽略顺序与 list/string 形态 */
function normalizeKindsValue(v) {
  let arr;
  if (Array.isArray(v)) arr = v.map((x) => String(x || "").trim()).filter(Boolean);
  else
    arr = String(v ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  return [...new Set(arr)].sort().join(",");
}

/** 布尔兼容：AstrBot / JSON 可能给 true/"true"/1/"1" */
function coerceBool(v) {
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v !== 0 && !Number.isNaN(v);
  const s = String(v ?? "")
    .trim()
    .toLowerCase();
  if (["1", "true", "yes", "on"].includes(s)) return true;
  if (["0", "false", "no", "off", ""].includes(s)) return false;
  return Boolean(v);
}

/** HH:MM / HH:MM:SS → HH:MM（浏览器 time 控件可能带秒） */
function normalizeTimeValue(v) {
  const s = String(v ?? "").trim();
  const m = s.match(/^(\d{1,2}):(\d{2})(?::\d{2})?$/);
  if (!m) return s;
  return `${m[1].padStart(2, "0")}:${m[2]}`;
}

/**
 * 设置字段是否相等（避免 number/string、bool、kinds 顺序导致的假脏）。
 * @param {{key?: string, type?: string, schema_type?: string}} f
 */
function settingsValuesEqual(f, a, b) {
  const type = f?.type || f?.schema_type || "";
  const key = f?.key || "";
  if (key === "render_kinds" || type === "kind_checks") {
    return normalizeKindsValue(a) === normalizeKindsValue(b);
  }
  if (type === "bool" || type === "boolean" || f?.schema_type === "bool") {
    return coerceBool(a) === coerceBool(b);
  }
  if (
    type === "number" ||
    type === "int" ||
    f?.schema_type === "int" ||
    f?.schema_type === "float"
  ) {
    const na = a === "" || a == null ? null : Number(a);
    const nb = b === "" || b == null ? null : Number(b);
    if (na == null && nb == null) return true;
    if (na == null || nb == null) return false;
    if (Number.isNaN(na) && Number.isNaN(nb)) return true;
    return na === nb;
  }
  if (type === "time") {
    return normalizeTimeValue(a) === normalizeTimeValue(b);
  }
  // 文本：统一成字符串；null/undefined 当空串
  return String(a ?? "").trim() === String(b ?? "").trim();
}

/**
 * 是否「敏感密码」字段：表单永远留空，非空才表示要改。
 * access_token 已明文回显，走普通字段比较，不再走这条路径。
 */
function isSensitivePasswordField(f) {
  if (!f) return false;
  if (f.sensitive) return true;
  // 兜底：历史/未知 schema 里仍可能把 secret 标成 password
  return f.type === "password" && f.key !== "access_token";
}

/** 从 draft / 敏感输入收集相对当前 config 的 patch（无变更返回 {}） */
function buildSettingsPatch() {
  const prev = state.data?.config || {};
  const draft = state.draft;
  if (!draft) return {};

  // 勾选框在 DOM 上：只读，不在脏检查时改写 draft（避免顺序/副作用假脏）
  const kindBoxes = [...document.querySelectorAll("[data-settings-kind]")];
  let kindsFromDom = null;
  if (kindBoxes.length) {
    kindsFromDom = kindBoxes.filter((el) => el.checked).map((el) => el.value);
  }

  const patch = {};
  for (const f of _allSettingsFields()) {
    const key = f.key;
    if (!key) continue;

    // 敏感密码：不在 public_config 回显；仅当输入框有新值才算变更
    if (isSensitivePasswordField(f)) {
      const el = [...document.querySelectorAll("#settings-form input")].find(
        (inp) => inp.name === key,
      );
      const typed = el?.value ?? "";
      if (typed) patch[key] = typed;
      continue;
    }

    let cur = draft[key];
    if ((key === "render_kinds" || f.type === "kind_checks") && kindsFromDom) {
      cur = kindsFromDom.join(",") || "session_list,pending,message";
    }

    if (settingsValuesEqual(f, cur, prev[key])) continue;

    if (key === "render_kinds" || f.type === "kind_checks") {
      const kinds =
        kindsFromDom ||
        (Array.isArray(draft.render_kinds_list)
          ? draft.render_kinds_list
          : String(cur || "")
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean));
      const joined = Array.isArray(kinds)
        ? kinds.join(",") || "session_list,pending,message"
        : String(kinds || "session_list,pending,message");
      patch.render_kinds = joined;
      draft.render_kinds = joined;
      draft.render_kinds_list = Array.isArray(kinds)
        ? kinds
        : joined.split(",").filter(Boolean);
      continue;
    }

    if (
      f.type === "number" ||
      f.schema_type === "int" ||
      f.schema_type === "float"
    ) {
      const n = cur === "" || cur == null ? cur : Number(cur);
      patch[key] = n;
      draft[key] = n;
    } else if (f.type === "bool" || f.schema_type === "bool") {
      const b = coerceBool(cur);
      patch[key] = b;
      draft[key] = b;
    } else if (f.type === "time") {
      const t = normalizeTimeValue(cur);
      patch[key] = t;
      draft[key] = t;
    } else {
      patch[key] = cur;
    }
  }

  return patch;
}

function isSettingsDirty() {
  return Object.keys(buildSettingsPatch()).length > 0;
}

function paintSettingsSaveStatus(status) {
  paintSaveStatus($("#settings-save-status"), status);
}

function syncSettingsSaveStatus() {
  if (state.page !== "settings") return;
  if (isSettingsDirty()) {
    paintSettingsSaveStatus("dirty");
    return;
  }
  // 无脏：保留「已保存」；保存中不打断
  const el = $("#settings-save-status");
  const st = el?.dataset.state || "";
  if (st === "saved" || st === "saving") return;
  paintSettingsSaveStatus("");
}

/**
 * @returns {Promise<boolean>} 是否保存成功（无变更也算成功）
 */
async function saveSettings() {
  const patch = buildSettingsPatch();
  if (!Object.keys(patch).length) {
    paintSettingsSaveStatus("saved");
    toast("没有变更");
    return true;
  }
  paintSettingsSaveStatus("saving");
  try {
    if (isLive() && getApi()) {
      const res = await getApi().saveConfig(patch);
      if (res.reconnect_error || (res.reconnect_required && !res.reconnected)) {
        showAlert(
          res.message ||
            "已保存，但自动重连未成功 — 可到概览页点「重连」重试。",
        );
      }
    } else {
      store.saveConfig(patch);
    }
    state.draft = null;
    // silent：不整页重绘（避免冲掉「已保存」状态）；再手动重绘表单
    await refresh({ silent: true });
    _repaintPage("settings");
    paintSettingsSaveStatus("saved");
    return true;
  } catch (e) {
    paintSettingsSaveStatus("error");
    toast("保存失败: " + (e.message || e));
    return false;
  }
}

export {
  applySnapFromResult,
  fetchSnapshot,
  refresh,
  stopPolling,
  startPolling,
  onVisibility,
  wireLiveMutations,
  buildSettingsPatch,
  isSettingsDirty,
  paintSettingsSaveStatus,
  syncSettingsSaveStatus,
  saveSettings,
  hasBridge,
  initBridge,
  createApi,
  getApi,
  isLive,
  setLiveApi,
};
