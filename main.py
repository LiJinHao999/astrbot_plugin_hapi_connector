"""HAPI Connector AstrBot 插件入口
注册指令组、快捷前缀、SSE 生命周期管理
所有指令仅管理员可用
"""

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig, logger
from astrbot.api.message_components import Poke
import astrbot.api.message_components as Comp

from .hapi_client import AsyncHapiClient
from .cf_access import CfAccessManager
from .sse_listener import SSEListener
from .binding_manager import BindingManager
from .state_manager import StateManager
from .notification_manager import NotificationManager
from .pending_manager import PendingManager
from .command_handlers import CommandHandlers
from . import session_ops
from . import formatters


# ── AstrBot v4.18.3 pydantic v1 的 __setattr__ 会拦截 File 的 property setter，
# ── 导致设置 file 属性时写入错误字段,文件传输会直接报错。此处的补丁在 bug 存在时自动生效，官方修复后自动跳过。
try:
    _test_file = Comp.File(name="test", url="test")
    _test_file.file = "test"
except Exception:
    _original_file_setattr = Comp.File.__setattr__
    def _patched_file_setattr(self, name, value):
        if name == "file":
            _original_file_setattr(self, "file_", value)
        else:
            _original_file_setattr(self, name, value)
    Comp.File.__setattr__ = _patched_file_setattr


@register("astrbot_plugin_hapi_connector", "LiJinHao999",
          "连接 HAPI，随时随地用 Claude Code / Codex / Gemini / OpenCode vibe coding",
          "2.0.0_llmtest")
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

        # Cloudflare Zero Trust Access（可选，仅在填写了 client_id 时生效）
        cf_id = self.config.get("cf_access_client_id", "").strip()
        cf_secret = self.config.get("cf_access_client_secret", "").strip()
        if cf_id.lower().startswith("cf-access-client-id:"):
            cf_id = cf_id.split(":", 1)[1].strip()
        if cf_secret.lower().startswith("cf-access-client-secret:"):
            cf_secret = cf_secret.split(":", 1)[1].strip()
        cf_mgr = None
        if cf_id and cf_secret:
            cf_mgr = CfAccessManager(client_id=cf_id, client_secret=cf_secret)

        self.client = AsyncHapiClient(
            endpoint=endpoint,
            access_token=token,
            proxy_url=proxy,
            jwt_lifetime=jwt_life,
            refresh_before=refresh_before,
            cf_access_mgr=cf_mgr,
        )

        # session 缓存
        self.sessions_cache: list[dict] = []

        # 绑定管理器
        self.binding_mgr = BindingManager()

        # 状态管理器
        self.state_mgr = StateManager(self, self.binding_mgr)

        # 通知管理器
        self.notification_mgr = NotificationManager(self.context, self.state_mgr)

        # SSE 监听器
        self.sse_listener = SSEListener(
            self.client,
            self.sessions_cache,
            lambda text, sid: self.notification_mgr.push_notification(text, sid, self.sessions_cache)
        )

        # 待审批管理器
        self.pending_mgr = PendingManager(self.sse_listener)

        # 命令处理器
        self.cmd_handlers = CommandHandlers(self)

        # 快捷前缀
        self._quick_prefix = self.config.get("quick_prefix", ">")

        # 戳一戳审批开关
        self._poke_approve = self.config.get("poke_approve", True)

        # summary 模式消息条数
        self._summary_msg_count = self.config.get("summary_msg_count", 5)

        # event 缓存，用于主动推送
        self.notification_mgr._event_cache = {}

        # LLM 工具集成
        from .llm_integration import LLMIntegration
        self.llm_integration = LLMIntegration(self)

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """检查发送者是否为管理员（动态读取配置）"""
        astrbot_config = self.context.get_config()
        admin_ids = [str(x) for x in astrbot_config.get("admins_id", [])]
        return str(event.get_sender_id()) in admin_ids

    @filter.on_llm_request()
    async def on_llm_request_hook(self, event: AstrMessageEvent, request):
        """LLM 工具可见性控制钩子"""
        await self.llm_integration.on_llm_request_hook(event, request)

    # ──── LLM 工具代理方法 ────

    @filter.llm_tool(name="hapi_coding_get_status")
    async def tool_get_status(self, event: AstrMessageEvent):
        '''获取当前交互中的 HAPI session 的状态信息。'''
        async for result in self.llm_integration.tool_get_status(event):
            yield result

    @filter.llm_tool(name="hapi_coding_list_sessions")
    async def tool_list_sessions(self, event: AstrMessageEvent, window: str = "", path: str = "", agent: str = ""):
        '''列出 HAPI 的可交互 session 列表。

        Args:
            window(string): 窗口过滤，空=当前窗口，all=所有窗口
            path(string): 路径搜索关键词
            agent(string): 代理类型，claude/codex/gemini/opencode
        '''
        async for result in self.llm_integration.tool_list_sessions(event, window, path, agent):
            yield result

    @filter.llm_tool(name="hapi_coding_message_history")
    async def tool_message_history(self, event: AstrMessageEvent, rounds: int = 1):
        '''查询当前交互中的 session 的历史消息。

        Args:
            rounds(number): 查询最近几轮消息，默认1轮
        '''
        async for result in self.llm_integration.tool_message_history(event, rounds):
            yield result

    @filter.llm_tool(name="hapi_coding_get_config_status")
    async def tool_get_config_status(self, event: AstrMessageEvent):
        '''获取当前插件配置状态及可修改项说明。'''
        async for result in self.llm_integration.tool_get_config_status(event):
            yield result

    @filter.llm_tool(name="hapi_coding_list_commands")
    async def tool_list_commands(self, event: AstrMessageEvent, topic: str = ""):
        '''列出所有可用的 HAPI 指令。

        Args:
            topic(string): 帮助主题，不填默认显示常用帮助，常用帮助末尾会列出所有可用专题
        '''
        async for result in self.llm_integration.tool_list_commands(event, topic):
            yield result

    @filter.llm_tool(name="hapi_coding_send_message")
    async def tool_send_message(self, event: AstrMessageEvent, message: str):
        '''向当前 session 发送消息。

        Args:
            message(string): 要发送的消息内容
        '''
        async for result in self.llm_integration.tool_send_message(event, message):
            yield result

    @filter.llm_tool(name="hapi_coding_switch_session")
    async def tool_switch_session(self, event: AstrMessageEvent, target: str):
        '''切换到指定的 session。

        Args:
            target(string): session序号如1或session ID前缀如abc12345
        '''
        async for result in self.llm_integration.tool_switch_session(event, target):
            yield result

    @filter.llm_tool(name="hapi_coding_create_session")
    async def tool_create_session(self, event: AstrMessageEvent, directory: str, agent: str,
                                   machine_id: str = "", session_type: str = "simple", yolo: bool = False):
        '''创建新的 coding session。

        Args:
            directory(string): 工作目录路径
            agent(string): 代理类型，claude/codex/gemini/opencode
            machine_id(string): 机器ID，可选，管理多机器时必填
            session_type(string): session类型，simple或worktree，默认simple
            yolo(boolean): 是否自动批准所有权限，默认false
        '''
        async for result in self.llm_integration.tool_create_session(event, directory, agent, machine_id, session_type, yolo):
            yield result

    @filter.llm_tool(name="hapi_coding_change_config")
    async def tool_change_config(self, event: AstrMessageEvent, config_name: str, value: str):
        '''修改插件配置项。必须先调用hapi_coding_get_config_status查看可修改项。

        Args:
            config_name(string): 配置项名称
            value(string): 新值
        '''
        async for result in self.llm_integration.tool_change_config(event, config_name, value):
            yield result

    @filter.llm_tool(name="hapi_coding_stop_message")
    async def tool_stop_message(self, event: AstrMessageEvent):
        '''停止当前 session 的消息生成。'''
        async for result in self.llm_integration.tool_stop_message(event):
            yield result

    @filter.llm_tool(name="hapi_coding_execute_command")
    async def tool_execute_command(self, event: AstrMessageEvent, command: str):
        '''直接执行HAPI指令。使用前请务必调用hapi_coding_list_commands查看指令格式和参数说明。

        Args:
            command(string): 完整的/hapi指令，不含/hapi前缀
        '''
        async for result in self.llm_integration.tool_execute_command(event, command):
            yield result

    # ──── 辅助方法 ────

    def _conn_warning(self) -> str | None:
        """SSE 连接异常时返回警告文本，正常时返回 None"""
        was_hibernated = self.sse_listener._hibernated
        self.sse_listener.wake_up()
        if was_hibernated:
            return "💤 SSE 已从休眠中唤醒，正在后台重连...\n请等待连接恢复通知后，使用 /hapi list 查看连接状态\n"
        n = self.sse_listener.conn_fail_count
        if n > 0:
            return f"⚠ SSE 连接已连续失败 {n} 次，正在后台重连...\n"
        return None

    @staticmethod
    def _strip_hapi_prefix(text: str) -> str:
        """Strip a leading /hapi command prefix and return the remainder."""
        normalized = (text or "").strip()
        lowered = normalized.lower()
        if lowered == "/hapi":
            return ""
        if lowered.startswith("/hapi "):
            return normalized[6:].strip()
        if lowered == "hapi":
            return ""
        if lowered.startswith("hapi "):
            return normalized[5:].strip()
        return normalized

    def _extract_hapi_remainder(self, event: AstrMessageEvent, raw: str = "") -> str:
        """Choose the most complete /hapi remainder from raw and message text."""
        candidates: list[str] = []
        seen: set[str] = set()

        for source in ((raw or "").strip(), (event.message_str or "").strip()):
            remainder = self._strip_hapi_prefix(source)
            if remainder in seen:
                continue
            seen.add(remainder)
            candidates.append(remainder)

        if not candidates:
            return ""

        return max(candidates, key=lambda item: (len(item.split()), len(item)))

    async def _refresh_sessions(self):
        """刷新 session 缓存"""
        try:
            self.sessions_cache[:] = await session_ops.fetch_sessions(self.client)
        except Exception as e:
            logger.warning("刷新 session 列表失败: %s", e)

    async def _format_bind_status_text(self, event: AstrMessageEvent) -> str:
        """生成绑定状态总览；供 /hapi list all 和 /hapi bind status 复用。"""
        await self._refresh_sessions()
        text = formatters.format_bind_status(
            self.sessions_cache,
            self.state_mgr._session_owners,
            self.binding_mgr._window_states,
        )
        route_lines = self.state_mgr.user_route_summary_lines(event)
        if route_lines:
            text += "\n\n" + "\n".join(route_lines)
        return text

    @staticmethod
    def _missing_machine_hint_text() -> str:
        return (
            "⚠️ HAPI Connector 服务没有获取到远端 machine，但 SSE 连接正常。\n"
            "请检查：\n"
            "1. 您的 HAPI Hub / HAPI Runner 是否正常运行。若长期拿不到 machine，可在服务端终端执行 hapi daemon start，或重启全部 hapi 相关服务。\n"
            "2. 当前 token 是否设置了 namespace，且与用户目录下 .hapi 配置中的 namespace 保持一致。\n"
            "这通常不是插件本身的问题，更像是后端服务或 namespace 配置异常。"
        )

    async def _machine_status_hint(self) -> str | None:
        try:
            machines = await session_ops.fetch_machines(self.client)
        except Exception as e:
            logger.error(f"检查 machine 列表失败: {e}")
            return None

        if machines or self.sse_listener.conn_error is not None:
            return None
        return self._missing_machine_hint_text()

    def _format_no_visible_sessions_text(self, event: AstrMessageEvent) -> str:
        lines = [
            "当前窗口没有接收任何 session 通知。",
            "如果希望在此聊天窗口接收默认通知，可使用 /hapi bind。",
            "如需按模型隔离默认通知，可使用 /hapi bind claude|codex|gemini。",
            "也可以使用 /hapi list all 查看所有 session 和全局绑定状态。",
        ]

        route_lines = self.state_mgr.user_route_summary_lines(event)
        if route_lines:
            lines.extend(["", *route_lines])
        return "\n".join(lines)

    # ──── 生命周期 ────

    async def initialize(self):
        """插件初始化：打开 client、加载用户状态、启动 SSE"""
        await self.client.init()

        # 从 KV 加载状态
        await self.state_mgr.load_all()

        # 执行数据迁移
        await self.state_mgr.migrate_to_capture_model()

        # 加载 session 缓存
        try:
            self.sessions_cache[:] = await session_ops.fetch_sessions(self.client)
        except Exception as e:
            logger.warning("初始化加载 session 列表失败: %s", e)

        # 加载已有的待审批请求（重启/断联后恢复）
        await self.sse_listener.load_existing_pending()

        # 启动 SSE
        output_level = self.config.get("output_level", "simple")
        remind = self.config.get("remind_pending", True)
        remind_interval = self.config.get("remind_interval", 180)
        auto_approve = self.config.get("auto_approve_enabled", False)
        auto_approve_start = self.config.get("auto_approve_start", "23:00")
        auto_approve_end = self.config.get("auto_approve_end", "07:00")
        max_reconnect = self.config.get("max_reconnect_attempts", 30)
        self.sse_listener.start(
            output_level,
            remind_pending=remind,
            remind_interval=remind_interval,
            auto_approve_enabled=auto_approve,
            auto_approve_start=auto_approve_start,
            auto_approve_end=auto_approve_end,
            summary_msg_count=self._summary_msg_count,
            max_reconnect_attempts=max_reconnect,
        )
        logger.info("HAPI Connector 已初始化，SSE 输出级别: %s", output_level)

    async def terminate(self):
        """插件销毁：停止 SSE、关闭 client"""
        await self.sse_listener.stop()
        await self.client.close()
        logger.info("HAPI Connector 已销毁")

    # ──── 命令路由 ────

    @filter.command("hapi")
    async def handle_hapi(self, event: AstrMessageEvent, raw: str = ""):
        """处理 /hapi 命令"""
        if not self._is_admin(event):
            return
        async for result in self.cmd_handlers.cmd_hapi_router(event, raw):
            yield result

    # ──── 戳一戳处理器 ────

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def poke_approve_handler(self, event: AstrMessageEvent):
        """戳一戳机器人 → 自动批准所有待审批请求 (仅 QQ NapCat)"""
        if not self._poke_approve:
            return

        if not self._is_poke_event(event):
            return

        if not self._is_admin(event):
            return

        await self.state_mgr.set_user_state(event)
        visible_sids = {s.get("id") for s in self.state_mgr.visible_sessions_for_window(event, self.sessions_cache) if s.get("id")}
        items = self.pending_mgr.flatten_pending(event, visible_sids)
        if not items:
            return  # 无待审批，静默

        regular = [(sid, rid, req) for sid, rid, req in items
                   if not formatters.is_question_request(req)]
        questions = [(sid, rid, req) for sid, rid, req in items
                     if formatters.is_question_request(req)]

        if regular:
            result = await self.pending_mgr.approve_items(regular, self.client)
            if result:
                yield event.plain_result(f"[戳一戳审批] {result}")

        if questions:
            yield event.plain_result(f"[戳一戳审批] 还有 {len(questions)} 个问题需要回答:")
            from astrbot.core.utils.session_waiter import session_waiter, SessionController
            await self.pending_mgr.answer_questions_interactive(
                event, questions, self.client, session_waiter, SessionController)

        event.stop_event()

    def _is_poke_event(self, event: AstrMessageEvent) -> bool:
        """检测是否为戳一戳机器人事件"""
        try:
            self_id = str(event.get_self_id() or "").strip()
            raw_message = getattr(event.message_obj, "raw_message", {}) or {}
            if not self_id:
                self_id = str(raw_message.get("self_id", "")).strip()

            for comp in getattr(event.message_obj, "message", []) or []:
                if isinstance(comp, Poke):
                    candidates = []
                    target_id = comp.target_id() if hasattr(comp, "target_id") else None
                    for value in (target_id, getattr(comp, "id", None), getattr(comp, "qq", None)):
                        if value is None:
                            continue
                        text = str(value).strip()
                        if text:
                            candidates.append(text)
                    if self_id and self_id in candidates:
                        return True

            subtype = str(raw_message.get("sub_type", "")).lower()
            target_id = str(raw_message.get("target_id", "")).strip()
            return subtype == "poke" and bool(self_id) and target_id == self_id
        except Exception:
            return False

    # ──── 快捷前缀处理器 ────

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def quick_prefix_handler(self, event: AstrMessageEvent):
        """快捷前缀: > 消息 或 >N 消息 (仅管理员)"""
        from . import file_ops
        self.notification_mgr._event_cache[event.unified_msg_origin] = event
        prefix = self._quick_prefix
        raw = event.message_str

        if not raw or not raw.startswith(prefix):
            return  # 不匹配，不拦截

        if not self._is_admin(event):
            return  # 非管理员，静默忽略

        await self.state_mgr.ensure_primary_session(event)
        rest = raw[len(prefix):]

        if not rest:
            return  # 只有前缀，忽略

        target_sid = None
        text = None

        parts = rest.split(None, 1)
        target_flavor = "claude"
        if parts[0].isdigit():
            idx = int(parts[0])
            if len(parts) < 2:
                return  # >N 但没有消息内容
            text = parts[1]

            await self._refresh_sessions()
            if 1 <= idx <= len(self.sessions_cache):
                target = self.sessions_cache[idx - 1]
                target_sid = target["id"]
                target_flavor = target.get("metadata", {}).get("flavor", "claude")
            else:
                yield event.plain_result(f"无效序号 {idx}，共 {len(self.sessions_cache)} 个 session")
                event.stop_event()
                return
        else:
            text = rest.lstrip()
            if not text:
                return
            target_sid = self.state_mgr.effective_sid(event)
            target_flavor = self.state_mgr.effective_flavor(event) or "claude"

        if not target_sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            event.stop_event()
            return

        # 提取文件并上传
        files = file_ops.extract_files_from_message(event)
        attachments = []

        if files:
            upload_msgs = []
            for fpath in files:
                ok, msg, attach = await file_ops.upload_file(self.client, target_sid, fpath)
                upload_msgs.append(msg)
                if ok and attach:
                    attachments.append(attach)

            if upload_msgs:
                yield event.plain_result("正在上传文件...\n" + "\n".join(upload_msgs))

        # 发送消息（带附件）
        current_sid = self.state_mgr.current_sid(event)
        reminder = ""
        if current_sid and current_sid != target_sid:
            reminder = f"→ 发送到 [{target_flavor}] {target_sid[:8]} (当前窗口: {current_sid[:8]})\n"

        ok, msg = await session_ops.send_message(self.client, target_sid, text, attachments)
        await self.state_mgr.set_user_state(event)
        yield event.plain_result(reminder + msg)
        event.stop_event()

