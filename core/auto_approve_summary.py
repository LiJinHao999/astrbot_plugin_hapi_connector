"""忙时托管操作记录：事件收集、桶、指纹、防漏发、KV 持久化与推送编排。

设计对照 dev-docs/auto-approve-silent-summary.md（§2/§4/§5.3）：

- 收集维度 (session_id → 事件)，推送每 session 一张，严格走既有窗口路由。
- 防漏发：last_pushed_at（本地日历日）+ last_content_fingerprint（sha256 规范化事件）
  + last_bucket_id；need_flush 见 §2.3 规则。
- 桶：daily=day:YYYY-MM-DD；window=window:进入日Tstart:结束日Tend（跨午夜一段窗一个 id）；
  per_event=event:uuid。
- 纯逻辑：不依赖 AstrBot Event；推送经注入回调完成（SSEListener.push_auto_approve_summary）。
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..render import formatters

logger = logging.getLogger("hapi_connector.auto_approve_summary")

# KV 键（§5.3）
KV_KEY = "auto_approve_summary_v1"

# 单 session pending 明细上限：超限丢最旧明细，统计计数（counters）不受影响
MAX_PENDING_EVENTS_PER_SESSION = 500
# 事件 detail 存储截断（不把大段 stdout 塞进 KV）
MAX_STORED_DETAIL_LEN = 200
# 采样/持久化节流
EDGE_SAMPLE_INTERVAL_SEC = 60
PERSIST_DEBOUNCE_SEC = 5

SUMMARY_MODES = ("daily", "window", "per_event")
PUSH_TRIGGERS = ("on_window_end", "at_fixed_time")

COUNT_KEYS = ("approve_ok", "approve_fail", "compact_ok", "compact_fail", "deny_fail")


@dataclass
class AutoApproveEvent:
    """一条托管时段内的自动批准 / 自动压缩结果。"""

    session_id: str
    at: datetime.datetime          # 本地时间
    kind: str                      # "approve" | "compact"
    ok: bool
    tool: str | None = None
    detail: str | None = None
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "at": self.at.isoformat(),
            "kind": self.kind,
            "ok": self.ok,
            "tool": self.tool,
            "detail": self.detail,
            "request_id": self.request_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AutoApproveEvent":
        at = data.get("at")
        try:
            parsed_at = datetime.datetime.fromisoformat(str(at)) if at else datetime.datetime.now()
        except ValueError:
            parsed_at = datetime.datetime.now()
        return cls(
            session_id=str(data.get("session_id") or ""),
            at=parsed_at,
            kind=str(data.get("kind") or "approve"),
            ok=bool(data.get("ok")),
            tool=data.get("tool"),
            detail=data.get("detail"),
            request_id=data.get("request_id"),
        )


@dataclass
class SessionSummaryState:
    """单个 session 的汇总运行时状态。

    pending = 当前统计窗事件；last_closed_snapshot = 上一已关闭窗（命令可重发）。
    见 dev-docs/busy-hours-agent-push.md §4。
    """

    pending: list[AutoApproveEvent] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    last_pushed_at: datetime.datetime | None = None
    last_content_fingerprint: str | None = None
    last_bucket_id: str | None = None
    current_bucket_id: str | None = None
    # 当前窗内累计 Agent 运行秒（SSE 边沿写入）
    runtime_sec: float = 0.0
    # 上一关闭统计窗快照（命令重放；每 sid 只保留一份）
    last_closed_snapshot: dict[str, Any] | None = None

    def counter(self, key: str) -> int:
        return self.counters.get(key, 0)


class AutoApproveSummaryService:
    """托管操作记录服务（每插件单例）。

    生命周期与 SSE 相同：load → start_tasks；stop 时先 flush 再取消任务。
    配置热更新走 update_config；「关 silent / 关 auto_approve / 改 mode/push/time」
    由调用方在更新后触发 flush_all()（need_flush 负责去重）。
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self._sessions: dict[str, SessionSummaryState] = {}
        self._lock = asyncio.Lock()
        self._push_callback: Callable[[str, dict, str], Awaitable[bool]] | None = None
        # 可选：flush 时附带 git 变更快照（main 注入；返回 dict 或 None=无 git/失败）
        self._git_provider: Callable[[str], Awaitable[dict | None]] | None = None
        # {sid: (monotonic_ts, data)}：git 快照 TTL 缓存，避免 per_event 高频 flush 重复查询
        self._git_cache: dict[str, tuple[float, dict | None]] = {}
        self._git_cache_ttl: float = 30.0

        # 配置（与 _conf_schema.json 默认一致，热更新覆盖）
        self._auto_approve_enabled: bool = False
        self._silent: bool = False
        self._mode: str = "window"
        self._push: str = "on_window_end"
        self._fixed_time: str = "08:00"
        self._include_failures: bool = True
        self._max_detail_lines: int = 30
        self._start: str = "23:00"
        self._end: str = "07:00"

        # 运行时
        self._was_in_window: bool = False
        self._window_enter_day: datetime.date | None = None
        self._edge_task: asyncio.Task | None = None
        self._fixed_time_task: asyncio.Task | None = None
        self._persist_task: asyncio.Task | None = None
        self._dirty: bool = False

    # ──── 配置 ────

    def update_config(self, **kwargs: Any) -> None:
        """热更新配置（不写盘）。未知键忽略。"""
        mapping = {
            "auto_approve_enabled": "_auto_approve_enabled",
            "auto_approve_start": "_start",
            "auto_approve_end": "_end",
            "auto_approve_silent": "_silent",
            "auto_approve_summary_mode": "_mode",
            "auto_approve_summary_push": "_push",
            "auto_approve_summary_time": "_fixed_time",
            "auto_approve_summary_include_failures": "_include_failures",
            "auto_approve_summary_max_detail_lines": "_max_detail_lines",
        }
        for key, attr in mapping.items():
            if key in kwargs and kwargs[key] is not None:
                setattr(self, attr, kwargs[key])

    @property
    def enabled(self) -> bool:
        """操作记录总开关：托管开启 且 操作记录开启。"""
        return bool(self._auto_approve_enabled and self._silent)

    @property
    def mode(self) -> str:
        return self._mode if self._mode in SUMMARY_MODES else "window"

    @property
    def push(self) -> str:
        return self._push if self._push in PUSH_TRIGGERS else "on_window_end"

    def set_push_callback(self, cb: Callable[[str, dict, str], Awaitable[bool]]) -> None:
        """注入推送回调：async (sid, view, fallback_text) -> 是否已发出。"""
        self._push_callback = cb

    def set_git_provider(self, cb: Callable[[str], Awaitable[dict | None]]) -> None:
        """注入 git 变更快照提供者：async (sid) -> dict | None（None=非 git 仓库/失败）。

        快照在 flush 时取（TTL 缓存避免 per_event 高频重复查询），随汇总一并展示。
        """
        self._git_provider = cb

    async def _git_snapshot(self, sid: str) -> dict | None:
        """带 TTL 缓存的 git 快照获取（锁外调用；30s 内同一 sid 不重复查）。"""
        import time as _time

        provider = self._git_provider
        if provider is None:
            return None
        now = _time.monotonic()
        cached = self._git_cache.get(sid)
        if cached is not None and now - cached[0] < self._git_cache_ttl:
            return cached[1]
        try:
            data = await provider(sid)
        except Exception as e:
            logger.warning("git 快照获取失败 sid=%s: %s", sid[:8], e)
            data = None
        self._git_cache[sid] = (now, data)
        return data

    # ──── 窗口判定（与 sse_listener._in_auto_approve_window 同规则，本地时区） ────

    def _in_window(self, now: datetime.datetime | None = None) -> bool:
        now = now or datetime.datetime.now()
        try:
            t = now.time()
            h_s, m_s = map(int, self._start.split(":"))
            h_e, m_e = map(int, self._end.split(":"))
            start = datetime.time(h_s, m_s)
            end = datetime.time(h_e, m_e)
            if start <= end:
                return start <= t <= end
            return t >= start or t <= end
        except Exception:
            return False

    # ──── 桶 ────

    def _bucket_for(self, now: datetime.datetime) -> str:
        mode = self.mode
        if mode == "daily":
            return f"day:{now:%Y-%m-%d}"
        if mode == "per_event":
            return f"event:{uuid.uuid4().hex[:12]}"
        # window：一段窗（可跨午夜）一个 id，以进入窗口的本地日生成
        if self._window_enter_day is None:
            self._window_enter_day = now.date()
        enter_day = self._window_enter_day
        start = self._start
        end = self._end
        if start > end:  # 跨午夜，如 23:00 ~ 07:00
            end_day = enter_day + datetime.timedelta(days=1)
        else:
            end_day = enter_day
        return f"window:{enter_day}T{start}:{end_day}T{end}"

    @staticmethod
    def _bucket_day(bucket_id: str | None) -> datetime.date | None:
        """从桶 ID 抽归属日（daily=桶日；window=进入日；event=无）。"""
        if not bucket_id:
            return None
        try:
            if bucket_id.startswith("day:"):
                return datetime.date.fromisoformat(bucket_id[4:])
            if bucket_id.startswith("window:"):
                rest = bucket_id[len("window:"):]
                day_part = rest.split("T", 1)[0]
                return datetime.date.fromisoformat(day_part)
        except ValueError:
            return None
        return None

    def _bucket_desc(self, bucket_id: str | None) -> str:
        """桶的可读描述（供汇总文案/卡片副标题）。"""
        if not bucket_id:
            return "—"
        if bucket_id.startswith("day:"):
            return bucket_id[4:]
        if bucket_id.startswith("window:"):
            rest = bucket_id[len("window:"):]
            try:
                # 格式 window:{enter_date}T{start}:{end_date}T{end}（start/end 为 HH:MM）
                enter_part, tail = rest.split("T", 1)
                h, m, tail2 = tail.split(":", 2)
                end_day, end_time = tail2.split("T", 1)
                return f"托管时段 {enter_part} {h}:{m} ~ {end_day} {end_time}"
            except Exception:
                return bucket_id
        if bucket_id.startswith("event:"):
            return "手动触发"
        return bucket_id

    # ──── 事件收集 ────

    async def append_event(
        self,
        session_id: str,
        kind: str,
        ok: bool,
        *,
        tool: str | None = None,
        detail: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """操作记录开启时收集一条托管自动动作（approve / compact）。

        per_event（手动触发）模式不做即时推送：等每次手动 /hapi summary 命令
        或防漏发补发点（stop/关开关）才推送。
        """
        if not self.enabled:
            return
        now = datetime.datetime.now()
        evt = AutoApproveEvent(
            session_id=session_id,
            at=now,
            kind=kind,
            ok=ok,
            tool=tool,
            detail=detail,
            request_id=request_id,
        )
        if evt.detail and len(evt.detail) > MAX_STORED_DETAIL_LEN:
            evt.detail = evt.detail[:MAX_STORED_DETAIL_LEN] + "…"

        async with self._lock:
            state = self._sessions.setdefault(session_id, SessionSummaryState())
            state.current_bucket_id = self._bucket_for(now)
            state.pending.append(evt)
            if len(state.pending) > MAX_PENDING_EVENTS_PER_SESSION:
                state.pending = state.pending[-MAX_PENDING_EVENTS_PER_SESSION:]
            key = f"{kind}_{'ok' if ok else 'fail'}"
            state.counters[key] = state.counters.get(key, 0) + 1
            self._mark_dirty()

    async def record_operation(
        self,
        session_id: str,
        kind: str,
        ok: bool,
        *,
        tool: str | None = None,
        detail: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """托管时段内 agent 操作记录入口（手动批准 / 拒绝 / LLM 工具自动批准等）。

        自动批准 / 自动压缩在 sse_listener 内直接走 append_event（必然发生在窗内）；
        手动路径（用户批准、拒绝）只在托管窗内且汇总开启时才记录。
        """
        if not self.enabled:
            return
        if not self._in_window():
            return
        await self.append_event(
            session_id, kind, ok,
            tool=tool, detail=detail, request_id=request_id,
        )

    # ──── 指纹 / 判定 ────

    def _fingerprint(self, state: SessionSummaryState) -> str:
        """内容指纹：规范化 JSON（含事件明细），不编入推送时刻（§3.3）。"""
        events = sorted(
            state.pending,
            key=lambda e: (e.at.isoformat(), e.request_id or "", e.kind, str(e.tool or "")),
        )
        payload = {
            "sid": events[0].session_id if events else "",
            "bucket_id": state.current_bucket_id,
            "approve_ok": state.counter("approve_ok"),
            "approve_fail": state.counter("approve_fail"),
            "compact_ok": state.counter("compact_ok"),
            "compact_fail": state.counter("compact_fail"),
            "events": [
                {
                    "t": e.at.isoformat(),
                    "kind": e.kind,
                    "tool": e.tool,
                    "ok": e.ok,
                    "err": e.detail,
                }
                for e in events
            ],
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def need_flush(self, state: SessionSummaryState, now: datetime.datetime | None = None) -> bool:
        """§2.3：有未推事件 且 非「同日 + 同指纹」。"""
        if not state.pending:
            return False
        now = now or datetime.datetime.now()
        if state.last_pushed_at is None:
            return True
        if state.last_pushed_at.date() == now.date():
            if state.last_content_fingerprint == self._fingerprint(state):
                return False
        return True

    # ──── 汇总视图 ────

    def build_summary_view(self, sid: str, state: SessionSummaryState | None = None) -> dict[str, Any]:
        """构造展示视图：formatters 与卡片 builder 只消费这份 dict。"""
        state = state or self._sessions.get(sid)
        now = datetime.datetime.now()
        session = next((s for s in self.plugin.sessions_cache if s.get("id") == sid), None)
        label = formatters.session_label_short(sid, self.plugin.sessions_cache)
        title = formatters.get_session_title(session) if session else label.splitlines()[0] if label else sid[:8]

        failures = [
            _event_view(e)
            for e in state.pending if not e.ok
        ]
        successes = [
            _event_view(e)
            for e in sorted(
                (e for e in state.pending if e.ok),
                key=lambda e: e.at.isoformat(),
            )
        ]
        view = {
            "sid": sid,
            "title": title,
            "label": label,
            "bucket_id": state.current_bucket_id,
            "bucket_desc": self._bucket_desc(state.current_bucket_id),
            "counters": {
                key: state.counter(key)
                for key in COUNT_KEYS
            },
            "failures": failures,
            "successes": successes,
            "total": len(state.pending),
            "last_pushed_at": state.last_pushed_at,
            "mode": self.mode,
            "push": self.push,
            "in_window": self._in_window(now),
            "include_failures": bool(self._include_failures),
            "max_detail_lines": max(1, int(self._max_detail_lines or 30)),
            "runtime_sec": float(state.runtime_sec or 0),
            "git": None,
        }
        cached = self._git_cache.get(sid)
        if cached is not None and cached[1]:
            view["git"] = cached[1]
        return view

    def _snapshot_from_state(self, sid: str, state: SessionSummaryState) -> dict[str, Any]:
        """当前桶 → 可序列化关闭窗快照（明细按 max_detail_lines 截断）。"""
        max_lines = max(1, int(self._max_detail_lines or 30))
        events = list(state.pending)
        # 失败全留 + 成功取最近 K，总体积可控
        fails = [e for e in events if not e.ok]
        oks = [e for e in events if e.ok][-max_lines:]
        kept = fails + oks
        return {
            "bucket_id": state.current_bucket_id,
            "bucket_desc": self._bucket_desc(state.current_bucket_id),
            "counters": {k: state.counter(k) for k in COUNT_KEYS},
            "events": [e.to_dict() for e in kept],
            "runtime_sec": float(state.runtime_sec or 0),
            "closed_at": datetime.datetime.now().isoformat(),
            "sid": sid,
        }

    def _view_from_snapshot(self, sid: str, snap: dict[str, Any]) -> dict[str, Any]:
        """关闭窗快照 → 展示视图（命令重放）。"""
        now = datetime.datetime.now()
        session = next((s for s in self.plugin.sessions_cache if s.get("id") == sid), None)
        label = formatters.session_label_short(sid, self.plugin.sessions_cache)
        title = formatters.get_session_title(session) if session else label.splitlines()[0] if label else sid[:8]
        events = [
            AutoApproveEvent.from_dict(e) for e in (snap.get("events") or []) if isinstance(e, dict)
        ]
        failures = [_event_view(e) for e in events if not e.ok]
        successes = [
            _event_view(e) for e in sorted((e for e in events if e.ok), key=lambda x: x.at.isoformat())
        ]
        counters_raw = snap.get("counters") or {}
        view = {
            "sid": sid,
            "title": title,
            "label": label,
            "bucket_id": snap.get("bucket_id"),
            "bucket_desc": snap.get("bucket_desc") or self._bucket_desc(snap.get("bucket_id")),
            "counters": {k: int(counters_raw.get(k) or 0) for k in COUNT_KEYS},
            "failures": failures,
            "successes": successes,
            "total": len(events),
            "last_pushed_at": None,
            "mode": self.mode,
            "push": self.push,
            "in_window": self._in_window(now),
            "include_failures": bool(self._include_failures),
            "max_detail_lines": max(1, int(self._max_detail_lines or 30)),
            "runtime_sec": float(snap.get("runtime_sec") or 0),
            "git": None,
            "from_snapshot": True,
        }
        return view

    async def add_runtime_sec(self, sid: str, delta: float) -> None:
        """SSE 完成边沿累加运行秒（仅 enabled 时有意义；调用方已判断）。"""
        if delta <= 0:
            return
        async with self._lock:
            state = self._sessions.setdefault(sid, SessionSummaryState())
            state.runtime_sec = float(state.runtime_sec or 0) + float(delta)
            self._mark_dirty()

    def _archive_and_clear_bucket(self, sid: str, state: SessionSummaryState) -> None:
        """桶切换/自动结算：写入 last_closed_snapshot 并清空当前桶事件。"""
        if state.pending or state.runtime_sec:
            state.last_closed_snapshot = self._snapshot_from_state(sid, state)
        state.pending = []
        state.counters = {}
        state.runtime_sec = 0.0

    # ──── 推送 ────

    async def _emit_view(self, sid: str, view: dict[str, Any]) -> tuple[bool, str]:
        """推送一张视图；返回 (ok, text)。"""
        text = formatters.format_auto_approve_summary(view)
        if self._push_callback is None:
            logger.warning("auto approve summary: push callback 未注入，无法推送 sid=%s", sid[:8])
            return False, text
        try:
            ok = bool(await self._push_callback(sid, view, text))
        except Exception as e:
            logger.warning("auto approve summary push 异常 sid=%s: %s", sid[:8], e)
            return False, text
        return ok, text

    async def flush_session(
        self,
        sid: str,
        *,
        force: bool = False,
        close_bucket: bool = False,
        prefer_snapshot: bool = False,
    ) -> dict[str, Any]:
        """推送单个 session 的操作记录。

        - 自动路径：force=False，受 need_flush 约束；成功只更新 last_*，
          close_bucket=True 时才归档快照并清当前桶（窗结束/日切）。
        - 命令路径：force=True；prefer_snapshot=True 时优先上一关闭窗，
          否则当前桶；**不**因推送清空数据（busy-hours-agent-push.md §4）。
        """
        source = "none"  # pending | snapshot
        async with self._lock:
            state = self._sessions.get(sid)
            snap = state.last_closed_snapshot if state else None
            has_snap = bool(
                snap and (
                    snap.get("events")
                    or any((snap.get("counters") or {}).values())
                    or snap.get("runtime_sec")
                )
            )
            has_pending = bool(state and state.pending)

            if force and prefer_snapshot and has_snap:
                source = "snapshot"
            elif has_pending:
                if not force and state is not None and not self.need_flush(state):
                    return {"pushed": False, "reason": "no_change", "text": ""}
                source = "pending"
            elif force and has_snap:
                source = "snapshot"
            else:
                return {
                    "pushed": False,
                    "reason": "no_record" if force else "no_pending",
                    "text": "",
                }

        # 锁外取 git（仅当前桶展示附带；快照重放也可附当前 git 作参考）
        await self._git_snapshot(sid)

        async with self._lock:
            state = self._sessions.get(sid)
            if state is None:
                return {"pushed": False, "reason": "no_record" if force else "no_pending", "text": ""}
            if source == "pending":
                if not state.pending:
                    return {"pushed": False, "reason": "no_pending", "text": ""}
                if not force and not self.need_flush(state):
                    return {"pushed": False, "reason": "no_change", "text": ""}
                view = self.build_summary_view(sid, state)
                fp_before = self._fingerprint(state)
                bucket_id = state.current_bucket_id
            else:
                snap = state.last_closed_snapshot
                if not snap:
                    return {"pushed": False, "reason": "no_record", "text": ""}
                view = self._view_from_snapshot(sid, snap)
                fp_before = None
                bucket_id = snap.get("bucket_id")

        cached = self._git_cache.get(sid)
        if cached is not None and cached[1]:
            view["git"] = cached[1]

        ok, text = await self._emit_view(sid, view)
        if not ok:
            return {"pushed": False, "reason": "push_failed", "text": text}

        async with self._lock:
            state = self._sessions.get(sid)
            if state is None:
                return {"pushed": True, "reason": "pushed", "text": text}
            state.last_pushed_at = datetime.datetime.now()
            if source == "pending" and state.pending:
                state.last_content_fingerprint = fp_before or self._fingerprint(state)
                state.last_bucket_id = bucket_id
                if close_bucket:
                    self._archive_and_clear_bucket(sid, state)
            await self._save()
        return {"pushed": True, "reason": "pushed", "text": text}

    async def flush_all(self, *, close_bucket: bool = False) -> dict[str, dict[str, Any]]:
        """对所有有 pending 的 session 各推一张（need_flush 过滤无变更）。"""
        async with self._lock:
            sids = [sid for sid, st in self._sessions.items() if st.pending]
        results: dict[str, dict[str, Any]] = {}
        for sid in sids:
            results[sid] = await self.flush_session(sid, close_bucket=close_bucket)
        return results

    async def push_for_command(self, sid: str) -> dict[str, Any]:
        """命令路径：优先上一关闭窗快照，否则当前桶；可重复发送。"""
        return await self.flush_session(sid, force=True, prefer_snapshot=True)

    def has_pending(self) -> bool:
        return any(st.pending for st in self._sessions.values())

    def has_record(self, sid: str | None = None) -> bool:
        """是否有可展示记录（当前桶或上一窗快照）。"""
        if sid is not None:
            st = self._sessions.get(sid)
            if st is None:
                return False
            return bool(st.pending or st.last_closed_snapshot)
        return any(st.pending or st.last_closed_snapshot for st in self._sessions.values())

    # ──── 触发点：窗边沿 / 桶结算 / 定点 ────

    async def _sample_edge(self) -> None:
        async with self._lock:
            in_window = self._in_window()
            window_ended = bool(self._was_in_window and not in_window)
            window_started = bool(not self._was_in_window and in_window)
            if window_ended:
                self._window_enter_day = None
            if window_started:
                self._window_enter_day = datetime.date.today()
            self._was_in_window = in_window

            # daily / window 桶过期检查：存在「归属日早于今天」且有 pending 的桶 → 结算
            today = datetime.date.today()
            bucket_rollover = False
            for st in self._sessions.values():
                if not st.pending:
                    continue
                day = self._bucket_day(st.current_bucket_id)
                if day is not None and day < today:
                    bucket_rollover = True
                    break
        # per_event（手动触发）模式：自动触发点不推，只等 /hapi summary 命令
        # 窗结束/日切：推送后 close_bucket 归档快照，供命令重发
        if (window_ended or bucket_rollover) and self.mode != "per_event":
            await self.flush_all(close_bucket=True)
        elif window_ended or bucket_rollover:
            # 手动模式不自动推，但仍归档关闭桶，避免命令只能看到空 pending
            async with self._lock:
                for sid, st in list(self._sessions.items()):
                    if st.pending:
                        self._archive_and_clear_bucket(sid, st)
                await self._save()

    async def _edge_loop(self) -> None:
        while True:
            await asyncio.sleep(EDGE_SAMPLE_INTERVAL_SEC)
            try:
                await self._sample_edge()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("auto approve summary 边沿采样异常: %s", e)

    async def _fixed_time_loop(self) -> None:
        """每天本地 HH:MM 推送（§2.5 at_fixed_time）。per_event（手动触发）模式不自动推。"""
        while True:
            try:
                now = datetime.datetime.now()
                h, m = map(int, self._fixed_time.split(":"))
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if target <= now:
                    target += datetime.timedelta(days=1)
                await asyncio.sleep(max(1, (target - now).total_seconds()))
                if self.mode != "per_event":
                    await self.flush_all()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("auto approve summary 定点推送异常: %s", e)

    def start_tasks(self) -> None:
        """启动边沿采样与定点任务（与 SSE 同生命周期，由 main 调用）。"""
        if self._edge_task is None or self._edge_task.done():
            self._edge_task = asyncio.create_task(self._edge_loop())
        if self._fixed_time_task is None or self._fixed_time_task.done():
            self._fixed_time_task = asyncio.create_task(self._fixed_time_loop())

    async def stop(self) -> None:
        """停止任务并落盘（flush 由调用方先执行）。"""
        for task in (self._edge_task, self._fixed_time_task, self._persist_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._edge_task = None
        self._fixed_time_task = None
        self._persist_task = None
        await self._save()

    # ──── KV 持久化 ────

    def _mark_dirty(self) -> None:
        self._dirty = True
        if self._persist_task is None or self._persist_task.done():
            self._persist_task = asyncio.create_task(self._persist_loop())

    async def _persist_loop(self) -> None:
        await asyncio.sleep(PERSIST_DEBOUNCE_SEC)
        self._persist_task = None
        await self._save()

    @staticmethod
    def _state_to_dict(state: SessionSummaryState) -> dict[str, Any]:
        return {
            "pending": [e.to_dict() for e in state.pending],
            "counters": dict(state.counters),
            "last_pushed_at": state.last_pushed_at.isoformat() if state.last_pushed_at else None,
            "last_content_fingerprint": state.last_content_fingerprint,
            "last_bucket_id": state.last_bucket_id,
            "current_bucket_id": state.current_bucket_id,
            "runtime_sec": float(state.runtime_sec or 0),
            "last_closed_snapshot": state.last_closed_snapshot,
        }

    @classmethod
    def _state_from_dict(cls, data: dict) -> SessionSummaryState:
        snap = data.get("last_closed_snapshot")
        if snap is not None and not isinstance(snap, dict):
            snap = None
        return SessionSummaryState(
            pending=[AutoApproveEvent.from_dict(e) for e in (data.get("pending") or []) if isinstance(e, dict)],
            counters={
                str(k): int(v) for k, v in (data.get("counters") or {}).items()
                if str(k) in COUNT_KEYS
            },
            last_pushed_at=_parse_dt(data.get("last_pushed_at")),
            last_content_fingerprint=data.get("last_content_fingerprint"),
            last_bucket_id=data.get("last_bucket_id"),
            current_bucket_id=data.get("current_bucket_id"),
            runtime_sec=float(data.get("runtime_sec") or 0),
            last_closed_snapshot=snap,
        )

    async def load(self) -> None:
        """从 KV 恢复（无键则空状态，§5.3 迁移：无键即空）。"""
        try:
            raw = await self.plugin.get_kv_data(KV_KEY, None)
        except Exception as e:
            logger.warning("auto approve summary KV 读取失败: %s", e)
            return
        if not isinstance(raw, dict):
            return
        sessions = raw.get("sessions") or {}
        for sid, data in sessions.items():
            if not isinstance(sid, str) or not isinstance(data, dict):
                continue
            state = self._state_from_dict(data)
            if state.pending or state.last_pushed_at or state.last_closed_snapshot:
                self._sessions[sid] = state

    async def _save(self) -> None:
        try:
            live_ids = {s.get("id") for s in self.plugin.sessions_cache if s.get("id")}
            # prune：已不在 session 列表且无 pending 的条目（保留 last_* 供 status）
            for sid in list(self._sessions.keys()):
                st = self._sessions[sid]
                if sid not in live_ids and not st.pending:
                    del self._sessions[sid]
            data = {
                "sessions": {
                    sid: self._state_to_dict(st)
                    for sid, st in self._sessions.items()
                    if st.pending or st.last_pushed_at or st.last_closed_snapshot
                }
            }
            await self.plugin.put_kv_data(KV_KEY, data)
        except Exception as e:
            logger.warning("auto approve summary KV 保存失败: %s", e)
        finally:
            self._dirty = False

    # ──── 状态查询（命令 / WebUI） ────

    def status(self) -> dict[str, Any]:
        """各 session pending 条数、上次推送时间、配置与是否在托管窗。"""
        now = datetime.datetime.now()
        return {
            "enabled": self.enabled,
            "silent": bool(self._silent),
            "auto_approve_enabled": bool(self._auto_approve_enabled),
            "mode": self.mode,
            "push": self.push,
            "fixed_time": self._fixed_time,
            "in_window": self._in_window(now),
            "sessions": {
                sid: {
                    "pending": len(st.pending),
                    "last_pushed_at": st.last_pushed_at.isoformat() if st.last_pushed_at else None,
                    "last_bucket_id": st.last_bucket_id,
                    "runtime_sec": float(st.runtime_sec or 0),
                    "has_snapshot": bool(st.last_closed_snapshot),
                    "snapshot_closed_at": (
                        (st.last_closed_snapshot or {}).get("closed_at")
                        if st.last_closed_snapshot else None
                    ),
                }
                for sid, st in self._sessions.items()
                if st.pending or st.last_pushed_at or st.last_closed_snapshot
            },
        }


def _parse_dt(raw: Any) -> datetime.datetime | None:
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _event_view(e: AutoApproveEvent) -> dict[str, Any]:
    """事件 → 展示视图 dict（渲染层只消费 dict，不依赖 dataclass）。"""
    return {
        "at": e.at,
        "kind": e.kind,
        "tool": e.tool,
        "ok": e.ok,
        "detail": e.detail,
        "request_id": e.request_id,
    }
