"""待审批权限请求管理"""

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
        approval_ops.remove_pending_entry(self.sse_listener.pending, sid, rid)

    async def approve_items(self, items: list[tuple[str, str, dict]], client) -> str | None:
        """批准给定列表中的所有非 question 请求。"""
        regular = [(sid, rid, req) for sid, rid, req in items
                   if not formatters.is_question_request(req)]
        if not regular:
            return None

        results = await approval_ops.batch_approve(client, regular)
        for sid, rid, success in results:
            if success:
                self.remove_entry(sid, rid)

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
