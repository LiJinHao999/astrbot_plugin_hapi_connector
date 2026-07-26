"""快捷关键词映射。

数据源：
- 命令目录 / 文案：formatters.HELP_COMMANDS（export_command_catalog）
- 是否可带参：hapi_routes.ROUTE_TAKES_ARG（与路由表同源）

匹配：
- 无参命令：整句严格匹配关键词
- 可带参命令：关键词整句，或「关键词 + 空白 + 参数」
- 映射可带固定 args（如 cl → send /clear，发到当前会话）；有固定 args 时仅整句匹配关键词
"""

from __future__ import annotations

import json
from typing import Any

# 默认映射（schema / 空配置时使用）
DEFAULT_KEYWORD_MAPS: list[dict[str, Any]] = [
    {"keywords": ["stop", "停"], "command": "stop", "args": ""},
    {"keywords": ["sw"], "command": "sw", "args": ""},
    {"keywords": ["cl"], "command": "send", "args": "/clear"},
    {"keywords": ["继续"], "command": "send", "args": "继续"},
    {"keywords": ["专注"], "command": "focus", "args": "on"},
    {"keywords": ["退出专注"], "command": "focus", "args": "off"},
    {"keywords": ["hapi指令别名"], "command": "alias", "args": ""},
]

# 旧默认（to + 固定序号 1）→ 新默认（send 当前会话）迁移表
_LEGACY_TO_SEND = {
    ("to", "1 /clear"): ("send", "/clear"),
    ("to", "1 继续"): ("send", "继续"),
}


def migrate_legacy_maps(maps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """把旧默认映射「to 1 xx」（写死发给 1 号 session）迁移为「send xx」（发给当前会话）。

    只迁移与旧默认完全一致的条目；用户自定义的其它 to 映射保持不动。
    返回 (迁移后的列表, 是否有改动)。
    """
    changed = False
    out: list[dict[str, Any]] = []
    for item in maps:
        cmd = str(item.get("command") or "")
        args = str(item.get("args") or "").strip()
        new = _LEGACY_TO_SEND.get((cmd, args))
        if new:
            item = dict(item)
            item["command"], item["args"] = new
            changed = True
        out.append(item)
    return out, changed


def default_maps_storage() -> str:
    return maps_to_storage(DEFAULT_KEYWORD_MAPS)


def _takes_arg_map() -> dict[str, bool]:
    from ..core.hapi_routes import ROUTE_TAKES_ARG

    return dict(ROUTE_TAKES_ARG)


def export_command_catalog() -> dict[str, Any]:
    """可映射命令目录：主题 + 命令（usage/summary 来自 HELP_*，takes_arg 来自路由表）。

    路由表里有、帮助表未单独列出的别名（如 stop）也会补进目录。
    """
    from ..render import formatters
    help_data = formatters.export_help_data()
    topics = help_data.get("topics") or []
    takes = _takes_arg_map()
    seen: set[str] = set()
    commands: list[dict[str, Any]] = []
    for item in formatters.HELP_COMMANDS:
        usage = str(item.get("usage") or "").strip()
        if not usage.startswith("/hapi"):
            continue
        rest = usage[len("/hapi") :].strip()
        if not rest:
            continue
        # /hapi list [all] → list；/hapi bind [<flavor>] → bind
        token = rest.split(None, 1)[0]
        cmd_id = token.strip("[]<>").lower()
        if not cmd_id or cmd_id in seen:
            continue
        # 只收录路由表里存在的子命令
        if cmd_id not in takes:
            continue
        seen.add(cmd_id)
        commands.append(
            {
                "id": cmd_id,
                "topic": str(item.get("topic") or ""),
                "usage": usage,
                "summary": str(item.get("summary") or ""),
                "takes_arg": bool(takes[cmd_id]),
            }
        )
    # 路由别名补全（如 stop → 与 abort 同表）
    for cmd_id, takes_arg in takes.items():
        if cmd_id in seen:
            continue
        # 跳过中文别名当主 id 已有时
        if not cmd_id.isascii():
            continue
        commands.append(
            {
                "id": cmd_id,
                "topic": "session",
                "usage": f"/hapi {cmd_id}",
                "summary": f"（路由别名）/hapi {cmd_id}",
                "takes_arg": bool(takes_arg),
            }
        )
        seen.add(cmd_id)
    return {"topics": topics, "commands": commands}


def _catalog_ids() -> set[str]:
    """合法 command id = 路由表全部键（含别名）。"""
    return set(_takes_arg_map().keys())


def normalize_maps(raw: Any) -> list[dict[str, Any]]:
    """规范化映射列表。

    支持：
    - list[dict]
    - JSON 字符串
    - 空 / 非法 → []
    每项：{keywords: [str], command: str, args?: str}
    """
    data = raw
    if data is None or data == "":
        return []
    if isinstance(data, str):
        s = data.strip()
        if not s:
            return []
        try:
            data = json.loads(s)
        except Exception:
            return []
    if not isinstance(data, list):
        return []

    # 中文子命令别名归一化为「英文命令 + 固定参数」，避免执行路径依赖原始消息文本
    canonical_cn = {
        "专注": ("focus", "on"),
        "退出专注": ("focus", "off"),
        "帮助": ("help", ""),
    }

    valid_ids = _catalog_ids()
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        cmd = str(item.get("command") or item.get("cmd") or "").strip().lower()
        if cmd in canonical_cn:
            canon_cmd, canon_args = canonical_cn[cmd]
            item = dict(item)
            item["command"] = cmd = canon_cmd
            if canon_args and not str(item.get("args") or "").strip():
                item["args"] = canon_args
        if not cmd or (valid_ids and cmd not in valid_ids):
            continue
        kws_raw = item.get("keywords") or item.get("keys") or []
        if isinstance(kws_raw, str):
            kws_raw = [p.strip() for p in kws_raw.replace("，", ",").split(",") if p.strip()]
        if not isinstance(kws_raw, list):
            continue
        keywords: list[str] = []
        for k in kws_raw:
            t = str(k or "").strip()
            if t and t not in keywords:
                keywords.append(t)
        if not keywords:
            continue
        # 固定发送消息（仅 send/to 等可带参命令；如 send → /clear）
        args = str(item.get("args") or item.get("argument") or "").strip()
        entry: dict[str, Any] = {"keywords": keywords, "command": cmd}
        if args:
            entry["args"] = args
        out.append(entry)
    return out


def maps_to_storage(maps: list[dict[str, Any]] | Any) -> str:
    """落盘为 JSON 字符串。"""
    cleaned = normalize_maps(maps)
    return json.dumps(cleaned, ensure_ascii=False)


def find_mapped_command(
    maps: list[dict[str, Any]] | Any, text: str
) -> tuple[str, str] | None:
    """匹配关键词 → (command_id, argument)。

    - 无参：仅整句 == 关键词
    - 可带参且无固定 args：整句 == 关键词（arg 空），或 关键词 + 空白 + 用户参数
    - 有固定 args：仅整句 == 关键词时生效，argument = 固定 args
    多关键词时按长度从长到短优先。
    """
    msg = str(text or "").strip()
    if not msg:
        return None

    takes = _takes_arg_map()
    # (kw_len, kw, cmd, fixed_args)
    candidates: list[tuple[int, str, str, str]] = []
    for item in normalize_maps(maps):
        cmd = str(item["command"])
        fixed = str(item.get("args") or "").strip()
        for kw in item.get("keywords") or []:
            k = str(kw or "").strip()
            if k:
                candidates.append((len(k), k, cmd, fixed))
    candidates.sort(key=lambda x: (-x[0], x[1]))

    for _, kw, cmd, fixed in candidates:
        takes_arg = bool(takes.get(cmd, False))
        if msg == kw:
            return cmd, fixed
        # 有固定发送消息的映射只做整句匹配，避免 cl xxx 误拼参数
        if fixed:
            continue
        if takes_arg:
            if msg.startswith(kw) and len(msg) > len(kw) and msg[len(kw)].isspace():
                return cmd, msg[len(kw) :].strip()
    return None


# ── /hapi alias 展示（纯数据 → 文案，不依赖 command_handlers / main） ──


# 路由别名 → 帮助表里的主命令 id（只为展示功能说明，不改执行）
_ALIAS_TO_CANONICAL = {
    "stop": "abort",
    "ls": "list",
    "s": "status",
    "messages": "msg",
    "out": "output",
    "a": "approve",
    "dl": "download",
    "file": "files",
}


def _cmd_help_meta(cmd: str) -> dict[str, str]:
    """从 HELP_COMMANDS / 目录取命令功能与参数说明（不硬编码业务文案）。"""
    cat = export_command_catalog()
    look = _ALIAS_TO_CANONICAL.get(cmd, cmd)
    for c in cat.get("commands") or []:
        if str(c.get("id") or "") == look:
            usage = str(c.get("usage") or f"/hapi {cmd}")
            summary = str(c.get("summary") or "")
            # 展示仍用实际映射的子命令名（stop 而不是 abort usage 里的 abort）
            if look != cmd and usage.startswith("/hapi "):
                rest = usage[len("/hapi ") :]
                parts = rest.split(None, 1)
                usage = f"/hapi {cmd}" + (f" {parts[1]}" if len(parts) > 1 else "")
            return {"usage": usage, "summary": summary}
    return {"usage": f"/hapi {cmd}", "summary": ""}


def _target_line(cmd: str, fixed_args: str) -> str:
    if fixed_args:
        return f"/hapi {cmd} {fixed_args}"
    return f"/hapi {cmd}"


def _what_it_does(cmd: str, fixed_args: str, takes_arg: bool) -> str:
    """人话：这条映射实际干什么 + 可选参数。"""
    meta = _cmd_help_meta(cmd)
    summary = (meta.get("summary") or "").strip()
    usage = (meta.get("usage") or f"/hapi {cmd}").strip()

    # 去掉 usage 里的 /hapi 前缀，只留「命令 + 参数位」给用户看
    usage_short = usage
    if usage_short.startswith("/hapi "):
        usage_short = usage_short[len("/hapi ") :]

    if fixed_args:
        # 固定参数：send/to 是发送内容，其它命令是命令参数
        base = summary or f"执行 /hapi {cmd}"
        label = "固定发送" if cmd in ("send", "to") else "固定参数"
        return f"{base}；{label}：{fixed_args}"
    if takes_arg:
        # 可带参：功能 + 参数位从帮助 usage 来
        # 例：sw <序号|ID前缀> → 切换 session，可跟序号或 ID 前缀
        base = summary or f"执行 /hapi {cmd}"
        # 从 usage 抠参数部分（第一个空格后）
        parts = usage_short.split(None, 1)
        if len(parts) > 1:
            return f"{base}；可选参数 {parts[1]}"
        return f"{base}；可跟参数"
    return summary or f"执行 /hapi {cmd}"


def format_maps_list(
    maps: list[dict[str, Any]] | Any,
    *,
    filter_text: str = "",
) -> str:
    """生成 /hapi alias 输出。filter_text 可选：按关键词或命令 id 过滤。"""
    items = normalize_maps(maps)
    takes = _takes_arg_map()
    q = str(filter_text or "").strip().lower()

    rows: list[dict[str, Any]] = []
    for item in items:
        cmd = str(item.get("command") or "")
        fixed = str(item.get("args") or "").strip()
        kws = [str(k) for k in (item.get("keywords") or []) if k]
        if q:

            def _field_hit(field: str) -> bool:
                f = str(field or "").lower()
                if not f:
                    return False
                if f == q:
                    return True
                if len(q) < 2:
                    return False
                if any("一" <= ch <= "鿿" for ch in q):
                    return q in f
                return f.startswith(q) or f == q

            hit = (
                _field_hit(cmd)
                or _field_hit(fixed)
                or any(_field_hit(k) for k in kws)
            )
            if not hit and fixed and len(q) >= 3 and q in fixed.lower():
                hit = True
            if not hit:
                continue
        rows.append(
            {
                "keywords": kws,
                "command": cmd,
                "args": fixed,
                "takes_arg": bool(takes.get(cmd, False)),
            }
        )

    lines: list[str] = [
        "快捷关键词映射",
        f"共 {len(rows)} 条" + (f"（过滤「{filter_text.strip()}」）" if q else ""),
        "",
    ]

    if not rows:
        lines.append("（无映射）")
        lines.append("请前往 WebUI 面板或配置中修改。")
        return "\n".join(lines)

    for i, row in enumerate(rows, 1):
        kw_txt = "、".join(row["keywords"])
        target = _target_line(row["command"], row["args"])
        what = _what_it_does(row["command"], row["args"], row["takes_arg"])
        lines.append(f"{i}. {kw_txt}")
        lines.append(f"   → {target}")
        lines.append(f"   {what}")
        lines.append("")

    lines.append("请前往 WebUI 面板或配置中修改。")
    return "\n".join(lines).rstrip() + "\n"
