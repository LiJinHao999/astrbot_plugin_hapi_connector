"""文件操作：查询文件列表、下载文件、解码写临时文件"""

import base64
import os
import tempfile

from .hapi_client import AsyncHapiClient
from . import session_ops

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


async def get_file_size(client: AsyncHapiClient, sid: str,
                        path: str) -> int:
    """查询远端文件大小，失败返回 0（不阻塞后续流程）"""
    try:
        parent = os.path.dirname(path) or "."
        entries = await session_ops.list_directory(client, sid, path=parent)
        fname = os.path.basename(path)
        for entry in entries:
            if entry.get("name") == fname:
                return entry.get("size", 0)
    except Exception:
        pass
    return 0


async def download_to_tmp(client: AsyncHapiClient, sid: str,
                          path: str) -> tuple[str, str, bool]:
    """下载远端文件并写入临时文件。

    Returns:
        (tmp_path, filename, is_image)

    Raises:
        Exception: 读取或解码失败时抛出，消息可直接展示给用户
    """
    ok, content = await session_ops.read_file(client, sid, path)
    if not ok:
        raise Exception(content)

    raw = base64.b64decode(content)
    ext = os.path.splitext(path)[1] or ""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.write(raw)
    tmp.close()

    filename = os.path.basename(path)
    is_image = ext.lower() in IMAGE_EXTS
    return tmp.name, filename, is_image
