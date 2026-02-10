"""纯函数：格式化 session 标签、消息预览、帮助文本等"""

import json


def extract_text_preview(content: dict, max_len: int = 80) -> str | None:
    """从消息 content 中提取文本预览。
    返回 None 表示该消息不应显示（如 token_count、ready 事件）。
    """
    inner = content.get("content", {})

    if isinstance(inner, str):
        return inner[:max_len]
    elif isinstance(inner, dict):
        # Codex 类型消息
        if inner.get("type") == "codex":
            data = inner.get("data", {})
            dtype = data.get("type", "")
            if dtype == "text":
                return data.get("text", "")[:max_len]
            elif dtype == "tool-call":
                tool = data.get("name", "?")
                inp = data.get("input", {})
                actual_tool = inp.get("tool", tool)
                cmd = inp.get("command", "")
                if cmd:
                    return f"[调用 {actual_tool}] {cmd[:max_len]}"
                else:
                    args_str = json.dumps(inp, ensure_ascii=False)[:max_len]
                    return f"[调用 {actual_tool}] {args_str}"
            elif dtype == "tool-call-result":
                output = data.get("output", {})
                if isinstance(output, dict):
                    cmd = output.get("command", "")
                    exit_code = output.get("exit_code", "")
                    status = output.get("status", "")
                    stdout = output.get("stdout", "")
                    if stdout:
                        lines = stdout.split('\n')[:3]
                        preview = '\n'.join(lines)
                        return f"[返回 exit={exit_code}] {preview[:max_len]}"
                    elif cmd:
                        return f"[返回 exit={exit_code}] {status}"
                    else:
                        return f"[返回] {json.dumps(output, ensure_ascii=False)[:max_len]}"
                else:
                    return f"[返回] {str(output)[:max_len]}"
            elif dtype == "token_count":
                return None
            elif dtype == "message":
                msg_text = data.get("message", "")
                if msg_text:
                    return msg_text[:max_len]
                return "[消息]"
            else:
                return f"[{dtype}]"
        # 事件类型消息
        elif inner.get("type") == "event":
            event_data = inner.get("data", {})
            event_type = event_data.get("type", "?")
            if event_type == "ready":
                return None
            else:
                return f"[事件: {event_type}]"
        # Claude 类型消息
        elif "text" in inner:
            return inner["text"][:max_len]
        else:
            if "id" in inner and "type" in inner:
                return f"[{inner.get('type')}]"
            return json.dumps(inner, ensure_ascii=False)[:max_len]
    else:
        return str(inner)[:max_len]


def session_label(s: dict, current_sid: str | None = None, show_path: bool = False) -> str:
    """生成 session 标签"""
    meta = s.get("metadata", {})
    flavor = meta.get("flavor", "?")
    sid_short = s.get("id", "?")[:8]

    summary = meta.get("summary", {}).get("text", "")
    title = summary or "(无标题)"

    if s.get("active"):
        status = "ACTIVE"
    else:
        status = "idle"

    pending = s.get("pendingRequestsCount", 0)
    parts = [flavor, status]
    if pending:
        parts.append(f"!{pending}待审批")
    if current_sid and s.get("id") == current_sid:
        parts.append("<<当前")

    tag = " | ".join(parts)
    label = f"({sid_short}) [{tag}] {title}"

    if show_path:
        path = meta.get("path", "(无路径)")
        label = f"{label} @ {path}"

    return label


def session_label_short(sid: str, sessions_cache: list[dict]) -> str:
    """获取 session 的简短标识（用于 SSE 推送）"""
    session = None
    for s in sessions_cache:
        if s.get("id") == sid:
            session = s
            break

    if not session:
        return f"[{sid[:8]}]"

    meta = session.get("metadata", {})
    flavor = meta.get("flavor", "?")
    summary = meta.get("summary", {}).get("text", "")
    path = meta.get("path", "")

    title = summary or "(无标题)"
    if len(path) > 40:
        path = "..." + path[-37:]

    return f"[{sid[:8]} | {flavor} | {title}] @ {path}"


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
    """格式化 session 列表（按 path 分组）"""
    if not sessions:
        return "没有任何 session"

    lines = [f"共 {len(sessions)} 个 Session:"]
    groups = group_sessions_by_path(sessions)
    idx = 1
    for path, group in groups.items():
        lines.append(f"\n📁 {path}")
        for s in group:
            lines.append(f"  [{idx}] {session_label(s, current_sid)}")
            idx += 1

    lines.append("\n用 /hapi sw <序号> 切换")
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
    summary = meta.get("summary", {}).get("text", "(无标题)")

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


def format_messages(messages: list[dict], max_preview: int = 120) -> str:
    """格式化消息列表"""
    if not messages:
        return "(暂无消息)"

    lines = []
    for m in messages:
        seq = m.get("seq", "?")
        content = m.get("content", {})
        role = content.get("role", "?")
        text = extract_text_preview(content, max_len=max_preview)
        if text is None:
            continue
        lines.append(f"[{seq:>4}] {role}: {text}")

    return "\n".join(lines) if lines else "(暂无可显示的消息)"


def format_request_detail(req: dict) -> str:
    """格式化权限请求详情（工具 + 关键参数）"""
    tool = req.get("tool", "?")
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
        tool = req.get("tool", "?")
        args = json.dumps(req.get("arguments", {}), ensure_ascii=False)[:80]
        label = session_label_short(sid, sessions_cache)
        lines.append(f"[{i}] {label} {tool}")
        lines.append(f"    {args}")

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


def get_help_text() -> str:
    """返回帮助信息"""
    return """HAPI Connector 指令帮助 (仅管理员可用)

── 当前 Session 操作 ──
  /hapi s          查看当前 session 状态
  /hapi msg [数量] 查看最近消息 (默认 10)
  /hapi perm [模式] 查看/切换权限模式
  /hapi model [模式] 查看/切换模型 (仅 Claude)
  /hapi output [级别] 查看/切换 SSE 推送级别

── Session 管理 ──
  /hapi list       列出所有 session
  /hapi sw <序号|ID前缀>  切换当前 session
  /hapi create     创建新 session (向导)
  /hapi archive    归档当前 session
  /hapi rename     重命名当前 session
  /hapi delete     删除当前 session

── 消息发送 ──
  /hapi to <序号> <内容>  发送到指定 session
  > 消息内容              快捷发送到当前 session
  >N 消息内容             快捷发送到第 N 个 session

── 审批 ──
  /hapi pending    查看待审批列表
  /hapi a          全部批准
  /hapi a <序号>   批准单个
  /hapi deny       全部拒绝
  /hapi deny <序号> 拒绝单个
  戳一戳机器人      全部批准 (仅 QQ NapCat)

── 其他 ──
  /hapi help       显示此帮助"""
