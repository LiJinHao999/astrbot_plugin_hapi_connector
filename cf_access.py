"""
Cloudflare Zero Trust Access 认证管理
- Service Token 方式获取 CF_Authorization cookie
- cookie 缓存与过期检测（解析 JWT exp）
- 通过回调持久化 cookie（KV 存储）
- 仅在配置了 cf_client_id 和 cf_client_secret 时生效
"""

import json
import time
import base64
import asyncio
from typing import Callable, Awaitable, Optional

import aiohttp
from astrbot.api import logger


def _decode_jwt_exp(token: str) -> Optional[float]:
    """从 JWT 中提取 exp（过期时间戳），不验签"""
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * (4 - len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("exp")
    except Exception:
        return None


class CfAccessManager:
    """Cloudflare Zero Trust Access 认证管理器

    通过 Service Token（CF-Access-Client-Id / CF-Access-Client-Secret）
    获取 CF_Authorization cookie，后续请求携带此 cookie 通过 CF Access。
    """

    # cookie 过期前提前刷新的秒数
    REFRESH_MARGIN = 60

    def __init__(
        self,
        endpoint: str,
        client_id: str,
        client_secret: str,
        *,
        cookie_save: Optional[Callable[[dict], Awaitable[None]]] = None,
        cookie_load: Optional[Callable[[], Awaitable[Optional[dict]]]] = None,
    ):
        self._endpoint = endpoint.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._cookie_save = cookie_save
        self._cookie_load = cookie_load

        self._cookie_value: Optional[str] = None
        self._cookie_expires: float = 0
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def _is_valid(self) -> bool:
        """当前 cookie 是否仍然有效"""
        if not self._cookie_value:
            return False
        return time.time() < (self._cookie_expires - self.REFRESH_MARGIN)

    async def load_cached_cookie(self):
        """从持久化存储加载 cookie（插件初始化时调用）"""
        if not self._cookie_load:
            return
        try:
            data = await self._cookie_load()
            if data and isinstance(data, dict):
                value = data.get("value", "")
                expires = data.get("expires", 0)
                if value and time.time() < (expires - self.REFRESH_MARGIN):
                    self._cookie_value = value
                    self._cookie_expires = expires
                    logger.info("从缓存恢复 CF Access cookie，剩余 %ds",
                                int(expires - time.time()))
        except Exception as e:
            logger.warning("加载 CF Access cookie 缓存失败: %s", e)

    async def ensure_auth(self, session: aiohttp.ClientSession):
        """确保 session 携带有效的 CF_Authorization cookie

        如果 cookie 不存在或已过期，自动用 Service Token 重新获取。
        """
        if not self.enabled:
            return

        async with self._lock:
            if self._is_valid():
                self._inject_cookie(session)
                return
            await self._fetch_cookie(session)
            self._inject_cookie(session)

    async def _fetch_cookie(self, session: aiohttp.ClientSession):
        """发送带 CF 凭证的请求获取 CF_Authorization cookie"""
        headers = {
            "CF-Access-Client-Id": self._client_id,
            "CF-Access-Client-Secret": self._client_secret,
        }
        url = f"{self._endpoint}/health"

        logger.info("正在获取 CF Access cookie ...")
        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                # 从响应 cookie 中提取 CF_Authorization
                cf_cookie = resp.cookies.get("CF_Authorization")
                if cf_cookie:
                    self._cookie_value = cf_cookie.value
                    exp = _decode_jwt_exp(self._cookie_value)
                    self._cookie_expires = exp or (time.time() + 86400)
                    logger.info("CF Access cookie 获取成功，过期时间戳: %s",
                                int(self._cookie_expires))
                    await self._save_cookie()
                else:
                    logger.warning(
                        "CF Access 请求返回 %d 但未获取到 cookie", resp.status)
        except Exception as e:
            logger.error("获取 CF Access cookie 失败: %s", e)
            raise

    def _inject_cookie(self, session: aiohttp.ClientSession):
        """将 cookie 注入到 session 的 cookie jar 中"""
        if self._cookie_value:
            session.cookie_jar.update_cookies(
                {"CF_Authorization": self._cookie_value}
            )

    async def _save_cookie(self):
        """持久化 cookie 到 KV 存储"""
        if not self._cookie_save or not self._cookie_value:
            return
        try:
            await self._cookie_save({
                "value": self._cookie_value,
                "expires": self._cookie_expires,
            })
        except Exception as e:
            logger.warning("持久化 CF Access cookie 失败: %s", e)

    def get_cookie_header(self) -> dict:
        """返回 cookie 请求头（供临时 session 使用，如 TokenManager._do_auth）"""
        if self._cookie_value and self._is_valid():
            return {"Cookie": f"CF_Authorization={self._cookie_value}"}
        return {}
