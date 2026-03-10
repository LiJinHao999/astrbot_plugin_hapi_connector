"""会话绑定管理器：处理 session 与聊天窗口的绑定关系"""


class BindingManager:
    """管理 session 到窗口的绑定关系（单窗口绑定模式）"""

    def __init__(self):
        self._session_owners: dict[str, list[str]] = {}  # {session_id: [umo]}
        self._chat_bindings: dict[str, dict] = {}  # {umo: {session_id, flavor}}

    def bind(self, umo: str, session_id: str, flavor: str):
        """绑定 session 到窗口（覆盖旧绑定）"""
        self._session_owners[session_id] = [umo]
        self._chat_bindings[umo] = {"session_id": session_id, "flavor": flavor}

    def unbind(self, umo: str):
        """解除窗口的所有绑定"""
        to_remove = [sid for sid, umos in self._session_owners.items() if umo in umos]
        for sid in to_remove:
            self._session_owners[sid].remove(umo)
            if not self._session_owners[sid]:
                del self._session_owners[sid]
        if umo in self._chat_bindings:
            del self._chat_bindings[umo]

    def get_owners(self, session_id: str) -> list[str]:
        """获取 session 绑定的窗口列表"""
        return self._session_owners.get(session_id, [])

    def get_bound_sessions(self, umo: str) -> list[str]:
        """获取窗口绑定的所有 session ID"""
        return [sid for sid, umos in self._session_owners.items() if umo in umos]

    def get_binding(self, umo: str) -> dict | None:
        """获取窗口的绑定信息"""
        return self._chat_bindings.get(umo)

    def filter_by_flavor(self, sessions: list[dict], flavor: str) -> list[dict]:
        """按 flavor 过滤 session 列表"""
        if flavor == "all":
            return sessions
        return [s for s in sessions if s.get("metadata", {}).get("flavor") == flavor]

    def get_all_bindings(self) -> dict[str, list[str]]:
        """获取所有绑定关系"""
        return self._session_owners.copy()
