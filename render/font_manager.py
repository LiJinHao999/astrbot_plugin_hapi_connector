"""可移植 CJK 字体解析（不自动下载、不往插件里塞大字体）。

- WebUI 扫描并列出：插件 `assets/fonts/` + 系统常见 CJK 路径。
- 用户可在下拉框选一项，或填自定义 `card_font_path`。
- 未指定且扫描为空时返回 None，调用方回退纯文本（绝不出方块字）。
"""

from __future__ import annotations

import os
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover
    import logging

    logger = logging.getLogger("hapi_connector.font_manager")

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_BUNDLED_DIR = _PLUGIN_ROOT / "assets" / "fonts"

# 系统兜底：有就用，没有跳过。不是「依赖开发者本机」，而是「用户机器上若系统已装则免费用」。
_SYSTEM_SANS = (
    # Linux（发行版包常见路径）
    "/usr/share/fonts/google-noto-sans-cjk-vf-fonts/NotoSansCJK-VF.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-droid-sans-fonts/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/wqy-zenhei-fonts/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Windows
    "C:\\Windows\\Fonts\\msyh.ttc",
    "C:\\Windows\\Fonts\\msyh.ttf",
    "C:\\Windows\\Fonts\\msyhbd.ttc",
    "C:\\Windows\\Fonts\\simhei.ttf",
    "C:\\Windows\\Fonts\\simsun.ttc",
    "C:\\Windows\\Fonts\\Deng.ttf",
)

_SYSTEM_MONO = (
    "/usr/share/fonts/google-noto-sans-mono-cjk-vf-fonts/NotoSansMonoCJK-VF.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansMonoCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "C:\\Windows\\Fonts\\consola.ttf",
    "/System/Library/Fonts/Menlo.ttc",
)

_SYSTEM_FALLBACKS = (
    # Windows supplementary CJK and symbol fonts.
    "C:\\Windows\\Fonts\\SimsunExtG.ttf",
    "C:\\Windows\\Fonts\\NotoSansSC-VF.ttf",
    "C:\\Windows\\Fonts\\NotoSerifSC-VF.ttf",
    "C:\\Windows\\Fonts\\seguisym.ttf",
    "C:\\Windows\\Fonts\\segoeui.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
    # Common Linux symbol/CJK fallbacks.
    "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansSymbols-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def bundled_dir() -> Path:
    return _BUNDLED_DIR


def plugin_root() -> Path:
    return _PLUGIN_ROOT


def resolve_user_font(path_raw: str | None) -> Path | None:
    if not path_raw:
        return None
    s = str(path_raw).strip()
    if not s:
        return None
    p = Path(s).expanduser()
    if not p.is_absolute():
        p = (_PLUGIN_ROOT / p).resolve()
    if p.is_file() and p.stat().st_size > 1024:
        return p
    return None


def _first_existing(paths: list[Path | str]) -> Path | None:
    for raw in paths:
        p = Path(raw)
        try:
            if p.is_file() and p.stat().st_size > 1024:
                return p
        except OSError:
            continue
    return None


def _scan_dir(directory: Path, prefer_mono: bool) -> Path | None:
    if not directory.is_dir():
        return None
    try:
        names = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return None
    mono_hit = None
    sans_hit = None
    any_hit = None
    for p in names:
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".ttf", ".otf", ".ttc", ".otc"):
            continue
        try:
            if p.stat().st_size <= 1024:
                continue
        except OSError:
            continue
        low = p.name.lower()
        any_hit = any_hit or p
        if "mono" in low or "code" in low:
            mono_hit = mono_hit or p
        if any(
            k in low
            for k in (
                "cjk",
                "sc",
                "cn",
                "noto",
                "sourcehan",
                "wqy",
                "droid",
                "yahei",
                "pingfang",
                "heiti",
                "simhei",
                "simsun",
            )
        ):
            sans_hit = sans_hit or p
    if prefer_mono:
        return mono_hit or sans_hit or any_hit
    return sans_hit or mono_hit or any_hit


def resolve_font_path(
    *,
    mono: bool = False,
    user_path: str | None = None,
    allow_download: bool = False,  # 保留参数兼容旧调用；**忽略**，永不自动下载
) -> Path | None:
    path, _src = resolve_font_path_with_source(
        mono=mono, user_path=user_path, allow_download=allow_download
    )
    return path


def resolve_font_path_with_source(
    *,
    mono: bool = False,
    user_path: str | None = None,
    allow_download: bool = False,
) -> tuple[Path | None, str | None]:
    """返回 (path, source)。source: user | bundled | system | None。"""
    _ = allow_download

    user = resolve_user_font(user_path)
    if user is not None:
        return user, "user"

    hit = _scan_dir(_BUNDLED_DIR, prefer_mono=mono)
    if hit is not None:
        return hit, "bundled"

    if mono:
        m = _first_existing(list(_SYSTEM_MONO))
        if m is not None:
            return m, "system"
        s = _first_existing(list(_SYSTEM_SANS))
        return (s, "system") if s is not None else (None, None)

    s = _first_existing(list(_SYSTEM_SANS))
    return (s, "system") if s is not None else (None, None)


def ensure_default_fonts(*, allow_download: bool = False) -> dict[str, Any]:
    """探测可用字体状态（不下载）。"""
    _ = allow_download
    sans = (
        _scan_dir(_BUNDLED_DIR, prefer_mono=False)
        or _first_existing(list(_SYSTEM_SANS))
    )
    mono = (
        _scan_dir(_BUNDLED_DIR, prefer_mono=True)
        or _first_existing(list(_SYSTEM_MONO))
        or sans
    )
    status: dict[str, Any] = {
        "bundled_dir": str(_BUNDLED_DIR),
        "sans": str(sans) if sans else None,
        "mono": str(mono) if mono else None,
        "downloaded": False,
        "auto_download": False,
        "error": None,
    }
    if sans is None:
        status["error"] = (
            "未找到可用中文字体。"
            f"请将 NotoSansSC-Regular.otf（或其它 CJK 字体）放到 {_BUNDLED_DIR}，"
            "或在配置中设置 card_font_path。"
            "未配置时出卡会回退纯文本，避免方块字。"
        )
    return status


@lru_cache(maxsize=96)
def _load_truetype(path_str: str, size: int, index: int, weight: int):
    """返回 (font, weight_applied)。weight_applied：可变字重轴是否成功。"""
    from PIL import ImageFont  # type: ignore

    font = ImageFont.truetype(path_str, size=size, index=index)
    weight_applied = False
    # 可变字体（如 NotoSansCJK-VF）：显式设字重。
    # 默认轴常是 Thin(100)，不设则正文偏细；bold 用 700 真正加粗。
    target_w = int(weight) if weight else 400
    if hasattr(font, "set_variation_by_axes"):
        try:
            axes = font.get_variation_axes() if hasattr(font, "get_variation_axes") else []
            for ax in axes or []:
                name = ax.get("name") if isinstance(ax, dict) else None
                # name 可能是 bytes
                n = (
                    name.decode("ascii", "ignore")
                    if isinstance(name, (bytes, bytearray))
                    else str(name or "")
                )
                if n.lower() in ("weight", "wght"):
                    lo = float(ax.get("minimum", 100))
                    hi = float(ax.get("maximum", 900))
                    w = max(lo, min(hi, float(target_w)))
                    font.set_variation_by_axes([w])
                    # 仅当请求粗体且成功落到 ≥600 时算「粗体已生效」
                    weight_applied = target_w >= 600
                    break
        except Exception:
            weight_applied = False
    return font, weight_applied


def _bold_sibling_path(path: Path) -> Path | None:
    """同目录找 Bold / Medium 兄弟文件（静态字体无字重轴时用）。"""
    if path is None:
        return None
    name = path.name
    stem = path.stem
    parent = path.parent
    low = name.lower()
    # 已是粗体
    if any(k in low for k in ("bold", "black", "heavy", "semibold", "medium")):
        return path
    candidates = [
        parent / name.replace("Regular", "Bold"),
        parent / name.replace("regular", "bold"),
        parent / name.replace("Regular", "Medium"),
        parent / f"{stem}-Bold{path.suffix}",
        parent / f"{stem}Bold{path.suffix}",
        parent / f"{stem}-bold{path.suffix}",
        parent / f"{stem}_Bold{path.suffix}",
        # Windows 雅黑粗体
        parent / "msyhbd.ttc",
        parent / "msyhbd.ttf",
        parent / "simhei.ttf",
    ]
    seen: set[str] = set()
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        try:
            if c.is_file() and c.stat().st_size > 1024 and c != path:
                return c
        except OSError:
            continue
    return None


def load_image_font(
    size: int,
    *,
    mono: bool = False,
    user_path: str | None = None,
    allow_download: bool = False,
    bold: bool = False,
    weight: int | None = None,
):
    """加载 Pillow ImageFont；失败抛 RuntimeError（含修复提示）。

    bold=True 或 weight>=600 时尽量用粗体：
    1) 可变字体字重轴（wght）
    2) 同目录 Bold 兄弟文件
    3) 仍失败则回退常规字（调用方可 stroke 合成）
    """
    try:
        from PIL import ImageFont  # type: ignore
    except ImportError as e:
        raise RuntimeError("未安装 Pillow") from e

    _ = allow_download
    want_w = int(weight) if weight is not None else (700 if bold else 400)
    want_w = max(100, min(900, want_w))
    want_bold = want_w >= 600

    path = resolve_font_path(
        mono=mono, user_path=user_path, allow_download=False
    )
    if path is None:
        # 等宽找不到时再试非等宽 CJK，至少保证中文
        if mono:
            path = resolve_font_path(
                mono=False, user_path=user_path, allow_download=False
            )
    if path is None:
        st = ensure_default_fonts()
        raise RuntimeError(st.get("error") or "无可用字体")

    load_path = path
    if want_bold:
        sib = _bold_sibling_path(path)
        if sib is not None:
            load_path = sib

    last_err: Exception | None = None
    for try_path in (load_path, path) if load_path != path else (path,):
        used_sibling = try_path != path and want_bold
        for idx in (0, 1, 2, 3, 4):
            try:
                font, weight_applied = _load_truetype(
                    str(try_path),
                    int(size),
                    idx,
                    want_w if want_bold else 400,
                )
                # 供 card_render 决定是否 stroke 合成加粗
                effective = (not want_bold) or weight_applied or used_sibling
                try:
                    font.hapi_bold_effective = effective  # type: ignore[attr-defined]
                    font.hapi_want_bold = want_bold  # type: ignore[attr-defined]
                except Exception:
                    pass
                return font
            except Exception as e:
                last_err = e
                if try_path.suffix.lower() not in (".ttc", ".otc"):
                    break
    raise RuntimeError(f"无法加载字体 {path}: {last_err}")


@lru_cache(maxsize=32)
def _load_fallback_fonts(size: int) -> tuple[Any, ...]:
    """Load optional system fonts used for characters absent from the card font."""
    from PIL import ImageFont

    fonts = []
    for raw_path in _SYSTEM_FALLBACKS:
        path = Path(raw_path)
        try:
            if not path.is_file() or path.stat().st_size <= 1024:
                continue
            fonts.append(ImageFont.truetype(str(path), size=size))
        except (OSError, ValueError):
            continue
    return tuple(fonts)


def _glyph_signature(font: Any, character: str) -> tuple[Any, bytes]:
    mask = font.getmask(character)
    return mask.size, bytes(mask)


def _font_supports(font: Any, text: str) -> bool:
    """Return whether a font maps every visible character to a real glyph."""
    try:
        missing = {
            _glyph_signature(font, "\u0378"),
            _glyph_signature(font, "\U0010FFFF"),
        }
        for character in text:
            if character.isspace() or unicodedata.category(character) in {"Cc", "Cf"}:
                continue
            if _glyph_signature(font, character) in missing:
                return False
        return True
    except (OSError, TypeError, UnicodeError, ValueError):
        return False


def get_font_runs(text: str, primary_font: Any) -> list[tuple[str, Any]]:
    """Split text into runs whose fonts contain the required glyphs.

    Combining marks and variation selectors remain attached to their base
    character so a fallback font can render the complete visible cluster.
    """
    if not text:
        return []
    size = max(1, int(getattr(primary_font, "size", 16)))
    candidates = (primary_font, *_load_fallback_fonts(size))
    clusters: list[str] = []
    for character in text:
        if clusters and (
            unicodedata.combining(character)
            or character == "\u200d"
            or 0xFE00 <= ord(character) <= 0xFE0F
            or 0xE0100 <= ord(character) <= 0xE01EF
        ):
            clusters[-1] += character
        else:
            clusters.append(character)

    runs: list[tuple[str, Any]] = []
    for cluster in clusters:
        font = next((candidate for candidate in candidates if _font_supports(candidate, cluster)), primary_font)
        if runs and runs[-1][1] is font:
            runs[-1] = (runs[-1][0] + cluster, font)
        else:
            runs.append((cluster, font))
    return runs


def _source_label(src: str | None) -> str:
    return {
        "user": "自定义路径",
        "bundled": "插件 assets/fonts",
        "plugin": "插件 assets/fonts",
        "system": "系统",
    }.get(src or "", "未找到")


def clear_font_cache_meta() -> None:
    _load_truetype.cache_clear()


# pip 包体积粗估（wheel + 常见依赖，供 WebUI 展示；非精确）
_DEP_SIZE_HINTS = {
    "dep_pillow": {
        "approx_mb": 3,
        "approx_label": "约 3MB",
        "desc_extra": "低延迟出图，不依赖浏览器",
    },
    "dep_matplotlib": {
        "approx_mb": 40,
        "approx_label": "约 40MB",
        "desc_extra": "含 numpy 等依赖；公式用它渲染（可选）",
    },
}


def _font_entry(path: Path, where: str, where_label: str) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size <= 1024:
            return None
    except OSError:
        return None
    if path.suffix.lower() not in (".ttf", ".otf", ".ttc", ".otc"):
        return None
    return {
        "path": str(path),
        "name": path.name,
        "where": where,
        "where_label": where_label,
        "label": f"{path.name}  ·  {where_label}",
        "kb": max(1, path.stat().st_size // 1024),
    }


def list_available_fonts() -> dict[str, Any]:
    """扫描可选字体，供 WebUI 下拉框。

    检测位置（仅列出扫到的文件，不做「优先级」话术）：
    1. 插件目录 assets/fonts/
    2. 本机常见 CJK 系统路径（Linux / macOS / Windows 若干固定路径）
    """
    seen: set[str] = set()
    items: list[dict[str, Any]] = []

    def add(path: Path | str, where: str, where_label: str) -> None:
        p = Path(path)
        key = str(p)
        if key in seen:
            return
        ent = _font_entry(p, where, where_label)
        if ent is None:
            return
        seen.add(key)
        items.append(ent)

    # 1) 插件 assets/fonts
    if _BUNDLED_DIR.is_dir():
        try:
            for p in sorted(_BUNDLED_DIR.iterdir(), key=lambda x: x.name.lower()):
                add(p, "plugin", "插件 assets/fonts")
        except OSError:
            pass

    # 2) 系统常见路径
    for p in list(_SYSTEM_SANS) + list(_SYSTEM_MONO):
        add(p, "system", "系统")

    return {
        "scan_locations": [
            {
                "id": "plugin",
                "label": "插件目录",
                "path": str(_BUNDLED_DIR),
                "hint": "你可以把 .ttf/.otf/.ttc 等字体文件放在此目录，或点击下方安装 Noto（请注意：卸载插件时会将此目录字体文件全部清理）",
            },
            {
                "id": "system",
                "label": "系统常见路径",
                "path": None,
                "hint": "Linux Noto/文泉驿、macOS PingFang、Windows 雅黑/黑体等固定路径",
            },
        ],
        "fonts": items,
        "count": len(items),
    }


def font_status(
    *, user_path: str | None = None, allow_download: bool = False
) -> dict[str, Any]:
    """WebUI / meta：当前生效字体 + 扫描列表。"""
    _ = allow_download
    scanned = list_available_fonts()
    user = resolve_user_font(user_path)
    active, src = resolve_font_path_with_source(mono=False, user_path=user_path)
    ok = active is not None
    return {
        "ok": ok,
        "bundled_dir": str(_BUNDLED_DIR),
        "active": str(active) if active else None,
        "active_name": active.name if active else None,
        "active_where": src,
        "active_where_label": _source_label(src),
        "user_font": str(user) if user else None,
        "user_font_name": user.name if user else None,
        # 兼容旧字段
        "sans": str(active) if active else None,
        "sans_name": active.name if active else None,
        "sans_source": src,
        "sans_source_label": _source_label(src),
        "mono": None,
        "mono_name": None,
        "error": None
        if ok
        else (
            "未找到可用中文字体。"
            f"可在下拉框选择，或把字体放到 {_BUNDLED_DIR}，或填自定义路径。"
        ),
        "scan_locations": scanned["scan_locations"],
        "fonts": scanned["fonts"],
        "count": scanned["count"],
    }


# ──── 可选：用户显式安装到插件目录（非自动） ────

# 仅 Noto Sans SC 一份，足够画中文；不塞 16MB mono
# 路径已实测 HTTP 200（notofonts/noto-cjk 仓库 SubsetOTF/SC，约 8.3MB OTF）
# 旧路径 Sans/OTF/SimplifiedChinese/... 已 404，勿再用
_FONT_PACK = {
    "id": "noto_sans_sc",
    "label": "中文字体 Noto Sans SC",
    "filename": "NotoSansSC-Regular.otf",
    "approx_mb": 8,
    "license": "SIL OFL",
    "urls": [
        "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf",
        "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf",
        "https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf",
    ],
    "min_bytes": 1_000_000,
}


def installable_items() -> list[dict[str, Any]]:
    """WebUI 勾选项：字体 + Pillow（均可单独选）。不含 Chromium。"""
    bundled = _scan_dir(_BUNDLED_DIR, prefer_mono=False)
    font_path = _BUNDLED_DIR / _FONT_PACK["filename"]
    font_present = font_path.is_file() and font_path.stat().st_size >= _FONT_PACK["min_bytes"]
    if not font_present and bundled is not None:
        font_present = True  # 目录里已有其它可用 CJK

    try:
        import PIL  # noqa: F401

        pillow_ok = True
        pillow_ver = getattr(PIL, "__version__", "?")
    except ImportError:
        pillow_ok = False
        pillow_ver = None

    try:
        import matplotlib  # noqa: F401

        mpl_ok = True
        mpl_ver = getattr(matplotlib, "__version__", "?")
    except ImportError:
        mpl_ok = False
        mpl_ver = None

    pillow_hint = _DEP_SIZE_HINTS["dep_pillow"]
    mpl_hint = _DEP_SIZE_HINTS["dep_matplotlib"]
    return [
        {
            "id": "font_noto_sc",
            "group": "font",
            "label": _FONT_PACK["label"],
            "desc": f"下载到插件 assets/fonts/（约 {_FONT_PACK['approx_mb']}MB，{_FONT_PACK['license']}）",
            "target": str(_BUNDLED_DIR / _FONT_PACK["filename"]),
            "installed": font_present,
            "detail": str(bundled) if bundled else None,
            "approx_mb": _FONT_PACK["approx_mb"],
            "approx_label": f"约 {_FONT_PACK['approx_mb']}MB",
        },
        {
            "id": "dep_pillow",
            "group": "dep",
            "label": "Pillow（出图引擎）",
            "desc": (
                f"pip install Pillow — {pillow_hint['desc_extra']}"
                f"（{pillow_hint['approx_label']}）"
            ),
            "target": "pip:Pillow",
            "installed": pillow_ok,
            "detail": f"v{pillow_ver}" if pillow_ok else None,
            "approx_mb": pillow_hint["approx_mb"],
            "approx_label": pillow_hint["approx_label"],
        },
        {
            "id": "dep_matplotlib",
            "group": "dep",
            "label": "matplotlib（公式）",
            "desc": (
                f"pip install matplotlib — {mpl_hint['desc_extra']}"
                f"（{mpl_hint['approx_label']}）"
            ),
            "target": "pip:matplotlib",
            "installed": mpl_ok,
            "detail": f"v{mpl_ver}" if mpl_ok else None,
            "approx_mb": mpl_hint["approx_mb"],
            "approx_label": mpl_hint["approx_label"],
        },
    ]


def download_font_to_bundled(
    *, force: bool = False, progress: list[str] | None = None
) -> dict[str, Any]:
    """用户勾选后：把 Noto Sans SC 流式下载到 assets/fonts/。"""
    import urllib.request

    log = progress if progress is not None else []
    _BUNDLED_DIR.mkdir(parents=True, exist_ok=True)
    dest = _BUNDLED_DIR / _FONT_PACK["filename"]
    if dest.is_file() and dest.stat().st_size >= _FONT_PACK["min_bytes"] and not force:
        clear_font_cache_meta()
        msg = f"已存在 {dest.name}（{dest.stat().st_size // 1024} KB），跳过"
        log.append(msg)
        return {
            "ok": True,
            "skipped": True,
            "path": str(dest),
            "bytes": dest.stat().st_size,
            "message": msg,
            "log": list(log),
        }

    tmp = dest.with_suffix(dest.suffix + ".part")
    last_err: str | None = None
    for url in _FONT_PACK["urls"]:
        try:
            log.append(f"开始下载: {url}")
            logger.info("用户请求下载字体: %s → %s", url, dest)
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "astrbot-plugin-hapi-connector/font-install"},
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                total = resp.headers.get("Content-Length")
                total_n = int(total) if total and str(total).isdigit() else 0
                if total_n:
                    log.append(f"大小约 {total_n // 1024} KB")
                chunks: list[bytes] = []
                got = 0
                last_pct = -1
                while True:
                    block = resp.read(64 * 1024)
                    if not block:
                        break
                    chunks.append(block)
                    got += len(block)
                    if total_n > 0:
                        pct = min(100, got * 100 // total_n)
                        if pct >= last_pct + 10 or pct == 100:
                            log.append(f"进度 {pct}%（{got // 1024} KB）")
                            last_pct = pct
                    elif got and got % (512 * 1024) < 64 * 1024:
                        log.append(f"已下载 {got // 1024} KB…")
                data = b"".join(chunks)
            if len(data) < int(_FONT_PACK["min_bytes"]):
                last_err = f"体积异常 {len(data)} B from {url}"
                log.append(last_err)
                continue
            tmp.write_bytes(data)
            tmp.replace(dest)
            clear_font_cache_meta()
            msg = f"已保存到 {dest}（{dest.stat().st_size // 1024} KB）"
            log.append(msg)
            return {
                "ok": True,
                "skipped": False,
                "path": str(dest),
                "bytes": dest.stat().st_size,
                "message": msg,
                "log": list(log),
            }
        except Exception as e:
            last_err = str(e)
            log.append(f"失败: {e}")
            logger.warning("字体下载失败 (%s): %s", url, e)
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
    return {
        "ok": False,
        "skipped": False,
        "path": str(dest),
        "error": last_err or "下载失败",
        "message": f"字体下载失败: {last_err}",
        "log": list(log),
    }


def _package_importable(mod_name: str) -> tuple[bool, str | None]:
    try:
        mod = __import__(mod_name)
        ver = getattr(mod, "__version__", None)
        return True, str(ver) if ver else None
    except ImportError:
        return False, None


def install_pip_package(
    spec: str,
    *,
    progress: list[str] | None = None,
    import_name: str | None = None,
) -> dict[str, Any]:
    """用户勾选后：python -m pip install 指定包。"""
    import subprocess
    import sys

    log = progress if progress is not None else []
    mod = import_name or spec.split(">=", 1)[0].split("==", 1)[0].split("[", 1)[0].strip()
    # Pillow 的 import 名是 PIL
    if mod.lower() == "pillow":
        mod = "PIL"

    ok_imp, ver = _package_importable(mod)
    if ok_imp:
        msg = f"已可 import {mod}" + (f"（v{ver}）" if ver else "") + "，跳过 pip"
        log.append(msg)
        return {
            "ok": True,
            "skipped": True,
            "message": msg,
            "log": list(log),
        }

    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--disable-pip-version-check",
        spec,
    ]
    log.append("执行: " + " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        out = proc.stdout or ""
        err = proc.stderr or ""
        combined = (out + "\n" + err).strip()
        tail_lines = combined.splitlines()[-40:] if combined else []
        log.extend(tail_lines)

        # 无 pip 模块时给出明确提示
        if proc.returncode != 0 and "No module named pip" in combined:
            msg = (
                f"当前 Python（{sys.executable}）没有 pip。"
                f"请在 AstrBot 运行环境里执行: {sys.executable} -m ensurepip && "
                f"{sys.executable} -m pip install {spec}"
            )
            log.append(msg)
            return {
                "ok": False,
                "cmd": " ".join(cmd),
                "returncode": proc.returncode,
                "message": msg,
                "log": list(log),
            }

        ok = proc.returncode == 0
        # 装完再验一次 import
        if ok:
            ok_imp2, ver2 = _package_importable(mod)
            if not ok_imp2:
                ok = False
                log.append(f"pip 成功但 import {mod} 仍失败，可能装到了别的环境")
            elif ver2:
                log.append(f"import {mod} ok · v{ver2}")

        msg = f"{'已安装' if ok else '安装失败'} {spec}（exit {proc.returncode}）"
        log.append(msg)
        return {
            "ok": ok,
            "cmd": " ".join(cmd),
            "returncode": proc.returncode,
            "stdout_tail": out[-3000:],
            "stderr_tail": err[-3000:],
            "message": msg,
            "log": list(log),
        }
    except Exception as e:
        log.append(f"pip 执行失败: {e}")
        return {
            "ok": False,
            "cmd": " ".join(cmd),
            "error": str(e),
            "message": f"pip 执行失败: {e}",
            "log": list(log),
        }


def install_selected(ids: list[str], *, force_font: bool = False) -> dict[str, Any]:
    """按勾选 id 安装；可多选，逐项执行。返回 log 便于 WebUI 展示进度。"""
    wanted = [str(x).strip() for x in (ids or []) if str(x).strip()]
    allowed = {x["id"] for x in installable_items()}
    unknown = [x for x in wanted if x not in allowed]
    if unknown:
        return {
            "ok": False,
            "error": f"未知选项: {', '.join(unknown)}",
            "results": [],
            "log": [f"未知选项: {', '.join(unknown)}"],
        }
    if not wanted:
        return {
            "ok": False,
            "error": "未勾选任何项",
            "results": [],
            "log": ["未勾选任何项"],
        }

    import sys

    results: list[dict[str, Any]] = []
    all_log: list[str] = [
        f"运行环境 Python: {sys.executable}",
        f"插件字体目录: {_BUNDLED_DIR}",
    ]
    for item_id in wanted:
        all_log.append(f"—— {item_id} ——")
        item_log: list[str] = []
        if item_id == "font_noto_sc":
            r = download_font_to_bundled(force=force_font, progress=item_log)
            results.append({"id": item_id, **r})
        elif item_id == "dep_pillow":
            r = install_pip_package("Pillow>=10.0,<12", progress=item_log, import_name="PIL")
            results.append({"id": item_id, **r})
        elif item_id == "dep_matplotlib":
            r = install_pip_package(
                "matplotlib>=3.7,<4",
                progress=item_log,
                import_name="matplotlib",
            )
            results.append({"id": item_id, **r})
        else:
            continue
        all_log.extend(item_log)

    all_ok = all(r.get("ok") or r.get("skipped") for r in results)
    return {
        "ok": all_ok,
        "results": results,
        "items": installable_items(),
        "log": all_log,
        "message": "所选项目已处理" if all_ok else "部分项目失败，见 log",
        "output": "\n".join(all_log),
    }
