"""Helpers for file extraction, upload, download, and cleanup."""

import base64
import mimetypes
import os
import tempfile
import uuid
from typing import Any
from urllib.parse import unquote, urlparse

import aiohttp

from . import session_ops
from .hapi_client import AsyncHapiClient

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ATTACHMENT_COMPONENT_TYPES = {"file", "image"}
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


def _get_message_components(event: Any) -> list[Any]:
    components: list[Any] = []

    getter = getattr(event, "get_messages", None)
    if callable(getter):
        try:
            result = getter()
            if result:
                components.extend(list(result))
        except Exception:
            pass

    message_obj = getattr(event, "message_obj", None)
    message_components = getattr(message_obj, "message", None)
    if message_components:
        components.extend(list(message_components))

    raw_message = getattr(message_obj, "raw_message", None)
    if isinstance(raw_message, dict):
        raw_components = raw_message.get("message")
        if isinstance(raw_components, list):
            for item in raw_components:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                data = item.get("data")
                if not isinstance(data, dict):
                    continue
                components.append({"type": item_type, **data})

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
    if source.get("kind") == "path":
        return f"path:{source.get('path', '')}"
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

    return files


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
    normalized = _normalize_upload_source(source)
    if not normalized:
        return False, f"Unsupported upload source: {source}", None

    try:
        raw, filename, mime_type = await _read_upload_source(normalized)
    except Exception as exc:
        display_name = (
            normalized.get("name")
            or normalized.get("path")
            or normalized.get("url")
            or "attachment"
        )
        return False, f"Failed to read {display_name}: {exc}", None

    payload = {
        "filename": filename,
        "content": base64.b64encode(raw).decode("ascii"),
        "mimeType": mime_type,
    }

    resp = await client.post(f"/api/sessions/{sid}/upload", json=payload)
    try:
        if not resp.ok:
            body = await resp.text()
            return False, f"Upload failed {filename}: {resp.status} {body[:200]}", None

        data = await resp.json()
        if not data.get("success") or not data.get("path"):
            error = data.get("error") or data.get("message") or "unknown error"
            return False, f"Upload failed {filename}: {error}", None

        attachment = {
            "id": str(uuid.uuid4()),
            "filename": filename,
            "mimeType": mime_type,
            "size": len(raw),
            "path": data["path"],
        }
        return True, f"Uploaded: {filename}", attachment
    finally:
        resp.release()


async def delete_uploaded_file(client: AsyncHapiClient, sid: str, path: str) -> tuple[bool, str]:
    """Delete a previously uploaded HAPI blob."""
    resp = await client.post(f"/api/sessions/{sid}/upload/delete", json={"path": path})
    try:
        if not resp.ok:
            body = await resp.text()
            return False, f"Delete failed: {resp.status} {body[:200]}"

        data = await resp.json()
        if data.get("success") or data.get("ok"):
            return True, f"Deleted: {path}"

        error = data.get("error") or data.get("message") or "unknown error"
        return False, f"Delete failed: {error}"
    finally:
        resp.release()
