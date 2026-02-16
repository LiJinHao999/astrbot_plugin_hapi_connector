"""后台 SSE 事件监听 + 推送通知"""

import copy
import json
import asyncio
from collections.abc import Callable, Awaitable

from astrbot.api import logger

from .hapi_client import AsyncHapiClient
from .formatters import extract_text_preview, session_label_short, format_request_detail, format_agent_line
from . import session_ops


class SSEListener:
    """后台 SSE 监听，实时捕获权限请求、等待输入、任务完成等事件"""

    def __init__(self, client: AsyncHapiClient, sessions_cache: list[dict],
                 notify_callback: Callable[[str, str], Awaitable[None]]):
        self.client = client
        self.sessions_cache = sessions_cache
        self.notify_callback = notify_callback
        self.output_level: str = "summary"
        # {session_id: {request_id: {tool, arguments, ...}}}
        self.pending: dict[str, dict] = {}
        # 跟踪 session 状态以检测变化
        self.session_states: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    def start(self, output_level: str = "summary"):
        """启动 SSE 监听任务"""
        self.output_level = output_level
        self._debounce_sids: set[str] = set()
        self._debounce_task: asyncio.Task | None = None
        self._task = asyncio.create_task(self._listen_loop())

    async def stop(self):
        """停止 SSE 监听"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def get_all_pending(self) -> dict[str, dict]:
        """返回所有 session 的待审批请求（同步读取快照）"""
        return copy.deepcopy(self.pending)

    async def _listen_loop(self):
        """主循环：SSE 监听 + 指数退避重连"""
        backoff = 1
        max_backoff = 60

        while True:
            resp = None
            try:
                resp = await self.client.subscribe_events_raw(all_events=True)
                backoff = 1  # 连接成功，重置退避

                while True:
                    line_bytes = await resp.content.readline()
                    if not line_bytes:
                        break  # 连接关闭
                    line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                    if not line or not line.startswith("data: "):
                        continue
                    try:
                        evt = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    await self._handle(evt)

            except asyncio.CancelledError:
                logger.info("SSE 监听已取消")
                return
            except Exception as e:
                logger.warning("SSE 断线: %s, %ds 后重连", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
            finally:
                if resp is not None:
                    resp.close()

    async def _handle(self, evt: dict):
        """处理单个 SSE 事件"""
        etype = evt.get("type")
        if etype != "session-updated":
            return

        sid = evt.get("sessionId", "")
        data = evt.get("data", {})
        agent_state = data.get("agentState")

        # 更新缓存中的 session 数据
        self._update_session_cache(sid, data)

        # 从旧状态或事件数据中获取当前状态
        async with self._lock:
            old_state = self.session_states.get(sid, {})

            is_active = data.get("active") if "active" in data else old_state.get("active", False)
            is_thinking = data.get("thinking") if "thinking" in data else old_state.get("thinking", False)
            old_thinking = old_state.get("thinking", False)
            old_seq = old_state.get("lastSeq", -1)

            # 如果是第一次遇到这个 session，初始化 lastSeq
            if old_seq == -1:
                old_seq = await self._get_latest_seq(sid)

            self.session_states[sid] = {
                "active": is_active,
                "thinking": is_thinking,
                "lastSeq": old_seq,
            }

        # 处理权限请求
        if agent_state:
            requests_data = agent_state.get("requests") or {}
            async with self._lock:
                old_reqs = self.pending.get(sid, {})
                new_ids = set(requests_data.keys()) - set(old_reqs.keys())
                if requests_data:
                    self.pending[sid] = requests_data
                elif sid in self.pending:
                    del self.pending[sid]

            # 有新的权限请求 -> 推送提醒
            for rid in new_ids:
                req = requests_data[rid]
                detail = format_request_detail(req)
                label = session_label_short(sid, self.sessions_cache)
                total = sum(len(r) for r in self.pending.values())
                lines = [
                    f"⚠ 权限请求 {label}",
                    f"  {detail}",
                    "",
                    f"当前共 {total} 个待审批，审批指令:",
                    "  /hapi a        全部批准",
                    "  /hapi a <序号>  批准单个",
                    "  /hapi deny     全部拒绝",
                    "  /hapi deny <序号> 拒绝单个",
                    "  /hapi pending   查看完整列表",
                ]
                await self._push_notification("\n".join(lines), sid)

        # === 输出级别处理 ===

        # detail/simple 模式：防抖，合并短时间内的事件一次性拉取
        if self.output_level in ("detail", "simple") and old_seq >= 0:
            if is_active or is_thinking:
                self._debounce_sids.add(sid)
                if self._debounce_task is None or self._debounce_task.done():
                    self._debounce_task = asyncio.create_task(self._debounced_fetch())

        # 所有模式都提醒：等待输入（在内容输出之后）
        if is_active and old_thinking and not is_thinking:
            pending_count = len(self.pending.get(sid, {}))
            if pending_count == 0:
                label = session_label_short(sid, self.sessions_cache)
                await self._push_notification(f"✅ 任务已完成，等待新的输入 {label}", sid)

    async def _get_latest_seq(self, sid: str) -> int:
        """获取 session 当前的最新消息序号"""
        try:
            messages = await session_ops.fetch_messages(self.client, sid, limit=1)
            if messages:
                return messages[0].get("seq", 0)
        except Exception:
            pass
        return 0

    def _update_session_cache(self, sid: str, updated_data: dict):
        """实时更新缓存中的 session 数据"""
        cache = self.sessions_cache
        if not cache:
            return

        for s in cache:
            if s.get("id") == sid:
                if "active" in updated_data and updated_data["active"] is not None:
                    s["active"] = updated_data["active"]
                if "thinking" in updated_data and updated_data["thinking"] is not None:
                    s["thinking"] = updated_data["thinking"]
                if "metadata" in updated_data:
                    s.setdefault("metadata", {}).update(updated_data["metadata"])
                if "pendingRequestsCount" in updated_data:
                    s["pendingRequestsCount"] = updated_data["pendingRequestsCount"]
                break

    async def _debounced_fetch(self):
        """等一小段时间再拉取，合并密集的 SSE 事件"""
        await asyncio.sleep(0.5)
        sids = list(self._debounce_sids)
        self._debounce_sids.clear()
        for sid in sids:
            async with self._lock:
                old_seq = self.session_states.get(sid, {}).get("lastSeq", -1)
            if old_seq >= 0:
                if self.output_level == "detail":
                    await self._show_detail(sid, old_seq)
                elif self.output_level == "simple":
                    await self._show_simple(sid, old_seq)

    async def _show_detail(self, sid: str, old_seq: int):
        """detail 模式：获取并显示所有新消息（使用统一格式）"""
        try:
            messages = await session_ops.fetch_messages(self.client, sid, limit=20)
            if not messages:
                return

            # 找出新消息（seq > old_seq），过滤掉用户消息
            new_msgs = [
                m for m in messages
                if m.get("seq", 0) > old_seq
                and m.get("content", {}).get("role") != "user"
            ]

            visible_msgs = []
            for msg in new_msgs:
                content = msg.get("content", {})
                text = extract_text_preview(content, max_len=500)
                if text is not None:
                    visible_msgs.append((msg, text))

            # 更新 lastSeq
            latest_seq = max(m.get("seq", 0) for m in messages)
            async with self._lock:
                if sid in self.session_states:
                    self.session_states[sid]["lastSeq"] = latest_seq

            if not visible_msgs:
                return

            label = session_label_short(sid, self.sessions_cache)

            if len(visible_msgs) == 1:
                msg, text = visible_msgs[0]
                output = f"{label}\n{format_agent_line(text)}"
            else:
                lines = [f"━━━ {label} — {len(visible_msgs)} 条新消息 ━━━"]
                for msg, text in sorted(visible_msgs, key=lambda x: x[0].get("seq", 0)):
                    lines.append(format_agent_line(text))
                lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                output = "\n\n".join(lines)

            await self._push_notification(output, sid)

        except Exception as e:
            logger.warning("detail 模式获取消息异常: %s", e)

    async def _show_simple(self, sid: str, old_seq: int):
        """simple 模式：获取并显示新的 agent 纯文本消息"""
        try:
            messages = await session_ops.fetch_messages(self.client, sid, limit=50)
            if not messages:
                return

            # 筛选: seq > old_seq、agent 角色、有文本内容、不以 [ 开头（排除工具调用/返回等）
            agent_texts = []
            for msg in messages:
                if msg.get("seq", 0) <= old_seq:
                    continue
                content = msg.get("content", {})
                if content.get("role") != "agent":
                    continue
                text = extract_text_preview(content, max_len=0)
                if text is None or text.startswith("["):
                    continue
                agent_texts.append((msg, text))

            # 更新 lastSeq
            latest_seq = max(m.get("seq", 0) for m in messages)
            async with self._lock:
                if sid in self.session_states:
                    self.session_states[sid]["lastSeq"] = latest_seq

            if not agent_texts:
                return

            label = session_label_short(sid, self.sessions_cache)

            if len(agent_texts) == 1:
                _, text = agent_texts[0]
                output = f"{label}\n[Message]: {text}"
            else:
                lines = [f"━━━ {label} ━━━"]
                for _, text in agent_texts:
                    lines.append(f"[Message]: {text}")
                lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
                output = "\n\n".join(lines)

            await self._push_notification(output, sid)

        except Exception as e:
            logger.warning("simple 模式获取消息异常: %s", e)

    async def _push_notification(self, text: str, session_id: str):
        """通过回调向所有已注册的管理员推送消息"""
        await self.notify_callback(text, session_id)

    async def load_existing_pending(self):
        """启动时从已有 session 加载待审批请求"""
        for s in self.sessions_cache:
            sid = s.get("id", "")
            pending_count = s.get("pendingRequestsCount", 0)
            if not sid or not pending_count:
                continue
            try:
                detail = await session_ops.fetch_session_detail(self.client, sid)
                agent_state = detail.get("agentState") or {}
                requests_data = agent_state.get("requests") or {}
                if requests_data:
                    self.pending[sid] = requests_data
                    logger.info("加载 session %s 的 %d 个待审批请求",
                                sid[:8], len(requests_data))
            except Exception as e:
                logger.warning("加载 session %s 待审批失败: %s", sid[:8], e)
