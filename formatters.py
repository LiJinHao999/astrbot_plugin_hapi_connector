"""纯函数：格式化 session 标签、消息预览、帮助文本等"""

import json


def extract_text_preview(content: dict, max_len: int = 80) -> str | None:
    """从消息 content 中提取文本预览（通用，适配所有 agent）。
    返回 None 表示该消息不应显示（如 token_count、ready 事件等噪音）。
    max_len <= 0 表示不截断。
    """
    if max_len <= 0:
        max_len = 999999
    inner = content.get("content", {})

    # 纯文本（部分 agent 直接返回字符串）
    if isinstance(inner, str):
        return inner[:max_len] if inner.strip() else None

    # content blocks 列表（标准格式）
    if isinstance(inner, list):
        return _extract_from_blocks(inner, max_len)

    # 单个 block（dict）
    if isinstance(inner, dict):
        return _extract_from_block(inner, max_len)

    return str(inner)[:max_len]


def _extract_from_blocks(blocks: list, max_len: int) -> str | None:
    """从 content blocks 列表中提取文本预览，只保留有意义的内容"""
    parts = []
    for block in blocks:
        if isinstance(block, str):
            if block.strip():
                parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        text = _extract_from_block(block, max_len)
        if text is not None:
            parts.append(text)

    if not parts:
        return None
    return "\n".join(parts)


def _extract_from_block(block: dict, max_len: int) -> str | None:
    """从单个 content block 中提取文本，返回 None 表示跳过"""
    btype = block.get("type", "")

    # ── 文本内容（模型回复）──
    if btype == "text":
        text = block.get("text", "")
        return text[:max_len] if text.strip() else None

    # ── 工具调用（Claude: tool_use / Codex: tool-call 等）──
    if btype in ("tool_use", "tool-call"):
        return _fmt_tool_call(block, max_len)

    # ── 工具返回：跳过，只关注模型文本和工具调用 ──
    if btype in ("tool_result", "tool-call-result"):
        return None

    # ── 包装类型（output/input）：内容在 data 字段里，递归处理 ──
    if btype in ("output", "input"):
        data = block.get("data")
        if isinstance(data, dict):
            return _extract_from_block(data, max_len)
        if isinstance(data, list):
            return _extract_from_blocks(data, max_len)
        if isinstance(data, str) and data.strip():
            return data[:max_len]
        return None

    # ── Codex 包装格式 {"type": "codex", "data": {...}} ──
    if btype == "codex":
        return _extract_codex_block(block.get("data", {}), max_len)

    # ── 事件 → [System] ──
    if btype == "event":
        event_data = block.get("data", {})
        event_type = event_data.get("type", "?") if isinstance(event_data, dict) else "?"
        if event_type == "ready":
            return None
        # message 类型事件：提取实际消息内容（如 "Context was reset"）
        if event_type == "message" and isinstance(event_data, dict):
            msg = event_data.get("message", "")
            if msg:
                return f"[System]: {msg}"
        return f"[System]: {event_type}"

    # ── Summary（Codex 等 agent 的会话摘要）──
    if btype == "summary":
        text = block.get("summary", "")
        return f"[Summary]: {text[:max_len]}" if text else None

    # ── 跳过噪音 ──
    if btype in ("token_count", "thinking"):
        return None

    # ── 嵌套消息结构（如 {"role": "user", "content": [...]} ）──
    if "role" in block and "content" in block:
        nested = block["content"]
        if isinstance(nested, list):
            return _extract_from_blocks(nested, max_len)
        if isinstance(nested, dict):
            return _extract_from_block(nested, max_len)
        if isinstance(nested, str) and nested.strip():
            return nested[:max_len]
        return None

    # ── HAPI 消息包装（含 message 字段的元数据结构）──
    msg = block.get("message")
    if isinstance(msg, dict) and "role" in msg and "content" in msg:
        nested = msg["content"]
        if isinstance(nested, list):
            return _extract_from_blocks(nested, max_len)
        if isinstance(nested, dict):
            return _extract_from_block(nested, max_len)
        if isinstance(nested, str) and nested.strip():
            return nested[:max_len]
        return None

    # ── 未识别或无 type：尝试从常见字段提取文本 ──
    for key in ("text", "data", "content", "message", "output"):
        val = block.get(key)
        if val is None:
            continue
        if isinstance(val, str) and val.strip():
            prefix = f"[{btype}] " if btype else ""
            return f"{prefix}{val[:max_len]}"
        if isinstance(val, list):
            result = _extract_from_blocks(val, max_len)
            if result:
                return result
        if isinstance(val, dict):
            result = _extract_from_block(val, max_len)
            if result:
                return result

    # 兜底
    raw = json.dumps(block, ensure_ascii=False)
    return raw[:max_len] if raw != "{}" else None


_TODO_STATUS_ICON = {
    "completed": "✅",
    "in_progress": "🔄",
    "pending": "⬜",
}


def _fmt_todo_write(inp: dict) -> str:
    """格式化 TodoWrite 工具调用，将 todos 列表渲染为可读清单"""
    todos = inp.get("todos", [])
    if not todos:
        return "🛠️ TodoWrite"
    lines = ["🛠️ TodoWrite 任务列表:"]
    for item in todos:
        status = item.get("status", "pending")
        icon = _TODO_STATUS_ICON.get(status, "⬜")
        content = item.get("content", item.get("activeForm", "?"))
        lines.append(f"  {icon} {content}")
    return "\n".join(lines)


def _fmt_tool_call(block: dict, max_len: int) -> str:
    """格式化工具调用 block"""
    name = block.get("name", "?")
    inp = block.get("input", {})
    if isinstance(inp, dict):
        if name == "TodoWrite":
            return _fmt_todo_write(inp)
        cmd = inp.get("command", "")
        if cmd:
            return f"🛠️ {name}: {cmd[:max_len]}"
        args_str = json.dumps(inp, ensure_ascii=False)[:max_len]
        return f"🛠️ {name}: {args_str}"
    return f"🛠️ {name}"



def _extract_codex_block(data: dict, max_len: int) -> str | None:
    """处理 Codex 专有的包装格式"""
    if not isinstance(data, dict):
        return str(data)[:max_len]
    dtype = data.get("type", "")
    if dtype == "text":
        text = data.get("text", "")
        return text[:max_len] if text.strip() else None
    if dtype == "tool-call":
        return _fmt_tool_call(data, max_len)
    if dtype == "tool-call-result":
        return None
    if dtype == "token_count":
        return None
    if dtype in ("reasoning", "agent_reasoning"):
        return None
    if dtype == "message":
        msg_text = data.get("message", "")
        return msg_text[:max_len] if msg_text else "[消息]"
    return f"[{dtype}]" if dtype else None


def session_label_short(sid: str, sessions_cache: list[dict]) -> str:
    """获取 session 的简短标识（用于 SSE 推送，多行格式）"""
    session = None
    for s in sessions_cache:
        if s.get("id") == sid:
            session = s
            break

    if not session:
        return f"🏷️ {sid[:8]}"

    meta = session.get("metadata", {})
    flavor = meta.get("flavor", "?")
    summary = (meta.get("summary") or {}).get("text", "")
    path = meta.get("path", "")

    title = summary or "(无标题)"
    if len(path) > 40:
        path = "..." + path[-37:]

    return f"💬 {title}\n📂 {path}\n🤖 {flavor} | 🏷️ {sid[:8]}"


def group_sessions_by_path(sessions: list[dict]) -> dict[str, list[dict]]:
    """按 path 分组 session"""
    groups: dict[str, list[dict]] = {}
    for s in sessions:
        path = s.get("metadata", {}).get("path", "(无路径)")
        if path not in groups:
            groups[path] = []
        groups[path].append(s)
    return groups


def format_bind_status(sessions: list[dict], session_owners: dict[str, str], window_states: dict[str, dict] = None) -> str:
    """格式化全局绑定状态（复用 session 列表格式 + 绑定信息 + 窗口状态）"""
    if not sessions:
        return "没有任何 session"

    lines = [f"=== 全局绑定状态 ===\n共 {len(sessions)} 个 Session:"]

    current_path = None
    for idx, s in enumerate(sessions, 1):
        meta = s.get("metadata", {})
        path = meta.get("path", "(无路径)")

        if path != current_path:
            count = sum(1 for x in sessions if x.get("metadata", {}).get("path", "(无路径)") == path)
            lines.append(f"\n📁 {path} ({count})")
            current_path = path

        sid = s.get("id", "?")
        sid_short = sid[:8]
        summary = (meta.get("summary") or {}).get("text", "") or "(无标题)"
        flavor = meta.get("flavor", "?")
        model = s.get("modelMode", "default")
        pending = s.get("pendingRequestsCount", 0)

        if s.get("thinking"):
            status = "💭思考中"
        elif s.get("active"):
            status = "🟢运行中"
        else:
            status = "⚪已关闭"

        lines.append(f"[{idx} | 🏷️{sid_short}] {summary}")

        parts = [status, f"🤖{flavor}:{model}"]
        if pending:
            parts.append(f"⚠️ {pending}待审批")

        # 添加绑定信息
        owner = session_owners.get(sid)
        if owner:
            owner_display = owner[:20] + "..." if len(owner) > 20 else owner
            parts.append(f"📌{owner_display}")

        # 添加窗口状态（显示当前活跃交互的窗口）
        if window_states:
            active_umo = next((umo for umo, state in window_states.items() if state.get("current_session") == sid), None)
            if active_umo:
                parts.append(f"🪟正在交互")

        lines.append(" | ".join(parts))

    return "\n".join(lines)


def format_session_list(
    sessions: list[dict],
    current_sid: str | None = None,
    all_sessions: list[dict] | None = None,
    header_current_window: str | None = None,
) -> str:
    """格式化 session 列表；可选沿用全局 session 列表编号。"""
    if not sessions:
        return "没有任何 session"

    lines: list[str] = []
    if header_current_window:
        lines.append(f"当前窗口 ID: {header_current_window}")
        lines.append("")

    lines.append(f"共 {len(sessions)} 个 Session:")
    index_by_sid: dict[str, int] = {}
    if all_sessions:
        for idx, session in enumerate(all_sessions, 1):
            sid = session.get("id")
            if sid and sid not in index_by_sid:
                index_by_sid[sid] = idx

    # 按 path 分组但保持原始顺序
    current_path = None
    for local_idx, s in enumerate(sessions, 1):
        meta = s.get("metadata", )
        path = meta.get("path", "(无路径)")

        # 当 path 变化时显示分组标题
        if path != current_path:
            # 统计该 path 下的 session 数量
            count = sum(1 for x in sessions if x.get("metadata", {}).get("path", "(无路径)") == path)
            lines.append(f"\n📁 {path} ({count})")
            current_path = path

        sid = s.get("id", "?")
        sid_short = sid[:8]
        display_idx = index_by_sid.get(sid, local_idx)
        summary = (meta.get("summary") or {}).get("text", "") or "(无标题)"
        flavor = meta.get("flavor", "?")
        model = s.get("modelMode", "default")
        pending = s.get("pendingRequestsCount", 0)

        # 状态
        if s.get("thinking"):
            status = "💭思考中"
        elif s.get("active"):
            status = "🟢运行中"
        else:
            status = "⚪已关闭"

        # 第一行：[序号|🏷️sid] 标题
        lines.append(f"[{display_idx} | 🏷️{sid_short}] {summary}")

        # 第二行：状态 | 模型 | 待审批 | 当前
        parts = [status, f"🤖{flavor}:{model}"]
        if pending:
            parts.append(f"⚠️ {pending}待审批")
        if current_sid and sid == current_sid:
            parts.append("<<当前")
        lines.append(" | ".join(parts))

    lines.append(f"\n💡 切换会话：/hapi sw <序号或ID前缀>")
    return "\n".join(lines)


def format_session_status(s: dict) -> str:
    """格式化单个 session 状态"""
    meta = s.get("metadata", {})
    sid = s.get("id", "?")
    flavor = meta.get("flavor", "?")
    path = meta.get("path", "?")
    active = s.get("active", False)
    thinking = s.get("thinking", False)
    perm = s.get("permissionMode", "default")
    model = s.get("modelMode", "default")
    summary = (meta.get("summary") or {}).get("text", "(无标题)")

    lines = [
        f"Session:  {sid[:8]}...",
        f"标题:     {summary}",
        f"Flavor:   {flavor}",
        f"Path:     {path}",
        f"Active:   {active}",
        f"Thinking: {thinking}",
        f"权限模式: {perm}",
        f"模型:     {model}",
    ]
    return "\n".join(lines)


def format_messages(messages: list[dict], max_preview: int = 0) -> str:
    """格式化消息列表（无 seq 编号，仅 role: text 格式）"""
    if not messages:
        return "(暂无消息)"

    lines = []
    for m in messages:
        content = m.get("content", {})
        role = content.get("role", "?")
        text = extract_text_preview(content, max_len=max_preview)
        if text is None:
            continue
        lines.append(f"{role}: {text}")

    return "\n".join(lines) if lines else "(暂无可显示的消息)"


def _get_message_role(msg: dict) -> str:
    """从 HAPI 消息中提取 role（处理包装层）"""
    content = msg.get("content", {})
    if not isinstance(content, dict):
        return "?"
    # 检查 HAPI 包装层（严格匹配：message 内必须同时有 role 和 content）
    wrapper = content.get("message")
    if isinstance(wrapper, dict) and "role" in wrapper and "content" in wrapper:
        return wrapper.get("role", "?")
    return content.get("role", "?")


def _is_human_input(msg: dict) -> bool:
    """判断消息是否为真实用户文本输入（非 tool_result 等协议消息）"""
    content = msg.get("content", {})
    if not isinstance(content, dict):
        return False
    role = content.get("role", "")
    inner = content
    # 检查 HAPI 包装层（严格匹配：message 内必须同时有 role 和 content）
    wrapper = content.get("message")
    if isinstance(wrapper, dict) and "role" in wrapper and "content" in wrapper:
        role = wrapper.get("role", "")
        inner = wrapper
    if role != "user":
        return False
    return _inner_has_text(inner.get("content", ""))


def _inner_has_text(inner) -> bool:
    """递归检查 content 内部是否包含真实文本"""
    if isinstance(inner, str):
        return bool(inner.strip())
    if isinstance(inner, list):
        return any(
            isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip()
            for b in inner
        )
    if isinstance(inner, dict):
        # 单个 text block
        if inner.get("type") == "text":
            return bool(inner.get("text", "").strip())
        # 嵌套消息结构 {"role": "user", "content": [...]}
        if "content" in inner:
            return _inner_has_text(inner["content"])
    return False


def split_into_rounds(messages: list[dict]) -> list[list[dict]]:
    """按用户输入将消息切分为轮次列表。
    一轮 = 一条用户文本输入 + 后续所有 agent 响应（直到下一条用户输入之前）。
    """
    rounds = []
    current = []
    for msg in messages:
        if _is_human_input(msg) and current:
            rounds.append(current)
            current = []
        current.append(msg)
    if current:
        rounds.append(current)
    return rounds


_PASSTHROUGH_PREFIXES = ("[System]:", "[Summary]:", "🛠️")


def format_agent_line(text: str) -> str:
    """格式化 agent 消息：工具调用 → 🛠️ ...，系统事件/摘要 → 透传，普通文本 → [Message]"""
    if any(text.startswith(p) for p in _PASSTHROUGH_PREFIXES):
        return text
    return f"[Message]: {text}"


def format_round(round_msgs: list[dict], round_idx: int, total_rounds: int,
                 max_preview: int = 0) -> str:
    """格式化单轮消息，带轮次标题"""
    lines = [f"── 第 {round_idx}/{total_rounds} 轮 ──"]
    for m in round_msgs:
        content = m.get("content", {})
        role = _get_message_role(m)
        text = extract_text_preview(content, max_len=max_preview)
        if text is None:
            continue
        if role in ("agent", "assistant"):
            lines.append(format_agent_line(text))
        elif role == "user":
            lines.append(f"[User Input]: {text}")
        else:
            lines.append(f"{role}: {text}")
    # 如果过滤后只剩标题行，说明该轮无可显示内容
    if len(lines) == 1:
        lines.append("(无可显示的消息)")
    return "\n\n".join(lines)


_QUESTION_TOOLS = {"AskUserQuestion", "ask_user_question"}
_COMPACT_TOOL = "__compact__"


def is_question_request(req: dict) -> bool:
    """判断是否为 AskUserQuestion 类型的请求"""
    return req.get("tool", "") in _QUESTION_TOOLS


def is_compact_request(req: dict) -> bool:
    """判断是否为插件合成的上下文压缩请求"""
    return req.get("tool", "") == _COMPACT_TOOL


def format_question_notification(req: dict, label: str, total: int, session_total: int, index: int) -> str:
    """格式化 AskUserQuestion SSE 通知"""
    args = req.get("arguments") or {}
    questions = args.get("questions", []) if isinstance(args, dict) else []
    lines = [f"❓ 问题请求 {label}"]
    for q in questions:
        if q.get("header"):
            lines.append(f"  [{q['header']}]")
        if q.get("question"):
            lines.append(f"  {q['question']}")
        for i, opt in enumerate(q.get("options", []), 1):
            desc = f" — {opt['description']}" if opt.get("description") else ""
            lines.append(f"    [{i}] {opt['label']}{desc}")
    lines += ["", f"当前总共 {total} 个待审批，当前会话共 {session_total} 个待审批，此请求审批序号 {index}", "💡 使用此命令交互式审批：/hapi answer"]
    return "\n".join(lines)


def format_permission_notification(label: str, detail: str, total: int, session_total: int, index: int) -> str:
    """格式化普通权限审批通知，复用统一的会话前缀。"""
    lines = [
        f"🔐 权限请求 {label}",
        f"  {detail}",
        "",
        f"当前总共 {total} 个待审批，当前会话共 {session_total} 个待审批，此请求审批序号 {index}",
        "",
        "审批指令:",
        "  /hapi a        全部批准",
        "  /hapi allow <序号>  批准单个",
        "  /hapi deny     全部拒绝",
        "  /hapi deny <序号> 拒绝单个",
        "  /hapi pending   查看完整列表",
    ]
    return "\n".join(lines)


def format_request_detail(req: dict) -> str:
    """格式化权限请求详情（工具 + 关键参数）"""
    tool = req.get("tool", "?")
    if tool == _COMPACT_TOOL:
        return "压缩上下文 (/compact)"
    args = req.get("arguments", {})
    if not isinstance(args, dict) or not args:
        return tool
    cmd = args.get("command", "")
    if cmd:
        return f"{tool}: {cmd[:150]}"
    args_str = json.dumps(args, ensure_ascii=False)
    if len(args_str) > 120:
        args_str = args_str[:120] + "..."
    return f"{tool}: {args_str}"


def format_pending_requests(pending: dict[str, dict], sessions_cache: list[dict]) -> str:
    """格式化所有待审批请求"""
    items = []
    for sid, reqs in pending.items():
        for rid, req in reqs.items():
            items.append((sid, rid, req))

    if not items:
        return "没有待审批的请求"

    lines = [f"当前窗口待审批 ({len(items)} 个):"]
    for sid, rid, req in items:
        label = session_label_short(sid, sessions_cache)
        detail = format_request_detail(req)
        index = req.get("index", 0)
        lines.append(f"\n[{index}] {label}")
        lines.append(f"    🛠️ {detail}")

    lines.append("\n💡 批准全部：/hapi a")
    lines.append("💡 批准单个：/hapi allow <序号>")
    lines.append("💡 拒绝全部：/hapi deny")
    lines.append("💡 拒绝单个：/hapi deny <序号>")
    return "\n".join(lines)


def format_permission_modes(modes: list[str], current: str) -> str:
    """格式化权限模式列表"""
    lines = [f"当前: {current}"]
    for i, m in enumerate(modes, 1):
        tag = " <--" if m == current else ""
        lines.append(f"  [{i}] {m}{tag}")
    lines.append("\n回复序号或模式名切换")
    return "\n".join(lines)


def format_model_modes(modes: list[str], current: str) -> str:
    """格式化模型模式列表"""
    lines = [f"当前模型: {current}"]
    for i, m in enumerate(modes, 1):
        tag = " <--" if m == current else ""
        lines.append(f"  [{i}] {m}{tag}")
    lines.append("\n回复序号或模式名切换")
    return "\n".join(lines)


def format_directory(entries: list[dict], path: str = ".",
                     detail: bool = False, sid: str = "") -> str:
    """格式化目录浏览（/hapi files 返回结果），目录在前文件在后"""
    if not entries:
        header = f"📌 Session: {sid}\n" if sid else ""
        return f"{header}📂 {path}\n（空目录）"

    dirs = [e for e in entries if e.get("type") == "directory"]
    files = [e for e in entries if e.get("type") != "directory"]
    dirs.sort(key=lambda e: e.get("name", ""))
    files.sort(key=lambda e: e.get("name", ""))

    lines = []
    if sid:
        lines.append(f"📌 Session: {sid}")
    lines.append(f"📂 {path}  ({len(dirs)} 个文件夹, {len(files)} 个文件)")
    for d in dirs:
        lines.append(f"  📁 {d.get('name', '?')}/")
    for f in files:
        name = f.get("name", "?")
        if detail:
            size = f.get("size", 0)
            if size >= 1024 * 1024:
                size_str = f"{size / 1024 / 1024:.1f}MB"
            elif size >= 1024:
                size_str = f"{size / 1024:.1f}KB"
            else:
                size_str = f"{size}B"
            lines.append(f"  📄 {name}  ({size_str})")
        else:
            lines.append(f"  📄 {name}")

    lines.append("")
    lines.append("💡 /hapi files <文件夹> — 查看子目录")
    lines.append("💡 /hapi find <关键词> — 搜索文件")
    lines.append("💡 /hapi dl <路径> — 下载文件")
    lines.append("💡 /hapi upload — 上传文件")
    return "\n".join(lines)


def format_file_search(files: list[dict], query: str) -> str:
    """格式化文件搜索结果（/hapi find 返回结果）"""
    if not files:
        return f"未找到匹配「{query}」的文件"

    total = len(files)
    cap = 50
    lines = [f"🔍 搜索「{query}」({total} 个结果):"]
    for i, f in enumerate(files[:cap], 1):
        name = f if isinstance(f, str) else (
            f.get("fullPath") or f.get("path") or f.get("fileName") or f.get("name") or "?"
        )
        lines.append(f"  [{i}] {name}")
    if total > cap:
        lines.append(f"  ... 还有 {total - cap} 个未显示")
    return "\n".join(lines)


HELP_TOPICS: list[tuple[str, str]] = [
    ("会话", "Session 管理"),
    ("对话", "对话与消息"),
    ("审批", "审批与回答"),
    ("通知", "多会话通知管理"),
    ("文件", "文件操作"),
    ("配置", "模式与配置"),
    ("全部", "完整命令列表"),
]


HELP_TOPIC_ALIASES = {
    "": "home",
    "home": "home",
    "index": "home",
    "首页": "home",
    "总览": "home",
    "session": "session",
    "sessions": "session",
    "会话": "session",
    "chat": "chat",
    "msg": "chat",
    "message": "chat",
    "messages": "chat",
    "对话": "chat",
    "消息": "chat",
    "approve": "approve",
    "approval": "approve",
    "pending": "approve",
    "审批": "approve",
    "push": "push",
    "notification": "push",
    "通知": "push",
    "绑定": "push",
    "files": "files",
    "file": "files",
    "文件": "files",
    "config": "config",
    "setting": "config",
    "settings": "config",
    "配置": "config",
    "all": "all",
    "full": "all",
    "全部": "all",
}


KNOWN_HAPI_SUBCOMMANDS = {
    "help", "帮助",
    "list", "ls",
    "sw",
    "s", "status",
    "msg", "messages",
    "to",
    "perm",
    "model",
    "remote",
    "output", "out",
    "pending",
    "approve", "a",
    "allow",
    "answer",
    "deny",
    "create",
    "abort", "stop",
    "archive",
    "rename",
    "delete",
    "clean",
    "bind",
    "routes",
    "files", "file",
    "find",
    "download", "dl",
    "upload",
}


HELP_COMMANDS = [
    {
        "topic": "session",
        "usage": "/hapi list [all]",
        "summary": "查看当前窗口会接收通知的 session",
        "example": None,
        "home": True,
    },
    {
        "topic": "session",
        "usage": "/hapi list all",
        "summary": "查看所有 session 和全局绑定状态",
        "example": None,
        "home": False,
    },
    {
        "topic": "push",
        "usage": "/hapi bind [claude|codex|gemini]",
        "summary": "设置当前聊天为默认通知窗口；带 claude/codex/gemini 时只对对应模型生效",
        "example": None,
        "home": True,
    },
    {
        "topic": "push",
        "usage": "/hapi bind status",
        "summary": "查看默认通知窗口、flavor 默认窗口和 session 绑定状态",
        "example": None,
        "home": True,
    },
    {
        "topic": "push",
        "usage": "/hapi routes",
        "summary": "查看当前生效的会话推送路由",
        "example": None,
        "home": False,
    },
    {
        "topic": "push",
        "usage": "/hapi bind reset",
        "summary": "清空会话路由和窗口状态，保留默认通知窗口和 flavor 默认窗口",
        "example": None,
        "home": True,
    },
    {
        "topic": "session",
        "usage": "/hapi sw <序号|ID前缀>",
        "summary": "切换当前 session",
        "example": "/hapi sw 2",
        "home": True,
    },
    {
        "topic": "session",
        "usage": "/hapi create",
        "summary": "创建新 session",
        "example": None,
        "home": True,
    },
    {
        "topic": "session",
        "usage": "/hapi s",
        "summary": "查看当前 session 状态（未绑定时回退默认窗口）",
        "example": None,
        "home": False,
    },
    {
        "topic": "session",
        "usage": "/hapi abort [序号|ID前缀]",
        "summary": "中断 session（默认当前，别名: /hapi stop）",
        "example": "/hapi abort 1",
        "home": True,
    },
    {
        "topic": "session",
        "usage": "/hapi archive",
        "summary": "归档当前 session",
        "example": None,
        "home": False,
    },
    {
        "topic": "session",
        "usage": "/hapi rename",
        "summary": "重命名当前 session",
        "example": None,
        "home": False,
    },
    {
        "topic": "session",
        "usage": "/hapi delete",
        "summary": "删除当前 session",
        "example": None,
        "home": False,
    },
    {
        "topic": "session",
        "usage": "/hapi clean [路径前缀]",
        "summary": "批量清理 inactive sessions",
        "example": "/hapi clean C:/work/project",
        "home": False,
    },
    {
        "topic": "chat",
        "usage": "> 内容",
        "summary": "快速发送到当前 session",
        "example": "> 帮我排查这个报错",
        "home": True,
    },
    {
        "topic": "chat",
        "usage": ">N 内容",
        "summary": "快速发送到第 N 个 session",
        "example": ">2 继续上一个任务",
        "home": True,
    },
    {
        "topic": "chat",
        "usage": "/hapi to <序号> <内容>",
        "summary": "发送到指定 session",
        "example": "/hapi to 2 继续上一个任务",
        "home": False,
    },
    {
        "topic": "chat",
        "usage": "/hapi msg [轮数]",
        "summary": "查看最近几轮消息（未绑定时回退默认窗口）",
        "example": "/hapi msg 2",
        "home": True,
    },
    {
        "topic": "approve",
        "usage": "/hapi pending",
        "summary": "查看当前窗口可见的待处理请求",
        "example": None,
        "home": True,
    },
    {
        "topic": "approve",
        "usage": "/hapi a",
        "summary": "批准全部非 question 请求，并继续回答 question",
        "example": None,
        "home": True,
    },
    {
        "topic": "approve",
        "usage": "/hapi allow [序号]",
        "summary": "批准全部或单个非 question 请求",
        "example": "/hapi allow 2",
        "home": False,
    },
    {
        "topic": "approve",
        "usage": "/hapi answer [序号]",
        "summary": "回答 question 请求",
        "example": "/hapi answer 1",
        "home": True,
    },
    {
        "topic": "approve",
        "usage": "/hapi deny [序号]",
        "summary": "拒绝请求",
        "example": "/hapi deny 3",
        "home": True,
    },
    {
        "topic": "approve",
        "usage": "戳一戳机器人",
        "summary": "批准全部权限请求（仅 QQ NapCat）",
        "example": None,
        "home": False,
    },
    {
        "topic": "files",
        "usage": "/hapi files [路径]",
        "summary": "浏览远端目录",
        "example": "/hapi files src",
        "home": True,
    },
    {
        "topic": "files",
        "usage": "/hapi files -l [路径]",
        "summary": "浏览目录并显示文件大小",
        "example": "/hapi files -l .",
        "home": False,
    },
    {
        "topic": "files",
        "usage": "/hapi find <关键词>",
        "summary": "搜索远端文件",
        "example": "/hapi find config",
        "home": True,
    },
    {
        "topic": "files",
        "usage": "/hapi download <路径>",
        "summary": "下载远端文件到聊天（别名: /hapi dl）",
        "example": "/hapi dl logs/app.log",
        "home": True,
    },
    {
        "topic": "files",
        "usage": "/hapi upload [cancel]",
        "summary": "上传文件到当前 session，支持快捷前缀附件",
        "example": "/hapi upload\n> 分析这张图 [附带图片]",
        "home": True,
    },
    {
        "topic": "config",
        "usage": "/hapi perm [模式]",
        "summary": "查看或切换权限模式（未绑定时回退默认窗口）",
        "example": None,
        "home": True,
    },
    {
        "topic": "config",
        "usage": "/hapi model [模式]",
        "summary": "查看或切换模型模式（仅 Claude）",
        "example": None,
        "home": True,
    },
    {
        "topic": "config",
        "usage": "/hapi output [级别]",
        "summary": "查看或切换推送级别",
        "example": "/hapi output summary",
        "home": True,
    },
    {
        "topic": "config",
        "usage": "/hapi remote",
        "summary": "切换当前 session 到 remote 托管模式",
        "example": None,
        "home": True,
    },
    {
        "topic": "config",
        "usage": "/hapi help [主题]",
        "summary": "查看帮助，可选主题：会话/对话/审批/通知/文件/配置/全部",
        "example": "/hapi help 文件",
        "home": False,
    },
]


def _get_command_summary(command: str) -> str | None:
    canonical = {
        "帮助": "help",
        "ls": "list",
        "status": "s",
        "messages": "msg",
        "out": "output",
        "approve": "a",
        "stop": "abort",
        "file": "files",
        "dl": "download",
    }.get(command, command)

    for item in HELP_COMMANDS:
        usage = item.get("usage", "")
        if not usage.startswith("/hapi "):
            continue
        command_name = usage.split()[1]
        if command_name == canonical:
            return item.get("summary")
    return None


def format_unknown_command_help(command: str) -> str:
    """格式化 /hapi 未知子命令提示。"""
    from difflib import get_close_matches

    normalized = command.strip().lower()
    if normalized == "reset":
        return "命令已调整为: /hapi bind reset"
    lines = [
        f"未知命令: /hapi {command}",
        "",
        "💡 按功能查看帮助：",
        "  /hapi help 会话    会话管理",
        "  /hapi help 对话    对话与消息",
        "  /hapi help 审批    审批权限请求",
        "  /hapi help 通知    通知与路由",
        "  /hapi help 文件    文件操作",
        "  /hapi help 配置    配置管理",
        "",
        "💡 查看常用命令：/hapi help",
    ]
    matches = get_close_matches(normalized, sorted(KNOWN_HAPI_SUBCOMMANDS), n=3, cutoff=0.45)
    if matches:
        lines.extend(["", "你可能想用："])
        for item in matches:
            summary = _get_command_summary(item)
            if summary:
                lines.append(f"  /hapi {item}  {summary}")
            else:
                lines.append(f"  /hapi {item}")
    return "\n".join(lines)


def _normalize_help_topic(topic: str) -> str | None:
    key = topic.strip().lower()
    return HELP_TOPIC_ALIASES.get(key)


def _iter_help_commands(topic: str) -> list[dict]:
    if topic == "all":
        return HELP_COMMANDS
    return [item for item in HELP_COMMANDS if item["topic"] == topic]


def _append_help_item(lines: list[str], item: dict) -> None:
    lines.append(item["usage"])
    lines.append(f"  {item['summary']}")
    example = item.get("example")
    if example:
        lines.append(f"  例：{example}")
    lines.append("")


def _format_help_commands(title: str, topic: str) -> str:
    lines = [title, ""]
    if topic == "all":
        sections = [
            ("💬 Session 管理", "session"),
            ("📨 对话", "chat"),
            ("✅ 权限审批", "approve"),
            ("🔔 多会话通知管理", "push"),
            ("📁 文件管理", "files"),
            ("⚙️ 配置管理", "config"),
        ]
        for section_title, section_topic in sections:
            lines.append(section_title)
            for item in HELP_COMMANDS:
                if item["topic"] == section_topic:
                    _append_help_item(lines, item)
        return "\n".join(lines).rstrip()

    if topic == "push":
        lines.extend([
            "通知发送规则：",
            "  1. 某个 session 如果已经绑定到聊天窗口，通知只发到那个窗口。",
            "  2. 没有绑定时，如果配置了模型默认窗口，例如 /hapi bind codex，就发到那个窗口。",
            "  3. 还没有时，发到 /hapi bind 设置的默认窗口。",
            "",
            "相关命令：",
            "  /hapi bind               设置默认通知窗口",
            "  /hapi bind codex         设置 Codex 默认通知窗口",
            "  /hapi bind status        查看当前通知配置",
            "  /hapi bind reset         清除 session 绑定和窗口状态，不清除默认窗口配置",
            "",
        ])

    commands = _iter_help_commands(topic)
    for item in commands:
        _append_help_item(lines, item)
    return "\n".join(lines).rstrip()


def _get_home_help_text() -> str:
    sections = [
        ("💬 Session 管理", "session"),
        ("📨 对话", "chat"),
        ("✅ 权限审批", "approve"),
        ("🔔 多会话通知管理", "push"),
        ("📁 文件管理", "files"),
        ("⚙️ 配置管理", "config"),
    ]
    lines = ["HAPI Connector 常用命令帮助", ""]
    for title, topic in sections:
        lines.append(title)
        for item in HELP_COMMANDS:
            if item["topic"] == topic and item["home"]:
                lines.append(item["usage"])
                lines.append(f"  {item['summary']}")
        lines.append("")

    lines.append("查看专题帮助：")
    for topic_key, topic_label in HELP_TOPICS:
        lines.append(f"/hapi help {topic_key}    {topic_label}")
    return "\n".join(lines).rstrip()


def get_help_text(topic: str = "") -> str:
    """未知命令时触发"""
    normalized = _normalize_help_topic(topic)
    if normalized is None:
        topics = ", ".join(name for name, _ in HELP_TOPICS)
        return (
            f"未知帮助主题: {topic}\n"
            f"可用主题: {topics}\n"
            "💡 查看常用命令：/hapi help"
        )

    if normalized == "home":
        return _get_home_help_text()
    if normalized == "session":
        return _format_help_commands("HAPI 帮助 / Session 管理", "session")
    if normalized == "chat":
        return _format_help_commands("HAPI 帮助 / 对话与消息", "chat")
    if normalized == "approve":
        return _format_help_commands("HAPI 帮助 / 审批与回答", "approve")
    if normalized == "push":
        return _format_help_commands("HAPI 帮助 / 多会话通知管理", "push")
    if normalized == "files":
        return _format_help_commands("HAPI 帮助 / 文件操作", "files")
    if normalized == "config":
        return _format_help_commands("HAPI 帮助 / 模式与配置", "config")
    return _format_help_commands("HAPI 帮助 / 完整命令列表", "all")
