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

    async def push_notification(self, text: str, session_id: str, sessions_cache: list[dict]) -> bool:
        """推送通知到单个目标窗口，优先走 session 当前路由。

        返回是否至少向一个目标成功发出（无目标或全部失败为 False）。
        """
        targets = self.state_mgr.select_notification_targets(session_id, sessions_cache)
        any_ok = False

        if targets:
            for umo in targets:
                if self.should_skip_duplicate(umo, session_id, text):
                    # 去重跳过视为「已送达过」，不算失败
                    any_ok = True
                    continue
                chunks = self.split_message(text) if len(text) > 4200 else [text]
                umo_ok = True
                for chunk in chunks:
                    cached_event = self._event_cache.get(umo)
                    chain = None
                    try:
                        if cached_event:
                            result = cached_event.plain_result(chunk)
                            logger.warning("send (umo=%s)   ", umo[:20])
                            await cached_event.send(result)
                        else:
                            logger.warning("send_message (umo=%s)", umo[:20])
                            chain = MessageChain().message(chunk)
                            await self.context.send_message(umo, chain)
                    except Exception:
                        cached_event = self._event_cache.get(umo)
                        if cached_event:
                            try:
                                if chain is None:
                                    chain = MessageChain().message(chunk)
                                await cached_event.send(chain)
                            except Exception as e:
                                logger.warning("推送到窗口失败 (umo=%s): %s", umo[:20], e)
                                umo_ok = False
                                break
                        else:
                            umo_ok = False
                            break
                if umo_ok:
                    any_ok = True
            return any_ok

        if session_id:
            sess = next((s for s in sessions_cache if s["id"] == session_id), None)
            flavor = sess.get("metadata", {}).get("flavor", "unknown") if sess else "unknown"
            logger.error("Session %s [%s] 无绑定窗口且无默认窗口，推送失败", session_id[:8], flavor)
        else:
            logger.error("全局通知无可用默认窗口，推送失败")
        return False

    async def push_image_notification(
        self,
        image_path: str,
        session_id: str,
        sessions_cache: list[dict],
        *,
        caption: str = "",
        dedupe_key: str = "",
    ) -> bool:
        """推送本地图片（结构卡/对话卡）到目标窗口；可选附带 caption 文本。

        返回是否至少成功发出一次。
        """
        import astrbot.api.message_components as Comp

        targets = self.state_mgr.select_notification_targets(session_id, sessions_cache)
        if not targets:
            if session_id:
                sess = next((s for s in sessions_cache if s["id"] == session_id), None)
                flavor = sess.get("metadata", {}).get("flavor", "unknown") if sess else "unknown"
                logger.error(
                    "Session %s [%s] 无绑定窗口且无默认窗口，图片推送失败",
                    session_id[:8],
                    flavor,
                )
            else:
                logger.error("全局通知无可用默认窗口，图片推送失败")
            return False

        body = dedupe_key or f"img:{image_path}:{caption}"
        any_ok = False
        for umo in targets:
            if self.should_skip_duplicate(umo, session_id, body):
                any_ok = True
                continue
            chain = None
            try:
                img = Comp.Image.fromFileSystem(image_path)
                parts = [img]
                if caption:
                    parts.append(Comp.Plain(str(caption)))
                # 兼容多种 MessageChain 构造 / 字段写法
                last_err = None
                for builder in (
                    lambda: MessageChain(parts),
                    lambda: MessageChain(chain=parts),
                    lambda: MessageChain(message_chain=parts),
                ):
                    try:
                        chain = builder()
                        break
                    except TypeError as te:
                        last_err = te
                    except Exception as te:
                        last_err = te
                if chain is None:
                    chain = MessageChain()
                    try:
                        chain.chain = list(parts)
                    except Exception:
                        try:
                            chain.message_chain = list(parts)  # type: ignore[attr-defined]
                        except Exception as e2:
                            logger.warning(
                                "MessageChain 构造失败，仅发 caption: %s / %s", last_err, e2
                            )
                            chain = MessageChain().message(caption or "[hapi card]")
                await self.context.send_message(umo, chain)
                any_ok = True
            except Exception as e:
                cached_event = self._event_cache.get(umo)
                if cached_event and chain is not None:
                    try:
                        await cached_event.send(chain)
                        any_ok = True
                    except Exception as e2:
                        logger.warning("图片推送到窗口失败 (umo=%s): %s / %s", umo[:20], e, e2)
                else:
                    logger.warning("图片推送到窗口失败 (umo=%s): %s", umo[:20], e)
        return any_ok
