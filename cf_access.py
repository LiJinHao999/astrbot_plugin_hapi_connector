"""
Cloudflare Zero Trust Access 认证管理
- Service Token 方式：每次请求携带 CF-Access-Client-Id / CF-Access-Client-Secret 头
- 仅在配置了 client_id 和 client_secret 时生效
"""


class CfAccessManager:
    """Cloudflare Zero Trust Access 认证管理器

    通过 Service Token 请求头通过 CF Access，无需管理 cookie。
    """

    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret

    @property
    def enabled(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def get_headers(self) -> dict:
        """返回 CF Access 认证请求头"""
        if not self.enabled:
            return {}
        return {
            "CF-Access-Client-Id": self._client_id,
            "CF-Access-Client-Secret": self._client_secret,
        }
