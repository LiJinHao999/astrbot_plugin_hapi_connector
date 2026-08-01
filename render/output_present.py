"""推送呈现：按配置决定纯文本或结构卡/对话卡图片。

Pillow / Playwright 为可选依赖；不可用或渲染失败时永远回退文本。
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any, AsyncIterator

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover
    import logging

    logger = logging.getLogger("hapi_connector.output_present")

try:
    from astrbot.api.event import AstrMessageEvent
except ImportError:  # pragma: no cover
    AstrMessageEvent = object  # type: ignore

from . import card_render


def _cfg_get(cfg: Any, key: str, default: Any = None) -> Any:
    if cfg is None:
        return default
    try:
        if hasattr(cfg, "get"):
            val = cfg.get(key, default)
        else:
            val = cfg[key]  # type: ignore[index]
    except Exception:
        val = getattr(cfg, key, default)
    return default if val is None else val


def _cfg_dict(plugin) -> dict[str, Any]:
    """从 plugin.config 读出卡相关配置（兼容 AstrBotConfig / dict）。"""
    cfg = getattr(plugin, "config", None)
    defaults = card_render.config_defaults()
    out: dict[str, Any] = dict(defaults)
    for k in defaults:
        val = _cfg_get(cfg, k, defaults[k])
        if val is not None:
            out[k] = val
    # 再读一遍关键键，防止 defaults 漏键
    for k in ("render_mode", "render_kinds", "formula_mode", "card_custom_css", "card_font_path"):
        val = _cfg_get(cfg, k, out.get(k))
        if val is not None:
            out[k] = val
    out["render_mode"] = card_render.normalize_render_mode(out.get("render_mode"))
    return out


def build_session_list_payload(
    sessions: list[dict],
    current_sid: str | None,
    *,
    header: str = "",
    all_sessions: list[dict] | None = None,
    header_current_window: str | None = None,
    max_items: int = 40,
    scope: str = "window",
) -> dict[str, Any]:
    """结构卡用会话列表数据（信息与文本列表一致，版式独立美化）。

    - 按 path 分组
    - 主行是 session 标题
    - 序号优先用全局 all_sessions（兼容 list / list all / sw）
    - 字段拆开：status_key / flavor / model / pending / is_current，供卡片排版
    """
    from . import formatters

    index_by_sid: dict[str, int] = {}
    if all_sessions:
        for idx, session in enumerate(all_sessions, 1):
            sid = session.get("id")
            if sid and sid not in index_by_sid:
                index_by_sid[sid] = idx

    rows: list[dict[str, Any]] = []
    shown = 0
    truncated = 0
    current_path = None
    n_thinking = n_active = n_closed = 0

    for local_idx, s in enumerate(sessions, 1):
        if shown >= max_items:
            truncated = len(sessions) - shown
            break

        meta = s.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        path = str(meta.get("path") or "(无路径)")
        sid = s.get("id") or "?"
        sid_short = sid[:8]
        display_idx = index_by_sid.get(sid, local_idx)
        title = formatters.get_session_title(s)
        flavor = str(meta.get("flavor") or "?").strip().lower()
        model = s.get("modelMode") or "default"
        pending = int(s.get("pendingRequestsCount") or 0)

        if path != current_path:
            count = sum(
                1
                for x in sessions
                if (x.get("metadata") or {}).get("path", "(无路径)") == path
            )
            # 卡片分组：短路径优先展示末两段，完整 path 放 full_path
            short_path = path
            if path not in ("(无路径)",) and "/" in path:
                parts = [p for p in path.split("/") if p]
                if len(parts) > 2:
                    short_path = "…/" + "/".join(parts[-2:])
            rows.append({
                "type": "section",
                "label": short_path,
                "full_path": path,
                "detail": f"{count}",
                "count": count,
            })
            current_path = path

        if s.get("thinking"):
            status_key = "thinking"
            status = "思考中"
            n_thinking += 1
        elif s.get("active"):
            status_key = "active"
            status = "运行中"
            n_active += 1
        else:
            status_key = "closed"
            status = "已关闭"
            n_closed += 1

        is_current = bool(current_sid and sid == current_sid)
        # detail 仅作兼容/回退文本；真正出图用结构化字段
        detail_bits = [status, f"{flavor}:{model}"]
        if pending:
            detail_bits.append(f"待审 {pending}")
        if is_current:
            detail_bits.append("当前")

        rows.append({
            "type": "session",
            "index": display_idx,
            "sid_short": sid_short,
            "label": title,
            "detail": " · ".join(detail_bits),
            "status": status,
            "status_key": status_key,
            "flavor": flavor,
            "model": str(model),
            "pending": pending,
            "is_current": is_current,
            "path": path,
        })
        shown += 1

    scope_label = "全局" if scope == "all" else "当前窗口"
    subtitle_bits = [f"{scope_label} · {len(sessions)} 个"]
    if header and header not in subtitle_bits[0]:
        # 调用方自定义 header 时优先
        subtitle_bits = [header]
    if header_current_window and scope != "all":
        win = header_current_window
        if len(win) > 28:
            win = win[:12] + "…" + win[-12:]
        subtitle_bits.append(f"窗口 {win}")
    stats = []
    if n_thinking:
        stats.append(f"思考 {n_thinking}")
    if n_active:
        stats.append(f"运行 {n_active}")
    if n_closed:
        stats.append(f"关闭 {n_closed}")
    if stats:
        subtitle_bits.append(" / ".join(stats))
    if truncated:
        subtitle_bits.append(f"另有 {truncated} 个未展示")

    return {
        "title": "Session 列表" if scope != "all" else "全部 Session",
        "subtitle": " · ".join(subtitle_bits),
        "rows": rows,
        "layout": "session_list",
        # 出卡不跟 footer 文字；操作提示只在纯文本回退里（format_session_list）
        "footer": "",
    }


def build_pending_payload(
    pending: dict[str, dict],
    sessions_cache: list[dict],
) -> dict[str, Any]:
    from . import formatters

    rows = []
    total = 0
    for sid, reqs in pending.items():
        for rid, req in reqs.items():
            total += 1
            if len(rows) >= 10:
                continue
            label = formatters.session_label_short(sid, sessions_cache)
            detail = formatters.format_request_detail(req)
            index = req.get("index", 0)
            rows.append({
                "index": index,
                "label": label[:40],
                "detail": str(detail)[:100],
            })
    extra = ""
    if total > 10:
        extra = f" · 仅显示前 10 / 共 {total}"
    return {
        "title": "待审批",
        "subtitle": f"当前窗口 {total} 项{extra}",
        "rows": rows or [{"index": 0, "label": "(空)", "detail": "没有待审批的请求"}],
        "footer": "/hapi a  全部批准    /hapi allow <n>  单项    /hapi pending",
    }


def build_status_payload(session: dict[str, Any]) -> dict[str, Any]:
    """单 session 状态卡（/hapi status · /hapi s）。"""
    from . import formatters

    meta = session.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    sid = str(session.get("id") or "?")
    sid_short = sid[:8]
    flavor = str(meta.get("flavor") or "?").strip().lower()
    path = str(meta.get("path") or meta.get("cwd") or "—")
    title = formatters.get_session_title(session) or "(无标题)"
    model = session.get("modelMode") or session.get("model") or "default"
    perm = session.get("permissionMode") or "default"
    collab = session.get("collaborationMode") or "default"
    effort = session.get("effort") or session.get("modelReasoningEffort")
    service_tier = session.get("serviceTier")
    pending = int(session.get("pendingRequestsCount") or 0)

    if session.get("thinking"):
        status_key, status = "thinking", "思考中"
    elif session.get("active"):
        status_key, status = "active", "运行中"
    else:
        status_key, status = "closed", "已关闭"

    # 路径展示：末两段优先
    path_show = path
    if path not in ("—", "", "?") and "/" in path:
        parts = [p for p in path.split("/") if p]
        if len(parts) > 2:
            path_show = "…/" + "/".join(parts[-2:])

    rows: list[dict[str, Any]] = [
        {
            "type": "kv",
            "label": "状态",
            "detail": status,
            "status_key": status_key,
        },
        {"type": "kv", "label": "Agent", "detail": flavor},
        {"type": "kv", "label": "模型", "detail": str(model)},
        {"type": "kv", "label": "权限", "detail": str(perm)},
    ]
    if effort:
        rows.append({"type": "kv", "label": "推理", "detail": str(effort)})
    if service_tier:
        rows.append({"type": "kv", "label": "Service", "detail": str(service_tier)})
    if collab and (collab != "default" or flavor == "codex"):
        rows.append({"type": "kv", "label": "协作", "detail": str(collab)})
    if pending:
        rows.append({"type": "kv", "label": "待审批", "detail": str(pending)})
    rows.append({"type": "kv", "label": "路径", "detail": path_show, "full": path})
    rows.append({"type": "kv", "label": "ID", "detail": sid_short, "full": sid})

    return {
        "title": title,
        "subtitle": f"{flavor} · {sid_short} · {status}",
        "rows": rows,
        "layout": "status",
        "status": status,
        "status_key": status_key,
        "flavor": flavor,
        "sid_short": sid_short,
        "footer": "/hapi sw  切换    /hapi list  列表    /hapi msg  最近消息",
    }


def build_routes_payload(
    *,
    session_rows: list[dict[str, Any]] | None = None,
    primary_umo: str | None = None,
    primary_title: str | None = None,
    flavor_routes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """推送路由结构卡。

    session_rows: [{sid_short, flavor, title, window_title}]
    flavor_routes: [{flavor, window_title}]
    """
    rows: list[dict[str, Any]] = []
    n_bind = 0
    n_flavor = 0

    if session_rows:
        rows.append({
            "type": "section",
            "label": "会话绑定",
            "detail": f"{len(session_rows)}",
            "count": len(session_rows),
        })
        for i, r in enumerate(session_rows, 1):
            n_bind += 1
            flavor = str(r.get("flavor") or "?")
            title = str(r.get("title") or "")
            sid = str(r.get("sid_short") or "")
            win = str(r.get("window_title") or r.get("umo") or "")
            label = f"[{flavor}] {title}" if title else f"[{flavor}]"
            detail = f"→ {win}" if win else ""
            rows.append({
                "type": "row",
                "index": i,
                "sid_short": sid,
                "label": label[:48],
                "detail": detail[:80],
            })

    if primary_title or primary_umo:
        rows.append({
            "type": "section",
            "label": "默认发送窗口",
            "detail": "",
            "count": 1,
        })
        rows.append({
            "type": "row",
            "index": 0,
            "label": "primary",
            "detail": str(primary_title or primary_umo or ""),
        })

    if flavor_routes:
        rows.append({
            "type": "section",
            "label": "Agent 默认窗口",
            "detail": f"{len(flavor_routes)}",
            "count": len(flavor_routes),
        })
        for r in flavor_routes:
            n_flavor += 1
            rows.append({
                "type": "row",
                "index": 0,
                "label": str(r.get("flavor") or "?"),
                "detail": str(r.get("window_title") or r.get("umo") or ""),
            })

    if not rows:
        rows = [{"type": "row", "index": 0, "label": "(空)", "detail": "暂无推送路由"}]

    bits = []
    if n_bind:
        bits.append(f"绑定 {n_bind}")
    if primary_title or primary_umo:
        bits.append("有默认窗口")
    if n_flavor:
        bits.append(f"Agent {n_flavor}")
    subtitle = " · ".join(bits) if bits else "暂无路由"

    return {
        "title": "推送路由",
        "subtitle": subtitle,
        "rows": rows,
        "footer": "/hapi bind  设默认推送窗口    /hapi bind <agent>  设 Agent 推送窗口    /hapi routes",
    }


def prepare_agent_body_for_card(text: str) -> str:
    """聊天文案 → 卡片正文：emoji 工具行换成 ASCII 标记，再剥剩余 emoji。

    文字推送仍用 formatters 的 emoji 版；卡片字体不含 emoji，需结构化标记
    才能在 Pillow 里区分工具调用 / 任务清单 / 系统事件。

    Edit 等多行差异：主行 [Tool] …，后续缩进的 -/+ 行保留，供解析器并入 tool.detail。
    """
    import re

    if not text:
        return ""
    out_lines: list[str] = []
    for raw in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw
        # Todo 状态行（缩进 + emoji）
        m = re.match(r"^(\s*)(✅|🔄|⬜|✔️|✔|□|■)\s*(.*)$", line)
        if m:
            ind, icon, rest = m.group(1), m.group(2), m.group(3)
            tag = {
                "✅": "done",
                "✔️": "done",
                "✔": "done",
                "🔄": "run",
                "⬜": "todo",
                "□": "todo",
                "■": "done",
            }.get(icon, "todo")
            out_lines.append(f"{ind}[{tag}] {rest}".rstrip())
            continue
        # 工具调用主行
        if line.startswith("🛠️"):
            rest = line[len("🛠️") :].lstrip()
            if rest.startswith("TodoWrite"):
                rest2 = rest[len("TodoWrite") :].lstrip(" :：")
                if rest2 in ("任务列表", "任务列表:"):
                    out_lines.append("[Tool] TodoWrite")
                elif rest2:
                    out_lines.append(f"[Tool] TodoWrite: {rest2}")
                else:
                    out_lines.append("[Tool] TodoWrite")
            else:
                out_lines.append(f"[Tool] {rest}" if rest else "[Tool]")
            continue
        if line.startswith("❓"):
            rest = line[len("❓") :].lstrip()
            out_lines.append(f"[Ask] {rest}" if rest else "[Ask]")
            continue
        # Edit 差异续行（已是 "  - "/"  + "/"  …"）原样保留
        out_lines.append(line)
    return _strip_emoji("\n".join(out_lines))


def _strip_emoji(text: str) -> str:
    """卡片用：去掉 emoji / 杂符号，避免 Pillow 缺字形出方块或发灰。"""
    import re

    if not text:
        return ""
    # 常见 emoji / 杂项符号区（保留中英文、数字、标点、换行）
    text = re.sub(
        "["
        "\U0001F300-\U0001FAFF"  # 杂项符号与象形
        "\U00002700-\U000027BF"  # Dingbats
        "\U00002600-\U000026FF"  # 杂项符号
        "\U0000FE00-\U0000FE0F"  # 变体选择符
        "\U0000200D"             # ZWJ
        "\U000020E3"             # 键帽
        "]+",
        "",
        text,
    )
    # 文本列表里常见的装饰前缀（再保险；工具行应已在 prepare_agent_body_for_card 转写）
    for ch in ("🏷️", "💬", "📂", "🤖", "📋", "🛠️", "💭", "🟢", "⚪", "⚠️", "💡", "📁", "❓", "✅", "🔄", "⬜"):
        text = text.replace(ch, "")
    # 行内多余空白：保留行首缩进（任务清单缩进）
    fixed_lines = []
    for ln in text.split("\n"):
        m = re.match(r"^([ \t]*)(.*)$", ln)
        ind, rest = (m.group(1), m.group(2)) if m else ("", ln)
        rest = re.sub(r"[ \t]{2,}", " ", rest).rstrip()
        fixed_lines.append(ind + rest)
    text = "\n".join(fixed_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 装饰分隔符残留「 |  · 」等压扁
    text = re.sub(r"[ \t]*·[ \t]*·[ \t]*", " · ", text)
    text = re.sub(r"[ \t]*\|[ \t]*\|[ \t]*", " | ", text)
    return text.strip()


_RE_HAPI_GENIMG = re.compile(
    r"!\[([^\]]*)\]\(hapi-genimg://([^)\s]+)\)",
    re.IGNORECASE,
)


async def resolve_hapi_genimg_markers(
    body: str,
    *,
    client: Any,
    session_id: str,
    max_images: int = 6,
) -> str:
    """把 ``![alt](hapi-genimg://id)`` 下载成临时文件，改写为 ``![alt](/tmp/...)``。

    供对话卡 Pillow 嵌真实像素。失败时保留可读占位，不拖垮整条消息。
    临时文件由调用方在出图后延迟清理（或 OS 清 /tmp）。
    """
    text = body or ""
    if "hapi-genimg://" not in text or not client or not session_id:
        return text

    from ..core import session_ops

    cache: dict[str, str] = {}  # imageId -> local path or ""
    out: list[str] = []
    last = 0
    count = 0
    for m in _RE_HAPI_GENIMG.finditer(text):
        out.append(text[last : m.start()])
        alt = m.group(1) or "generated-image"
        image_id = (m.group(2) or "").strip()
        last = m.end()
        if not image_id or count >= max_images:
            out.append(f"[图片] {alt}")
            continue
        if image_id in cache:
            path = cache[image_id]
            if path:
                out.append(f"![{alt}]({path})")
            else:
                out.append(f"[图片] {alt}")
            continue
        try:
            raw, mime, err = await session_ops.fetch_generated_image(
                client, session_id, image_id
            )
            if err or not raw:
                logger.info(
                    "genimg fetch fail sid=%s id=%s err=%s",
                    (session_id or "")[:8],
                    image_id[:16],
                    err,
                )
                cache[image_id] = ""
                out.append(f"[图片] {alt}")
                continue
            ext = ".png"
            mt = (mime or "").lower()
            if "jpeg" in mt or "jpg" in mt:
                ext = ".jpg"
            elif "webp" in mt:
                ext = ".webp"
            elif "gif" in mt:
                ext = ".gif"
            fd, path = tempfile.mkstemp(prefix="hapi_genimg_", suffix=ext)
            os.close(fd)
            with open(path, "wb") as f:
                f.write(raw)
            cache[image_id] = path
            count += 1
            out.append(f"![{alt}]({path})")
            logger.info(
                "genimg cached sid=%s id=%s bytes=%s path=%s",
                (session_id or "")[:8],
                image_id[:16],
                len(raw),
                path,
            )
        except Exception as e:
            logger.warning("genimg resolve error: %s", e)
            cache[image_id] = ""
            out.append(f"[图片] {alt}")
    out.append(text[last:])
    return "".join(out)


def build_message_payload(
    *,
    label: str,
    body: str,
    title: str = "",
    footer: str = "",
    session_title: str = "",
) -> dict[str, Any]:
    """对话卡 payload。

    - title：会话标题（对话名），缺省时从 label 首行 / session_title 取
    - subtitle：路径 · flavor · sid 等元信息（label 其余行）
    - footer：默认空；不再附 output=simple 等尾注（避免图片后再冒出一条文本）
    - body 可含 ``![alt](/local/path)``；由 Pillow 嵌图
    """
    lines = [
        p.strip()
        for p in _strip_emoji(label or "").splitlines()
        if p.strip()
    ]
    conv_title = _strip_emoji(session_title or title or "")
    if not conv_title and lines:
        conv_title = lines[0]
        lines = lines[1:]
    elif conv_title and lines and lines[0] == conv_title:
        # session_title 与 label 首行重复时去掉，避免副标题再塞一遍
        lines = lines[1:]
    if not conv_title:
        conv_title = "对话"
    sub = " · ".join(lines)
    # 在副标题前加轻量类型提示，放在开头而不是 footer
    if sub:
        sub = f"Agent 消息 · {sub}"
    else:
        sub = "Agent 消息"
    from . import formatters as _fmt

    raw_body = body or ""
    # 出卡前再挡一层：SDK tool_progress / heartbeat 整包 JSON 不当正文
    if _fmt.is_sdk_noise_text(raw_body):
        raw_body = ""
    return {
        "title": conv_title,
        "subtitle": sub,
        # 工具调用等：先转 ASCII 标记再剥 emoji，避免卡片丢结构
        # 注意：本地路径 ![alt](/tmp/...) 需保留，勿被剥坏
        "body": prepare_agent_body_for_card(raw_body),
        "footer": _strip_emoji(footer or ""),
    }


def build_permission_payload(
    *,
    label: str,
    detail: str,
    total: int,
    session_total: int,
    index: int,
    kind: str = "permission",
    req: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """权限请求 / 问题请求结构卡。

    kind: "permission" | "question"
    """
    from . import formatters

    clean_label = _strip_emoji(label or "")
    label_lines = [p.strip() for p in clean_label.splitlines() if p.strip()]
    sess_title = label_lines[0] if label_lines else ""
    meta = " · ".join(label_lines[1:]) if len(label_lines) > 1 else ""

    is_q = kind == "question" or (
        req is not None and formatters.is_question_request(req)
    )
    title = "问题请求" if is_q else "权限请求"
    sub_bits = []
    if index:
        sub_bits.append(f"序号 {index}")
    if sess_title:
        sub_bits.append(sess_title)
    if meta:
        sub_bits.append(meta)
    subtitle = " · ".join(sub_bits) if sub_bits else (sess_title or "")

    rows: list[dict[str, Any]] = []
    if is_q and req is not None:
        args = req.get("arguments") or {}
        questions = args.get("questions", []) if isinstance(args, dict) else []
        if not questions:
            rows.append({
                "type": "row",
                "index": 0,
                "label": "问题",
                "detail": _strip_emoji(str(detail or ""))[:200] or "(无内容)",
            })
        for qi, q in enumerate(questions):
            header = q.get("header") or q.get("id") or f"问题 {qi + 1}"
            qtext = str(q.get("question") or "").strip()
            rows.append({
                "type": "section",
                "label": _strip_emoji(str(header)),
                "detail": "",
                "count": 0,
            })
            if qtext:
                rows.append({
                    "type": "row",
                    "index": 0,
                    "label": _strip_emoji(qtext)[:120],
                    "detail": "",
                })
            for oi, opt in enumerate(q.get("options") or [], 1):
                olabel = str(opt.get("label") or f"选项 {oi}")
                odesc = str(opt.get("description") or "").strip()
                rows.append({
                    "type": "row",
                    "index": oi,
                    "label": _strip_emoji(olabel)[:80],
                    "detail": _strip_emoji(odesc)[:120] if odesc else "",
                })
        footer = "/hapi answer  交互回答    /hapi answer <序号>"
    else:
        tool_detail = _strip_emoji(str(detail or ""))
        # detail 形如 "Bash: npm test" → 拆开
        if ":" in tool_detail:
            tool, _, rest = tool_detail.partition(":")
            rows.append({
                "type": "row",
                "index": 0,
                "label": "工具",
                "detail": tool.strip()[:60] or "?",
            })
            if rest.strip():
                rows.append({
                    "type": "row",
                    "index": 0,
                    "label": "详情",
                    "detail": rest.strip()[:200],
                })
        else:
            rows.append({
                "type": "row",
                "index": 0,
                "label": "请求",
                "detail": tool_detail[:200] or "(无详情)",
            })
        footer = (
            "/hapi a  全部批准    /hapi allow <序号>  单项\n"
            "/hapi deny  全部拒绝    /hapi pending  列表"
        )

    rows.append({
        "type": "row",
        "index": 0,
        "label": "待审批",
        "detail": f"全局 {total} · 本会话 {session_total} · 本条序号 {index}",
    })

    return {
        "title": title,
        "subtitle": subtitle,
        "rows": rows,
        "footer": footer,
    }


def try_render_png(plugin, kind: str, data: dict[str, Any]) -> card_render.RenderResult | None:
    """若配置要求出卡且引擎可用，返回 RenderResult；否则 None（调用方发文本）。"""
    cfg = _cfg_dict(plugin)
    mode = card_render.normalize_render_mode(cfg.get("render_mode"))
    kinds = card_render.parse_kinds(cfg.get("render_kinds"))
    formula_mode = card_render.normalize_formula_mode(cfg.get("formula_mode"))

    if not card_render.should_render_card(kind=kind, render_mode=mode, kinds=kinds):
        logger.info(
            "card skip kind=%s mode=%s kinds=%s (需 render_mode=card 且 kinds 含该类型)",
            kind, mode, ",".join(kinds),
        )
        return None

    has_formula = card_render.payload_has_formula(data)

    # plain：含公式 → 只发文字
    if formula_mode == "plain" and has_formula:
        logger.info("card skip kind=%s formula_mode=plain (正文含公式，回退纯文本)", kind)
        return None

    # formula_only：仅 Agent 对话且正文含公式才出图；其它消息（含无公式对话）只发文字
    if formula_mode == "formula_only":
        if kind != "message":
            # 结构卡不受「仅公式消息」约束，仍按 render_kinds 出图
            pass
        elif not has_formula:
            logger.info(
                "card skip kind=message formula_mode=formula_only (无公式，只发文字)",
            )
            return None

    style = card_render.style_from_config(cfg)
    result = card_render.render_card(kind, data, style, formula_mode=formula_mode)
    if not result.ok or not result.png:
        logger.warning(
            "card present fallback kind=%s error=%s engine=%s",
            kind, result.error, result.engine,
        )
        return None
    logger.info(
        "card rendered kind=%s engine=%s bytes=%s ms=%.1f",
        kind, result.engine, result.bytes_len, result.ms,
    )
    return result


def write_temp_png(png: bytes) -> str:
    fd, path = tempfile.mkstemp(prefix="hapi_card_", suffix=".png")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(png)
    return path


async def present(
    plugin,
    event: AstrMessageEvent,
    kind: str,
    data: dict[str, Any],
    fallback_text: str,
) -> AsyncIterator:
    """yield 一条 event 结果：优先图片卡，否则纯文本。

    出卡成功时只发图，不另附 footer 文本（避免「操作提示」再冒一条消息）。
    footer 若有内容，由卡片引擎画在图内底部。
    """
    result = try_render_png(plugin, kind, data)
    if result is None:
        yield event.plain_result(fallback_text)
        return

    path = None
    try:
        path = write_temp_png(result.png or b"")
        if hasattr(event, "image_result"):
            yield event.image_result(path)
            return

        import astrbot.api.message_components as Comp

        chain = [Comp.Image.fromFileSystem(path)]
        if hasattr(event, "chain_result"):
            yield event.chain_result(chain)
        else:
            yield event.plain_result(fallback_text)
    except Exception as e:
        logger.warning("present image failed: %s", e)
        yield event.plain_result(fallback_text)
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


async def present_push(
    plugin,
    notification_mgr,
    kind: str,
    data: dict[str, Any],
    fallback_text: str,
    session_id: str,
    sessions_cache: list[dict],
) -> bool:
    """SSE/后台推送路径：尝试发图，失败则发文本。返回是否已发送（图或文）。"""
    result = try_render_png(plugin, kind, data)
    if result is None or not result.png:
        await notification_mgr.push_notification(
            fallback_text, session_id, sessions_cache
        )
        return True

    path = None
    try:
        path = write_temp_png(result.png)
        # 只发图：footer 已画在卡内，不再 caption 再冒一条文字
        logger.info("card push kind=%s path=%s sid=%s", kind, path, (session_id or "")[:8])
        await notification_mgr.push_image_notification(
            path,
            session_id,
            sessions_cache,
            caption="",
            dedupe_key=f"card:{kind}:{session_id}:{hash(fallback_text) & 0xFFFFFFFF:x}",
        )
        return True
    except Exception as e:
        logger.warning("present_push image failed kind=%s: %s", kind, e)
        await notification_mgr.push_notification(
            fallback_text, session_id, sessions_cache
        )
        return True
    finally:
        if path:
            # 稍延迟删除，避免部分适配器异步读文件时已被删
            try:
                import asyncio

                async def _unlink_later(p: str):
                    try:
                        await asyncio.sleep(30)
                        os.remove(p)
                    except OSError:
                        pass

                try:
                    asyncio.get_running_loop().create_task(_unlink_later(path))
                except RuntimeError:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            except Exception:
                try:
                    os.remove(path)
                except OSError:
                    pass
