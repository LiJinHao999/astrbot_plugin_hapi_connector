"""后台 SSE 事件监听 + 推送通知"""

import copy
import json
import asyncio
import datetime
from collections.abc import Callable, Awaitable

from astrbot.api import logger

from .hapi_client import AsyncHapiClient
from .formatters import (extract_text_preview, session_label_short, format_request_detail,
                         format_agent_line, is_question_request, format_question_notification)
from . import session_ops


class SSEListener:
    """后台 SSE 监听，实时捕获权限请求、等待输入、任务完成等事件"""

    def __init__(self, client: AsyncHapiClient, sessions_cache: list[dict],
                 notify_callback: Callable[[str, str], Awaitable[None]]):
        self.client = client
        self.sessions_cache = sessions_cache
        self.notify_callback = notify_callback
        self.output_level: str = "detail"
        # {session_id: {request_id: {tool, arguments, ...}}}
        self.pending: dict[str, dict] = {}
        # 跟踪 session 状态以检测变化
        self.session_states: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._remind_task: asyncio.Task | None = None
        self._remind_enabled: bool = False
        self._remind_interval: int = 180
        self._auto_approve_enabled: bool = False
        self._auto_approve_start: str = "23:00"
        self._auto_approve_end: str = "07:00"
        # {session_id: seq}，记录已触发通知的消息序号，防止重复
        self._compact_notified_seqs: dict[str, int] = {}
        # {session_id: seq}，记录已处理的压缩完成消息序号，防止重复发「继续」
        self._compaction_completed_seqs: dict[str, int] = {}

    def start(self, output_level: str = "summary", remind_pending: bool = False, remind_interval: int = 180,
              auto_approve_enabled: bool = False, auto_approve_start: str = "23:00", auto_approve_end: str = "07:00"):
        """启动 SSE 监听任务"""
        self.output_level = output_level
        self._remind_enabled = remind_pending
        self._remind_interval = remind_interval
        self._auto_approve_enabled = auto_approve_enabled
        self._auto_approve_start = auto_approve_start
        self._auto_approve_end = auto_approve_end
        self._debounce_sids: set[str] = set()
        self._debounce_task: asyncio.Task | None = None
        self._completion_sids: set[str] = set()
        self._completion_task: asyncio.Task | None = None
        self._compact_check_sids: set[str] = set()
        self._compact_check_task: asyncio.Task | None = None
        self._task = asyncio.create_task(self._listen_loop())

    async def stop(self):
        """停止 SSE 监听"""
        for task in (self._task, self._remind_task,
                     getattr(self, '_debounce_task', None),
                     getattr(self, '_completion_task', None),
                     getattr(self, '_compact_check_task', None)):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._task = None
        self._remind_task = None

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

                buf = b""
                while True:
                    chunk = await resp.content.read(1024 * 1024)
                    if not chunk:
                        break  # 连接关闭
                    buf += chunk
                    while b"\n" in buf:
                        line_bytes, buf = buf.split(b"\n", 1)
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
                    await resp.release()

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
        new_ids: set = set()
        if agent_state:
            requests_data = agent_state.get("requests") or {}
            async with self._lock:
                old_reqs = self.pending.get(sid, {})
                new_ids = set(requests_data.keys()) - set(old_reqs.keys())
                if requests_data:
                    self.pending[sid] = requests_data
                elif sid in self.pending:
                    del self.pending[sid]

            # 有新的权限请求 -> 推送提醒（或忙时自动批准）
            for rid in new_ids:
                req = requests_data[rid]
                label = session_label_short(sid, self.sessions_cache)
                async with self._lock:
                    total = sum(len(r) for r in self.pending.values())

                if self._auto_approve_enabled and self._in_auto_approve_window() and not is_question_request(req):
                    # 忙时托管审批：自动批准非 question 请求
                    ok, _ = await session_ops.approve_permission(self.client, sid, rid)
                    tool = req.get("tool", "?")
                    result_mark = "✓" if ok else "✗"
                    notify_msg = f"[忙时托管审批] 已自动批准\n{label}\n  {result_mark} {tool}"
                    await self._push_notification(notify_msg, sid)
                else:
                    if is_question_request(req):
                        msg = format_question_notification(req, label, total)
                    else:
                        detail = format_request_detail(req)
                        lines = [
                            f"⚠ 权限请求\n{label}",
                            f"  {detail}",
                            "",
                            f"当前共 {total} 个待审批，审批指令:",
                            "  /hapi a        全部批准",
                            "  /hapi allow <序号>  批准单个",
                            "  /hapi deny     全部拒绝",
                            "  /hapi deny <序号> 拒绝单个",
                            "  /hapi pending   查看完整列表",
                        ]
                        msg = "\n".join(lines)
                    await self._push_notification(msg, sid)

        # 有新请求 → 启动一次性提醒倒计时（如未启动）
        if new_ids and self._remind_enabled:
            if self._remind_task is None or self._remind_task.done():
                self._remind_task = asyncio.create_task(self._remind_once())

        # pending 已全部清空 → 取消提醒倒计时
        async with self._lock:
            pending_empty = not self.pending
        if pending_empty and self._remind_task and not self._remind_task.done():
            self._remind_task.cancel()

        # === 输出级别处理 ===

        # detail/simple 模式：防抖，合并短时间内的事件一次性拉取
        if self.output_level in ("detail", "simple") and old_seq >= 0:
            if is_active or is_thinking:
                self._debounce_sids.add(sid)
                if self._debounce_task is None or self._debounce_task.done():
                    self._debounce_task = asyncio.create_task(self._debounced_fetch())

        # 所有模式都提醒：等待输入（防抖，避免 Codex 频繁切换 thinking 状态导致重复推送）
        if is_active and old_thinking and not is_thinking:
            async with self._lock:
                pending_count = len(self.pending.get(sid, {}))
            if pending_count == 0:
                self._completion_sids.add(sid)
                if self._completion_task is None or self._completion_task.done():
                    self._completion_task = asyncio.create_task(self._debounced_completion())

        # silence 模式：单独检测 Prompt is too long（detail/simple 模式在 _show_* 里检测）
        if self.output_level == "silence" and old_seq >= 0 and (is_active or is_thinking):
            self._compact_check_sids.add(sid)
            if self._compact_check_task is None or self._compact_check_task.done():
                self._compact_check_task = asyncio.create_task(self._debounced_compact_check())

    async def _get_latest_seq(self, sid: str) -> int:
        """获取 session 当前的最新消息序号"""
        try:
            messages = await session_ops.fetch_messages(self.client, sid, limit=1)
            if messages:
                return messages[0].get("seq", 0)
        except Exception as e:
            logger.warning(f"获取最新序号失败: {e}")
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

    async def _debounced_completion(self):
        """防抖：等待状态稳定后再推送任务完成通知（避免 Codex 频繁切换 thinking 导致重复推送）"""
        await asyncio.sleep(1.5)
        sids = list(self._completion_sids)
        self._completion_sids.clear()
        for sid in sids:
            async with self._lock:
                state = self.session_states.get(sid, {})
                has_pending = len(self.pending.get(sid, {})) > 0
            if not state.get("thinking", False) and not has_pending:
                label = session_label_short(sid, self.sessions_cache)
                await self._push_notification(f"✅ 任务已完成，等待新的输入\n{label}", sid)

    async def _check_and_handle_compact(self, sid: str, messages: list[dict], old_seq: int):
        """检测新消息中是否含 Prompt is too long 或 Compaction completed，触发对应流程"""
        last_notified = self._compact_notified_seqs.get(sid, -1)
        last_completed = self._compaction_completed_seqs.get(sid, -1)
        triggered_compact = False

        for msg in messages:
            seq = msg.get("seq", 0)
            if seq <= old_seq:
                continue
            content = msg.get("content", {})
            text = extract_text_preview(content, max_len=0)
            if text is None:
                continue
            text_lower = text.lower()

            # 检测 Prompt is too long → 触发压缩流程（每次只处理一条）
            if not triggered_compact and seq > last_notified and "prompt is too long" in text_lower:
                triggered_compact = True
                self._compact_notified_seqs[sid] = seq
                label = session_label_short(sid, self.sessions_cache)
                if self._auto_approve_enabled and self._in_auto_approve_window():
                    ok, _ = await session_ops.send_message(self.client, sid, "/compact")
                    mark = "✓" if ok else "✗"
                    await self._push_notification(
                        f"[忙时托管审批] 已自动压缩上下文\n{label}\n  {mark} /compact", sid)
                else:
                    async with self._lock:
                        self.pending.setdefault(sid, {})["__compact__"] = {
                            "tool": "__compact__", "arguments": {}}
                        total = sum(len(r) for r in self.pending.values())
                    lines = [
                        f"⚠ 上下文过长\n{label}",
                        "  压缩上下文 (/compact)",
                        "",
                        f"当前共 {total} 个待审批，审批指令:",
                        "  /hapi a        全部批准",
                        "  /hapi deny     取消",
                        "  /hapi pending  查看完整列表",
                    ]
                    await self._push_notification("\n".join(lines), sid)

            # 检测 Compaction completed → 自动发送「继续」恢复会话
            if seq > last_completed and "compaction completed" in text_lower:
                self._compaction_completed_seqs[sid] = seq
                label = session_label_short(sid, self.sessions_cache)
                ok, _ = await session_ops.send_message(self.client, sid, "继续")
                mark = "✓" if ok else "✗"
                await self._push_notification(
                    f"[上下文压缩完成] 已自动发送「继续」\n{label}\n  {mark}", sid)

    async def _debounced_compact_check(self):
        """silence 模式下防抖检测 Prompt is too long"""
        await asyncio.sleep(0.5)
        sids = list(self._compact_check_sids)
        self._compact_check_sids.clear()
        for sid in sids:
            async with self._lock:
                old_seq = self.session_states.get(sid, {}).get("lastSeq", -1)
            if old_seq < 0:
                continue
            try:
                messages = await session_ops.fetch_messages(self.client, sid, limit=5)
                if not messages:
                    continue
                latest_seq = max(m.get("seq", 0) for m in messages)
                async with self._lock:
                    if sid in self.session_states:
                        self.session_states[sid]["lastSeq"] = latest_seq
                await self._check_and_handle_compact(sid, messages, old_seq)
            except Exception as e:
                logger.warning("compact 检测失败 (sid=%s): %s", sid[:8], e)

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
                text = extract_text_preview(content, max_len=0)
                if text is not None:
                    visible_msgs.append((msg, text))

            # 更新 lastSeq
            latest_seq = max(m.get("seq", 0) for m in messages)
            async with self._lock:
                if sid in self.session_states:
                    self.session_states[sid]["lastSeq"] = latest_seq

            # 检测 Prompt is too long
            await self._check_and_handle_compact(sid, messages, old_seq)

            if not visible_msgs:
                return

            label = session_label_short(sid, self.sessions_cache)

            if len(visible_msgs) == 1:
                msg, text = visible_msgs[0]
                output = f"{label}\n{format_agent_line(text)}"
            else:
                lines = [f"{label}\n━━━ {len(visible_msgs)} 条新消息 ━━━"]
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
                if content.get("role") not in ("agent", "assistant"):
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

            # 检测 Prompt is too long
            await self._check_and_handle_compact(sid, messages, old_seq)

            if not agent_texts:
                return

            label = session_label_short(sid, self.sessions_cache)

            if len(agent_texts) == 1:
                _, text = agent_texts[0]
                output = f"{label}\n[Message]: {text}"
            else:
                lines = [f"{label}\n━━━ {len(agent_texts)} 条新消息 ━━━"]
                for _, text in agent_texts:
                    lines.append(f"[Message]: {text}")
                lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
                output = "\n\n".join(lines)

            await self._push_notification(output, sid)

        except Exception as e:
            logger.warning("simple 模式获取消息异常: %s", e)

    async def _remind_once(self):
        """倒计时结束后，若仍有待审批请求则发一次提醒"""
        try:
            await asyncio.sleep(self._remind_interval)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if not self.pending:
                return
            total = sum(len(r) for r in self.pending.values())
        lines = [
            f"⏰ 提醒：仍有 {total} 个待审批请求，请及时处理以避免会话缓存失效",
            "  /hapi a        全部批准",
            "  /hapi pending  查看列表",
        ]
        await self._push_notification("\n".join(lines), "")

    def _in_auto_approve_window(self) -> bool:
        """判断当前本地时间是否在忙时托管审批时间窗口内"""
        try:
            now = datetime.datetime.now().time()
            h_s, m_s = map(int, self._auto_approve_start.split(":"))
            h_e, m_e = map(int, self._auto_approve_end.split(":"))
            start = datetime.time(h_s, m_s)
            end = datetime.time(h_e, m_e)
            if start <= end:
                return start <= now <= end
            else:  # 跨午夜，如 23:00 ~ 07:00
                return now >= start or now <= end
        except Exception:
            return False

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
                    async with self._lock:
                        self.pending[sid] = requests_data
                    logger.info("加载 session %s 的 %d 个待审批请求",
                                sid[:8], len(requests_data))
            except Exception as e:
                logger.warning("加载 session %s 待审批失败: %s", sid[:8], e)
