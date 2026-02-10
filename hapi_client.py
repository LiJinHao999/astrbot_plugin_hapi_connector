"""
HAPI 异步 HTTP 客户端
- JWT 自动获取 / 缓存 / 刷新 (asyncio.Lock)
- 401 自动重试
- SOCKS5/HTTP 代理支持 (aiohttp-socks)
"""

import time
import asyncio

import aiohttp
from astrbot.api import logger


def _build_connector(proxy_url: str | None):
    """根据 proxy_url 构造 aiohttp connector"""
    if proxy_url:
        try:
            from aiohttp_socks import ProxyConnector
            return ProxyConnector.from_url(proxy_url)
        except ImportError:
            logger.warning("aiohttp-socks 未安装，忽略代理配置")
    return aiohttp.TCPConnector()


class AsyncTokenManager:
    """异步 JWT 令牌管理：获取、缓存、主动刷新"""

    def __init__(self, endpoint: str, access_token: str, proxy_url: str | None,
                 jwt_lifetime: int = 900, refresh_before: int = 180):
        self._endpoint = endpoint
        self._access_token = access_token
        self._proxy_url = proxy_url
        self._jwt_lifetime = jwt_lifetime
        self._refresh_before = refresh_before

        self._jwt: str | None = None
        self._obtained_at: float = 0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """获取有效的 JWT，必要时自动刷新"""
        async with self._lock:
            if self._should_refresh():
                await self._do_auth()
            return self._jwt

    async def force_refresh(self) -> str:
        """强制重新获取 JWT（用于 401 兜底）"""
        async with self._lock:
            self._do_auth_needed = True
            await self._do_auth()
            return self._jwt

    def _should_refresh(self) -> bool:
        if self._jwt is None:
            return True
        elapsed = time.time() - self._obtained_at
        return elapsed >= (self._jwt_lifetime - self._refresh_before)

    async def _do_auth(self):
        """调用 POST /api/auth 换取 JWT"""
        url = f"{self._endpoint}/api/auth"
        payload = {"accessToken": self._access_token}

        logger.info("正在获取 JWT ...")
        connector = _build_connector(self._proxy_url)
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    self._jwt = data["token"]
                    self._obtained_at = time.time()
                    logger.info("JWT 获取成功，有效期 %ds", self._jwt_lifetime)
        except Exception:
            connector.close()
            raise


class AsyncHapiClient:
    """异步 HAPI HTTP 客户端，封装鉴权、重试"""

    def __init__(self, endpoint: str, access_token: str, proxy_url: str | None = None,
                 jwt_lifetime: int = 900, refresh_before: int = 180):
        self._endpoint = endpoint.rstrip("/")
        self._proxy_url = proxy_url

        self._token_mgr = AsyncTokenManager(
            endpoint=self._endpoint,
            access_token=access_token,
            proxy_url=proxy_url,
            jwt_lifetime=jwt_lifetime,
            refresh_before=refresh_before,
        )
        self._session: aiohttp.ClientSession | None = None

    async def init(self):
        """初始化 aiohttp.ClientSession"""
        if self._session is None or self._session.closed:
            connector = _build_connector(self._proxy_url)
            self._session = aiohttp.ClientSession(connector=connector)

    async def close(self):
        """关闭 aiohttp.ClientSession"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            await self.init()

    async def _auth_headers(self) -> dict:
        token = await self._token_mgr.get_token()
        return {"Authorization": f"Bearer {token}"}

    async def request(self, method: str, path: str, *,
                      retry_on_401: bool = True, **kwargs) -> aiohttp.ClientResponse:
        """
        发送请求到 HAPI，自动附加 JWT。
        遇到 401 时自动刷新令牌并重试一次。
        返回 aiohttp.ClientResponse（已读取 body）。
        """
        await self._ensure_session()
        url = f"{self._endpoint}{path}"
        headers = kwargs.pop("headers", {})
        headers.update(await self._auth_headers())

        resp = await self._session.request(
            method, url, headers=headers,
            timeout=aiohttp.ClientTimeout(total=15), **kwargs
        )

        if resp.status == 401 and retry_on_401:
            logger.warning("收到 401，尝试刷新 JWT 后重试 ...")
            await self._token_mgr.force_refresh()
            headers.update(await self._auth_headers())
            resp = await self._session.request(
                method, url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15), **kwargs
            )

        return resp

    async def get(self, path: str, **kwargs) -> aiohttp.ClientResponse:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs) -> aiohttp.ClientResponse:
        return await self.request("POST", path, **kwargs)

    async def patch(self, path: str, **kwargs) -> aiohttp.ClientResponse:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs) -> aiohttp.ClientResponse:
        return await self.request("DELETE", path, **kwargs)

    async def get_json(self, path: str, **kwargs) -> dict:
        """GET 并返回 JSON"""
        resp = await self.get(path, **kwargs)
        return await resp.json()

    async def post_json(self, path: str, **kwargs) -> dict:
        """POST 并返回 JSON"""
        resp = await self.post(path, **kwargs)
        return await resp.json()

    async def health(self) -> bool:
        """GET /health，不需要 JWT"""
        try:
            await self._ensure_session()
            async with self._session.get(
                f"{self._endpoint}/health",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return resp.ok
        except Exception:
            return False

    async def subscribe_events_raw(self, *, session_id: str = None,
                                   machine_id: str = None, all_events: bool = True):
        """
        订阅 GET /api/events（SSE 长连接）。
        返回 aiohttp 流式 response 供外部逐行解析。
        """
        await self._ensure_session()
        params = {}
        if all_events:
            params["all"] = "1"
        if session_id:
            params["sessionId"] = session_id
        if machine_id:
            params["machineId"] = machine_id

        # SSE 用 query token 方式鉴权
        params["token"] = await self._token_mgr.get_token()

        url = f"{self._endpoint}/api/events"
        resp = await self._session.get(url, params=params, timeout=None)
        resp.raise_for_status()
        return resp
