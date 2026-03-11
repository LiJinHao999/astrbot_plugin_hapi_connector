"""会话捕获管理器：记录 session 的捕获窗口 + 窗口状态"""


class BindingManager:
    """管理 session 的捕获窗口（最近交互窗口）+ 窗口状态"""

    def __init__(self):
        self._session_owners: dict[str, list[str]] = {}  # {session_id: [umo]}
        self._window_states: dict[str, dict] = {}  # {umo: {current_session, current_flavor}}

    def _remove_owner(self, session_id: str, umo: str):
        owners = self._session_owners.get(session_id, [])
        if not owners:
            return
        next_owners = [owner for owner in owners if owner != umo]
        if next_owners:
            self._session_owners[session_id] = next_owners
        else:
            self._session_owners.pop(session_id, None)

    def bind_window(self, session_id: str, umo: str, flavor: str):
        """将 session 独占绑定到窗口，并同步窗口当前状态"""
        released_windows: list[str] = []

        previous_session = self.get_window_session(umo)
        if previous_session and previous_session != session_id:
            self._remove_owner(previous_session, umo)

        current_owners = list(self._session_owners.get(session_id, []))
        for owner in current_owners:
            if owner == umo:
                continue
            self._remove_owner(session_id, owner)
            if self.get_window_session(owner) == session_id:
                self.clear_window_state(owner)
            released_windows.append(owner)

        self._session_owners[session_id] = [umo]
        self.set_window_state(umo, session_id, flavor)
        return released_windows

    def capture(self, session_id: str, umo: str):
        """兼容旧接口：仅更新 session 的捕获窗口"""
        self._session_owners[session_id] = [umo]

    def get_owners(self, session_id: str) -> list[str]:
        """获取 session 的捕获窗口"""
        return self._session_owners.get(session_id, [])

    def get_bound_sessions(self, umo: str) -> list[str]:
        """获取窗口捕获的所有 session ID"""
        return [sid for sid, umos in self._session_owners.items() if umo in umos]

    def filter_by_flavor(self, sessions: list[dict], flavor: str) -> list[dict]:
        """按 flavor 过滤 session 列表"""
        if flavor == "all":
            return sessions
        return [s for s in sessions if s.get("metadata", {}).get("flavor") == flavor]

    def get_all_bindings(self) -> dict[str, list[str]]:
        """获取所有捕获关系"""
        return self._session_owners.copy()

    def set_window_state(self, umo: str, session_id: str, flavor: str):
        """设置窗口状态"""
        self._window_states[umo] = {"current_session": session_id, "current_flavor": flavor}

    def get_window_session(self, umo: str) -> str | None:
        """获取窗口的当前 session"""
        return self._window_states.get(umo, {}).get("current_session")

    def get_window_flavor(self, umo: str) -> str | None:
        """获取窗口的当前 flavor"""
        return self._window_states.get(umo, {}).get("current_flavor")

    def clear_window_state(self, umo: str):
        """清理窗口状态"""
        if umo in self._window_states:
            del self._window_states[umo]

    def unbind_window(self, umo: str) -> dict | None:
        """解除窗口与当前 session 的绑定"""
        state = self._window_states.pop(umo, None)
        if state:
            session_id = state.get("current_session")
            if session_id:
                self._remove_owner(session_id, umo)
            return state

        for session_id, owners in list(self._session_owners.items()):
            if umo in owners:
                self._remove_owner(session_id, umo)
                break
        return None

    def unbind_session(self, session_id: str) -> list[str]:
        """解除 session 的所有窗口绑定"""
        owners = list(self._session_owners.pop(session_id, []))
        for owner in owners:
            if self.get_window_session(owner) == session_id:
                self.clear_window_state(owner)
        return owners

    def find_window_by_session(self, session_id: str) -> str | None:
        """查找持有指定 session 的窗口"""
        for umo, state in self._window_states.items():
            if state.get("current_session") == session_id:
                return umo
        return None

    def reset_all_states(self):
        """重置所有状态（清空捕获关系和窗口状态）"""
        self._session_owners.clear()
        self._window_states.clear()
