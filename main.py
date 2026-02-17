"""HAPI Connector AstrBot 插件入口
注册指令组、快捷前缀、SSE 生命周期管理
所有指令仅管理员可用
"""

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig, logger
from astrbot.api.message_components import Poke

from astrbot.core.utils.session_waiter import session_waiter, SessionController

from .hapi_client import AsyncHapiClient
from .sse_listener import SSEListener
from .constants import PERMISSION_MODES, MODEL_MODES, AGENTS
from . import session_ops
from . import formatters


@register("astrbot_plugin_hapi_connector", "LiJinHao999",
          "连接 HAPI，随时随地用 Claude Code / Codex / Gemini / OpenCode vibe coding",
          "1.2.0")
class HapiConnectorPlugin(Star):

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # HAPI 客户端
        endpoint = self.config.get("hapi_endpoint", "")
        token = self.config.get("access_token", "")
        proxy = self.config.get("proxy_url", "") or None
        jwt_life = self.config.get("jwt_lifetime", 900)
        refresh_before = self.config.get("refresh_before_expiry", 180)

        self.client = AsyncHapiClient(
            endpoint=endpoint,
            access_token=token,
            proxy_url=proxy,
            jwt_lifetime=jwt_life,
            refresh_before=refresh_before,
        )

        # session 缓存
        self.sessions_cache: list[dict] = []

        # SSE 监听器
        self.sse_listener = SSEListener(self.client, self.sessions_cache, self._push_notification)

        # 用户状态缓存: {sender_id: {"current_session": ..., "current_flavor": ..., "notify_umo": ...}}
        self._user_states_cache: dict[str, dict] = {}

        # 快捷前缀
        self._quick_prefix = self.config.get("quick_prefix", ">")

        # 戳一戳审批开关
        self._poke_approve = self.config.get("poke_approve", False)

        # simple 模式消息条数
        self._simple_msg_count = self.config.get("simple_msg_count", 5)

        # 管理员列表（用于 catch-all 处理器手动鉴权）
        astrbot_config = self.context.get_config()
        self._admin_ids = [str(x) for x in astrbot_config.get("admins_id", [])]

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """检查发送者是否为管理员"""
        return str(event.get_sender_id()) in self._admin_ids

    # ──── 生命周期 ────

    async def initialize(self):
        """插件初始化：打开 client、加载用户状态、启动 SSE"""
        await self.client.init()

        # 从 KV 加载已知用户列表
        known_users = await self.get_kv_data("known_users", [])
        for uid in known_users:
            state = await self.get_kv_data(f"user_state_{uid}", None)
            if state:
                self._user_states_cache[uid] = state

        # 加载 session 缓存
        try:
            self.sessions_cache[:] = await session_ops.fetch_sessions(self.client)
        except Exception as e:
            logger.warning("初始化加载 session 列表失败: %s", e)

        # 加载已有的待审批请求（重启/断联后恢复）
        await self.sse_listener.load_existing_pending()

        # 启动 SSE
        output_level = self.config.get("output_level", "detail")
        self.sse_listener.start(output_level)
        logger.info("HAPI Connector 已初始化，SSE 输出级别: %s", output_level)

    async def terminate(self):
        """插件销毁：停止 SSE、关闭 client"""
        await self.sse_listener.stop()
        await self.client.close()
        logger.info("HAPI Connector 已销毁")

    # ──── 用户状态辅助 ────

    def _get_user_state(self, event: AstrMessageEvent) -> dict:
        sender_id = event.get_sender_id()
        return self._user_states_cache.get(sender_id, {})

    async def _set_user_state(self, event: AstrMessageEvent, **kwargs):
        sender_id = event.get_sender_id()
        state = self._user_states_cache.get(sender_id, {})
        # 脏检查：无 kwargs 且 notify_umo 未变时跳过写入
        new_umo = event.unified_msg_origin
        if not kwargs and state.get("notify_umo") == new_umo:
            return
        state.update(kwargs)
        # 始终更新 notify_umo
        state["notify_umo"] = new_umo
        self._user_states_cache[sender_id] = state

        # 持久化
        await self.put_kv_data(f"user_state_{sender_id}", state)
        # 维护 known_users 列表
        known = await self.get_kv_data("known_users", [])
        if sender_id not in known:
            known.append(sender_id)
            await self.put_kv_data("known_users", known)

    def _current_sid(self, event: AstrMessageEvent) -> str | None:
        return self._get_user_state(event).get("current_session")

    def _current_flavor(self, event: AstrMessageEvent) -> str | None:
        return self._get_user_state(event).get("current_flavor")

    async def _refresh_sessions(self):
        """刷新 session 缓存"""
        try:
            self.sessions_cache[:] = await session_ops.fetch_sessions(self.client)
        except Exception as e:
            logger.warning("刷新 session 列表失败: %s", e)

    async def _push_notification(self, text: str, session_id: str):
        """向所有已注册的管理员推送消息（供 SSEListener 回调），超过 4200 字自动分片"""
        chunks = self._split_message(text) if len(text) > 4200 else [text]
        for sender_id, state in self._user_states_cache.items():
            umo = state.get("notify_umo")
            if umo:
                for chunk in chunks:
                    try:
                        chain = MessageChain().message(chunk)
                        await self.context.send_message(umo, chain)
                    except Exception as e:
                        logger.warning("推送消息失败 (user=%s): %s", sender_id, e)
                        break

    @staticmethod
    def _split_message(text: str, max_len: int = 4200) -> list[str]:
        """按行边界将长消息分片"""
        chunks = []
        current = ""
        for line in text.split("\n"):
            if current and len(current) + 1 + len(line) > max_len:
                chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)
        return chunks

    # ──── 审批快捷操作（内部复用） ────

    def _flatten_pending(self) -> list[tuple[str, str, dict]]:
        """将 pending 请求扁平化为 [(sid, rid, req), ...]"""
        items = []
        for sid, reqs in self.sse_listener.get_all_pending().items():
            for rid, req in reqs.items():
                items.append((sid, rid, req))
        return items

    async def _approve_all_pending(self) -> str | None:
        """批准所有待审批请求，返回结果文本。无待审批时返回 None。"""
        items = self._flatten_pending()
        if not items:
            return None

        results = []
        for sid, rid, req in items:
            ok, msg = await session_ops.approve_permission(self.client, sid, rid)
            tool = req.get("tool", "?")
            results.append(f"{'✓' if ok else '✗'} {tool}")

        return f"已全部批准 ({len(items)} 个):\n" + "\n".join(results)

    # ──── 指令组 ────

    @filter.command_group("hapi")
    def hapi(self):
        """HAPI 远程 AI 编码会话管理 (仅管理员)"""
        pass

    # ── help ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("help")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        await self._set_user_state(event)
        yield event.plain_result(formatters.get_help_text())

    # ── list ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("list", alias={"ls"})
    async def cmd_list(self, event: AstrMessageEvent):
        """列出所有 session"""
        await self._set_user_state(event)
        await self._refresh_sessions()
        current_sid = self._current_sid(event)
        text = formatters.format_session_list(self.sessions_cache, current_sid)
        yield event.plain_result(text)

    # ── sw ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("sw")
    async def cmd_sw(self, event: AstrMessageEvent, target: str = ""):
        """切换当前 session: /hapi sw <序号或ID前缀>"""
        if not target:
            await self._refresh_sessions()
            current_sid = self._current_sid(event)
            text = formatters.format_session_list(self.sessions_cache, current_sid)
            yield event.plain_result(text + "\n\n请使用 /hapi sw <序号或ID前缀> 切换")
            return

        await self._refresh_sessions()

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
        await self._set_user_state(event, current_session=sid, current_flavor=flavor)
        summary = chosen.get("metadata", {}).get("summary", {}).get("text", "(无标题)")
        yield event.plain_result(f"已切换到 [{flavor}] {sid[:8]}... {summary}")

    # ── s (status) ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("s", alias={"status"})
    async def cmd_status(self, event: AstrMessageEvent):
        """查看当前 session 状态"""
        await self._set_user_state(event)
        sid = self._current_sid(event)
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

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("msg", alias={"messages"})
    async def cmd_msg(self, event: AstrMessageEvent, rounds: int = 1):
        """查看最近消息（按轮次）: /hapi msg [轮数]"""
        await self._set_user_state(event)
        sid = self._current_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return
        if rounds < 1:
            rounds = 1
        try:
            # 多取消息以保证覆盖 N 轮（每轮约含多条原始消息）
            fetch_limit = min(rounds * 80, 500)
            messages = await session_ops.fetch_messages(self.client, sid, limit=fetch_limit)
            all_rounds = formatters.split_into_rounds(messages)
            # 取最后 N 轮
            selected = all_rounds[-rounds:]
            if not selected:
                yield event.plain_result("(暂无消息)")
                return
            total = len(selected)
            for i, round_msgs in enumerate(selected, 1):
                text = formatters.format_round(round_msgs, i, total)
                yield event.plain_result(text)
        except Exception as e:
            yield event.plain_result(f"获取消息失败: {e}")

    # ── to ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("to")
    async def cmd_to(self, event: AstrMessageEvent):
        """发消息到指定 session: /hapi to <序号> <内容>"""
        raw = event.message_str.strip()
        parts = raw.split(None, 1)
        if len(parts) < 2 or not parts[0].isdigit():
            yield event.plain_result("格式: /hapi to <序号> <内容>")
            return

        idx = int(parts[0])
        text = parts[1]

        await self._refresh_sessions()
        if idx < 1 or idx > len(self.sessions_cache):
            yield event.plain_result(f"无效序号，共 {len(self.sessions_cache)} 个 session")
            return

        target = self.sessions_cache[idx - 1]
        ok, msg = await session_ops.send_message(self.client, target["id"], text)
        await self._set_user_state(event)
        yield event.plain_result(msg)

    # ── perm ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("perm")
    async def cmd_perm(self, event: AstrMessageEvent, mode: str = ""):
        """查看/切换权限模式: /hapi perm [模式名]"""
        await self._set_user_state(event)
        sid = self._current_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return

        flavor = self._current_flavor(event) or "claude"
        modes = PERMISSION_MODES.get(flavor, ["default"])

        if mode:
            target = mode
            if mode.isdigit() and 1 <= int(mode) <= len(modes):
                target = modes[int(mode) - 1]
            if target not in modes:
                yield event.plain_result(f"无效模式，可用: {', '.join(modes)}")
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

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("model")
    async def cmd_model(self, event: AstrMessageEvent, mode: str = ""):
        """查看/切换模型: /hapi model [模式名]"""
        await self._set_user_state(event)
        sid = self._current_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return

        flavor = self._current_flavor(event) or "claude"
        if flavor != "claude":
            yield event.plain_result("模型切换仅支持 Claude session")
            return

        if mode:
            target = mode
            if mode.isdigit() and 1 <= int(mode) <= len(MODEL_MODES):
                target = MODEL_MODES[int(mode) - 1]
            if target not in MODEL_MODES:
                yield event.plain_result(f"无效模式，可用: {', '.join(MODEL_MODES)}")
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

    # ── output ──

    _OUTPUT_LEVELS = {
        "silence": "仅推送权限请求和任务完成提醒",
        "simple": "AI 思考完成后推送最近 agent 文本消息",
        "detail": "实时推送所有新消息（信息量较大）",
    }

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("output", alias={"out"})
    async def cmd_output(self, event: AstrMessageEvent, level: str = ""):
        """查看/切换 SSE 推送级别: /hapi output [级别]"""
        await self._set_user_state(event)
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
                t = reply
                if reply.isdigit() and 1 <= int(reply) <= len(levels):
                    t = levels[int(reply) - 1]
                if t not in self._OUTPUT_LEVELS:
                    await ev.send(ev.plain_result(f"无效级别，可用: {', '.join(levels)}"))
                else:
                    self.sse_listener.output_level = t
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
            lines = ["无效级别，可用:"]
            for i, (lvl, desc) in enumerate(self._OUTPUT_LEVELS.items(), 1):
                lines.append(f"  [{i}] {lvl} — {desc}")
            yield event.plain_result("\n".join(lines))
            return

        self.sse_listener.output_level = target
        yield event.plain_result(
            f"SSE 推送级别已切换为: {target}\n{self._OUTPUT_LEVELS[target]}")

    # ── pending (查看待审批列表) ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("pending")
    async def cmd_pending(self, event: AstrMessageEvent):
        """查看待审批请求列表: /hapi pending"""
        await self._set_user_state(event)
        all_pending = self.sse_listener.get_all_pending()
        text = formatters.format_pending_requests(all_pending, self.sessions_cache)
        yield event.plain_result(text)

    # ── approve ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("approve", alias={"a"})
    async def cmd_approve(self, event: AstrMessageEvent):
        """批准审批请求: /hapi a 全部批准, /hapi a <序号> 批准单个"""
        await self._set_user_state(event)
        items = self._flatten_pending()
        if not items:
            yield event.plain_result("没有待审批的请求")
            return

        raw = event.message_str.strip()

        if raw and raw.isdigit():
            # 批准单个
            n = int(raw)
            if n < 1 or n > len(items):
                yield event.plain_result(f"无效序号，当前共 {len(items)} 个待审批")
                return
            sid, rid, req = items[n - 1]
            ok, msg = await session_ops.approve_permission(self.client, sid, rid)
            tool = req.get("tool", "?")
            yield event.plain_result(f"{'✓' if ok else '✗'} 已批准: {tool}")
        else:
            # 全部批准
            results = []
            for sid, rid, req in items:
                ok, msg = await session_ops.approve_permission(self.client, sid, rid)
                tool = req.get("tool", "?")
                results.append(f"{'✓' if ok else '✗'} {tool}")
            yield event.plain_result(f"已全部批准 ({len(items)} 个):\n" + "\n".join(results))

    # ── deny ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("deny")
    async def cmd_deny(self, event: AstrMessageEvent):
        """拒绝审批请求: /hapi deny 全部拒绝, /hapi deny <序号> 拒绝单个"""
        await self._set_user_state(event)
        items = self._flatten_pending()
        if not items:
            yield event.plain_result("没有待审批的请求")
            return

        raw = event.message_str.strip()

        if raw and raw.isdigit():
            # 拒绝单个
            n = int(raw)
            if n < 1 or n > len(items):
                yield event.plain_result(f"无效序号，当前共 {len(items)} 个待审批")
                return
            sid, rid, req = items[n - 1]
            ok, msg = await session_ops.deny_permission(self.client, sid, rid)
            tool = req.get("tool", "?")
            yield event.plain_result(f"{'✓' if ok else '✗'} 已拒绝: {tool}")
        else:
            # 全部拒绝
            results = []
            for sid, rid, req in items:
                ok, msg = await session_ops.deny_permission(self.client, sid, rid)
                tool = req.get("tool", "?")
                results.append(f"{'✓' if ok else '✗'} {tool}")
            yield event.plain_result(f"已全部拒绝 ({len(items)} 个):\n" + "\n".join(results))

    # ── create ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("create")
    async def cmd_create(self, event: AstrMessageEvent):
        """创建新 session (5 步向导)"""
        await self._set_user_state(event)
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

        wizard = {
            "step": 1,
            "machines": machines,
            "labels": labels,
            "machine_id": None,
            "machine_label": None,
            "directory": None,
            "session_type": "simple",
            "worktree_name": "",
            "agent": None,
            "yolo": False,
            "recent_paths": [],
        }

        if len(machines) == 1:
            wizard["machine_id"] = machines[0]["id"]
            wizard["machine_label"] = labels[0]
            wizard["step"] = 2

        if wizard["step"] == 1:
            lines = ["步骤 1/5 — 选择机器:"]
            for i, label in enumerate(labels, 1):
                lines.append(f"  [{i}] {label}")
            lines.append("\n回复序号选择")
            yield event.plain_result("\n".join(lines))
        else:
            try:
                wizard["recent_paths"] = await session_ops.fetch_recent_paths(self.client)
            except Exception:
                pass

            lines = [f"自动选择机器: {wizard['machine_label']}", "", "步骤 2/5 — 工作目录:"]
            if wizard["recent_paths"]:
                lines.append("最近使用的目录:")
                for i, p in enumerate(wizard["recent_paths"], 1):
                    lines.append(f"  [{i}] {p}")
                lines.append("回复序号选择，或直接输入新路径")
            else:
                lines.append("请输入完整路径")
            yield event.plain_result("\n".join(lines))

        @session_waiter(timeout=120, record_history_chains=False)
        async def create_waiter(controller: SessionController, ev: AstrMessageEvent):
            raw = ev.message_str.strip()
            step = wizard["step"]

            if step == 1:
                if not raw.isdigit() or not (1 <= int(raw) <= len(wizard["machines"])):
                    await ev.send(ev.plain_result(f"请输入 1~{len(wizard['machines'])} 的数字"))
                    controller.keep(timeout=120, reset_timeout=True)
                    return

                idx = int(raw) - 1
                wizard["machine_id"] = wizard["machines"][idx]["id"]
                wizard["machine_label"] = wizard["labels"][idx]
                wizard["step"] = 2

                try:
                    wizard["recent_paths"] = await session_ops.fetch_recent_paths(self.client)
                except Exception:
                    pass

                lines = [f"已选机器: {wizard['machine_label']}", "", "步骤 2/5 — 工作目录:"]
                if wizard["recent_paths"]:
                    lines.append("最近使用的目录:")
                    for i, p in enumerate(wizard["recent_paths"], 1):
                        lines.append(f"  [{i}] {p}")
                    lines.append("回复序号选择，或直接输入新路径")
                else:
                    lines.append("请输入完整路径")
                await ev.send(ev.plain_result("\n".join(lines)))
                controller.keep(timeout=120, reset_timeout=True)

            elif step == 2:
                recent = wizard["recent_paths"]
                if raw.isdigit() and recent and 1 <= int(raw) <= len(recent):
                    wizard["directory"] = recent[int(raw) - 1]
                elif raw:
                    wizard["directory"] = raw
                else:
                    await ev.send(ev.plain_result("目录不能为空，请重新输入"))
                    controller.keep(timeout=120, reset_timeout=True)
                    return

                wizard["step"] = 3
                lines = [
                    f"目录: {wizard['directory']}",
                    "",
                    "步骤 3/5 — 会话类型:",
                    "  [1] simple  — 直接使用选定目录",
                    "  [2] worktree — 在仓库旁创建新工作树",
                ]
                await ev.send(ev.plain_result("\n".join(lines)))
                controller.keep(timeout=120, reset_timeout=True)

            elif step == 3:
                if raw == "1":
                    wizard["session_type"] = "simple"
                elif raw == "2":
                    wizard["session_type"] = "worktree"
                else:
                    await ev.send(ev.plain_result("请输入 1 或 2"))
                    controller.keep(timeout=120, reset_timeout=True)
                    return

                if wizard["session_type"] == "worktree":
                    wizard["step"] = 31
                    await ev.send(ev.plain_result("工作树名称 (回复任意名称，或输入 - 自动生成):"))
                    controller.keep(timeout=120, reset_timeout=True)
                else:
                    wizard["step"] = 4
                    lines = [
                        f"类型: {wizard['session_type']}",
                        "",
                        "步骤 4/5 — 选择 Vibe Coding 代理:",
                    ]
                    for i, a in enumerate(AGENTS, 1):
                        lines.append(f"  [{i}] {a}")
                    await ev.send(ev.plain_result("\n".join(lines)))
                    controller.keep(timeout=120, reset_timeout=True)

            elif step == 31:
                if raw != "-":
                    wizard["worktree_name"] = raw
                wizard["step"] = 4
                lines = [
                    f"类型: {wizard['session_type']}"
                    + (f" (工作树: {wizard['worktree_name']})" if wizard["worktree_name"] else ""),
                    "",
                    "步骤 4/5 — 选择 Vibe Coding 代理:",
                ]
                for i, a in enumerate(AGENTS, 1):
                    lines.append(f"  [{i}] {a}")
                await ev.send(ev.plain_result("\n".join(lines)))
                controller.keep(timeout=120, reset_timeout=True)

            elif step == 4:
                if raw.isdigit() and 1 <= int(raw) <= len(AGENTS):
                    wizard["agent"] = AGENTS[int(raw) - 1]
                elif raw in AGENTS:
                    wizard["agent"] = raw
                else:
                    await ev.send(ev.plain_result(f"请输入 1~{len(AGENTS)} 的数字或代理名"))
                    controller.keep(timeout=120, reset_timeout=True)
                    return

                wizard["step"] = 5
                lines = [
                    f"代理: {wizard['agent']}",
                    "",
                    "步骤 5/5 — 启用 YOLO 模式?",
                    "  [1] 否 — 正常审批流程",
                    "  [2] 是 — 跳过审批和沙箱 (危险)",
                ]
                await ev.send(ev.plain_result("\n".join(lines)))
                controller.keep(timeout=120, reset_timeout=True)

            elif step == 5:
                if raw == "1":
                    wizard["yolo"] = False
                elif raw == "2":
                    wizard["yolo"] = True
                else:
                    await ev.send(ev.plain_result("请输入 1 或 2"))
                    controller.keep(timeout=120, reset_timeout=True)
                    return

                wizard["step"] = 6
                lines = [
                    "即将创建 Session:",
                    f"  机器:     {wizard['machine_label']}",
                    f"  目录:     {wizard['directory']}",
                    f"  类型:     {wizard['session_type']}",
                    f"  代理:     {wizard['agent']}",
                    f"  YOLO:     {'是' if wizard['yolo'] else '否'}",
                ]
                if wizard["worktree_name"]:
                    lines.append(f"  工作树名: {wizard['worktree_name']}")
                if wizard["agent"] == "codex" and wizard["yolo"]:
                    lines.append(f"\n⚠ 提醒: Codex YOLO 模式需要在配置中设置信任等级，否则无法使用 tools:")
                    lines.append(f'  [projects."{wizard["directory"]}"]')
                    lines.append(f'  trust_level = "trusted"')
                if wizard["agent"] == "codex":
                    lines.append(
                        "\n⚠ 已知问题: 远程创建的 Codex 可能因缺少 TTY 而无法调用工具。"
                        "\n如遇此问题，建议手动操作:"
                        "\n  1. SSH 到目标机器，启动 screen: screen -S codex"
                        "\n  2. cd 到工作目录，运行 codex"
                        "\n  3. Ctrl+A Ctrl+D 挂到后台"
                        "\n效果与此处创建相同，且拥有完整 TTY 环境。"
                    )
                lines.append("\n回复 y 确认创建，其他取消")
                await ev.send(ev.plain_result("\n".join(lines)))
                controller.keep(timeout=60, reset_timeout=True)

            elif step == 6:
                if raw.lower() != "y":
                    await ev.send(ev.plain_result("已取消"))
                    controller.stop()
                    return

                await ev.send(ev.plain_result("正在创建 ..."))

                ok, msg, new_sid = await session_ops.spawn_session(
                    self.client,
                    machine_id=wizard["machine_id"],
                    directory=wizard["directory"],
                    agent=wizard["agent"],
                    session_type=wizard["session_type"],
                    yolo=wizard["yolo"],
                    worktree_name=wizard["worktree_name"],
                )
                await self._refresh_sessions()
                if ok and new_sid:
                    flavor = wizard["agent"]
                    await self._set_user_state(ev, current_session=new_sid, current_flavor=flavor)
                    msg += f"\n已自动切换到该 session [{flavor}] {new_sid[:8]}..."
                await ev.send(ev.plain_result(msg))
                controller.stop()

        try:
            await create_waiter(event)
        except TimeoutError:
            yield event.plain_result("创建向导超时，已取消")
        finally:
            event.stop_event()

    # ── abort ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("abort", alias={"stop"})
    async def cmd_abort(self, event: AstrMessageEvent, target: str = ""):
        """中断 session: /hapi abort [序号|ID前缀]"""
        await self._set_user_state(event)
        await self._refresh_sessions()

        if not target:
            sid = self._current_sid(event)
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
            await self._refresh_sessions()
        yield event.plain_result(msg)

    # ── archive ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("archive")
    async def cmd_archive(self, event: AstrMessageEvent):
        """归档当前 session"""
        await self._set_user_state(event)
        sid = self._current_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return

        yield event.plain_result(f"确认归档 session [{sid[:8]}]?\n回复 y 确认")

        @session_waiter(timeout=30, record_history_chains=False)
        async def archive_waiter(controller: SessionController, ev: AstrMessageEvent):
            if ev.message_str.strip().lower() == "y":
                ok, msg = await session_ops.archive_session(self.client, sid)
                await ev.send(ev.plain_result(msg))
                if ok:
                    await self._refresh_sessions()
            else:
                await ev.send(ev.plain_result("已取消"))
            controller.stop()

        try:
            await archive_waiter(event)
        except TimeoutError:
            yield event.plain_result("操作超时，已取消")
        finally:
            event.stop_event()

    # ── rename ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("rename")
    async def cmd_rename(self, event: AstrMessageEvent):
        """重命名当前 session"""
        await self._set_user_state(event)
        sid = self._current_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return

        yield event.plain_result(f"请输入 session [{sid[:8]}] 的新名称:")

        @session_waiter(timeout=60, record_history_chains=False)
        async def rename_waiter(controller: SessionController, ev: AstrMessageEvent):
            new_name = ev.message_str.strip()
            if not new_name:
                await ev.send(ev.plain_result("名称不能为空，已取消"))
            else:
                ok, msg = await session_ops.rename_session(self.client, sid, new_name)
                await ev.send(ev.plain_result(msg))
                if ok:
                    await self._refresh_sessions()
            controller.stop()

        try:
            await rename_waiter(event)
        except TimeoutError:
            yield event.plain_result("操作超时，已取消")
        finally:
            event.stop_event()

    # ── delete ──

    @filter.permission_type(filter.PermissionType.ADMIN)
    @hapi.command("delete")
    async def cmd_delete(self, event: AstrMessageEvent):
        """删除当前 session"""
        sid = self._current_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
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
            if ev.message_str.strip() == "delete":
                if is_active:
                    ok_arc, msg_arc = await session_ops.archive_session(self.client, sid)
                    if not ok_arc:
                        await ev.send(ev.plain_result(f"归档失败，删除中止: {msg_arc}"))
                        controller.stop()
                        return
                ok, msg = await session_ops.delete_session(self.client, sid)
                await ev.send(ev.plain_result(msg))
                if ok:
                    await self._set_user_state(ev, current_session=None, current_flavor=None)
                    await self._refresh_sessions()
            else:
                await ev.send(ev.plain_result("已取消"))
            controller.stop()

        try:
            await delete_waiter(event)
        except TimeoutError:
            yield event.plain_result("操作超时，已取消")
        finally:
            event.stop_event()

    # ──── 戳一戳全部审批 (仅 QQ NapCat) ────

    @filter.event_message_type(filter.EventMessageType.ALL, priority=20)
    async def poke_approve_handler(self, event: AstrMessageEvent):
        """戳一戳机器人 → 自动批准所有待审批请求 (仅 QQ NapCat)"""
        if not self._poke_approve:
            return

        if not self._is_poke_event(event):
            return

        if not self._is_admin(event):
            return

        await self._set_user_state(event)
        result = await self._approve_all_pending()
        if result is None:
            return  # 无待审批，静默
        yield event.plain_result(f"[戳一戳审批] {result}")
        event.stop_event()

    def _is_poke_event(self, event: AstrMessageEvent) -> bool:
        """检测是否为戳一戳机器人事件"""
        try:
            for comp in event.message_obj.message:
                if isinstance(comp, Poke):
                    bot_id = event.message_obj.raw_message.get('self_id')
                    if comp.qq == bot_id:
                        return True
            return False
        except Exception:
            return False

    # ──── 快捷前缀处理器 ────

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def quick_prefix_handler(self, event: AstrMessageEvent):
        """快捷前缀: > 消息 或 >N 消息 (仅管理员)"""
        prefix = self._quick_prefix
        raw = event.message_str

        if not raw or not raw.startswith(prefix):
            return  # 不匹配，不拦截

        if not self._is_admin(event):
            return  # 非管理员，静默忽略

        rest = raw[len(prefix):]

        if not rest:
            return  # 只有前缀，忽略

        target_sid = None
        text = None

        parts = rest.split(None, 1)
        if parts[0].isdigit():
            idx = int(parts[0])
            if len(parts) < 2:
                return  # >N 但没有消息内容
            text = parts[1]

            await self._refresh_sessions()
            if 1 <= idx <= len(self.sessions_cache):
                target_sid = self.sessions_cache[idx - 1]["id"]
            else:
                yield event.plain_result(f"无效序号 {idx}，共 {len(self.sessions_cache)} 个 session")
                event.stop_event()
                return
        else:
            text = rest.lstrip()
            if not text:
                return
            target_sid = self._current_sid(event)

        if not target_sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            event.stop_event()
            return

        ok, msg = await session_ops.send_message(self.client, target_sid, text)
        await self._set_user_state(event)
        yield event.plain_result(msg)
        event.stop_event()
