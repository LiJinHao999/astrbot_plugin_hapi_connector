"""Helpers for file extraction, upload, download, and cleanup."""

import base64
import hashlib
import mimetypes
import os
import re
import struct
import tempfile
import uuid
from typing import Any
from urllib.parse import unquote, urlparse

import aiohttp

from . import session_ops
from .hapi_client import AsyncHapiClient

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ATTACHMENT_COMPONENT_TYPES = {"file", "image"}
# AstrBot 二次落盘常见名：download.png / 123-download.jpg
_DOWNLOAD_CACHE_NAME_RE = re.compile(
    r"(?:^|[-_])download\.(?:png|jpe?g|gif|webp)$", re.IGNORECASE
)
LOCAL_PATH_ATTRS = (
    "file",
    "file_",
    "path",
    "local_path",
    "localPath",
    "temp_file",
    "temp_path",
    "cache_file",
    "cache_path",
)
REMOTE_URL_ATTRS = (
    "url",
    "uri",
    "download_url",
    "downloadUrl",
    "file",
    "file_",
)
FILENAME_ATTRS = ("name", "filename", "fileName", "title")
MIMETYPE_ATTRS = ("mimeType", "mime_type", "contentType", "content_type")


def _get_component_value(component: Any, key: str) -> Any:
    if isinstance(component, dict):
        return component.get(key)
    try:
        return getattr(component, key)
    except Exception:
        return None


def _component_type_name(component: Any) -> str:
    if isinstance(component, dict):
        value = component.get("type")
        return str(value).lower() if value is not None else ""
    return component.__class__.__name__.lower()


def _normalize_local_path(raw: Any) -> str | None:
    if raw is None:
        return None

    if not isinstance(raw, (str, os.PathLike)):
        return None

    path = os.fspath(raw).strip()
    if not path:
        return None

    lower = path.lower()
    if lower.startswith(("http://", "https://", "base64://", "data:")):
        return None

    if not os.path.exists(path):
        return None

    return path


def _normalize_remote_url(raw: Any) -> str | None:
    if raw is None or not isinstance(raw, str):
        return None

    url = raw.strip()
    if not url:
        return None

    lower = url.lower()
    if lower.startswith(("http://", "https://")):
        return url

    return None


def _first_component_value(component: Any, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _get_component_value(component, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _components_from_get_messages(event: Any) -> list[Any]:
    getter = getattr(event, "get_messages", None)
    if not callable(getter):
        return []
    try:
        result = getter()
    except Exception:
        return []
    return list(result) if result else []


def _components_from_message_obj(event: Any) -> list[Any]:
    message_obj = getattr(event, "message_obj", None)
    message_components = getattr(message_obj, "message", None)
    return list(message_components) if message_components else []


def _components_from_raw_message(event: Any) -> list[Any]:
    message_obj = getattr(event, "message_obj", None)
    raw_message = getattr(message_obj, "raw_message", None)
    if not isinstance(raw_message, dict):
        return []

    raw_components = raw_message.get("message")
    if not isinstance(raw_components, list):
        return []

    components: list[Any] = []
    for item in raw_components:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        components.append({"type": item_type, **data})
    return components


def _get_message_components(event: Any) -> list[Any]:
    """合并 AstrBot 同一消息的多路组件表示。

    常见三路（本地缓存 / 解析后组件 / 平台原始包）会指向同一附件的不同
    path 或 url。此处仍合并全部来源以保证不漏附件；真正的去重在
    extract（path/url）与 upload_event_files（内容哈希）两层完成。
    """
    components: list[Any] = []
    for loader in (
        _components_from_get_messages,
        _components_from_message_obj,
        _components_from_raw_message,
    ):
        components.extend(loader(event))
    return components


def _build_upload_source(component: Any) -> dict[str, Any] | None:
    component_type = _component_type_name(component)
    if component_type not in ATTACHMENT_COMPONENT_TYPES:
        return None

    name = _first_component_value(component, FILENAME_ATTRS)
    mime_type = _first_component_value(component, MIMETYPE_ATTRS)

    for attr in LOCAL_PATH_ATTRS:
        path = _normalize_local_path(_get_component_value(component, attr))
        if path:
            return {
                "kind": "path",
                "path": path,
                "name": name,
                "mimeType": mime_type,
                "componentType": component_type,
            }

    for attr in REMOTE_URL_ATTRS:
        url = _normalize_remote_url(_get_component_value(component, attr))
        if url:
            return {
                "kind": "url",
                "url": url,
                "name": name,
                "mimeType": mime_type,
                "componentType": component_type,
            }

    return None


def _source_key(source: dict[str, Any]) -> str:
    """路径尽量 realpath，降低同一文件不同相对/符号路径的重复。"""
    if source.get("kind") == "path":
        path = source.get("path", "") or ""
        try:
            path = os.path.realpath(path)
        except OSError:
            pass
        return f"path:{path}"
    return f"url:{source.get('url', '')}"


def _normalize_upload_source(source: Any) -> dict[str, Any] | None:
    if isinstance(source, dict):
        kind = source.get("kind")
        if kind == "path":
            path = _normalize_local_path(source.get("path"))
            if path:
                normalized = dict(source)
                normalized["path"] = path
                return normalized
        if kind == "url":
            url = _normalize_remote_url(source.get("url"))
            if url:
                normalized = dict(source)
                normalized["url"] = url
                return normalized

    if isinstance(source, (str, os.PathLike)):
        path = _normalize_local_path(source)
        if path:
            return {"kind": "path", "path": path}

        url = _normalize_remote_url(os.fspath(source))
        if url:
            return {"kind": "url", "url": url}

    return None


def _filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    filename = unquote(os.path.basename(parsed.path or ""))
    return filename or "upload"


def _finalize_filename(filename: str, mime_type: str, component_type: str) -> str:
    ext = os.path.splitext(filename)[1]
    if ext:
        return filename

    guessed_ext = mimetypes.guess_extension(mime_type) if mime_type else None
    if not guessed_ext and component_type == "image":
        guessed_ext = ".png"
    if guessed_ext:
        return f"{filename}{guessed_ext}"
    return filename


async def _read_upload_source(source: dict[str, Any]) -> tuple[bytes, str, str]:
    kind = source["kind"]
    component_type = str(source.get("componentType") or "")

    if kind == "path":
        path = source["path"]
        filename = source.get("name") or os.path.basename(path)
        mime_type = source.get("mimeType") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            raw = f.read()
        return raw, filename, mime_type

    url = source["url"]
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            raw = await resp.read()
            header_mime = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip()

    filename = source.get("name") or _filename_from_url(url)
    mime_type = source.get("mimeType") or header_mime or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    filename = _finalize_filename(filename, mime_type, component_type)
    return raw, filename, mime_type


def _source_display_name(source: Any) -> str:
    if isinstance(source, dict):
        return (
            source.get("name")
            or source.get("path")
            or source.get("url")
            or "attachment"
        )
    return str(source) if source else "attachment"


def _path_byte_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _is_local_image_source(source: dict[str, Any]) -> bool:
    if source.get("kind") != "path":
        return False
    if str(source.get("componentType") or "").lower() == "image":
        return True
    path = str(source.get("path") or "")
    name = str(source.get("name") or path)
    ext = os.path.splitext(name)[1].lower() or os.path.splitext(path)[1].lower()
    return ext in IMAGE_EXTS


def _is_download_cache_name(path_or_name: str) -> bool:
    """AstrBot 二次缓存文件名，如 download.png / 1785-download.jpg。"""
    base = os.path.basename(path_or_name or "").strip()
    if not base:
        return False
    return bool(_DOWNLOAD_CACHE_NAME_RE.search(base))


def _image_pixel_size(path: str) -> tuple[int, int] | None:
    """读本地 PNG/JPEG 宽高（无 PIL 依赖），失败返回 None。"""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
            if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
                w, h = struct.unpack(">II", head[16:24])
                if w > 0 and h > 0:
                    return int(w), int(h)
            if not head.startswith(b"\xff\xd8"):
                return None
            f.seek(2)
            while True:
                b = f.read(1)
                if not b:
                    return None
                while b == b"\xff":
                    b = f.read(1)
                    if not b:
                        return None
                marker = b[0]
                if marker in (0xD8, 0xD9) or marker == 0x01 or 0xD0 <= marker <= 0xD7:
                    continue
                bl_bytes = f.read(2)
                if len(bl_bytes) < 2:
                    return None
                bl = struct.unpack(">H", bl_bytes)[0]
                if bl < 2:
                    return None
                if marker in (
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                ):
                    data = f.read(5)
                    if len(data) < 5:
                        return None
                    h, w = struct.unpack(">HH", data[1:5])
                    if w > 0 and h > 0:
                        return int(w), int(h)
                    return None
                f.seek(bl - 2, os.SEEK_CUR)
    except OSError:
        return None
    return None


def _dedupe_local_images_by_pixels(
    images: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """同一消息内像素尺寸完全相同的本地图只留体积最大的一份。

    media_image.jpg 与 download.png 编码不同、哈希不同，但宽高一致；
    真·多图一般分辨率不同，误伤概率低。
    """
    if len(images) < 2:
        return images
    best_by_wh: dict[tuple[int, int], dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    order: list[tuple[int, int] | None] = []
    for source in images:
        path = str(source.get("path") or "")
        wh = _image_pixel_size(path) if path else None
        if wh is None:
            passthrough.append(source)
            order.append(None)
            continue
        prev = best_by_wh.get(wh)
        if prev is None or _path_byte_size(path) > _path_byte_size(
            str(prev.get("path") or "")
        ):
            best_by_wh[wh] = source
        order.append(wh)
    # 保持首次出现顺序
    out: list[dict[str, Any]] = []
    seen_wh: set[tuple[int, int]] = set()
    pi = 0
    for marker in order:
        if marker is None:
            out.append(passthrough[pi])
            pi += 1
            continue
        if marker in seen_wh:
            continue
        seen_wh.add(marker)
        out.append(best_by_wh[marker])
    return out


def _dedupe_astrbot_image_dual_cache(
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """同条消息里 AstrBot 常把一张图落成两份本地缓存（如 media_image_*.jpg
    与 download.png）：路径不同、编码也不同，内容哈希去不掉。

    策略（由严到宽）：
    1. 有任一主图（非 download 名）时：**一律丢掉全部 download.*** 副缓存
       （不再按体积二选一——避免两份都混进上传列表的边界情况）。
    2. 仅有多份 download.*：留体积最大的一份。
    3. 剩余本地图再按 **像素宽高** 合并：同尺寸只留更大文件
       （双缓存最后兜底；真多图分辨率通常不同）。
    4. URL 文件名是 download.* 且已有本地图：丢掉，避免 path+url 双传。
    """
    if len(sources) < 2:
        return sources

    path_images: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    for source in sources:
        if _is_local_image_source(source):
            path_images.append(source)
        else:
            others.append(source)

    downloads: list[dict[str, Any]] = []
    primaries: list[dict[str, Any]] = []
    for source in path_images:
        label = str(source.get("name") or source.get("path") or "")
        path = str(source.get("path") or "")
        if _is_download_cache_name(label) or _is_download_cache_name(path):
            downloads.append(source)
        else:
            primaries.append(source)

    def _by_size(s: dict[str, Any]) -> int:
        return _path_byte_size(str(s.get("path") or ""))

    # URL 侧文件名若是 download.*：已有任一本地 path 图时丢掉
    other_kept: list[dict[str, Any]] = []
    for source in others:
        label = str(source.get("name") or source.get("url") or "")
        if (
            source.get("kind") == "url"
            and _is_download_cache_name(label)
            and (primaries or downloads or path_images)
        ):
            continue
        other_kept.append(source)

    kept_images: list[dict[str, Any]]
    if primaries and downloads:
        # 有主图就丢光 download 副缓存（1 张或多张主图都如此）
        kept_images = list(primaries)
    elif primaries:
        kept_images = list(primaries)
    elif len(downloads) >= 2:
        kept_images = [max(downloads, key=_by_size)]
    elif downloads:
        kept_images = list(downloads)
    else:
        # 无 download 命名、但仍可能是同图双 path（名都不叫 download）
        kept_images = list(path_images)
        if not other_kept and kept_images == path_images and len(path_images) < 2:
            return sources

    kept_images = _dedupe_local_images_by_pixels(kept_images)
    return other_kept + kept_images


def _image_pixel_size_from_bytes(raw: bytes) -> tuple[int, int] | None:
    """从已读字节解析 PNG/JPEG 宽高。"""
    if not raw or len(raw) < 24:
        return None
    try:
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            w, h = struct.unpack(">II", raw[16:24])
            if w > 0 and h > 0:
                return int(w), int(h)
            return None
        if not raw.startswith(b"\xff\xd8"):
            return None
        i = 2
        n = len(raw)
        while i < n:
            while i < n and raw[i] == 0xFF:
                i += 1
            if i >= n:
                return None
            marker = raw[i]
            i += 1
            if marker in (0xD8, 0xD9) or marker == 0x01 or 0xD0 <= marker <= 0xD7:
                continue
            if i + 2 > n:
                return None
            bl = struct.unpack(">H", raw[i : i + 2])[0]
            i += 2
            if bl < 2 or i + bl - 2 > n:
                return None
            if marker in (
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            ):
                if i + 5 > n:
                    return None
                h, w = struct.unpack(">HH", raw[i + 1 : i + 5])
                if w > 0 and h > 0:
                    return int(w), int(h)
                return None
            i += bl - 2
    except Exception:
        return None
    return None


async def upload_event_files(client: AsyncHapiClient, event: Any,
                             sid: str) -> tuple[list[dict], str]:
    """从消息中提取附件并全部上传到 session。

    返回 (attachments, 上传过程说明)。无附件时返回 ([], "")。
    供快捷前缀 / Focus 转发链路使用：图片、文件经 /upload 接口
    转为 attachments 随消息发出。指令类路径（send/to）不自动捎带附件。

    去重层次：
    1. extract：丢 download 副缓存 + 同像素尺寸留大图
    2. 上传前：内容 SHA-256
    3. 上传前：图片像素宽高（编码不同的双缓存最终兜底，同尺寸留更大）
    """
    files = extract_files_from_message(event)
    if not files:
        return [], ""

    # 先读全部，便于按像素尺寸在「上传前」做二次合并（路径阶段可能漏判）
    loaded: list[tuple[dict[str, Any], bytes, str, str]] = []
    msgs: list[str] = []
    for source in files:
        normalized = _normalize_upload_source(source)
        if not normalized:
            msgs.append(f"不支持的上传来源: {_source_display_name(source)}")
            continue
        try:
            raw, filename, mime_type = await _read_upload_source(normalized)
        except Exception as exc:
            msgs.append(f"读取 {_source_display_name(normalized)} 失败: {exc}")
            continue
        loaded.append((normalized, raw, filename, mime_type))

    # SHA-256 去重
    unique: list[tuple[dict[str, Any], bytes, str, str]] = []
    seen_hash: set[str] = set()
    for item in loaded:
        h = hashlib.sha256(item[1]).hexdigest()
        if h in seen_hash:
            continue
        seen_hash.add(h)
        unique.append(item)

    # 像素尺寸去重：同 wh 只留更大文件（media_image vs download 编码不同）
    best_by_wh: dict[tuple[int, int], tuple[dict[str, Any], bytes, str, str]] = {}
    non_image: list[tuple[dict[str, Any], bytes, str, str]] = []
    wh_order: list[tuple[int, int] | None] = []
    for item in unique:
        wh = _image_pixel_size_from_bytes(item[1])
        if wh is None:
            non_image.append(item)
            wh_order.append(None)
            continue
        prev = best_by_wh.get(wh)
        if prev is None or len(item[1]) > len(prev[1]):
            best_by_wh[wh] = item
        wh_order.append(wh)
    final_items: list[tuple[dict[str, Any], bytes, str, str]] = []
    seen_wh: set[tuple[int, int]] = set()
    ni = 0
    for marker in wh_order:
        if marker is None:
            final_items.append(non_image[ni])
            ni += 1
            continue
        if marker in seen_wh:
            continue
        seen_wh.add(marker)
        final_items.append(best_by_wh[marker])

    attachments: list[dict] = []
    for normalized, raw, filename, mime_type in final_items:
        ok, msg, attach = await upload_file(
            client, sid, {**normalized, "_preloaded": (raw, filename, mime_type)}
        )
        msgs.append(msg)
        if ok and attach:
            attachments.append(attach)
    notice = "正在上传文件...\n" + "\n".join(msgs) if msgs else ""
    return attachments, notice


def extract_files_from_message(event: Any) -> list[dict[str, Any]]:
    """Extract uploadable attachment sources from AstrBot message components."""
    files: list[dict[str, Any]] = []
    seen: set[str] = set()

    for component in _get_message_components(event):
        source = _build_upload_source(component)
        if not source:
            continue

        key = _source_key(source)
        if key in seen:
            continue

        seen.add(key)
        files.append(source)

    return _dedupe_astrbot_image_dual_cache(files)


async def get_file_size(client: AsyncHapiClient, sid: str, path: str) -> int:
    """Query remote file size. Return 0 on failure."""
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


async def download_to_tmp(client: AsyncHapiClient, sid: str, path: str) -> tuple[str, str, bool]:
    """Download a remote file into a local temporary file."""
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


async def upload_file(client: AsyncHapiClient, sid: str, source: Any) -> tuple[bool, str, dict | None]:
    """Upload a local path or remote URL attachment to HAPI."""
    preloaded = None
    if isinstance(source, dict):
        preloaded = source.get("_preloaded")
        # 不把内部字段传给 normalize
        source = {k: v for k, v in source.items() if k != "_preloaded"}

    if preloaded is not None:
        raw, filename, mime_type = preloaded
    else:
        normalized = _normalize_upload_source(source)
        if not normalized:
            return False, f"不支持的上传来源: {source}", None
        try:
            raw, filename, mime_type = await _read_upload_source(normalized)
        except Exception as exc:
            display_name = (
                normalized.get("name")
                or normalized.get("path")
                or normalized.get("url")
                or "attachment"
            )
            return False, f"读取 {display_name} 失败: {exc}", None

    payload = {
        "filename": filename,
        "content": base64.b64encode(raw).decode("ascii"),
        "mimeType": mime_type,
    }

    resp = await client.post(f"/api/sessions/{sid}/upload", json=payload)
    try:
        if not resp.ok:
            body = await resp.text()
            return False, f"上传 {filename} 失败: {resp.status} {body[:200]}", None

        data = await resp.json()
        if not data.get("success") or not data.get("path"):
            error = data.get("error") or data.get("message") or "未知错误"
            return False, f"上传 {filename} 失败: {error}", None

        attachment = {
            "id": str(uuid.uuid4()),
            "filename": filename,
            "mimeType": mime_type,
            "size": len(raw),
            "path": data["path"],
        }
        return True, f"已上传: {filename}", attachment
    finally:
        resp.release()


async def delete_uploaded_file(client: AsyncHapiClient, sid: str, path: str) -> tuple[bool, str]:
    """Delete a previously uploaded HAPI blob."""
    resp = await client.post(f"/api/sessions/{sid}/upload/delete", json={"path": path})
    try:
        if not resp.ok:
            body = await resp.text()
            return False, f"删除失败: {resp.status} {body[:200]}"

        data = await resp.json()
        if data.get("success") or data.get("ok"):
            return True, f"已删除: {path}"

        error = data.get("error") or data.get("message") or "未知错误"
        return False, f"删除失败: {error}"
    finally:
        resp.release()
