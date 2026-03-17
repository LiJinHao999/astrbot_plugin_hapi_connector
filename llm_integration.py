"""LLM 工具集成 - 为 LLM 提供 HAPI Coding Session 交互能力"""

import asyncio
import time
from astrbot.api.event import filter, AstrMessageEvent
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
        if not self.plugin._is_admin(event):
            self._remove_hapi_tools(request)
            return

        # 2. 上下文检查：窗口无可见 session 时移除工具
        visible_sessions = self.state_mgr.visible_sessions_for_window(event, self.sessions_cache)
        if not visible_sessions:
            self._remove_hapi_tools(request)
            return

        # 3. 状态注入：附加 HAPI Hub 信息
        total_count = len(self.sessions_cache)
        visible_count = len(visible_sessions)
        bound_sid = self.state_mgr.current_sid(event)

        context_info = f"""

## HAPI Coding Hub
已连接到 HAPI 服务，可使用 hapi_coding 工具管理远程 coding sessions。
- 当前聊天窗口可见 {visible_count} 个 session
- Hub 总共 {total_count} 个 session"""

        if bound_sid:
            bound = next((s for s in visible_sessions if s.get("id") == bound_sid), None)
            if bound:
                meta = bound.get("metadata", {})
                path = meta.get("path", "unknown")
                agent = bound.get("agent", "unknown")
                active = "活跃" if bound.get("active") else "非活跃"
                title = (meta.get("summary") or {}).get("text", "")

                session_info = f"session {bound_sid[:8]}"
                if title:
                    session_info = f"{title} ({session_info})"

                context_info += f"\n- 当前正在与 {session_info} 交互: {path} ({agent}, {active})"

        request.system_prompt = (request.system_prompt or "") + context_info

    def _remove_hapi_tools(self, request: ProviderRequest):
        """移除所有 hapi_coding 工具"""
        if not hasattr(request, 'func_tool') or not request.func_tool:
            return

        tool_names = [
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
        ]

        for tool_name in tool_names:
            request.func_tool.remove_tool(tool_name)

    # ──── 审批机制 ────

    async def _require_approval(self, tool_name: str, args: dict, event: AstrMessageEvent) -> bool:
        """请求审批并等待结果（复用现有审批机制）"""
        # 获取当前 session（用于存储审批请求）
        sid = self.state_mgr.current_sid(event) or "llm_global"

        # 添加到 pending 队列（伪装成 HAPI 权限请求）
        req_id, future = self.pending_mgr.add_llm_tool_request(sid, tool_name, args)

        # 发送通知（复用现有通知机制）
        args_str = ", ".join(f"{k}={v}" for k, v in args.items())
        msg = f"🤖 LLM 工具调用请求\n工具: {tool_name}\n参数: {args_str}\n\n使用 /hapi approve 批准"

        targets = self.state_mgr.select_notification_targets(sid if sid != "llm_global" else "", self.sessions_cache)
        if targets:
            await self.plugin.context.send_message(targets[0], msg)

        # 等待审批结果（5分钟超时）
        try:
            approved = await asyncio.wait_for(future, timeout=300)
            return approved
        except asyncio.TimeoutError:
            # 超时清理
            self.pending_mgr.remove_entry(sid, req_id)
            return False

    # ──── 查询类工具（无需审批）────

    @filter.llm_tool(
        name="hapi_coding_get_status",
        description="获取当前交互中的 HAPI session 的状态信息",
        parameters={"type": "object", "properties": {}, "required": []}
    )
    async def tool_get_status(self, event: AstrMessageEvent):
        '''获取当前交互中的 HAPI session 的状态信息。'''
        sid = self.state_mgr.current_sid(event)
        if not sid:
            yield event.plain_result("当前窗口未绑定 session")
            return

        session = next((s for s in self.sessions_cache if s.get("id") == sid), None)
        if not session:
            yield event.plain_result("Session 不存在")
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
        yield event.plain_result(info)

    @filter.llm_tool(
        name="hapi_coding_list_sessions",
        description="列出 HAPI 的可交互 session 列表",
        parameters={
            "type": "object",
            "properties": {
                "window": {"type": "string", "description": "按聊天窗口过滤（默认为空表示当前窗口，设为 'all' 查询所有聊天窗口，用户没有明确要求时一般置空）"},
                "path": {"type": "string", "description": "按路径搜索（可选）"},
                "agent": {"type": "string", "description": "按代理类型过滤（claude/codex/gemini/opencode，可选）"}
            },
            "required": []
        }
    )
    async def tool_list_sessions(self, event: AstrMessageEvent, window: str = "", path: str = "", agent: str = ""):
        '''列出 HAPI 的可交互 session 列表。

        Args:
            window(string): 按聊天窗口过滤（默认为空表示当前窗口，设为 'all' 查询所有聊天窗口，用户没有明确要求时一般置空）
            path(string): 按路径搜索
            agent(string): 按代理类型过滤（claude/codex/gemini/opencode）
        '''
        if window == "all":
            sessions = self.sessions_cache
        else:
            sessions = self.state_mgr.visible_sessions_for_window(event, self.sessions_cache)

        # 过滤
        if path:
            sessions = [s for s in sessions if path.lower() in s.get("metadata", {}).get("path", "").lower()]
        if agent:
            sessions = [s for s in sessions if s.get("agent", "").lower() == agent.lower()]

        if not sessions:
            yield event.plain_result("没有找到符合条件的 session")
            return

        lines = [f"找到 {len(sessions)} 个 session:"]
        for idx, s in enumerate(sessions, 1):
            sid = s.get("id", "")
            meta = s.get("metadata", {})
            path_str = meta.get("path", "unknown")
            agent_str = s.get("agent", "unknown")
            active = "✓" if s.get("active") else "✗"
            lines.append(f"{idx}. [{sid[:8]}] {path_str} ({agent_str}) {active}")

        yield event.plain_result("\n".join(lines))

    @filter.llm_tool(
        name="hapi_coding_message_history",
        description="查询当前交互中的 session 的历史消息",
        parameters={
            "type": "object",
            "properties": {
                "rounds": {"type": "integer", "description": "查询最近几轮消息（默认 1 轮）"}
            },
            "required": []
        }
    )
    async def tool_message_history(self, event: AstrMessageEvent, rounds: int = 1):
        '''查询当前交互中的 session 的历史消息。

        Args:
            rounds(number): 查询最近几轮消息（默认 1 轮）
        '''
        sid = self.state_mgr.current_sid(event)
        if not sid:
            yield event.plain_result("当前窗口未绑定 session")
            return

        limit = max(1, min(rounds * 2, 20))
        ok, data = await session_ops.get_messages(self.client, sid, limit)
        if not ok:
            yield event.plain_result(f"获取消息失败: {data}")
            return

        messages = data.get("messages", [])
        if not messages:
            yield event.plain_result("暂无消息记录")
            return

        lines = [f"最近 {len(messages)} 条消息:"]
        for msg in reversed(messages[-limit:]):
            role = msg.get("role", "?")
            content = msg.get("content", {})
            preview = formatters.extract_text_preview({"content": content}, max_len=100)
            if preview:
                lines.append(f"[{role}]: {preview}")

        yield event.plain_result("\n".join(lines))

    @filter.llm_tool(
        name="hapi_coding_get_config_status",
        description="获取当前插件配置状态及可修改项说明",
        parameters={"type": "object", "properties": {}, "required": []}
    )
    async def tool_get_config_status(self, event: AstrMessageEvent):
        '''获取当前插件配置状态及可修改项说明。'''
        output_level = self.plugin.config.get("output_level", "simple")
        auto_approve = self.plugin.sse_listener._auto_approve_enabled
        auto_start = self.plugin.sse_listener._auto_approve_start
        auto_end = self.plugin.sse_listener._auto_approve_end
        remind = self.plugin.sse_listener._remind_enabled
        remind_interval = self.plugin.sse_listener._remind_interval
        quick_prefix = self.plugin.config.get("quick_prefix", ">")
        summary_msg_count = self.plugin._summary_msg_count

        info = f"""当前配置状态:

output_level (SSE推送级别): {output_level}
  - silence: 仅推送权限请求和任务完成提醒
  - summary: 任务完成时推送最近的 agent 消息
  - simple: 仅推送 agent 文本消息，不包含复杂的工具调用信息
  - detail: 实时推送所有新消息（信息量较大）

auto_approve_enabled (忙时自动审批): {'开启' if auto_approve else '关闭'}
  时间段: {auto_start} - {auto_end}
  值: true/false

remind_pending (定时提醒待审批): {'开启' if remind else '关闭'}
  间隔: {remind_interval} 秒
  值: true/false

quick_prefix (快捷前缀): {quick_prefix}
  用于快速发送消息，如 "> 消息内容"

summary_msg_count (summary模式消息数): {summary_msg_count}
  summary 模式下推送的消息条数"""
        yield event.plain_result(info)

    @filter.llm_tool(
        name="hapi_coding_list_commands",
        description="列出所有可用的 HAPI 指令",
        parameters={"type": "object", "properties": {}, "required": []}
    )
    async def tool_list_commands(self, event: AstrMessageEvent):
        '''列出所有可用的 HAPI 指令。'''
        async for result in self.plugin.cmd_handlers.cmd_help(event, ""):
            yield result

    # ──── 操作类工具（需要审批）────

    @filter.llm_tool(
        name="hapi_coding_send_message",
        description="向当前 session 发送消息",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "要发送的消息内容"}
            },
            "required": ["message"]
        }
    )
    async def tool_send_message(self, event: AstrMessageEvent, message: str):
        '''向当前 session 发送消息。

        Args:
            message(string): 要发送的消息内容
        '''
        sid = self.state_mgr.current_sid(event)
        if not sid:
            yield event.plain_result("当前窗口未绑定 session")
            return

        # 请求审批
        if not await self._require_approval("hapi_coding_send_message", {"message": message}, event):
            yield event.plain_result("操作已被拒绝")
            return

        # 执行发送
        ok, result = await session_ops.send_message(self.client, sid, message)
        yield event.plain_result(result if ok else f"发送失败: {result}")

    @filter.llm_tool(
        name="hapi_coding_switch_session",
        description="切换到指定的 session",
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "session 序号（如 \"1\"）或 session ID（如 \"abc12345\"）"}
            },
            "required": ["target"]
        }
    )
    async def tool_switch_session(self, event: AstrMessageEvent, target: str):
        '''切换到指定的 session。

        Args:
            target(string): session 序号（如 "1"）或 session ID（如 "abc12345"）
        '''
        # 请求审批
        if not await self._require_approval("hapi_coding_switch_session", {"target": target}, event):
            yield event.plain_result("操作已被拒绝")
            return

        # 复用 cmd_sw 逻辑
        async for result in self.plugin.cmd_handlers.cmd_sw(event, target):
            yield result

    @filter.llm_tool(
        name="hapi_coding_create_session",
        description="创建新的 coding session",
        parameters={
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "工作目录路径"},
                "agent": {"type": "string", "description": "代理类型（claude/codex/gemini/opencode）"},
                "machine_id": {"type": "string", "description": "机器 ID（可选，管理多机器时必填）"},
                "session_type": {"type": "string", "description": "session 类型（simple/worktree，默认 simple）"},
                "yolo": {"type": "boolean", "description": "是否自动批准所有权限（默认 false）"}
            },
            "required": ["directory", "agent"]
        }
    )
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
            yield event.plain_result(f"获取机器列表失败: {e}")
            return

        if not machines:
            yield event.plain_result("没有在线的机器")
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
                yield event.plain_result("\n".join(lines))
                return

        # 请求审批
        if not await self._require_approval("hapi_coding_create_session",
                                           {"machine_id": machine_id, "directory": directory,
                                            "agent": agent, "session_type": session_type, "yolo": yolo}, event):
            yield event.plain_result("操作已被拒绝")
            return

        # 执行创建
        ok, msg, sid = await session_ops.spawn_session(self.client, machine_id, directory, agent, session_type, yolo)
        if ok and sid:
            await self.state_mgr.capture_window(sid, event.unified_msg_origin, agent)
            yield event.plain_result(f"✅ 已创建 session: {sid[:8]}")
        else:
            yield event.plain_result(f"创建失败: {msg}")

    @filter.llm_tool(
        name="hapi_coding_change_config",
        description="修改插件配置项。必须先调用 hapi_coding_get_config_status 查看可修改项",
        parameters={
            "type": "object",
            "properties": {
                "config_name": {"type": "string", "description": "配置项名称"},
                "value": {"type": "string", "description": "新值"}
            },
            "required": ["config_name", "value"]
        }
    )
    async def tool_change_config(self, event: AstrMessageEvent, config_name: str, value: str):
        '''修改插件配置项。必须先调用 hapi_coding_get_config_status 查看可修改项。

        Args:
            config_name(string): 配置项名称
            value(string): 新值
        '''
        # 请求审批
        if not await self._require_approval("hapi_coding_change_config",
                                           {"config_name": config_name, "value": value}, event):
            yield event.plain_result("操作已被拒绝")
            return

        # 执行修改
        if config_name == "output_level":
            if value not in ["silence", "summary", "simple", "detail"]:
                yield event.plain_result("output_level 只能是 silence/summary/simple/detail")
                return
            self.plugin.sse_listener.output_level = value
            self.plugin.config["output_level"] = value
            yield event.plain_result(f"✅ 已设置 {config_name} = {value}")
        elif config_name == "auto_approve_enabled":
            bool_val = value.lower() in ["true", "1", "yes", "on", "开启"]
            self.plugin.sse_listener._auto_approve_enabled = bool_val
            yield event.plain_result(f"✅ 已设置 {config_name} = {bool_val}")
        elif config_name == "remind_pending":
            bool_val = value.lower() in ["true", "1", "yes", "on", "开启"]
            self.plugin.sse_listener._remind_enabled = bool_val
            yield event.plain_result(f"✅ 已设置 {config_name} = {bool_val}")
        elif config_name == "quick_prefix":
            self.plugin._quick_prefix = value
            self.plugin.config["quick_prefix"] = value
            yield event.plain_result(f"✅ 已设置 {config_name} = {value}")
        elif config_name == "summary_msg_count":
            try:
                count = int(value)
                self.plugin._summary_msg_count = count
                self.plugin.config["summary_msg_count"] = count
                yield event.plain_result(f"✅ 已设置 {config_name} = {count}")
            except ValueError:
                yield event.plain_result("summary_msg_count 必须是数字")
        else:
            yield event.plain_result(f"不支持的配置项: {config_name}，请先调用 hapi_coding_get_config_status 查看可用配置")

    @filter.llm_tool(
        name="hapi_coding_stop_message",
        description="停止当前 session 的消息生成",
        parameters={"type": "object", "properties": {}, "required": []}
    )
    async def tool_stop_message(self, event: AstrMessageEvent):
        '''停止当前 session 的消息生成。'''
        sid = self.state_mgr.current_sid(event)
        if not sid:
            yield event.plain_result("当前窗口未绑定 session")
            return

        # 请求审批
        if not await self._require_approval("hapi_coding_stop_message", {"session_id": sid[:8]}, event):
            yield event.plain_result("操作已被拒绝")
            return

        # 执行停止
        ok, msg = await session_ops.abort_session(self.client, sid)
        if ok:
            await self.plugin._refresh_sessions()
        yield event.plain_result(msg)

    @filter.llm_tool(
        name="hapi_coding_execute_command",
        description="直接执行 HAPI 指令。在使用前请务必调用 hapi_coding_list_commands 查看指令格式和参数说明，错误的指令可能导致不可预料的后果",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "完整的 /hapi 指令（不含 /hapi 前缀）"}
            },
            "required": ["command"]
        }
    )
    async def tool_execute_command(self, event: AstrMessageEvent, command: str):
        '''直接执行 HAPI 指令。在使用前请务必调用 hapi_coding_list_commands 查看指令格式和参数说明，错误的指令可能导致不可预料的后果。

        Args:
            command(string): 完整的 /hapi 指令（不含 /hapi 前缀）
        '''
        # 请求审批
        if not await self._require_approval("hapi_coding_execute_command", {"command": command}, event):
            yield event.plain_result("操作已被拒绝")
            return

        # 执行命令
        async for result in self.plugin.cmd_handlers.cmd_hapi_router(event, command):
            yield result

