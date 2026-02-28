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


def format_session_list(sessions: list[dict], current_sid: str | None = None) -> str:
    """格式化 session 列表（按 path 分组，紧凑格式）"""
    if not sessions:
        return "没有任何 session"

    lines = [f"共 {len(sessions)} 个 Session:"]
    groups = group_sessions_by_path(sessions)
    idx = 1
    for path, group in groups.items():
        lines.append(f"\n📁 {path} ({len(group)})")
        for s in group:
            meta = s.get("metadata", {})
            sid_short = s.get("id", "?")[:8]
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
            lines.append(f"[{idx} | 🏷️{sid_short}] {summary}")

            # 第二行：状态 | 模型 | 待审批 | 当前
            parts = [status, f"🤖{flavor}:{model}"]
            if pending:
                parts.append(f"⚠️ {pending}待审批")
            if current_sid and s.get("id") == current_sid:
                parts.append("<<当前")
            lines.append(" | ".join(parts))
            idx += 1

    lines.append(f"\n用 /hapi sw <序号>或<🏷️session id> 切换")
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


def format_question_notification(req: dict, label: str, total: int) -> str:
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
    lines += ["", f"当前共 {total} 个待审批", "  /hapi answer        交互式回答"]
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

    lines = [f"全局待审批 ({len(items)} 个):"]
    for i, (sid, rid, req) in enumerate(items, 1):
        label = session_label_short(sid, sessions_cache)
        detail = format_request_detail(req)
        lines.append(f"\n[{i}] {label}")
        lines.append(f"    🛠️ {detail}")

    lines.append("\n/hapi a 全部批准 | /hapi a <序号> 批准单个")
    lines.append("/hapi deny 全部拒绝 | /hapi deny <序号> 拒绝单个")
    return "\n".join(lines)


def format_permission_modes(modes: list[str], current: str) -> str:
    """格式化权限模式列表"""
    lines = [f"当前: {current}"]
    for i, m in enumerate(modes, 1):
        tag = " <--" if m == current else ""
        lines.append(f"  [{i}] {m}{tag}")
    lines.append("\n回复序号切换，或直接输入模式名")
    return "\n".join(lines)


def format_model_modes(modes: list[str], current: str) -> str:
    """格式化模型模式列表"""
    lines = [f"当前模型: {current}"]
    for i, m in enumerate(modes, 1):
        tag = " <--" if m == current else ""
        lines.append(f"  [{i}] {m}{tag}")
    lines.append("\n回复序号切换，或直接输入模式名")
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


def get_help_text() -> str:
    """返回帮助信息"""
    return """HAPI Connector 指令帮助 (仅管理员可用)

── 当前 Session 操作 ──
  /hapi s          查看当前 session 状态
  /hapi msg [轮数] 查看最近消息 (默认 1 轮)
  /hapi perm [模式] 查看/切换权限模式
  /hapi model [模式] 查看/切换模型 (仅 Claude)
  /hapi remote     切换到 remote 远程托管模式
  /hapi output [级别] 查看/切换 SSE 推送级别 (silence/simple/summary/detail)

── Session 管理 ──
  /hapi list       列出所有 session
  /hapi sw <序号|ID前缀>  切换当前 session
  /hapi create     创建新 session (向导)
  /hapi abort [序号|ID前缀] 中断 session (默认当前)
  /hapi archive    归档当前 session
  /hapi rename     重命名当前 session
  /hapi delete     删除当前 session

── 消息发送 ──
  /hapi to <序号> <内容>  发送到指定 session
  > 消息内容              快捷发送到当前 session
  >N 消息内容             快捷发送到第 N 个 session

── 审批 ──
  /hapi pending    查看待审批列表
  /hapi a          全部批准（权限请求）+ 交互式回答所有问题
  /hapi allow      批准所有权限请求（跳过 question）
  /hapi allow <序号> 批准单个权限请求
  /hapi answer     交互式回答所有 question 请求
  /hapi answer <序号> 回答指定 question 请求
  /hapi deny       全部拒绝
  /hapi deny <序号> 拒绝单个
  戳一戳机器人      批准所有权限请求 (仅 QQ NapCat)

── 文件 ──
  /hapi files [-l] [路径]   浏览远端目录 (-l 显示文件大小)
  /hapi find <关键词>  搜索远端文件
  /hapi download <路径>  下载远端文件到聊天 (别名: dl)

── 其他 ──
  /hapi help       显示此帮助"""
