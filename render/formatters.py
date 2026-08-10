"""纯函数：格式化 session 标签、消息预览、帮助文本等"""

import json
import re


# SDK / MCP 进度与内部控制：不应进对话卡正文
_NOISE_BLOCK_TYPES = frozenset({
    "token_count",
    "thinking",
    "tool_progress",
    "tool-progress",
    "progress",
    "heartbeat",
    "rate_limit_event",
    "rate_limit",
    "stream_event",
    "status",
})

# 多 agent / Task 子代理 sidechain：simple 默认隐藏，detail 可折叠展示
_SIDECHAIN_BLOCK_TYPES = frozenset({"sidechain"})


def extract_text_preview(
    content: dict,
    max_len: int = 80,
    *,
    include_sidechain: bool = False,
) -> str | None:
    """从消息 content 中提取文本预览（通用，适配所有 agent）。

    返回 None 表示该消息不应显示（噪音 / 被过滤的 sidechain 等）。
    max_len <= 0 表示不截断。

    include_sidechain:
      - False（默认，simple/summary）：子代理正文不展示
      - True（detail）：子代理正文加【子代理】前缀

    注意：tool_progress / heartbeat 等 SDK 噪音无论是否 sidechain 都丢弃。
    """
    if max_len <= 0:
        max_len = 999999
    if not isinstance(content, dict):
        if isinstance(content, str):
            return _filter_text_piece(content, max_len)
        return None

    # 整包就是 SDK 进度 / 元数据（含 isSidechain=true 的 tool_progress 心跳）
    # 必须优先于 sidechain 展示逻辑，否则 detail 会把心跳 JSON 当正文
    if _looks_like_sdk_control_dict(content):
        return None

    # 整条消息带 isSidechain（SDK 日志字段可能挂在 content 根上）
    if _flag_true(content.get("isSidechain")) or _flag_true(content.get("is_sidechain")):
        if not include_sidechain:
            return None

    inner = content.get("content", content if "type" in content else {})
    if isinstance(inner, dict) and _looks_like_sdk_control_dict(inner):
        return None

    # 纯文本（部分 agent 直接返回字符串）
    if isinstance(inner, str):
        text = _filter_text_piece(inner, max_len)
        if text is None:
            return None
        if include_sidechain and (
            _flag_true(content.get("isSidechain")) or _flag_true(content.get("is_sidechain"))
        ):
            label = _sidechain_agent_label(content, text=text)
            return _tag_sidechain(text, label)
        return text

    # content blocks 列表（标准格式）
    if isinstance(inner, list):
        text = _extract_from_blocks(inner, max_len, include_sidechain=include_sidechain)
        if text and include_sidechain and (
            _flag_true(content.get("isSidechain")) or _flag_true(content.get("is_sidechain"))
        ):
            label = _sidechain_agent_label(content, *inner, text=text)
            return _tag_sidechain(text, label)
        return text

    # 单个 block（dict）
    if isinstance(inner, dict):
        # output 包装里的 data 可能自带 isSidechain
        if not include_sidechain and _block_tree_is_sidechain(inner):
            return None
        text = _extract_from_block(inner, max_len, include_sidechain=include_sidechain)
        if text and include_sidechain and _block_tree_is_sidechain(inner):
            label = _sidechain_agent_label(content, inner, text=text)
            return _tag_sidechain(text, label)
        return text

    return None


def _flag_true(v) -> bool:
    return v is True or v == "true" or v == 1


def _sidechain_agent_label(*objs: object, text: str = "") -> str:
    """尽量从 SDK/HAPI 字段推断子 agent 可读名（Task description、agent 名等）。"""
    skip = {
        "",
        "assistant",
        "agent",
        "user",
        "system",
        "claude",
        "codex",
        "general-purpose",
        "general_purpose",
    }
    keys = (
        "agentName",
        "agent_name",
        "displayName",
        "display_name",
        "subagent_type",
        "subagentType",
        "activeForm",
        "description",
        "title",
        "name",
        "label",
    )
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for key in keys:
            v = obj.get(key)
            if not isinstance(v, str):
                continue
            s = v.strip()
            if not s or s.lower() in skip:
                continue
            # 过长 description 截断
            if len(s) > 36:
                s = s[:35] + "…"
            return s
        # Task / Agent 工具入参
        inp = obj.get("input") if isinstance(obj.get("input"), dict) else None
        if inp:
            for key in ("description", "prompt", "name", "subagent_type"):
                v = inp.get(key)
                if isinstance(v, str) and v.strip() and v.strip().lower() not in skip:
                    s = v.strip().split("\n", 1)[0].strip()
                    if len(s) > 36:
                        s = s[:35] + "…"
                    return s
        # content 里第一个 tool_use 名
        inner = obj.get("content")
        if isinstance(inner, list):
            for block in inner:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in ("tool_use", "tool-call"):
                    n = str(block.get("name") or "").strip()
                    if n and n.lower() not in skip:
                        return n[:36]
    # 正文首行当弱标签
    head = (text or "").strip().split("\n", 1)[0].strip()
    if head and not head.startswith("{") and not head.startswith("【"):
        if len(head) > 28:
            head = head[:27] + "…"
        return head
    return ""


def _tag_sidechain(text: str, label: str = "") -> str:
    """detail 子代理正文标记。

    形如 ``【子代理:查旧金山天气】…`` 或无名称时 ``【子代理】…``。
    卡片解析器认前者画独立小卡并显示身份。
    """
    t = (text or "").strip()
    if not t:
        return t
    # 已有标记则抽出 label / 正文
    m = re.match(r"^【子代理(?::([^】]*))?】\s*", t)
    if m:
        if not label and m.group(1):
            label = m.group(1).strip()
        t = t[m.end() :].strip()
    label = (label or "").strip()
    if label:
        return f"【子代理:{label}】{t}" if t else f"【子代理:{label}】"
    return f"【子代理】{t}" if t else "【子代理】"


def _block_tree_is_sidechain(block: dict) -> bool:
    if not isinstance(block, dict):
        return False
    if _flag_true(block.get("isSidechain")) or _flag_true(block.get("is_sidechain")):
        return True
    if block.get("type") in _SIDECHAIN_BLOCK_TYPES:
        return True
    data = block.get("data")
    if isinstance(data, dict):
        return _block_tree_is_sidechain(data)
    msg = block.get("message")
    if isinstance(msg, dict) and (
        _flag_true(msg.get("isSidechain")) or _flag_true(msg.get("is_sidechain"))
    ):
        return True
    return False


def _looks_like_sdk_control_dict(obj: dict) -> bool:
    """识别 Claude SDK 泄漏的进度/元数据整包 JSON（不应当正文）。"""
    if not isinstance(obj, dict):
        return False
    btype = str(obj.get("type") or "")
    if btype in _NOISE_BLOCK_TYPES:
        return True
    # tool_progress 心跳：常带 tool_use_id + heartbeat / elapsed_time_seconds
    if obj.get("tool_use_id") or obj.get("toolUseId"):
        if obj.get("heartbeat") is True or "elapsed_time_seconds" in obj or "elapsedTimeSeconds" in obj:
            return True
        if btype in ("tool_progress", "tool-progress", "progress"):
            return True
    # 元数据信封：parentUuid + sessionId + userType（HAPI internalEventFilter 同款）
    has_parent = "parentUuid" in obj or "parent_uuid" in obj
    has_session = isinstance(obj.get("sessionId") or obj.get("session_id"), str)
    has_user = isinstance(obj.get("userType") or obj.get("user_type"), str)
    if has_parent and has_session and has_user and btype in (
        "tool_progress",
        "tool-progress",
        "progress",
        "output",
        "system",
        "queue-operation",
        "",
    ):
        # 纯元数据、无 message/text 正文
        if not obj.get("message") and not obj.get("text") and not obj.get("content"):
            return True
        if btype in _NOISE_BLOCK_TYPES or btype in ("", "system"):
            return True
    return False


def _is_internal_event_json(text: str) -> bool:
    """文本是否为应丢弃的内部控制 JSON（对齐 HAPI isInternalEventJson + 进度包）。"""
    s = (text or "").strip()
    if not s:
        return False
    # 去掉常见前缀后再判（【消息】/[Message]/Agent: 等包一层时仍应丢弃）
    for prefix in (
        "【消息】",
        "[Message]:",
        "[Message]",
        "【子代理】",
        "Agent:",
        "agent:",
        "Message:",
        "message:",
    ):
        if s.startswith(prefix):
            s = s[len(prefix) :].lstrip(" :：").strip()
            break
    if not s or s[0] not in "{[":
        # 启发式：整段像泄漏的 tool_progress 原文（解析失败时的兜底）
        if '"type"' in s and (
            "tool_progress" in s
            or "tool-progress" in s
            or '"heartbeat"' in s
            or "elapsed_time_seconds" in s
        ):
            if "tool_use_id" in s or "toolUseId" in s or "parentUuid" in s:
                return True
        return False
    # 多层 JSON 字符串包装：'"{"type":...}"'
    for _ in range(3):
        try:
            parsed = json.loads(s)
        except Exception:
            break
        if isinstance(parsed, str):
            s = parsed.strip()
            continue
        if isinstance(parsed, dict):
            if _looks_like_sdk_control_dict(parsed):
                return True
            # { type: "output", data: { parentUuid, sessionId, userType } }
            if parsed.get("type") == "output" and isinstance(parsed.get("data"), dict):
                data = parsed["data"]
                if _looks_like_sdk_control_dict(data):
                    return True
                has_parent = "parentUuid" in data or "parent_uuid" in data
                if (
                    has_parent
                    and isinstance(data.get("sessionId") or data.get("session_id"), str)
                    and isinstance(data.get("userType") or data.get("user_type"), str)
                ):
                    return True
            return False
        break
    return False


def is_sdk_noise_text(text: str | None) -> bool:
    """推送前最终兜底：正文是否仍是 SDK/MCP 进度 JSON。"""
    if text is None:
        return True
    s = str(text).strip()
    if not s:
        return True
    return _is_internal_event_json(s)


def _filter_text_piece(text: str, max_len: int) -> str | None:
    s = (text or "").strip()
    if not s:
        return None
    if _is_internal_event_json(s):
        return None
    # task-notification 系统注入（子代理完成 XML）—— simple 不当正文
    if s.startswith("<task-notification"):
        return None
    return s[:max_len]


def _extract_from_blocks(
    blocks: list,
    max_len: int,
    *,
    include_sidechain: bool = False,
) -> str | None:
    """从 content blocks 列表中提取文本预览，只保留有意义的内容"""
    parts = []
    for block in blocks:
        if isinstance(block, str):
            piece = _filter_text_piece(block, max_len)
            if piece:
                parts.append(piece)
            continue
        if not isinstance(block, dict):
            continue
        text = _extract_from_block(
            block, max_len, include_sidechain=include_sidechain
        )
        if text is not None:
            parts.append(text)

    if not parts:
        return None
    return "\n".join(parts)


def _extract_from_block(
    block: dict,
    max_len: int,
    *,
    include_sidechain: bool = False,
) -> str | None:
    """从单个 content block 中提取文本，返回 None 表示跳过"""
    if not isinstance(block, dict):
        return None

    # 整包就是 SDK 进度 / 元数据
    if _looks_like_sdk_control_dict(block):
        return None

    btype = block.get("type", "") or ""

    # ── 文本内容（模型回复）──
    if btype == "text":
        text = block.get("text", "")
        return _filter_text_piece(str(text), max_len)

    # ── 工具调用（Claude: tool_use / Codex: tool-call 等）──
    if btype in ("tool_use", "tool-call"):
        return _fmt_tool_call(block, max_len)

    # ── 工具返回：跳过，只关注模型文本和工具调用 ──
    if btype in ("tool_result", "tool-call-result"):
        return None

    # ── HAPI 生成图（Codex/Claude display_image 等）──
    if btype in ("generated-image", "generated_image"):
        return _fmt_generated_image(block, max_len)

    # ── 子代理 sidechain 提示块 ──
    if btype in _SIDECHAIN_BLOCK_TYPES:
        if not include_sidechain:
            return None
        prompt = block.get("prompt") or block.get("text") or ""
        prompt = str(prompt).strip()
        if not prompt:
            return None
        short = prompt if len(prompt) <= max_len else prompt[: max_len - 1] + "…"
        label = _sidechain_agent_label(block, text=short)
        return _tag_sidechain(short, label)

    # ── 明确噪音类型 ──
    if btype in _NOISE_BLOCK_TYPES:
        return None

    # ── 包装类型（output/input）：内容在 data 字段里，递归处理 ──
    if btype in ("output", "input"):
        data = block.get("data")
        if isinstance(data, dict):
            if _looks_like_sdk_control_dict(data):
                return None
            if not include_sidechain and _block_tree_is_sidechain(data):
                return None
            text = _extract_from_block(
                data, max_len, include_sidechain=include_sidechain
            )
            if text and include_sidechain and _block_tree_is_sidechain(data):
                label = _sidechain_agent_label(block, data, text=text)
                return _tag_sidechain(text, label)
            return text
        if isinstance(data, list):
            return _extract_from_blocks(
                data, max_len, include_sidechain=include_sidechain
            )
        if isinstance(data, str) and data.strip():
            return _filter_text_piece(data, max_len)
        return None

    # ── Codex 包装格式 {"type": "codex", "data": {...}} ──
    if btype == "codex":
        return _extract_codex_block(block.get("data", {}), max_len)

    # ── 事件 → 【系统】 ──
    if btype == "event":
        event_data = block.get("data", {})
        event_type = event_data.get("type", "?") if isinstance(event_data, dict) else "?"
        if event_type in ("ready", "heartbeat", "thinking"):
            return None
        if event_type == "message" and isinstance(event_data, dict):
            msg = event_data.get("message", "")
            if msg:
                return f"【系统】{msg}"
        return f"【系统】{event_type}"

    # ── Summary（Codex 等 agent 的会话摘要）──
    if btype == "summary":
        text = block.get("summary", "")
        return f"【总结】{text[:max_len]}" if text else None

    # ── assistant / user SDK 消息：走进 message.content ──
    if btype in ("assistant", "user", "system"):
        if btype == "system":
            return None
        if not include_sidechain and (
            _flag_true(block.get("isSidechain")) or _flag_true(block.get("is_sidechain"))
        ):
            return None
        msg = block.get("message")
        if isinstance(msg, dict) and "content" in msg:
            nested = msg["content"]
            if isinstance(nested, list):
                text = _extract_from_blocks(
                    nested, max_len, include_sidechain=include_sidechain
                )
            elif isinstance(nested, dict):
                text = _extract_from_block(
                    nested, max_len, include_sidechain=include_sidechain
                )
            elif isinstance(nested, str):
                text = _filter_text_piece(nested, max_len)
            else:
                text = None
            if text and include_sidechain and (
                _flag_true(block.get("isSidechain"))
                or _flag_true(block.get("is_sidechain"))
            ):
                label = _sidechain_agent_label(block, msg, text=text)
                return _tag_sidechain(text, label)
            return text
        if isinstance(msg, str):
            return _filter_text_piece(msg, max_len)
        return None

    # ── 嵌套消息结构（如 {"role": "user", "content": [...]} ）──
    if "role" in block and "content" in block:
        nested = block["content"]
        if isinstance(nested, list):
            return _extract_from_blocks(
                nested, max_len, include_sidechain=include_sidechain
            )
        if isinstance(nested, dict):
            return _extract_from_block(
                nested, max_len, include_sidechain=include_sidechain
            )
        if isinstance(nested, str) and nested.strip():
            return _filter_text_piece(nested, max_len)
        return None

    # ── HAPI 消息包装（含 message 字段的元数据结构）──
    msg = block.get("message")
    if isinstance(msg, dict) and "role" in msg and "content" in msg:
        nested = msg["content"]
        if isinstance(nested, list):
            return _extract_from_blocks(
                nested, max_len, include_sidechain=include_sidechain
            )
        if isinstance(nested, dict):
            return _extract_from_block(
                nested, max_len, include_sidechain=include_sidechain
            )
        if isinstance(nested, str) and nested.strip():
            return _filter_text_piece(nested, max_len)
        return None

    # ── 未识别：常见字段；仍拒绝控制 JSON ──
    for key in ("text", "data", "content", "message", "output"):
        val = block.get(key)
        if val is None:
            continue
        if isinstance(val, str) and val.strip():
            piece = _filter_text_piece(val, max_len)
            if piece is None:
                continue
            # 有明确 type 且不是文本类时，避免 `[tool_progress] ...`
            if btype and btype not in ("text", "output", "input", "message"):
                # 无有效可读正文则跳过
                if _is_internal_event_json(val):
                    return None
            return piece
        if isinstance(val, list):
            result = _extract_from_blocks(
                val, max_len, include_sidechain=include_sidechain
            )
            if result:
                return result
        if isinstance(val, dict):
            if _looks_like_sdk_control_dict(val):
                continue
            result = _extract_from_block(
                val, max_len, include_sidechain=include_sidechain
            )
            if result:
                return result

    # 兜底：不再把整包 JSON 当正文（这就是 tool_progress 刷屏根因）
    return None


_TODO_STATUS_ICON = {
    "completed": "✅",
    "in_progress": "🔄",
    "pending": "⬜",
}


def _fmt_generated_image(block: dict, max_len: int) -> str | None:
    """HAPI generated-image → Markdown 图语法，供对话卡下载嵌图。

    形如 ``![shot.png](hapi-genimg://<imageId>)``。
    无 imageId 时退回可读占位，避免再吐整段 JSON。

    注意：块上的 ``id`` 是消息/内容 UUID，不是 imageId（CLI display_image
    会同时带 imageId 与 id）。勿把 ``id`` 当下载键，否则会 404 仍无图。
    """
    image_id = block.get("imageId") or block.get("image_id") or ""
    image_id = str(image_id).strip()
    file_name = (
        block.get("fileName")
        or block.get("file_name")
        or block.get("name")
        or "generated-image"
    )
    file_name = str(file_name).strip() or "generated-image"
    # 文件名里的 ] ( 会破坏 MD 图语法
    safe_name = file_name.replace("]", "").replace("[", "").replace("(", "").replace(")", "")
    if image_id:
        # 去掉 imageId 中可能破坏 URL 的字符
        safe_id = image_id.replace(")", "").replace(" ", "")
        marker = f"![{safe_name}](hapi-genimg://{safe_id})"
        return marker[:max_len] if max_len else marker
    return f"[{safe_name}]"[:max_len]


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


def _short_path(path: str, max_len: int = 56) -> str:
    p = str(path or "").strip()
    if len(p) <= max_len:
        return p
    return "…" + p[-(max_len - 1) :]


def _diff_snippet(old: str, new: str, *, max_lines: int = 0, max_line_len: int = 0) -> list[str]:
    """行级 -/+ 差异。默认完整渲染变更区；max_*=0 表示不截断（仅极端长度有保护上限）。"""
    old_lines = str(old or "").replace("\r\n", "\n").split("\n")
    new_lines = str(new or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    # 相同前后缀折叠，中间变更区尽量完整输出
    i = 0
    while i < len(old_lines) and i < len(new_lines) and old_lines[i] == new_lines[i]:
        i += 1
    j = 0
    while (
        j < (len(old_lines) - i)
        and j < (len(new_lines) - i)
        and old_lines[-(j + 1)] == new_lines[-(j + 1)]
    ):
        j += 1
    old_mid = old_lines[i : len(old_lines) - j if j else None]
    new_mid = new_lines[i : len(new_lines) - j if j else None]

    hard_cap = 800 if max_lines <= 0 else max_lines
    line_cap = 0 if max_line_len <= 0 else max_line_len

    def _clip(ln: str) -> str:
        if line_cap and len(ln) > line_cap:
            return ln[: line_cap - 1] + "…"
        return ln

    for ln in old_mid[:hard_cap]:
        out.append(f"  - {_clip(ln)}")
    if len(old_mid) > hard_cap:
        out.append(f"  - · 另有 {len(old_mid) - hard_cap} 行未展示（过长保护）")
    for ln in new_mid[:hard_cap]:
        out.append(f"  + {_clip(ln)}")
    if len(new_mid) > hard_cap:
        out.append(f"  + · 另有 {len(new_mid) - hard_cap} 行未展示（过长保护）")
    if not out:
        out.append("  (无文本差异)")
    return out


def _fmt_edit_tool(name: str, inp: dict, max_len: int) -> str:
    """Edit / Write 类：路径 + 轻量 -/+ 差异，卡片与文本共用。"""
    path = (
        inp.get("file_path")
        or inp.get("path")
        or inp.get("filePath")
        or inp.get("target_file")
        or ""
    )
    old = inp.get("old_string") or inp.get("oldString") or inp.get("old_str") or ""
    new = inp.get("new_string") or inp.get("newString") or inp.get("new_str") or ""
    content = inp.get("content") or inp.get("contents") or ""
    head = f"🛠️ {name}: {_short_path(str(path))}" if path else f"🛠️ {name}"
    lines = [head]
    if name in ("Write", "write_file", "create_file") and content and not old:
        preview = str(content).replace("\r\n", "\n").split("\n")
        hard_cap = 800
        for ln in preview[:hard_cap]:
            lines.append(f"  + {ln}")
        if len(preview) > hard_cap:
            lines.append(f"  + · 另有 {len(preview) - hard_cap} 行未展示（过长保护）")
    elif old or new:
        lines.extend(_diff_snippet(str(old), str(new)))
    else:
        # 无结构化字段时回退 JSON 摘要
        args_str = json.dumps(inp, ensure_ascii=False)
        if len(args_str) > max_len:
            args_str = args_str[: max_len - 1] + "…"
        lines.append(f"  {args_str}")
    return "\n".join(lines)


def _fmt_tool_call(block: dict, max_len: int) -> str:
    """格式化工具调用 block"""
    name = block.get("name", "?")
    inp = block.get("input", {})
    if isinstance(inp, dict):
        if name == "TodoWrite":
            return _fmt_todo_write(inp)
        if name == "request_user_input":
            questions = inp.get("questions", [])
            if questions:
                lines = ["❓ request_user_input:"]
                for q in questions:
                    qid = q.get("id", "")
                    qtext = q.get("question", "")
                    if qtext:
                        lines.append(f"  [{qid}] {qtext}")
                    for i, opt in enumerate(q.get("options", []), 1):
                        lines.append(f"    [{i}] {opt.get('label', '')}")
                return "\n".join(lines)
        # Edit / 多实现别名：结构化差异
        if name in (
            "Edit",
            "edit",
            "StrReplace",
            "str_replace",
            "search_replace",
            "Write",
            "write_file",
            "create_file",
        ) or (
            ("old_string" in inp or "oldString" in inp or "new_string" in inp)
            and ("file_path" in inp or "path" in inp or "filePath" in inp)
        ):
            return _fmt_edit_tool(name, inp, max_len)
        # Read：只亮路径
        if name in ("Read", "read_file", "read") and (
            inp.get("file_path") or inp.get("path") or inp.get("filePath")
        ):
            path = inp.get("file_path") or inp.get("path") or inp.get("filePath")
            return f"🛠️ {name}: {_short_path(str(path))}"
        cmd = inp.get("command") or inp.get("cmd") or ""
        if cmd:
            cmd_str = str(cmd).replace("```", "``\u200b`")
            if len(cmd_str) > max_len:
                cmd_str = cmd_str[:max_len] + "..."
            tool_icon = "🛠️"
            return tool_icon + " " + name + ":\n```\n" + cmd_str + "\n```"
        # 非 command 类（wait 等纯 JSON 参数）：与 command 一致用代码块包裹，
        # 避免 `🛠️ wait: {...}` 裸 JSON 一行贴在正文里（原输出样式）
        args_str = json.dumps(inp, ensure_ascii=False)
        args_str = args_str.replace("```", "``\u200b`")
        if len(args_str) > max_len:
            args_str = args_str[:max_len] + "..."
        return "🛠️ " + name + ":\n```json\n" + args_str + "\n```"
    return f"🛠️ {name}"



def _extract_codex_block(data: dict, max_len: int) -> str | None:
    """处理 Codex / 通用 agent 包装：``{type: 'codex', data: {...}}``。

    CLI ``sendAgentMessage`` 把 display_image 等也塞进 data，因此
    generated-image 必须在这里展开，不能落成 ``[generated-image]`` 占位。
    """
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
    if dtype in ("generated-image", "generated_image"):
        return _fmt_generated_image(data, max_len)
    if dtype in _NOISE_BLOCK_TYPES or dtype in ("token_count", "reasoning", "agent_reasoning"):
        return None
    if dtype == "message":
        msg_text = data.get("message", "")
        return msg_text[:max_len] if msg_text else "(空消息)"
    # 未知 dtype：不再用 [type] 占位刷屏（曾导致 generated-image 只剩方括号）
    return None


def session_label_short(sid: str, sessions_cache: list[dict]) -> str:
    """获取 session 的简短标识（用于 SSE 推送 / 纯文本，保留 emoji）。

    形如::

        💬 会话标题
        📂 /path/to/cwd
        🤖 claude | 🏷️ 70ed1d5c

    对话卡出图时由 ``output_present._strip_emoji`` 再剥符号，避免缺字形。
    """
    session = None
    for s in sessions_cache:
        if s.get("id") == sid:
            session = s
            break

    if not session:
        return f"🏷️ {sid[:8]}"

    meta = session.get("metadata", {})
    flavor = meta.get("flavor", "?")
    summary = get_session_title(session)
    path = meta.get("path", "")

    title = summary or "(无标题)"
    if len(path) > 40:
        path = "..." + path[-37:]

    in_plan = (
        session.get("permissionMode") == "plan"
        or session.get("collaborationMode") == "plan"
    )
    plan_tag = " · Plan" if in_plan else ""
    return f"💬 {title}{plan_tag}\n📂 {path}\n🤖 {flavor} | 🏷️ {sid[:8]}"


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
        return "还没有任何 session，可用 /hapi create 创建"

    lines = [f"全局绑定状态 · 共 {len(sessions)} 个 session:"]

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
        summary = get_session_title(s)
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
            parts.append(f"⚠️ {pending} 待审批")
        owner = session_owners.get(sid)
        if owner:
            owner_display = owner[:20] + "..." if len(owner) > 20 else owner
            parts.append(f"📌{owner_display}")

        # 添加窗口状态（显示当前活跃交互的窗口）
        if window_states:
            active_umo = next((umo for umo, state in window_states.items() if state.get("current_session") == sid), None)
            if active_umo:
                parts.append("🪟 正在交互")

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
        return "还没有任何 session，可用 /hapi create 创建"

    lines: list[str] = []
    if header_current_window:
        lines.append(f"当前窗口 ID: {header_current_window}")
        lines.append("")

    lines.append(f"共 {len(sessions)} 个 session:")
    index_by_sid: dict[str, int] = {}
    if all_sessions:
        for idx, session in enumerate(all_sessions, 1):
            sid = session.get("id")
            if sid and sid not in index_by_sid:
                index_by_sid[sid] = idx

    # 按 path 分组但保持原始顺序
    current_path = None
    for local_idx, s in enumerate(sessions, 1):
        meta = s.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
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
        summary = get_session_title(s)
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
            parts.append(f"⚠️ {pending} 待审批")
        if current_sid and sid == current_sid:
            parts.append("◀ 当前")
        lines.append(" | ".join(parts))

    lines.append("\n💡 切换会话：/hapi sw <序号或ID前缀>")
    return "\n".join(lines)


def get_session_title(session: dict) -> str:
    """
    获取 Session 标题（兼容新版 Codex / HAPI / 旧版）
    """

    meta = session.get("metadata") or {}

    # summary 兼容 dict / string
    summary = meta.get("summary")
    if isinstance(summary, dict):
        summary = summary.get("text")
    elif summary is not None:
        summary = str(summary)

    candidates = (
        session.get("thread_name"),      # 新版 Codex
        meta.get("thread_name"),

        meta.get("name"),                # HAPI rename
        session.get("name"),

        session.get("title"),

        summary,                         # 旧版 Codex

        meta.get("path"),                # 最后兜底
    )

    for value in candidates:
        if value:
            return str(value)

    return "(无标题)"


def format_session_status(s: dict) -> str:
    """格式化单个 session 状态（纯文本回退；无 emoji）。"""
    meta = s.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    sid = s.get("id", "?")
    flavor = meta.get("flavor", "?")
    path = meta.get("path", "?")
    if s.get("thinking"):
        status = "思考中"
    elif s.get("active"):
        status = "运行中"
    else:
        status = "已关闭"
    perm = s.get("permissionMode", "default")
    model = s.get("modelMode", "default")
    collab = s.get("collaborationMode", "default")
    summary = get_session_title(s)
    pending = s.get("pendingRequestsCount") or 0

    effort = s.get("effort") or s.get("modelReasoningEffort")
    service_tier = s.get("serviceTier")
    lines = [
        f"标题:   {summary}",
        f"ID:     {str(sid)[:8]}",
        f"Agent:  {flavor}",
        f"状态:   {status}",
        f"模型:   {model}",
        f"权限:   {perm}",
        f"路径:   {path}",
    ]
    if effort:
        lines.append(f"推理:   {effort}")
    if service_tier:
        lines.append(f"档位:   {service_tier}")
    if collab and (collab != "default" or flavor == "codex"):
        lines.append(f"协作:   {collab}")
    if pending:
        lines.append(f"待审批: {pending}")
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


_PASSTHROUGH_PREFIXES = (
    "【系统】",
    "【总结】",
    "【子代理】",
    "【子代理:",
    "🛠️",
    "❓",
    "[Message]",
    "[Message]:",
)


def format_agent_line(text: str) -> str:
    """格式化 agent 消息（纯文本推送）：工具/系统/子代理透传，普通文本 → ``[Message]:``。

    对话卡路径会再经 ``prepare_agent_body_for_card`` 转写/剥 emoji；
    纯文本必须保留可读前缀，不要改成无标记正文。
    """
    if is_sdk_noise_text(text):
        return ""
    if any(text.startswith(p) for p in _PASSTHROUGH_PREFIXES):
        return text
    # 兼容旧【消息】前缀：统一成 [Message]:
    if text.startswith("【消息】"):
        rest = text[len("【消息】") :].lstrip()
        return f"[Message]: {rest}" if rest else "[Message]:"
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
            lines.append(f"【用户输入】{text}")
        else:
            lines.append(f"{role}: {text}")
    # 如果过滤后只剩标题行，说明该轮无可显示内容
    if len(lines) == 1:
        lines.append("(无可显示的消息)")
    return "\n\n".join(lines)


_QUESTION_TOOLS = {"AskUserQuestion", "ask_user_question", "request_user_input"}
_COMPACT_TOOL = "__compact__"


def is_question_request(req: dict) -> bool:
    """判断是否为 AskUserQuestion 类型的请求"""
    return req.get("tool", "") in _QUESTION_TOOLS


def is_compact_request(req: dict) -> bool:
    """判断是否为插件合成的上下文压缩请求"""
    return req.get("tool", "") == _COMPACT_TOOL


def format_question_notification(req: dict, label: str, total: int, session_total: int, index: int) -> str:
    """格式化问题请求 SSE 通知（支持 AskUserQuestion 和 request_user_input）"""
    args = req.get("arguments") or {}
    questions = args.get("questions", []) if isinstance(args, dict) else []
    lines = [f"❓ 问题请求 {label}"]
    for q in questions:
        header = q.get("header") or q.get("id")
        if header:
            lines.append(f"  [{header}]")
        if q.get("question"):
            lines.append(f"  {q['question']}")
        for i, opt in enumerate(q.get("options", []), 1):
            desc = f" — {opt['description']}" if opt.get("description") else ""
            lines.append(f"    [{i}] {opt['label']}{desc}")
    lines += ["", format_pending_summary_line(total, session_total, index), "💡 交互式回答：/hapi answer"]
    return "\n".join(lines)


def format_pending_summary_line(total: int, session_total: int, index: int) -> str:
    """统一的待审批统计行：全局数 / 本会话数 / 本条序号"""
    return f"待审批：全局 {total} 个，本会话 {session_total} 个（此条序号 {index}）"


def format_permission_notification(label: str, detail: str, total: int, session_total: int, index: int) -> str:
    """格式化普通权限审批通知，复用统一的会话前缀。"""
    lines = [
        f"🔐 权限请求 {label}",
        f"  {detail}",
        "",
        format_pending_summary_line(total, session_total, index),
        "",
        "审批指令:",
        "  /hapi a             全部批准",
        "  /hapi allow <序号>  批准单个",
        "  /hapi deny          全部拒绝",
        "  /hapi deny <序号>   拒绝单个",
        "  /hapi pending       查看完整列表",
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
        tag = " ◀" if m == current else ""
        lines.append(f"  [{i}] {m}{tag}")
    lines.append("\n回复序号或模式名切换")
    return "\n".join(lines)


def format_model_modes(modes: list[str], current: str) -> str:
    """格式化模型模式列表"""
    lines = [f"当前模型: {current}"]
    for i, m in enumerate(modes, 1):
        tag = " ◀" if m == current else ""
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
    "send",
    "retry",
    "perm",
    "model",
    "effort",
    "plan",
    "fast",
    "focus", "专注", "退出专注",
    "remote",
    "output", "out",
    "pending",
    "approve", "a",
    "allow",
    "answer",
    "deny",
    "summary",
    "create",
    "abort", "stop",
    "archive",
    "resume",
    "reopen",
    "rename",
    "delete",
    "clean",
    "bind",
    "alias",
    "routes",
    "files", "file",
    "find",
    "download", "dl",
    "upload",
    "git",
    "diffstat",
    "diff",
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
        "usage": "/hapi bind [<flavor>]",
        "summary": "设置当前聊天为默认通知窗口；带 flavor（如 claude/codex/cursor）时只对对应 agent 生效",
        "example": None,
        "home": False,
    },
    {
        "topic": "push",
        "usage": "/hapi bind status",
        "summary": "查看默认通知窗口、flavor 默认窗口和 session 绑定状态",
        "example": None,
        "home": False,
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
        "usage": "/hapi alias [过滤词]",
        "summary": "查看快捷关键词映射（匹配规则与当前条目；可按关键词/命令过滤）",
        "example": "/hapi alias to",
        "home": True,
    },
    {
        "topic": "push",
        "usage": "/hapi bind reset",
        "summary": "清空会话路由和窗口状态，保留默认通知窗口和 flavor 默认窗口",
        "example": None,
        "home": False,
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
        "usage": "/hapi create [模板名] [目录]",
        "summary": "创建新 session：无参进交互向导；带模板名一步创建（模板在 WebUI 管理）",
        "example": "/hapi create 主项目",
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
        "usage": "/hapi resume [序号|ID前缀]",
        "summary": "恢复已停掉的会话",
        "example": "/hapi resume 1",
        "home": True,
    },
    {
        "topic": "session",
        "usage": "/hapi reopen [序号|ID前缀]",
        "summary": "恢复已停掉的会话（resume 备用接口）",
        "example": "/hapi reopen 1",
        "home": True,
    },
    {
        "topic": "session",
        "usage": "/hapi sync [序号|ID前缀]",
        "summary": "同步 Codex Session 到 HAPI（未传参时同步当前选中的会话）",
        "example": "/hapi sync 1",
        "home": True,
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
        "usage": "/hapi send <内容>",
        "summary": "发送到当前会话（适合做关键词映射，如 cl → send /clear）",
        "example": "/hapi send /clear",
        "home": False,
    },
    {
        "topic": "chat",
        "usage": "/hapi retry",
        "summary": "重发本窗口上一条发出的消息（AI 无响应时使用）",
        "example": None,
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
        "usage": "/hapi summary [all|<序号|ID>|status]",
        "summary": "推送忙时托管操作汇总：无参=当前窗口有变更的 session；all=全部；指定序号/ID 推单个；status 查看汇总队列",
        "example": "/hapi summary",
        "home": False,
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
        "home": False,
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
        "home": False,
    },
    {
        "topic": "files",
        "usage": "/hapi download <路径>",
        "summary": "下载远端文件到聊天（别名: /hapi dl）",
        "example": "/hapi dl logs/app.log",
        "home": False,
    },
    {
        "topic": "files",
        "usage": "/hapi upload [cancel]",
        "summary": "上传文件到当前 session，支持快捷前缀附件",
        "example": "/hapi upload\n> 分析这张图 [附带图片]",
        "home": False,
    },
    {
        "topic": "files",
        "usage": "/hapi git",
        "summary": "查看当前 session 工作区的 git 状态（只读）",
        "example": None,
        "home": False,
    },
    {
        "topic": "files",
        "usage": "/hapi diffstat [staged|unstaged]",
        "summary": "查看变更统计（+新增 -删除；staged=仅暂存，unstaged=仅未暂存）",
        "example": "/hapi diffstat staged",
        "home": False,
    },
    {
        "topic": "files",
        "usage": "/hapi diff <路径> [staged|unstaged]",
        "summary": "查看单文件 diff（统一 diff 格式，只读）",
        "example": "/hapi diff src/main.py",
        "home": False,
    },
    {
        "topic": "config",
        "usage": "/hapi perm [模式]",
        "summary": "查看或切换权限模式（未绑定时回退默认窗口）",
        "example": None,
        "home": False,
    },
    {
        "topic": "config",
        "usage": "/hapi plan",
        "summary": "开关 Plan 模式（再次执行切换回来）",
        "example": None,
        "home": False,
    },
    {
        "topic": "config",
        "usage": "/hapi model [模式]",
        "summary": "查看或切换当前使用的模型（Claude 含 fable 等预设；其它 flavor 可自由输入）",
        "example": None,
        "home": False,
    },
    {
        "topic": "config",
        "usage": "/hapi effort [值]",
        "summary": "查看或切换推理强度（可选值随 agent 不同，直接执行可查看列表）",
        "example": "/hapi effort high",
        "home": False,
    },
    {
        "topic": "config",
        "usage": "/hapi fast [on|off]",
        "summary": "查看或切换 Codex Fast mode（service tier: fast/standard）",
        "example": "/hapi fast on",
        "home": False,
    },
    {
        "topic": "config",
        "usage": "/hapi focus [on|off]",
        "summary": "开启/关闭 Focus 模式（文字直发当前 session；纯附件暂存）",
        "example": "/hapi focus on",
        "home": True,
    },
    {
        "topic": "config",
        "usage": "/hapi 专注",
        "summary": "开启 Focus 模式快捷指令",
        "example": None,
        "home": False,
    },
    {
        "topic": "config",
        "usage": "/hapi 退出专注",
        "summary": "关闭 Focus 模式快捷指令",
        "example": None,
        "home": False,
    },
    {
        "topic": "config",
        "usage": "/hapi output [级别]",
        "summary": "查看或切换推送级别",
        "example": "/hapi output summary",
        "home": False,
    },
    {
        "topic": "config",
        "usage": "/hapi remote",
        "summary": "切换当前 session 到 remote 托管模式",
        "example": None,
        "home": False,
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
        return "该命令已并入 bind，请使用 /hapi bind reset 重置窗口路由"
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


def export_help_data() -> dict:
    """导出结构化帮助数据给 WebUI（与 HELP_* 常量同源）"""
    # HELP_TOPICS uses Chinese names; map to English topic ids used by HELP_COMMANDS
    zh_to_id = {
        "会话": "session",
        "对话": "chat",
        "审批": "approve",
        "通知": "push",
        "文件": "files",
        "配置": "config",
    }
    topics = []
    for zh, desc in HELP_TOPICS:
        tid = zh_to_id.get(zh)
        if not tid:
            continue
        topics.append({"id": tid, "name": zh, "desc": desc})
    commands = [
        {
            "topic": item["topic"],
            "usage": item["usage"],
            "summary": item["summary"],
            "example": item.get("example"),
            "home": bool(item.get("home")),
        }
        for item in HELP_COMMANDS
    ]
    return {"topics": topics, "commands": commands}


def _normalize_help_topic(topic: str) -> str | None:
    key = topic.strip().lower()
    return HELP_TOPIC_ALIASES.get(key)
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


# ──── 忙时托管操作汇总（dev-docs/auto-approve-silent-summary.md §3） ────

_SUMMARY_MODE_LABELS = {
    "daily": "按天",
    "window": "按托管时段",
    "per_event": "手动触发",
}
_SUMMARY_PUSH_LABELS = {
    "on_window_end": "托管结束时",
    "at_fixed_time": "每天固定时间",
}


def _fmt_summary_dt(dt) -> str:
    if not dt:
        return "无"
    if hasattr(dt, "strftime"):
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(dt)


def _summary_detail_line(event: dict, mark: str) -> str:
    """单条事件明细行：· 07:12 ✓ approve [Bash] 或失败附错误摘要。"""
    at = event.get("at")
    time_part = ""
    if hasattr(at, "strftime"):
        time_part = at.strftime("%H:%M") + " "
    kind = str(event.get("kind") or "approve")
    kind_label = "批准" if kind == "approve" else "压缩"
    tool = event.get("tool")
    bits = [time_part + mark, kind_label]
    if tool:
        bits.append(f"[{tool}]")
    detail = str(event.get("detail") or "").strip()
    if detail:
        bits.append(detail[:120])
    return "· " + " ".join(bits)


def format_auto_approve_summary(view: dict) -> str:
    """托管操作汇总的纯文本（§3.1）。

    消费 AutoApproveSummaryService.build_summary_view 的视图 dict。
    """
    title = str(view.get("title") or view.get("label") or (view.get("sid") or "")[:8])
    lines = [f"[操作汇总] {title}", f"时段/日期: {view.get('bucket_desc') or '—'}"]

    counters = view.get("counters") or {}
    approve_ok = int(counters.get("approve_ok") or 0)
    approve_fail = int(counters.get("approve_fail") or 0)
    compact_ok = int(counters.get("compact_ok") or 0)
    compact_fail = int(counters.get("compact_fail") or 0)
    if approve_ok + approve_fail:
        lines.append(f"自动批准  ✓{approve_ok} ✗{approve_fail}")
    if compact_ok + compact_fail:
        lines.append(f"自动压缩  ✓{compact_ok} ✗{compact_fail}")

    failures = list(view.get("failures") or [])
    successes = list(view.get("successes") or [])
    max_lines = int(view.get("max_detail_lines") or 30)
    include_failures = bool(view.get("include_failures"))

    if failures:
        if include_failures:
            lines.append("── 失败明细 ──")
            for evt in failures:
                lines.append(_summary_detail_line(evt, "✗"))
        else:
            lines.append(f"── 失败 {len(failures)} 次（已隐藏明细）──")

    if successes:
        shown = successes[-max_lines:]
        lines.append(f"── 成功明细（最近 {len(shown)} 条）──")
        for evt in shown:
            lines.append(_summary_detail_line(evt, "✓"))
        hidden = len(successes) - len(shown)
        if hidden > 0:
            lines.append(f"另有 {hidden} 条")

    lines.extend(_format_git_summary_block(view))

    mode = str(view.get("mode") or "")
    push = str(view.get("push") or "")
    mode_label = _SUMMARY_MODE_LABELS.get(mode, mode or "?")
    push_label = _SUMMARY_PUSH_LABELS.get(push, push or "?")
    lines.append("")
    lines.append(f"上次汇总: {_fmt_summary_dt(view.get('last_pushed_at'))}")
    lines.append(f"模式: {mode_label} · 推送: {push_label}")
    return "\n".join(lines)


def _format_git_summary_block(view: dict) -> list[str]:
    """汇总里的 git 变更区块（flush 时刻快照；无 git/失败时返回空列表）。"""
    git = view.get("git")
    if not isinstance(git, dict):
        return []
    status_count = int(git.get("status_count") or 0)
    added = int(git.get("added") or 0)
    deleted = int(git.get("deleted") or 0)
    if status_count == 0 and added == 0 and deleted == 0:
        return []
    lines = ["── git 变更 ──"]
    lines.append(f"· {status_count} 个文件变更（+{added} -{deleted}）")
    for mark, path in git.get("entries") or []:
        lines.append(f"· {mark:<12} {path[:80]}")
    hidden = int(git.get("total_entries") or 0) - len(git.get("entries") or [])
    if hidden > 0:
        lines.append(f"另有 {hidden} 个文件")
    return lines


def format_summary_status(status: dict, sessions_cache: list[dict]) -> str:
    """/hapi summary status 的只读文本：队列、上次推送、配置。"""
    enabled = bool(status.get("enabled"))
    auto_approve = bool(status.get("auto_approve_enabled"))
    silent = bool(status.get("silent"))
    mode = str(status.get("mode") or "")
    push = str(status.get("push") or "")
    in_window = bool(status.get("in_window"))
    sessions = status.get("sessions") or {}

    lines = ["托管操作汇总状态"]
    if enabled:
        lines.append("总开关: 开启")
    else:
        why = []
        if not auto_approve:
            why.append("托管审批未开启")
        if not silent:
            why.append("操作汇总未开启")
        lines.append(f"总开关: 关闭（{'；'.join(why) if why else '—'}）")
    lines.append(f"模式: {_SUMMARY_MODE_LABELS.get(mode, mode or '?')}")
    lines.append(f"推送: {_SUMMARY_PUSH_LABELS.get(push, push or '?')}"
                 + (f"（{status.get('fixed_time')}）" if push == "at_fixed_time" else ""))
    lines.append(f"当前在托管窗: {'是' if in_window else '否'}")

    if not sessions:
        lines.append("已收集: 无")
        return "\n".join(lines)

    lines.append(f"已收集 {len(sessions)} 个 session:")
    for sid in sorted(sessions):
        info = sessions[sid]
        label = session_label_short(sid, sessions_cache).splitlines()[0]
        pending = int(info.get("pending") or 0)
        last = _fmt_summary_dt(info.get("last_pushed_at"))
        lines.append(f"  {label[:40]}  pending={pending}  last={last}")
    return "\n".join(lines)


# ──── git 查看（dev-docs/auto-approve-silent-summary.md §10） ────

_GIT_STATUS_LABELS = {
    " ": "",      # porcelain XY 码的空位（如 " M"=仅工作区修改、"M "=仅暂存修改）
    "M": "修改",
    "A": "新增",
    "D": "删除",
    "R": "重命名",
    "C": "复制",
    "U": "冲突",
    "?": "未跟踪",
    "!": "忽略",
}


def _git_status_label(codes: str) -> str:
    """porcelain XY 码 → 中文状态（去重保序，如 MM→修改、AM→新增+修改、??→未跟踪）。"""
    uniq: list[str] = []
    for c in codes:
        lab = _GIT_STATUS_LABELS.get(c, c)
        if lab and lab not in uniq:
            uniq.append(lab)
    if not uniq:
        return "?"
    return "+".join(uniq) if len(uniq) > 1 else uniq[0]


def parse_git_porcelain(stdout: str) -> list[tuple[str, str, str]]:
    """解析 ``git status --porcelain`` 输出 → [(XY码, 中文状态, 路径)]。

    无法解析的行按 ("", "?", 原行) 保留，避免丢内容。
    """
    rows: list[tuple[str, str, str]] = []
    for line in (stdout or "").splitlines():
        line = line.rstrip()
        if not line:
            continue
        if len(line) >= 2:
            x, y = line[0], line[1]
            if x in _GIT_STATUS_LABELS and y in _GIT_STATUS_LABELS:
                path = line[2:].strip().strip('"')
                codes = "".join(c for c in (x, y) if c != " ")
                rows.append((codes, _git_status_label(codes), path))
                continue
        rows.append(("", "?", line))
    return rows


def parse_git_numstat(stdout: str) -> list[tuple[str, str]]:
    """解析 ``git diff --numstat`` 输出 → [("+a -d", path)]。二进制（-）按 0 计。"""
    entries: list[tuple[str, str]] = []
    for line in (stdout or "").splitlines():
        line = line.rstrip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            added_raw, deleted_raw = parts[0], parts[1]
            path = "\t".join(parts[2:]).strip().strip('"')
            try:
                added = 0 if added_raw == "-" else int(added_raw)
                deleted = 0 if deleted_raw == "-" else int(deleted_raw)
            except ValueError:
                entries.append((line, ""))
                continue
            entries.append((f"+{added} -{deleted}", path))
        elif line.strip():
            entries.append((line, ""))
    return entries


def format_git_status(label: str, stdout: str) -> str:
    """git 状态纯文本（porcelain 解析为可读列表）。"""
    rows = parse_git_porcelain(stdout)
    if not rows:
        return f"{label}\ngit 状态 · 工作区干净"
    lines = [label, f"git 状态 · {len(rows)} 项"]
    for code, status, path in rows:
        lines.append(f"  {status}  {path}")
    return "\n".join(lines)


def format_git_diff_numstat(label: str, stdout: str) -> str:
    """变更统计纯文本（+added -deleted 对齐）。"""
    entries = parse_git_numstat(stdout)
    if not entries:
        return f"{label}\n无变更"
    lines = [label, "变更统计（+新增 -删除）"]
    total_added = 0
    total_deleted = 0
    for mark, path in entries:
        if path:
            lines.append(f"  {mark:<12} {path}")
            try:
                added = int(mark.split()[0][1:])
                deleted = int(mark.split()[1][1:])
            except (IndexError, ValueError):
                added = deleted = 0
            total_added += added
            total_deleted += deleted
        else:
            lines.append(f"  {mark}")
    lines.append(f"合计 +{total_added} -{total_deleted}")
    return "\n".join(lines)
