"""命令处理器 - 处理所有 /hapi 子命令
"""

from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.session_waiter import session_waiter, SessionController
from ..render import formatters
from ..core import session_ops
from ..render.formatters import is_compact_request
from ..core.hapi_routes import ROUTE_HANDLERS, ROUTE_TAKES_ARG


def _session_resume_state(session: dict) -> str:
    """Return the lifecycle state used by /hapi resume pre-checks."""
    explicit_state = session.get("state")
    if isinstance(explicit_state, str) and explicit_state:
        return explicit_state

    metadata = session.get("metadata") or {}
    if isinstance(metadata, dict):
        lifecycle_state = metadata.get("lifecycleState")
        if isinstance(lifecycle_state, str) and lifecycle_state:
            return lifecycle_state

    if "active" in session:
        return "active" if session.get("active") else "inactive"

    return "unknown"


class CommandHandlers:
    """处理所有 /hapi 子命令"""

    # 与 hapi_routes 同源，便于外部引用
    ROUTE_TAKES_ARG = ROUTE_TAKES_ARG

    def __init__(self, plugin):
        self.plugin = plugin
        # 不缓存 client：WebUI 保存/重连会替换 plugin.client
        self.sessions_cache = plugin.sessions_cache
        self.state_mgr = plugin.state_mgr
        self.sse_listener = plugin.sse_listener
        self.binding_mgr = plugin.binding_mgr

    @property
    def client(self):
        """始终使用插件当前 client（重连后自动生效）。"""
        return self.plugin.client

    def _handler_for(self, subcommand: str):
        """按子命令名解析 handler；别名映射到同一方法。"""
        method = ROUTE_HANDLERS.get(subcommand)
        return getattr(self, method, None) if method else None

    # ──── 公共 helper ────

    def _require_sid(self, event: AstrMessageEvent, cmd: str = "") -> tuple[str | None, str | None]:
        """获取当前窗口选中的 session ID；未选中时返回统一提示文案 (sid, err)"""
        sid = self.state_mgr.effective_sid(event)
        if sid:
            return sid, None
        hint = "请先用 /hapi sw <序号> 选择一个 session"
        if cmd:
            hint += f"，或使用 /hapi {cmd} <序号>"
        return None, hint

    def _visible_sids(self, event: AstrMessageEvent) -> set[str]:
        """当前窗口可见的 session ID 集合（含窗口自身 ID，覆盖 LLM 工具请求）"""
        sids = {
            s.get("id")
            for s in self.state_mgr.visible_sessions_for_window(event, self.sessions_cache)
            if s.get("id")
        }
        sids.add(event.unified_msg_origin)
        return sids

    def _resolve_target_verbose(self, target: str) -> tuple[str | None, str | None]:
        """解析序号或 ID 前缀为 session ID；返回 (sid, 错误提示)，多个匹配时列出候选"""
        if target.isdigit():
            idx = int(target)
            if 1 <= idx <= len(self.sessions_cache):
                return self.sessions_cache[idx - 1]["id"], None
        matches = [s for s in self.sessions_cache if s.get("id", "").startswith(target)]
        if len(matches) == 1:
            return matches[0]["id"], None
        if len(matches) > 1:
            labels = "\n".join(f"  {s['id'][:8]}..." for s in matches)
            return None, f"匹配到 {len(matches)} 个 session，请输入更长的 ID 前缀:\n{labels}"
        return None, f"未找到匹配「{target}」的 session"

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
        if subcommand not in self.ROUTE_TAKES_ARG:
            yield event.plain_result(formatters.format_unknown_command_help(subcommand))
            return

        handler = self._handler_for(subcommand)
        if handler is None:
            yield event.plain_result(formatters.format_unknown_command_help(subcommand))
            return

        await self.state_mgr.ensure_primary_session(event)
        takes_arg = self.ROUTE_TAKES_ARG[subcommand]
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
        await self.plugin._refresh_sessions()
        machine_hint = await self.plugin._machine_status_hint()

        if scope_head == "all":
            # 全局列表：文本走 bind status；卡片走 path 分组 + 全局序号（与 list 同引擎）
            text = await self.plugin._format_bind_status_text(event)
            if machine_hint:
                text += "\n\n" + machine_hint
            from ..render import output_present
            current_sid = self.state_mgr.effective_sid(event)
            payload = output_present.build_session_list_payload(
                self.sessions_cache,
                current_sid,
                all_sessions=self.sessions_cache,
                header=f"全局 · 共 {len(self.sessions_cache)} 个",
                scope="all",
            )
            async for result in output_present.present(
                self.plugin, event, "session_list", payload, text
            ):
                yield result
            return

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

        # 可选结构卡（Pillow）；失败/未安装/render_mode=text 时回退纯文本
        from ..render import output_present
        payload = output_present.build_session_list_payload(
            visible_sessions,
            current_sid,
            all_sessions=self.sessions_cache,
            header=f"当前窗口 · {len(visible_sessions)} 个",
            header_current_window=event.unified_msg_origin,
            scope="window",
        )
        async for result in output_present.present(
            self.plugin, event, "session_list", payload, text
        ):
            yield result

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

        chosen_sid, err = self._resolve_target_verbose(target)
        if err:
            yield event.plain_result(err)
            return
        chosen = next(s for s in self.sessions_cache if s.get("id") == chosen_sid)

        sid = chosen["id"]
        flavor = chosen.get("metadata", {}).get("flavor", "claude")
        umo = event.unified_msg_origin
        await self.state_mgr.capture_window(sid, umo, flavor)
        summary = formatters.get_session_title(chosen)
        yield event.plain_result(f"已切换到 [{flavor}] {sid[:8]}... {summary}")

    # ── s (status) ──

    async def cmd_status(self, event: AstrMessageEvent):
        """查看当前 session 状态（出卡优先）"""
        await self.state_mgr.ensure_primary_session(event)
        await self.state_mgr.set_user_state(event)
        sid, err = self._require_sid(event)
        if err:
            yield event.plain_result(err)
            return
        try:
            detail = await session_ops.fetch_session_detail(self.client, sid)
            text = formatters.format_session_status(detail)
            from ..render import output_present
            payload = output_present.build_status_payload(detail)
            async for result in output_present.present(
                self.plugin, event, "status", payload, text
            ):
                yield result
        except Exception as e:
            yield event.plain_result(f"获取状态失败: {e}")

    # ── msg ──

    async def cmd_msg(self, event: AstrMessageEvent, rounds: str = ""):
        """查看最近消息（按轮次）: /hapi msg [轮数]"""
        from astrbot.api import logger
        logger.debug(f"[cmd_msg] 收到参数 rounds='{rounds}', type={type(rounds)}")
        await self.state_mgr.set_user_state(event)
        sid, err = self._require_sid(event)
        if err:
            yield event.plain_result(err)
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
                from ..core.notification_manager import NotificationManager
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
            yield event.plain_result(f"✗ 无效序号，当前共 {len(self.sessions_cache)} 个 session")
            return

        target = self.sessions_cache[idx - 1]
        target_sid = target["id"]
        target_flavor = target.get("metadata", {}).get("flavor", "claude")

        ok_ready, ready_sid, ready_msg = await self.plugin.ensure_session_for_send(event, target_sid)
        if not ok_ready:
            yield event.plain_result(f"发送前恢复 session 失败: {ready_msg}")
            return
        if ready_sid != target_sid:
            target_sid = ready_sid
            target_flavor = self.state_mgr.effective_flavor(event) or target_flavor

        # 提醒用户当前窗口的 session
        current_sid = self.state_mgr.current_sid(event)
        reminder = ready_msg
        if current_sid and current_sid != target_sid:
            reminder += f"→ 发送到 [{target_flavor}] {target_sid[:8]} (当前窗口: {current_sid[:8]})\n"

        self.plugin._last_sends[event.unified_msg_origin] = (target_sid, text)
        ok, msg = await session_ops.send_message(self.client, target_sid, text)
        await self.state_mgr.set_user_state(event)
        yield event.plain_result(reminder + msg)

    # ── send（发送到当前会话） ──

    async def cmd_send(self, event: AstrMessageEvent, text: str = ""):
        """发消息到当前窗口选中的 session: /hapi send <内容>"""
        content = (text or "").strip()
        if not content:
            yield event.plain_result("格式: /hapi send <内容>（发送到当前会话；附件请用快捷前缀或 /hapi upload）")
            return

        sid, err = self._require_sid(event)
        if err:
            yield event.plain_result(err)
            return

        ok_ready, ready_sid, ready_msg = await self.plugin.ensure_session_for_send(event, sid)
        if not ok_ready:
            yield event.plain_result(f"发送前恢复 session 失败: {ready_msg}")
            return

        self.plugin._last_sends[event.unified_msg_origin] = (ready_sid, content)
        ok, msg = await session_ops.send_message(self.client, ready_sid, content)
        await self.state_mgr.set_user_state(event)
        yield event.plain_result((ready_msg or "") + msg)

    # ── retry（重发上一条） ──

    async def cmd_retry(self, event: AstrMessageEvent):
        """重发本窗口上一条发出的消息: /hapi retry"""
        record = self.plugin._last_sends.get(event.unified_msg_origin)
        if not record:
            yield event.plain_result("本窗口还没有可重发的消息（插件重启后记录会清空）")
            return

        sid, text = record
        await self.plugin._refresh_sessions()
        if not any(s.get("id") == sid for s in self.sessions_cache):
            yield event.plain_result(
                f"上一条消息的目标会话 [{sid[:8]}] 已不存在，"
                "请用 /hapi sw 选择会话后手动发送"
            )
            return

        ok_ready, ready_sid, ready_msg = await self.plugin.ensure_session_for_send(event, sid)
        if not ok_ready:
            yield event.plain_result(f"发送前恢复 session 失败: {ready_msg}")
            return

        preview = text if len(text) <= 40 else text[:40] + "..."
        ok, msg = await session_ops.send_message(self.client, ready_sid, text)
        await self.state_mgr.set_user_state(event)
        yield event.plain_result(f"🔁 重发「{preview}」\n{(ready_msg or '')}{msg}")

    # ── perm ──

    async def cmd_perm(self, event: AstrMessageEvent, mode: str = ""):
        """查看/切换权限模式: /hapi perm [模式名]"""
        from .flavor_profiles import (
            allows_any_permission_mode,
            flavor_label,
            is_permission_mode_allowed,
            permission_modes_for,
            profile_for,
        )
        await self.state_mgr.set_user_state(event)
        sid, err = self._require_sid(event)
        if err:
            yield event.plain_result(err)
            return

        flavor = self.state_mgr.effective_flavor(event) or "claude"
        p = profile_for(flavor)
        modes = permission_modes_for(flavor)
        passthrough = allows_any_permission_mode(flavor)

        if p.permission_modes is not None and len(p.permission_modes) == 0:
            note = p.notes or "该 agent 不支持运行时权限模式切换"
            yield event.plain_result(f"({flavor_label(flavor)}) {note}")
            return

        if mode:
            target = mode
            if mode.isdigit() and modes and 1 <= int(mode) <= len(modes):
                target = modes[int(mode) - 1]
            if not passthrough and not is_permission_mode_allowed(flavor, target):
                yield event.plain_result(f"✗ 无效模式: {mode}\n可用: {', '.join(modes)}")
                return
            ok, msg = await session_ops.set_permission_mode(self.client, sid, target)
            yield event.plain_result(msg)
        else:
            try:
                detail = await session_ops.fetch_session_detail(self.client, sid)
                current = detail.get("permissionMode", "default")
                if modes:
                    text = formatters.format_permission_modes(modes, current)
                else:
                    text = f"当前: {current}\n（无本地枚举，可直接输入模式名）"
                header = f"({flavor_label(flavor)} / {flavor})"
                if p.notes:
                    header += f"\n{p.notes}"
                yield event.plain_result(f"{header}\n{text}")
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
                if reply.isdigit() and modes and 1 <= int(reply) <= len(modes):
                    target = modes[int(reply) - 1]
                if not passthrough and not is_permission_mode_allowed(flavor, target):
                    await ev.send(ev.plain_result(f"✗ 无效模式，可用: {', '.join(modes)}"))
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
        from .flavor_profiles import flavor_label, model_modes_for, profile_for, supports_model_change
        await self.state_mgr.set_user_state(event)
        sid, err = self._require_sid(event)
        if err:
            yield event.plain_result(err)
            return

        flavor = self.state_mgr.effective_flavor(event) or "claude"
        if not supports_model_change(flavor):
            yield event.plain_result(
                f"当前 session（{flavor_label(flavor)}）暂不确定是否支持切换模型；"
                "若 HAPI 支持，可直接 /hapi model <模型名> 尝试"
            )
            # 仍允许透传任意模型名（自适应）
            if not mode:
                return

        modes = model_modes_for(flavor)
        freeform = not modes  # 无静态列表时允许自由输入

        if mode:
            target = mode
            if mode.isdigit() and modes and 1 <= int(mode) <= len(modes):
                target = modes[int(mode) - 1]
            if modes and target not in modes and not freeform:
                yield event.plain_result(f"✗ 无效模式: {mode}\n可用: {', '.join(modes)}")
                return
            ok, msg = await session_ops.set_model_mode(self.client, sid, target)
            yield event.plain_result(msg)
        else:
            try:
                detail = await session_ops.fetch_session_detail(self.client, sid)
                current = detail.get("modelMode") or detail.get("model") or "default"
                if modes:
                    text = formatters.format_model_modes(modes, current)
                else:
                    text = (
                        f"({flavor_label(flavor)}) 当前模型: {current}\n"
                        "无本地模型列表，直接回复模型名即可切换"
                    )
                p = profile_for(flavor)
                if p.notes:
                    text = f"{p.notes}\n{text}"
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
                if reply.isdigit() and modes and 1 <= int(reply) <= len(modes):
                    target = modes[int(reply) - 1]
                if modes and target not in modes and not freeform:
                    await ev.send(ev.plain_result(f"✗ 无效模式，可用: {', '.join(modes)}"))
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

    # ── effort ──

    async def cmd_effort(self, event: AstrMessageEvent, effort: str = ""):
        """查看/切换推理强度: /hapi effort [值]"""
        from .flavor_profiles import (
            effort_allows_freeform,
            effort_none_aliases,
            effort_none_label,
            effort_options_for,
            effort_values_for,
            flavor_label,
            profile_for,
            supports_any_effort,
            supports_reasoning_effort,
        )
        await self.state_mgr.set_user_state(event)
        sid, err = self._require_sid(event)
        if err:
            yield event.plain_result(err)
            return

        flavor = self.state_mgr.effective_flavor(event) or "claude"
        if not supports_any_effort(flavor):
            yield event.plain_result(
                f"推理强度设置当前未映射到 {flavor_label(flavor)} session"
            )
            return

        use_reasoning = supports_reasoning_effort(flavor)
        freeform = effort_allows_freeform(flavor)
        options = effort_options_for(flavor)
        valid_values = effort_values_for(flavor)
        none_aliases = effort_none_aliases(flavor)
        none_label = effort_none_label(flavor)

        async def _apply(target):
            if use_reasoning:
                return await session_ops.set_codex_reasoning_effort(self.client, sid, target)
            return await session_ops.set_effort(self.client, sid, target)

        if effort:
            val = effort.lower()
            target = None if val in none_aliases else val
            if (
                target is not None
                and valid_values
                and target not in valid_values
                and not freeform
            ):
                yield event.plain_result(
                    f"✗ 无效值: {effort}\n可用: {none_label}, {', '.join(valid_values)}"
                )
                return
            ok, msg = await _apply(target)
            yield event.plain_result(msg)
        else:
            try:
                detail = await session_ops.fetch_session_detail(self.client, sid)
                current = (
                    detail.get("modelReasoningEffort")
                    if use_reasoning
                    else detail.get("effort")
                )
                current = current or none_label
            except Exception:
                yield event.plain_result("获取推理强度信息失败")
                return

            p = profile_for(flavor)
            lines = [f"({flavor_label(flavor)}) 当前推理强度，回复序号或名称切换："]
            if p.notes:
                lines.append(p.notes)
            if freeform:
                lines.append("（上游支持动态选项，列表外的值也可直接输入）")
            for i, (val, label) in enumerate(options, 1):
                mark = " ◀" if (val or none_label) == current else ""
                lines.append(f"  {i}. {label}{mark}")
            yield event.plain_result("\n".join(lines))

            @session_waiter(timeout=30, record_history_chains=False)
            async def effort_waiter(controller: SessionController, ev: AstrMessageEvent):
                reply = ev.message_str.strip().lower()
                if not reply:
                    controller.keep(timeout=30, reset_timeout=True)
                    return
                if reply.isdigit() and 1 <= int(reply) <= len(options):
                    target = options[int(reply) - 1][0]
                elif reply in none_aliases:
                    target = None
                elif freeform or not valid_values or reply in valid_values:
                    target = reply
                else:
                    await ev.send(
                        ev.plain_result(
                            f"✗ 无效值，可用: {none_label}, {', '.join(valid_values)}"
                        )
                    )
                    controller.stop()
                    return
                ok, msg = await _apply(target)
                await ev.send(ev.plain_result(msg))
                controller.stop()

            try:
                await effort_waiter(event)
            except TimeoutError:
                yield event.plain_result("操作超时，已取消")
            finally:
                event.stop_event()

    # ── plan ──

    async def cmd_plan(self, event: AstrMessageEvent, arg: str = ""):
        """切换 Plan 模式（toggle）: permissionMode 或 collaborationMode，按 flavor profile"""
        from .flavor_profiles import flavor_label, profile_for, supports_plan
        await self.state_mgr.set_user_state(event)
        sid, err = self._require_sid(event)
        if err:
            yield event.plain_result(err)
            return

        flavor = self.state_mgr.effective_flavor(event) or "claude"
        p = profile_for(flavor)
        if not supports_plan(flavor):
            yield event.plain_result(
                f"Plan 模式当前未映射到 {flavor_label(flavor)} session"
            )
            return

        try:
            detail = await session_ops.fetch_session_detail(self.client, sid)
        except Exception:
            yield event.plain_result("获取 session 状态失败")
            return

        if p.plan_via_collaboration:
            current = detail.get("collaborationMode", "default")
            target = "default" if current == "plan" else "plan"
            ok, msg = await session_ops.set_collaboration_mode(self.client, sid, target)
            if ok:
                for s in self.sessions_cache:
                    if s.get("id") == sid:
                        s["collaborationMode"] = target
                        break
        else:
            current = detail.get("permissionMode", "default")
            target = "default" if current == "plan" else "plan"
            ok, msg = await session_ops.set_permission_mode(self.client, sid, target)
            if ok:
                for s in self.sessions_cache:
                    if s.get("id") == sid:
                        s["permissionMode"] = target
                        break

        action = "已开启" if target == "plan" else "已关闭"
        if ok:
            label = formatters.session_label_short(sid, self.sessions_cache)
            yield event.plain_result(f"{label}\n该 session 的 Plan 模式{action}")
        else:
            yield event.plain_result(msg)

    # ── fast (Codex service tier) ──

    async def cmd_fast(self, event: AstrMessageEvent, mode: str = ""):
        """查看/切换 Codex Fast mode: /hapi fast [on|off|fast|standard]"""
        from .flavor_profiles import (
            flavor_label,
            normalize_service_tier,
            service_tier_options,
            supports_service_tier,
        )
        await self.state_mgr.set_user_state(event)
        sid, err = self._require_sid(event)
        if err:
            yield event.plain_result(err)
            return

        flavor = self.state_mgr.effective_flavor(event) or "claude"
        if not supports_service_tier(flavor):
            yield event.plain_result(
                f"Fast mode（service tier）当前仅映射到 Codex session，"
                f"当前为 {flavor_label(flavor)}"
            )
            return

        options = service_tier_options()

        async def _apply(tier: str):
            ok, msg = await session_ops.set_service_tier(self.client, sid, tier)
            if ok:
                for s in self.sessions_cache:
                    if s.get("id") == sid:
                        s["serviceTier"] = tier
                        break
            return ok, msg

        if mode:
            tier = normalize_service_tier(mode)
            if tier is None:
                yield event.plain_result(
                    f"✗ 无效值: {mode}\n可用: on/off、fast/standard"
                )
                return
            ok, msg = await _apply(tier)
            yield event.plain_result(msg)
            return

        try:
            detail = await session_ops.fetch_session_detail(self.client, sid)
            current = detail.get("serviceTier") or "standard"
        except Exception:
            yield event.plain_result("获取 Fast mode 状态失败")
            return

        lines = [
            f"({flavor_label(flavor)}) Codex Fast mode，回复序号或 on/off 切换：",
            f"当前: {current}",
        ]
        for i, (val, label) in enumerate(options, 1):
            mark = " ◀" if val == current else ""
            lines.append(f"  {i}. {label}{mark}")
        yield event.plain_result("\n".join(lines))

        @session_waiter(timeout=30, record_history_chains=False)
        async def fast_waiter(controller: SessionController, ev: AstrMessageEvent):
            reply = ev.message_str.strip().lower()
            if not reply:
                controller.keep(timeout=30, reset_timeout=True)
                return
            if reply.isdigit() and 1 <= int(reply) <= len(options):
                tier = options[int(reply) - 1][0]
            else:
                tier = normalize_service_tier(reply)
            if tier is None:
                await ev.send(ev.plain_result("✗ 无效值，可用: on/off、fast/standard"))
                controller.stop()
                return
            ok, msg = await _apply(tier)
            await ev.send(ev.plain_result(msg))
            controller.stop()

        try:
            await fast_waiter(event)
        except TimeoutError:
            yield event.plain_result("操作超时，已取消")
        finally:
            event.stop_event()

    # ── focus (专注模式) ──

    async def cmd_focus(self, event: AstrMessageEvent, mode: str = ""):
        """开启/关闭 Focus 模式: /hapi focus [on|off]"""
        await self.state_mgr.set_user_state(event)
        umo = event.unified_msg_origin

        # 中文子命令直呼（/hapi 专注）时补默认参数；已带显式参数（如关键词映射）不覆盖
        if not mode:
            raw_cmd = self.plugin._extract_hapi_remainder(event).strip().lower()
            if raw_cmd == "专注":
                mode = "on"
            elif raw_cmd == "退出专注":
                mode = "off"

        current_focus = self.binding_mgr.get_window_focus_mode(umo)

        if not mode:
            status = "开启" if current_focus else "关闭"
            sid = self.state_mgr.effective_sid(event)
            if not sid:
                yield event.plain_result(
                    f"Focus 模式当前: {status}\n"
                    "请先用 /hapi sw 选择一个 session，然后用 /hapi focus on 开启"
                )
                return

            session = next((s for s in self.sessions_cache if s.get("id") == sid), None)
            title = formatters.get_session_title(session) if session else sid[:8]

            yield event.plain_result(
                f"Focus 模式当前: {status}\n"
                f"目标 session: {title}\n\n"
                "回复 on 或 off："
            )

            @session_waiter(timeout=30, record_history_chains=False)
            async def focus_waiter(controller: SessionController, ev: AstrMessageEvent):
                reply = ev.message_str.strip().lower()
                if not reply:
                    controller.keep(timeout=30, reset_timeout=True)
                    return
                if reply not in ("on", "off", "开启", "关闭"):
                    await ev.send(ev.plain_result("✗ 请回复 on 或 off"))
                    controller.stop()
                    return
                enabled = reply in ("on", "开启")
                self.binding_mgr.set_window_focus_mode(umo, enabled)
                await self.state_mgr.persist_window_state(umo)
                if not enabled:
                    self.plugin._clear_staged_attachments(umo)
                if enabled:
                    await ev.send(ev.plain_result(
                        "此聊天窗口的 Focus 模式已开启。\n"
                        "当前窗口文字消息、附件、图片等消息将会自动发送到 Hapi agent。"
                    ))
                else:
                    await ev.send(ev.plain_result("Focus 模式已关闭"))
                controller.stop()

            try:
                await focus_waiter(event)
            except TimeoutError:
                yield event.plain_result("操作超时，已取消")
            finally:
                event.stop_event()
            return

        normalized = mode.lower()
        if normalized not in ("on", "off", "开启", "关闭"):
            yield event.plain_result("✗ 参数错误，用法: /hapi focus [on|off]")
            return

        enabled = normalized in ("on", "开启")

        if enabled:
            sid = self.state_mgr.effective_sid(event)
            if not sid:
                yield event.plain_result("✗ 请先用 /hapi sw 选择一个 session")
                return

        self.binding_mgr.set_window_focus_mode(umo, enabled)
        await self.state_mgr.persist_window_state(umo)

        if enabled:
            yield event.plain_result(
                "此聊天窗口的 Focus 模式已开启。\n"
                "当前窗口文字消息、附件、图片等消息将会自动发送到 Hapi agent。"
            )
        else:
            self.plugin._clear_staged_attachments(umo)
            yield event.plain_result("Focus 模式已关闭")

    # ── remote ──

    async def cmd_remote(self, event: AstrMessageEvent):
        """切换当前 session 到 remote 远程托管模式"""
        await self.state_mgr.set_user_state(event)
        sid, err = self._require_sid(event)
        if err:
            yield event.plain_result(err)
            return
        ok, msg = await session_ops.switch_to_remote(self.client, sid)
        yield event.plain_result(msg)

    # ── output ──

    _OUTPUT_LEVELS = {
        "silence": "几乎不推正文，主要保留权限请求等关键提醒；可作为 agent 完成任务/需要审批时的通知",
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
                tag = " ◀" if lvl == current else ""
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
                    await ev.send(ev.plain_result(f"✗ 无效级别: {reply}\n可用: {', '.join(levels)}"))
                else:
                    self.sse_listener.output_level = t
                    self.plugin.config["output_level"] = t
                    self.plugin.config.save_config()
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
            lines = [f"✗ 无效级别: {level}\n", "可用:"]
            for i, (lvl, desc) in enumerate(self._OUTPUT_LEVELS.items(), 1):
                lines.append(f"  [{i}] {lvl} — {desc}")
            yield event.plain_result("\n".join(lines))
            return

        self.sse_listener.output_level = target
        self.plugin.config["output_level"] = target
        self.plugin.config.save_config()
        yield event.plain_result(
            f"SSE 推送级别已切换为: {target}\n{self._OUTPUT_LEVELS[target]}")

    # ── pending (查看待审批列表) ──

    async def cmd_pending(self, event: AstrMessageEvent):
        """查看待审批请求列表: /hapi pending"""
        await self.state_mgr.set_user_state(event)
        pending = self.plugin.pending_mgr.get_pending_for_window(event, self._visible_sids(event))
        text = formatters.format_pending_requests(pending, self.sessions_cache)
        from ..render import output_present
        payload = output_present.build_pending_payload(pending, self.sessions_cache)
        async for result in output_present.present(
            self.plugin, event, "pending", payload, text
        ):
            yield result

    # ── approve ──

    async def cmd_approve(self, event: AstrMessageEvent):
        """批准所有权限请求，再交互式回答 question: /hapi a"""
        await self.state_mgr.set_user_state(event)
        items = self.plugin.pending_mgr.flatten_pending(event, self._visible_sids(event))
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
            yield event.plain_result(f"还有 {len(questions)} 个问题需要回答：")
            await self.plugin.pending_mgr.answer_questions_interactive(
                event, questions, self.client, session_waiter, SessionController)

        event.stop_event()

    # ── allow ──

    async def cmd_allow(self, event: AstrMessageEvent, target: str = ""):
        """批准权限请求（跳过 question）: /hapi allow [序号]"""
        await self.state_mgr.set_user_state(event)
        items = self.plugin.pending_mgr.flatten_pending(event, self._visible_sids(event))
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
                yield event.plain_result(f"✗ 无效序号 {n}，可用 /hapi pending 查看序号")
                return
            sid, rid, req = found[0]
            summary_svc = getattr(self.plugin, "summary_service", None)
            if is_compact_request(req):
                ok, _ = await session_ops.send_message(self.client, sid, "/compact")
                self.plugin.pending_mgr.remove_entry(sid, rid)
                if summary_svc is not None:
                    await summary_svc.record_operation(
                        sid, "compact", ok, tool="__compact__",
                        detail=("压缩上下文 (/compact)" if ok else "批准压缩失败"), request_id=rid)
                yield event.plain_result("✓ 已批准: /compact" if ok else "✗ 批准失败: /compact 发送未成功")
            elif self.plugin.pending_mgr.is_llm_tool_request(req):
                self.plugin.pending_mgr.resolve_llm_tool(sid, rid, approved=True)
                tool = req.get("tool", "?")
                if summary_svc is not None:
                    await summary_svc.record_operation(
                        sid, "approve", True, tool=tool,
                        detail=formatters.format_request_detail(req), request_id=rid)
                yield event.plain_result(f"✓ 已批准: {tool}")
            else:
                ok, _ = await session_ops.approve_permission(self.client, sid, rid)
                tool = req.get("tool", "?")
                if summary_svc is not None:
                    await summary_svc.record_operation(
                        sid, "approve", ok, tool=tool,
                        detail=(formatters.format_request_detail(req) if ok else "批准失败"), request_id=rid)
                yield event.plain_result(f"✓ 已批准: {tool}" if ok else f"✗ 批准失败: {tool}")
        else:
            result = await self.plugin.pending_mgr.approve_items(regular, self.client)
            if result:
                yield event.plain_result(result)

    # ── answer ──

    async def cmd_answer(self, event: AstrMessageEvent, target: str = ""):
        """交互式回答 question 请求: /hapi answer [序号]"""
        await self.state_mgr.set_user_state(event)
        items = self.plugin.pending_mgr.flatten_pending(event, self._visible_sids(event))
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
                yield event.plain_result(f"✗ 无效序号 {n}，可用 /hapi pending 查看序号")
                return
            q_items = [found[0]]

        await self.plugin.pending_mgr.answer_questions_interactive(
            event, q_items, self.client, session_waiter, SessionController)
        event.stop_event()

    # ── deny ──

    async def cmd_deny(self, event: AstrMessageEvent, target: str = ""):
        """拒绝审批请求: /hapi deny 全部拒绝, /hapi deny <序号> 拒绝单个"""
        await self.state_mgr.set_user_state(event)
        items = self.plugin.pending_mgr.flatten_pending(event, self._visible_sids(event))
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
                yield event.plain_result(f"✗ 无效序号 {n}，可用 /hapi pending 查看序号")
                return
            sid, rid, req = found[0]
            summary_svc = getattr(self.plugin, "summary_service", None)
            if is_compact_request(req):
                self.plugin.pending_mgr.remove_entry(sid, rid)
                if summary_svc is not None:
                    await summary_svc.record_operation(
                        sid, "deny", False, tool="__compact__",
                        detail="用户取消压缩 (/compact)", request_id=rid)
                yield event.plain_result("✓ 已取消压缩: /compact")
            elif self.plugin.pending_mgr.is_llm_tool_request(req):
                self.plugin.pending_mgr.resolve_llm_tool(sid, rid, approved=False)
                tool = req.get("tool", "?")
                if summary_svc is not None:
                    await summary_svc.record_operation(
                        sid, "deny", False, tool=tool,
                        detail=formatters.format_request_detail(req), request_id=rid)
                yield event.plain_result(f"✓ 已拒绝: {tool}")
            else:
                ok, msg = await session_ops.deny_permission(self.client, sid, rid)
                tool = req.get("tool", "?")
                if summary_svc is not None:
                    await summary_svc.record_operation(
                        sid, "deny", False, tool=tool,
                        detail=(formatters.format_request_detail(req) if ok else f"拒绝失败: {msg}"),
                        request_id=rid)
                yield event.plain_result(f"✓ 已拒绝: {tool}" if ok else f"✗ 拒绝失败: {tool}")
        else:
            # 全部拒绝
            results = []
            summary_svc = getattr(self.plugin, "summary_service", None)
            for sid, rid, req in items:
                if is_compact_request(req):
                    self.plugin.pending_mgr.remove_entry(sid, rid)
                    if summary_svc is not None:
                        await summary_svc.record_operation(
                            sid, "deny", False, tool="__compact__",
                            detail="用户取消压缩 (/compact)", request_id=rid)
                    results.append("✓ /compact (已取消)")
                elif self.plugin.pending_mgr.is_llm_tool_request(req):
                    self.plugin.pending_mgr.resolve_llm_tool(sid, rid, approved=False)
                    tool = req.get("tool", "?")
                    if summary_svc is not None:
                        await summary_svc.record_operation(
                            sid, "deny", False, tool=tool,
                            detail=formatters.format_request_detail(req), request_id=rid)
                    results.append(f"✓ {tool}")
                else:
                    ok, msg = await session_ops.deny_permission(self.client, sid, rid)
                    tool = req.get("tool", "?")
                    if summary_svc is not None:
                        await summary_svc.record_operation(
                            sid, "deny", False, tool=tool,
                            detail=(formatters.format_request_detail(req) if ok else f"拒绝失败: {msg}"),
                            request_id=rid)
                    results.append(f"{'✓' if ok else '✗'} {tool}")
            yield event.plain_result(f"已全部拒绝（{len(items)} 个，✗ 表示操作失败）:\n" + "\n".join(results))

    # ── create ──

    async def _spawn_and_capture(self, event: AstrMessageEvent, spec: dict) -> str:
        """按 spec 创建 session 并绑定当前窗口，返回结果文案（向导与模板两路共用）。"""
        ok, msg, new_sid = await session_ops.spawn_session(
            self.client,
            machine_id=spec["machine_id"],
            directory=spec["directory"],
            agent=spec["agent"],
            session_type=spec.get("session_type", "simple"),
            yolo=spec.get("yolo", False),
            worktree_name=spec.get("worktree_name", ""),
            model_reasoning_effort=spec.get("model_reasoning_effort") or None,
        )
        await self.plugin._refresh_sessions()
        if ok and new_sid:
            flavor = spec["agent"]
            await self.state_mgr.capture_window(new_sid, event.unified_msg_origin, flavor)
            msg += f"\n已自动切换到该 session [{flavor}] {new_sid[:8]}..."
        return msg

    async def _resolve_template_machine(
        self, tpl: dict, machines: list[dict]
    ) -> tuple[str | None, str | None]:
        """解析模板机器：返回 (machine_id, error_text)。"""
        machine_id = tpl.get("machine_id") or ""
        if machine_id:
            if not any(m.get("id") == machine_id for m in machines):
                return None, (
                    f"模板指定的机器（{machine_id[:12]}...）当前不在线，"
                    "请在 WebUI 更新模板或改用交互向导"
                )
            return machine_id, None
        if len(machines) == 1:
            return machines[0]["id"], None
        return None, (
            f"当前有 {len(machines)} 台在线机器，模板未指定用哪台。\n"
            "请在 WebUI「交互优化 → 会话模板」为模板选择机器"
        )

    async def _ask_directory_like_create(
        self,
        event: AstrMessageEvent,
        *,
        title: str,
        chosen: dict,
    ):
        """模板缺目录时：与 create 向导步骤 2 相同的最近目录选择。

        chosen 为可变 dict，成功时写入 chosen["directory"]；取消/超时不写。
        """
        from .create_wizard import CreateWizard

        recent_paths: list[str] = []
        try:
            recent_paths = await session_ops.fetch_recent_paths(self.client)
        except Exception:
            pass

        prompt = CreateWizard.format_directory_prompt(
            recent_paths,
            prefix=title,
            header="选择工作目录:",
        )
        yield event.plain_result(prompt)

        @session_waiter(timeout=120, record_history_chains=False)
        async def dir_waiter(controller: SessionController, ev: AstrMessageEvent):
            raw = (ev.message_str or "").strip()
            if not raw:
                controller.keep(timeout=120, reset_timeout=True)
                return
            if raw.lower() in ("c", "cancel", "取消", "q", "quit"):
                await ev.send(ev.plain_result("已取消"))
                controller.stop()
                return
            directory = CreateWizard.resolve_directory_input(raw, recent_paths)
            if not directory:
                await ev.send(ev.plain_result("目录不能为空，请重新输入（或回复 取消）"))
                controller.keep(timeout=120, reset_timeout=True)
                return
            chosen["directory"] = directory
            controller.stop()

        try:
            await dir_waiter(event)
        except TimeoutError:
            yield event.plain_result("选择目录超时，已取消")

    async def _create_from_template(self, event: AstrMessageEvent, arg: str):
        """模板一步创建: /hapi create <模板名> [目录]

        目录优先命令参数，其次模板默认；都没有时拉 recent_paths，
        交互选择（与 /hapi create 向导步骤 2 相同）。
        """
        from .session_templates import describe_template, find_template, format_templates_list

        templates = self.state_mgr.get_session_templates()
        parts = arg.split(None, 1)
        tpl_name = parts[0]
        dir_override = parts[1].strip() if len(parts) > 1 else ""

        tpl = find_template(templates, tpl_name)
        if not tpl:
            yield event.plain_result(
                f"未找到模板「{tpl_name}」\n\n可用模板:\n{format_templates_list(templates)}\n\n"
                "不带参数的 /hapi create 会进入交互向导"
            )
            return

        # 机器解析：模板指定且在线 → 用之；未指定且仅一台在线 → 自动选
        try:
            machines = await session_ops.fetch_machines(self.client)
        except Exception as e:
            yield event.plain_result(f"获取机器列表失败: {e}")
            return
        if not machines:
            yield event.plain_result("没有在线的机器")
            return

        machine_id, machine_err = await self._resolve_template_machine(tpl, machines)
        if machine_err or not machine_id:
            yield event.plain_result(machine_err or "无法解析机器")
            return

        directory = dir_override or tpl.get("directory") or ""
        if not directory:
            # 与 create 向导一致：展示最近目录供序号选择 / 手输路径
            chosen: dict = {}
            async for msg in self._ask_directory_like_create(
                event,
                title=f"模板「{tpl['name']}」未设置默认目录",
                chosen=chosen,
            ):
                yield msg
            directory = chosen.get("directory") or ""
            if not directory:
                return

        spec = dict(tpl)
        spec["machine_id"] = machine_id
        spec["directory"] = directory

        yield event.plain_result(f"按模板创建 Session:\n{describe_template(spec)}\n\n正在创建 ...")
        msg = await self._spawn_and_capture(event, spec)
        yield event.plain_result(msg)

    async def cmd_create(self, event: AstrMessageEvent, arg: str = ""):
        """创建新 session: 无参进向导，带参走模板（/hapi create [模板名] [目录]）"""
        from .create_wizard import CreateWizard
        from .session_templates import format_templates_list
        await self.state_mgr.ensure_primary_session(event)
        await self.state_mgr.set_user_state(event)

        arg = (arg or "").strip()
        if arg:
            async for result in self._create_from_template(event, arg):
                yield result
            return

        try:
            machines = await session_ops.fetch_machines(self.client)
        except Exception as e:
            yield event.plain_result(f"获取机器列表失败: {e}")
            return

        if not machines:
            yield event.plain_result("没有在线的机器")
            return

        # 有模板时提示一步创建入口
        templates = self.state_mgr.get_session_templates()
        if templates:
            yield event.plain_result(
                "💡 可用模板（/hapi create <模板名> [目录] 一步创建）:\n"
                + format_templates_list(templates)
            )

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
                msg = await self._spawn_and_capture(ev, wiz.state)
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
            sid, err = self._require_sid(event, cmd="abort")
            if err:
                yield event.plain_result(err)
                return
        else:
            sid, err = self._resolve_target_verbose(target)
            if err:
                yield event.plain_result(err)
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
            sid, err = self._resolve_target_verbose(target)
            if err:
                yield event.plain_result(err)
                return
        else:
            sid, err = self._require_sid(event, cmd="archive")
            if err:
                yield event.plain_result(err)
                return

        yield event.plain_result(f"确认归档 session [{sid[:8]}]？\n回复 y 确认，其他任意内容取消")

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

    # ── resume ──

    async def cmd_resume(self, event: AstrMessageEvent, target: str = ""):
        """恢复已停掉的会话: /hapi resume [序号|ID前缀]"""
        await self.state_mgr.set_user_state(event)
        await self.plugin._refresh_sessions()

        if not target:
            sid, err = self._require_sid(event, cmd="resume")
            if err:
                yield event.plain_result(err)
                return
            exact = next((s for s in self.sessions_cache if s.get("id") == sid), None)
            if exact is None:
                matches = [s for s in self.sessions_cache if s.get("id", "").startswith(sid)]
                if len(matches) == 1:
                    sid = matches[0]["id"]
                    flavor = matches[0].get("metadata", {}).get("flavor", self.state_mgr.effective_flavor(event) or "claude")
                    await self.state_mgr.capture_window(sid, event.unified_msg_origin, flavor)
                elif len(matches) > 1:
                    labels = [f"  {s['id'][:8]}..." for s in matches]
                    yield event.plain_result(
                        f"当前窗口记录的 session 匹配到 {len(matches)} 个，请改用 /hapi resume <序号或更长 ID 前缀>\n"
                        + "\n".join(labels))
                    return
                else:
                    yield event.plain_result(
                        f"当前窗口记录的 session [{sid[:8]}] 已不在列表中，请用 /hapi sw <序号> 重新选择")
                    return
        else:
            sid, err = self._resolve_target_verbose(target)
            if err:
                yield event.plain_result(err)
                return

        # 状态预检查
        target_session = next((s for s in self.sessions_cache if s.get("id") == sid), None)
        if target_session:
            state = _session_resume_state(target_session)
            if state != "inactive":
                yield event.plain_result(
                    f"Session [{sid[:8]}] 当前状态为 {state}，只有已停止（inactive）的 session 才能恢复")
                return

        ok, resumed_sid, msg = await self.plugin.ensure_session_for_send(event, sid)
        if ok:
            resumed = next((s for s in self.sessions_cache if s.get("id") == resumed_sid), None)
            flavor = (resumed or {}).get("metadata", {}).get("flavor") or self.state_mgr.effective_flavor(event) or "claude"
            if resumed_sid != sid:
                msg += f"已切换到恢复后的会话 [{flavor}] {resumed_sid[:8]}..."
            elif not msg:
                msg = f"会话 [{sid[:8]}] 已可用"
        yield event.plain_result(msg)

    # ── reopen ──

    async def cmd_reopen(self, event: AstrMessageEvent, target: str = ""):
        """恢复已停掉的会话（resume 备用接口）: /hapi reopen [序号|ID前缀]"""
        await self.state_mgr.set_user_state(event)
        await self.plugin._refresh_sessions()

        if not target:
            sid, err = self._require_sid(event, cmd="reopen")
            if err:
                yield event.plain_result(err)
                return
        else:
            sid, err = self._resolve_target_verbose(target)
            if err:
                yield event.plain_result(err)
                return

        target_session = next(
            (s for s in self.sessions_cache if s.get("id") == sid), None
        )
        if target_session:
            state = _session_resume_state(target_session)
            if state != "inactive":
                yield event.plain_result(
                    f"Session [{sid[:8]}] 当前状态为 {state}，只有已停止（inactive）的 session 才能恢复"
                )
                return

        ok, msg, reopened_sid = await session_ops.reopen_session(self.client, sid)
        if ok:
            await self.plugin._refresh_sessions()
            final_sid = reopened_sid or sid
            reopened = next(
                (s for s in self.sessions_cache if s.get("id") == final_sid), None
            )
            flavor = (
                (reopened or {}).get("metadata", {}).get("flavor")
                or self.state_mgr.effective_flavor(event)
                or "claude"
            )
            await self.state_mgr.capture_window(
                final_sid, event.unified_msg_origin, flavor
            )
            if final_sid != sid:
                msg += f"\n已切换到恢复后的会话 [{flavor}] {final_sid[:8]}..."
            else:
                msg += f"\n已绑定当前窗口 [{flavor}] {final_sid[:8]}..."
        yield event.plain_result(msg)

    # ── sync ──

    def _shorten_sync_error(self, text: str, max_len: int = 80) -> str:
        """把同步失败原因压缩成一行短文本，用于聊天展示（完整信息进日志）。

        与项目其他命令的报错风格保持一致（简短一句），避免把 HAPI 原始错误
        原文、本地路径等敏感信息直接暴露在聊天里。
        """
        text = (text or "").strip().replace("\n", " ").strip()
        for prefix in ("HAPI 同步失败: ", "响应解析失败: ", "网络错误: "):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        if len(text) > max_len:
            text = text[:max_len] + "…"
        return text or "未知错误"

    async def cmd_sync(self, event: AstrMessageEvent, target: str = ""):
        """同步 Codex Session 到 HAPI: /hapi sync [序号|ID前缀]"""
        from astrbot.api import logger
        await self.state_mgr.set_user_state(event)

        if target:
            await self.plugin._refresh_sessions()
            sid, err = self._resolve_target_verbose(target)
            if err:
                yield event.plain_result(err)
                return
        else:
            sid, err = self._require_sid(event, cmd="sync")
            if err:
                yield event.plain_result(err)
                return

        from ..core import session_ops

        try:
            result = await session_ops.sync_codex_session(
                self.client,
                sid,
                service_tier="standard",
                collaboration_mode="default",
            )
        except session_ops.SyncCodexError as e:
            # 完整错误进日志，聊天只给一行简短原因（避免暴露 HAPI 错误原文/路径等）
            logger.warning(
                "sync codex session failed sid=%s: %s (status=%s, body=%s)",
                sid[:8], e, e.status, (e.body or "")[:500],
            )
            reason = self._shorten_sync_error(str(e))
            if e.status == 409:
                reason += "\n请先在 HAPI WebUI 中停止或归档该会话后重试。"
            else:
                reason += "\n详情请查看 HAPI 日志。"
            yield event.plain_result(f"同步失败 [{sid[:8]}]\n{reason}")
            return
        except Exception as e:
            logger.exception("sync codex session failed sid=%s", sid[:8])
            yield event.plain_result(f"同步失败 [{sid[:8]}]\n{self._shorten_sync_error(f'{type(e).__name__}: {e}')}")
            return

        # 成功后刷新 Session 缓存；刷新失败不误报同步失败
        try:
            await self.plugin._refresh_sessions()
        except Exception as e:
            logger.warning("sync codex refresh after success failed: %s", e)

        # 成功提示：会话卡片头（同 /hapi msg 推送的 💬📂🤖 格式）+ 导入成功新增条数
        import re
        from ..render.formatters import session_label_short
        label = session_label_short(sid, self.sessions_cache)
        count = 0
        appended_total = 0
        if isinstance(result, dict):
            try:
                count = int(result.get("syncedCount") or 0)
            except (TypeError, ValueError):
                count = 0
            # 解析每段 "Appended messages: N" 并求和。
            # 注意：Action=created（新建会话）时 N 是该 transcript 的总消息数，
            # 全部为本次新增；Action=updated（二次同步）时 N 才是真正的增量。
            output = str(result.get("output") or "").strip()
            for m in re.finditer(r"Appended messages:\s*(\d+)", output):
                try:
                    appended_total += int(m.group(1))
                except ValueError:
                    pass
        head = f"✅ 导入成功 {count} 个会话，新增消息 {appended_total} 条" if count > 0 else "✅ 导入成功"
        yield event.plain_result(f"{label}\n{head}")

    # ── rename ──

    async def cmd_rename(self, event: AstrMessageEvent, target: str = ""):
        """重命名 session: /hapi rename [序号或ID前缀]"""
        await self.state_mgr.set_user_state(event)

        if target:
            await self.plugin._refresh_sessions()
            sid, err = self._resolve_target_verbose(target)
            if err:
                yield event.plain_result(err)
                return
        else:
            sid, err = self._require_sid(event, cmd="rename")
            if err:
                yield event.plain_result(err)
                return

        yield event.plain_result(f"请输入 session [{sid[:8]}] 的新名称：")

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
            sid, err = self._resolve_target_verbose(target)
            if err:
                yield event.plain_result(err)
                return
        else:
            sid, err = self._require_sid(event, cmd="delete")
            if err:
                yield event.plain_result(err)
                return

        # 检查是否处于 active 状态
        is_active = False
        cached = [s for s in self.sessions_cache if s.get("id") == sid]
        if cached:
            is_active = cached[0].get("active", False)

        if is_active:
            yield event.plain_result(
                f"⚠️ session [{sid[:8]}] 正在运行，将先归档再删除\n"
                "输入 delete 确认，其他任意内容取消：")
        else:
            yield event.plain_result(f"即将删除 session [{sid[:8]}]\n输入 delete 确认，其他任意内容取消：")

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
        yield event.plain_result(f"{warning}\n将删除以下已停止的 session:\n\n{summary}\n\n输入 yes 确认，其他任意内容取消：")

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
                await ev.send(ev.plain_result(f"清理完成: {success}/{len(targets)}\n\n💡 列表编号已更新，可用 /hapi list 查看"))
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
        from ..core import file_ops
        await self.state_mgr.set_user_state(event)
        if w := self.plugin._conn_warning():
            yield event.plain_result(w)
        sid, err = self._require_sid(event)
        if err:
            yield event.plain_result(err)
            return
        # 解析 -l 参数
        parts = path.split()
        detail = "-l" in parts
        parts = [p for p in parts if p != "-l"]
        path = parts[0] if parts else "."
        try:
            entries = await session_ops.list_directory(self.client, sid, path=path)
            text = formatters.format_directory(entries, path=path, detail=detail, sid=sid)
            from ..core.notification_manager import NotificationManager
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
        sid, err = self._require_sid(event)
        if err:
            yield event.plain_result(err)
            return
        if not query:
            yield event.plain_result("用法: /hapi find <关键词>\n示例: /hapi find main.py")
            return
        try:
            files = await session_ops.list_files(self.client, sid, query=query)
            text = formatters.format_file_search(files, query=query)
            from ..core.notification_manager import NotificationManager
            for chunk in NotificationManager.split_message(text):
                yield event.plain_result(chunk)
        except Exception as e:
            yield event.plain_result(f"搜索文件失败: {e}")

    # ── download ──

    async def cmd_download(self, event: AstrMessageEvent, path: str = ""):
        """下载远端文件到聊天: /hapi download <路径>"""
        import os
        import astrbot.api.message_components as Comp
        from ..core import file_ops
        await self.state_mgr.set_user_state(event)
        if w := self.plugin._conn_warning():
            yield event.plain_result(w)
        sid, err = self._require_sid(event)
        if err:
            yield event.plain_result(err)
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
        from ..core import file_ops
        await self.state_mgr.ensure_primary_session(event)
        sid, err = self._require_sid(event)
        if err:
            yield event.plain_result(err)
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

            await ev.send(ev.plain_result("未检测到文件，请重新发送；输入 done 完成、cancel 取消"))
            controller.keep(timeout=120, reset_timeout=True)

        try:
            await upload_waiter(event)
        except TimeoutError:
            yield event.plain_result("操作超时，已取消")
        finally:
            event.stop_event()

    # ── bind ──

    async def cmd_bind(self, event: AstrMessageEvent, arg: str = ""):
        """设置默认发送窗口: /hapi bind [<flavor>|status|reset]"""
        from .flavor_profiles import format_bind_flavor_examples, is_bindable_flavor, normalize_flavor
        await self.state_mgr.ensure_primary_session(event)
        sender_id = str(event.get_sender_id())
        umo = event.unified_msg_origin
        action = normalize_flavor(arg)

        if not action:
            # 设置当前窗口为默认
            state = self.state_mgr._user_states_cache.get(sender_id, {})
            state["primary_umo"] = umo
            self.state_mgr._user_states_cache[sender_id] = state
            await self.plugin.put_kv_data(f"user_state_{sender_id}", state)
            yield event.plain_result("✓ 已设置当前窗口为默认发送窗口")
        elif is_bindable_flavor(action):
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
            examples = format_bind_flavor_examples()
            yield event.plain_result(
                f"✗ 无效参数: {action}\n\n"
                "用法:\n"
                "  /hapi bind              设置当前窗口为默认\n"
                f"  /hapi bind <flavor>     设置当前窗口为某 agent 默认（如 {examples}）\n"
                "  /hapi bind status       查看推送路由\n"
                "  /hapi bind reset        重置窗口路由"
            )

    # ── alias（快捷关键词映射一览） ──

    async def cmd_alias(self, event: AstrMessageEvent, arg: str = ""):
        """查看快捷关键词映射：/hapi alias [过滤词]

        文案与规则在 keyword_maps.format_maps_list，此处只取运行时配置并输出。
        """
        from .keyword_maps import (
            DEFAULT_KEYWORD_MAPS,
            format_maps_list,
            normalize_maps,
        )

        maps = getattr(self.plugin, "_cmd_keyword_maps", None)
        if not maps:
            raw = None
            try:
                raw = self.plugin.config.get("cmd_keyword_maps")
            except Exception:
                raw = None
            maps = normalize_maps(raw)
            if not maps and (
                raw is None
                or (isinstance(raw, str) and str(raw).strip() in ("", "[]"))
            ):
                maps = normalize_maps(DEFAULT_KEYWORD_MAPS)

        text = format_maps_list(maps, filter_text=arg or "")
        yield event.plain_result(text)

    # ── routes ──

    async def cmd_routes(self, event: AstrMessageEvent):
        """查看会话推送路由（出卡优先）"""
        await self.state_mgr.ensure_primary_session(event)
        await self.plugin._refresh_sessions()

        from ..render import output_present
        from ..render.umo_display import format_umo_title, resolve_umo_names

        # 收集全部相关 UMO，批量解析群名/别名
        umos_needed: set[str] = set()
        session_bind: list[tuple[str, str, dict]] = []
        for sid, umo in self.state_mgr._session_owners.items():
            if not umo:
                continue
            s = next((x for x in self.sessions_cache if x.get("id") == sid), None)
            if s:
                session_bind.append((sid, str(umo), s))
                umos_needed.add(str(umo))

        sender_id = str(event.get_sender_id())
        state = self.state_mgr._user_states_cache.get(sender_id, {})
        primary = state.get("primary_umo")
        if primary:
            umos_needed.add(str(primary))
        flavor_routes = self.state_mgr.normalized_flavor_primary_umos(state)
        for u in flavor_routes.values():
            if u:
                umos_needed.add(str(u))

        name_map: dict[str, str] = {}
        try:
            name_map = await resolve_umo_names(self.plugin.context, umos_needed)
        except Exception:
            name_map = {}

        def _win_title(umo: str) -> str:
            return format_umo_title(umo, name=name_map.get(str(umo)))

        lines = ["会话推送路由："]
        has_routes = False
        session_rows: list[dict] = []

        for sid, umo, s in session_bind:
            metadata = s.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}
            flavor = metadata.get("flavor", "?")
            summary = formatters.get_session_title(s)[:24]
            win = _win_title(umo)
            lines.append(f"  [{flavor}] {sid[:8]} {summary}\n    → {win}")
            session_rows.append({
                "sid_short": sid[:8],
                "flavor": flavor,
                "title": summary,
                "window_title": win,
                "umo": umo,
            })
            has_routes = True

        primary_title = None
        if primary:
            primary_title = _win_title(str(primary))
            lines.append(f"\n默认发送窗口: {primary_title}")
            has_routes = True

        flavor_rows: list[dict] = []
        if flavor_routes:
            lines.append("\nAgent 默认窗口:")
            for flavor in sorted(flavor_routes):
                win = _win_title(flavor_routes[flavor])
                lines.append(f"  {flavor} -> {win}")
                flavor_rows.append({
                    "flavor": flavor,
                    "window_title": win,
                    "umo": flavor_routes[flavor],
                })
            has_routes = True

        if not has_routes:
            yield event.plain_result("暂无推送路由\n使用 /hapi bind 设置默认发送窗口")
            return

        text = "\n".join(lines)
        payload = output_present.build_routes_payload(
            session_rows=session_rows,
            primary_umo=str(primary) if primary else None,
            primary_title=primary_title,
            flavor_routes=flavor_rows,
        )
        async for result in output_present.present(
            self.plugin, event, "routes", payload, text
        ):
            yield result

    # ── summary ──

    async def cmd_summary(self, event: AstrMessageEvent, arg: str = ""):
        """操作记录：/hapi summary [all|<序号|ID>|status]

        优先上一统计窗快照，否则当前桶；可重复发送（busy-hours-agent-push.md §4）。
        """
        await self.state_mgr.ensure_primary_session(event)
        await self.state_mgr.set_user_state(event)
        summary_svc = getattr(self.plugin, "summary_service", None)
        if summary_svc is None:
            yield event.plain_result("操作记录服务未就绪")
            return

        normalized = (arg or "").strip()
        if normalized == "status":
            yield event.plain_result(
                formatters.format_summary_status(
                    summary_svc.status(), self.sessions_cache
                )
            )
            return

        status = summary_svc.status()
        if not bool(self.plugin.config.get("auto_approve_silent", False)):
            yield event.plain_result(
                "操作记录统计未开启：托管时段内的操作不会记录。\n"
                "请在 WebUI「设置 → 审批」开启「Agent 操作记录统计」（auto_approve_silent）后再试；"
                "当前配置可用 /hapi summary status 查看。"
            )
            return
        session_infos = status.get("sessions") or {}
        # 有当前桶或上一窗快照的 sid
        record_sids = [
            sid for sid, info in session_infos.items()
            if (
                int(info.get("pending") or 0) > 0
                or info.get("has_snapshot")
                or info.get("has_activity")
            )
        ]

        if normalized == "all":
            target_sids = record_sids
        elif normalized:
            target_sid, err = self._resolve_target_verbose(normalized)
            if err:
                yield event.plain_result(err)
                return
            target_sids = [target_sid] if target_sid else []
        else:
            visible = self._visible_sids(event)
            target_sids = [sid for sid in record_sids if sid in visible]

        if not target_sids:
            yield event.plain_result("无记录")
            return

        results: dict[str, dict] = {}
        for sid in target_sids:
            results[sid] = await summary_svc.push_for_command(sid)

        pushed = [sid for sid, r in results.items() if r.get("pushed")]
        failed = [sid for sid, r in results.items() if r.get("reason") == "push_failed"]
        empty = [
            sid for sid, r in results.items()
            if r.get("reason") in ("no_record", "no_pending", "no_change")
        ]

        lines = []
        if pushed:
            lines.append(f"✓ 已发送 {len(pushed)} 个 session")
        if failed:
            lines.append("⚠ 失败: " + ", ".join(s[:8] for s in failed))
        if empty and not pushed:
            lines.append("无记录")
        yield event.plain_result("\n".join(lines) or "无记录")

    # ── git 查看（只读） ──

    @staticmethod
    def _parse_git_staged(arg: str) -> tuple[bool | None, str]:
        """解析 diffstat / diff 参数尾部的 staged 关键词。

        返回 (staged 三态, 剩余参数)。合法词：staged/暂存=true，unstaged/未暂存=false。
        """
        parts = (arg or "").strip().split(None, 1)
        if not parts:
            return None, ""
        first = parts[0].strip().lower()
        if first in ("staged", "暂存"):
            return True, (parts[1] if len(parts) > 1 else "").strip()
        if first in ("unstaged", "未暂存"):
            return False, (parts[1] if len(parts) > 1 else "").strip()
        return None, (arg or "").strip()

    def _require_sid_or_arg(self, event: AstrMessageEvent, cmd: str, arg: str = "") -> tuple[str | None, str | None]:
        """git 系列：当前选中 session 优先；参数为数字/ID 前缀时指向该 session。"""
        if arg:
            target_sid, err = self._resolve_target_verbose(arg)
            if err:
                return None, err
            if target_sid:
                return target_sid, None
        return self._require_sid(event, cmd)

    async def cmd_git(self, event: AstrMessageEvent):
        """查看当前 session 工作区 git 状态（只读）"""
        await self.state_mgr.ensure_primary_session(event)
        await self.state_mgr.set_user_state(event)
        sid, err = self._require_sid(event, "git")
        if err:
            yield event.plain_result(err)
            return
        ok, stdout, _ = await session_ops.fetch_git_status(self.client, sid)
        if not ok:
            yield event.plain_result(stdout)
            return
        label = formatters.session_label_short(sid, self.sessions_cache)
        text = formatters.format_git_status(label, stdout)
        from ..render import output_present
        payload = output_present.build_git_status_payload(label, stdout)
        async for result in output_present.present(
            self.plugin, event, "git_status", payload, text
        ):
            yield result

    async def cmd_diffstat(self, event: AstrMessageEvent, arg: str = ""):
        """查看当前 session 变更统计（--numstat；可跟 staged/unstaged）"""
        await self.state_mgr.ensure_primary_session(event)
        await self.state_mgr.set_user_state(event)
        staged, rest = self._parse_git_staged(arg)
        sid, err = self._require_sid_or_arg(event, "diffstat", rest)
        if err:
            yield event.plain_result(err)
            return
        ok, stdout, _ = await session_ops.fetch_git_diff_numstat(self.client, sid, staged=staged)
        if not ok:
            yield event.plain_result(stdout)
            return
        label = formatters.session_label_short(sid, self.sessions_cache)
        text = formatters.format_git_diff_numstat(label, stdout)
        from ..render import output_present
        payload = output_present.build_git_status_payload(label, stdout, is_numstat=True)
        async for result in output_present.present(
            self.plugin, event, "git_status", payload, text
        ):
            yield result

    async def cmd_diff(self, event: AstrMessageEvent, arg: str = ""):
        """查看单文件 diff：/hapi diff <路径> [staged|unstaged] [序号|ID前缀]"""
        await self.state_mgr.ensure_primary_session(event)
        await self.state_mgr.set_user_state(event)
        staged, rest = self._parse_git_staged(arg)
        if not rest:
            yield event.plain_result(
                "格式: /hapi diff <文件路径> [staged|unstaged]\n"
                "示例: /hapi diff src/main.py\n"
                "先看 /hapi diffstat 确认文件路径"
            )
            return
        sid, err = self._require_sid(event, "diff")
        if err:
            yield event.plain_result(err)
            return
        ok, stdout, _ = await session_ops.fetch_git_diff_file(self.client, sid, rest, staged=staged)
        if not ok:
            yield event.plain_result(stdout)
            return
        label = formatters.session_label_short(sid, self.sessions_cache)
        scope = "（暂存）" if staged is True else ("（未暂存）" if staged is False else "")
        title = f"{label}\ndiff {scope} {rest}".strip()
        if not stdout.strip():
            yield event.plain_result(f"{title}\n（无差异）")
            return
        body = f"```diff\n{stdout.rstrip()}\n```"
        from ..render import output_present
        payload = output_present.build_message_payload(
            label=label,
            body=body,
            title=f"diff {rest}",
            footer="",
        )
        async for result in output_present.present(
            self.plugin, event, "message", payload, f"{title}\n\n{body}"
        ):
            yield result

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

        yield event.plain_result("✓ 已重置所有状态\n捕获关系、窗口状态（含 Focus 模式）已清空，默认窗口和 flavor 默认路由已保留")
