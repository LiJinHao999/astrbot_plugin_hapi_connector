"""从仓库 docs/ 读取 Markdown，供 WebUI 帮助页展示。

标题取文件首个 H1，图片改写为 data URI，便于 iframe 内直接渲染，无需外链 CDN。
"""

from __future__ import annotations

import base64
import mimetypes
import re
from functools import lru_cache
from pathlib import Path

try:
    from astrbot.api import logger
except Exception:  # 本地无 AstrBot 时降级
    import logging

    logger = logging.getLogger("hapi_connector.docs")

# webui/ → 插件根目录
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = _PLUGIN_ROOT / "docs"

# 仅暴露白名单文档；顺序即帮助页 tab 顺序（install 优先）
DOC_CATALOG: tuple[dict[str, str], ...] = (
    {"id": "install", "file": "install.md"},
    {"id": "usage-guide", "file": "usage-guide.md"},
    {"id": "session-isolation", "file": "session-isolation.md"},
    {"id": "cf-access", "file": "cf_access_guide.md"},
)

DEFAULT_DOC_ID = "install"

_H1_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
# ![alt](pics/xx.png) 或 ![alt](./pics/xx.png)
_MD_IMG_RE = re.compile(
    r"!\[([^\]]*)\]\((?:\./)?(pics/[^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)"
)
# HTML <img ... src="pics/.."> / src='pics/..'
_HTML_IMG_RE = re.compile(
    r"""(?P<prefix><img\b[^>]*?\bsrc\s*=\s*["'])(?P<src>(?:\./)?pics/[^"']+)(?P<suffix>["'])""",
    re.IGNORECASE,
)
# 文档互链：(cf_access_guide.md) / (./session-isolation.md#x)
_DOC_LINK_RE = re.compile(
    r"\[([^\]]+)\]\((?:\./)?("
    + "|".join(re.escape(d["file"]) for d in DOC_CATALOG)
    + r")(#[^)\s]*)?\)"
)

_FILE_TO_ID = {d["file"]: d["id"] for d in DOC_CATALOG}


def _safe_under_docs(rel: str) -> Path | None:
    """解析 docs 下相对路径，拒绝逃逸。"""
    rel = (rel or "").strip().lstrip("./")
    if not rel or ".." in Path(rel).parts:
        return None
    target = (DOCS_DIR / rel).resolve()
    try:
        target.relative_to(DOCS_DIR.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


def _file_data_uri(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError as e:
        logger.warning("docs asset read failed %s: %s", path, e)
        return None
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "application/octet-stream"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _rewrite_images(md: str) -> str:
    def md_img(m: re.Match) -> str:
        alt, rel = m.group(1), m.group(2)
        path = _safe_under_docs(rel)
        if not path:
            return m.group(0)
        uri = _file_data_uri(path)
        if not uri:
            return m.group(0)
        return f"![{alt}]({uri})"

    def html_img(m: re.Match) -> str:
        rel = m.group("src").lstrip("./")
        path = _safe_under_docs(rel)
        if not path:
            return m.group(0)
        uri = _file_data_uri(path)
        if not uri:
            return m.group(0)
        return f'{m.group("prefix")}{uri}{m.group("suffix")}'

    md = _MD_IMG_RE.sub(md_img, md)
    md = _HTML_IMG_RE.sub(html_img, md)
    return md


def _rewrite_doc_links(md: str) -> str:
    """文档互链 → #doc:<id>，前端点击时切换文档。"""

    def repl(m: re.Match) -> str:
        text, filename, frag = m.group(1), m.group(2), m.group(3) or ""
        doc_id = _FILE_TO_ID.get(filename)
        if not doc_id:
            return m.group(0)
        return f"[{text}](#doc:{doc_id}{frag})"

    return _DOC_LINK_RE.sub(repl, md)


def extract_title(md: str, fallback: str = "") -> str:
    m = _H1_RE.search(md or "")
    if m:
        return m.group(1).strip()
    return fallback or "未命名文档"


def strip_leading_h1(md: str) -> str:
    """正文区已用 H1 作卡片标题时，去掉首行 H1 避免重复。"""
    lines = (md or "").splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and re.match(r"^\s*#\s+\S", lines[i]):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        return "\n".join(lines[i:]).lstrip("\n")
    return md or ""


@lru_cache(maxsize=8)
def _load_raw(doc_id: str) -> tuple[str, str] | None:
    """返回 (title, raw_md) 或 None。"""
    meta = next((d for d in DOC_CATALOG if d["id"] == doc_id), None)
    if not meta:
        return None
    path = DOCS_DIR / meta["file"]
    if not path.is_file():
        logger.warning("docs missing: %s", path)
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("docs read failed %s: %s", path, e)
        return None
    title = extract_title(text, fallback=meta["file"])
    return title, text


@lru_cache(maxsize=8)
def _prepared_doc(doc_id: str) -> tuple[str, str, str] | None:
    """返回 (title, file, rewritten_markdown) 或 None。图片 data URI 一并缓存。"""
    meta = next((d for d in DOC_CATALOG if d["id"] == doc_id), None)
    loaded = _load_raw(doc_id)
    if not meta or not loaded:
        return None
    title, raw = loaded
    body = strip_leading_h1(raw)
    body = _rewrite_images(body)
    body = _rewrite_doc_links(body)
    return title, meta["file"], body


def list_docs() -> dict:
    docs = []
    for meta in DOC_CATALOG:
        loaded = _load_raw(meta["id"])
        title = loaded[0] if loaded else meta["file"]
        docs.append(
            {
                "id": meta["id"],
                "file": meta["file"],
                "title": title,
            }
        )
    return {"docs": docs, "default": DEFAULT_DOC_ID}


def get_doc(doc_id: str) -> dict | None:
    prepared = _prepared_doc(doc_id)
    if not prepared:
        return None
    title, file, body = prepared
    return {
        "id": doc_id,
        "title": title,
        "markdown": body,
        "file": file,
    }


def clear_cache() -> None:
    _load_raw.cache_clear()
    _prepared_doc.cache_clear()
