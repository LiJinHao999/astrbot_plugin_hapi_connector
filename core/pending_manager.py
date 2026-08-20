"""待审批权限请求管理"""

import asyncio
import time
from astrbot.api.event import AstrMessageEvent
from . import approval_ops
from ..render import formatters
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

    def get_llm_tool_future(self, sid: str, rid: str):
        """从原始 pending 中取 LLM 工具请求的 Future（列表中的 req 可能是副本）"""
        session_pending = self.sse_listener.pending.get(sid) or {}
        if not isinstance(session_pending, dict):
            return None
        original_req = session_pending.get(rid) or {}
        if not isinstance(original_req, dict):
            return None
        return original_req.get("future")

    def resolve_llm_tool(self, sid: str, rid: str, approved: bool):
        """审批 LLM 工具请求：设置 Future 结果并移除条目"""
        future = self.get_llm_tool_future(sid, rid)
        if future and not future.done():
            future.set_result(approved)
        self.remove_entry(sid, rid)

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

        # 先从原始 pending 提取 LLM 工具请求的 Future
        llm_futures = []
        for sid, rid, req in regular:
            if self.is_llm_tool_request(req):
                future = self.get_llm_tool_future(sid, rid)
                if future:
                    llm_futures.append((sid, rid, future))

        results = await approval_ops.batch_approve(client, regular)

        # 先设置 Future 结果，再删除条目
        for sid, rid, future in llm_futures:
            if not future.done():
                future.set_result(True)

        for sid, rid, success in results:
            if success:
                self.remove_entry(sid, rid)

        # 托管时段内手动批准 → 进操作记录（汇总开启且窗内才记录；question 不记）
        summary_svc = getattr(self.sse_listener, "summary_service", None)
        if summary_svc is not None:
            req_by_rid = {(sid, rid): req for sid, rid, req in regular}
            for sid, rid, success in results:
                req = req_by_rid.get((sid, rid))
                if req is None or formatters.is_question_request(req):
                    continue
                if formatters.is_compact_request(req):
                    await summary_svc.record_operation(
                        sid, "compact", success, tool="__compact__",
                        detail=("压缩上下文 (/compact)" if success else "批准压缩失败"),
                        request_id=rid,
                    )
                    continue
                tool = str(req.get("tool") or "?")
                await summary_svc.record_operation(
                    sid, "approve", success, tool=tool,
                    detail=(formatters.format_request_detail(req) if success else "批量批准失败"),
                    request_id=rid,
                )

        success_count = sum(1 for _, _, ok in results if ok)
        fail_count = len(results) - success_count
        if fail_count > 0:
            return f"✓ 已批准 {success_count} 项，✗ 失败 {fail_count} 项"
        return f"✓ 已批准 {success_count} 项"

    async def answer_questions_interactive(self, event: AstrMessageEvent, items: list[tuple[str, str, dict]],
                                          client, session_waiter, SessionController):
        """交互式回答 question 类型的请求"""
        questions = [(sid, rid, req) for sid, rid, req in items
                     if formatters.is_question_request(req)]
        if not questions:
            return

        for qi_idx, (sid, rid, req) in enumerate(questions):
            args = req.get("arguments") or {}
            question_list = args.get("questions", []) if isinstance(args, dict) else []
            if not question_list:
                await event.send(event.plain_result("❌ 当前问题请求缺少题目内容，无法继续回答"))
                continue

            is_rui = req.get("tool") == "request_user_input"

            # 收集每题的回答，支持重做
            collected_answers: list[list[str]] = [[] for _ in question_list]

            async def collect_one(qi: int) -> bool:
                """收集第 qi 题的回答，返回是否成功"""
                question = question_list[qi]
                opts = question.get("options", [])
                prompt = approval_ops.build_question_prompt(
                    questions, qi_idx, qi, question, self.sse_listener.sessions_cache, is_rui=is_rui
                )
                await event.send(event.plain_result(prompt))

                collected = []

                if is_rui:
                    @session_waiter(timeout=120, record_history_chains=False)
                    async def q_waiter(controller: SessionController, ev: AstrMessageEvent,
                                       _opts=opts, _collected=collected):
                        reply = (ev.message_str or "").strip()
                        if not reply:
                            controller.keep(timeout=120, reset_timeout=True)
                            return
                        if reply.isdigit() and 1 <= int(reply) <= len(_opts):
                            _collected.append(_opts[int(reply) - 1]["label"])
                        else:
                            _collected.append(reply)
                        controller.stop()

                    try:
                        await q_waiter(event)
                    except TimeoutError:
                        await event.send(event.plain_result("操作超时，已取消"))
                        return False

                    await event.send(event.plain_result("请描述此问题的补充信息或要求，若无请输入 n:"))

                    @session_waiter(timeout=60, record_history_chains=False)
                    async def note_waiter(controller: SessionController, ev: AstrMessageEvent,
                                         _collected=collected):
                        reply = (ev.message_str or "").strip()
                        if reply and reply.lower() != "n":
                            _collected.append(f"user_note: {reply}")
                        controller.stop()

                    try:
                        await note_waiter(event)
                    except TimeoutError:
                        pass

                else:
                    @session_waiter(timeout=120, record_history_chains=False)
                    async def q_waiter(controller: SessionController, ev: AstrMessageEvent,
                                       _opts=opts, _collected=collected, _state={"other": False}):
                        reply = (ev.message_str or "").strip()
                        if not reply:
                            controller.keep(timeout=120, reset_timeout=True)
                            return
                        if _state["other"]:
                            _collected.append(reply)
                            controller.stop()
                        elif reply.isdigit() and 1 <= int(reply) <= len(_opts):
                            _collected.append(_opts[int(reply) - 1]["label"])
                            controller.stop()
                        elif reply.isdigit() and int(reply) == len(_opts) + 1:
                            _state["other"] = True
                            await ev.send(ev.plain_result("请输入自定义回答:"))
                            controller.keep(timeout=120, reset_timeout=True)
                        else:
                            _collected.append(reply)
                            controller.stop()

                    try:
                        await q_waiter(event)
                    except TimeoutError:
                        await event.send(event.plain_result("操作超时，已取消"))
                        return False

                collected_answers[qi] = collected
                return True

            # 逐题收集
            for qi in range(len(question_list)):
                if not await collect_one(qi):
                    return

            # 审阅循环
            while True:
                lines = ["📋 回答汇总:"]
                for qi, question in enumerate(question_list):
                    q_text = question.get("question", "") or question.get("id", f"问题{qi+1}")
                    ans = collected_answers[qi]
                    ans_display = "、".join(ans) if ans else "(未回答)"
                    lines.append(f"[{qi+1}] {q_text[:40]}")
                    lines.append(f"  → {ans_display}")
                lines.append("\n输入序号修改某题，y 提交，n 取消")
                await event.send(event.plain_result("\n".join(lines)))

                reply_box = {"v": ""}

                @session_waiter(timeout=120, record_history_chains=False)
                async def review_waiter(controller: SessionController, ev: AstrMessageEvent,
                                        _box=reply_box):
                    _box["v"] = (ev.message_str or "").strip()
                    controller.stop()

                try:
                    await review_waiter(event)
                except TimeoutError:
                    await event.send(event.plain_result("操作超时，已取消"))
                    return

                reply = reply_box["v"]
                if reply.lower() == "y":
                    break
                elif reply.lower() == "n":
                    await event.send(event.plain_result("已取消"))
                    return
                elif reply.isdigit() and 1 <= int(reply) <= len(question_list):
                    if not await collect_one(int(reply) - 1):
                        return

            # 构造 answers
            answers: dict = {}
            for qi, question in enumerate(question_list):
                key = question.get("id", str(qi)) if is_rui else str(qi)
                if is_rui:
                    answers[key] = {"answers": collected_answers[qi]}
                else:
                    answers[key] = collected_answers[qi]

            success, msg = await approval_ops.answer_question(client, sid, rid, answers)
            if success:
                self.remove_entry(sid, rid)
                await event.send(event.plain_result(msg))
            else:
                await event.send(event.plain_result(f"❌ {msg}"))

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

