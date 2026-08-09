"""HAPI Connector AstrBot 插件入口
注册指令组、快捷前缀、SSE 生命周期管理
所有指令仅管理员可用
"""

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig, logger
from astrbot.api.message_components import Poke
import astrbot.api.message_components as Comp

from .core.hapi_client import AsyncHapiClient
from .core.cf_access import CfAccessManager
from .core.sse_listener import SSEListener
from .core.binding_manager import BindingManager
from .core.state_manager import StateManager
from .core.notification_manager import NotificationManager
from .core.pending_manager import PendingManager
from .chat.command_handlers import CommandHandlers
from .core import session_ops
from .render import formatters


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
          "连接 HAPI，随时随地用 Claude / Codex / Cursor / Grok / Kimi / OpenCode / Pi vibe coding",
          "3.2.6")
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

        # session 缓存（WebUI soft_refresh 用时间戳节流全量拉取）
        self.sessions_cache: list[dict] = []
        self._sessions_cache_ts: float = 0.0

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
        # 供 SSE 推送呈现（对话/结构卡）读取 config 与 notification_mgr
        self.sse_listener.plugin = self

        # 待审批管理器
        self.pending_mgr = PendingManager(self.sse_listener)

        # 命令处理器
        self.cmd_handlers = CommandHandlers(self)

        # 快捷前缀
        self._quick_prefix = self.config.get("quick_prefix", ">")

        # 戳一戳：总开关 + 映射动作（默认 approve 兼容旧行为）
        self._poke_approve = self.config.get("poke_approve", True)
        from .chat.poke_actions import normalize_poke_action

        self._poke_action = normalize_poke_action(self.config.get("poke_action", "approve"))

        # 快捷关键词映射（默认 stop/停、sw、cl→send /clear、继续→send 继续、专注/退出专注）
        from .chat.keyword_maps import (
            DEFAULT_KEYWORD_MAPS,
            maps_to_storage,
            migrate_legacy_maps,
            normalize_maps,
        )

        raw_kw = self.config.get("cmd_keyword_maps", None)
        maps = normalize_maps(raw_kw)
        fresh_config = not maps and (
            raw_kw is None
            or (isinstance(raw_kw, str) and str(raw_kw).strip() in ("", "[]"))
        )
        if fresh_config:
            maps = normalize_maps(DEFAULT_KEYWORD_MAPS)
        elif not self.config.get("kw_maps_migrated_v320", False):
            # 一次性迁移（v3.2.0）：旧默认「to 1 /clear」「to 1 继续」→ 发到当前会话的 send。
            maps, migrated = migrate_legacy_maps(maps)
            if migrated:
                try:
                    self.config["cmd_keyword_maps"] = maps_to_storage(maps)
                    logger.info("关键词映射已迁移：cl/继续 改为发送到当前会话（send）")
                except Exception as e:
                    logger.warning("关键词映射迁移落盘失败: %s", e)
        # 无论新装还是老用户，标记只打一次；打过后用户有意配置的 to 1 xx 不再被改写
        if not self.config.get("kw_maps_migrated_v320", False):
            try:
                self.config["kw_maps_migrated_v320"] = True
                self.config.save_config()
            except Exception as e:
                logger.warning("迁移标记落盘失败: %s", e)
        self._cmd_keyword_maps = maps

        # summary 模式消息条数
        self._summary_msg_count = self.config.get("summary_msg_count", 5)

        # event 缓存，用于主动推送
        self.notification_mgr._event_cache = {}

        # 每窗口最近一次用户发送记录 {umo: (sid, text)}，供 /hapi retry（内存，不落盘）
        self._last_sends: dict[str, tuple[str, str]] = {}

        # Focus 纯附件暂存：{umo: {"sid": str, "attachments": [dict, ...]}}
        # 无文字时只 upload 不 send；下一条带文字的 Focus/快捷前缀消息一并送出（内存，不落盘）
        self._pending_attachments: dict[str, dict] = {}

        # 每 session 斜杠命令缓存 {sid: (monotonic_ts, {name,...})}，Focus 判定用（TTL 5min）
        self._slash_cmd_cache: dict[str, tuple[float, set[str]]] = {}

        # LLM 工具集成
        from .chat.llm_integration import LLMIntegration
        self.llm_integration = LLMIntegration(self)

        # WebUI Plugin Pages：按官方示例在 __init__ 注册 API
        # 静态页由 AstrBot 扫描 pages/console/index.html 自动发现
        try:
            from .webui.web_api import register_pages
            register_pages(self)
        except Exception as e:
            logger.exception("注册 WebUI API 失败: %s", e)

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """检查发送者是否为管理员（动态读取配置）"""
        astrbot_config = self.context.get_config(event.unified_msg_origin)
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
            agent(string): 代理类型，如 claude/codex/cursor/grok/kimi/opencode/pi
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
        '''列出所有可用的HAPI指令。根据用户问题选择对应专题：
        - 会话：会话管理（创建、切换、列表、删除等）
        - 对话：对话与消息（发送消息、查看历史等）
        - 审批：审批权限请求（批准、拒绝等）
        - 通知：通知与路由（推送设置、默认推送通知窗口绑定等）
        - 文件：文件操作（读取、写入等）
        - 配置：配置管理（修改推送级别、权限模式等）
        - 全部：查看所有命令
        不填topic显示常用帮助。

        Args:
            topic(string): 帮助专题，可选值：会话/对话/审批/通知/文件/配置/全部
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
                                   machine_id: str = "", session_type: str = "simple", yolo: bool = False,
                                   model_reasoning_effort: str = ""):
        '''创建新的 coding session。创建成功后会自动切换到新session，无需手动调用switch_session。

        Args:
            directory(string): 工作目录路径
            agent(string): 代理类型，推荐 claude/codex/cursor/grok/kimi/opencode/pi（gemini 不可新建）
            machine_id(string): 机器ID，可选，管理多机器时必填
            session_type(string): session类型，simple或worktree，默认simple
            yolo(boolean): 是否自动批准所有权限，默认false
            model_reasoning_effort(string): 支持 reasoning effort 的代理可选；留空继承默认，可选 none/minimal/low/medium/high/xhigh
        '''
        async for result in self.llm_integration.tool_create_session(
                event, directory, agent, machine_id, session_type, yolo, model_reasoning_effort):
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
            return f"⚠️ SSE 连接已连续失败 {n} 次，正在后台重连...\n"
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
        message_str = (event.message_str or "").strip()
        raw_stripped = raw.strip() if raw else ""

        # 从 message_str 提取完整内容
        from_message = self._strip_hapi_prefix(message_str)

        # 如果 raw 非空且看起来更完整（LLM 工具调用场景会传入完整指令），使用 raw
        if raw_stripped and len(raw_stripped.split()) >= len(from_message.split()):
            return self._strip_hapi_prefix(raw_stripped)

        # 否则使用 message_str（普通命令场景）
        return from_message

    async def _refresh_sessions(self):
        """刷新 session 缓存；成功时更新时间戳并清理 SSE 侧过期序号 map。"""
        import time
        try:
            self.sessions_cache[:] = await session_ops.fetch_sessions(self.client)
            self._sessions_cache_ts = time.monotonic()
            live = {s.get("id") for s in self.sessions_cache if s.get("id")}
            prune = getattr(self.sse_listener, "prune_stale_session_maps", None)
            if callable(prune):
                prune(live)
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

    async def ensure_session_for_send(self, event: AstrMessageEvent, sid: str) -> tuple[bool, str, str]:
        """Return an active session id for sending, resuming or respawning if needed."""
        await self._refresh_sessions()
        session = next((s for s in self.sessions_cache if s.get("id") == sid), None)
        if not session:
            return False, sid, f"未找到 session [{sid[:8]}]"
        if session.get("active"):
            return True, sid, ""

        ok, msg, resumed_sid = await session_ops.resume_session(self.client, sid)
        if ok and resumed_sid:
            await self._refresh_sessions()
            resumed = next((s for s in self.sessions_cache if s.get("id") == resumed_sid), None)
            flavor = (resumed or session).get("metadata", {}).get("flavor") or self.state_mgr.effective_flavor(event) or "claude"
            await self.state_mgr.capture_window(resumed_sid, event.unified_msg_origin, flavor)
            note = f"已恢复会话 [{resumed_sid[:8]}]\n"
            return True, resumed_sid, note

        return False, sid, msg

    def _format_no_visible_sessions_text(self, event: AstrMessageEvent) -> str:
        lines = [
            "当前窗口没有接收任何 session 通知。",
            "如果希望在此聊天窗口接收默认通知，可使用 /hapi bind。",
            "如需按 agent 隔离默认通知，可使用 /hapi bind <flavor>（如 claude|codex|cursor|grok）。",
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
        logger.debug(f"[handle_hapi] raw='{raw}', message_str='{event.message_str}'")
        if not self._is_admin(event):
            yield event.plain_result("⚠️ 此命令仅限管理员使用")
            return
        async for result in self.cmd_handlers.cmd_hapi_router(event, raw):
            yield result

    # ──── 戳一戳处理器 ────

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def poke_approve_handler(self, event: AstrMessageEvent):
        """戳一戳机器人 → 执行用户配置的快捷动作（默认批准待审，仅 QQ NapCat 等）"""
        if not self._poke_approve:
            return

        if not self._is_poke_event(event):
            return

        if not self._is_admin(event):
            return

        await self.state_mgr.set_user_state(event)
        from .chat.poke_actions import run_poke_action

        async for result in run_poke_action(self, event, self._poke_action):
            yield result

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

    # ──── 快捷关键词映射（整句严格匹配） ────

    @filter.event_message_type(filter.EventMessageType.ALL, priority=11)
    async def keyword_map_handler(self, event: AstrMessageEvent):
        """关键词映射 → /hapi 子命令。

        - 仅管理员
        - 仅当当前窗口存在「交互中」会话（active / thinking）时生效（类似 LLM 工具动态注册）
        - 无参命令整句严格匹配；可带参命令允许「关键词 + 参数」
        """
        raw = (event.message_str or "").strip()
        if not raw:
            return
        maps = getattr(self, "_cmd_keyword_maps", None)
        if not maps:
            return
        if not self._is_admin(event):
            return
        # Focus 开启的窗口关键词始终生效（保证 session 停止后仍能「退出专注」）；
        # 其余窗口沿用「有交互中会话才生效」的门禁
        if not self.binding_mgr.get_window_focus_mode(event.unified_msg_origin) \
                and not self._window_has_interactive_session(event):
            return
        from .chat.keyword_maps import find_mapped_command

        hit = find_mapped_command(maps, raw)
        if not hit:
            return
        cmd, argument = hit
        remainder = f"{cmd} {argument}".strip() if argument else cmd
        self.notification_mgr._event_cache[event.unified_msg_origin] = event
        async for result in self.cmd_handlers.cmd_hapi_router(event, remainder):
            yield result
        event.stop_event()

    def _window_has_interactive_session(self, event: AstrMessageEvent) -> bool:
        """当前窗口是否有交互中的 HAPI session（active 或 thinking）。"""
        try:
            visible = self.state_mgr.visible_sessions_for_window(event, self.sessions_cache)
        except Exception:
            visible = []
        for s in visible or []:
            if not isinstance(s, dict):
                continue
            if s.get("thinking"):
                return True
            if s.get("active"):
                return True
            # 部分缓存字段可能用 status / state
            st = str(s.get("status") or s.get("state") or "").lower()
            if st in ("active", "thinking", "running", "busy"):
                return True
        return False

    # ──── Focus 模式判定 ────

    async def _focus_forward_text(self, event: AstrMessageEvent, raw: str) -> str | None:
        """Focus 模式下，决定这条消息是否转发给当前 session。

        返回要发送的文本（"" 表示纯附件消息）；返回 None 表示不转发、放行给其它处理器。

        斜杠消息的处理（注意 AstrBot 的 WakingCheckStage 在插件处理器之前就会剥离
        唤醒前缀，默认 "/"，此时 event.message_str 已不含前缀，须回看
        message_obj.message_str 原文判断）：
        - 原文以 "/" 开头且命中当前会话的斜杠命令表（实时从 HAPI 拉取，失败回退
          内置表）→ 还原斜杠、原样转发给 agent（如 Claude 的 /clear、Codex 的 /model）
        - /hapi 永远归插件，其余未命中的 "/" 命令放行给 AstrBot
        - 自定义唤醒前缀（如 "!"）开头视为 AstrBot 指令，不转发

        其余排除项：
        - "hapi ..." 开头（无斜杠的 hapi 调用）
        - 关键词别名命中（如 stop / 继续 / 专注，与 keyword_map_handler 同一套匹配）

        注意：裸子命令词（如单发 "list"）没有任何处理器接管，
        故不做排除，按普通消息转发给 AI。
        """
        text = (raw or "").strip()
        if not text:
            # 无文本但可能带附件（图片等），也转发
            from .core import file_ops
            return "" if file_ops.extract_files_from_message(event) else None

        # 还原用户输入的斜杠原文（唤醒前缀可能已被 AstrBot 提前剥离）
        slash_text = None
        if text.startswith("/"):
            slash_text = text
        else:
            try:
                original = (getattr(event.message_obj, "message_str", "") or "").strip()
            except Exception:
                original = ""
            if original.startswith("/"):
                slash_text = original
            elif self._original_text_has_wake_prefix(event):
                # 非 "/" 的自定义唤醒前缀（如 "!"）：是 AstrBot 指令，放行
                return None

        if slash_text is not None:
            body = slash_text[1:].strip()
            name = body.split(None, 1)[0].lower() if body else ""
            if not name or name == "hapi":
                return None
            sid = self.state_mgr.effective_sid(event)
            if not sid:
                return None  # 无目标会话，放行给 AstrBot
            flavor = self.state_mgr.effective_flavor(event)
            commands = await self._get_session_slash_commands(sid, flavor)
            if name in commands:
                return slash_text  # agent 内置/自定义命令：带斜杠原样转发
            return None

        first = text.split(None, 1)[0].lower()
        if first == "hapi":
            return None

        # 关键词别名：匹配规则与 keyword_map_handler 一致
        # （Focus 开启的窗口 keyword_map_handler 必定生效，见其门禁，不会漏接）
        maps = getattr(self, "_cmd_keyword_maps", None)
        if maps:
            from .chat.keyword_maps import find_mapped_command
            if find_mapped_command(maps, text):
                return None
        return text

    async def _get_session_slash_commands(self, sid: str, flavor: str | None) -> set[str]:
        """当前会话可用斜杠命令名集合（小写、不带 /），带 TTL 缓存。

        优先 GET /api/sessions/:id/slash-commands（含用户/项目/插件自定义命令）；
        拉取失败或为空时回退 flavor 内置表。
        """
        import time as _time

        now = _time.monotonic()
        cached = self._slash_cmd_cache.get(sid)
        if cached and now - cached[0] < 300:
            return cached[1]
        names: set[str] = set()
        try:
            cmds = await session_ops.fetch_slash_commands(self.client, sid)
            names = {
                str(c.get("name", "")).strip().lstrip("/").lower()
                for c in cmds if isinstance(c, dict)
            }
            names.discard("")
        except Exception as e:
            logger.debug("拉取 session %s 斜杠命令失败，回退内置表: %s", sid[:8], e)
        if not names:
            from .chat.flavor_profiles import builtin_slash_commands_for
            names = set(builtin_slash_commands_for(flavor))
        self._slash_cmd_cache[sid] = (now, names)
        return names

    def _original_text_has_wake_prefix(self, event: AstrMessageEvent) -> bool:
        """原始消息文本是否以 AstrBot 唤醒前缀开头。

        WakingCheckStage 在插件处理器之前执行，命中唤醒前缀时会把
        event.message_str 剥离前缀（"/help" → "help"），但 message_obj.message_str
        保留原文；据此还原「用户输入的是一条指令」这一事实。
        """
        try:
            original = (getattr(event.message_obj, "message_str", "") or "").strip()
        except Exception:
            return False
        if not original:
            return False
        try:
            prefixes = self.context.get_config(event.unified_msg_origin).get("wake_prefix", ["/"]) or ["/"]
        except Exception:
            prefixes = ["/"]
        return any(p and original.startswith(str(p)) for p in prefixes)

    # ──── 快捷前缀处理器 ────

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def quick_prefix_handler(self, event: AstrMessageEvent):
        """快捷前缀: > 消息 或 >N 消息 (仅管理员)；Focus 模式下普通消息免前缀转发"""
        from .core import file_ops
        self.notification_mgr._event_cache[event.unified_msg_origin] = event
        prefix = self._quick_prefix
        raw = event.message_str

        if not self._is_admin(event):
            return  # 非管理员，静默忽略

        # Focus 模式：本窗口普通消息直接转发到当前 session（无需前缀）
        umo = event.unified_msg_origin
        focus_forward = False
        rest = None

        if raw and raw.startswith(prefix):
            # 带前缀仍走原快捷前缀逻辑（Focus 下也允许 >N 指定序号）
            rest = raw[len(prefix):]
        elif self.binding_mgr.get_window_focus_mode(umo):
            forward_text = await self._focus_forward_text(event, raw)
            if forward_text is None:
                return
            focus_forward = True
            rest = forward_text  # 整句（含还原的斜杠命令）视为要发送的内容
        else:
            return  # 不匹配，不拦截

        await self.state_mgr.ensure_primary_session(event)

        if not rest and not focus_forward:
            return  # 只有前缀，忽略（Focus 下允许纯附件消息，文本为空）

        target_sid = None
        text = None

        parts = rest.split(None, 1)
        target_flavor = "claude"
        if not focus_forward and parts and parts[0].isdigit():
            # 仅快捷前缀支持 >N 序号；Focus 转发把整句当内容（避免「3 个文件」被当序号）
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
            if not text and not focus_forward:
                return
            target_sid = self.state_mgr.effective_sid(event)
            target_flavor = self.state_mgr.effective_flavor(event) or "claude"

        if not target_sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            event.stop_event()
            return

        reminder = ""
        ok_ready, ready_sid, ready_msg = await self.ensure_session_for_send(event, target_sid)
        if not ok_ready:
            yield event.plain_result(f"发送前恢复 session 失败: {ready_msg}")
            event.stop_event()
            return
        if ready_sid != target_sid:
            target_sid = ready_sid
            target_flavor = self.state_mgr.effective_flavor(event) or target_flavor
            reminder += ready_msg

        # 提取文件并上传（双缓存留更大 + 内容哈希去重）
        attachments, upload_notice = await file_ops.upload_event_files(self.client, event, target_sid)
        if upload_notice:
            yield event.plain_result(upload_notice)

        send_text = (text or "").strip()

        # Focus 纯附件：只暂存到「发送区」，不立刻发给 AI（等下一条文字一并送出）
        if focus_forward and not send_text:
            if not attachments:
                yield event.plain_result(
                    reminder + "✗ 未提取到可暂存的附件（或上传全部失败）"
                )
                event.stop_event()
                return
            staged = self._stage_focus_attachments(umo, target_sid, attachments)
            yield event.plain_result(reminder + self._format_staged_attachments(staged))
            event.stop_event()
            return

        # 带文字发送：合并本窗口针对该 session 的暂存附件
        staged_atts = self._peek_staged_attachments(umo, target_sid)
        merged = self._merge_attachment_lists(staged_atts, attachments)

        if not send_text and not merged:
            yield event.plain_result(
                reminder + "✗ 未提取到可发送的附件（或上传全部失败），已取消发送"
            )
            event.stop_event()
            return

        # 发送消息（带附件）
        current_sid = self.state_mgr.current_sid(event)
        if current_sid and current_sid != target_sid:
            reminder += f"→ 发送到 [{target_flavor}] {target_sid[:8]} (当前窗口: {current_sid[:8]})\n"

        if send_text:
            self._last_sends[umo] = (target_sid, send_text)
        ok, msg = await session_ops.send_message(
            self.client, target_sid, send_text, merged or None
        )
        if ok and staged_atts:
            self._clear_staged_attachments(umo, target_sid)
            msg += f"（含暂存附件 ×{len(staged_atts)}）"
        await self.state_mgr.set_user_state(event)
        yield event.plain_result(reminder + msg)
        event.stop_event()

    # ──── Focus 附件暂存（发送区） ────

    def _stage_focus_attachments(
        self, umo: str, sid: str, attachments: list[dict]
    ) -> dict:
        """把已 upload 的附件放入本窗口发送区；切换 session 时丢弃旧暂存。"""
        bucket = self._pending_attachments.get(umo)
        if not bucket or bucket.get("sid") != sid:
            bucket = {"sid": sid, "attachments": []}
        existing_paths = {
            a.get("path") for a in bucket["attachments"] if a.get("path")
        }
        for att in attachments:
            path = att.get("path")
            if path and path in existing_paths:
                continue
            bucket["attachments"].append(att)
            if path:
                existing_paths.add(path)
        self._pending_attachments[umo] = bucket
        return bucket

    def _peek_staged_attachments(self, umo: str, sid: str) -> list[dict]:
        """读取本窗口针对 sid 的暂存附件（不清除）。sid 不一致则忽略旧暂存。"""
        bucket = self._pending_attachments.get(umo)
        if not bucket or bucket.get("sid") != sid:
            return []
        return list(bucket.get("attachments") or [])

    def _clear_staged_attachments(self, umo: str, sid: str | None = None) -> None:
        """清空本窗口暂存。sid 给定时仅在匹配时清除。"""
        bucket = self._pending_attachments.get(umo)
        if not bucket:
            return
        if sid is not None and bucket.get("sid") != sid:
            return
        self._pending_attachments.pop(umo, None)

    @staticmethod
    def _merge_attachment_lists(
        staged: list[dict], current: list[dict]
    ) -> list[dict]:
        """暂存在前、本条在后；按 path 去重。"""
        merged: list[dict] = []
        seen: set[str] = set()
        for att in (staged or []) + (current or []):
            path = att.get("path") or att.get("id") or ""
            if path and path in seen:
                continue
            if path:
                seen.add(path)
            merged.append(att)
        return merged

    @staticmethod
    def _format_staged_attachments(bucket: dict) -> str:
        atts = bucket.get("attachments") or []
        lines = [f"📎 已暂存 {len(atts)} 个附件"]
        for att in atts:
            name = att.get("filename") or att.get("path") or "file"
            size = att.get("size")
            if isinstance(size, int) and size > 0:
                if size < 1024:
                    size_s = f"{size}B"
                elif size < 1024 * 1024:
                    size_s = f"{size / 1024:.1f}KB"
                else:
                    size_s = f"{size / (1024 * 1024):.1f}MB"
                lines.append(f"  · {name}（{size_s}）")
            else:
                lines.append(f"  · {name}")
        return "\n".join(lines)
