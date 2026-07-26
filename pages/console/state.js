/**
 * 本地 mock store + 全局 UI state + 会话页窗口可见性
 */
import { UMO } from "./constants.js?v=3.0.1";
import { resolve, parseUmo, attr, esc } from "./utils.js?v=3.0.1";
import { CONFIG_SCHEMA_FALLBACK } from "./settings_schema_fallback.js?v=3.0.1";

function createStore() {
  const sessions = [
    {
      id: "a1b2c3d4e5f67890",
      title: "重构鉴权中间件",
      flavor: "claude",
      path: "/home/dev/proj-auth",
      active: true,
      thinking: true,
      pending: 2,
      permissionMode: "default",
      modelMode: "opus",
    },
    {
      id: "b2c3d4e5f6789012",
      title: "补 session 列表单测",
      flavor: "claude",
      path: "/home/dev/proj-auth",
      active: true,
      thinking: false,
      pending: 0,
      permissionMode: "acceptEdits",
      modelMode: "sonnet",
    },
    {
      id: "c3d4e5f678901234",
      title: "API 文档生成",
      flavor: "codex",
      path: "/home/dev/docs-site",
      active: true,
      thinking: false,
      pending: 1,
      permissionMode: "default",
      modelMode: "default",
    },
    {
      id: "d4e5f67890123456",
      title: "scratch 实验",
      flavor: "claude",
      path: "/tmp/scratch",
      active: false,
      thinking: false,
      pending: 0,
      permissionMode: "default",
      modelMode: "default",
    },
    {
      id: "e5f6789012345678",
      title: "杂项脚本",
      flavor: "opencode",
      path: "/home/dev/misc",
      active: true,
      thinking: false,
      pending: 0,
      permissionMode: "default",
      modelMode: "default",
    },
    {
      id: "f678901234567890",
      title: "Gemini 旧任务",
      flavor: "gemini",
      path: "/orphan",
      active: false,
      thinking: false,
      pending: 0,
      permissionMode: "default",
      modelMode: "default",
      forceNone: true,
    },
  ];
  const owners = {
    a1b2c3d4e5f67890: UMO.groupA,
    b2c3d4e5f6789012: UMO.groupA,
    c3d4e5f678901234: UMO.groupB,
  };
  const defaults = {
    primary: UMO.private,
    flavor: { claude: UMO.private, codex: UMO.groupB },
  };
  // 默认值来自 _conf_schema；mock 只补运行期视图字段
  const config = {
    ...CONFIG_SCHEMA_FALLBACK.defaults,
    hapi_endpoint: "http://127.0.0.1:3006",
    access_token: "demo-token:default",
    access_token_configured: true,
    access_token_namespace: "default",
    hapi_web_url: "http://127.0.0.1:3006/?hub=http%3A%2F%2F127.0.0.1%3A3006&token=demo-token%3Adefault",
    hapi_web_url_safe: "http://127.0.0.1:3006/",
    cf_access_enabled: false,
    cf_access_client_secret_configured: false,
    poke_actions: [
      { id: "approve", label: "批准待审", desc: "批准当前窗口可见的非 question 权限请求", cmd: "/hapi a", emoji: "✅" },
      { id: "pending", label: "查看待审", desc: "列出当前窗口待审批请求", cmd: "/hapi pending", emoji: "📋" },
      { id: "list", label: "会话列表", desc: "列出当前窗口可见的 session", cmd: "/hapi list", emoji: "☰" },
      { id: "status", label: "当前状态", desc: "查看当前绑定 session 状态", cmd: "/hapi s", emoji: "◎" },
      { id: "stop", label: "中止当前", desc: "中止当前窗口生效中的 session", cmd: "/hapi abort", emoji: "⏹" },
      { id: "output_cycle", label: "切换推送级别", desc: "在 silence → simple → summary → detail 间循环", emoji: "📢" },
      { id: "none", label: "仅确认（无业务）", desc: "提示已收到戳一戳，不执行业务动作", emoji: "👋" },
    ],
    cmd_keyword_maps_list: [
      { keywords: ["stop", "停"], command: "stop", args: "" },
      { keywords: ["sw"], command: "sw", args: "" },
      { keywords: ["cl"], command: "send", args: "/clear" },
      { keywords: ["继续"], command: "send", args: "继续" },
      { keywords: ["专注"], command: "focus", args: "on" },
      { keywords: ["退出专注"], command: "focus", args: "off" },
      { keywords: ["hapi指令别名"], command: "alias", args: "" },
    ],
    render_kinds_list: String(CONFIG_SCHEMA_FALLBACK.defaults.render_kinds || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
    render_engine: {
      pillow: false,
      install_hint: "pip install Pillow",
      installable: [
        { id: "font_noto_sc", group: "font", label: "中文字体 Noto Sans SC", desc: "下载到插件 assets/fonts/（约 8MB）", installed: false, approx_label: "约 8MB" },
        { id: "dep_pillow", group: "dep", label: "Pillow（出图引擎）", desc: "pip install Pillow（约 3MB）", installed: false, approx_label: "约 3MB" },
        { id: "dep_matplotlib", group: "dep", label: "matplotlib（公式）", desc: "pip install matplotlib（约 40MB）", installed: false, approx_label: "约 40MB" },
      ],
    },
    card_style: {
      preset: CONFIG_SCHEMA_FALLBACK.defaults.card_style_preset || "terminal_light",
      width: CONFIG_SCHEMA_FALLBACK.defaults.card_width || 720,
      bg: CONFIG_SCHEMA_FALLBACK.defaults.card_bg || "#f7f4ea",
      fg: CONFIG_SCHEMA_FALLBACK.defaults.card_fg || "#14120f",
      accent: CONFIG_SCHEMA_FALLBACK.defaults.card_accent || "#0f6b3c",
      density: CONFIG_SCHEMA_FALLBACK.defaults.card_density || "comfortable",
      show_brand: Boolean(CONFIG_SCHEMA_FALLBACK.defaults.card_show_brand),
      mono: Boolean(CONFIG_SCHEMA_FALLBACK.defaults.card_mono),
      font_scale: (Number(CONFIG_SCHEMA_FALLBACK.defaults.card_font_scale) || 112) / 100,
    },
  };

  const conn = { sse_status: "connected", conn_fail_count: 0, conn_error: null };

  function list() {
    return sessions.map((s) => {
      const r = resolve(s, owners, defaults);
      return {
        ...s,
        id_short: s.id.slice(0, 8),
        bound_umo: owners[s.id] || null,
        effective_umo: r.umo,
        layer: r.layer,
      };
    });
  }

  function columns() {
    const rows = list();
    const map = new Map();
    const ensure = (umo) => {
      const k = umo || "__none__";
      if (!map.has(k)) {
        map.set(k, {
          umo,
          title: umo ? wTitle(umo) : "未投递",
          is_primary: umo === defaults.primary,
          flavors: Object.entries(defaults.flavor)
            .filter(([, v]) => v === umo)
            .map(([f]) => f),
          sessions: [],
        });
      }
      return map.get(k);
    };
    ensure(defaults.primary);
    for (const u of Object.values(defaults.flavor)) ensure(u);
    for (const u of Object.values(owners)) ensure(u);
    for (const s of rows) ensure(s.effective_umo).sessions.push(s);
    return [...map.values()].sort((a, b) => {
      if (!a.umo) return 1;
      if (!b.umo) return -1;
      return b.sessions.length - a.sessions.length;
    });
  }

  function umos() {
    return [...new Set([defaults.primary, ...Object.values(defaults.flavor), ...Object.values(owners)])]
      .filter(Boolean)
      .map((u) => ({ umo: u, title: wTitle(u) }));
  }

  return {
    snap() {
      const rows = list();
      return {
        connection: { ...conn, endpoint_host: "127.0.0.1:3006" },
        metrics: {
          active: rows.filter((s) => s.active).length,
          thinking: rows.filter((s) => s.thinking).length,
          pending: rows.reduce((n, s) => n + s.pending, 0),
          unrouted: rows.filter((s) => s.layer === "none").length,
          total: rows.length,
          machines: 1,
        },
        sessions: rows,
        machines: [
          {
            id: "demo-machine-1",
            label: "Somn…",
            host: "somn.local",
            platform: "linux",
            platform_label: "Linux",
            active: true,
            runner_status: "running",
            health: {
              collected_at: Date.now(),
              cpu_count: 20,
              load1m: 5.2,
              cpu_percent: 18,
              memory_percent: 59,
              uptime_seconds: 4 * 86400 + 2 * 3600,
            },
            cli_version: "demo",
          },
        ],
        columns: columns(),
        defaults: { primary: defaults.primary, flavor: { ...defaults.flavor } },
        window_options: umos(),
        hidden_windows: Array.isArray(state.hiddenWindows) ? [...state.hiddenWindows] : [],
        config: { ...config },
      };
    },
    bind(sid, umo) {
      if (!umo) delete owners[sid];
      else {
        owners[sid] = umo;
        const s = sessions.find((x) => x.id === sid);
        if (s) s.forceNone = false;
      }
    },
    setPermission(sid, mode) {
      const s = sessions.find((x) => x.id === sid);
      if (s) s.permissionMode = mode;
    },
    setDefault(kind, umo) {
      if (kind === "primary") defaults.primary = umo || null;
      else if (umo) defaults.flavor[kind] = umo;
      else delete defaults.flavor[kind];
    },
    lifecycle(sid, action) {
      const idx = sessions.findIndex((x) => x.id === sid);
      if (idx < 0) return {};
      const s = sessions[idx];
      if (action === "resume") {
        const newId = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
        const bound = owners[sid] || null;
        sessions[idx] = { ...s, id: newId, active: true, thinking: false, forceNone: false };
        delete owners[sid];
        if (bound) owners[newId] = bound;
        return { new_id: newId };
      }
      if (action === "archive") {
        s.active = false;
        s.thinking = false;
      }
      if (action === "delete") {
        sessions.splice(idx, 1);
        delete owners[sid];
      }
      return {};
    },
    saveConfig(patch) {
      for (const [k, v] of Object.entries(patch)) {
        if ((k === "access_token" || k === "cf_access_client_secret") && !v) continue;
        if (k === "access_token") {
          config.access_token_configured = true;
          if (String(v).includes(":")) config.access_token_namespace = String(v).split(":")[1];
          continue;
        }
        if (k === "cf_access_client_secret") {
          config.cf_access_client_secret_configured = true;
          continue;
        }
        if (k === "cf_access_client_id") {
          config.cf_access_client_id = v;
          config.cf_access_enabled = Boolean(String(v || "").trim());
          continue;
        }
        if (k === "cmd_keyword_maps" || k === "cmd_keyword_maps_list") {
          const maps = (Array.isArray(v)
            ? v
            : typeof v === "string"
              ? (() => {
                  try {
                    return JSON.parse(v || "[]");
                  } catch (_) {
                    return [];
                  }
                })()
              : []
          ).map((m) => {
            const entry = {
              keywords: [...(m.keywords || [])],
              command: m.command || "",
            };
            if (m.args) entry.args = m.args;
            return entry;
          });
          config.cmd_keyword_maps_list = maps;
          config.cmd_keyword_maps = JSON.stringify(maps);
          continue;
        }
        config[k] = v;
      }
    },
    wake() {
      conn.sse_status = "connected";
      conn.conn_fail_count = 0;
      conn.conn_error = null;
    },
    hibernate() {
      conn.sse_status = "hibernated";
      conn.conn_fail_count = 10;
      conn.conn_error = "达到重连上限";
    },
  };
}


const store = createStore();
const state = {
  page: "overview",
  focusWindow: null,
  selected: new Set(),
  draft: null,
  settingsSection: "connection",
  docsDocId: "install",
  helpTopic: "session",
  helpQuery: "",
  /** WebUI 隐藏的推送窗口 UMO 列表（来自插件 KV，见 snapshot.hidden_windows） */
  hiddenWindows: [],
  data: null,
};

/** 窗口展示标题（依赖 state.data.window_options） */
function wTitle(u) {
  if (!u) return "—";
  const opt = (state.data?.window_options || []).find((w) => w.umo === u);
  if (opt?.title) return opt.title;
  const { platform, kindLabel, sid } = parseUmo(u);
  const name = opt?.name || "";
  const tail = name || sid || u;
  return `Bot:${platform || "bot"}-${kindLabel}-${tail}`;
}



function ruleText() {
  return "";
}


/**
 * WebUI「管理可见窗口」隐藏列表。
 * AstrBot Page iframe 无 localStorage（sandbox 无 allow-same-origin），
 * 唯一数据源：插件 KV `webui_hidden_windows`，经 snapshot / ui/hidden-windows 下发。
 * 前端内存缓存 state.hiddenWindows，应用时 POST 落盘后再重绘。
 */
function loadHiddenWindows() {
  const arr = state.hiddenWindows;
  if (!Array.isArray(arr)) return new Set();
  return new Set(arr.map((x) => String(x || "").trim()).filter(Boolean));
}

/** 仅更新内存；落盘请走 API setHiddenWindows */
function setHiddenWindowsLocal(hiddenSet) {
  state.hiddenWindows = [...(hiddenSet || [])]
    .map((x) => String(x || "").trim())
    .filter(Boolean);
}

/** @deprecated 兼容旧调用名 → 只改内存 */
function saveHiddenWindows(hiddenSet) {
  setHiddenWindowsLocal(hiddenSet);
}

/** 本页下拉/左侧是否显示该窗口。keep 里的 umo 始终保留（当前已选值，避免选中项消失）。 */
function isWindowShown(umo, keep = null) {
  if (!umo) return true;
  const u = String(umo);
  if (keep && (keep === u || (keep instanceof Set && keep.has(u)))) return true;
  return !loadHiddenWindows().has(u);
}

/** 合并 window_options + columns，去重，供管理弹窗 / 下拉用 */
function allKnownWindows() {
  const map = new Map();
  for (const w of state.data?.window_options || []) {
    if (w?.umo) map.set(String(w.umo), { umo: String(w.umo), title: w.title || wTitle(w.umo) });
  }
  for (const col of state.data?.columns || []) {
    if (!col?.umo) continue;
    const u = String(col.umo);
    if (!map.has(u)) map.set(u, { umo: u, title: col.title || wTitle(u) });
  }
  return [...map.values()].sort((a, b) => String(a.title).localeCompare(String(b.title), "zh"));
}

/** 某窗口上的 session 统计：显式绑定 / 有效投递 / 运行中 */
function windowSessionStats(umo) {
  const u = String(umo || "");
  let bound = 0;
  let effective = 0;
  let active = 0;
  if (!u) return { bound, effective, active };
  for (const s of state.data?.sessions || []) {
    if (s?.bound_umo && String(s.bound_umo) === u) bound++;
    if (s?.effective_umo && String(s.effective_umo) === u) {
      effective++;
      if (s.active) active++;
    }
  }
  return { bound, effective, active };
}

/** 弹窗列表里展示的绑定数文案 */
function formatWindowBindMeta(umo) {
  const { bound, effective, active } = windowSessionStats(umo);
  const parts = [];
  parts.push(`绑 ${bound}`);
  if (effective !== bound) parts.push(`投递 ${effective}`);
  if (active > 0) parts.push(`运行 ${active}`);
  return parts.join(" · ");
}

/** 本页下拉选项（隐藏的不出现；keep 里的已选值强制保留） */
function visibleWindowOptions(keepUmos = []) {
  const keep = new Set((keepUmos || []).filter(Boolean).map(String));
  return allKnownWindows().filter((w) => isWindowShown(w.umo, keep));
}

function groupWindowsByBot(wins) {
  const groups = new Map();
  for (const w of wins) {
    const { platform } = parseUmo(w.umo);
    const bot = platform || "其它";
    if (!groups.has(bot)) groups.set(bot, []);
    groups.get(bot).push(w);
  }
  return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0], "zh"));
}

function bindSelect(s) {
  const opts = visibleWindowOptions([s.bound_umo])
    .map(
      (w) =>
        `<option value="${attr(w.umo)}" ${s.bound_umo === w.umo ? "selected" : ""}>${esc(w.title)}</option>`,
    )
    .join("");
  return `<option value="" ${s.bound_umo ? "" : "selected"}>按推送设置</option>${opts}`;
}


export {
  store,
  state,
  setHiddenWindowsLocal,
  createStore,
  wTitle,
  ruleText,
  loadHiddenWindows,
  saveHiddenWindows,
  isWindowShown,
  allKnownWindows,
  windowSessionStats,
  formatWindowBindMeta,
  visibleWindowOptions,
  groupWindowsByBot,
  bindSelect,
};
