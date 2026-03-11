"""会话捕获管理器：记录 session 的捕获窗口"""


class BindingManager:
    """管理 session 的捕获窗口（最近交互窗口）"""

    def __init__(self):
        self._session_owners: dict[str, list[str]] = {}  # {session_id: [umo]}

    def capture(self, session_id: str, umo: str):
        """捕获窗口为 session 的推送目标"""
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
