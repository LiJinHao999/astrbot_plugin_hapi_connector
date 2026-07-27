/**
 * 会话管理页（窗口列表 / 推送路由 / session 表 / 详情弹窗）
 */
import { PERM, LAYER, FLAVOR_ROUTE_KEYS } from "../constants.js?v=3.0.1";
import {
  state,
  store,
  wTitle,
  allKnownWindows,
  visibleWindowOptions,
  loadHiddenWindows,
  setHiddenWindowsLocal,
  isWindowShown,
  windowSessionStats,
  formatWindowBindMeta,
  groupWindowsByBot,
  bindSelect,
} from "../state.js?v=3.0.1";
import { $, $$, esc, attr, pill, layerTag, statusLabel, parseUmo } from "../utils.js?v=3.0.1";
import {
  renderTopConn,
  renderAlert,
  toast,
  askConfirm,
} from "../ui.js?v=3.0.1";
import { refresh, applySnapFromResult } from "../data.js?v=3.0.1";
import { isLive, getApi } from "../live.js?v=3.0.1";


function renderRoutePanel() {
  const def = state.data?.defaults || { primary: null, flavor: {}, writable: false };
  const flavorMap = def.flavor && typeof def.flavor === "object" ? def.flavor : {};
  const allOpts = allKnownWindows();
  const winOpts = (selected) => {
    const list = visibleWindowOptions([selected]);
    return (
      `<option value="">未设置</option>` +
      list
        .map(
          (w) =>
            `<option value="${attr(w.umo)}" ${selected === w.umo ? "selected" : ""}>${esc(w.title)}</option>`,
        )
        .join("")
    );
  };

  const flavorCells = FLAVOR_ROUTE_KEYS.map(
    (f) => `<label class="route-cell">
      <span class="route-cell-label">${esc(f)} 推送窗口</span>
      <select class="ctrl-sm js-route-flavor" data-flavor="${f}">${winOpts(flavorMap[f] || "")}</select>
    </label>`,
  ).join("");

  const routeWritable = def.writable !== false;
  const routeHint =
    def.writable_reason || (allOpts.length ? "" : "尚无聊天窗口记录，请先在聊天里 /hapi bind");
  const subExtra = routeWritable
    ? ""
    : routeHint
      ? ` · ${esc(routeHint)}`
      : " · 当前只读";

  $("#route-panel").innerHTML = `
    <div class="route-panel-inner">
      <div class="route-panel-head">
        <div>
          <div class="route-panel-title">通知发到哪</div>
          <p class="route-panel-sub">没有专属窗口的会话按这里的规则投递：先看代理类型对应的窗口，再兜底到默认窗口${subExtra}</p>
        </div>
      </div>
      <div class="route-row">
        <label class="route-cell route-cell-primary">
          <span class="route-cell-label">默认推送窗口</span>
          <select id="route-primary" class="ctrl-sm" ${routeWritable ? "" : "disabled"}>${winOpts(def.primary || "")}</select>
        </label>
        <div class="route-flavor-grid">${flavorCells}</div>
      </div>
    </div>`;

  if (!routeWritable) {
    $$(".js-route-flavor").forEach((sel) => sel.setAttribute("disabled", "disabled"));
  }
  const primarySel = $("#route-primary");
  if (primarySel) primarySel.onchange = async () => {
    const umo = $("#route-primary").value || null;
    try {
      if (isLive() && getApi()) {
        if (!routeWritable) {
          toast(routeHint || "当前不可写路由");
          await refresh();
          return;
        }
        const res = await getApi().setPrimaryRoute(umo);
        toast(res.message || "已更新默认推送窗口");
        if (!applySnapFromResult(res)) await refresh();
        else { renderTopConn(); renderSessions(); }
        return;
      }
      store.setDefault("primary", umo);
      await refresh();
    } catch (err) {
      toast("更新失败: " + (err.message || err));
      await refresh();
    }
  };
  $$(".js-route-flavor").forEach((sel) => {
    sel.onchange = async () => {
      const flavor = sel.dataset.flavor;
      const umo = sel.value || null;
      try {
        if (isLive() && getApi()) {
          if (!routeWritable) {
            toast(routeHint || "当前不可写路由");
            await refresh();
            return;
          }
          const res = await getApi().setFlavorRoute(flavor, umo);
          toast(res.message || `已更新 ${flavor} 推送窗口`);
          if (!applySnapFromResult(res)) await refresh();
          else { renderTopConn(); renderSessions(); }
          return;
        }
        store.setDefault(flavor, umo);
        await refresh();
      } catch (err) {
        toast("更新失败: " + (err.message || err));
        await refresh();
      }
    };
  });
}

function filteredSessions() {
  const key = state.focusWindow || "__none__";
  const all = state.data.sessions;
  if (key === "__none__") return all.filter((s) => s.layer === "none");
  return all.filter((s) => s.effective_umo === key);
}

function renderWindowList() {
  const allCols = state.data.columns || [];
  // 未投递列始终保留；有 umo 的按可见性过滤
  const cols = allCols.filter((col) => !col.umo || isWindowShown(col.umo));
  if (!state.focusWindow || !cols.some((c) => (c.umo || "__none__") === state.focusWindow)) {
    state.focusWindow = cols[0]?.umo || (cols[0] ? "__none__" : allCols[0]?.umo || "__none__");
  }

  $("#window-list").innerHTML = cols
    .map((col) => {
      const key = col.umo || "__none__";
      const on = state.focusWindow === key;
      const tags = [];
      if (col.umo && (state.data?.focus_windows || []).includes(col.umo)) {
        tags.push(`<span class="tag tag-layer-session_bind">Focus</span>`);
      }
      if (col.is_primary) {
        tags.push(`<span class="tag tag-muted">默认推送窗口</span>`);
      }
      for (const f of col.flavors || []) {
        tags.push(`<span class="tag tag-layer-flavor_default">${esc(f)} 推送窗口</span>`);
      }
      return `<button type="button" data-win="${attr(key)}" class="win-item ${on ? "is-on" : ""}">
        <div class="win-item-top">
          <span class="win-item-title ${col.umo ? "" : "is-none"}">${esc(col.title)}</span>
          <span class="win-item-count">${col.sessions.length}</span>
        </div>
        <div class="win-item-umo">${esc(col.umo || "无目标")}</div>
        ${tags.length ? `<div class="win-item-tags">${tags.join("")}</div>` : ""}
      </button>`;
    })
    .join("");

  $$("#window-list [data-win]").forEach((b) => {
    b.onclick = () => {
      state.focusWindow = b.dataset.win;
      renderSessions();
    };
  });

  const btnVis = $("#btn-win-vis");
  if (btnVis) {
    const hiddenN = loadHiddenWindows().size;
    btnVis.textContent = hiddenN ? `管理可见窗口（已隐藏 ${hiddenN} 个）` : "管理可见窗口";
    btnVis.onclick = () => openWindowVisibilityDialog();
  }
}

function openWindowVisibilityDialog() {
  const wins = allKnownWindows();
  if (!wins.length) {
    toast("暂无推送窗口记录，请先在聊天里 /hapi bind");
    return;
  }
  const hidden = loadHiddenWindows();
  const groups = groupWindowsByBot(wins);

  const body = groups
    .map(([bot, list]) => {
      const shownN = list.filter((w) => !hidden.has(w.umo)).length;
      const rows = list
        .map((w) => {
          const on = !hidden.has(w.umo);
          const meta = formatWindowBindMeta(w.umo);
          const { bound, effective, active } = windowSessionStats(w.umo);
          // 活跃：当前有运行中 session 投递到此窗口
          const isActive = active > 0;
          return `<label class="win-vis-item${isActive ? " is-active-win" : ""}" data-bound="${bound}" data-effective="${effective}" data-active="${active}">
            <input type="checkbox" data-vis-umo="${attr(w.umo)}" value="${attr(w.umo)}" ${on ? "checked" : ""} />
            <span class="win-vis-title">${esc(w.title)}</span>
            <span class="win-vis-bind${bound || effective ? "" : " is-zero"}">${esc(meta)}</span>
            <span class="win-vis-umo mono">${esc(w.umo)}</span>
          </label>`;
        })
        .join("");
      return `<div class="win-vis-group" data-bot="${attr(bot)}">
        <div class="win-vis-group-head">
          <span class="win-vis-bot">Bot:${esc(bot)}</span>
          <span class="win-vis-count muted">${shownN}/${list.length}</span>
          <button type="button" class="btn btn-sm js-vis-group-all" data-bot="${attr(bot)}">全选</button>
          <button type="button" class="btn btn-sm js-vis-group-none" data-bot="${attr(bot)}">全不选</button>
        </div>
        <div class="win-vis-group-body">${rows}</div>
      </div>`;
    })
    .join("");

  $("#dlg-title").textContent = "管理可见推送窗口";
  const dlg = $("#dlg");
  dlg?.classList.add("dlg-win-vis");
  $("#dlg-body").innerHTML = `
    <p class="field-help win-vis-help">不想在列表里看到的窗口可以取消勾选（只影响面板显示，不影响通知推送）。右侧数字是该窗口的会话统计：绑定数 / 投递数 / 运行中数。</p>
    <div class="win-vis-toolbar">
      <button type="button" class="btn btn-sm" id="vis-all">全部显示</button>
      <button type="button" class="btn btn-sm" id="vis-none">全部隐藏</button>
      <button type="button" class="btn btn-sm" id="vis-active" title="只勾选当前有运行中 session 的窗口">仅选择活跃窗口</button>
      <span class="spacer"></span>
      <button type="button" class="btn btn-primary btn-sm" id="vis-apply">应用</button>
    </div>
    <div class="win-vis-list" id="win-vis-list" tabindex="0">${body}</div>
  `;
  dlg?.showModal();
  requestAnimationFrame(() => {
    const list = $("#win-vis-list");
    if (list) {
      list.scrollTop = 0;
      try {
        list.focus({ preventScroll: true });
      } catch (_) {
        /* ignore */
      }
    }
  });
  const onClose = () => {
    dlg?.classList.remove("dlg-win-vis");
    dlg?.removeEventListener("close", onClose);
  };
  dlg?.addEventListener("close", onClose);

  const setGroup = (bot, checked) => {
    $$("#dlg-body .win-vis-group").forEach((g) => {
      if (g.dataset.bot !== bot) return;
      g.querySelectorAll("input[data-vis-umo]").forEach((inp) => {
        inp.checked = checked;
      });
    });
    refreshGroupCounts();
  };
  const refreshGroupCounts = () => {
    $$("#dlg-body .win-vis-group").forEach((g) => {
      const boxes = [...g.querySelectorAll("input[data-vis-umo]")];
      const n = boxes.filter((b) => b.checked).length;
      const el = g.querySelector(".win-vis-count");
      if (el) el.textContent = `${n}/${boxes.length}`;
    });
  };

  $$("#dlg-body .js-vis-group-all").forEach((b) => {
    b.onclick = () => setGroup(b.dataset.bot, true);
  });
  $$("#dlg-body .js-vis-group-none").forEach((b) => {
    b.onclick = () => setGroup(b.dataset.bot, false);
  });
  $$("#dlg-body input[data-vis-umo]").forEach((inp) => {
    inp.onchange = refreshGroupCounts;
  });
  $("#vis-all") &&
    ($("#vis-all").onclick = () => {
      $$("#dlg-body input[data-vis-umo]").forEach((inp) => {
        inp.checked = true;
      });
      refreshGroupCounts();
    });
  $("#vis-none") &&
    ($("#vis-none").onclick = () => {
      $$("#dlg-body input[data-vis-umo]").forEach((inp) => {
        inp.checked = false;
      });
      refreshGroupCounts();
    });
  $("#vis-active") &&
    ($("#vis-active").onclick = () => {
      let n = 0;
      $$("#dlg-body input[data-vis-umo]").forEach((inp) => {
        const row = inp.closest(".win-vis-item");
        const activeN = Number(row?.dataset?.active || 0);
        const on = activeN > 0;
        inp.checked = on;
        if (on) n++;
      });
      refreshGroupCounts();
      toast(n ? `已勾选 ${n} 个活跃窗口（有运行中 session）` : "当前没有运行中 session 的窗口");
    });
  $("#vis-apply") &&
    ($("#vis-apply").onclick = async () => {
      const nextHidden = new Set();
      const boxes = $$("#dlg-body input[data-vis-umo]");
      if (!boxes.length) {
        toast("未找到窗口列表，请关闭后重试");
        return;
      }
      boxes.forEach((inp) => {
        const umo = String(inp.getAttribute("data-vis-umo") || inp.value || "").trim();
        if (!inp.checked && umo) nextHidden.add(umo);
      });
      const list = [...nextHidden];
      try {
        if (isLive() && getApi()) {
          const res = await getApi().setHiddenWindows(list);
          // 以服务端落盘结果为准
          setHiddenWindowsLocal(res?.hidden || list);
          toast(
            res?.message ||
              (list.length
                ? `已隐藏 ${list.length} 个 · 下拉/左侧保留 ${Math.max(0, boxes.length - list.length)} 个`
                : "已全部显示"),
          );
        } else {
          // 本地 mock：只改内存
          setHiddenWindowsLocal(list);
          toast(
            list.length
              ? `已隐藏 ${list.length} 个 · 下拉/左侧保留 ${Math.max(0, boxes.length - list.length)} 个`
              : "已全部显示",
          );
        }
      } catch (e) {
        toast("保存失败: " + (e.message || e));
        return;
      }
      $("#dlg").close();
      renderSessions();
    });
}


function isFocusOn(umo) {
  return Boolean(umo) && (state.data?.focus_windows || []).includes(umo);
}

function renderFocusToggle(col) {
  const slot = $("#focus-mode-slot");
  if (!slot) return;
  // 「未投递」列没有真实窗口，不显示开关
  if (!col || !col.umo) {
    slot.innerHTML = "";
    return;
  }
  const on = isFocusOn(col.umo);
  slot.innerHTML = `
    <label class="tb-check focus-toggle" title="开启后：该聊天窗口里发的文字消息会直接转发给当前 session（免前缀）；纯图片/文件会先暂存，配上文字后再一并送出。与 /hapi focus 同步。">
      <input type="checkbox" id="focus-mode-chk" ${on ? "checked" : ""} />
      <span>Focus 模式${on ? " · 开" : ""}</span>
    </label>`;
  const chk = $("#focus-mode-chk");
  if (chk) chk.onchange = async () => {
    const enabled = chk.checked;
    try {
      if (isLive() && getApi()) {
        const res = await getApi().setWindowFocus(col.umo, enabled);
        toast(res.message || (enabled ? "Focus 模式已开启" : "Focus 模式已关闭"));
        if (!applySnapFromResult(res)) await refresh();
        else { renderTopConn(); renderSessions(); }
        return;
      }
      // mock：直接改本地状态
      const list = new Set(state.data.focus_windows || []);
      enabled ? list.add(col.umo) : list.delete(col.umo);
      state.data.focus_windows = [...list];
      renderSessions();
    } catch (err) {
      toast("Focus 切换失败: " + (err.message || err));
      chk.checked = !enabled;
    }
  };
}

function renderSessPanel() {
  const key = state.focusWindow || "__none__";
  const col = state.data.columns.find((c) => (c.umo || "__none__") === key);
  $("#focus-title").textContent = col ? `推送到「${col.title}」` : "会话列表";
  renderFocusToggle(col);

  const rows = filteredSessions();
  const visibleIds = rows.map((s) => s.id);
  const selectedVisible = visibleIds.filter((id) => state.selected.has(id));
  const allOn = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;
  const someOn = selectedVisible.length > 0 && !allOn;

  if (!rows.length) {
    $("#sess-panel").innerHTML = `<div class="empty">这个窗口下还没有会话。
      在对应聊天窗口发 <code>/hapi create</code> 新建，或发 <code>/hapi sw &lt;序号&gt;</code> 把已有会话接管过来。</div>`;
    return;
  }

  const groups = new Map();
  for (const s of rows) {
    if (!groups.has(s.path)) groups.set(s.path, []);
    groups.get(s.path).push(s);
  }

  let body = "";
  for (const [path, items] of groups) {
    const ids = items.map((s) => s.id);
    const folderAll = ids.every((id) => state.selected.has(id));
    const folderSome = ids.some((id) => state.selected.has(id));
    body += `<tr class="folder-row">
      <td><input type="checkbox" class="js-folder" data-path="${attr(path)}" ${folderAll ? "checked" : ""} ${
      folderSome && !folderAll ? "data-ind=1" : ""
    }/></td>
      <td colspan="4"><span class="folder-path">${esc(path)}</span> · ${items.length}</td>
    </tr>`;
    for (const s of items) {
      const checked = state.selected.has(s.id);
      const perms = (PERM[s.flavor] || ["default"])
        .map((p) => `<option value="${p}" ${p === s.permissionMode ? "selected" : ""}>${p}</option>`)
        .join("");
      body += `<tr class="sess-row ${checked ? "is-selected" : ""}" data-sid="${s.id}">
        <td><input type="checkbox" class="js-sel" data-id="${s.id}" ${checked ? "checked" : ""}/></td>
        <td>
          <div class="sess-title">${esc(s.title)}</div>
          <div class="sess-meta">
            <span>${esc(s.flavor)}</span>
            <span>${esc(s.id_short)}</span>
            ${layerTag(s.layer)}
          </div>
        </td>
        <td>${pill(s)}</td>
        <td><select class="ctrl-sm js-perm" data-id="${s.id}">${perms}</select></td>
        <td class="col-bind"><select class="ctrl-sm js-bind" data-id="${s.id}">${bindSelect(s)}</select></td>
      </tr>`;
    }
  }

  $("#sess-panel").innerHTML = `
    <div class="table-card">
      <div class="table-toolbar">
        <label class="tb-check">
          <input type="checkbox" id="sel-all" ${allOn ? "checked" : ""} ${someOn ? "data-ind=1" : ""} />
          <span>${allOn ? "取消全选" : "全选列表"}</span>
        </label>
        <button type="button" class="btn btn-sm ${selectedVisible.length ? "" : "is-ghost"}" id="sel-clear" ${
          selectedVisible.length ? "" : "disabled"
        }>已选 ${selectedVisible.length} · 清除</button>
        <span class="spacer"></span>
        <button type="button" class="btn btn-sm" data-batch="resume" ${selectedVisible.length ? "" : "disabled"}>恢复</button>
        <button type="button" class="btn btn-sm" data-batch="archive" ${selectedVisible.length ? "" : "disabled"}>归档</button>
        <button type="button" class="btn btn-sm btn-danger" data-batch="delete" ${selectedVisible.length ? "" : "disabled"}>删除</button>
      </div>
      <div class="table-wrap">
        <table class="data">
          <thead>
            <tr>
              <th class="col-check"></th>
              <th>会话</th>
              <th class="col-status">状态</th>
              <th class="col-perm">权限</th>
              <th class="col-bind">通知投递</th>
            </tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>
      <p class="muted xs" style="margin:6px 10px">提示：双击任意一行可查看会话详情、恢复 / 归档 / 删除单个会话</p>
    </div>`;

  wireTable(visibleIds);
}

function wireTable(visibleIds) {
  const selAll = $("#sel-all");
  if (selAll?.dataset.ind) selAll.indeterminate = true;
  $$(".js-folder[data-ind]").forEach((cb) => {
    cb.indeterminate = true;
  });

  selAll?.addEventListener("change", () => {
    const shouldSelect = !visibleIds.every((id) => state.selected.has(id));
    visibleIds.forEach((id) => {
      if (shouldSelect) state.selected.add(id);
      else state.selected.delete(id);
    });
    renderSessions();
  });

  $("#sel-clear")?.addEventListener("click", () => {
    visibleIds.forEach((id) => state.selected.delete(id));
    renderSessions();
  });

  $$(".js-sel").forEach((cb) => {
    cb.onchange = (e) => {
      e.stopPropagation();
      if (cb.checked) state.selected.add(cb.dataset.id);
      else state.selected.delete(cb.dataset.id);
      renderSessions();
    };
  });

  $$(".js-folder").forEach((cb) => {
    cb.onchange = (e) => {
      e.stopPropagation();
      const pathIds = state.data.sessions.filter((s) => s.path === cb.dataset.path).map((s) => s.id);
      // 仅作用于当前列表可见项
      const ids = pathIds.filter((id) => visibleIds.includes(id));
      const shouldSelect = !ids.every((id) => state.selected.has(id));
      ids.forEach((id) => {
        if (shouldSelect) state.selected.add(id);
        else state.selected.delete(id);
      });
      renderSessions();
    };
  });

  $$(".js-perm").forEach((sel) => {
    sel.onchange = async (e) => {
      e.stopPropagation();
      const sid = sel.dataset.id;
      const mode = sel.value;
      try {
        if (isLive() && getApi()) {
          const res = await getApi().setPermission(sid, mode);
          toast(res.message || "权限已更新");
          if (!applySnapFromResult(res)) await refresh();
          else { renderTopConn(); renderSessions(); }
          return;
        }
        store.setPermission(sid, mode);
        await refresh();
      } catch (err) {
        toast("权限切换失败: " + (err.message || err));
        await refresh();
      }
    };
  });
  $$(".js-bind").forEach((sel) => {
    sel.onchange = async (e) => {
      e.stopPropagation();
      const sid = sel.dataset.id;
      const umo = sel.value || null;
      try {
        if (isLive() && getApi()) {
          const res = await getApi().bindSession(sid, umo);
          toast(res.message || "绑定已更新");
          if (!applySnapFromResult(res)) await refresh();
          else { renderTopConn(); renderSessions(); }
          return;
        }
        store.bind(sid, umo);
        await refresh();
      } catch (err) {
        toast("绑定失败: " + (err.message || err));
        await refresh();
      }
    };
  });

  // 单击行：切换勾选；双击打开详情
  $$("tr[data-sid]").forEach((tr) => {
    tr.onclick = (e) => {
      if (e.target.closest("select,input,button,a")) return;
      const id = tr.dataset.sid;
      if (state.selected.has(id)) state.selected.delete(id);
      else state.selected.add(id);
      renderSessions();
    };
    tr.ondblclick = (e) => {
      if (e.target.closest("select,input,button,a")) return;
      openDetail(tr.dataset.sid);
    };
  });

  $$("#sess-panel [data-batch]").forEach((b) => {
    b.onclick = async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const ids = visibleIds.filter((id) => state.selected.has(id));
      if (!ids.length) {
        toast("请先勾选 session");
        return;
      }
      const action = b.dataset.batch;
      const labels = { resume: "恢复", archive: "归档", delete: "删除" };
      const label = labels[action] || action;
      const confirmMsgs = {
        delete: `删除 ${ids.length} 个会话？删了就找不回来了。`,
        archive: `归档 ${ids.length} 个会话？`,
        resume: `恢复 ${ids.length} 个会话？恢复后 ID 可能变化，绑定关系会自动跟过去。`,
      };
      if (confirmMsgs[action]) {
        const ok = await askConfirm(confirmMsgs[action], {
          title: label,
          yes: label,
          danger: action === "delete",
        });
        if (!ok) return;
      }

      $$("#sess-panel [data-batch]").forEach((x) => {
        x.disabled = true;
        x.classList.add("is-busy");
      });
      try {
        if (!isLive() || !getApi()) {
          for (const id of ids) store.lifecycle(id, action);
          ids.forEach((id) => state.selected.delete(id));
          toast(`${label}完成（本地 mock）`);
          await refresh({ repaint: true });
          return;
        }
        toast(`正在${label} ${ids.length} 个…`);
        const res = await getApi().batchLifecycle(ids, action);
        const results = Array.isArray(res?.results) ? res.results : [];
        const okN = results.filter((r) => r && r.ok).length;
        const failN = Math.max(0, (results.length || ids.length) - okN);
        const detail = results
          .filter((r) => r && !r.ok)
          .slice(0, 3)
          .map((r) => `${String(r.id || "").slice(0, 8)}: ${r.message || "失败"}`)
          .join("；");
        let tip = res?.message || "";
        if (!tip) {
          if (failN === 0) tip = `${label}成功 ${okN}/${results.length || ids.length}`;
          else if (okN === 0) tip = `${label}全部失败` + (detail ? ` · ${detail}` : "");
          else tip = `${label}部分成功 ${okN}/${results.length}` + (detail ? ` · ${detail}` : "");
        }
        toast(tip);
        ids.forEach((id) => state.selected.delete(id));
        if (res?.snapshot) applySnapFromResult(res);
        await refresh({ fresh: true, repaint: true });
      } catch (err) {
        console.error("batch lifecycle", action, err);
        toast(`${label}失败: ` + (err.message || err));
        await refresh({ fresh: true, repaint: true });
      } finally {
        $$("#sess-panel [data-batch]").forEach((x) => {
          x.disabled = false;
          x.classList.remove("is-busy");
        });
      }
    };
  });
}

function renderSessions() {
  if (!state.data) return;
  const liveIds = new Set((state.data.sessions || []).map((s) => s.id));
  for (const id of [...state.selected]) if (!liveIds.has(id)) state.selected.delete(id);

  renderTopConn();
  renderAlert();
  renderWindowList();
  renderRoutePanel();
  renderSessPanel();
}

function openDetail(id) {
  const s = state.data.sessions.find((x) => x.id === id);
  if (!s) return;
  const why = {
    session_bind: "这个会话已经「认」了一个聊天窗口（你在那里创建或操作过它），通知会一直发到那里。",
    flavor_default: "这个会话没有专属窗口，按它的 AI 代理类型（如 claude/codex）发到对应的默认窗口。",
    primary: "这个会话没有专属窗口，它的代理类型也没设默认窗口，所以发到总的默认推送窗口。",
    none: "找不到任何可发的窗口，它的通知你会错过。在上方「通知投递」给它选一个窗口即可。",
  }[s.layer];

  $("#dlg-title").textContent = s.title;
  $("#dlg-body").innerHTML = `
    <pre class="dlg-pre">${esc(
      [
        `Session:  ${s.id_short}…`,
        `标题:     ${s.title}`,
        `代理:     ${s.flavor}`,
        `路径:     ${s.path}`,
        `状态:     ${statusLabel(s)}`,
        `权限:     ${s.permissionMode}`,
        `模型:     ${s.modelMode}`,
        `通知去向: ${wTitle(s.effective_umo)}（${LAYER[s.layer].text}）`,
      ].join("\n"),
    )}</pre>
    <label class="dlg-field">权限
      <select id="dlg-perm" class="ctrl">
        ${(PERM[s.flavor] || ["default"])
          .map((p) => `<option value="${p}" ${p === s.permissionMode ? "selected" : ""}>${p}</option>`)
          .join("")}
      </select>
    </label>
    <label class="dlg-field">通知投递
      <select id="dlg-bind" class="ctrl">${bindSelect(s)}</select>
    </label>
    <div class="dlg-actions">
      <button type="button" class="btn btn-sm" data-life="resume">恢复</button>
      <button type="button" class="btn btn-sm" data-life="archive">归档</button>
      <button type="button" class="btn btn-sm btn-danger" data-life="delete">删除</button>
    </div>
    <p class="dlg-why">${esc(why)}</p>`;

  $("#dlg").showModal();
  $("#dlg-perm").onchange = async () => {
    const mode = $("#dlg-perm").value;
    try {
      if (isLive() && getApi()) {
        const res = await getApi().setPermission(id, mode);
        toast(res.message || "权限已更新");
        if (!applySnapFromResult(res)) await refresh();
        else renderTopConn();
        openDetail(id);
        return;
      }
      store.setPermission(id, mode);
      await refresh();
      openDetail(id);
    } catch (err) {
      toast("权限切换失败: " + (err.message || err));
      await refresh();
      openDetail(id);
    }
  };
  $("#dlg-bind").onchange = async () => {
    const umo = $("#dlg-bind").value || null;
    try {
      if (isLive() && getApi()) {
        const res = await getApi().bindSession(id, umo);
        toast(res.message || "绑定已更新");
        if (!applySnapFromResult(res)) await refresh();
        else { renderTopConn(); renderSessions(); }
        openDetail(id);
        return;
      }
      store.bind(id, umo);
      await refresh();
      openDetail(id);
    } catch (err) {
      toast("绑定失败: " + (err.message || err));
      await refresh();
      openDetail(id);
    }
  };
  $$("#dlg-body [data-life]").forEach((b) => {
    b.onclick = async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const action = b.dataset.life;
      const labels = { resume: "恢复", archive: "归档", delete: "删除" };
      const label = labels[action] || action;
      const confirmMsgs = {
        delete: "确定删除这个会话？删了就找不回来了。",
        resume: "恢复后会话 ID 可能变化（绑定关系会自动跟过去），继续？",
        archive: "确定归档？",
      };
      if (confirmMsgs[action]) {
        const ok = await askConfirm(confirmMsgs[action], {
          title: label,
          yes: label,
          danger: action === "delete",
        });
        if (!ok) return;
      }
      $$("#dlg-body [data-life]").forEach((x) => {
        x.disabled = true;
        x.classList.add("is-busy");
      });
      try {
        if (!isLive() || !getApi()) {
          const res = store.lifecycle(id, action);
          state.selected.delete(id);
          toast(`${label}完成（本地 mock）`);
          if (action === "delete") {
            $("#dlg").close();
            await refresh({ repaint: true });
            return;
          }
          await refresh({ repaint: true });
          openDetail(res.new_id || id);
          return;
        }
        toast(`正在${label}…`);
        const res = await getApi().lifecycle(id, action);
        if (res && res.ok === false) {
          throw new Error(res.message || `${label}失败`);
        }
        toast(res?.message || `${label}成功`);
        state.selected.delete(id);
        if (res?.snapshot) applySnapFromResult(res);
        if (action === "delete") {
          $("#dlg").close();
          await refresh({ fresh: true, repaint: true });
          return;
        }
        await refresh({ fresh: true, repaint: true });
        const nextId = res?.new_id || id;
        if (state.data?.sessions?.some((x) => x.id === nextId)) openDetail(nextId);
        else {
          $("#dlg").close();
          toast(`${label}成功（列表已刷新）`);
        }
      } catch (err) {
        console.error("lifecycle", action, id, err);
        toast(`${label}失败: ` + (err.message || err));
        await refresh({ fresh: true, repaint: true });
        // 失败后重开详情，恢复按钮
        if ($("#dlg")?.open === false && state.data?.sessions?.some((x) => x.id === id)) {
          openDetail(id);
        } else if (state.data?.sessions?.some((x) => x.id === id)) {
          openDetail(id);
        }
      }
    };
  });
}

export { renderSessions, openDetail };
