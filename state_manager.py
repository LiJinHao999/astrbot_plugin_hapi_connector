"""用户状态和通知窗口绑定管理"""

from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger
from .binding_manager import BindingManager


NOTIFICATION_ROUTE_FLAVORS = ("claude", "codex", "gemini")


class StateManager:
    """管理用户状态、通知窗口绑定、路由"""

    def __init__(self, kv_helper, binding_mgr: BindingManager):
        self.kv = kv_helper
        self.binding_mgr = binding_mgr
        self._user_states_cache: dict[str, dict] = {}
        self._session_owners = binding_mgr._session_owners

    # ──── 持久化 ────

    async def persist_session_owners(self):
        """持久化 session -> 窗口路由"""
        await self.kv.put_kv_data("session_owners", self._session_owners)

    async def persist_window_state(self, umo: str):
        """持久化单个窗口状态；不存在时删除对应 KV"""
        window_state = self.binding_mgr._window_states.get(umo)
        await self.kv.put_kv_data(f"window_state_{umo}", window_state if window_state else None)

    # ──── 通知窗口绑定 ────

    async def capture_window(self, session_id: str, umo: str, flavor: str):
        """将 session 捕获到当前窗口，并释放旧窗口上的同 session 绑定"""
        released_umos = self.binding_mgr.bind_window(session_id, umo, flavor)
        await self.persist_session_owners()
        for released_umo in released_umos:
            await self.persist_window_state(released_umo)
        await self.persist_window_state(umo)

    async def unbind_window(self, umo: str):
        """解除窗口当前 session 绑定"""
        self.binding_mgr.unbind_window(umo)
        await self.persist_session_owners()
        await self.persist_window_state(umo)

    async def unbind_session(self, session_id: str):
        """解除 session 当前绑定窗口"""
        released_umos = self.binding_mgr.unbind_session(session_id)
        await self.persist_session_owners()
        for released_umo in released_umos:
            await self.persist_window_state(released_umo)

    # ──── 用户状态 ────

    def get_user_state(self, event: AstrMessageEvent) -> dict:
        sender_id = str(event.get_sender_id())
        return self._user_states_cache.get(sender_id, {})

    async def set_user_state(self, event: AstrMessageEvent, **kwargs):
        sender_id = str(event.get_sender_id())
        state = dict(self._user_states_cache.get(sender_id, {}))
        if kwargs:
            state.update(kwargs)
            self._user_states_cache[sender_id] = state
            await self.kv.put_kv_data(f"user_state_{sender_id}", state)
        elif sender_id not in self._user_states_cache:
            self._user_states_cache[sender_id] = state

        # 存储消息ID（用于QQ官渠回复）
        if hasattr(event, 'message_id') and event.message_id:
            self.binding_mgr.set_window_message_id(event.unified_msg_origin, str(event.message_id))

        # 维护 known_users 列表
        known = [str(uid) for uid in await self.kv.get_kv_data("known_users", [])]
        if sender_id not in known:
            known.append(sender_id)
            await self.kv.put_kv_data("known_users", known)

    async def ensure_primary_session(self, event: AstrMessageEvent):
        """确保用户已有默认通知窗口；仅首次自动设置，不迁移现有窗口绑定"""
        sender_id = str(event.get_sender_id())
        umo = event.unified_msg_origin
        state = self._user_states_cache.get(sender_id, {})
        if not state.get("primary_umo"):
            await self.set_user_state(event, primary_umo=umo)
            logger.info("设置用户 %s 的主会话: %s", sender_id, umo[:20] if len(umo) > 20 else umo)
        else:
            await self.set_user_state(event)

    # ──── 状态查询 ────

    def current_sid(self, event: AstrMessageEvent) -> str | None:
        """获取当前窗口的会话 ID"""
        return self.binding_mgr.get_window_session(event.unified_msg_origin)

    def current_flavor(self, event: AstrMessageEvent) -> str | None:
        """获取当前窗口的 flavor"""
        return self.binding_mgr.get_window_flavor(event.unified_msg_origin)

    def primary_umo(self, event: AstrMessageEvent) -> str | None:
        """获取当前用户配置的默认通知窗口"""
        state = self.get_user_state(event)
        primary_umo = state.get("primary_umo")
        return str(primary_umo) if primary_umo else None

    @staticmethod
    def normalized_flavor_primary_umos(state: dict) -> dict[str, str]:
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

    def flavor_primary_umos(self, event: AstrMessageEvent) -> dict[str, str]:
        """Get current user's flavor-specific default notification windows."""
        return self.normalized_flavor_primary_umos(self.get_user_state(event))

    def flavor_primary_umo(self, event: AstrMessageEvent, flavor: str | None) -> str | None:
        """Get current user's flavor-specific default notification window."""
        if not flavor:
            return None
        return self.flavor_primary_umos(event).get(str(flavor).strip().lower())

    def effective_sid(self, event: AstrMessageEvent) -> str | None:
        """获取当前命令应作用的会话 ID；未显式绑定时回退到默认窗口的当前会话"""
        current_sid = self.current_sid(event)
        if current_sid:
            return current_sid

        primary_umo = self.primary_umo(event)
        if not primary_umo or primary_umo == event.unified_msg_origin:
            return None
        return self.binding_mgr.get_window_session(primary_umo)

    def effective_flavor(self, event: AstrMessageEvent) -> str | None:
        """获取当前命令应作用会话的 flavor；回退规则与 effective_sid 一致"""
        current_flavor = self.current_flavor(event)
        if current_flavor:
            return current_flavor

        primary_umo = self.primary_umo(event)
        if not primary_umo or primary_umo == event.unified_msg_origin:
            return None
        return self.binding_mgr.get_window_flavor(primary_umo)

    def visible_sessions_for_window(self, event: AstrMessageEvent, sessions_cache: list[dict]) -> list[dict]:
        """返回当前窗口会接收通知的 session 列表"""
        current_umo = event.unified_msg_origin
        primary_umo = self.primary_umo(event)
        flavor_umos = self.flavor_primary_umos(event)
        visible_sessions: list[dict] = []

        for session in sessions_cache:
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

    # ──── 路由管理 ────

    def get_flavor_primary_windows(self, flavor: str | None) -> list[str]:
        """Return all configured default windows for the given flavor across users."""
        if not flavor:
            return []

        flavor_key = str(flavor).strip().lower()
        targets: list[str] = []
        seen: set[str] = set()
        for state in self._user_states_cache.values():
            target_umo = self.normalized_flavor_primary_umos(state).get(flavor_key)
            if not target_umo or target_umo in seen:
                continue
            seen.add(target_umo)
            targets.append(target_umo)
        return targets

    def get_primary_windows(self) -> list[str]:
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

    def select_notification_targets(self, session_id: str, sessions_cache: list[dict]) -> list[str]:
        """根据 session 选择最终通知窗口；同一通知只投递到一个窗口。"""
        if session_id:
            owners = self.binding_mgr.get_owners(session_id)
            if owners:
                return [owners[-1]]

            bound_umo = self.binding_mgr.find_window_by_session(session_id)
            if bound_umo:
                return [bound_umo]

            session = next((s for s in sessions_cache if s.get("id") == session_id), None)
            flavor = session.get("metadata", {}).get("flavor") if session else None
            flavor_targets = self.get_flavor_primary_windows(str(flavor).strip().lower() if flavor else None)
            if flavor_targets:
                return [flavor_targets[0]]

        primary_targets = self.get_primary_windows()
        if primary_targets:
            return [primary_targets[0]]
        return []

    @staticmethod
    def format_umo_for_display(umo: str | None, max_len: int = 40) -> str:
        if not umo:
            return ""
        return umo[:max_len] + "..." if len(umo) > max_len else umo

    def user_route_summary_lines(self, event: AstrMessageEvent) -> list[str]:
        """Format current user's default notification routing summary."""
        state = self.get_user_state(event)
        lines: list[str] = []

        primary = state.get("primary_umo")
        if primary:
            lines.append(f"默认发送窗口: {self.format_umo_for_display(str(primary))}")

        flavor_routes = self.normalized_flavor_primary_umos(state)
        if flavor_routes:
            lines.append("Flavor 默认窗口:")
            for flavor in sorted(flavor_routes):
                lines.append(f"  {flavor}: {self.format_umo_for_display(flavor_routes[flavor])}")

        return lines

    # ──── 数据加载 ────

    async def load_all(self):
        """从 KV 加载所有状态"""
        # 加载用户状态
        known_users = await self.kv.get_kv_data("known_users", [])
        for uid in known_users:
            uid = str(uid)
            state = await self.kv.get_kv_data(f"user_state_{uid}", None)
            if state:
                self._user_states_cache[uid] = state

        # 加载会话绑定关系（兼容多会话绑定）
        stored_session_owners = await self.kv.get_kv_data("session_owners", {})
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
            window_state = await self.kv.get_kv_data(f"window_state_{umo}", None)
            if window_state:
                self.binding_mgr.set_window_state(
                    umo,
                    window_state.get("current_session", ""),
                    window_state.get("current_flavor", "")
                )

    async def migrate_to_capture_model(self):
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
                    await self.persist_session_owners()
                    await self.persist_window_state(target_umo)
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
                await self.kv.put_kv_data(f"user_state_{uid}", state)
                migrated = True

        # 清理废弃的 chat_bindings KV 数据
        known_chats = await self.kv.get_kv_data("known_chats", [])
        if known_chats:
            for umo in known_chats:
                await self.kv.put_kv_data(f"chat_binding_{umo}", None)
            logger.info("已清理 %d 个废弃的 chat_binding 数据", len(known_chats))
            migrated = True

        if migrated:
            logger.info("数据迁移完成")
