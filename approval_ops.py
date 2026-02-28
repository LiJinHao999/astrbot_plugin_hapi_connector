"""审批业务逻辑：扁平化待审批、批量审批、问题提示构建"""

from .hapi_client import AsyncHapiClient
from . import session_ops
from . import formatters
from .formatters import is_compact_request


def flatten_pending(pending_dict: dict) -> list[tuple[str, str, dict]]:
    """将 pending 请求扁平化为 [(sid, rid, req), ...]"""
    items = []
    for sid, reqs in pending_dict.items():
        for rid, req in reqs.items():
            items.append((sid, rid, req))
    return items


def remove_pending_entry(pending_dict: dict, sid: str, rid: str):
    """移除合成条目（如 __compact__），不触发 HAPI API"""
    if sid in pending_dict:
        pending_dict[sid].pop(rid, None)
        if not pending_dict[sid]:
            del pending_dict[sid]


async def approve_all(client: AsyncHapiClient,
                      pending_dict: dict) -> str | None:
    """批准所有非 question 待审批请求，返回结果文本。无待审批时返回 None。"""
    items = flatten_pending(pending_dict)
    regular = [(sid, rid, req) for sid, rid, req in items
               if not formatters.is_question_request(req)]
    if not regular:
        return None

    results = []
    for sid, rid, req in regular:
        if is_compact_request(req):
            ok, _ = await session_ops.send_message(client, sid, "/compact")
            remove_pending_entry(pending_dict, sid, rid)
            results.append(f"{'✓' if ok else '✗'} /compact")
        else:
            ok, msg = await session_ops.approve_permission(client, sid, rid)
            tool = req.get("tool", "?")
            results.append(f"{'✓' if ok else '✗'} {tool}")

    return f"已全部批准 ({len(regular)} 个):\n" + "\n".join(results)


def build_question_prompt(q_items: list, qi_idx: int, qi: int,
                          q: dict, sessions_cache: list) -> str:
    """构建单个问题的提示文本"""
    sid = q_items[qi_idx][0]
    opts = q.get("options", [])
    lines = []
    if len(q_items) > 1:
        label = formatters.session_label_short(sid, sessions_cache)
        lines.append(f"问题请求 [{qi_idx + 1}/{len(q_items)}] {label}")
    questions = (q_items[qi_idx][2].get("arguments") or {}).get("questions", [])
    if len(questions) > 1:
        lines.append(f"[{qi + 1}/{len(questions)}]")
    if q.get("header"):
        lines.append(f"[{q['header']}]")
    if q.get("question"):
        lines.append(q["question"])
    for i, opt in enumerate(opts, 1):
        desc = f" — {opt['description']}" if opt.get("description") else ""
        lines.append(f"  [{i}] {opt['label']}{desc}")
    lines.append(f"  [{len(opts) + 1}] 其他（自定义输入）")
    return "\n".join(lines)
