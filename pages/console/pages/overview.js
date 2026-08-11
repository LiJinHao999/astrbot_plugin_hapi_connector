/**
 * 概览页：连接指标 + 机器负载 + 常用设置
 */
import { OUTPUT_LEVELS, BUSY_AGENT_PUSH_LEVELS } from "../constants.js?v=3.0.1";
import { state, store, wTitle } from "../state.js?v=3.0.1";
import { $, $$, esc, attr } from "../utils.js?v=3.0.1";
import {
  renderTopConn,
  renderAlert,
  ensureFxLayer,
  toast,
  askConfirm,
  connIsOk,
  connLabel,
} from "../ui.js?v=3.0.1";
import { refresh } from "../data.js?v=3.0.1";
import { isLive, getApi } from "../live.js?v=3.0.1";
import { go } from "../go.js?v=3.0.1";

/** 对齐 HAPI 官方阈值：≥90 critical，≥75 warn */
function percentTone(v) {
  if (v == null || Number.isNaN(Number(v))) return "unknown";
  const n = Number(v);
  if (n >= 90) return "critical";
  if (n >= 75) return "warn";
  return "ok";
}

function loadTone(load1m, cpuCount) {
  if (load1m == null) return "unknown";
  const cores = cpuCount && cpuCount > 0 ? cpuCount : 1;
  const ratio = Number(load1m) / cores;
  if (ratio >= 1.5) return "critical";
  if (ratio >= 1) return "warn";
  return "ok";
}

function worstTone(...tones) {
  if (tones.includes("critical")) return "critical";
  if (tones.includes("warn")) return "warn";
  if (tones.includes("unknown") && !tones.some((t) => t === "ok")) return "unknown";
  return "ok";
}

function statusLabel(tone) {
  if (tone === "critical") return "偏高";
  if (tone === "warn") return "升高";
  if (tone === "ok") return "健康";
  return "未知";
}

function formatUptime(sec) {
  if (sec == null || !Number.isFinite(Number(sec)) || Number(sec) < 0) return null;
  const s = Math.floor(Number(sec));
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return `${s}s`;
}

function formatLoad(load1m, cpuCount) {
  if (load1m == null) return null;
  const n = Number(load1m);
  if (!Number.isFinite(n)) return null;
  if (cpuCount && cpuCount > 0) return `${n.toFixed(1)}/${cpuCount}`;
  return n.toFixed(1);
}

function presentHealth(machine) {
  const h = machine?.health;
  if (!h) {
    return {
      metrics: [],
      overallTone: "unknown",
      status: "未知",
      loadDetail: null,
      uptimeDetail: null,
      cpuCount: null,
    };
  }
  const metrics = [];
  const tones = [];
  if (h.cpu_percent != null) {
    const tone = percentTone(h.cpu_percent);
    metrics.push({ id: "cpu", label: "CPU", percent: Math.round(h.cpu_percent), tone });
    tones.push(tone);
  }
  if (h.memory_percent != null) {
    const tone = percentTone(h.memory_percent);
    metrics.push({ id: "ram", label: "RAM", percent: Math.round(h.memory_percent), tone });
    tones.push(tone);
  }
  const loadDetail =
    machine.platform !== "win32" ? formatLoad(h.load1m, h.cpu_count) : null;
  if (loadDetail != null) tones.push(loadTone(h.load1m, h.cpu_count));
  const uptimeDetail = formatUptime(h.uptime_seconds);
  const overall =
    metrics.length || loadDetail != null
      ? worstTone(...(tones.length ? tones : ["unknown"]))
      : "unknown";
  return {
    metrics,
    overallTone: overall,
    status: statusLabel(overall),
    loadDetail,
    uptimeDetail,
    cpuCount: h.cpu_count,
  };
}

function meterBar(label, percent, tone) {
  const p = Math.max(0, Math.min(100, Number(percent) || 0));
  const w = Math.max(4, p);
  return `<div class="mh-meter" title="${esc(label)} ${p}%">
    <span class="mh-meter-lab">${esc(label)}</span>
    <span class="mh-meter-track" aria-hidden="true"><span class="mh-meter-fill tone-${esc(tone)}" style="width:${w}%"></span></span>
    <span class="mh-meter-val">${p}%</span>
  </div>`;
}

function machineCard(m) {
  const p = presentHealth(m);
  const hostSub =
    m.host && m.host.toLowerCase() !== String(m.label || "").toLowerCase()
      ? m.host
      : "";
  const meters = p.metrics.map((x) => meterBar(x.label, x.percent, x.tone)).join("");
  const chips = [
    m.platform_label ? `<span class="mh-chip">${esc(m.platform_label)}</span>` : "",
    p.uptimeDetail ? `<span class="mh-chip">已运行 ${esc(p.uptimeDetail)}</span>` : "",
    m.runner_status ? `<span class="mh-chip mono">${esc(m.runner_status)}</span>` : "",
  ]
    .filter(Boolean)
    .join("");

  const details = [];
  if (p.metrics.some((x) => x.id === "cpu")) {
    const cpu = p.metrics.find((x) => x.id === "cpu");
    details.push(
      `<div class="mh-detail"><span class="mh-detail-k">${p.cpuCount ? `全部 ${p.cpuCount} 核的 CPU` : "CPU"}</span><span class="mh-detail-v">${cpu.percent}%</span><span class="mh-detail-bar"><span class="mh-meter-fill tone-${cpu.tone}" style="width:${Math.max(4, cpu.percent)}%"></span></span></div>`,
    );
  }
  if (p.metrics.some((x) => x.id === "ram")) {
    const ram = p.metrics.find((x) => x.id === "ram");
    details.push(
      `<div class="mh-detail"><span class="mh-detail-k">内存占用</span><span class="mh-detail-v">${ram.percent}%</span><span class="mh-detail-bar"><span class="mh-meter-fill tone-${ram.tone}" style="width:${Math.max(4, ram.percent)}%"></span></span></div>`,
    );
  }
  if (p.loadDetail) {
    details.push(
      `<div class="mh-detail"><span class="mh-detail-k">负载 (1 分钟)</span><span class="mh-detail-v mono">${esc(p.loadDetail)}</span></div>`,
    );
  }
  if (p.uptimeDetail) {
    details.push(
      `<div class="mh-detail"><span class="mh-detail-k">运行时间</span><span class="mh-detail-v mono">${esc(p.uptimeDetail)}</span></div>`,
    );
  }

  return `<article class="mh-card tone-${esc(p.overallTone)} ${m.active ? "is-active" : "is-idle"}">
    <div class="mh-card-main">
      <div class="mh-ico" aria-hidden="true">▣</div>
      <div class="mh-meta">
        <div class="mh-title-row">
          <h3 class="mh-name" title="${attr(m.label || m.id)}">${esc(m.label || m.id || "—")}</h3>
          <span class="mh-status tone-${esc(p.overallTone)}">${esc(p.status)}</span>
        </div>
        <div class="mh-sub">
          ${chips}
          ${hostSub ? `<span class="mh-host mono">${esc(hostSub)}</span>` : ""}
        </div>
      </div>
      <div class="mh-meters ${p.metrics.length ? "" : "is-empty"}">
        ${meters || `<span class="mh-empty-health">暂无负载采样</span>`}
      </div>
    </div>
    <div class="mh-popover" role="tooltip">
      <div class="mh-pop-head">
        <span>机器负载</span>
        <span class="tone-${esc(p.overallTone)}">${esc(
          p.overallTone === "ok"
            ? "健康 — 还可运行更多代理"
            : p.overallTone === "warn"
              ? "升高 — 注意负载"
              : p.overallTone === "critical"
                ? "偏高 — 建议暂缓新建"
                : "未知 — 等待 runner 上报",
        )}</span>
      </div>
      <div class="mh-pop-body">
        ${details.join("") || `<div class="muted xs">Runner 在线后约每 15–20 秒更新一次健康数据。</div>`}
      </div>
      <p class="mh-pop-hint">约每 15–20 秒由该机器上的 runner 更新 · 数据来自 HAPI <code>/api/machines</code></p>
    </div>
  </article>`;
}

function machinesSectionHtml(machines) {
  const list = Array.isArray(machines) ? machines : [];
  if (!list.length) {
    return `<div class="card card-fx mh-section" id="mh-section">
      <div class="card-head">
        <div>
          <h2>机器负载</h2>
          <p class="sub">跑 AI 会话的那些电脑现在忙不忙</p>
        </div>
      </div>
      <div class="empty mh-empty">
        还没有电脑连上来。在你跑代码的那台电脑上执行 <code>hapi runner start</code>，
        它就会出现在这里（注意要连的是同一个 HAPI 服务和 namespace）。
      </div>
    </div>`;
  }
  return `<div class="card card-fx mh-section" id="mh-section">
    <div class="card-head">
      <div>
        <h2>机器负载</h2>
        <p class="sub">${list.length} 台在线 · 鼠标悬停可看 CPU / 内存详情</p>
      </div>
      <span class="tag tag-muted">${list.filter((m) => m.active).length} 台活跃</span>
    </div>
    <div class="mh-grid">
      ${list.map(machineCard).join("")}
    </div>
  </div>`;
}

/** 轮询时只刷机器区与「机器」指标，不整页重绘（避免打断常用设置表单） */
export function patchOverviewMachines() {
  if (state.page !== "overview" || !state.data) return;
  const root = $("#view-overview");
  if (!root || root.hidden) return;
  const machines = Array.isArray(state.data.machines) ? state.data.machines : [];
  const host = $("#mh-section");
  const html = machinesSectionHtml(machines);
  if (host) {
    const tmp = document.createElement("div");
    tmp.innerHTML = html;
    const next = tmp.firstElementChild;
    if (next) host.replaceWith(next);
  }
  // 顶部「机器」指标
  for (const metric of root.querySelectorAll(".metric-grid .metric")) {
    const lab = metric.querySelector(".label")?.textContent?.trim();
    if (lab === "机器") {
      const v = metric.querySelector(".value");
      if (v) v.textContent = String(machines.length);
      break;
    }
  }
}

function renderOverview() {
  if (!state.data) return;
  renderTopConn();
  renderAlert();
  ensureFxLayer();

  const c = state.data.connection || {};
  const m = state.data.metrics || {};
  const cfg = state.data.config || {};
  const def = state.data.defaults || { primary: null, flavor: {}, writable: false };
  const winOptsList = Array.isArray(state.data.window_options) ? state.data.window_options : [];
  const machines = Array.isArray(state.data.machines) ? state.data.machines : [];
  const ok = connIsOk(c);
  const label = connLabel(c);
  const primaryTitle = def.primary ? wTitle(def.primary) : "未设置";
  const routeWritable = def.writable !== false;

  const levelOpts = OUTPUT_LEVELS.map(
    (o) =>
      `<option value="${o.value}" ${cfg.output_level === o.value ? "selected" : ""}>${esc(o.title)}</option>`,
  ).join("");
  const busyLevel = cfg.busy_agent_push_level || "inherit";
  const busyLevelOpts = BUSY_AGENT_PUSH_LEVELS.map(
    (o) =>
      `<option value="${o.value}" ${busyLevel === o.value ? "selected" : ""}>${esc(o.title)}</option>`,
  ).join("");
  const autoApproveOn = !!cfg.auto_approve_enabled;
  const opSummaryOn = !!cfg.auto_approve_silent;

  const primarySelectOpts =
    `<option value="">未设置</option>` +
    winOptsList
      .map(
        (w) =>
          `<option value="${attr(w.umo)}" ${def.primary === w.umo ? "selected" : ""}>${esc(w.title || wTitle(w.umo))}</option>`,
      )
      .join("");

  $("#view-overview").innerHTML = `
    <div class="metric-grid">
      <div class="metric ${ok ? "ok" : "danger"}" title="插件与 HAPI 服务的连接状态，断开时收不到任何推送">
        <div class="label">连接</div>
        <div class="value" style="font-size:1.1rem">${esc(label)}</div>
      </div>
      <div class="metric" title="正在运行的 AI 会话数">
        <div class="label">运行中</div>
        <div class="value">${m.active ?? 0}</div>
      </div>
      <div class="metric" title="AI 正在生成回复的会话数">
        <div class="label">思考中</div>
        <div class="value">${m.thinking ?? 0}</div>
      </div>
      <div class="metric ${m.pending ? "warn" : ""}" title="AI 在等你批准的操作。聊天里发 /hapi a 一键批准">
        <div class="label">待审批</div>
        <div class="value">${m.pending ?? 0}</div>
      </div>
      <div class="metric ${m.unrouted ? "danger" : ""}" title="这些会话的通知找不到聊天窗口可发，你会错过消息。去「会话管理」为它们设置推送窗口">
        <div class="label">收不到通知</div>
        <div class="value">${m.unrouted ?? 0}</div>
      </div>
      <div class="metric" title="连接到 HAPI 的在线电脑数">
        <div class="label">在线机器</div>
        <div class="value">${machines.length}</div>
      </div>
    </div>

    ${machinesSectionHtml(machines)}

    <div class="card card-fx">
      <div class="card-head">
        <div>
          <h2>常用设置</h2>
          <p class="sub">改完立刻生效，不用点保存；更多选项在「设置」页</p>
        </div>
        <button type="button" class="linkish" data-go="settings">全部设置 →</button>
      </div>
      <div class="quick-settings">
        <label class="qs-field">
          <span class="qs-label">推送详细程度</span>
          <select id="qs-level" class="ctrl">${levelOpts}</select>
        </label>
        ${
          cfg.output_level === "summary"
            ? `<label class="qs-field">
          <span class="qs-label">摘要条数</span>
          <input id="qs-summary" class="ctrl" type="number" min="1" max="50" value="${attr(cfg.summary_msg_count)}" />
        </label>`
            : ""
        }
        <div class="qs-field qs-bool">
          <span class="qs-label">戳一戳快捷</span>
          <label class="switch">
            <input id="qs-poke" type="checkbox" ${cfg.poke_approve ? "checked" : ""} />
            <span class="switch-track" aria-hidden="true"></span>
            <span class="switch-text">${cfg.poke_approve ? "开启" : "关闭"}</span>
          </label>
        </div>

        <div class="qs-field qs-section">
          <span class="qs-label">忙时托管</span>
          <span class="qs-section-hint">时段内自动批权限 · 可压对话 · 可选操作汇总</span>
        </div>
        <div class="qs-field qs-bool">
          <span class="qs-label">忙时托管审批</span>
          <label class="switch">
            <input id="qs-auto" type="checkbox" ${autoApproveOn ? "checked" : ""} />
            <span class="switch-track" aria-hidden="true"></span>
            <span class="switch-text">${autoApproveOn ? "开启" : "关闭"}</span>
          </label>
        </div>
        <label class="qs-field">
          <span class="qs-label">托管从</span>
          <input id="qs-auto-start" class="ctrl mono" type="text" inputmode="numeric" autocomplete="off"
            spellcheck="false" placeholder="23:00" maxlength="5"
            value="${attr(cfg.auto_approve_start || "23:00")}" title="整段输入 HH:MM，如 23:00"
            ${autoApproveOn ? "" : "disabled"} />
        </label>
        <label class="qs-field">
          <span class="qs-label">托管到</span>
          <input id="qs-auto-end" class="ctrl mono" type="text" inputmode="numeric" autocomplete="off"
            spellcheck="false" placeholder="07:00" maxlength="5"
            value="${attr(cfg.auto_approve_end || "07:00")}" title="整段输入 HH:MM，如 07:00"
            ${autoApproveOn ? "" : "disabled"} />
        </label>
        <label class="qs-field">
          <span class="qs-label">忙时托管免打扰</span>
          <select id="qs-busy-level" class="ctrl" title="托管时段内压 AI 对话刷屏；权限已自动批"
            ${autoApproveOn ? "" : "disabled"}>${busyLevelOpts}</select>
        </label>
        <div class="qs-field qs-bool">
          <span class="qs-label">操作记录汇总</span>
          <label class="switch">
            <input id="qs-op-summary" type="checkbox" ${opSummaryOn ? "checked" : ""} ${autoApproveOn ? "" : "disabled"} />
            <span class="switch-track" aria-hidden="true"></span>
            <span class="switch-text">${opSummaryOn ? "开启" : "关闭"}</span>
          </label>
        </div>
        <div class="qs-field qs-note">
          <span class="qs-label">说明</span>
          <span class="qs-note-text">${
            autoApproveOn
              ? `托管 ${esc(cfg.auto_approve_start || "23:00")}–${esc(cfg.auto_approve_end || "07:00")} 自动批权限；免打扰=${esc(
                  (BUSY_AGENT_PUSH_LEVELS.find((x) => x.value === busyLevel) || {}).title || busyLevel,
                )}（只压对话，不弹批）；操作记录=${opSummaryOn ? "汇总推送（细项在设置→审批）" : "仍可能逐条推已自动批准"}。AI 提问仍会提醒。可 /hapi summary 重发。`
              : "先开托管并设时段；再选免打扰（不推/仅摘要/跟随），可选开操作记录汇总。细项在「设置 → 审批」。"
          }</span>
        </div>
      </div>
    </div>

    <div class="card card-fx">
      <div class="card-head">
        <div>
          <h2>连接信息</h2>
          <p class="sub">插件连的是哪个 HAPI 服务、连得怎么样</p>
        </div>
        <div class="card-head-actions">
          <button type="button" class="btn btn-sm" id="btn-reconnect">重连</button>
          <button type="button" class="linkish" data-go="settings">改连接 →</button>
        </div>
      </div>
      <dl class="kv">
        <dt>服务地址</dt><dd>${esc(cfg.hapi_endpoint || c.endpoint || c.endpoint_host || "—")}</dd>
        <dt>访问令牌</dt><dd class="mono break">${esc(cfg.access_token || "—")}</dd>
        <dt>HAPI 网页</dt>
        <dd>${
          cfg.hapi_web_url
            ? `<a class="ext-link mono break" href="${attr(cfg.hapi_web_url)}" target="_blank" rel="noopener noreferrer">${esc(cfg.hapi_web_url_safe || cfg.hapi_web_url)}</a>
               <span class="muted xs">点击可免登录打开，链接含访问令牌，别转发给他人</span>`
            : `<span class="muted">未配置地址</span>`
        }</dd>
        <dt>实时推送</dt><dd>${esc(label)}${c.stream_live ? " · 流活跃" : ""}${c.task_running === false ? " · 任务未运行" : ""}</dd>
        <dt>推送级别</dt><dd>${esc(cfg.output_level || "—")}</dd>
        <dt>默认推送窗口</dt>
        <dd class="kv-stack">
          <div class="primary-now mono break">${esc(primaryTitle)}</div>
          <select id="qs-primary" class="ctrl ctrl-sm" ${routeWritable ? "" : "disabled"} title="${attr(def.writable_reason || "")}">${primarySelectOpts}</select>
          ${
            !winOptsList.length
              ? `<span class="muted xs">尚无聊天窗口记录，请先在聊天里 /hapi bind</span>`
              : !routeWritable
                ? `<span class="muted xs">${esc(def.writable_reason || "当前不可改")}</span>`
                : `<span class="muted xs">与「会话管理」中的默认推送窗口同步</span>`
          }
        </dd>
        ${c.conn_error ? `<dt>最近错误</dt><dd>${esc(c.conn_error)}</dd>` : ""}
      </dl>
    </div>
  `;

  const applyQuick = async (patch) => {
    try {
      if (isLive() && getApi()) {
        const res = await getApi().saveConfig(patch);
        if (res?.message) toast(res.message);
      } else {
        store.saveConfig(patch);
      }
      if (state.draft) Object.assign(state.draft, patch);
      await refresh({ silent: true, repaint: true });
    } catch (e) {
      toast("保存失败: " + (e.message || e));
    }
  };

  $("#qs-level").onchange = () => applyQuick({ output_level: $("#qs-level").value });
  $("#qs-summary") &&
    ($("#qs-summary").onchange = () =>
      applyQuick({ summary_msg_count: Number($("#qs-summary").value) || 5 }));
  $("#qs-poke") &&
    ($("#qs-poke").onchange = () => {
      const on = $("#qs-poke").checked;
      const txt = $("#qs-poke").closest(".switch")?.querySelector(".switch-text");
      if (txt) txt.textContent = on ? "开启" : "关闭";
      applyQuick({ poke_approve: on });
    });
  $("#qs-auto") &&
    ($("#qs-auto").onchange = () => {
      const on = $("#qs-auto").checked;
      const txt = $("#qs-auto").closest(".switch")?.querySelector(".switch-text");
      if (txt) txt.textContent = on ? "开启" : "关闭";
      applyQuick({ auto_approve_enabled: on });
    });
  $("#qs-busy-level") &&
    ($("#qs-busy-level").onchange = () =>
      applyQuick({ busy_agent_push_level: $("#qs-busy-level").value || "inherit" }));
  $("#qs-op-summary") &&
    ($("#qs-op-summary").onchange = () => {
      const on = $("#qs-op-summary").checked;
      const txt = $("#qs-op-summary").closest(".switch")?.querySelector(".switch-text");
      if (txt) txt.textContent = on ? "开启" : "关闭";
      applyQuick({ auto_approve_silent: on });
    });
  const normalizeHm = (raw, fallback) => {
    const s = String(raw ?? "").trim();
    const m = s.match(/^(\d{1,2}):(\d{2})(?::\d{2})?$/);
    if (!m) return fallback;
    const hh = Math.min(23, Math.max(0, Number(m[1])));
    const mm = Math.min(59, Math.max(0, Number(m[2])));
    if (Number.isNaN(hh) || Number.isNaN(mm)) return fallback;
    return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
  };
  const wireTimeText = (id, key, fallback) => {
    const el = $(id);
    if (!el) return;
    const commit = () => {
      const next = normalizeHm(el.value, fallback);
      el.value = next;
      applyQuick({ [key]: next });
    };
    el.addEventListener("change", commit);
    el.addEventListener("blur", commit);
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        el.blur();
      }
    });
  };
  wireTimeText("#qs-auto-start", "auto_approve_start", "23:00");
  wireTimeText("#qs-auto-end", "auto_approve_end", "07:00");

  $("#qs-primary") &&
    ($("#qs-primary").onchange = async () => {
      const umo = $("#qs-primary").value || null;
      try {
        if (isLive() && getApi()) {
          const res = await getApi().setPrimaryRoute(umo);
          toast(res?.message || "已更新默认推送窗口");
        } else {
          store.setDefault("primary", umo);
          toast("已更新默认推送窗口");
        }
        await refresh({ silent: true, repaint: true });
      } catch (e) {
        toast("更新失败: " + (e.message || e));
      }
    });

  $$("#view-overview [data-go]").forEach((b) => {
    b.onclick = () => {
      void go(b.dataset.go);
    };
  });
  $("#btn-reconnect")?.addEventListener("click", async () => {
    const yes = await askConfirm("按当前已保存配置重建连接并重启 SSE？", {
      title: "重连",
      yes: "重连",
    });
    if (!yes) return;
    try {
      if (isLive() && getApi()) {
        const res = await getApi().reconnect();
        toast(res.message || "已重连");
        await refresh();
      } else {
        store.wake();
        toast("已模拟重连");
        await refresh();
      }
    } catch (e) {
      toast("重连失败: " + (e.message || e));
    }
  });
}

export { renderOverview };
