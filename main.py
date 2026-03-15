"""HAPI Connector AstrBot 插件入口
注册指令组、快捷前缀、SSE 生命周期管理
所有指令仅管理员可用
"""

import os
import time
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig, logger
from astrbot.api.message_components import Poke
import astrbot.api.message_components as Comp

from astrbot.core.utils.session_waiter import session_waiter, SessionController

from .hapi_client import AsyncHapiClient
from .cf_access import CfAccessManager
from .sse_listener import SSEListener
from .constants import PERMISSION_MODES, MODEL_MODES
from .binding_manager import BindingManager
from . import session_ops
from . import formatters
from . import file_ops
from . import approval_ops
from .create_wizard import CreateWizard
from .formatters import is_compact_request


NOTIFICATION_ROUTE_FLAVORS = ("claude", "codex", "gemini")

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
          "1.6.1")
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
        # 兼容用户从 CF 控制台一键复制时带上的请求头前缀
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

        # SSE 监听器
        self.sse_listener = SSEListener(self.client, self.sessions_cache, self._push_notification)

        # 用户状态缓存: {sender_id: {"primary_umo": ...}}
        self._user_states_cache: dict[str, dict] = {}

        # 绑定管理器
        self.binding_mgr = BindingManager()
        # 直接访问捕获窗口映射
        self._session_owners = self.binding_mgr._session_owners

        # 快捷前缀
        self._quick_prefix = self.config.get("quick_prefix", ">")

        # 戳一戳审批开关
        self._poke_approve = self.config.get("poke_approve", True)

        # summary 模式消息条数
        self._summary_msg_count = self.config.get("summary_msg_count", 5)

        # 管理员列表（用于 catch-all 处理器手动鉴权）
        astrbot_config = self.context.get_config()
        self._admin_ids = [str(x) for x in astrbot_config.get("admins_id", [])]
        self._recent_notifications: dict[tuple[str, str, str], float] = {}

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """检查发送者是否为管理员"""
        return str(event.get_sender_id()) in self._admin_ids

    # ──── 会话捕获管理 ────

    async def _persist_session_owners(self):
        """持久化 session -> 窗口路由"""
        await self.put_kv_data("session_owners", self._session_owners)

    async def _persist_window_state(self, umo: str):
        """持久化单个窗口状态；不存在时删除对应 KV"""
        window_state = self.binding_mgr._window_states.get(umo)
        await self.put_kv_data(f"window_state_{umo}", window_state if window_state else None)

    async def _capture_window(self, session_id: str, umo: str, flavor: str):
        """将 session 捕获到当前窗口，并释放旧窗口上的同 session 绑定"""
        released_umos = self.binding_mgr.bind_window(session_id, umo, flavor)
        await self._persist_session_owners()
        for released_umo in released_umos:
            await self._persist_window_state(released_umo)
        await self._persist_window_state(umo)

    async def _unbind_window(self, umo: str):
        """解除窗口当前 session 绑定"""
        self.binding_mgr.unbind_window(umo)
        await self._persist_session_owners()
        await self._persist_window_state(umo)

    async def _unbind_session(self, session_id: str):
        """解除 session 当前绑定窗口"""
        released_umos = self.binding_mgr.unbind_session(session_id)
        await self._persist_session_owners()
        for released_umo in released_umos:
            await self._persist_window_state(released_umo)

    async def _ensure_primary_session(self, event: AstrMessageEvent):
        """确保用户已有默认通知窗口；仅首次自动设置，不迁移现有窗口绑定"""
        sender_id = str(event.get_sender_id())
        umo = event.unified_msg_origin
        state = self._user_states_cache.get(sender_id, {})
        if not state.get("primary_umo"):
            await self._set_user_state(event, primary_umo=umo)
            logger.info("设置用户 %s 的主会话: %s", sender_id, umo[:20] if len(umo) > 20 else umo)
        else:
            await self._set_user_state(event)

    # ──── 生命周期 ────

    async def initialize(self):
        """插件初始化：打开 client、加载用户状态、启动 SSE"""
        await self.client.init()

        # 从 KV 加载已知用户列表
        known_users = await self.get_kv_data("known_users", [])
        for uid in known_users:
            uid = str(uid)
            state = await self.get_kv_data(f"user_state_{uid}", None)
            if state:
                self._user_states_cache[uid] = state

        # 加载会话绑定关系（兼容多会话绑定）
        stored_session_owners = await self.get_kv_data("session_owners", {})
        if isinstance(stored_session_owners, dict):
            for sid, umos in stored_session_owners.items():
                if not isinstance(sid, str):
                    continue
                # 兼容旧格式（列表）和新格式（字符串）
                if isinstance(umos, list):
                    if umos:
                        umo = str(umos[-1])
                        self._session_owners[sid] = umo
                        if umo not in self.binding_mgr._window_sessions:
                            self.binding_mgr._window_sessions[umo] = []
                        if sid not in self.binding_mgr._window_sessions[umo]:
                            self.binding_mgr._window_sessions[umo].append(sid)
                elif isinstance(umos, str):
                    self._session_owners[sid] = umos
                    if umos not in self.binding_mgr._window_sessions:
                        self.binding_mgr._window_sessions[umos] = []
                    if sid not in self.binding_mgr._window_sessions[umos]:
                        self.binding_mgr._window_sessions[umos].append(sid)

        # 加载窗口状态
        for sid, umo in self._session_owners.items():
            window_state = await self.get_kv_data(f"window_state_{umo}", None)
            if window_state:
                self.binding_mgr.set_window_state(
                    umo,
                    window_state.get("current_session", ""),
                    window_state.get("current_flavor", "")
                )

        # 执行数据迁移
        await self._migrate_to_capture_model()

        # 加载 session 缓存
        try:
            self.sessions_cache[:] = await session_ops.fetch_sessions(self.client)
        except Exception as e:
            logger.warning("初始化加载 session 列表失败: %s", e)

        # 加载已有的待审批请求（重启/断联后恢复）
        await self.sse_listener.load_existing_pending()

        # 启动 SSE
        output_level = self.config.get("output_level", "detail")
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

    async def _migrate_to_capture_model(self):
        """数据迁移：绑定模式 → 捕获+默认窗口模式"""
        migrated = False

        # 迁移用户状态
        for uid, state in list(self._user_states_cache.items()):
            modified = False

            # notify_umo → primary_umo
            if "notify_umo" in state and not state.get("primary_umo"):
                state["primary_umo"] = state["notify_umo"]
                modified = True
                logger.info("迁移用户 %s: notify_umo → primary_umo", uid)

            # 清理废弃字段
            if "notify_umo" in state:
                del state["notify_umo"]
                modified = True

            # 迁移 current_session 到窗口状态
            old_session = state.get("current_session")
            old_flavor = state.get("current_flavor")
            if old_session:
                target_umo = state.get("primary_umo")
                for sid, umos in self._session_owners.items():
                    if sid == old_session and umos:
                        target_umo = umos[0]
                        break

                if target_umo:
                    self.binding_mgr.bind_window(old_session, target_umo, old_flavor or "unknown")
                    await self._persist_session_owners()
                    await self._persist_window_state(target_umo)
                    logger.info("迁移用户 %s: current_session → window_state[%s]", uid, target_umo[:20])

            # 清理用户状态中的窗口级别字段
            if "current_session" in state:
                del state["current_session"]
                modified = True
            if "current_flavor" in state:
                del state["current_flavor"]
                modified = True

            if modified:
                self._user_states_cache[uid] = state
                await self.put_kv_data(f"user_state_{uid}", state)
                migrated = True

        # 清理废弃的 chat_bindings KV 数据
        known_chats = await self.get_kv_data("known_chats", [])
        if known_chats:
            for umo in known_chats:
                await self.put_kv_data(f"chat_binding_{umo}", None)
            logger.info("已清理 %d 个废弃的 chat_binding 数据", len(known_chats))
            migrated = True

        if migrated:
            logger.info("数据迁移完成")

    # ──── 用户状态辅助 ────

    def _get_user_state(self, event: AstrMessageEvent) -> dict:
        sender_id = str(event.get_sender_id())
        return self._user_states_cache.get(sender_id, {})

    async def _set_user_state(self, event: AstrMessageEvent, **kwargs):
        sender_id = str(event.get_sender_id())
        state = dict(self._user_states_cache.get(sender_id, {}))
        if kwargs:
            state.update(kwargs)
            self._user_states_cache[sender_id] = state
            await self.put_kv_data(f"user_state_{sender_id}", state)
        elif sender_id not in self._user_states_cache:
            self._user_states_cache[sender_id] = state

        # 维护 known_users 列表
        known = [str(uid) for uid in await self.get_kv_data("known_users", [])]
        if sender_id not in known:
            known.append(sender_id)
            await self.put_kv_data("known_users", known)

    def _current_sid(self, event: AstrMessageEvent) -> str | None:
        """获取当前窗口的会话 ID"""
        return self.binding_mgr.get_window_session(event.unified_msg_origin)

    def _current_flavor(self, event: AstrMessageEvent) -> str | None:
        """获取当前窗口的 flavor"""
        return self.binding_mgr.get_window_flavor(event.unified_msg_origin)

    def _primary_umo(self, event: AstrMessageEvent) -> str | None:
        """获取当前用户配置的默认通知窗口"""
        state = self._get_user_state(event)
        primary_umo = state.get("primary_umo")
        return str(primary_umo) if primary_umo else None

    @staticmethod
    def _normalized_flavor_primary_umos(state: dict) -> dict[str, str]:
        """Normalize persisted flavor -> default window mappings."""
        raw = state.get("flavor_primary_umos", {})
        if not isinstance(raw, dict):
            return {}

        normalized: dict[str, str] = {}
        for flavor, umo in raw.items():
            flavor_key = str(flavor).strip().lower()
            target_umo = str(umo).strip() if umo is not None else ""
            if flavor_key in NOTIFICATION_ROUTE_FLAVORS and target_umo:
                normalized[flavor_key] = target_umo
        return normalized

    def _flavor_primary_umos(self, event: AstrMessageEvent) -> dict[str, str]:
        """Get current user's flavor-specific default notification windows."""
        return self._normalized_flavor_primary_umos(self._get_user_state(event))

    def _flavor_primary_umo(self, event: AstrMessageEvent, flavor: str | None) -> str | None:
        """Get current user's flavor-specific default notification window."""
        if not flavor:
            return None
        return self._flavor_primary_umos(event).get(str(flavor).strip().lower())

    def _get_flavor_primary_windows(self, flavor: str | None) -> list[str]:
        """Return all configured default windows for the given flavor across users."""
        if not flavor:
            return []

        flavor_key = str(flavor).strip().lower()
        targets: list[str] = []
        seen: set[str] = set()
        for state in self._user_states_cache.values():
            target_umo = self._normalized_flavor_primary_umos(state).get(flavor_key)
            if not target_umo or target_umo in seen:
                continue
            seen.add(target_umo)
            targets.append(target_umo)
        return targets

    @staticmethod
    def _format_umo_for_display(umo: str | None, max_len: int = 40) -> str:
        if not umo:
            return ""
        return umo[:max_len] + "..." if len(umo) > max_len else umo

    def _user_route_summary_lines(self, event: AstrMessageEvent) -> list[str]:
        """Format current user's default notification routing summary."""
        state = self._get_user_state(event)
        lines: list[str] = []

        primary = state.get("primary_umo")
        if primary:
            lines.append(f"默认发送窗口: {self._format_umo_for_display(str(primary))}")

        flavor_routes = self._normalized_flavor_primary_umos(state)
        if flavor_routes:
            lines.append("Flavor 默认窗口:")
            for flavor in sorted(flavor_routes):
                lines.append(f"  {flavor}: {self._format_umo_for_display(flavor_routes[flavor])}")

        return lines

    def _effective_sid(self, event: AstrMessageEvent) -> str | None:
        """获取当前命令应作用的会话 ID；未显式绑定时回退到默认窗口的当前会话"""
        current_sid = self._current_sid(event)
        if current_sid:
            return current_sid

        primary_umo = self._primary_umo(event)
        if not primary_umo or primary_umo == event.unified_msg_origin:
            return None
        return self.binding_mgr.get_window_session(primary_umo)

    def _effective_flavor(self, event: AstrMessageEvent) -> str | None:
        """获取当前命令应作用会话的 flavor；回退规则与 _effective_sid 一致"""
        current_flavor = self._current_flavor(event)
        if current_flavor:
            return current_flavor

        primary_umo = self._primary_umo(event)
        if not primary_umo or primary_umo == event.unified_msg_origin:
            return None
        return self.binding_mgr.get_window_flavor(primary_umo)

    def _visible_sessions_for_window(self, event: AstrMessageEvent) -> list[dict]:
        """返回当前窗口会接收通知的 session 列表"""
        current_umo = event.unified_msg_origin
        primary_umo = self._primary_umo(event)
        flavor_umos = self._flavor_primary_umos(event)
        visible_sessions: list[dict] = []

        for session in self.sessions_cache:
            sid = session.get("id")
            if not sid:
                continue

            owners = self.binding_mgr.get_owners(sid)
            if current_umo in owners:
                visible_sessions.append(session)
                continue

            if owners:
                continue

            flavor = str(session.get("metadata", {}).get("flavor", "")).strip().lower()
            flavor_umo = flavor_umos.get(flavor)
            if flavor_umo:
                if flavor_umo == current_umo:
                    visible_sessions.append(session)
                continue

            if not owners and primary_umo == current_umo:
                visible_sessions.append(session)

        return visible_sessions

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
            self._session_owners,
            self.binding_mgr._window_states,
        )
        route_lines = self._user_route_summary_lines(event)
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

        route_lines = self._user_route_summary_lines(event)
        if route_lines:
            lines.extend(["", *route_lines])
        return "\n".join(lines)

    def _get_primary_windows(self) -> list[str]:
        """返回所有用户当前生效的默认通知窗口（去重后）"""
        targets: list[str] = []
        seen: set[str] = set()
        for state in self._user_states_cache.values():
            primary_umo = state.get("primary_umo")
            if not primary_umo or primary_umo in seen:
                continue
            seen.add(primary_umo)
            targets.append(primary_umo)
        return targets

    def _select_notification_targets(self, session_id: str) -> list[str]:
        """根据 session 选择最终通知窗口；同一通知只投递到一个窗口。"""
        if session_id:
            owners = self.binding_mgr.get_owners(session_id)
            if owners:
                return [owners[-1]]

            bound_umo = self.binding_mgr.find_window_by_session(session_id)
            if bound_umo:
                return [bound_umo]

            session = next((s for s in self.sessions_cache if s.get("id") == session_id), None)
            flavor = session.get("metadata", {}).get("flavor") if session else None
            flavor_targets = self._get_flavor_primary_windows(str(flavor).strip().lower() if flavor else None)
            if flavor_targets:
                return [flavor_targets[0]]

        primary_targets = self._get_primary_windows()
        if primary_targets:
            return [primary_targets[0]]
        return []

    @staticmethod
    def _notification_body_key(text: str) -> str:
        """Normalize label variants so duplicate notifications collapse to one body."""
        lines = text.splitlines()
        if len(lines) >= 3 and lines[0].startswith("💬 ") and lines[1].startswith("📂 ") and lines[2].startswith("🤖 "):
            lines = lines[3:]
        elif lines and lines[0].startswith("🏷️ "):
            lines = lines[1:]
        return "\n".join(line.rstrip() for line in lines).strip() or text.strip()

    @staticmethod
    def _is_request_notification(text: str) -> bool:
        return "待审批" in text and ("/hapi a" in text or "/hapi answer" in text)

    def _should_skip_duplicate_notification(self, umo: str, session_id: str, text: str) -> bool:
        """Drop short-interval duplicate notifications for the same target/session/body."""
        if self._is_request_notification(text):
            return False

        now = time.monotonic()
        dedupe_window = 2.5
        expire_before = now - 30
        for key, ts in list(self._recent_notifications.items()):
            if ts < expire_before:
                self._recent_notifications.pop(key, None)

        body_key = self._notification_body_key(text)
        cache_key = (umo, session_id or "", body_key)
        last_sent = self._recent_notifications.get(cache_key)
        if last_sent is not None and now - last_sent <= dedupe_window:
            logger.info("跳过重复通知: sid=%s umo=%s", (session_id or "global")[:8], umo[:20])
            return True

        self._recent_notifications[cache_key] = now
        return False

    async def _push_notification(self, text: str, session_id: str):
        """推送通知到单个目标窗口，优先走 session 当前路由。"""
        targets = self._select_notification_targets(session_id)

        if targets:
            for umo in targets:
                if self._should_skip_duplicate_notification(umo, session_id, text):
                    continue
                chunks = self._split_message(text) if len(text) > 4200 else [text]
                for chunk in chunks:
                    try:
                        chain = MessageChain().message(chunk)
                        await self.context.send_message(umo, chain)
                    except Exception as e:
                        logger.warning("推送到窗口失败 (umo=%s): %s", umo[:20], e)
                        break
            return

        if session_id:
            sess = next((s for s in self.sessions_cache if s["id"] == session_id), None)
            flavor = sess.get("metadata", {}).get("flavor", "unknown") if sess else "unknown"
            logger.error("Session %s [%s] 无绑定窗口且无默认窗口，推送失败", session_id[:8], flavor)
        else:
            logger.error("全局通知无可用默认窗口，推送失败")

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

    def _visible_session_ids_for_window(self, event: AstrMessageEvent) -> set[str]:
        """返回当前窗口可见的 session ID 集合。"""
        return {
            session.get("id")
            for session in self._visible_sessions_for_window(event)
            if session.get("id")
        }

    def _pending_for_event(self, event: AstrMessageEvent) -> dict[str, dict]:
        """返回当前窗口可见范围内的待审批请求。"""
        visible_sids = self._visible_session_ids_for_window(event)
        pending = self.sse_listener.get_all_pending()
        return {
            sid: reqs
            for sid, reqs in pending.items()
            if sid in visible_sids
        }

    def _flatten_pending(self, event: AstrMessageEvent | None = None) -> list[tuple[str, str, dict]]:
        pending = self.sse_listener.get_all_pending() if event is None else self._pending_for_event(event)
        return approval_ops.flatten_pending(pending)

    def _remove_pending_entry(self, sid: str, rid: str):
        approval_ops.remove_pending_entry(self.sse_listener.pending, sid, rid)

    async def _approve_pending_items(self, items: list[tuple[str, str, dict]]) -> str | None:
        """批准给定列表中的所有非 question 请求。"""
        regular = [(sid, rid, req) for sid, rid, req in items
                   if not formatters.is_question_request(req)]
        if not regular:
            return None

        results = []
        for sid, rid, req in regular:
            if is_compact_request(req):
                ok, _ = await session_ops.send_message(self.client, sid, "/compact")
                self._remove_pending_entry(sid, rid)
                results.append(f"{'✓' if ok else '✗'} /compact")
            else:
                ok, _ = await session_ops.approve_permission(self.client, sid, rid)
                tool = req.get("tool", "?")
                results.append(f"{'✓' if ok else '✗'} {tool}")

        return f"已全部批准 ({len(regular)} 个):\n" + "\n".join(results)

    async def _answer_questions_interactive(self, event: AstrMessageEvent,
                                             q_items: list) -> bool:
        """交互式逐个回答 question 请求，返回是否全部完成"""
        for qi_idx, (sid, rid, req) in enumerate(q_items):
            args = req.get("arguments") or {}
            questions = args.get("questions", []) if isinstance(args, dict) else []
            answers = {}

            for qi, q in enumerate(questions):
                opts = q.get("options", [])
                prompt = approval_ops.build_question_prompt(
                    q_items, qi_idx, qi, q, self.sessions_cache)
                await event.send(event.plain_result(prompt))

                collected = []

                @session_waiter(timeout=60, record_history_chains=False)
                async def q_waiter(controller: SessionController, ev: AstrMessageEvent,
                                   _opts=opts, _collected=collected, _state={'other': False}):
                    reply = ev.message_str.strip()
                    if not reply:
                        controller.keep(timeout=60, reset_timeout=True)
                        return
                    if _state['other']:
                        _collected.append(reply)
                        controller.stop()
                    elif reply.isdigit() and 1 <= int(reply) <= len(_opts):
                        _collected.append(_opts[int(reply) - 1]["label"])
                        controller.stop()
                    elif reply.isdigit() and int(reply) == len(_opts) + 1:
                        _state['other'] = True
                        await ev.send(ev.plain_result("请输入自定义回答:"))
                        controller.keep(timeout=60, reset_timeout=True)
                    else:
                        _collected.append(reply)
                        controller.stop()

                try:
                    await q_waiter(event)
                except TimeoutError:
                    await event.send(event.plain_result("操作超时，已取消"))
                    return False

                answers[str(qi)] = collected

            ok, msg = await session_ops.approve_permission(
                self.client, sid, rid, answers=answers)
            await event.send(event.plain_result(msg))

        return True

    # ──── 指令组 ────

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("hapi")
    async def cmd_hapi_router(self, event: AstrMessageEvent, raw: str = ""):
        """统一处理 /hapi 路由与帮助提示"""
        remainder = self._extract_hapi_remainder(event, raw)
        if not remainder:
            await self._ensure_primary_session(event)
            async for result in self.cmd_help(event, ""):
                yield result
            return

        parts = remainder.split(None, 1)
        subcommand = parts[0].lower()
        argument = parts[1] if len(parts) > 1 else ""
        routes = {
            "help": (self.cmd_help, True),
            "帮助": (self.cmd_help, True),
            "list": (self.cmd_list, True),
            "ls": (self.cmd_list, True),
            "sw": (self.cmd_sw, True),
            "s": (self.cmd_status, False),
            "status": (self.cmd_status, False),
            "msg": (self.cmd_msg, True),
            "messages": (self.cmd_msg, True),
            "to": (self.cmd_to, True),
            "perm": (self.cmd_perm, True),
            "model": (self.cmd_model, True),
            "remote": (self.cmd_remote, False),
            "output": (self.cmd_output, True),
            "out": (self.cmd_output, True),
            "pending": (self.cmd_pending, False),
            "approve": (self.cmd_approve, False),
            "a": (self.cmd_approve, False),
            "allow": (self.cmd_allow, True),
            "answer": (self.cmd_answer, True),
            "deny": (self.cmd_deny, True),
            "create": (self.cmd_create, False),
            "abort": (self.cmd_abort, True),
            "stop": (self.cmd_abort, True),
            "archive": (self.cmd_archive, False),
            "rename": (self.cmd_rename, False),
            "delete": (self.cmd_delete, False),
            "clean": (self.cmd_clean, True),
            "files": (self.cmd_files, True),
            "file": (self.cmd_files, True),
            "find": (self.cmd_find, True),
            "download": (self.cmd_download, True),
            "dl": (self.cmd_download, True),
            "upload": (self.cmd_upload, True),
            "bind": (self.cmd_bind, True),
            "routes": (self.cmd_routes, False),
        }
        route = routes.get(subcommand)
        if route is None:
            yield event.plain_result(formatters.format_unknown_command_help(subcommand))
            return

        await self._ensure_primary_session(event)
        handler, takes_arg = route
        if takes_arg:
            async for result in handler(event, argument):
                yield result
        else:
            async for result in handler(event):
                yield result

    # ── help ──

    async def cmd_help(self, event: AstrMessageEvent, topic: str = ""):
        """显示帮助信息，可按主题查看"""
        await self._set_user_state(event)
        if w := self._conn_warning():
            yield event.plain_result(w)
        yield event.plain_result(formatters.get_help_text(topic))

    # ── list ──

    async def cmd_list(self, event: AstrMessageEvent, scope: str = ""):
        """列出 session: /hapi list [all]"""
        await self._ensure_primary_session(event)
        await self._set_user_state(event)
        if w := self._conn_warning():
            yield event.plain_result(w)

        normalized_scope = (scope or "").strip().lower()
        if not normalized_scope:
            remainder = self._extract_hapi_remainder(event).lower()
            parts = remainder.split(None, 1)
            if parts and parts[0] in ("list", "ls"):
                normalized_scope = parts[1].strip() if len(parts) > 1 else ""

        scope_head = normalized_scope.split(None, 1)[0] if normalized_scope else ""
        if scope_head == "all":
            text = await self._format_bind_status_text(event)
            yield event.plain_result(text)
            return

        await self._refresh_sessions()
        machine_hint = await self._machine_status_hint()

        visible_sessions = self._visible_sessions_for_window(event)
        if not visible_sessions:
            text = self._format_no_visible_sessions_text(event)
            if machine_hint:
                text += "\n\n" + machine_hint
            yield event.plain_result(text)
            return

        current_sid = self._effective_sid(event)
        text = formatters.format_session_list(
            visible_sessions,
            current_sid,
            self.sessions_cache,
            header_current_window=event.unified_msg_origin,
        )

        if machine_hint:
            text += "\n\n" + machine_hint

        yield event.plain_result(text)

    # ── sw ──

    async def cmd_sw(self, event: AstrMessageEvent, target: str = ""):
        """切换当前 session: /hapi sw <序号或ID前缀>"""
        await self._ensure_primary_session(event)

        if not target:
            await self._refresh_sessions()
            current_sid = self._effective_sid(event)
            text = formatters.format_session_list(
                self.sessions_cache,
                current_sid,
                header_current_window=event.unified_msg_origin,
            )
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
        umo = event.unified_msg_origin
        await self._capture_window(sid, umo, flavor)
        summary = chosen.get("metadata", {}).get("summary", {}).get("text", "(无标题)")
        yield event.plain_result(f"已切换到 [{flavor}] {sid[:8]}... {summary}")

    # ── s (status) ──

    async def cmd_status(self, event: AstrMessageEvent):
        """查看当前 session 状态"""
        await self._ensure_primary_session(event)
        await self._set_user_state(event)
        sid = self._effective_sid(event)
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

    async def cmd_msg(self, event: AstrMessageEvent, rounds: str = ""):
        """查看最近消息（按轮次）: /hapi msg [轮数]"""
        await self._set_user_state(event)
        sid = self._effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return
        rounds_int = int(rounds) if rounds.isdigit() and int(rounds) >= 1 else 1
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
                for chunk in self._split_message(text):
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

        await self._refresh_sessions()
        if idx < 1 or idx > len(self.sessions_cache):
            yield event.plain_result(f"无效序号，共 {len(self.sessions_cache)} 个 session")
            return

        target = self.sessions_cache[idx - 1]
        target_sid = target["id"]
        target_flavor = target.get("metadata", {}).get("flavor", "claude")
        ok, msg = await session_ops.send_message(self.client, target_sid, text)
        if ok:
            await self._capture_window(target_sid, event.unified_msg_origin, target_flavor)
        await self._set_user_state(event)
        yield event.plain_result(msg)

    # ── perm ──

    async def cmd_perm(self, event: AstrMessageEvent, mode: str = ""):
        """查看/切换权限模式: /hapi perm [模式名]"""
        await self._set_user_state(event)
        sid = self._effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return

        flavor = self._effective_flavor(event) or "claude"
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
                if not reply:
                    controller.keep(timeout=30, reset_timeout=True)
                    return
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

    async def cmd_model(self, event: AstrMessageEvent, mode: str = ""):
        """查看/切换模型: /hapi model [模式名]"""
        await self._set_user_state(event)
        sid = self._effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return

        flavor = self._effective_flavor(event) or "claude"
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
                if not reply:
                    controller.keep(timeout=30, reset_timeout=True)
                    return
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

    # ── remote ──

    async def cmd_remote(self, event: AstrMessageEvent):
        """切换当前 session 到 remote 远程托管模式"""
        await self._set_user_state(event)
        sid = self._effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return
        ok, msg = await session_ops.switch_to_remote(self.client, sid)
        yield event.plain_result(msg)

    # ── output ──

    _OUTPUT_LEVELS = {
        "silence": "仅推送权限请求和任务完成提醒",
        "summary": "任务完成时推送最近的 agent 消息",
        "simple": "仅推送 agent 文本消息，不包含复杂的工具调用信息",
        "detail": "实时推送所有新消息（信息量较大）",
    }

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
                if not reply:
                    controller.keep(timeout=30, reset_timeout=True)
                    return
                t = reply
                if reply.isdigit() and 1 <= int(reply) <= len(levels):
                    t = levels[int(reply) - 1]
                if t not in self._OUTPUT_LEVELS:
                    await ev.send(ev.plain_result(f"无效级别，可用: {', '.join(levels)}"))
                else:
                    self.sse_listener.output_level = t
                    self.config["output_level"] = t
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
        self.config["output_level"] = target
        yield event.plain_result(
            f"SSE 推送级别已切换为: {target}\n{self._OUTPUT_LEVELS[target]}")

    # ── pending (查看待审批列表) ──

    async def cmd_pending(self, event: AstrMessageEvent):
        """查看待审批请求列表: /hapi pending"""
        await self._set_user_state(event)
        pending = self._pending_for_event(event)
        text = formatters.format_pending_requests(pending, self.sessions_cache)
        yield event.plain_result(text)

    # ── approve ──

    async def cmd_approve(self, event: AstrMessageEvent):
        """批准所有权限请求，再交互式回答 question: /hapi a"""
        await self._set_user_state(event)
        items = self._flatten_pending(event)
        if not items:
            yield event.plain_result("没有待审批的请求")
            return

        regular = [(sid, rid, req) for sid, rid, req in items
                   if not formatters.is_question_request(req)]
        questions = [(sid, rid, req) for sid, rid, req in items
                     if formatters.is_question_request(req)]

        if regular:
            result = await self._approve_pending_items(regular)
            if result:
                yield event.plain_result(result)

        if questions:
            yield event.plain_result(f"还有 {len(questions)} 个问题需要回答:")
            await self._answer_questions_interactive(event, questions)

        event.stop_event()

    # ── allow ──

    async def cmd_allow(self, event: AstrMessageEvent, target: str = ""):
        """批准权限请求（跳过 question）: /hapi allow [序号]"""
        await self._set_user_state(event)
        items = self._flatten_pending(event)
        regular = [(sid, rid, req) for sid, rid, req in items
                   if not formatters.is_question_request(req)]

        if not regular:
            yield event.plain_result("没有待批准的权限请求")
            return

        raw = (target or "").strip()
        if raw and raw.isdigit():
            n = int(raw)
            if n < 1 or n > len(regular):
                yield event.plain_result(f"无效序号，当前共 {len(regular)} 个待批准权限请求")
                return
            sid, rid, req = regular[n - 1]
            if is_compact_request(req):
                ok, _ = await session_ops.send_message(self.client, sid, "/compact")
                self._remove_pending_entry(sid, rid)
                yield event.plain_result(f"{'✓' if ok else '✗'} 已批准: /compact")
            else:
                ok, _ = await session_ops.approve_permission(self.client, sid, rid)
                tool = req.get("tool", "?")
                yield event.plain_result(f"{'✓' if ok else '✗'} 已批准: {tool}")
        else:
            result = await self._approve_pending_items(regular)
            if result:
                yield event.plain_result(result)

    # ── answer ──

    async def cmd_answer(self, event: AstrMessageEvent, target: str = ""):
        """交互式回答 question 请求: /hapi answer [序号]"""
        await self._set_user_state(event)
        items = self._flatten_pending(event)
        q_items = [(sid, rid, req) for sid, rid, req in items
                   if formatters.is_question_request(req)]

        if not q_items:
            yield event.plain_result("没有待回答的问题")
            return

        raw = (target or event.message_str).strip()
        if raw and raw.isdigit():
            n = int(raw)
            if n < 1 or n > len(q_items):
                yield event.plain_result(f"无效序号，当前共 {len(q_items)} 个待回答问题")
                return
            q_items = [q_items[n - 1]]

        await self._answer_questions_interactive(event, q_items)
        event.stop_event()

    # ── deny ──

    async def cmd_deny(self, event: AstrMessageEvent, target: str = ""):
        """拒绝审批请求: /hapi deny 全部拒绝, /hapi deny <序号> 拒绝单个"""
        await self._set_user_state(event)
        items = self._flatten_pending(event)
        if not items:
            yield event.plain_result("没有待审批的请求")
            return

        raw = (target or "").strip()
        if raw and raw.isdigit():
            # 拒绝单个
            n = int(raw)
            if n < 1 or n > len(items):
                yield event.plain_result(f"无效序号，当前共 {len(items)} 个待审批")
                return
            sid, rid, req = items[n - 1]
            if is_compact_request(req):
                self._remove_pending_entry(sid, rid)
                yield event.plain_result("✓ 已取消压缩: /compact")
            else:
                ok, msg = await session_ops.deny_permission(self.client, sid, rid)
                tool = req.get("tool", "?")
                yield event.plain_result(f"{'✓' if ok else '✗'} 已拒绝: {tool}")
        else:
            # 全部拒绝
            results = []
            for sid, rid, req in items:
                if is_compact_request(req):
                    self._remove_pending_entry(sid, rid)
                    results.append("✓ /compact (已取消)")
                else:
                    ok, msg = await session_ops.deny_permission(self.client, sid, rid)
                    tool = req.get("tool", "?")
                    results.append(f"{'✓' if ok else '✗'} {tool}")
            yield event.plain_result(f"已全部拒绝 ({len(items)} 个):\n" + "\n".join(results))

    # ── create ──

    async def cmd_create(self, event: AstrMessageEvent):
        """创建新 session (5 步向导)"""
        await self._ensure_primary_session(event)
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
                s = wiz.state
                ok, msg, new_sid = await session_ops.spawn_session(
                    self.client,
                    machine_id=s["machine_id"],
                    directory=s["directory"],
                    agent=s["agent"],
                    session_type=s["session_type"],
                    yolo=s["yolo"],
                    worktree_name=s["worktree_name"],
                )
                await self._refresh_sessions()
                if ok and new_sid:
                    flavor = s["agent"]
                    umo = ev.unified_msg_origin
                    await self._capture_window(new_sid, umo, flavor)
                    msg += f"\n已自动切换到该 session [{flavor}] {new_sid[:8]}..."
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
        await self._set_user_state(event)
        await self._refresh_sessions()

        if not target:
            sid = self._effective_sid(event)
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

    async def cmd_archive(self, event: AstrMessageEvent, target: str = ""):
        """归档 session: /hapi archive [序号或ID前缀]"""
        await self._set_user_state(event)

        if target:
            await self._refresh_sessions()
            sid = self._resolve_target(target)
            if not sid:
                yield event.plain_result(f"未找到匹配的 session: {target}")
                return
        else:
            sid = self._effective_sid(event)
            if not sid:
                yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session，或使用 /hapi archive <序号>")
                return

        yield event.plain_result(f"确认归档 session [{sid[:8]}]?\n回复 y 确认")

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

    async def cmd_rename(self, event: AstrMessageEvent, target: str = ""):
        """重命名 session: /hapi rename [序号或ID前缀]"""
        await self._set_user_state(event)

        if target:
            await self._refresh_sessions()
            sid = self._resolve_target(target)
            if not sid:
                yield event.plain_result(f"未找到匹配的 session: {target}")
                return
        else:
            sid = self._effective_sid(event)
            if not sid:
                yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session，或使用 /hapi rename <序号>")
                return

        yield event.plain_result(f"请输入 session [{sid[:8]}] 的新名称:")

        @session_waiter(timeout=60, record_history_chains=False)
        async def rename_waiter(controller: SessionController, ev: AstrMessageEvent):
            new_name = ev.message_str.strip()
            if not new_name:
                controller.keep(timeout=60, reset_timeout=True)
                return
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

    async def cmd_delete(self, event: AstrMessageEvent, target: str = ""):
        """删除 session: /hapi delete [序号或ID前缀]"""
        await self._set_user_state(event)

        # 支持传入序号或 ID 前缀
        if target:
            await self._refresh_sessions()
            sid = self._resolve_target(target)
            if not sid:
                yield event.plain_result(f"未找到匹配的 session: {target}")
                return
        else:
            sid = self._effective_sid(event)
            if not sid:
                yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session，或使用 /hapi delete <序号>")
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
                    await self._unbind_session(sid)
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

    # ── clean ──

    async def cmd_clean(self, event: AstrMessageEvent, path: str = ""):
        """清理 inactive sessions: /hapi clean [路径]"""
        await self._set_user_state(event)
        await self._refresh_sessions()

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
        yield event.plain_result(f"{warning}\n将删除以下 inactive sessions:\n\n{summary}\n\n输入 yes 确认:")

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
                await ev.send(ev.plain_result(f"清理完成: {success}/{len(targets)}\n\n💡 列表编号已更新，请用 /hapi ls 查看最新编号"))
                if success > 0:
                    await self._refresh_sessions()
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
        await self._set_user_state(event)
        if w := self._conn_warning():
            yield event.plain_result(w)
        sid = self._effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return
        # 解析 -l 参数
        parts = path.split()
        detail = "-l" in parts
        parts = [p for p in parts if p != "-l"]
        path = parts[0] if parts else "."
        try:
            entries = await session_ops.list_directory(self.client, sid, path=path)
            text = formatters.format_directory(entries, path=path, detail=detail, sid=sid)
            for chunk in self._split_message(text):
                yield event.plain_result(chunk)
        except Exception as e:
            yield event.plain_result(f"获取目录失败: {e}")

    # ── find ──

    async def cmd_find(self, event: AstrMessageEvent, query: str = ""):
        """搜索远端文件: /hapi find <关键词>"""
        await self._set_user_state(event)
        if w := self._conn_warning():
            yield event.plain_result(w)
        sid = self._effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
            return
        if not query:
            yield event.plain_result("用法: /hapi find <关键词>\n示例: /hapi find main.py")
            return
        try:
            files = await session_ops.list_files(self.client, sid, query=query)
            text = formatters.format_file_search(files, query=query)
            for chunk in self._split_message(text):
                yield event.plain_result(chunk)
        except Exception as e:
            yield event.plain_result(f"搜索文件失败: {e}")

    # ── download ──

    async def cmd_download(self, event: AstrMessageEvent, path: str = ""):
        """下载远端文件到聊天: /hapi download <路径>"""
        await self._set_user_state(event)
        if w := self._conn_warning():
            yield event.plain_result(w)
        sid = self._effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
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

    async def cmd_upload(self, event: AstrMessageEvent, action: str = ""):
        """上传文件到当前 session: /hapi upload [cancel]"""
        await self._ensure_primary_session(event)
        sid = self._effective_sid(event)
        if not sid:
            yield event.plain_result("请先用 /hapi sw <序号> 选择一个 session")
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
                flavor = self._effective_flavor(ev)
                summary += f"\n\n已上传 {len(attachments)} 个文件到 [{flavor}] {sid[:8]}"
                await ev.send(ev.plain_result(summary))
                controller.stop()
                return

            await ev.send(ev.plain_result("未检测到文件，请重新发送"))
            controller.keep(timeout=120, reset_timeout=True)

        try:
            await upload_waiter(event)
        except TimeoutError:
            yield event.plain_result("操作超时，已取消")
        finally:
            event.stop_event()

    # ── bind ──

    async def cmd_bind(self, event: AstrMessageEvent, arg: str = ""):
        """设置默认发送窗口: /hapi bind [claude|codex|gemini|status|reset]"""
        await self._ensure_primary_session(event)
        sender_id = str(event.get_sender_id())
        umo = event.unified_msg_origin
        action = (arg or "").strip().lower()

        if not action:
            # 设置当前窗口为默认
            state = self._user_states_cache.get(sender_id, {})
            state["primary_umo"] = umo
            self._user_states_cache[sender_id] = state
            await self.put_kv_data(f"user_state_{sender_id}", state)
            yield event.plain_result("✓ 已设置当前窗口为默认发送窗口")
        elif action in NOTIFICATION_ROUTE_FLAVORS:
            state = dict(self._user_states_cache.get(sender_id, {}))
            flavor_routes = self._normalized_flavor_primary_umos(state)
            flavor_routes[action] = umo
            state["flavor_primary_umos"] = flavor_routes
            self._user_states_cache[sender_id] = state
            await self.put_kv_data(f"user_state_{sender_id}", state)
            yield event.plain_result(f"✓ 已设置当前窗口为 {action} 默认发送窗口")
        elif action == "status":
            text = await self._format_bind_status_text(event)
            yield event.plain_result(text)
        elif action == "reset":
            async for result in self.cmd_reset(event):
                yield result
        else:
            yield event.plain_result(
                "用法:\n"
                "  /hapi bind              设置当前窗口为默认\n"
                "  /hapi bind claude       设置当前窗口为 claude 默认\n"
                "  /hapi bind codex        设置当前窗口为 codex 默认\n"
                "  /hapi bind gemini       设置当前窗口为 gemini 默认\n"
                "  /hapi bind status       查看推送路由\n"
                "  /hapi bind reset        重置窗口路由"
            )

    # ── routes ──

    async def cmd_routes(self, event: AstrMessageEvent):
        """查看会话推送路由"""
        await self._ensure_primary_session(event)
        await self._refresh_sessions()

        lines = ["会话推送路由："]
        has_routes = False

        for sid, umo in self._session_owners.items():
            s = next((s for s in self.sessions_cache if s["id"] == sid), None)
            if s and umo:
                flavor = s.get("metadata", {}).get("flavor", "?")
                summary = s.get("metadata", {}).get("summary", {}).get("text", "")[:20]
                umo_display = umo[:40] + "..." if len(umo) > 40 else umo
                lines.append(f"  [{flavor}] {sid[:8]} {summary}\n    → {umo_display}")
                has_routes = True

        sender_id = str(event.get_sender_id())
        state = self._user_states_cache.get(sender_id, {})
        primary = state.get("primary_umo")

        if primary:
            display = self._format_umo_for_display(str(primary))
            lines.append(f"\n默认发送窗口: {display}")
            has_routes = True

        flavor_routes = self._normalized_flavor_primary_umos(state)
        if flavor_routes:
            lines.append("\nFlavor 默认窗口:")
            for flavor in sorted(flavor_routes):
                display = self._format_umo_for_display(flavor_routes[flavor])
                lines.append(f"  {flavor} -> {display}")
            has_routes = True

        if not has_routes:
            yield event.plain_result("暂无推送路由\n使用 /hapi bind 设置默认发送窗口")
        else:
            yield event.plain_result("\n".join(lines))

    # ── reset ──

    async def cmd_reset(self, event: AstrMessageEvent):
        """重置所有状态（/hapi bind reset；清空捕获关系和窗口状态，保留默认窗口和 flavor 默认路由）"""
        await self._ensure_primary_session(event)

        umos_to_clear = set(self.binding_mgr._window_states.keys())
        for owners in self._session_owners.values():
            umos_to_clear.update(owners)

        self.binding_mgr.reset_all_states()

        await self.put_kv_data("session_owners", {})
        for umo in umos_to_clear:
            await self.put_kv_data(f"window_state_{umo}", None)

        await self._refresh_sessions()

        yield event.plain_result("✓ 已重置所有状态\n捕获关系和窗口状态已清空，默认窗口和 flavor 默认路由已保留")

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
        items = self._flatten_pending(event)
        if not items:
            return  # 无待审批，静默

        regular = [(sid, rid, req) for sid, rid, req in items
                   if not formatters.is_question_request(req)]
        questions = [(sid, rid, req) for sid, rid, req in items
                     if formatters.is_question_request(req)]

        if regular:
            result = await self._approve_pending_items(regular)
            if result:
                yield event.plain_result(f"[戳一戳审批] {result}")

        if questions:
            yield event.plain_result(f"[戳一戳审批] 还有 {len(questions)} 个问题需要回答:")
            await self._answer_questions_interactive(event, questions)

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
        prefix = self._quick_prefix
        raw = event.message_str

        if not raw or not raw.startswith(prefix):
            return  # 不匹配，不拦截

        if not self._is_admin(event):
            return  # 非管理员，静默忽略

        await self._ensure_primary_session(event)
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
            target_sid = self._effective_sid(event)
            target_flavor = self._effective_flavor(event) or "claude"

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
        ok, msg = await session_ops.send_message(self.client, target_sid, text, attachments)
        if ok:
            await self._capture_window(target_sid, event.unified_msg_origin, target_flavor)
        await self._set_user_state(event)
        yield event.plain_result(msg)
        event.stop_event()

