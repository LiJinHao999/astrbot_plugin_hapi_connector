"""待审批权限请求管理"""

import asyncio
import time
from astrbot.api.event import AstrMessageEvent
from . import approval_ops
from . import formatters


class PendingManager:
    """管理待审批的权限请求"""

    def __init__(self, sse_listener):
        self.sse_listener = sse_listener

    def get_pending_for_window(self, event: AstrMessageEvent, visible_sids: set[str]) -> dict[str, dict]:
        """返回当前窗口可见范围内的待审批请求。"""
        pending = self.sse_listener.get_all_pending()
        return {
            sid: reqs
            for sid, reqs in pending.items()
            if sid in visible_sids
        }

    def flatten_pending(self, event: AstrMessageEvent | None, visible_sids: set[str] | None) -> list[tuple[str, str, dict]]:
        """展平待审批请求为列表"""
        if event is None or visible_sids is None:
            pending = self.sse_listener.get_all_pending()
        else:
            pending = self.get_pending_for_window(event, visible_sids)
        return approval_ops.flatten_pending(pending)

    def remove_entry(self, sid: str, rid: str):
        """移除单个待审批条目"""
        # 回收序号
        if sid in self.sse_listener.pending and rid in self.sse_listener.pending[sid]:
            req = self.sse_listener.pending[sid][rid]
            index = req.get("index", 0)
            if index > 0:
                self.sse_listener.free_index(index)
        approval_ops.remove_pending_entry(self.sse_listener.pending, sid, rid)

    async def approve_items(self, items: list[tuple[str, str, dict]], client) -> str | None:
        """批准给定列表中的所有非 question 请求。"""
        regular = [(sid, rid, req) for sid, rid, req in items
                   if not formatters.is_question_request(req)]
        if not regular:
            return None

        # 先处理 LLM 工具请求的 Future（避免序列化问题）
        llm_futures = []
        for sid, rid, req in regular:
            if self.is_llm_tool_request(req):
                future = req.pop("future", None)  # 移除 future 避免序列化
                if future:
                    llm_futures.append((sid, rid, future))

        results = await approval_ops.batch_approve(client, regular)
        for sid, rid, success in results:
            if success:
                self.remove_entry(sid, rid)

        # 设置 LLM 工具请求的 Future 结果
        for sid, rid, future in llm_futures:
            if not future.done():
                future.set_result(True)

        success_count = sum(1 for _, _, ok in results if ok)
        fail_count = len(results) - success_count
        if fail_count > 0:
            return f"✅ 已批准 {success_count} 项，❌ 失败 {fail_count} 项"
        return f"✅ 已批准 {success_count} 项"

    async def answer_questions_interactive(self, event: AstrMessageEvent, items: list[tuple[str, str, dict]],
                                          client, session_waiter, SessionController):
        """交互式回答 question 类型的请求"""
        questions = [(sid, rid, req) for sid, rid, req in items
                     if formatters.is_question_request(req)]
        if not questions:
            return

        async def q_waiter(controller: SessionController, ev: AstrMessageEvent,
                          sid: str, rid: str, req: dict):
            user_input = (ev.message_str or "").strip()
            if not user_input:
                await ev.send("❌ 输入为空，已取消")
                return

            answers = {opt["label"]: user_input for opt in req.get("options", [])}
            success = await approval_ops.answer_question(client, sid, rid, answers)
            if success:
                self.remove_entry(sid, rid)
                await ev.send(f"✅ 已提交答案")
            else:
                await ev.send(f"❌ 提交失败")

        for sid, rid, req in questions:
            prompt = formatters.format_question_prompt(req)
            await event.send(prompt)
            await session_waiter(
                event,
                lambda ctrl, ev, s=sid, r=rid, rq=req: q_waiter(ctrl, ev, s, r, rq),
                timeout=120
            )

    # ──── LLM 工具审批（伪装成 HAPI 权限请求）────

    def add_llm_tool_request(self, session_id: str, tool_name: str, args: dict) -> tuple[str, asyncio.Future, int]:
        """添加 LLM 工具审批请求到 pending 队列，返回 (request_id, future, index)"""
        import uuid
        req_id = f"llm_{uuid.uuid4().hex[:8]}"
        future = asyncio.Future()

        # 分配序号
        index = self.sse_listener.allocate_index()

        # 伪装成 HAPI 权限请求格式
        fake_request = {
            "tool": tool_name,
            "arguments": args,
            "type": "llm_tool",
            "future": future,
            "index": index,
        }

        if session_id not in self.sse_listener.pending:
            self.sse_listener.pending[session_id] = {}
        self.sse_listener.pending[session_id][req_id] = fake_request

        return req_id, future, index

    def is_llm_tool_request(self, req: dict) -> bool:
        """判断是否为 LLM 工具审批请求"""
        return req.get("type") == "llm_tool"

