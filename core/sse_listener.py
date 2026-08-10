"""后台 SSE 事件监听 + 推送通知"""

import copy
import json
import asyncio
import datetime
from collections.abc import Callable, Awaitable

from astrbot.api import logger

from .hapi_client import AsyncHapiClient, ContentTypeError
from ..render import formatters
from ..render.formatters import (extract_text_preview, session_label_short, format_request_detail,
                         format_agent_line, is_question_request, format_question_notification,
                         format_permission_notification)
from . import session_ops


class SSEListener:
    """后台 SSE 监听，实时捕获权限请求、等待输入、任务完成等事件"""

    def __init__(self, client: AsyncHapiClient, sessions_cache: list[dict],
                 notify_callback: Callable[[str, str], Awaitable[None]]):
        self.client = client
        self.sessions_cache = sessions_cache
        self.notify_callback = notify_callback
        # 可选：插件引用，用于推送呈现（卡片/对话渲染）。由 main 注入。
        self.plugin = None
        self.output_level: str = "detail"
        # {session_id: {request_id: {tool, arguments, ...}}}
        self.pending: dict[str, dict] = {}
        # 序号管理：空闲序号池
        self._free_indices: set[int] = set()
        self._max_index: int = 0
        # 跟踪 session 状态以检测变化
        self.session_states: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        # 上次 SSE 连接错误描述，None 表示尚无错误记录
        self.conn_error: str | None = None
        # 连续失败计数（内存，重启归零）
        self.conn_fail_count: int = 0
        # 最大重连次数（0 表示无限）
        self._max_reconnect: int = 0
        # 是否已休眠（达到重连上限）
        self._hibernated: bool = False
        # 当前是否正持有可用的 SSE 流（读到数据后置 True，断线/停任务置 False）
        self._stream_live: bool = False
        self._task: asyncio.Task | None = None
        self._remind_task: asyncio.Task | None = None
        self._remind_enabled: bool = False
        self._remind_interval: int = 180
        self._auto_approve_enabled: bool = False
        self._auto_approve_start: str = "23:00"
        self._auto_approve_end: str = "07:00"
        # 忙时托管静默汇总（dev-docs/auto-approve-silent-summary.md）
        self._auto_approve_silent: bool = False
        self._auto_approve_summary_mode: str = "window"
        self._auto_approve_summary_push: str = "on_window_end"
        self._auto_approve_summary_time: str = "08:00"
        self._auto_approve_summary_include_failures: bool = True
        self._auto_approve_summary_max_detail_lines: int = 30
        # 汇总服务（main 组装后注入；append 前必须已就绪）
        self.summary_service = None
        self._summary_msg_count: int = 5
        # {session_id: seq}，记录已触发通知的消息序号，防止重复
        self._compact_notified_seqs: dict[str, int] = {}
        # {session_id: seq}，记录已推送的新消息最大可见序号，防止重复通知
        self._message_notified_seqs: dict[str, int] = {}
        # {session_id: seq}，记录已处理的压缩完成消息序号，防止重复发「继续」
        self._compaction_completed_seqs: dict[str, int] = {}
        # {session_id: seq}，记录已发送“任务完成”通知时的 lastSeq，防止状态抖动重复提醒
        self._completion_notified_seqs: dict[str, int] = {}
        # {session_id: [text|dict, ...]}，短暂排队权限类通知，先补普通消息再发送
        self._queued_request_notifications: dict[str, list] = {}
        self._request_notify_sids: set[str] = set()
        self._request_notify_task: asyncio.Task | None = None

    def start(self, output_level: str = "summary", remind_pending: bool = False, remind_interval: int = 180,
              auto_approve_enabled: bool = False, auto_approve_start: str = "23:00", auto_approve_end: str = "07:00",
              summary_msg_count: int = 5, max_reconnect_attempts: int = 0,
              auto_approve_silent: bool = False, auto_approve_summary_mode: str = "window",
              auto_approve_summary_push: str = "on_window_end", auto_approve_summary_time: str = "08:00",
              auto_approve_summary_include_failures: bool = True,
              auto_approve_summary_max_detail_lines: int = 30):
        """启动 SSE 监听任务"""
        self.output_level = output_level
        self._summary_msg_count = summary_msg_count
        self._remind_enabled = remind_pending
        self._remind_interval = remind_interval
        self._auto_approve_enabled = auto_approve_enabled
        self._auto_approve_start = auto_approve_start
        self._auto_approve_end = auto_approve_end
        self._auto_approve_silent = auto_approve_silent
        self._auto_approve_summary_mode = auto_approve_summary_mode
        self._auto_approve_summary_push = auto_approve_summary_push
        self._auto_approve_summary_time = auto_approve_summary_time
        self._auto_approve_summary_include_failures = auto_approve_summary_include_failures
        self._auto_approve_summary_max_detail_lines = auto_approve_summary_max_detail_lines
        self._max_reconnect = max_reconnect_attempts
        self._debounce_sids: set[str] = set()
        self._debounce_task: asyncio.Task | None = None
        self._completion_sids: set[str] = set()
        self._completion_task: asyncio.Task | None = None
        self._compact_check_sids: set[str] = set()
        self._compact_check_task: asyncio.Task | None = None
        self._queued_request_notifications = {}
        self._request_notify_sids = set()
        self._request_notify_task = None
        if self._task and not self._task.done():
            logger.info("SSE 监听已在运行，跳过重复启动")
            return
        self._hibernated = False
        self._stream_live = False
        self._task = asyncio.create_task(self._listen_loop())

    async def stop(self):
        """停止 SSE 监听"""
        self._stream_live = False
        # 防漏发：停止前把未推送的托管汇总补发（need_flush 去重）
        summary_svc = getattr(self, "summary_service", None)
        if summary_svc is not None:
            try:
                if summary_svc.has_pending():
                    await summary_svc.flush_all()
            except Exception as e:
                logger.warning("SSE stop 时托管汇总 flush 失败: %s", e)
        for task in (self._task, self._remind_task,
                     getattr(self, '_debounce_task', None),
                     getattr(self, '_completion_task', None),
                     getattr(self, '_compact_check_task', None),
                     getattr(self, '_request_notify_task', None)):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._task = None
        self._remind_task = None
        self._request_notify_task = None
        self._queued_request_notifications = {}
        self._request_notify_sids.clear()

    def wake_up(self):
        """唤醒休眠的监听器（重置失败计数并重启任务）"""
        if self._hibernated:
            self._hibernated = False
            self.conn_fail_count = 0
            self.conn_error = None
            self._stream_live = False
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._listen_loop())
                logger.info("SSE 监听器已唤醒，重新开始连接")

    def get_connection_status(self) -> dict:
        """供 WebUI / 诊断使用的连接状态快照（不暴露私有字段直接读写）。

        状态语义（插件侧 SSE 任务，不是浏览器直连 HAPI）：
        - connected: 任务在跑且已成功读到 SSE 流
        - reconnecting: 任务在跑但未建立流，或刚失败正在退避重试
        - hibernated: 达到重连上限，已停任务
        - disconnected: 无监听任务（未 initialize / 已 stop）
        """
        task_running = bool(self._task and not self._task.done())
        stream_live = bool(getattr(self, "_stream_live", False))
        if self._hibernated:
            sse_status = "hibernated"
        elif task_running and stream_live and self.conn_fail_count == 0:
            sse_status = "connected"
        elif task_running:
            sse_status = "reconnecting"
        else:
            sse_status = "disconnected"
        return {
            "sse_status": sse_status,
            "conn_fail_count": self.conn_fail_count,
            "conn_error": self.conn_error,
            "hibernated": self._hibernated,
            "task_running": task_running,
            "stream_live": stream_live,
            "output_level": self.output_level,
            "max_reconnect_attempts": self._max_reconnect,
        }

    def get_all_pending(self) -> dict[str, dict]:
        """返回所有 session 的待审批请求（同步读取快照）"""
        # 移除 Future 后再浅拷贝（避免 deepcopy 整表）
        result = {}
        for sid, reqs in self.pending.items():
            result[sid] = {}
            for rid, req in reqs.items():
                req_copy = req.copy()
                req_copy.pop("future", None)
                result[sid][rid] = req_copy
        return result

    def pending_counts(self) -> dict[str, int]:
        """仅计数，WebUI snapshot 用，避免拷贝 pending 内容。"""
        return {sid: len(reqs) for sid, reqs in self.pending.items() if reqs}

    def prune_stale_session_maps(self, live_ids: set[str] | None = None):
        """清理已不存在 session 的序号记忆，控制内存。不碰 pending（审批仍需）。"""
        if live_ids is None:
            live_ids = {s.get("id") for s in self.sessions_cache if s.get("id")}
        maps = (
            self._compact_notified_seqs,
            self._message_notified_seqs,
            self._compaction_completed_seqs,
            self._completion_notified_seqs,
        )
        for m in maps:
            for sid in list(m.keys()):
                if sid not in live_ids:
                    m.pop(sid, None)
        # 排队通知里的孤儿 sid
        for sid in list(self._queued_request_notifications.keys()):
            if sid not in live_ids:
                self._queued_request_notifications.pop(sid, None)
        self._request_notify_sids &= live_ids if live_ids else set()

    async def _listen_loop(self):
        """主循环：SSE 监听 + 指数退避重连"""
        backoff = 1
        max_backoff = 60

        while True:
            resp = None
            got_data = False
            try:
                resp = await self.client.subscribe_events_raw(all_events=True)

                buf = b""
                while True:
                    chunk = await resp.content.read(1024 * 1024)
                    if not chunk:
                        break
                    if not got_data:
                        got_data = True
                        self.conn_error = None
                        self._stream_live = True
                        was_hibernated = self._hibernated
                        self._hibernated = False
                        if self.conn_fail_count > 0:
                            logger.info("SSE 连接已恢复（此前连续失败 %d 次）", self.conn_fail_count)
                            if was_hibernated:
                                await self._push_notification("✅ SSE 连接已恢复", "")
                        backoff = 1
                        self.conn_fail_count = 0
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
                self._stream_live = False
                logger.info("SSE 监听已取消")
                return
            except ContentTypeError as e:
                self._stream_live = False
                self.conn_fail_count += 1
                backoff = min(max(backoff, 15) * 2, max_backoff)
                hint = "（疑似 Cloudflare 验证页）" if "text/html" in e.content_type else ""
                self.conn_error = f"{e} {hint}".strip()
                logger.warning("SSE 连接异常: %s %s, %ds 后重连", e, hint, backoff)
            except Exception as e:
                self._stream_live = False
                self.conn_fail_count += 1
                err_desc = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                self.conn_error = err_desc
                logger.warning("SSE 断线: %s, %ds 后重连", err_desc, backoff)
            finally:
                # 流结束（正常 EOF 或异常）后不再视为 live，直到下次读到数据
                if got_data and not self._hibernated:
                    # 循环将重连；短暂显示 reconnecting 更准确
                    self._stream_live = False
                if resp is not None:
                    resp.release()

            # 检查是否达到重连上限
            if self._max_reconnect > 0 and self.conn_fail_count >= self._max_reconnect:
                self._hibernated = True
                logger.warning("SSE 已连续失败 %d 次，达到重连上限，进入休眠。发送 /hapi list 可重新唤醒", self.conn_fail_count)
                await self._push_notification(
                    f"⚠️ SSE 已连续失败 {self.conn_fail_count} 次，达到重连上限，已进入休眠\n"
                    "发送 /hapi list 可重新唤醒并尝试重连", "")
                return

            if self.conn_fail_count == 20:
                logger.warning("SSE 已连续失败 20 次，请检查 HAPI 服务或网络")

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

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
        new_requests: list[tuple[str, dict]] = []
        if agent_state:
            # SSE 事件中 agentState 结构为 {version, value:{requests, ...}}，
            # 兼容部分场景直接扁平放 requests
            agent_state_value = agent_state.get("value") or {}
            requests_data = agent_state_value.get("requests") or agent_state.get("requests") or {}
            async with self._lock:
                old_reqs = self.pending.get(sid, {})
                new_requests = [
                    (rid, req)
                    for rid, req in requests_data.items()
                    if rid not in old_reqs
                ]
                # 检测被删除的请求，回收序号
                removed_rids = set(old_reqs.keys()) - set(requests_data.keys())
                for rid in removed_rids:
                    req = old_reqs[rid]
                    index = req.get("index", 0)
                    if index > 0:
                        self.free_index(index)

                # 为新请求分配序号
                for rid, req in new_requests:
                    if "index" not in req:
                        req["index"] = self.allocate_index()

                if requests_data:
                    self.pending[sid] = requests_data
                elif sid in self.pending:
                    del self.pending[sid]

            # 有新的权限请求 -> 推送提醒（或忙时自动批准）
            # 队列元素：str（纯文本）或 dict（结构卡参数，_flush 时出卡）
            queued_notifications: list = []
            for rid, req in new_requests:
                label = session_label_short(sid, self.sessions_cache)
                async with self._lock:
                    total = sum(len(r) for r in self.pending.values())
                    session_total = len(self.pending.get(sid, {}))

                index = req.get("index", 0)

                if self._auto_approve_enabled and self._in_auto_approve_window() and not is_question_request(req):
                    # 忙时托管审批：自动批准非 question 请求
                    ok, msg = await session_ops.approve_permission(self.client, sid, rid)
                    tool = req.get("tool", "?")
                    if self._auto_approve_silent and self.summary_service is not None:
                        # 静默收集：进托管汇总管线，不再逐条推送
                        await self.summary_service.append_event(
                            sid, "approve", ok, tool=str(tool), request_id=rid,
                            detail=(None if ok else str(msg or "审批失败"))[:200],
                        )
                    else:
                        result_mark = "✓" if ok else "✗"
                        notify_msg = f"[忙时托管审批] 已自动批准\n{label}\n  {result_mark} {tool}"
                        queued_notifications.append(notify_msg)
                else:
                    is_q = is_question_request(req)
                    if is_q:
                        msg = format_question_notification(
                            req, label, total, session_total, index
                        )
                        detail = format_request_detail(req)
                    else:
                        detail = format_request_detail(req)
                        msg = format_permission_notification(
                            label, detail, total, session_total, index
                        )
                    queued_notifications.append({
                        "text": msg,
                        "label": label,
                        "detail": detail,
                        "total": total,
                        "session_total": session_total,
                        "index": index,
                        "req": req,
                        "is_question": is_q,
                    })

            self._queue_request_notifications(sid, queued_notifications)

        # 有新请求 → 启动一次性提醒倒计时（如未启动）
        if new_requests and self._remind_enabled:
            if self._remind_task is None or self._remind_task.done():
                self._remind_task = asyncio.create_task(self._remind_once())

        # pending 已全部清空 → 取消提醒倒计时
        async with self._lock:
            pending_empty = not self.pending
        if pending_empty and self._remind_task and not self._remind_task.done():
            self._remind_task.cancel()

        # === 输出级别处理 ===

        # detail/simple 模式：防抖，合并短时间内的事件一次性拉取
        requests_flush_messages_first = bool(new_requests) and self.output_level in ("detail", "simple")
        if self.output_level in ("detail", "simple") and old_seq >= 0:
            if is_active or is_thinking:
                if not requests_flush_messages_first:
                    self._debounce_sids.add(sid)
                    if self._debounce_task is None or self._debounce_task.done():
                        self._debounce_task = asyncio.create_task(self._debounced_fetch())

        # 完成边沿只看 thinking -> idle；部分 Codex SSE 不会携带 active。
        if old_thinking and not is_thinking:
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
                if "permissionMode" in updated_data:
                    s["permissionMode"] = updated_data["permissionMode"]
                if "collaborationMode" in updated_data:
                    s["collaborationMode"] = updated_data["collaborationMode"]
                break
        else:
            metadata = updated_data.get("metadata")
            cache.append({
                "id": sid,
                "active": updated_data.get("active", False),
                "thinking": updated_data.get("thinking", False),
                "pendingRequestsCount": updated_data.get("pendingRequestsCount", 0),
                "metadata": copy.deepcopy(metadata) if isinstance(metadata, dict) else {},
            })

    def _already_notified_messages(self, sid: str, latest_visible_seq: int) -> bool:
        return latest_visible_seq <= self._message_notified_seqs.get(sid, -1)

    def _mark_messages_notified(self, sid: str, latest_visible_seq: int):
        self._message_notified_seqs[sid] = latest_visible_seq

    def _queue_request_notifications(self, sid: str, items: list):
        if not items:
            return
        self._queued_request_notifications.setdefault(sid, []).extend(items)
        self._request_notify_sids.add(sid)
        if self._request_notify_task is None or self._request_notify_task.done():
            self._request_notify_task = asyncio.create_task(self._debounced_request_notifications())

    async def _flush_request_notifications(self, sid: str):
        queued = self._queued_request_notifications.pop(sid, [])
        if not queued:
            return

        if self.output_level in ("detail", "simple"):
            async with self._lock:
                old_seq = self.session_states.get(sid, {}).get("lastSeq", -1)
            if old_seq >= 0:
                if self.output_level == "detail":
                    await self._show_detail(sid, old_seq)
                else:
                    await self._show_simple(sid, old_seq)

        for item in queued:
            if isinstance(item, dict):
                await self._push_permission_card(
                    session_id=sid,
                    label=str(item.get("label") or ""),
                    fallback_text=str(item.get("text") or ""),
                    total=int(item.get("total") or 0),
                    session_total=int(item.get("session_total") or 0),
                    index=int(item.get("index") or 0),
                    detail=str(item.get("detail") or ""),
                    req=item.get("req"),
                    is_question=bool(item.get("is_question")),
                )
            else:
                await self._push_notification(str(item), sid)

    async def _debounced_request_notifications(self):
        while True:
            await asyncio.sleep(0.5)
            sids = list(self._request_notify_sids)
            self._request_notify_sids.clear()
            for sid in sids:
                await self._flush_request_notifications(sid)
            if not self._request_notify_sids:
                break

    async def _debounced_completion(self):
        """防抖：等待状态稳定后再推送任务完成通知（避免 Codex 频繁切换 thinking 导致重复推送）。

        多 agent / 子代理场景下 thinking 会多次边沿跳变；拉长稳定窗口，
        且本轮已成功推过正文时不再额外弹「会话已完成」。
        """
        while True:
            # 完成边沿防抖：1.5s 合并一轮 thinking 抖动
            await asyncio.sleep(1.5)
            sids = list(self._completion_sids)
            self._completion_sids.clear()
            for sid in sids:
                async with self._lock:
                    state = self.session_states.get(sid, {})
                    has_pending = len(self.pending.get(sid, {})) > 0
                if state.get("thinking", False) or has_pending:
                    continue
                last_seq = state.get("lastSeq", 0)
                # 再确认一次稳定：短等后 thinking 仍为 false
                await asyncio.sleep(0.8)
                async with self._lock:
                    state2 = self.session_states.get(sid, {})
                    has_pending2 = len(self.pending.get(sid, {})) > 0
                if state2.get("thinking", False) or has_pending2:
                    self._completion_sids.add(sid)
                    continue

                showed = False
                if self.output_level == "summary":
                    showed = bool(await self._show_summary(sid, last_seq))
                elif self.output_level == "detail":
                    showed = bool(await self._show_detail(sid, last_seq))
                elif self.output_level == "simple":
                    showed = bool(await self._show_simple(sid, last_seq))

                async with self._lock:
                    last_seq = self.session_states.get(sid, {}).get("lastSeq", last_seq)
                if last_seq <= self._completion_notified_seqs.get(sid, -1):
                    continue
                self._completion_notified_seqs[sid] = last_seq
                # 本轮已推过对话卡，或防抖拉取时已推过同 seq 正文 → 不再叠「会话已完成」
                if showed:
                    continue
                if last_seq > 0 and last_seq <= self._message_notified_seqs.get(sid, -1):
                    continue
                label = session_label_short(sid, self.sessions_cache)
                if self.output_level == "silence":
                    await self._push_notification(
                        f"✅ 会话已完成，等待新的输入\n{label}", sid
                    )
                else:
                    # 无新可见正文时轻量提示（避免全被过滤后完全没反馈）
                    await self._push_notification(f"✅ 会话已完成\n{label}", sid)
            if not self._completion_sids:
                break

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
                    ok, msg = await session_ops.send_message(self.client, sid, "/compact")
                    mark = "✓" if ok else "✗"
                    if self._auto_approve_silent and self.summary_service is not None:
                        # 静默收集：进托管汇总管线，不再逐条推送
                        await self.summary_service.append_event(
                            sid, "compact", ok,
                            detail=(None if ok else str(msg or "自动压缩失败"))[:200],
                        )
                    else:
                        await self._push_notification(
                            f"[忙时托管审批] 已自动压缩上下文\n{label}\n  {mark} /compact", sid)
                else:
                    async with self._lock:
                        # 为压缩请求分配序号
                        compact_req = {
                            "tool": "__compact__",
                            "arguments": {},
                            "index": self.allocate_index()
                        }
                        self.pending.setdefault(sid, {})["__compact__"] = compact_req
                        total = sum(len(r) for r in self.pending.values())
                        session_total = len(self.pending.get(sid, {}))

                    index = compact_req["index"]
                    lines = [
                        f"⚠️ 上下文过长\n{label}",
                        "  压缩上下文（/compact）",
                        "",
                        formatters.format_pending_summary_line(total, session_total, index),
                        "",
                        "审批指令:",
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
        while True:
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
            if not self._debounce_sids:
                break

    def _preview_message(self, content: dict, *, detail: bool) -> str | None:
        """统一抽正文；detail 可含【子代理】，simple/summary 隐藏 sidechain 与工具进度。"""
        text = extract_text_preview(
            content,
            max_len=0,
            include_sidechain=detail,
        )
        # 最终兜底：tool_progress 心跳 JSON 绝不当正文
        if text is not None and formatters.is_sdk_noise_text(text):
            return None
        return text

    async def _show_detail(self, sid: str, old_seq: int) -> bool:
        """detail 模式：获取并显示所有新消息（含折叠后的子代理正文）"""
        try:
            messages = await session_ops.fetch_messages(self.client, sid, limit=20)
            if not messages:
                return False

            # 找出新消息（seq > old_seq），过滤掉用户消息
            new_msgs = [
                m for m in messages
                if m.get("seq", 0) > old_seq
                and m.get("content", {}).get("role") != "user"
            ]

            visible_msgs = []
            for msg in new_msgs:
                content = msg.get("content", {})
                text = self._preview_message(content, detail=True)
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
                return False

            latest_visible_seq = max(msg.get("seq", 0) for msg, _ in visible_msgs)
            if self._already_notified_messages(sid, latest_visible_seq):
                return False

            label = session_label_short(sid, self.sessions_cache)

            if len(visible_msgs) == 1:
                msg, text = visible_msgs[0]
                output = f"{label}\n{format_agent_line(text)}"
                body = text
            else:
                lines = [f"{label}\n━━━ {len(visible_msgs)} 条新消息 ━━━"]
                body_parts = []
                for msg, text in sorted(visible_msgs, key=lambda x: x[0].get("seq", 0)):
                    lines.append(format_agent_line(text))
                    body_parts.append(text)
                lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                output = "\n\n".join(lines)
                body = "\n\n---\n\n".join(body_parts)

            self._mark_messages_notified(sid, latest_visible_seq)
            await self._push_message_card(
                session_id=sid,
                label=label,
                body=body,
                fallback_text=output,
            )
            return True

        except Exception as e:
            logger.warning("detail 模式获取消息异常: %s", e)
            return False

    async def _show_simple(self, sid: str, old_seq: int) -> bool:
        """simple 模式：仅主 agent 纯文本；过滤工具进度 / sidechain / 工具调用行。"""
        try:
            messages = await session_ops.fetch_messages(self.client, sid, limit=50)
            if not messages:
                return False

            agent_texts = []
            for msg in messages:
                if msg.get("seq", 0) <= old_seq:
                    continue
                content = msg.get("content", {})
                if content.get("role") not in ("agent", "assistant"):
                    continue
                text = self._preview_message(content, detail=False)
                if text is None or text.startswith("🛠️") or text.startswith("❓"):
                    continue
                # 子代理标签（若上游漏标 isSidechain 但正文被标了）在 simple 仍隐藏
                if text.startswith("【子代理】") or text.startswith("【子代理:"):
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
                return False

            latest_visible_seq = max(msg.get("seq", 0) for msg, _ in agent_texts)
            if self._already_notified_messages(sid, latest_visible_seq):
                return False

            label = session_label_short(sid, self.sessions_cache)

            if len(agent_texts) == 1:
                _, text = agent_texts[0]
                output = f"{label}\n{format_agent_line(text)}"
                body = text
            else:
                lines = [f"{label}\n━━━ {len(agent_texts)} 条新消息 ━━━"]
                body_parts = []
                for _, text in agent_texts:
                    lines.append(format_agent_line(text))
                    body_parts.append(text)
                lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
                output = "\n\n".join(lines)
                body = "\n\n---\n\n".join(body_parts)

            self._mark_messages_notified(sid, latest_visible_seq)
            await self._push_message_card(
                session_id=sid,
                label=label,
                body=body,
                fallback_text=output,
            )
            return True

        except Exception as e:
            logger.warning("simple 模式获取消息异常: %s", e)
            return False

    async def _show_summary(self, sid: str, old_seq: int) -> bool:
        """summary 模式：最近 N 条主 agent 消息（过滤工具调用与 sidechain）"""
        try:
            messages = await session_ops.fetch_messages(self.client, sid, limit=50)
            if not messages:
                return False

            agent_texts = []
            for msg in messages:
                if msg.get("seq", 0) <= old_seq:
                    continue
                content = msg.get("content", {})
                if content.get("role") not in ("agent", "assistant"):
                    continue
                text = self._preview_message(content, detail=False)
                if text is None or text.startswith("🛠️") or text.startswith("❓"):
                    continue
                if text.startswith("【子代理】") or text.startswith("【子代理:"):
                    continue
                agent_texts.append((msg, text))

            latest_seq = max(m.get("seq", 0) for m in messages)
            async with self._lock:
                if sid in self.session_states:
                    self.session_states[sid]["lastSeq"] = latest_seq

            await self._check_and_handle_compact(sid, messages, old_seq)

            if not agent_texts:
                return False

            agent_texts = agent_texts[-self._summary_msg_count:]
            latest_visible_seq = max(msg.get("seq", 0) for msg, _ in agent_texts)
            if self._already_notified_messages(sid, latest_visible_seq):
                return False

            label = session_label_short(sid, self.sessions_cache)

            if len(agent_texts) == 1:
                _, text = agent_texts[0]
                output = f"{label}\n{format_agent_line(text)}"
                body = text
            else:
                lines = [f"{label}\n━━━ 最近 {len(agent_texts)} 条消息 ━━━"]
                body_parts = []
                for _, text in agent_texts:
                    lines.append(format_agent_line(text))
                    body_parts.append(text)
                lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
                output = "\n\n".join(lines)
                body = "\n\n---\n\n".join(body_parts)

            self._mark_messages_notified(sid, latest_visible_seq)
            await self._push_message_card(
                session_id=sid,
                label=label,
                body=body,
                fallback_text=output,
            )
            return True

        except Exception as e:
            logger.warning("summary 模式获取消息异常: %s", e)
            return False

    async def _remind_once(self):
        """倒计时结束后，若仍有待审批请求则发一次提醒"""
        try:
            await asyncio.sleep(self._remind_interval)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if not self.pending:
                return
            pending_snapshot = self.get_all_pending()

        total = sum(len(r) for r in pending_snapshot.values())
        for sid, reqs in pending_snapshot.items():
            count = len(reqs)
            if count == 0:
                continue
            label = session_label_short(sid, self.sessions_cache)
            lines = [
                f"⏰ 提醒：该会话仍有 {count} 个待审批请求",
                label,
                "",
                f"当前全局共 {total} 个待审批请求，请及时处理以避免会话缓存失效",
                "  /hapi a        全部批准",
                "  /hapi pending  查看列表",
            ]
            await self._push_notification("\n".join(lines), sid)

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

    def allocate_index(self) -> int:
        """分配最小可用序号"""
        if self._free_indices:
            idx = min(self._free_indices)
            self._free_indices.remove(idx)
            return idx
        self._max_index += 1
        return self._max_index

    def free_index(self, index: int):
        """回收序号"""
        if index > 0:
            self._free_indices.add(index)

    async def _push_notification(self, text: str, session_id: str):
        """通过回调向所有已注册的管理员推送消息"""
        await self.notify_callback(text, session_id)

    async def _push_message_card(
        self,
        *,
        session_id: str,
        label: str,
        body: str,
        fallback_text: str,
        title: str = "",
        footer: str = "",
    ):
        """Agent 对话推送：按 render_mode / render_kinds 尝试出卡，失败回退文本。

        标题用会话标题；不附 output=* 尾注（避免图片后再多一条文本）。
        """
        # 推送入口再挡：整段 body / fallback 仍是 tool_progress 时直接不推
        if formatters.is_sdk_noise_text(body) and formatters.is_sdk_noise_text(
            fallback_text
        ):
            logger.debug("skip message push: sdk noise body session=%s", session_id[:8])
            return
        if formatters.is_sdk_noise_text(body):
            body = ""
        if formatters.is_sdk_noise_text(fallback_text):
            fallback_text = (body or "").strip() or label
        if not (body or "").strip() and not (fallback_text or "").strip():
            return
        plugin = self.plugin
        if plugin is None:
            # 兜底：从 notify 链路外拿不到 plugin 时只发文本
            logger.warning(
                "message card: sse_listener.plugin 未注入，回退文本 "
                "（请确认 main 里 self.sse_listener.plugin = self 已执行并已重载插件）"
            )
            await self._push_notification(fallback_text, session_id)
            return
        try:
            from ..render import output_present
            from ..render import formatters as fmt
            sess = next(
                (s for s in self.sessions_cache if s.get("id") == session_id),
                None,
            )
            sess_title = fmt.get_session_title(sess) if sess else ""
            # HAPI generated-image → 本地下载，对话卡可嵌真图
            body_resolved = body
            try:
                client = getattr(self, "client", None) or getattr(plugin, "client", None)
                if client and body and "hapi-genimg://" in body:
                    body_resolved = await output_present.resolve_hapi_genimg_markers(
                        body, client=client, session_id=session_id
                    )
            except Exception as e:
                logger.warning("resolve genimg markers failed: %s", e)
                body_resolved = body
            # 纯文本回退同样解析 hapi-genimg://，否则 text 模式图片退化成
            # ![alt](hapi-genimg://id) 文本（上游 v3.2.4 未覆盖此路径）
            fallback_resolved = fallback_text
            try:
                if client and fallback_text and "hapi-genimg://" in fallback_text:
                    fallback_resolved = await output_present.resolve_hapi_genimg_markers(
                        fallback_text, client=client, session_id=session_id
                    )
            except Exception as e:
                logger.warning("resolve genimg markers in fallback failed: %s", e)
                fallback_resolved = fallback_text
            payload = output_present.build_message_payload(
                label=label,
                body=body_resolved,
                title=title or sess_title,
                footer=footer,  # 默认空，不推 caption
                session_title=sess_title,
            )
            notif = getattr(plugin, "notification_mgr", None)
            if notif is None:
                logger.warning("message card: plugin.notification_mgr 缺失，回退文本")
                await self._push_notification(fallback_text, session_id)
                return
            await output_present.present_push(
                plugin,
                notif,
                "message",
                payload,
                fallback_resolved,
                session_id,
                self.sessions_cache,
            )
        except Exception as e:
            logger.warning("message card push failed: %s", e, exc_info=True)
            await self._push_notification(fallback_text, session_id)

    async def _push_permission_card(
        self,
        *,
        session_id: str,
        label: str,
        fallback_text: str,
        total: int,
        session_total: int,
        index: int,
        detail: str = "",
        req: dict | None = None,
        is_question: bool = False,
    ):
        """权限 / 问题请求推送：结构卡优先，失败回退文本。"""
        plugin = self.plugin
        if plugin is None:
            await self._push_notification(fallback_text, session_id)
            return
        try:
            from ..render import output_present
            kind = "question" if is_question else "permission"
            # 卡片 kind 统一用 permission（CARD_KINDS 已有）；question 也走同一结构卡
            payload = output_present.build_permission_payload(
                label=label,
                detail=detail,
                total=total,
                session_total=session_total,
                index=index,
                kind=kind,
                req=req,
            )
            notif = getattr(plugin, "notification_mgr", None)
            if notif is None:
                await self._push_notification(fallback_text, session_id)
                return
            await output_present.present_push(
                plugin,
                notif,
                "permission",
                payload,
                fallback_text,
                session_id,
                self.sessions_cache,
            )
        except Exception as e:
            logger.warning("permission card push failed: %s", e, exc_info=True)
            await self._push_notification(fallback_text, session_id)

    async def push_auto_approve_summary(self, session_id: str, view: dict, fallback_text: str) -> bool:
        """托管汇总推送：结构卡优先，失败回退文本（§5.4 路由带 session_id）。

        供 AutoApproveSummaryService 注入为推送回调；返回值=是否已发出。
        """
        plugin = self.plugin
        if plugin is None:
            await self._push_notification(fallback_text, session_id)
            return True
        try:
            from ..render import output_present
            payload = output_present.build_auto_approve_summary_payload(view)
            notif = getattr(plugin, "notification_mgr", None)
            if notif is None:
                await self._push_notification(fallback_text, session_id)
                return True
            await output_present.present_push(
                plugin,
                notif,
                "auto_approve_summary",
                payload,
                fallback_text,
                session_id,
                self.sessions_cache,
            )
            return True
        except Exception as e:
            logger.warning("auto approve summary push failed: %s", e, exc_info=True)
            await self._push_notification(fallback_text, session_id)
            return True

    async def load_existing_pending(self):
        """启动时从已有 session 加载待审批请求"""
        for s in self.sessions_cache:
            sid = s.get("id", "")
            pending_count = s.get("pendingRequestsCount", 0)
            if not sid or not pending_count:
                continue
            try:
                detail = await session_ops.fetch_session_detail(self.client, sid)
                # fetch_session_detail 已解包 {session:{...}}，返回扁平 session 对象；
                # agentState 为 {controlledByUser, startingMode, requests, completedRequests} 扁平结构
                agent_state = detail.get("agentState") or {}
                requests_data = agent_state.get("requests") or {}
                if requests_data:
                    async with self._lock:
                        # 为已有请求分配序号
                        for rid, req in requests_data.items():
                            if "index" not in req:
                                req["index"] = self.allocate_index()
                        self.pending[sid] = requests_data
                    logger.info("加载 session %s 的 %d 个待审批请求",
                                sid[:8], len(requests_data))
            except Exception as e:
                logger.warning("加载 session %s 待审批失败: %s", sid[:8], e)
