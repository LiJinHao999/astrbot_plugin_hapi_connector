/**
 * Shell 渲染、toast、确认框、特效层
 */
import { PAGE_META } from "./constants.js?v=3.0.1";
import { state, store } from "./state.js?v=3.0.1";
import { $, $$, esc } from "./utils.js?v=3.0.1";
import { getApi, isLive } from "./live.js?v=3.0.1";

/** 由 data.js 注入，避免 ui ↔ data 循环 */
let _refresh = async () => {};
export function setRefresh(fn) {
  _refresh = typeof fn === "function" ? fn : async () => {};
}
async function refresh(opts) {
  return _refresh(opts);
}

function connLabel(c) {
  const st = c?.sse_status || "disconnected";
  if (st === "connected") return "已连接";
  if (st === "hibernated") return "已休眠";
  if (st === "reconnecting") return "重连中";
  if (st === "disconnected") return "未连接";
  return st;
}

function connIsOk(c) {
  return c?.sse_status === "connected";
}

function renderTopConn() {
  const c = state.data?.connection || {};
  const ok = connIsOk(c);
  const label = connLabel(c);
  const host = c.endpoint_host || "—";

  $("#top-conn").className = `conn-chip ${ok ? "ok" : "bad"}`;
  $("#top-conn").innerHTML = `<span class="dot"></span>${esc(label)} · ${esc(host)}`;

  const foot = $("#sidebar-conn");
  foot.className = `sidebar-footer ${ok ? "ok" : "bad"}`;
  $("#sidebar-conn-text").textContent = label;
}

function renderAlert() {
  const c = state.data?.connection || {};
  const el = $("#alert");
  if (c.sse_status === "hibernated") {
    el.hidden = false;
    el.innerHTML = `<div class="alert alert-danger">
      <span>插件 SSE 已休眠（失败 ${c.conn_fail_count || 0} 次）${c.conn_error ? " · " + esc(c.conn_error) : ""}。这是插件↔HAPI 的连接，不是网页本身。</span>
      <button type="button" class="btn btn-sm" id="btn-wake">唤醒</button>
    </div>`;
    $("#btn-wake").onclick = async () => {
      try {
        if (isLive() && getApi()) await getApi().wake();
        else store.wake();
        await refresh();
      } catch (e) {
        toast("唤醒失败: " + (e.message || e));
      }
    };
  } else if (c.sse_status === "reconnecting" && c.conn_error) {
    el.hidden = false;
    el.innerHTML = `<div class="alert alert-danger">
      <span>插件 SSE 重连中（失败 ${c.conn_fail_count || 0} 次）· ${esc(c.conn_error)}</span>
    </div>`;
  } else if (c.sse_status === "disconnected") {
    el.hidden = false;
    el.innerHTML = `<div class="alert alert-danger">
      <span>还没连上 HAPI 服务${c.conn_error ? " · " + esc(c.conn_error) : "（可能是插件刚启动还没连好，或「设置」里的服务地址 / 令牌不对）"}。可以点概览页的「重连」按钮，或在 AstrBot 里重载插件。</span>
    </div>`;
  } else if (state.data?.error) {
    el.hidden = false;
    el.innerHTML = `<div class="alert alert-danger"><span>部分数据加载异常: ${esc(state.data.error)}</span></div>`;
  } else {
    el.hidden = true;
    el.innerHTML = "";
  }
}

function setPageChrome(page) {
  const meta = PAGE_META[page] || PAGE_META.overview;
  $("#page-title").textContent = meta.title;
  $("#page-desc").textContent = meta.desc;
  $$("#nav .side-link").forEach((b) => {
    b.classList.toggle("is-active", b.dataset.page === page);
  });
  $$(".view").forEach((v) => {
    v.hidden = v.dataset.view !== page;
  });
}

function closeSidebar() {
  $("#app")?.classList.remove("sidebar-open");
  const scrim = $("#scrim");
  if (scrim) scrim.hidden = true;
}


export { connLabel, connIsOk, renderTopConn, renderAlert, setPageChrome, closeSidebar };

const FX_STORAGE_KEY = "hapi_console_fx";
const THEME_STORAGE_KEY = "hapi_console_theme"; // "light" | "dark"

/** 内存态：避免 localStorage 读失败 / 重复 ensure 时状态乱 */
let _fxOn = null;
/** 内存态主题：true=暗，false=亮；默认亮 */
let _darkOn = null;

/** 动效默认关闭；localStorage "1"=开，其它=关 */
function isFxEnabled() {
  if (_fxOn != null) return _fxOn;
  try {
    _fxOn = localStorage.getItem(FX_STORAGE_KEY) === "1";
  } catch (_) {
    _fxOn = false;
  }
  return _fxOn;
}

function setFxEnabled(on) {
  _fxOn = Boolean(on);
  try {
    localStorage.setItem(FX_STORAGE_KEY, _fxOn ? "1" : "0");
  } catch (_) {
    /* ignore */
  }
  applyFxEnabled(_fxOn);
}

function applyFxEnabled(on) {
  on = Boolean(on);
  _fxOn = on;
  const layer = document.getElementById("fx-layer");
  const btn = document.getElementById("fx-toggle");
  document.body.classList.toggle("has-fx", on);
  document.body.classList.toggle("fx-off", !on);
  if (layer) {
    if (on) {
      layer.removeAttribute("hidden");
      layer.style.display = "";
    } else {
      layer.setAttribute("hidden", "");
      layer.style.display = "none";
    }
  }
  if (btn) {
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.title = on ? "关闭终端动效（粒子 / 扫描线）" : "开启终端动效（粒子 / 扫描线）";
    btn.textContent = on ? "动效 · 开" : "动效 · 关";
  }
}

/** 暗色是否开启；默认 false（亮色）。兼容旧值 auto → 亮 */
function isDarkEnabled() {
  if (_darkOn != null) return _darkOn;
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY);
    // 旧版 auto / 空 → 亮色
    _darkOn = v === "dark";
  } catch (_) {
    _darkOn = false;
  }
  return _darkOn;
}

function setDarkEnabled(on) {
  _darkOn = Boolean(on);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, _darkOn ? "dark" : "light");
  } catch (_) {
    /* ignore */
  }
  applyTheme();
}

function applyTheme() {
  const dark = isDarkEnabled();
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  const btn = document.getElementById("theme-toggle");
  if (btn) {
    btn.textContent = dark ? "主题 · 暗" : "主题 · 亮";
    btn.setAttribute("aria-pressed", dark ? "true" : "false");
    btn.title = dark ? "切换为亮色主题" : "切换为暗色主题";
  }
}

function ensureChromeControls() {
  let bar = document.getElementById("chrome-toggles");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "chrome-toggles";
    bar.className = "chrome-toggles";
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", "界面开关");
    document.body.appendChild(bar);
  }

  if (!document.getElementById("fx-toggle")) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.id = "fx-toggle";
    btn.className = "fx-toggle chrome-toggle";
    btn.setAttribute("aria-label", "切换终端动效");
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      setFxEnabled(!isFxEnabled());
    });
    bar.appendChild(btn);
  }

  if (!document.getElementById("theme-toggle")) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.id = "theme-toggle";
    btn.className = "theme-toggle chrome-toggle";
    btn.setAttribute("aria-label", "切换亮暗主题");
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      setDarkEnabled(!isDarkEnabled());
    });
    bar.appendChild(btn);
  }
}

/**
 * 创建粒子层 + 左下角开关栏。默认关闭动效。
 * 无大绿团 / vignette；仅扫描线 + 光束 + 粒子。
 */
function ensureFxLayer() {
  if (!document.getElementById("fx-layer")) {
    const layer = document.createElement("div");
    layer.id = "fx-layer";
    layer.className = "fx-layer";
    layer.setAttribute("aria-hidden", "true");
    const dots = Array.from({ length: 56 }, (_, i) => {
      const left = ((i * 37 + (i % 11) * 2) % 100) + (i % 5) * 0.2;
      const delay = ((i * 0.31) % 9).toFixed(2);
      const dur = (7 + (i % 10) * 1.2).toFixed(1);
      const size = 2 + (i % 3);
      const dx = ((i % 9) - 4) * 8;
      return `<span class="fx-dot" style="--x:${left.toFixed(1)}%;--d:${delay}s;--t:${dur}s;--s:${size}px;--dx:${dx}px"></span>`;
    }).join("");
    layer.innerHTML = `
      <div class="fx-scan"></div>
      <div class="fx-beam"></div>
      <div class="fx-particles">${dots}</div>
    `;
    document.body.prepend(layer);
  }

  ensureChromeControls();
  applyFxEnabled(isFxEnabled());
  applyTheme();
}

export {
  ensureFxLayer,
  isFxEnabled,
  setFxEnabled,
  isDarkEnabled,
  setDarkEnabled,
  applyTheme,
};

function askConfirm(message, opts = {}) {
  const msg = String(message || "确定？");
  const danger = Boolean(opts.danger);
  const title = opts.title || "确认";
  const yesText = opts.yes || "确定";
  const noText = opts.no || "取消";
  const discardText = opts.discard || "";

  return new Promise((resolve) => {
    const dlg = $("#dlg-confirm");
    const msgEl = $("#dlg-confirm-msg");
    const titleEl = $("#dlg-confirm-title");
    const yes = $("#dlg-confirm-yes");
    const no = $("#dlg-confirm-no");
    const discard = $("#dlg-confirm-discard");
    const x = $("#dlg-confirm-x");
    if (!dlg || !msgEl || !yes || !no) {
      toast(msg);
      resolve(true);
      return;
    }
    if (titleEl) titleEl.textContent = title;
    msgEl.textContent = msg;
    yes.textContent = yesText;
    no.textContent = noText;
    yes.classList.toggle("btn-danger", danger);
    yes.classList.toggle("btn-primary", !danger);
    if (discard) {
      if (discardText) {
        discard.hidden = false;
        discard.textContent = discardText;
      } else {
        discard.hidden = true;
        discard.textContent = "丢弃更改";
      }
    }

    let settled = false;
    const finish = (v) => {
      if (settled) return;
      settled = true;
      yes.onclick = null;
      no.onclick = null;
      if (discard) discard.onclick = null;
      if (x) x.onclick = null;
      dlg.removeEventListener("close", onClose);
      try {
        if (dlg.open) dlg.close();
      } catch (_) {}
      resolve(v);
    };
    const onClose = () => finish(false);
    yes.onclick = () => finish(true);
    no.onclick = () => finish(false);
    if (discard) {
      discard.onclick = () => finish("discard");
    }
    if (x) x.onclick = () => finish(false);
    dlg.addEventListener("close", onClose);
    try {
      if (dlg.open) dlg.close();
      dlg.showModal();
    } catch (e) {
      console.error("confirm dialog failed", e);
      toast(msg);
      resolve(true);
    }
  });
}

/**
 * 离开页未保存确认。
 * @returns {Promise<"save"|"discard"|"cancel">}
 */
async function askUnsavedLeave(message, opts = {}) {
  const r = await askConfirm(message || "有未保存的更改，离开前如何处理？", {
    title: opts.title || "未保存的更改",
    yes: opts.yes || "保存",
    discard: opts.discard || "丢弃更改",
    no: opts.no || "取消",
  });
  if (r === true) return "save";
  if (r === "discard") return "discard";
  return "cancel";
}

/**
 * 更新保存按钮左侧状态文案。
 * state: "" | "dirty" | "saved" | "saving" | "error"
 */
function paintSaveStatus(el, status) {
  if (!el) return;
  const st = String(status || "");
  el.dataset.state = st;
  if (st === "dirty") el.textContent = "有更改未保存";
  else if (st === "saved") el.textContent = "已保存";
  else if (st === "saving") el.textContent = "保存中…";
  else if (st === "error") el.textContent = "保存失败";
  else el.textContent = "";
}

function toast(msg) {
  let el = $("#global-toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "global-toast";
    el.className = "global-toast";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    document.body.appendChild(el);
  }
  el.hidden = false;
  el.textContent = String(msg || "");
  el.classList.add("is-show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.classList.remove("is-show");
    el.hidden = true;
  }, 2400);
}


export { askConfirm, askUnsavedLeave, paintSaveStatus, toast };

export function showAlert(msg) {
  const el = $("#alert");
  if (!el) return;
  el.hidden = false;
  el.innerHTML = `<div class="alert alert-danger"><span>${esc(msg)}</span></div>`;
}
