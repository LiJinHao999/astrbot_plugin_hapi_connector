"""命令处理器 - 处理所有 /hapi 子命令
"""

from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.session_waiter import session_waiter, SessionController
from . import formatters
from . import session_ops
from .formatters import is_compact_request


class CommandHandlers:
    """处理所有 /hapi 子命令"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.client = plugin.client
        self.sessions_cache = plugin.sessions_cache
        self.state_mgr = plugin.state_mgr
        self.sse_listener = plugin.sse_listener
        self.binding_mgr = plugin.binding_mgr

    # ──── 路由 ────

    async def cmd_hapi_router(self, event: AstrMessageEvent, raw: str = ""):
        """统一处理 /hapi 路由与帮助提示"""
        from astrbot.api import logger
        remainder = self.plugin._extract_hapi_remainder(event, raw)
        logger.debug(f"[cmd_hapi_router] raw='{raw}', remainder='{remainder}'")
        if not remainder:
            await self.state_mgr.ensure_primary_session(event)
            async for result in self.cmd_help(event, ""):
                yield result
            return

        parts = remainder.split(None, 1)
        subcommand = parts[0].lower()
        argument = parts[1] if len(parts) > 1 else ""
        logger.debug(f"[cmd_hapi_router] subcommand='{subcommand}', argument='{argument}', parts={parts}")
        routes = {
            "help": (self.cmd_help, True),
            "帮助": (self.cmd_help, True),
            "list": (self.cmd_list, True),
            "ls": (self.cmd_list, True),
            "sw": (self.cmd_sw, True),
            "s": (self.cmd_status, False),
            "status": (self.cmd_status, False),
            "msg": (self.cmd_msg, True),
            "messages": (self.cmd_msg, True),
            "to": (self.cmd_to, True),
            "perm": (self.cmd_perm, True),
            "model": (self.cmd_model, True),
            "remote": (self.cmd_remote, False),
            "output": (self.cmd_output, True),
            "out": (self.cmd_output, True),
            "pending": (self.cmd_pending, False),
            "approve": (self.cmd_approve, False),
            "a": (self.cmd_approve, False),
            "allow": (self.cmd_allow, True),
            "answer": (self.cmd_answer, True),
            "deny": (self.cmd_deny, True),
            "create": (self.cmd_create, False),
            "abort": (self.cmd_abort, True),
            "stop": (self.cmd_abort, True),
            "archive": (self.cmd_archive, False),
            "rename": (self.cmd_rename, False),
            "delete": (self.cmd_delete, False),
            "clean": (self.cmd_clean, True),
            "files": (self.cmd_files, True),
            "file": (self.cmd_files, True),
            "find": (self.cmd_find, True),
            "download": (self.cmd_download, True),
            "dl": (self.cmd_download, True),
            "upload": (self.cmd_upload, True),
            "bind": (self.cmd_bind, True),
            "routes": (self.cmd_routes, False),
        }
        route = routes.get(subcommand)
        if route is None:
            yield event.plain_result(formatters.format_unknown_command_help(subcommand))
            return

        await self.state_mgr.ensure_primary_session(event)
        handler, takes_arg = route
        if takes_arg:
            async for result in handler(event, argument):
                yield result
        else:
            async for result in handler(event):
                yield result

    # ── help ──

    async def cmd_help(self, event: AstrMessageEvent, topic: str = ""):
        """显示帮助信息，可按主题查看"""
        await self.state_mgr.set_user_state(event)
        if w := self.plugin._conn_warning():
            yield event.plain_result(w)
        yield event.plain_result(formatters.get_help_text(topic))

    # ── list ──

    async def cmd_list(self, event: AstrMessageEvent, scope: str = ""):
        """列出 session: /hapi list [all]"""
        await self.state_mgr.ensure_primary_session(event)
        await self.state_mgr.set_user_state(event)
        if w := self.plugin._conn_warning():
            yield event.plain_result(w)

        normalized_scope = (scope or "").strip().lower()
        if not normalized_scope:
            remainder = self.plugin._extract_hapi_remainder(event).lower()
            parts = remainder.split(None, 1)
            if parts and parts[0] in ("list", "ls"):
                normalized_scope = parts[1].strip() if len(parts) > 1 else ""

        scope_head = normalized_scope.split(None, 1)[0] if normalized_scope else ""
        if scope_head == "all":
            text = await self.plugin._format_bind_status_text(event)
            yield event.plain_result(text)
            return

        await self.plugin._refresh_sessions()
        machine_hint = await self.plugin._machine_status_hint()

        visible_sessions = self.state_mgr.visible_sessions_for_window(event, self.sessions_cache)
        if not visible_sessions:
            text = self.plugin._format_no_visible_sessions_text(event)
            if machine_hint:
                text += "\n\n" + machine_hint
            yield event.plain_result(text)
            return

        current_sid = self.state_mgr.effective_sid(event)
        text = formatters.format_session_list(
            visible_sessions,
            current_sid,
            self.sessions_cache,
            header_current_window=event.unified_msg_origin,
        )

        if machine_hint:
            text += "\n\n" + machine_hint

        yield event.plain_result(text)

    # ── sw ──

    async def cmd_sw(self, event: AstrMessageEvent, target: str = ""):
        """切换当前 session: /hapi sw <序号或ID前缀>"""
        await self.state_mgr.ensure_primary_session(event)

        if not target:
            await self.plugin._refresh_sessions()
            current_sid = self.state_mgr.effective_sid(event)
            text = formatters.format_session_list(
                self.sessions_cache,
                current_sid,
                header_current_window=event.unified_msg_origin,
            )
            yield event.plain_result(text + "\n\n请使用 /hapi sw <序号或ID前缀> 切换")
            return

        await self.plugin._refresh_sessions()

        chosen = None
        if target.isdigit():
            index = int(target)
            if 1 <= index <= len(self.sessions_cache):
                chosen = self.sessions_cache[index - 1]

        if chosen is None:
            # 按 session ID 前缀匹配
            matches = [s for s in self.sessions_cache
                       if s.get("id", "").startswith(target)]
            if len(matches) == 1:
                chosen = matches[0]
            elif len(matches) > 1:
                labels = [f"  {s['id'][:8]}..." for s in matches]
                yield event.plain_result(
                    f"匹配到 {len(matches)} 个 session，请更精确:\n"
                    + "\n".join(labels))
                return

        if chosen is None:
            yield event.plain_result(
                f"未找到匹配的 session，共 {len(self.sessions_cache)} 个")
            return

        sid = chosen["id"]
        flavor = chosen.get("metadata", {}).get("flavor", "claude")
        umo = event.unified_msg_origin
        await self.state_mgr.capture_window(sid, umo, flavor)
        summary = chosen.get("metadata", {}).get("summary", {}).get("text", "(无标题)")
        yield event.plain_result(f"已切换到 [{flavor}] {sid[:8]}... {summary}")

    # ── s (status) ──

    async def cmd_status(self, event: AstrMessageEvent):
        """查看当前 session 状态"""
        await self.state_mgr.ensure_primary_session(event)
        await self.state_mgr.set_user_state(event)
        sid = self.state_mgr.effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return
        try:
            detail = await session_ops.fetch_session_detail(self.client, sid)
            text = formatters.format_session_status(detail)
            yield event.plain_result(text)
        except Exception as e:
            yield event.plain_result(f"获取状态失败: {e}")

    # ── msg ──

    async def cmd_msg(self, event: AstrMessageEvent, rounds: str = ""):
        """查看最近消息（按轮次）: /hapi msg [轮数]"""
        from astrbot.api import logger
        logger.debug(f"[cmd_msg] 收到参数 rounds='{rounds}', type={type(rounds)}")
        await self.state_mgr.set_user_state(event)
        sid = self.state_mgr.effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return
        rounds_int = int(rounds) if rounds.isdigit() and int(rounds) >= 1 else 1
        logger.debug(f"[cmd_msg] 解析后 rounds_int={rounds_int}")
        try:
            # 多取消息以保证覆盖 N 轮（每轮约含多条原始消息）
            fetch_limit = min(rounds_int * 80, 500)
            msgs = await session_ops.fetch_messages(self.client, sid, limit=fetch_limit)
            all_rounds = formatters.split_into_rounds(msgs)
            # 取最后 N 轮
            selected = all_rounds[-rounds_int:]
            if not selected:
                yield event.plain_result("(暂无消息)")
                return
            total = len(selected)
            for i, round_msgs in enumerate(selected, 1):
                text = formatters.format_round(round_msgs, i, total)
                from .notification_manager import NotificationManager
                for chunk in NotificationManager.split_message(text):
                    yield event.plain_result(chunk)
        except Exception as e:
            yield event.plain_result(f"获取消息失败: {e}")

    # ── to ──

    async def cmd_to(self, event: AstrMessageEvent, args: str = ""):
        """发消息到指定 session: /hapi to <序号> <内容>"""
        raw = (args or event.message_str).strip()
        parts = raw.split(None, 1)
        if len(parts) < 2 or not parts[0].isdigit():
            yield event.plain_result("格式: /hapi to <序号> <内容>")
            return

        idx = int(parts[0])
        text = parts[1]

        await self.plugin._refresh_sessions()
        if idx < 1 or idx > len(self.sessions_cache):
            yield event.plain_result(f"无效序号，共 {len(self.sessions_cache)} 个 session")
            return

        target = self.sessions_cache[idx - 1]
        target_sid = target["id"]
        target_flavor = target.get("metadata", {}).get("flavor", "claude")

        # 提醒用户当前窗口的 session
        current_sid = self.state_mgr.current_sid(event)
        reminder = ""
        if current_sid and current_sid != target_sid:
            reminder = f"→ 发送到 [{target_flavor}] {target_sid[:8]} (当前窗口: {current_sid[:8]})\n"

        ok, msg = await session_ops.send_message(self.client, target_sid, text)
        await self.state_mgr.set_user_state(event)
        yield event.plain_result(reminder + msg)

    # ── perm ──

    async def cmd_perm(self, event: AstrMessageEvent, mode: str = ""):
        """查看/切换权限模式: /hapi perm [模式名]"""
        from .constants import PERMISSION_MODES
        await self.state_mgr.set_user_state(event)
        sid = self.state_mgr.effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return

        flavor = self.state_mgr.effective_flavor(event) or "claude"
        modes = PERMISSION_MODES.get(flavor, ["default"])

        if mode:
            target = mode
            if mode.isdigit() and 1 <= int(mode) <= len(modes):
                target = modes[int(mode) - 1]
            if target not in modes:
                yield event.plain_result(f"❌ 无效模式: {mode}\n可用: {', '.join(modes)}")
                return
            ok, msg = await session_ops.set_permission_mode(self.client, sid, target)
            yield event.plain_result(msg)
        else:
            try:
                detail = await session_ops.fetch_session_detail(self.client, sid)
                current = detail.get("permissionMode", "default")
                text = formatters.format_permission_modes(modes, current)
                yield event.plain_result(f"({flavor} 模式)\n{text}")
            except Exception:
                yield event.plain_result("获取权限模式失败")
                return

            @session_waiter(timeout=30, record_history_chains=False)
            async def perm_waiter(controller: SessionController, ev: AstrMessageEvent):
                reply = ev.message_str.strip()
                if not reply:
                    controller.keep(timeout=30, reset_timeout=True)
                    return
                target = reply
                if reply.isdigit() and 1 <= int(reply) <= len(modes):
                    target = modes[int(reply) - 1]
                if target not in modes:
                    await ev.send(ev.plain_result(f"无效模式，可用: {', '.join(modes)}"))
                else:
                    ok, msg = await session_ops.set_permission_mode(self.client, sid, target)
                    await ev.send(ev.plain_result(msg))
                controller.stop()

            try:
                await perm_waiter(event)
            except TimeoutError:
                yield event.plain_result("操作超时，已取消")
            finally:
                event.stop_event()

    # ── model ──

    async def cmd_model(self, event: AstrMessageEvent, mode: str = ""):
        """查看/切换模型: /hapi model [模式名]"""
        from .constants import MODEL_MODES
        await self.state_mgr.set_user_state(event)
        sid = self.state_mgr.effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return

        flavor = self.state_mgr.effective_flavor(event) or "claude"
        if flavor != "claude":
            yield event.plain_result("模型切换仅支持 Claude session")
            return

        if mode:
            target = mode
            if mode.isdigit() and 1 <= int(mode) <= len(MODEL_MODES):
                target = MODEL_MODES[int(mode) - 1]
            if target not in MODEL_MODES:
                yield event.plain_result(f"❌ 无效模式: {mode}\n可用: {', '.join(MODEL_MODES)}")
                return
            ok, msg = await session_ops.set_model_mode(self.client, sid, target)
            yield event.plain_result(msg)
        else:
            try:
                detail = await session_ops.fetch_session_detail(self.client, sid)
                current = detail.get("modelMode", "default")
                text = formatters.format_model_modes(MODEL_MODES, current)
                yield event.plain_result(text)
            except Exception:
                yield event.plain_result("获取模型信息失败")
                return

            @session_waiter(timeout=30, record_history_chains=False)
            async def model_waiter(controller: SessionController, ev: AstrMessageEvent):
                reply = ev.message_str.strip()
                if not reply:
                    controller.keep(timeout=30, reset_timeout=True)
                    return
                target = reply
                if reply.isdigit() and 1 <= int(reply) <= len(MODEL_MODES):
                    target = MODEL_MODES[int(reply) - 1]
                if target not in MODEL_MODES:
                    await ev.send(ev.plain_result(f"无效模式，可用: {', '.join(MODEL_MODES)}"))
                else:
                    ok, msg = await session_ops.set_model_mode(self.client, sid, target)
                    await ev.send(ev.plain_result(msg))
                controller.stop()

            try:
                await model_waiter(event)
            except TimeoutError:
                yield event.plain_result("操作超时，已取消")
            finally:
                event.stop_event()

    # ── remote ──

    async def cmd_remote(self, event: AstrMessageEvent):
        """切换当前 session 到 remote 远程托管模式"""
        await self.state_mgr.set_user_state(event)
        sid = self.state_mgr.effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return
        ok, msg = await session_ops.switch_to_remote(self.client, sid)
        yield event.plain_result(msg)

    # ── output ──

    _OUTPUT_LEVELS = {
        "silence": "仅推送权限请求和任务完成提醒",
        "summary": "任务完成时推送最近的 agent 消息",
        "simple": "仅推送 agent 文本消息，不包含复杂的工具调用信息",
        "detail": "实时推送所有新消息（信息量较大）",
    }

    async def cmd_output(self, event: AstrMessageEvent, level: str = ""):
        """查看/切换 SSE 推送级别: /hapi output [级别]"""
        await self.state_mgr.set_user_state(event)
        current = self.sse_listener.output_level
        levels = list(self._OUTPUT_LEVELS.keys())

        if not level:
            lines = [f"当前 SSE 推送级别: {current}"]
            for i, (lvl, desc) in enumerate(self._OUTPUT_LEVELS.items(), 1):
                tag = " <--" if lvl == current else ""
                lines.append(f"  [{i}] {lvl}{tag} — {desc}")
            lines.append("\n回复序号或级别名切换")
            yield event.plain_result("\n".join(lines))

            @session_waiter(timeout=30, record_history_chains=False)
            async def output_waiter(controller: SessionController, ev: AstrMessageEvent):
                reply = ev.message_str.strip()
                if not reply:
                    controller.keep(timeout=30, reset_timeout=True)
                    return
                t = reply
                if reply.isdigit() and 1 <= int(reply) <= len(levels):
                    t = levels[int(reply) - 1]
                if t not in self._OUTPUT_LEVELS:
                    await ev.send(ev.plain_result(f"❌ 无效级别: {reply}\n可用: {', '.join(levels)}"))
                else:
                    self.sse_listener.output_level = t
                    self.plugin.config["output_level"] = t
                    await ev.send(ev.plain_result(
                        f"SSE 推送级别已切换为: {t}\n{self._OUTPUT_LEVELS[t]}"))
                controller.stop()

            try:
                await output_waiter(event)
            except TimeoutError:
                yield event.plain_result("操作超时，已取消")
            finally:
                event.stop_event()
            return

        target = level
        if level.isdigit() and 1 <= int(level) <= len(levels):
            target = levels[int(level) - 1]
        if target not in self._OUTPUT_LEVELS:
            lines = [f"❌ 无效级别: {level}\n", "可用:"]
            for i, (lvl, desc) in enumerate(self._OUTPUT_LEVELS.items(), 1):
                lines.append(f"  [{i}] {lvl} — {desc}")
            yield event.plain_result("\n".join(lines))
            return

        self.sse_listener.output_level = target
        self.plugin.config["output_level"] = target
        yield event.plain_result(
            f"SSE 推送级别已切换为: {target}\n{self._OUTPUT_LEVELS[target]}")

    # ── pending (查看待审批列表) ──

    async def cmd_pending(self, event: AstrMessageEvent):
        """查看待审批请求列表: /hapi pending"""
        await self.state_mgr.set_user_state(event)
        visible_sids = {s.get("id") for s in self.state_mgr.visible_sessions_for_window(event, self.sessions_cache) if s.get("id")}
        visible_sids.add(event.unified_msg_origin)  # 包含当前窗口 ID（LLM 工具请求）
        pending = self.plugin.pending_mgr.get_pending_for_window(event, visible_sids)
        text = formatters.format_pending_requests(pending, self.sessions_cache)
        yield event.plain_result(text)

    # ── approve ──

    async def cmd_approve(self, event: AstrMessageEvent):
        """批准所有权限请求，再交互式回答 question: /hapi a"""
        await self.state_mgr.set_user_state(event)
        visible_sids = {s.get("id") for s in self.state_mgr.visible_sessions_for_window(event, self.sessions_cache) if s.get("id")}
        visible_sids.add(event.unified_msg_origin)  # 包含当前窗口 ID（LLM 工具请求）
        items = self.plugin.pending_mgr.flatten_pending(event, visible_sids)
        if not items:
            yield event.plain_result("没有待审批的请求")
            return

        regular = [(sid, rid, req) for sid, rid, req in items
                   if not formatters.is_question_request(req)]
        questions = [(sid, rid, req) for sid, rid, req in items
                     if formatters.is_question_request(req)]

        if regular:
            result = await self.plugin.pending_mgr.approve_items(regular, self.client)
            if result:
                yield event.plain_result(result)

        if questions:
            yield event.plain_result(f"还有 {len(questions)} 个问题需要回答:")
            await self.plugin.pending_mgr.answer_questions_interactive(
                event, questions, self.client, session_waiter, SessionController)

        event.stop_event()

    # ── allow ──

    async def cmd_allow(self, event: AstrMessageEvent, target: str = ""):
        """批准权限请求（跳过 question）: /hapi allow [序号]"""
        await self.state_mgr.set_user_state(event)
        visible_sids = {s.get("id") for s in self.state_mgr.visible_sessions_for_window(event, self.sessions_cache) if s.get("id")}
        visible_sids.add(event.unified_msg_origin)  # 包含当前窗口 ID（LLM 工具请求）
        items = self.plugin.pending_mgr.flatten_pending(event, visible_sids)
        regular = [(sid, rid, req) for sid, rid, req in items
                   if not formatters.is_question_request(req)]

        if not regular:
            yield event.plain_result("没有待批准的权限请求")
            return

        raw = (target or "").strip()
        if raw and raw.isdigit():
            n = int(raw)
            # 根据 index 查找，而不是列表索引
            found = [(sid, rid, req) for sid, rid, req in regular if req.get("index") == n]
            if not found:
                yield event.plain_result(f"无效序号 {n}")
                return
            sid, rid, req = found[0]
            if is_compact_request(req):
                ok, _ = await session_ops.send_message(self.client, sid, "/compact")
                self.plugin.pending_mgr.remove_entry(sid, rid)
                yield event.plain_result(f"{'✓' if ok else '✗'} 已批准: /compact")
            else:
                ok, _ = await session_ops.approve_permission(self.client, sid, rid)
                tool = req.get("tool", "?")
                yield event.plain_result(f"{'✓' if ok else '✗'} 已批准: {tool}")
        else:
            result = await self.plugin.pending_mgr.approve_items(regular, self.client)
            if result:
                yield event.plain_result(result)

    # ── answer ──

    async def cmd_answer(self, event: AstrMessageEvent, target: str = ""):
        """交互式回答 question 请求: /hapi answer [序号]"""
        await self.state_mgr.set_user_state(event)
        visible_sids = {s.get("id") for s in self.state_mgr.visible_sessions_for_window(event, self.sessions_cache) if s.get("id")}
        visible_sids.add(event.unified_msg_origin)  # 包含当前窗口 ID（LLM 工具请求）
        items = self.plugin.pending_mgr.flatten_pending(event, visible_sids)
        q_items = [(sid, rid, req) for sid, rid, req in items
                   if formatters.is_question_request(req)]

        if not q_items:
            yield event.plain_result("没有待回答的问题")
            return

        raw = (target or event.message_str).strip()
        if raw and raw.isdigit():
            n = int(raw)
            # 根据 index 查找
            found = [(sid, rid, req) for sid, rid, req in q_items if req.get("index") == n]
            if not found:
                yield event.plain_result(f"无效序号 {n}")
                return
            q_items = [found[0]]

        await self.plugin.pending_mgr.answer_questions_interactive(
            event, q_items, self.client, session_waiter, SessionController)
        event.stop_event()

    # ── deny ──

    async def cmd_deny(self, event: AstrMessageEvent, target: str = ""):
        """拒绝审批请求: /hapi deny 全部拒绝, /hapi deny <序号> 拒绝单个"""
        await self.state_mgr.set_user_state(event)
        visible_sids = {s.get("id") for s in self.state_mgr.visible_sessions_for_window(event, self.sessions_cache) if s.get("id")}
        visible_sids.add(event.unified_msg_origin)  # 包含当前窗口 ID（LLM 工具请求）
        items = self.plugin.pending_mgr.flatten_pending(event, visible_sids)
        if not items:
            yield event.plain_result("没有待审批的请求")
            return

        raw = (target or "").strip()
        if raw and raw.isdigit():
            # 拒绝单个
            n = int(raw)
            # 根据 index 查找
            found = [(sid, rid, req) for sid, rid, req in items if req.get("index") == n]
            if not found:
                yield event.plain_result(f"无效序号 {n}")
                return
            sid, rid, req = found[0]
            if is_compact_request(req):
                self.plugin.pending_mgr.remove_entry(sid, rid)
                yield event.plain_result("✓ 已取消压缩: /compact")
            elif self.plugin.pending_mgr.is_llm_tool_request(req):
                # 从原始 pending 获取 Future
                original_req = self.sse_listener.pending.get(sid, {}).get(rid, {})
                future = original_req.get("future")
                if future and not future.done():
                    future.set_result(False)
                self.plugin.pending_mgr.remove_entry(sid, rid)
                tool = req.get("tool", "?")
                yield event.plain_result(f"✓ 已拒绝: {tool}")
            else:
                ok, msg = await session_ops.deny_permission(self.client, sid, rid)
                tool = req.get("tool", "?")
                yield event.plain_result(f"{'✓' if ok else '✗'} 已拒绝: {tool}")
        else:
            # 全部拒绝
            results = []
            for sid, rid, req in items:
                if is_compact_request(req):
                    self.plugin.pending_mgr.remove_entry(sid, rid)
                    results.append("✓ /compact (已取消)")
                elif self.plugin.pending_mgr.is_llm_tool_request(req):
                    # 从原始 pending 获取 Future
                    original_req = self.sse_listener.pending.get(sid, {}).get(rid, {})
                    future = original_req.get("future")
                    if future and not future.done():
                        future.set_result(False)
                    self.plugin.pending_mgr.remove_entry(sid, rid)
                    tool = req.get("tool", "?")
                    results.append(f"✓ {tool}")
                else:
                    ok, msg = await session_ops.deny_permission(self.client, sid, rid)
                    tool = req.get("tool", "?")
                    results.append(f"{'✓' if ok else '✗'} {tool}")
            yield event.plain_result(f"已全部拒绝 ({len(items)} 个):\n" + "\n".join(results))

    # ── create ──

    async def cmd_create(self, event: AstrMessageEvent):
        """创建新 session (5 步向导)"""
        from .create_wizard import CreateWizard
        await self.state_mgr.ensure_primary_session(event)
        await self.state_mgr.set_user_state(event)
        try:
            machines = await session_ops.fetch_machines(self.client)
        except Exception as e:
            yield event.plain_result(f"获取机器列表失败: {e}")
            return

        if not machines:
            yield event.plain_result("没有在线的机器")
            return

        labels = []
        for m in machines:
            meta = m.get("metadata", {})
            host = meta.get("host", "unknown")
            plat = meta.get("platform", "?")
            labels.append(f"{host} ({plat})")

        wiz = CreateWizard(machines, labels)
        result = wiz.initial_prompt()

        # 初始提示可能需要先拉 recent_paths
        if result.need_recent_paths:
            try:
                wiz.set_recent_paths(await session_ops.fetch_recent_paths(self.client))
            except Exception:
                pass
            prompt = wiz._step2_prompt(result.prompt)
            yield event.plain_result(prompt)
        else:
            yield event.plain_result(result.prompt)

        @session_waiter(timeout=120, record_history_chains=False)
        async def create_waiter(controller: SessionController, ev: AstrMessageEvent):
            raw = ev.message_str.strip()
            if not raw:
                controller.keep(timeout=120, reset_timeout=True)
                return
            r = wiz.process(raw)

            # 需要拉 recent_paths 再显示步骤 2
            if r.need_recent_paths:
                try:
                    wiz.set_recent_paths(await session_ops.fetch_recent_paths(self.client))
                except Exception:
                    pass
                prompt = wiz._step2_prompt(r.prompt)
                await ev.send(ev.plain_result(prompt))
                controller.keep(timeout=120, reset_timeout=True)
                return

            # 用户取消
            if r.cancelled:
                await ev.send(ev.plain_result(r.prompt))
                controller.stop()
                return

            # 用户确认创建
            if r.confirmed:
                await ev.send(ev.plain_result(r.prompt))
                s = wiz.state
                ok, msg, new_sid = await session_ops.spawn_session(
                    self.client,
                    machine_id=s["machine_id"],
                    directory=s["directory"],
                    agent=s["agent"],
                    session_type=s["session_type"],
                    yolo=s["yolo"],
                    worktree_name=s["worktree_name"],
                )
                await self.plugin._refresh_sessions()
                if ok and new_sid:
                    flavor = s["agent"]
                    umo = ev.unified_msg_origin
                    await self.state_mgr.capture_window(new_sid, umo, flavor)
                    msg += f"\n已自动切换到该 session [{flavor}] {new_sid[:8]}..."
                await ev.send(ev.plain_result(msg))
                controller.stop()
                return

            # 普通步骤推进 / 校验失败重试
            await ev.send(ev.plain_result(r.prompt))
            controller.keep(timeout=120, reset_timeout=True)

        try:
            await create_waiter(event)
        except TimeoutError:
            yield event.plain_result("创建向导超时，已取消")
        finally:
            event.stop_event()

    # ── abort ──

    async def cmd_abort(self, event: AstrMessageEvent, target: str = ""):
        """中断 session: /hapi abort [序号|ID前缀]"""
        await self.state_mgr.set_user_state(event)
        await self.plugin._refresh_sessions()

        if not target:
            sid = self.state_mgr.effective_sid(event)
            if not sid:
                yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
                return
        else:
            sid = None
            if target.isdigit():
                idx = int(target)
                if 1 <= idx <= len(self.sessions_cache):
                    sid = self.sessions_cache[idx - 1]["id"]
            if sid is None:
                matches = [s for s in self.sessions_cache
                           if s.get("id", "").startswith(target)]
                if len(matches) == 1:
                    sid = matches[0]["id"]
                elif len(matches) > 1:
                    labels = [f"  {s['id'][:8]}..." for s in matches]
                    yield event.plain_result(
                        f"匹配到 {len(matches)} 个 session，请更精确:\n"
                        + "\n".join(labels))
                    return
            if sid is None:
                yield event.plain_result(f"未找到匹配的 session")
                return

        ok, msg = await session_ops.abort_session(self.client, sid)
        if ok:
            await self.plugin._refresh_sessions()
        yield event.plain_result(msg)

    # ── archive ──

    async def cmd_archive(self, event: AstrMessageEvent, target: str = ""):
        """归档 session: /hapi archive [序号或ID前缀]"""
        await self.state_mgr.set_user_state(event)

        if target:
            await self.plugin._refresh_sessions()
            sid = self._resolve_target(target)
            if not sid:
                yield event.plain_result(f"未找到匹配的 session: {target}")
                return
        else:
            sid = self.state_mgr.effective_sid(event)
            if not sid:
                yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session，或使用 /hapi archive <序号>")
                return

        yield event.plain_result(f"确认归档 session [{sid[:8]}]?\n回复 y 确认")

        @session_waiter(timeout=30, record_history_chains=False)
        async def archive_waiter(controller: SessionController, ev: AstrMessageEvent):
            reply = ev.message_str.strip()
            if not reply:
                controller.keep(timeout=30, reset_timeout=True)
                return
            if reply.lower() == "y":
                ok, msg = await session_ops.archive_session(self.client, sid)
                await ev.send(ev.plain_result(msg))
                if ok:
                    await self.plugin._refresh_sessions()
            else:
                await ev.send(ev.plain_result("已取消"))
            controller.stop()

        try:
            await archive_waiter(event)
        except TimeoutError:
            yield event.plain_result("操作超时，已取消")
        finally:
            event.stop_event()

    def _resolve_target(self, target: str) -> str | None:
        """解析序号或ID前缀为 session ID"""
        if target.isdigit():
            idx = int(target)
            if 1 <= idx <= len(self.sessions_cache):
                return self.sessions_cache[idx - 1]["id"]
        matches = [s for s in self.sessions_cache if s.get("id", "").startswith(target)]
        return matches[0]["id"] if len(matches) == 1 else None

    # ── rename ──

    async def cmd_rename(self, event: AstrMessageEvent, target: str = ""):
        """重命名 session: /hapi rename [序号或ID前缀]"""
        await self.state_mgr.set_user_state(event)

        if target:
            await self.plugin._refresh_sessions()
            sid = self._resolve_target(target)
            if not sid:
                yield event.plain_result(f"未找到匹配的 session: {target}")
                return
        else:
            sid = self.state_mgr.effective_sid(event)
            if not sid:
                yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session，或使用 /hapi rename <序号>")
                return

        yield event.plain_result(f"请输入 session [{sid[:8]}] 的新名称:")

        @session_waiter(timeout=60, record_history_chains=False)
        async def rename_waiter(controller: SessionController, ev: AstrMessageEvent):
            new_name = ev.message_str.strip()
            if not new_name:
                controller.keep(timeout=60, reset_timeout=True)
                return
            ok, msg = await session_ops.rename_session(self.client, sid, new_name)
            await ev.send(ev.plain_result(msg))
            if ok:
                await self.plugin._refresh_sessions()
            controller.stop()

        try:
            await rename_waiter(event)
        except TimeoutError:
            yield event.plain_result("操作超时，已取消")
        finally:
            event.stop_event()

    # ── delete ──

    async def cmd_delete(self, event: AstrMessageEvent, target: str = ""):
        """删除 session: /hapi delete [序号或ID前缀]"""
        await self.state_mgr.set_user_state(event)

        # 支持传入序号或 ID 前缀
        if target:
            await self.plugin._refresh_sessions()
            sid = self._resolve_target(target)
            if not sid:
                yield event.plain_result(f"未找到匹配的 session: {target}")
                return
        else:
            sid = self.state_mgr.effective_sid(event)
            if not sid:
                yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session，或使用 /hapi delete <序号>")
                return

        # 检查是否处于 active 状态
        is_active = False
        cached = [s for s in self.sessions_cache if s.get("id") == sid]
        if cached:
            is_active = cached[0].get("active", False)

        if is_active:
            yield event.plain_result(
                f"⚠ session [{sid[:8]}] 当前处于 ACTIVE 状态，将先归档再删除\n"
                "输入 delete 确认:")
        else:
            yield event.plain_result(f"即将删除 session [{sid[:8]}]\n输入 delete 确认删除:")

        @session_waiter(timeout=30, record_history_chains=False)
        async def delete_waiter(controller: SessionController, ev: AstrMessageEvent):
            reply = ev.message_str.strip()
            if not reply:
                controller.keep(timeout=30, reset_timeout=True)
                return
            if reply == "delete":
                if is_active:
                    ok_arc, msg_arc = await session_ops.archive_session(self.client, sid)
                    if not ok_arc:
                        await ev.send(ev.plain_result(f"归档失败，删除中止: {msg_arc}"))
                        controller.stop()
                        return
                ok, msg = await session_ops.delete_session(self.client, sid)
                await ev.send(ev.plain_result(msg))
                if ok:
                    await self.state_mgr.unbind_session(sid)
                    await self.plugin._refresh_sessions()
            else:
                await ev.send(ev.plain_result("已取消"))
            controller.stop()

        try:
            await delete_waiter(event)
        except TimeoutError:
            yield event.plain_result("操作超时，已取消")
        finally:
            event.stop_event()

    # ── clean ──

    async def cmd_clean(self, event: AstrMessageEvent, path: str = ""):
        """清理 inactive sessions: /hapi clean [路径]"""
        await self.state_mgr.set_user_state(event)
        await self.plugin._refresh_sessions()

        # 筛选 inactive
        targets = [s for s in self.sessions_cache if not s.get("active", False)]

        # 路径过滤
        warning = ""
        if path:
            matched = [s for s in targets if s.get("metadata", {}).get("path", "").startswith(path)]
            if not matched:
                # 模糊匹配：找相似度最高的路径
                all_paths = list(set(s.get("metadata", {}).get("path", "") for s in targets))
                if all_paths:
                    from difflib import get_close_matches
                    closest = get_close_matches(path, all_paths, n=1, cutoff=0.3)
                    if closest:
                        matched = [s for s in targets if s.get("metadata", {}).get("path", "") == closest[0]]
                        warning = f"⚠️ 未找到路径 '{path}'，已匹配相似路径: {closest[0]}，请务必注意需要删除的文件夹是否符合预期\n\n"
            targets = matched

        if not targets:
            yield event.plain_result("没有符合条件的 inactive session")
            return

        # 使用 formatters 格式化列表
        summary = formatters.format_session_list(targets, current_sid=None)
        yield event.plain_result(f"{warning}\n将删除以下 inactive sessions:\n\n{summary}\n\n输入 yes 确认:")

        @session_waiter(timeout=30, record_history_chains=False)
        async def clean_waiter(controller: SessionController, ev: AstrMessageEvent):
            reply = ev.message_str.strip()
            if not reply:
                controller.keep(timeout=30, reset_timeout=True)
                return
            if reply.lower() == "yes":
                success = 0
                for s in targets:
                    ok, _ = await session_ops.delete_session(self.client, s["id"])
                    if ok:
                        success += 1
                await ev.send(ev.plain_result(f"清理完成: {success}/{len(targets)}\n\n💡 列表编号已更新，请用 /hapi ls 查看最新编号"))
                if success > 0:
                    await self.plugin._refresh_sessions()
            else:
                await ev.send(ev.plain_result("已取消"))
            controller.stop()

        try:
            await clean_waiter(event)
        except TimeoutError:
            yield event.plain_result("操作超时，已取消")
        finally:
            event.stop_event()

    # ── files ──

    async def cmd_files(self, event: AstrMessageEvent, path: str = "."):
        """浏览远端目录: /hapi files [-l] [路径]"""
        from . import file_ops
        await self.state_mgr.set_user_state(event)
        if w := self.plugin._conn_warning():
            yield event.plain_result(w)
        sid = self.state_mgr.effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return
        # 解析 -l 参数
        parts = path.split()
        detail = "-l" in parts
        parts = [p for p in parts if p != "-l"]
        path = parts[0] if parts else "."
        try:
            entries = await session_ops.list_directory(self.client, sid, path=path)
            text = formatters.format_directory(entries, path=path, detail=detail, sid=sid)
            from .notification_manager import NotificationManager
            for chunk in NotificationManager.split_message(text):
                yield event.plain_result(chunk)
        except Exception as e:
            yield event.plain_result(f"获取目录失败: {e}")

    # ── find ──

    async def cmd_find(self, event: AstrMessageEvent, query: str = ""):
        """搜索远端文件: /hapi find <关键词>"""
        await self.state_mgr.set_user_state(event)
        if w := self.plugin._conn_warning():
            yield event.plain_result(w)
        sid = self.state_mgr.effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return
        if not query:
            yield event.plain_result("用法: /hapi find <关键词>\n示例: /hapi find main.py")
            return
        try:
            files = await session_ops.list_files(self.client, sid, query=query)
            text = formatters.format_file_search(files, query=query)
            from .notification_manager import NotificationManager
            for chunk in NotificationManager.split_message(text):
                yield event.plain_result(chunk)
        except Exception as e:
            yield event.plain_result(f"搜索文件失败: {e}")

    # ── download ──

    async def cmd_download(self, event: AstrMessageEvent, path: str = ""):
        """下载远端文件到聊天: /hapi download <路径>"""
        import os
        import astrbot.api.message_components as Comp
        from . import file_ops
        await self.state_mgr.set_user_state(event)
        if w := self.plugin._conn_warning():
            yield event.plain_result(w)
        sid = self.state_mgr.effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return
        if not path:
            yield event.plain_result("用法: /hapi download <文件路径>\n示例: /hapi dl README.md")
            return

        # 大文件拒绝（整个文件会以 base64 加载到内存，限制 10 MB）
        size = await file_ops.get_file_size(self.client, sid, path)
        if size > 10 * 1024 * 1024:
            yield event.plain_result(
                f"文件过大 ({size / 1024 / 1024:.1f} MB)，超过 10 MB 限制，无法下载")
            return

        # 下载、解码、写临时文件
        try:
            tmp_path, filename, is_image = await file_ops.download_to_tmp(
                self.client, sid, path)
        except Exception as e:
            yield event.plain_result(f"下载文件失败: {e}")
            return

        # 发送到聊天
        try:
            if is_image:
                yield event.image_result(tmp_path)
            else:
                chain = [Comp.File(file=tmp_path, name=filename)]
                yield event.chain_result(chain)
        except Exception as e:
            yield event.plain_result(f"发送文件失败: {e}")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ── upload ──

    async def cmd_upload(self, event: AstrMessageEvent, action: str = ""):
        """上传文件到当前 session: /hapi upload [cancel]"""
        from . import file_ops
        await self.state_mgr.ensure_primary_session(event)
        sid = self.state_mgr.effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return

        # cancel 子命令：删除所有已上传文件
        if action == "cancel":
            try:
                entries = await session_ops.list_directory(self.client, sid, path="/blobs")
            except Exception as e:
                yield event.plain_result(f"获取文件列表失败: {e}")
                return

            files = [e for e in entries if e.get("type") == "file"]
            if not files:
                yield event.plain_result("当前 session 没有已上传的文件")
                return

            results = []
            for f in files:
                path = f"/blobs/{f['name']}"
                ok, msg = await file_ops.delete_uploaded_file(self.client, sid, path)
                results.append(msg)

            yield event.plain_result("\n".join(results))
            event.stop_event()
            return

        # 交互式上传
        yield event.plain_result(
            "请发送要上传的文件（支持图片和文件，可多个）\n"
            "完成后输入 done，取消输入 cancel"
        )

        collected_files = []

        @session_waiter(timeout=120, record_history_chains=False)
        async def upload_waiter(controller: SessionController, ev: AstrMessageEvent):
            nonlocal collected_files

            files = file_ops.extract_files_from_message(ev)
            if files:
                collected_files.extend(files)
                await ev.send(ev.plain_result(
                    f"✓ 已接收 {len(files)} 个文件（共 {len(collected_files)} 个）\n"
                    "继续发送或输入 done"
                ))
                controller.keep(timeout=120, reset_timeout=True)
                return

            text = ev.message_str.strip().lower()

            # 忽略空消息
            if not text:
                controller.keep(timeout=120, reset_timeout=True)
                return

            # 取消
            if text == "cancel":
                await ev.send(ev.plain_result("已取消上传"))
                controller.stop()
                return

            # 完成
            if text == "done":
                if not collected_files:
                    await ev.send(ev.plain_result("未收到任何文件"))
                    controller.stop()
                    return

                # 开始上传
                await ev.send(ev.plain_result(f"正在上传 {len(collected_files)} 个文件..."))

                attachments = []
                results = []
                for fpath in collected_files:
                    ok, msg, attach = await file_ops.upload_file(self.client, sid, fpath)
                    results.append(msg)
                    if ok and attach:
                        attachments.append(attach)

                summary = "\n".join(results)
                flavor = self.state_mgr.effective_flavor(ev)
                summary += f"\n\n已上传 {len(attachments)} 个文件到 [{flavor}] {sid[:8]}"
                await ev.send(ev.plain_result(summary))
                controller.stop()
                return

            await ev.send(ev.plain_result("未检测到文件，请重新发送"))
            controller.keep(timeout=120, reset_timeout=True)

        try:
            await upload_waiter(event)
        except TimeoutError:
            yield event.plain_result("操作超时，已取消")
        finally:
            event.stop_event()

    # ── bind ──

    async def cmd_bind(self, event: AstrMessageEvent, arg: str = ""):
        """设置默认发送窗口: /hapi bind [claude|codex|gemini|status|reset]"""
        from .state_manager import NOTIFICATION_ROUTE_FLAVORS
        await self.state_mgr.ensure_primary_session(event)
        sender_id = str(event.get_sender_id())
        umo = event.unified_msg_origin
        action = (arg or "").strip().lower()

        if not action:
            # 设置当前窗口为默认
            state = self.state_mgr._user_states_cache.get(sender_id, {})
            state["primary_umo"] = umo
            self.state_mgr._user_states_cache[sender_id] = state
            await self.plugin.put_kv_data(f"user_state_{sender_id}", state)
            yield event.plain_result("✓ 已设置当前窗口为默认发送窗口")
        elif action in NOTIFICATION_ROUTE_FLAVORS:
            state = dict(self.state_mgr._user_states_cache.get(sender_id, {}))
            flavor_routes = self.state_mgr.normalized_flavor_primary_umos(state)
            flavor_routes[action] = umo
            state["flavor_primary_umos"] = flavor_routes
            self.state_mgr._user_states_cache[sender_id] = state
            await self.plugin.put_kv_data(f"user_state_{sender_id}", state)
            yield event.plain_result(f"✓ 已设置当前窗口为 {action} 默认发送窗口")
        elif action == "status":
            text = await self.plugin._format_bind_status_text(event)
            yield event.plain_result(text)
        elif action == "reset":
            async for result in self.cmd_reset(event):
                yield result
        else:
            yield event.plain_result(
                f"❌ 无效参数: {action}\n\n"
                "用法:\n"
                "  /hapi bind              设置当前窗口为默认\n"
                "  /hapi bind claude       设置当前窗口为 claude 默认\n"
                "  /hapi bind codex        设置当前窗口为 codex 默认\n"
                "  /hapi bind gemini       设置当前窗口为 gemini 默认\n"
                "  /hapi bind status       查看推送路由\n"
                "  /hapi bind reset        重置窗口路由"
            )

    # ── routes ──

    async def cmd_routes(self, event: AstrMessageEvent):
        """查看会话推送路由"""
        await self.state_mgr.ensure_primary_session(event)
        await self.plugin._refresh_sessions()

        lines = ["会话推送路由："]
        has_routes = False

        for sid, umo in self.state_mgr._session_owners.items():
            s = next((s for s in self.sessions_cache if s["id"] == sid), None)
            if s and umo:
                flavor = s.get("metadata", {}).get("flavor", "?")
                summary = s.get("metadata", {}).get("summary", ).get("text", "")[:20]
                umo_display = umo[:40] + "..." if len(umo) > 40 else umo
                lines.append(f"  [{flavor}] {sid[:8]} {summary}\n    → {umo_display}")
                has_routes = True

        sender_id = str(event.get_sender_id())
        state = self.state_mgr._user_states_cache.get(sender_id, {})
        primary = state.get("primary_umo")

        if primary:
            display = self.state_mgr.format_umo_for_display(str(primary))
            lines.append(f"\n默认发送窗口: {display}")
            has_routes = True

        flavor_routes = self.state_mgr.normalized_flavor_primary_umos(state)
        if flavor_routes:
            lines.append("\nFlavor 默认窗口:")
            for flavor in sorted(flavor_routes):
                display = self.state_mgr.format_umo_for_display(flavor_routes[flavor])
                lines.append(f"  {flavor} -> {display}")
            has_routes = True

        if not has_routes:
            yield event.plain_result("暂无推送路由\n使用 /hapi bind 设置默认发送窗口")
        else:
            yield event.plain_result("\n".join(lines))

    # ── reset ──

    async def cmd_reset(self, event: AstrMessageEvent):
        """重置所有状态（/hapi bind reset；清空捕获关系和窗口状态，保留默认窗口和 flavor 默认路由）"""
        await self.state_mgr.ensure_primary_session(event)

        umos_to_clear = set(self.binding_mgr._window_states.keys())
        for owners in self.state_mgr._session_owners.values():
            umos_to_clear.update(owners)

        self.binding_mgr.reset_all_states()

        await self.plugin.put_kv_data("session_owners", {})
        for umo in umos_to_clear:
            await self.plugin.put_kv_data(f"window_state_{umo}", None)

        await self.plugin._refresh_sessions()

        yield event.plain_result("✓ 已重置所有状态\n捕获关系和窗口状态已清空，默认窗口和 flavor 默认路由已保留")

