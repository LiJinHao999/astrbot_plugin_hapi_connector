"""会话创建模板：清洗、查找、展示（纯数据逻辑，不依赖 AstrBot）。

模板存 AstrBot KV（键 session_templates），WebUI「交互优化」页管理；
聊天里 /hapi create <模板名> [目录] 一步创建；
目录可省略——若模板也未设默认目录，会像 create 向导一样让你选最近路径。
"""

from __future__ import annotations

from typing import Any

from .flavor_profiles import is_creatable, normalize_flavor

_SESSION_TYPES = ("simple", "worktree")

# reasoning effort 常见值；列表外值透传（与向导行为一致）
_TEMPLATE_FIELDS = (
    "name", "machine_id", "directory", "agent",
    "session_type", "worktree_name", "yolo", "model_reasoning_effort",
)


def normalize_templates(raw: Any) -> list[dict[str, Any]]:
    """规范化模板列表：name 非空去重、agent 合法可建、session_type 枚举。

    非法条目直接丢弃（与 keyword_maps.normalize_maps 一致的宽松策略）。
    """
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen_names:
            continue

        agent = normalize_flavor(str(item.get("agent") or ""))
        if not agent or not is_creatable(agent):
            continue

        session_type = str(item.get("session_type") or "simple").strip().lower()
        if session_type not in _SESSION_TYPES:
            session_type = "simple"

        seen_names.add(name)
        out.append({
            "name": name,
            "machine_id": str(item.get("machine_id") or "").strip(),
            "directory": str(item.get("directory") or "").strip(),
            "agent": agent,
            "session_type": session_type,
            "worktree_name": str(item.get("worktree_name") or "").strip(),
            "yolo": bool(item.get("yolo")),
            "model_reasoning_effort": str(
                item.get("model_reasoning_effort") or ""
            ).strip().lower(),
        })
    return out


def find_template(templates: list[dict], name: str) -> dict | None:
    """按名称查找模板：精确匹配优先，其次唯一前缀匹配。"""
    query = (name or "").strip()
    if not query:
        return None
    for t in templates:
        if t.get("name") == query:
            return t
    matches = [t for t in templates if str(t.get("name", "")).startswith(query)]
    return matches[0] if len(matches) == 1 else None


def format_templates_list(templates: list[dict]) -> str:
    """模板列表展示（供 create 无参提示与找不到模板时使用）。"""
    if not templates:
        return "（暂无模板，可在 WebUI「交互优化」页创建）"
    lines = []
    for t in templates:
        parts = [t.get("agent", "?")]
        if t.get("directory"):
            parts.append(t["directory"])
        else:
            parts.append("目录需传参")
        if t.get("yolo"):
            parts.append("YOLO")
        lines.append(f"  {t['name']} — {' · '.join(parts)}")
    return "\n".join(lines)


def describe_template(t: dict) -> str:
    """单个模板的创建确认摘要。"""
    lines = [
        f"  模板:     {t.get('name')}",
        f"  代理:     {t.get('agent')}",
        f"  目录:     {t.get('directory') or '(命令参数指定)'}",
        f"  类型:     {t.get('session_type', 'simple')}",
        f"  YOLO:     {'是' if t.get('yolo') else '否'}",
    ]
    if t.get("model_reasoning_effort"):
        lines.append(f"  思考深度: {t['model_reasoning_effort']}")
    if t.get("worktree_name"):
        lines.append(f"  工作树名: {t['worktree_name']}")
    return "\n".join(lines)
