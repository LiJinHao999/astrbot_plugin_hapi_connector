"""Helpers for file extraction, upload, download, and cleanup."""

import base64
import mimetypes
import os
import tempfile
import uuid
from typing import Any

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


def extract_files_from_message(event: Any) -> list[str]:
    """Extract local attachment paths from AstrBot message components."""
    message_obj = getattr(event, "message_obj", None)
    components = getattr(message_obj, "message", None)
    if not components:
        return []

    files: list[str] = []
    seen: set[str] = set()

    for component in components:
        if _component_type_name(component) not in ATTACHMENT_COMPONENT_TYPES:
            continue

        for attr in LOCAL_PATH_ATTRS:
            path = _normalize_local_path(_get_component_value(component, attr))
            if not path or path in seen:
                continue
            seen.add(path)
            files.append(path)
            break

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


async def upload_file(client: AsyncHapiClient, sid: str, local_path: str) -> tuple[bool, str, dict | None]:
    """Upload a local file to HAPI and return AttachmentMetadata-like dict."""
    path = _normalize_local_path(local_path)
    if not path:
        return False, f"File not found: {local_path}", None

    filename = os.path.basename(path)
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception as exc:
        return False, f"Failed to read {filename}: {exc}", None

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
