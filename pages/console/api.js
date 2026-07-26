/**
 * Bridge API 封装。
 * - 有 window.AstrBotPluginPage：走真实插件 API
 * - 本地 file:// 或无 bridge：调用方应使用 mock
 */

export function hasBridge() {
  return Boolean(window.AstrBotPluginPage);
}

export async function initBridge() {
  const bridge = window.AstrBotPluginPage;
  if (!bridge) return null;
  const ctx = await bridge.ready();
  return { bridge, ctx };
}

function formatApiError(err, endpoint) {
  if (!err) return `API ${endpoint} 失败`;
  // axios / fetch-like
  const status = err.status || err.statusCode || err.response?.status;
  const body = err.response?.data || err.data || err.body;
  let detail = "";
  if (typeof body === "string") detail = body;
  else if (body && typeof body === "object") {
    detail = body.message || body.error || body.msg || body.detail || JSON.stringify(body).slice(0, 300);
  } else {
    detail = err.message || String(err);
  }
  if (status) return `API ${endpoint} → ${status}: ${detail}`;
  return `API ${endpoint} 失败: ${detail}`;
}

/** Dashboard 可能包一层 { code, data, message }，尽量解包到业务体 */
function unwrap(payload) {
  if (!payload || typeof payload !== "object") return payload;
  // 常见包装：{ code:0, data:{...} } / { status:"ok", data }
  if (payload.data != null && (payload.code === 0 || payload.code === 200 || payload.status === "ok" || payload.success === true)) {
    return payload.data;
  }
  // 有时 bridge 直接返回业务对象
  return payload;
}

export function createApi(bridge) {
  async function get(endpoint, params) {
    try {
      const raw = await bridge.apiGet(endpoint, params);
      return unwrap(raw);
    } catch (e) {
      const err = new Error(formatApiError(e, endpoint));
      err.cause = e;
      err.endpoint = endpoint;
      throw err;
    }
  }

  async function post(endpoint, body) {
    try {
      const raw = await bridge.apiPost(endpoint, body || {});
      return unwrap(raw);
    } catch (e) {
      const err = new Error(formatApiError(e, endpoint));
      err.cause = e;
      err.endpoint = endpoint;
      throw err;
    }
  }

  return {
    get,
    post,
    meta: () => get("meta"),
    // fresh=true 时强制打 HAPI；默认走服务端缓存 TTL，不唤醒 SSE
    overview: (opts = {}) => get("overview", opts.fresh ? { fresh: 1 } : undefined),
    config: () => get("config"),
    saveConfig: (patch) => post("config", patch),
    help: () => get("help"),
    docsList: () => get("docs"),
    docsGet: (docId) => get(`docs/${encodeURIComponent(docId)}`),
    machines: (opts = {}) => get("machines", opts.fresh ? { fresh: 1 } : undefined),
    wake: () => post("connection/wake"),
    reconnect: () => post("connection/reconnect"),
    sessionsSnapshot: (opts = {}) => get("sessions/snapshot", opts.fresh ? { fresh: 1 } : undefined),
    sessionDetail: (sid) => get(`sessions/${encodeURIComponent(sid)}`),
    setPermission: (sid, mode) => post(`sessions/${encodeURIComponent(sid)}/permission`, { mode }),
    bindSession: (sid, umo) => post(`sessions/${encodeURIComponent(sid)}/bind`, { umo }),
    lifecycle: (sid, action) => post(`sessions/${encodeURIComponent(sid)}/lifecycle`, { action }),
    batchLifecycle: (ids, action) => post("sessions/batch", { ids, action }),
    setPrimaryRoute: (umo, user_id) => post("routes/primary", { umo, user_id }),
    setFlavorRoute: (flavor, umo, user_id) => post("routes/flavor", { flavor, umo, user_id }),
    /** 会话页可见窗口：隐藏列表存插件 KV（iframe 无 localStorage） */
    getHiddenWindows: () => get("ui/hidden-windows"),
    setHiddenWindows: (hidden) => post("ui/hidden-windows", { hidden: hidden || [] }),
    /** 窗口 Focus 模式开关（与聊天 /hapi focus 同源） */
    setWindowFocus: (umo, enabled) => post("windows/focus", { umo, enabled }),
    /** 会话创建模板（与聊天 /hapi create <模板名> 同源） */
    getSessionTemplates: () => get("ui/session-templates"),
    setSessionTemplates: (templates) => post("ui/session-templates", { templates: templates || [] }),
    /** 推送卡片：能力元数据 / 实卡预览 / 勾选安装字体或依赖 */
    renderMeta: () => get("render/meta"),
    renderPreview: (body) => post("render/preview", body || {}),
    renderTextTest: (body) => post("render/text-test", body || {}),
    /** body: { ids: ["font_noto_sc","dep_pillow"], force?: bool } */
    renderInstall: (body) => post("render/install", body || {}),
  };
}
