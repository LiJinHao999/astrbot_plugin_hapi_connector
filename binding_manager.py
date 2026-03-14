"""会话捕获管理器：记录 session 的捕获窗口 + 窗口状态"""


class BindingManager:
    """管理 session 的捕获窗口（最近交互窗口）+ 窗口状态"""

    def __init__(self):
        self._session_owners: dict[str, str] = {}  # {session_id: umo} 一个session只能绑定一个窗口
        self._window_sessions: dict[str, list[str]] = {}  # {umo: [session_ids]} 一个窗口可以绑定多个session
        self._window_states: dict[str, dict] = {}  # {umo: {current_session, current_flavor}}

    def bind_window(self, session_id: str, umo: str, flavor: str):
        """将 session 绑定到窗口（持久绑定，一个窗口可以有多个session，一个session只能绑定一个窗口）"""
        old_owner = self._session_owners.get(session_id)

        # 如果 session 之前绑定了其他窗口，从旧窗口移除
        if old_owner and old_owner != umo:
            old_sessions = self._window_sessions.get(old_owner, [])
            self._window_sessions[old_owner] = [s for s in old_sessions if s != session_id]

        # 绑定 session 到新窗口
        self._session_owners[session_id] = umo
        if umo not in self._window_sessions:
            self._window_sessions[umo] = []
        if session_id not in self._window_sessions[umo]:
            self._window_sessions[umo].append(session_id)

        # 更新窗口当前状态
        self.set_window_state(umo, session_id, flavor)
        return []

    def capture(self, session_id: str, umo: str):
        """兼容旧接口：仅更新 session 的捕获窗口"""
        old_owner = self._session_owners.get(session_id)
        if old_owner and old_owner != umo:
            old_sessions = self._window_sessions.get(old_owner, [])
            self._window_sessions[old_owner] = [s for s in old_sessions if s != session_id]

        self._session_owners[session_id] = umo
        if umo not in self._window_sessions:
            self._window_sessions[umo] = []
        if session_id not in self._window_sessions[umo]:
            self._window_sessions[umo].append(session_id)

    def get_owners(self, session_id: str) -> list[str]:
        """获取 session 的捕获窗口（返回列表以兼容旧接口）"""
        owner = self._session_owners.get(session_id)
        return [owner] if owner else []

    def get_bound_sessions(self, umo: str) -> list[str]:
        """获取窗口捕获的所有 session ID"""
        return self._window_sessions.get(umo, [])

    def filter_by_flavor(self, sessions: list[dict], flavor: str) -> list[dict]:
        """按 flavor 过滤 session 列表"""
        if flavor == "all":
            return sessions
        return [s for s in sessions if s.get("metadata", {}).get("flavor") == flavor]

    def get_all_bindings(self) -> dict[str, list[str]]:
        """获取所有捕获关系（返回 {session_id: [umo]} 格式以兼容旧接口）"""
        result = {}
        for sid, owner in self._session_owners.items():
            result[sid] = [owner]
        return result

    def set_window_state(self, umo: str, session_id: str, flavor: str):
        """设置窗口活跃状态（不影响通知绑定）"""
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

        # 清理该窗口的所有 session 绑定
        bound_sessions = self._window_sessions.pop(umo, [])
        for sid in bound_sessions:
            if self._session_owners.get(sid) == umo:
                self._session_owners.pop(sid, None)

        return state

    def unbind_session(self, session_id: str) -> list[str]:
        """解除 session 的所有窗口绑定"""
        owner = self._session_owners.pop(session_id, None)
        if not owner:
            return []

        # 从窗口的 session 列表中移除
        if owner in self._window_sessions:
            self._window_sessions[owner] = [s for s in self._window_sessions[owner] if s != session_id]

        # 如果这是窗口的当前 session，清理窗口状态
        if self.get_window_session(owner) == session_id:
            self.clear_window_state(owner)

        return [owner]

    def find_window_by_session(self, session_id: str) -> str | None:
        """查找持有指定 session 的窗口"""
        for umo, state in self._window_states.items():
            if state.get("current_session") == session_id:
                return umo
        return None

    def reset_all_states(self):
        """重置所有状态（清空捕获关系和窗口状态）"""
        self._session_owners.clear()
        self._window_sessions.clear()
        self._window_states.clear()
