"""通知推送和去重管理"""

import time
from astrbot.api.event import MessageChain
from astrbot.api import logger


class NotificationManager:
    """处理 SSE 事件通知的推送和去重"""

    def __init__(self, context, state_mgr):
        self.context = context
        self.state_mgr = state_mgr
        self._recent_notifications: dict[tuple[str, str, str], float] = {}
        self._event_cache: dict[str, any] = {}

    @staticmethod
    def notification_body_key(text: str) -> str:
        """Normalize label variants so duplicate notifications collapse to one body."""
        lines = text.splitlines()
        if len(lines) >= 3 and lines[0].startswith("💬 ") and lines[1].startswith("📂 ") and lines[2].startswith("🤖 "):
            lines = lines[3:]
        elif lines and lines[0].startswith("🏷️ "):
            lines = lines[1:]
        return "\n".join(line.rstrip() for line in lines).strip() or text.strip()

    @staticmethod
    def is_request_notification(text: str) -> bool:
        return "待审批" in text and ("/hapi a" in text or "/hapi answer" in text)

    def should_skip_duplicate(self, umo: str, session_id: str, text: str) -> bool:
        """Drop short-interval duplicate notifications for the same target/session/body."""
        if self.is_request_notification(text):
            return False

        now = time.monotonic()
        dedupe_window = 2.5
        expire_before = now - 30
        for key, ts in list(self._recent_notifications.items()):
            if ts < expire_before:
                self._recent_notifications.pop(key, None)

        body_key = self.notification_body_key(text)
        cache_key = (umo, session_id or "", body_key)
        last_sent = self._recent_notifications.get(cache_key)
        if last_sent is not None and now - last_sent <= dedupe_window:
            logger.info("跳过重复通知: sid=%s umo=%s", (session_id or "global")[:8], umo[:20])
            return True

        self._recent_notifications[cache_key] = now
        return False

    @staticmethod
    def split_message(text: str, max_len: int = 4200) -> list[str]:
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

    async def push_notification(self, text: str, session_id: str, sessions_cache: list[dict]):
        """推送通知到单个目标窗口，优先走 session 当前路由。"""
        targets = self.state_mgr.select_notification_targets(session_id, sessions_cache)

        if targets:
            for umo in targets:
                if self.should_skip_duplicate(umo, session_id, text):
                    continue
                chunks = self.split_message(text) if len(text) > 4200 else [text]

                for chunk in chunks:
                    try:
                        chain = MessageChain().message(chunk)
                        await self.context.send_message(umo, chain)
                    except Exception:
                        cached_event = self._event_cache.get(umo)
                        if cached_event:
                            try:
                                await cached_event.send(chain)
                            except Exception as e:
                                logger.warning("推送到窗口失败 (umo=%s): %s", umo[:20], e)
                                break
                        else:
                            break
                        break
            return

        if session_id:
            sess = next((s for s in sessions_cache if s["id"] == session_id), None)
            flavor = sess.get("metadata", {}).get("flavor", "unknown") if sess else "unknown"
            logger.error("Session %s [%s] 无绑定窗口且无默认窗口，推送失败", session_id[:8], flavor)
        else:
            logger.error("全局通知无可用默认窗口，推送失败")
