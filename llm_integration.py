"""LLM 工具集成 - 为 LLM 提供 HAPI Coding Session 交互能力"""

import asyncio
import time
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger
from . import session_ops
from . import formatters


class LLMIntegration:
    """LLM 工具集成管理器"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.client = plugin.client
        self.state_mgr = plugin.state_mgr
        self.pending_mgr = plugin.pending_mgr
        self.sessions_cache = plugin.sessions_cache

    # ──── 工具可见性控制 ────

    @filter.on_llm_request()
    async def on_llm_request_hook(self, event: AstrMessageEvent, request: ProviderRequest):
        """根据权限和窗口状态动态控制工具可见性"""
        # 1. 权限检查：非管理员移除所有工具
        is_admin = self.plugin._is_admin(event)
        logger.debug(f"[LLM工具] 权限检查: is_admin={is_admin}")
        if not is_admin:
            self._remove_hapi_tools(request, keep_basic=False)
            logger.debug("[LLM工具] 非管理员，已移除所有工具")
            return

        # 2. 上下文检查：窗口无可见 session 时只保留基础工具
        visible_sessions = self.state_mgr.visible_sessions_for_window(event, self.sessions_cache)
        logger.debug(f"[LLM工具] 可见session数: {len(visible_sessions)}, 总session数: {len(self.sessions_cache)}")
        if not visible_sessions:
            self._remove_hapi_tools(request, keep_basic=True)
            logger.debug("[LLM工具] 当前窗口无可见session，已移除非基础工具")
            return

    def _remove_hapi_tools(self, request: ProviderRequest, keep_basic: bool = False):
        """移除所有 hapi_coding 工具

        Args:
            keep_basic: 是否保留基础工具（list_sessions/list_commands/execute_command）
        """
        if not hasattr(request, 'func_tool') or not request.func_tool:
            return

        # 基础工具（始终可用）
        basic_tools = {
            "hapi_coding_list_sessions",
            "hapi_coding_list_commands",
            "hapi_coding_execute_command",
        }

        # 所有工具
        all_tools = {
            "hapi_coding_get_status",
            "hapi_coding_list_sessions",
            "hapi_coding_message_history",
            "hapi_coding_get_config_status",
            "hapi_coding_list_commands",
            "hapi_coding_send_message",
            "hapi_coding_switch_session",
            "hapi_coding_create_session",
            "hapi_coding_change_config",
            "hapi_coding_stop_message",
            "hapi_coding_execute_command",
        }

        # 决定要移除的工具
        tools_to_remove = all_tools - basic_tools if keep_basic else all_tools

        for tool_name in tools_to_remove:
            request.func_tool.remove_tool(tool_name)

    # ──── 审批机制 ────

    async def _require_approval(self, tool_name: str, args: dict, event: AstrMessageEvent) -> tuple[bool, str]:
        """请求审批并等待结果

        Returns:
            (approved, reason): approved=True表示批准，reason说明原因（"approved"/"denied"/"timeout"/"notification_failed"）
        """
        # LLM 工具审批使用窗口 ID 作为 key，而不是 session ID
        window_id = event.unified_msg_origin

        # 添加到 pending 队列（伪装成 HAPI 权限请求）
        req_id, future, index = self.pending_mgr.add_llm_tool_request(window_id, tool_name, args)

        # 计算当前待审批总数（LLM 工具审批不受窗口限制，统计所有待审批）
        items = self.pending_mgr.flatten_pending(None, None)
        total = len(items)

        # 发送通知到当前窗口
        args_str = ", ".join(f"{k}={v}" for k, v in args.items())
        msg = f"""🤖 Astrbot 工具调用请求
  {tool_name}
  参数: {args_str}

当前共 {total} 个待审批，此请求审批序号：{index}

审批指令:
  /hapi a        全部批准
  /hapi a <序号>  批准单个
  /hapi deny     全部拒绝
  /hapi deny <序号> 拒绝单个
  /hapi pending   查看完整列表"""

        notification_sent = False
        try:
            await event.send(MessageChain().message(msg))
            notification_sent = True
        except Exception as e:
            logger.warning(f"LLM 工具审批通知发送失败: {e}")

        # 如果通知发送失败，立即返回拒绝
        if not notification_sent:
            self.pending_mgr.remove_entry(window_id, req_id)
            logger.error(f"LLM 工具 {tool_name} 审批通知发送失败，自动拒绝")
            return False, "notification_failed"

        # 等待审批结果（1分钟超时）
        try:
            approved = await asyncio.wait_for(future, timeout=60)
            return (True, "approved") if approved else (False, "denied")
        except asyncio.TimeoutError:
            # 超时，清理请求
            self.pending_mgr.remove_entry(window_id, req_id)
            logger.warning(f"LLM 工具 {tool_name} 审批超时（60秒无响应）")
            # 如果处于忙时托管时段，超时默认允许
            if self.plugin.sse_listener._auto_approve_enabled and self.plugin.sse_listener._in_auto_approve_window():
                logger.info(f"忙时托管时段，自动批准 {tool_name}")
                return True, "auto_approved"
            return False, "timeout"
        except asyncio.CancelledError:
            # 任务被取消（通常是外部超时），清理并返回拒绝，不再传播异常
            self.pending_mgr.remove_entry(window_id, req_id)
            logger.warning(f"LLM 工具 {tool_name} 审批被取消")
            return False, "cancelled"

    # ──── 查询类工具（无需审批）────

    async def tool_get_status(self, event: AstrMessageEvent):
        '''获取当前交互中的 HAPI session 的状态信息。'''
        sid = self.state_mgr.current_sid(event)
        if not sid:
            yield "当前窗口未绑定 session"
            return

        session = next((s for s in self.sessions_cache if s.get("id") == sid), None)
        if not session:
            yield "Session 不存在"
            return

        meta = session.get("metadata", {})
        agent = session.get("agent", "unknown")
        active = "活跃" if session.get("active") else "非活跃"
        perm_mode = session.get("permission_mode", "unknown")
        path = meta.get("path", "unknown")

        info = f"""当前 HAPI Coding Session 状态:
- Session ID: {sid[:8]}...
- 路径: {path}
- Agent: {agent}
- 状态: {active}
- 权限模式: {perm_mode}"""
        yield info

    async def tool_list_sessions(self, event: AstrMessageEvent, window: str = "", path: str = "", agent: str = ""):
        '''列出 HAPI 的可交互 session 列表。

        Args:
            window(string): 按聊天窗口过滤（默认为空表示当前窗口，设为 'all' 查询所有聊天窗口，用户没有明确要求时一般置空）
            path(string): 按路径搜索
            agent(string): 按代理类型过滤（claude/codex/gemini/opencode）
        '''
        # 当前窗口无session时，自动查询所有session
        visible_sessions = self.state_mgr.visible_sessions_for_window(event, self.sessions_cache)
        if not visible_sessions and window == "":
            window = "all"
            auto_switched = True
        else:
            auto_switched = False

        if window == "all":
            sessions = self.sessions_cache
        else:
            sessions = visible_sessions

        # 过滤
        if path:
            sessions = [s for s in sessions if path.lower() in s.get("metadata", {}).get("path", "").lower()]
        if agent:
            sessions = [s for s in sessions if s.get("metadata", {}).get("flavor", "").lower() == agent.lower()]

        if not sessions:
            yield "没有找到符合条件的 session"
            return

        # 复用 formatters.format_session_list，但移除 emoji
        current_sid = self.state_mgr.current_sid(event)
        text = formatters.format_session_list(sessions, current_sid, self.sessions_cache, header_current_window=event.unified_msg_origin)

        # 替换 emoji 为文字
        text = text.replace("📁", "[目录]")
        text = text.replace("🏷️", "ID:")
        text = text.replace("💭", "[思考中]")
        text = text.replace("🟢", "[运行中]")
        text = text.replace("⚪", "[已关闭]")
        text = text.replace("🤖", "")
        text = text.replace("⚠️", "[待审批]")
        text = text.replace("💡", "提示:")

        # 如果自动切换到all，添加提示
        if auto_switched:
            text = "提示：当前窗口无可见session，已自动查询所有窗口的session\n\n" + text

        yield text

    async def tool_message_history(self, event: AstrMessageEvent, rounds: int = 1):
        '''查询当前交互中的 session 的历史消息。

        Args:
            rounds(number): 查询最近几轮消息（默认 1 轮）
        '''
        sid = self.state_mgr.current_sid(event)
        if not sid:
            yield "当前窗口未绑定 session"
            return

        try:
            # 多取消息以保证覆盖 N 轮
            fetch_limit = min(rounds * 80, 500)
            msgs = await session_ops.fetch_messages(self.client, sid, limit=fetch_limit)
            all_rounds = formatters.split_into_rounds(msgs)
            # 取最后 N 轮
            selected = all_rounds[-rounds:]
            if not selected:
                yield "暂无消息记录"
                return

            # 格式化所有轮次
            lines = []
            total = len(selected)
            for i, round_msgs in enumerate(selected, 1):
                text = formatters.format_round(round_msgs, i, total)
                lines.append(text)

            yield "\n\n".join(lines)
        except Exception as e:
            yield f"获取消息失败: {e}"

    async def tool_get_config_status(self, event: AstrMessageEvent):
        '''获取当前插件配置状态及可修改项说明。'''
        output_level = self.plugin.config.get("output_level", "simple")
        auto_approve = self.plugin.sse_listener._auto_approve_enabled
        auto_start = self.plugin.sse_listener._auto_approve_start
        auto_end = self.plugin.sse_listener._auto_approve_end
        remind = self.plugin.sse_listener._remind_enabled
        remind_interval = self.plugin.sse_listener._remind_interval
        quick_prefix = self.plugin.config.get("quick_prefix", ">")

        info = f"""当前配置状态:

output_level (SSE推送级别): {output_level}
  - silence: 仅推送权限请求和任务完成提醒
  - simple: 仅推送 agent 文本消息，不包含复杂的工具调用信息
  - summary: 任务完成时推送最近的 agent 消息
  - detail: 实时推送所有新消息（信息量较大）

auto_approve_enabled (忙时自动审批): {'开启' if auto_approve else '关闭'}
  时间段: {auto_start} - {auto_end}
  值: true/false

remind_pending (定时提醒待审批): {'开启' if remind else '关闭'}
  间隔: {remind_interval} 秒
  值: true/false

quick_prefix (快捷前缀): {quick_prefix}
  用于快速发送消息，如 "> 消息内容\""""
        yield info

    async def tool_list_commands(self, event: AstrMessageEvent, topic: str = ""):
        '''列出所有可用的 HAPI 指令。

        Args:
            topic(string): 帮助主题（可选，默认显示常用帮助）
        '''
        yield formatters.get_help_text(topic)

    # ──── 操作类工具（需要审批）────

    async def tool_send_message(self, event: AstrMessageEvent, message: str):
        '''向当前 session 发送消息。

        Args:
            message(string): 要发送的消息内容
        '''
        sid = self.state_mgr.current_sid(event)
        if not sid:
            yield "当前窗口未绑定 session"
            return

        # 请求审批
        approved, reason = await self._require_approval("hapi_coding_send_message", {"message": message}, event)
        logger.debug(f"[tool_send_message] approved={approved}, reason={reason}")
        if not approved:
            if reason == "timeout":
                yield "操作超时：60秒内未收到用户审批。请提醒用户使用 /hapi a 批准或 /hapi deny 拒绝。"
            elif reason == "notification_failed":
                yield "操作失败：无法发送审批通知到用户。请检查是否已绑定 session。"
            else:
                yield "操作被用户拒绝"
            return

        # 执行发送
        ok, result = await session_ops.send_message(self.client, sid, message)
        yield result if ok else f"发送失败: {result}"

    async def tool_switch_session(self, event: AstrMessageEvent, target: str):
        '''切换到指定的 session。

        Args:
            target(string): session 序号（如 "1"）或 session ID（如 "abc12345"）
        '''
        # 请求审批
        approved, reason = await self._require_approval("hapi_coding_switch_session", {"target": target}, event)
        if not approved:
            if reason == "timeout":
                yield "操作超时：60秒内未收到用户审批。请提醒用户使用 /hapi a 批准或 /hapi deny 拒绝。"
            elif reason == "notification_failed":
                yield "操作失败：无法发送审批通知到用户。请检查是否已绑定 session。"
            else:
                yield "操作被用户拒绝"
            return

        # 复用 cmd_sw 逻辑，提取消息内容返回给 LLM
        async for result in self.plugin.cmd_handlers.cmd_sw(event, target):
            # result 是 MessageChain，提取文本内容
            if hasattr(result, 'chain'):
                for seg in result.chain:
                    if hasattr(seg, 'text'):
                        yield seg.text
            else:
                yield str(result)

    async def tool_create_session(self, event: AstrMessageEvent, directory: str, agent: str,
                                   machine_id: str = "", session_type: str = "simple", yolo: bool = False):
        '''创建新的 coding session。

        Args:
            directory(string): 工作目录路径
            agent(string): 代理类型（claude/codex/gemini/opencode）
            machine_id(string): 机器 ID（可选，管理多机器时必填）
            session_type(string): session 类型（simple/worktree，默认 simple）
            yolo(boolean): 是否自动批准所有权限（默认 false）
        '''
        # 获取机器列表
        try:
            machines = await session_ops.fetch_machines(self.client)
        except Exception as e:
            yield f"获取机器列表失败: {e}"
            return

        if not machines:
            yield "没有在线的机器"
            return

        # 处理 machine_id
        if not machine_id:
            if len(machines) == 1:
                machine_id = machines[0].get("id")
            else:
                lines = ["有多个机器在线，请指定 machine_id:"]
                for m in machines:
                    mid = m.get("id", "?")
                    meta = m.get("metadata", {})
                    host = meta.get("host", "unknown")
                    plat = meta.get("platform", "?")
                    lines.append(f"  - {mid}: {host} ({plat})")
                yield "\n".join(lines)
                return

        # 请求审批
        approved, reason = await self._require_approval("hapi_coding_create_session",
                                           {"machine_id": machine_id, "directory": directory,
                                            "agent": agent, "session_type": session_type, "yolo": yolo}, event)
        if not approved:
            if reason == "timeout":
                yield "操作超时：60秒内未收到用户审批。请提醒用户使用 /hapi a 批准或 /hapi deny 拒绝。"
            elif reason == "notification_failed":
                yield "操作失败：无法发送审批通知到用户。请检查是否已绑定 session。"
            else:
                yield "操作被用户拒绝"
            return

        # 执行创建
        ok, msg, sid = await session_ops.spawn_session(self.client, machine_id, directory, agent, session_type, yolo)
        if ok and sid:
            await self.state_mgr.capture_window(sid, event.unified_msg_origin, agent)
            yield f"✅ 已创建 session: {sid[:8]}"
        else:
            yield f"创建失败: {msg}"

    async def tool_change_config(self, event: AstrMessageEvent, config_name: str, value: str):
        '''修改插件配置项。必须先调用 hapi_coding_get_config_status 查看可修改项。

        Args:
            config_name(string): 配置项名称
            value(string): 新值
        '''
        # 请求审批
        approved, reason = await self._require_approval("hapi_coding_change_config",
                                           {"config_name": config_name, "value": value}, event)
        if not approved:
            if reason == "timeout":
                yield "操作超时：60秒内未收到用户审批。请提醒用户使用 /hapi a 批准或 /hapi deny 拒绝。"
            elif reason == "notification_failed":
                yield "操作失败：无法发送审批通知到用户。请检查是否已绑定 session。"
            else:
                yield "操作被用户拒绝"
            return

        # 执行修改
        if config_name == "output_level":
            if value not in ["silence", "summary", "simple", "detail"]:
                yield "output_level 只能是 silence/summary/simple/detail"
                return
            self.plugin.sse_listener.output_level = value
            self.plugin.config["output_level"] = value
            self.plugin.config.save_config()
            yield f"✅ 已设置 {config_name} = {value}"
        elif config_name == "auto_approve_enabled":
            bool_val = value.lower() in ["true", "1", "yes", "on", "开启"]
            self.plugin.sse_listener._auto_approve_enabled = bool_val
            self.plugin.config["auto_approve_enabled"] = bool_val
            self.plugin.config.save_config()
            yield f"✅ 已设置 {config_name} = {bool_val}"
        elif config_name == "remind_pending":
            bool_val = value.lower() in ["true", "1", "yes", "on", "开启"]
            self.plugin.sse_listener._remind_enabled = bool_val
            self.plugin.config["remind_pending"] = bool_val
            self.plugin.config.save_config()
            yield f"✅ 已设置 {config_name} = {bool_val}"
        elif config_name == "quick_prefix":
            self.plugin._quick_prefix = value
            self.plugin.config["quick_prefix"] = value
            self.plugin.config.save_config()
            yield f"✅ 已设置 {config_name} = {value}"
        else:
            yield f"不支持的配置项: {config_name}，请先调用 hapi_coding_get_config_status 查看可用配置"

    async def tool_stop_message(self, event: AstrMessageEvent):
        '''停止当前 session 的消息生成。'''
        sid = self.state_mgr.current_sid(event)
        if not sid:
            yield "当前窗口未绑定 session"
            return

        # 请求审批
        approved, reason = await self._require_approval("hapi_coding_stop_message", {"session_id": sid[:8]}, event)
        if not approved:
            if reason == "timeout":
                yield "操作超时：60秒内未收到用户审批。请提醒用户使用 /hapi a 批准或 /hapi deny 拒绝。"
            elif reason == "notification_failed":
                yield "操作失败：无法发送审批通知到用户。请检查是否已绑定 session。"
            else:
                yield "操作被用户拒绝"
            return

        # 执行停止
        ok, msg = await session_ops.abort_session(self.client, sid)
        if ok:
            await self.plugin._refresh_sessions()
        yield msg

    async def tool_execute_command(self, event: AstrMessageEvent, command: str):
        '''直接执行hapi相关指令。当用户希望执行hapi相关指令操作时，使用此工具，而不是使用默认的shell。使用前请务必调用 hapi_coding_list_commands 查看指令格式和参数说明，错误的指令可能导致不可预料的后果。

        Args:
            command(string): 完整的 /hapi 指令（不含 /hapi 前缀）
        '''
        # 请求审批
        approved, reason = await self._require_approval("hapi_coding_execute_command", {"command": command}, event)
        if not approved:
            if reason == "timeout":
                yield "操作超时：60秒内未收到用户审批。请提醒用户使用 /hapi a 批准或 /hapi deny 拒绝。"
            elif reason == "notification_failed":
                yield "操作失败：无法发送审批通知到用户。请检查是否已绑定 session。"
            else:
                yield "操作被用户拒绝"
            return

        # 执行命令
        logger.info(f"[LLM工具] 开始执行命令: {command}")
        results = []
        async for result in self.plugin.cmd_handlers.cmd_hapi_router(event, f"/hapi {command}"):
            logger.info(f"[LLM工具] 收到结果，类型: {type(result)}")

            # 立即发送给用户
            await event.send(result)

            # 提取文本
            if hasattr(result, 'chain'):
                text_parts = []
                for seg in result.chain:
                    if hasattr(seg, 'text'):
                        text_parts.append(seg.text)
                if text_parts:
                    text = "".join(text_parts)
                    logger.info(f"[LLM工具] 提取文本: {text[:100]}...")
                    results.append(text)

        logger.info(f"[LLM工具] 命令执行完成，共 {len(results)} 条消息")

        # 检测交互式命令
        cmd_name = command.strip().split()[0] if command.strip() else ""
        interactive_cmds = ['create', 'delete', 'rename', 'archive', 'perm', 'model', 'output', 'prune']

        if cmd_name in interactive_cmds:
            yield f"这是一条交互式命令，用户已自行完成交互设置，你可以自行思考和查看操作结果"
        elif results:
            yield "\n\n".join(results)
        else:
            yield "命令执行完成"

