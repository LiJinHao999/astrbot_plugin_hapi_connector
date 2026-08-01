"""推送卡片渲染。

能力：
1. 结构卡（list / pending / status / permission / routes）
2. 对话卡（message）：Markdown 子集
3. **自定义 CSS**：`card_custom_css`；Pillow 解析 `--card-*` 变量与基础排版
4. **字体**：见 font_manager（card_font_path → assets/fonts → 系统 CJK；无则回退文本）

可选依赖：
    pip install Pillow
    pip install matplotlib  # formula_mode=detect：每个公式单独紧凑出图，嵌进 Pillow 消息卡
"""

from __future__ import annotations

import html
import io
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover
    import logging

    logger = logging.getLogger("hapi_connector.card_render")

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore

    _HAS_PILLOW = True
except ImportError:  # pragma: no cover
    Image = ImageDraw = ImageFont = None  # type: ignore
    _HAS_PILLOW = False

try:
    from . import font_manager
except ImportError:  # pragma: no cover — 脚本直跑 / 未作为包部署
    try:
        import font_manager  # type: ignore
    except ImportError:
        class _FontManagerStub:
            def font_status(self):
                return {"sans": None, "mono": None, "user_font": None}
            def installable_items(self):
                return []
            def bundled_dir(self):
                from pathlib import Path
                return Path(__file__).resolve().parent.parent / "assets" / "fonts"
            def resolve_font_path(self, *a, **k):
                return None
            def ensure_default_fonts(self):
                return {}
            def load_image_font(self, *a, **k):
                raise RuntimeError("font_manager 不可用")
        font_manager = _FontManagerStub()  # type: ignore


RENDER_MODES = ("text", "card")
# off=关闭公式渲染（公式当普通字）
# detect=公式用 matplotlib 渲染（嵌进消息卡；无公式消息仍可按 render_kinds 出图）
# formula_only=仅含公式的 Agent 消息出图（其它消息只发文字）
# plain=消息含公式时只发文字（无公式仍可出图）
FORMULA_MODES = ("off", "detect", "formula_only", "plain")
CARD_KINDS = (
    "session_list",
    "pending",
    "status",
    "permission",
    "routes",
    "message",
)
DENSITY_OPTIONS = ("comfortable", "compact")
PRESET_IDS = ("terminal_light", "terminal_dark", "clean", "compact")
DEFAULT_KINDS = (
    "session_list",
    "pending",
    "status",
    "permission",
    "routes",
    "message",
)

# 默认 CSS：用户可在 WebUI 整段覆盖。
# Pillow 读取 :root 全部 --card-*（色 + 字号 + 布局尺寸）；选择器仅 DOM 预览用。
DEFAULT_CARD_CSS = """\
/* ============================================
 * 推送卡片样式
 *
 * ① :root 里的 --card-*  —— 出图真正读这些
 *    颜色 / 宽度 / 字号 / 徽章 / 序号框 / 行距
 * ② 下面的 .card / .row 等 —— 只给网页预览
 *    聊天出图不读选择器，只认上面的变量
 * ============================================ */
:root {
  /* —— 颜色 —— */
  --card-bg: #f7f4ea;
  --card-fg: #14120f;
  --card-accent: #0f6b3c;
  --card-muted: #3a362e;
  --card-border: #c9c2b0;
  --card-code-bg: #ebe4d0;

  /* —— 整体尺寸 —— */
  --card-radius: 12px;
  --card-pad: 28px;
  --card-width: 720px;
  --card-font-scale: 1.12;

  /* —— 字号 —— */
  --card-title-size: 24px;
  --card-sub-size: 14.5px;
  --card-body-size: 16.5px;
  --card-meta-size: 13.5px;
  --card-foot-size: 13px;
  --card-mono: 0;

  /* —— status 状态徽章 —— */
  --card-badge-h: 40px;
  --card-badge-pad-x: 20px;
  --card-badge-font: 16.5px;
  --card-badge-dot: 6px;

  /* —— list 序号框 —— */
  --card-idx-w: 46px;
  --card-idx-h: 32px;
  --card-idx-font: 14px;
  --card-idx-radius: 7px;
  --card-idx-top: 6px;

  /* —— 行距 / 间距 —— */
  --card-row-pad-y: 13px;
  --card-row-pad-x: 14px;
  --card-row-gap: 10px;
  --card-section-gap: 16px;

  /* —— 工具调用条（detail 消息卡；出图可读）—— */
  --card-tool-bg: #e8f0e9;
  --card-tool-fg: #14120f;
  --card-tool-accent: #0f6b3c;
  --card-tool-border: #c9c2b0;
  --card-tool-radius: 8px;
  --card-tool-pad-y: 10px;
  --card-tool-pad-x: 14px;
  --card-tool-gap: 12px;
  --card-tool-bar-w: 4px;
  /* Edit 差异行 */
  --card-diff-add: #0f6b3c;
  --card-diff-del: #ed333b;
  /* Ask 条（可与 tool 区分） */
  --card-ask-bg: #eef3e8;
}

/* —— 以下仅 DOM 预览用 —— */
* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: transparent;
  font-family: "HapiCard", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
  color: var(--card-fg);
}

.card {
  width: var(--card-width);
  max-width: 100%;
  background: var(--card-bg);
  color: var(--card-fg);
  border: 2px solid var(--card-border);
  border-radius: var(--card-radius);
  padding: var(--card-pad);
  font-size: calc(var(--card-body-size) * var(--card-font-scale));
  line-height: 1.55;
}

.card.mono {
  font-family: "HapiCardMono", "HapiCard", "Noto Sans Mono CJK SC", ui-monospace, monospace;
}

.card-title {
  font-size: calc(var(--card-title-size) * var(--card-font-scale));
  font-weight: 700;
  letter-spacing: 0.01em;
  margin-bottom: 6px;
}

.card-sub {
  color: var(--card-muted);
  font-size: 0.92em;
  margin-bottom: 12px;
}

.card-bar {
  width: 120px;
  height: 3px;
  background: var(--card-accent);
  border-radius: 2px;
  margin-bottom: 14px;
}

.row { margin-bottom: 12px; }
.row-section {
  margin: 14px 0 6px;
  font-weight: 700;
  color: var(--card-accent);
  font-size: 0.95em;
}
.row-section .row-detail {
  display: inline;
  padding-left: 6px;
  font-weight: 500;
  color: var(--card-muted);
  font-size: 0.9em;
}
.row-head { font-weight: 600; }
.row-detail {
  color: var(--card-muted);
  margin-top: 2px;
  padding-left: 12px;
  word-break: break-word;
}

.card-foot {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--card-border);
  color: var(--card-accent);
  font-size: 0.9em;
  white-space: pre-wrap;
}


/* 工具条（DOM 预览；出图读 :root 变量） */
.tool-block {
  background: var(--card-tool-bg);
  color: var(--card-tool-fg);
  border: 1px solid var(--card-tool-border);
  border-radius: var(--card-tool-radius);
  padding: var(--card-tool-pad-y) var(--card-tool-pad-x);
  margin: 0 0 var(--card-tool-gap) 0;
  border-left: var(--card-tool-bar-w) solid var(--card-tool-accent);
}
.tool-block.ask { background: var(--card-ask-bg); }
.tool-block .tool-head {
  font-weight: 700;
  color: var(--card-tool-accent);
  margin-bottom: 4px;
}
.tool-block .tool-detail { font-family: "HapiCardMono", monospace; font-size: 0.9em; white-space: pre-wrap; }
.tool-block .diff-add { color: var(--card-diff-add); }
.tool-block .diff-del { color: var(--card-diff-del); }

.card-brand {
  margin-top: 12px;
  text-align: right;
  color: var(--card-muted);
  font-size: 0.78em;
}

/* 对话 Markdown */
.md h1, .md h2, .md h3 {
  font-weight: 700;
  margin: 0.7em 0 0.35em;
  line-height: 1.3;
}
.md h1 { font-size: 1.35em; }
.md h2 { font-size: 1.2em; }
.md h3 { font-size: 1.08em; }
.md p { margin: 0.45em 0; white-space: pre-wrap; word-break: break-word; }
.md ul, .md ol { margin: 0.4em 0 0.4em 1.2em; }
.md li { margin: 0.2em 0; }
.md code {
  font-family: "HapiCardMono", "HapiCard", ui-monospace, monospace;
  background: var(--card-code-bg);
  padding: 0.1em 0.35em;
  border-radius: 4px;
  font-size: 0.92em;
}
.md pre {
  background: var(--card-code-bg);
  border: 1px solid var(--card-border);
  border-radius: 8px;
  padding: 10px 12px;
  overflow-x: auto;
  margin: 0.55em 0;
  font-family: "HapiCardMono", "HapiCard", ui-monospace, monospace;
  font-size: 0.88em;
  white-space: pre-wrap;
  word-break: break-word;
}
.md pre code { background: transparent; padding: 0; }
.md blockquote {
  border-left: 3px solid var(--card-accent);
  margin: 0.5em 0;
  padding: 0.2em 0 0.2em 12px;
  color: var(--card-muted);
}
.md a { color: var(--card-accent); text-decoration: none; }
.md hr {
  border: none;
  border-top: 1px solid var(--card-border);
  margin: 0.8em 0;
}
.md strong { font-weight: 700; }
.md em { font-style: italic; }
.md table {
  width: 100%;
  border-collapse: collapse;
  margin: 0.55em 0;
  font-size: 0.92em;
}
.md th, .md td {
  border: 1px solid var(--card-border);
  padding: 6px 10px;
  text-align: left;
  vertical-align: top;
  word-break: break-word;
}
.md th {
  background: var(--card-code-bg);
  font-weight: 700;
  color: var(--card-fg);
}
"""


@dataclass
class CardStyle:
    preset: str = "terminal_light"
    width: int = 720
    padding: int = 28
    radius: int = 12
    bg: str = "#f7f4ea"
    fg: str = "#14120f"
    accent: str = "#0f6b3c"
    muted: str = "#3a362e"
    border: str = "#c9c2b0"
    code_bg: str = "#ebe4d0"
    font_scale: float = 1.12
    mono: bool = False
    show_brand: bool = False
    density: str = "comfortable"
    custom_css: str = ""
    font_path: str = ""
    # 字号基值（px，再 × font_scale）
    title_size: float = 24.0
    sub_size: float = 14.5
    body_size: float = 16.5
    meta_size: float = 13.5
    foot_size: float = 13.0
    # status 徽章
    badge_h: int = 40
    badge_pad_x: int = 20
    badge_font: float = 16.5
    badge_dot: int = 6
    # list 序号
    idx_w: int = 46
    idx_h: int = 32
    idx_font: float = 14.0
    idx_radius: int = 7
    idx_top: int = 6
    # list 行
    row_pad_y: int = 13
    row_pad_x: int = 14
    row_gap: int = 10
    section_gap: int = 16
    # 工具调用条（消息卡 detail）
    tool_bg: str = "#e8f0e9"
    tool_fg: str = "#14120f"
    tool_accent: str = "#0f6b3c"
    tool_border: str = "#c9c2b0"
    tool_radius: int = 8
    tool_pad_y: int = 10
    tool_pad_x: int = 14
    tool_gap: int = 12
    tool_bar_w: int = 4
    diff_add: str = "#0f6b3c"
    diff_del: str = "#ed333b"
    ask_bg: str = "#eef3e8"

    def resolved(self) -> "CardStyle":
        preset = self.preset if self.preset in PRESETS else "terminal_light"
        dens = self.density if self.density in DENSITY_OPTIONS else "comfortable"
        width = max(400, min(1400, int(self.width or 720)))
        scale = float(self.font_scale or 1.12)
        if scale > 3:
            scale = scale / 100.0
        scale = max(0.85, min(1.6, scale))

        def _i(v, lo, hi, default):
            try:
                return max(lo, min(hi, int(v)))
            except (TypeError, ValueError):
                return default

        def _f(v, lo, hi, default):
            try:
                return max(lo, min(hi, float(v)))
            except (TypeError, ValueError):
                return default

        return CardStyle(
            preset=preset,
            width=width,
            padding=_i(self.padding, 8, 64, 28),
            radius=_i(self.radius, 0, 32, 12),
            bg=self.bg or "#f7f4ea",
            fg=self.fg or "#14120f",
            accent=self.accent or "#0f6b3c",
            muted=self.muted or "#3a362e",
            border=self.border or "#c9c2b0",
            code_bg=self.code_bg or "#ebe4d0",
            font_scale=scale,
            mono=bool(self.mono),
            show_brand=bool(self.show_brand),
            density=dens,
            custom_css=self.custom_css or "",
            font_path=self.font_path or "",
            title_size=_f(self.title_size, 12, 48, 24),
            sub_size=_f(self.sub_size, 10, 28, 14.5),
            body_size=_f(self.body_size, 10, 32, 16.5),
            meta_size=_f(self.meta_size, 9, 24, 13.5),
            foot_size=_f(self.foot_size, 9, 22, 13),
            badge_h=_i(self.badge_h, 20, 80, 40),
            badge_pad_x=_i(self.badge_pad_x, 8, 48, 20),
            badge_font=_f(self.badge_font, 10, 28, 16.5),
            badge_dot=_i(self.badge_dot, 2, 16, 6),
            idx_w=_i(self.idx_w, 24, 96, 46),
            idx_h=_i(self.idx_h, 0, 96, 32),
            idx_font=_f(self.idx_font, 10, 28, 14),
            idx_radius=_i(self.idx_radius, 0, 24, 7),
            idx_top=_i(self.idx_top, 0, 40, 6),
            row_pad_y=_i(self.row_pad_y, 4, 40, 13),
            row_pad_x=_i(self.row_pad_x, 4, 40, 14),
            row_gap=_i(self.row_gap, 0, 32, 10),
            section_gap=_i(self.section_gap, 0, 40, 16),
            tool_bg=self.tool_bg or "#e8f0e9",
            tool_fg=self.tool_fg or self.fg or "#14120f",
            tool_accent=self.tool_accent or self.accent or "#0f6b3c",
            tool_border=self.tool_border or self.border or "#c9c2b0",
            tool_radius=_i(self.tool_radius, 0, 24, 8),
            tool_pad_y=_i(self.tool_pad_y, 4, 32, 10),
            tool_pad_x=_i(self.tool_pad_x, 4, 40, 14),
            tool_gap=_i(self.tool_gap, 0, 40, 12),
            tool_bar_w=_i(self.tool_bar_w, 0, 16, 4),
            diff_add=self.diff_add or self.accent or "#0f6b3c",
            diff_del=self.diff_del or "#ed333b",
            ask_bg=self.ask_bg or self.tool_bg or "#eef3e8",
        )


PRESETS: dict[str, dict[str, Any]] = {
    "terminal_light": {
        "preset": "terminal_light",
        "width": 720,
        "padding": 28,
        "radius": 12,
        "bg": "#f7f4ea",
        "fg": "#14120f",
        "accent": "#0f6b3c",
        "muted": "#3a362e",
        "border": "#c9c2b0",
        "code_bg": "#ebe4d0",
        "font_scale": 1.12,
        "mono": False,
        "show_brand": False,
        "density": "comfortable",
    },
    "terminal_dark": {
        "preset": "terminal_dark",
        "width": 720,
        "padding": 28,
        "radius": 12,
        "bg": "#16140f",
        "fg": "#f4efe4",
        "accent": "#4ade9b",
        "muted": "#c4bba8",
        "border": "#3d3a32",
        "code_bg": "#2a261e",
        "font_scale": 1.12,
        "mono": False,
        "show_brand": False,
        "density": "comfortable",
    },
    "clean": {
        "preset": "clean",
        "width": 720,
        "padding": 30,
        "radius": 16,
        "bg": "#ffffff",
        "fg": "#0f172a",
        "accent": "#1d4ed8",
        "muted": "#334155",
        "border": "#cbd5e1",
        "code_bg": "#f1f5f9",
        "font_scale": 1.15,
        "mono": False,
        "show_brand": False,
        "density": "comfortable",
    },
    "compact": {
        "preset": "compact",
        "width": 600,
        "padding": 20,
        "radius": 10,
        "bg": "#f7f4ea",
        "fg": "#14120f",
        "accent": "#0f6b3c",
        "muted": "#3a362e",
        "border": "#c9c2b0",
        "code_bg": "#ebe4d0",
        "font_scale": 1.05,
        "mono": False,
        "show_brand": False,
        "density": "compact",
    },
}


@dataclass
class RenderResult:
    ok: bool
    png: bytes | None = None
    mime: str = "image/png"
    width: int = 0
    height: int = 0
    bytes_len: int = 0
    ms: float = 0.0
    engine: str = "none"
    error: str | None = None
    kind: str = ""
    fallback_text: str = ""
    font_path: str = ""


def pillow_available() -> bool:
    return _HAS_PILLOW


def engine_status(user_font_path: str | None = None) -> dict[str, Any]:
    fonts = font_manager.font_status(user_path=user_font_path or None)
    mpl = matplotlib_available()
    return {
        "pillow": _HAS_PILLOW,
        "matplotlib": mpl,
        "engines": {
            "card": "pillow" if _HAS_PILLOW else None,
            "message_formula": "matplotlib" if mpl else None,
        },
        "fonts": fonts,
        "installable": font_manager.installable_items(),
        "install_hint": _install_hint(),
    }


def _install_hint() -> str | None:
    parts = []
    if not _HAS_PILLOW:
        parts.append("pip install Pillow（或 WebUI 勾选安装）")
    if not matplotlib_available():
        parts.append("公式渲染：pip install matplotlib（或 WebUI 勾选）")
    fonts = font_manager.font_status()
    if not fonts.get("sans") and not fonts.get("user_font"):
        parts.append(
            f"中文字体：WebUI 勾选安装，或放到 {font_manager.bundled_dir()} / 配置 card_font_path"
        )
    return " · ".join(parts) if parts else None


def parse_kinds(raw: Any) -> list[str]:
    if raw is None:
        return list(DEFAULT_KINDS)
    if isinstance(raw, (list, tuple)):
        items = [str(x).strip() for x in raw]
    else:
        s = str(raw).strip()
        if not s:
            return list(DEFAULT_KINDS)
        if s.startswith("["):
            try:
                import json

                data = json.loads(s)
                if isinstance(data, list):
                    items = [str(x).strip() for x in data]
                else:
                    items = [p.strip() for p in s.split(",")]
            except Exception:
                items = [p.strip() for p in s.split(",")]
        else:
            items = [p.strip() for p in s.replace("，", ",").split(",")]
    out = [k for k in items if k in CARD_KINDS]
    return out or list(DEFAULT_KINDS)


def kinds_to_storage(kinds: list[str]) -> str:
    return ",".join(k for k in kinds if k in CARD_KINDS) or ",".join(DEFAULT_KINDS)


def _parse_bool(v: Any, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def _css_num(raw: Any, default: float) -> float:
    """从 CSS 值抽数字：'40px' / '1.12' → float。"""
    if raw is None:
        return default
    s = str(raw).strip()
    if not s:
        return default
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return default
    try:
        return float(m.group(0))
    except ValueError:
        return default


def style_from_config(cfg: dict[str, Any] | None) -> CardStyle:
    cfg = cfg or {}
    preset = str(cfg.get("card_style_preset") or "terminal_light").strip()
    if preset not in PRESETS:
        preset = "terminal_light"
    base = dict(PRESETS[preset])

    def _int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            n = int(cfg.get(key, default))
        except (TypeError, ValueError):
            n = default
        return max(lo, min(hi, n))

    def _float_scale(raw: Any, default: float = 1.0) -> float:
        try:
            v = float(raw)
            if v > 3:
                v = v / 100.0
            return max(0.75, min(1.6, v))
        except (TypeError, ValueError):
            return default

    def _color(key: str, default: str) -> str:
        if key not in cfg or cfg.get(key) in (None, ""):
            return default
        s = str(cfg.get(key)).strip()
        if not s.startswith("#"):
            s = "#" + s
        if len(s) not in (4, 7):
            return default
        try:
            _hex_to_rgb(s)
        except Exception:
            return default
        return s

    density = str(cfg.get("card_density") or base.get("density") or "comfortable")
    if density not in DENSITY_OPTIONS:
        density = "comfortable"

    width = _int("card_width", int(base["width"]), 400, 1400)
    mono = base.get("mono", True)
    if "card_mono" in cfg and cfg.get("card_mono") is not None:
        mono = _parse_bool(cfg.get("card_mono"), bool(mono))
    # 产品要求：推送图片右下角永不显示 hapi connector 角标
    show_brand = False

    custom_css = ""
    if cfg.get("card_custom_css") is not None:
        custom_css = str(cfg.get("card_custom_css") or "")
    font_path = str(cfg.get("card_font_path") or "").strip()

    vars_from_css = extract_css_vars(custom_css) if custom_css else {}

    def pick_color(key: str, conf_key: str, base_key: str) -> str:
        if conf_key in cfg and cfg.get(conf_key) not in (None, ""):
            return _color(conf_key, str(base[base_key]))
        if key in vars_from_css:
            return vars_from_css[key]
        return str(base[base_key])

    def vnum(css_key: str, default: float) -> float:
        if css_key in vars_from_css:
            return _css_num(vars_from_css[css_key], default)
        return default

    pad = 16 if density == "compact" else int(base.get("padding", 28))
    pad = int(vnum("--card-pad", pad))
    radius = int(vnum("--card-radius", int(base.get("radius", 12))))
    if "--card-width" in vars_from_css:
        width = max(400, min(1400, int(vnum("--card-width", width))))

    if "card_font_scale" in cfg and cfg.get("card_font_scale") not in (None, ""):
        font_scale = _float_scale(
            cfg.get("card_font_scale"), float(base.get("font_scale", 1.12))
        )
    elif "--card-font-scale" in vars_from_css:
        font_scale = _float_scale(
            vars_from_css["--card-font-scale"], float(base.get("font_scale", 1.12))
        )
    else:
        font_scale = float(base.get("font_scale", 1.12))

    compact = density == "compact"
    bg_v = pick_color("--card-bg", "card_bg", "bg")
    fg_v = pick_color("--card-fg", "card_fg", "fg")
    accent_v = pick_color("--card-accent", "card_accent", "accent")
    border_v = str(vars_from_css.get("--card-border") or base["border"])
    # tool 条默认：略掺强调色的底；用户可在 CSS 里用 --card-tool-* 覆盖
    tool_bg_default = bg_v
    try:
        br, bg_, bb = _hex_to_rgb(bg_v)
        ar, ag, ab = _hex_to_rgb(accent_v)
        t = 0.10
        tool_bg_default = "#{:02x}{:02x}{:02x}".format(
            int(br * (1 - t) + ar * t),
            int(bg_ * (1 - t) + ag * t),
            int(bb * (1 - t) + ab * t),
        )
    except Exception:
        pass
    tool_bg_v = str(vars_from_css.get("--card-tool-bg") or tool_bg_default)
    ask_bg_v = str(vars_from_css.get("--card-ask-bg") or tool_bg_v)

    return CardStyle(
        preset=preset,
        width=width,
        padding=pad,
        radius=radius,
        bg=bg_v,
        fg=fg_v,
        accent=accent_v,
        muted=str(vars_from_css.get("--card-muted") or base["muted"]),
        border=border_v,
        code_bg=str(
            vars_from_css.get("--card-code-bg") or base.get("code_bg", "#efe9d8")
        ),
        font_scale=font_scale,
        mono=bool(mono),
        show_brand=bool(show_brand),
        density=density,
        custom_css=custom_css,
        font_path=font_path,
        title_size=vnum("--card-title-size", 24),
        sub_size=vnum("--card-sub-size", 14.5),
        body_size=vnum("--card-body-size", 16.5),
        meta_size=vnum("--card-meta-size", 13.5),
        foot_size=vnum("--card-foot-size", 13),
        badge_h=int(vnum("--card-badge-h", 40)),
        badge_pad_x=int(vnum("--card-badge-pad-x", 20)),
        badge_font=vnum("--card-badge-font", 16.5),
        badge_dot=int(vnum("--card-badge-dot", 6)),
        idx_w=int(vnum("--card-idx-w", 40 if compact else 46)),
        idx_h=int(vnum("--card-idx-h", 28 if compact else 32)),
        idx_font=vnum("--card-idx-font", 14),
        idx_radius=int(vnum("--card-idx-radius", 7)),
        idx_top=int(vnum("--card-idx-top", 4 if compact else 6)),
        row_pad_y=int(vnum("--card-row-pad-y", 10 if compact else 13)),
        row_pad_x=int(vnum("--card-row-pad-x", 12 if compact else 14)),
        row_gap=int(vnum("--card-row-gap", 8 if compact else 10)),
        section_gap=int(vnum("--card-section-gap", 12 if compact else 16)),
        tool_bg=tool_bg_v,
        tool_fg=str(vars_from_css.get("--card-tool-fg") or fg_v),
        tool_accent=str(vars_from_css.get("--card-tool-accent") or accent_v),
        tool_border=str(vars_from_css.get("--card-tool-border") or border_v),
        tool_radius=int(vnum("--card-tool-radius", 8)),
        tool_pad_y=int(vnum("--card-tool-pad-y", 10)),
        tool_pad_x=int(vnum("--card-tool-pad-x", 14)),
        tool_gap=int(vnum("--card-tool-gap", 12)),
        tool_bar_w=int(vnum("--card-tool-bar-w", 4)),
        diff_add=str(vars_from_css.get("--card-diff-add") or accent_v),
        diff_del=str(vars_from_css.get("--card-diff-del") or "#ed333b"),
        ask_bg=ask_bg_v,
    )


def style_to_public(style: CardStyle) -> dict[str, Any]:
    d = asdict(style)
    # 不在公开字段里塞超长 css 两遍；调用方已有 config.card_custom_css
    return d


def extract_css_vars(css: str) -> dict[str, str]:
    """从 CSS 文本抽 --name: value;（足够覆盖 :root 与任意选择器）。"""
    out: dict[str, str] = {}
    if not css:
        return out
    for m in re.finditer(
        r"(--[a-zA-Z0-9-_]+)\s*:\s*([^;]+);", css
    ):
        out[m.group(1)] = m.group(2).strip()
    return out


def sample_payload(kind: str) -> dict[str, Any]:
    if kind == "pending":
        return {
            "title": "待审批",
            "subtitle": "当前窗口 2 项 · 全局 3 项",
            "rows": [
                {"index": 1, "label": "claude · auth-mw", "detail": "Bash · npm test"},
                {"index": 2, "label": "claude · auth-mw", "detail": "Edit · src/auth.ts"},
            ],
            "footer": "/hapi a  全部批准    /hapi pending  列表",
        }
    if kind == "permission":
        return {
            "title": "权限请求",
            "subtitle": "序号 1 · 重构鉴权中间件 · claude · a1b2c3d4",
            "rows": [
                {"index": 0, "label": "工具", "detail": "Bash"},
                {"index": 0, "label": "详情", "detail": "pytest -q tests/test_auth.py"},
                {"index": 0, "label": "待审批", "detail": "全局 1 · 本会话 1 · 本条序号 1"},
            ],
            "footer": "/hapi a  全部批准    /hapi allow <序号>  单项\n/hapi deny  全部拒绝    /hapi pending  列表",
        }
    if kind == "status":
        return {
            "title": "重构鉴权中间件",
            "subtitle": "claude · a1b2c3d4 · 思考中",
            "layout": "status",
            "status": "思考中",
            "status_key": "thinking",
            "flavor": "claude",
            "sid_short": "a1b2c3d4",
            "rows": [
                {"type": "kv", "label": "状态", "detail": "思考中", "status_key": "thinking"},
                {"type": "kv", "label": "Agent", "detail": "claude"},
                {"type": "kv", "label": "模型", "detail": "opus"},
                {"type": "kv", "label": "权限", "detail": "default"},
                {"type": "kv", "label": "推理", "detail": "high"},
                {"type": "kv", "label": "路径", "detail": "…/dev/proj-auth"},
                {"type": "kv", "label": "ID", "detail": "a1b2c3d4"},
            ],
            "footer": "/hapi sw  切换    /hapi list  列表    /hapi msg  最近消息",
        }
    if kind == "routes":
        return {
            "title": "推送路由",
            "subtitle": "绑定 1 · 有默认窗口 · Agent 1",
            "rows": [
                {"type": "section", "label": "会话绑定", "detail": "1", "count": 1},
                {
                    "type": "row",
                    "index": 1,
                    "sid_short": "a1b2c3d4",
                    "label": "[claude] 重构鉴权",
                    "detail": "→ Bot:maimai-群聊-1081179981",
                },
                {"type": "section", "label": "默认发送窗口", "detail": "", "count": 1},
                {
                    "type": "row",
                    "index": 0,
                    "label": "primary",
                    "detail": "Bot:maimai-私聊-2732367272",
                },
                {"type": "section", "label": "Agent 默认窗口", "detail": "1", "count": 1},
                {
                    "type": "row",
                    "index": 0,
                    "label": "claude",
                    "detail": "Bot:maimai-私聊-2732367272",
                },
            ],
            "footer": "/hapi bind  设默认推送窗口    /hapi bind <agent>  设 Agent 推送窗口    /hapi routes",
        }
    if kind == "message":
        return {
            "title": "重构鉴权中间件",
            "subtitle": "Agent 消息 · /home/dev/proj-auth · claude · a1b2c3d4",
            "body": (
                "## 修复摘要\n\n"
                "已完成鉴权中间件重构：\n\n"
                "| 项 | 状态 |\n"
                "| --- | --- |\n"
                "| JWT 校验 | 已统一 |\n"
                "| 单测覆盖 | 已补充 |\n\n"
                "```ts\n"
                "export function requireAuth(req) {\n"
                "  return verify(req.headers.authorization);\n"
                "}\n"
                "```\n\n"
                "> 建议：合并前跑一遍 `npm test`\n"
            ),
            "footer": "",
        }
    return {
        "title": "Session 列表",
        "subtitle": "当前窗口 · 3 个 · 思考 1 / 运行 1 / 关闭 1",
        "layout": "session_list",
        "rows": [
            {"type": "section", "label": "…/dev/proj-auth", "full_path": "/home/dev/proj-auth", "detail": "2", "count": 2},
            {
                "type": "session",
                "index": 1,
                "sid_short": "a1b2c3d4",
                "label": "重构鉴权中间件",
                "detail": "思考中 · claude:opus · 当前",
                "status": "思考中",
                "status_key": "thinking",
                "flavor": "claude",
                "model": "opus",
                "pending": 0,
                "is_current": True,
            },
            {
                "type": "session",
                "index": 2,
                "sid_short": "e5f6g7h8",
                "label": "补 session 列表单测",
                "detail": "已关闭 · claude:sonnet",
                "status": "已关闭",
                "status_key": "closed",
                "flavor": "claude",
                "model": "sonnet",
                "pending": 0,
                "is_current": False,
            },
            {"type": "section", "label": "…/dev/docs", "full_path": "/home/dev/docs", "detail": "1", "count": 1},
            {
                "type": "session",
                "index": 3,
                "sid_short": "i9j0k1l2",
                "label": "API 文档生成",
                "detail": "运行中 · codex:default",
                "status": "运行中",
                "status_key": "active",
                "flavor": "codex",
                "model": "default",
                "pending": 1,
                "is_current": False,
            },
        ],
        "footer": "",
    }


def payload_to_fallback_text(kind: str, data: dict[str, Any]) -> str:
    if kind == "message":
        parts = [
            str(data.get("title") or ""),
            str(data.get("subtitle") or ""),
            str(data.get("body") or data.get("text") or ""),
        ]
        footer = data.get("footer")
        if footer:
            parts.append(str(footer))
        return "\n".join(p for p in parts if p).strip()

    lines = [str(data.get("title") or kind), str(data.get("subtitle") or "")]
    for row in data.get("rows") or []:
        rtype = str(row.get("type") or "row")
        label = str(row.get("label") or "")
        detail = str(row.get("detail") or "")
        if rtype == "section":
            lines.append("")
            lines.append(f"{label}" + (f" ({detail})" if detail else ""))
            continue
        idx = row.get("index") or 0
        sid = str(row.get("sid_short") or "")
        if idx and sid:
            head = f"[{idx} | {sid}] {label}"
        elif idx:
            head = f"[{idx}] {label}"
        else:
            head = label
        lines.append(head)
        if detail:
            lines.append(detail)
    footer = data.get("footer")
    if footer:
        lines.append("")
        lines.append(str(footer))
    return "\n".join(x for x in lines if x is not None).strip()


def normalize_render_mode(raw) -> str:
    """仅 text / card。"""
    mode = str(raw or "text").strip().lower()
    if mode == "card":
        return "card"
    return "text"


def normalize_formula_mode(raw) -> str:
    """off / detect / formula_only / plain。历史 always 映射为 plain。"""
    mode = str(raw or "off").strip().lower()
    if mode == "always":
        return "plain"
    # 兼容旧别名
    if mode in ("detect_only", "math_only", "formula-only"):
        return "formula_only"
    if mode in FORMULA_MODES:
        return mode
    return "off"


# 公式：只认明确数学定界/环境，不搞命令密度白名单
_RE_FORMULA_BLOCK = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_RE_FORMULA_INLINE = re.compile(r"(?<!\$)\$(?!\$)([^\$\n]+?)\$(?!\$)")
_RE_FORMULA_BRACKET = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)
_RE_FORMULA_PAREN = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_RE_FORMULA_BEGIN = re.compile(
    r"\\begin\{(equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?|"
    r"cases|matrix|pmatrix|bmatrix|vmatrix|Vmatrix|array)\}"
    r".*?\\end\{\1\}",
    re.DOTALL,
)


def _looks_like_currency_dollar_span(inner: str) -> bool:
    """行内 $...$ 是否更像价格/普通美元，而非数学公式。

    典型误伤：表格里的 `$5 / $30`、`$0.14 / $0.28` 会被贪婪匹配成
    `$5 / $` / `$0.14 / $`，既渲染不出公式，又把 GFM 表格拆碎。
    """
    s = (inner or "").strip()
    if not s:
        return True
    # 含 LaTeX 命令或明显数学结构 → 当公式
    if re.search(r"\\[a-zA-Z]+", s):
        return False
    if re.search(r"[=^_{}\[\]<>]|\\", s):
        return False
    # 纯数字 / 金额片段（可带小数、千分位、空格、斜杠、百分号、加减）
    if re.fullmatch(r"[\d\s.,/%+\-–—]*", s):
        return True
    # 极短且无字母的残留（如 "5 / "）
    if len(s) <= 8 and not re.search(r"[A-Za-z一-鿿]", s):
        return True
    return False


def text_has_formula(text: str | None) -> bool:
    """正文是否含数学公式（仅定界符 / LaTeX 环境，不猜命令密度）。"""
    s = str(text or "")
    if not s or len(s) < 3:
        return False
    if (
        _RE_FORMULA_BLOCK.search(s)
        or _RE_FORMULA_BRACKET.search(s)
        or _RE_FORMULA_PAREN.search(s)
        or _RE_FORMULA_BEGIN.search(s)
    ):
        return True
    for m in _RE_FORMULA_INLINE.finditer(s):
        if not _looks_like_currency_dollar_span(m.group(1) or ""):
            return True
    return False


def matplotlib_available() -> bool:
    try:
        import matplotlib  # noqa: F401

        return True
    except ImportError:
        return False


def extract_formula_segments(text: str) -> list[tuple[str, str, bool]]:
    """把正文拆成 (kind, content, is_display)。

    kind 为 text|formula；is_display 仅 formula 有意义。
    块级：$$ / \\[ \\] / begin…end；行内：$ / \\( \\)。
    """
    s = str(text or "")
    if not s:
        return []

    # start, end, latex, is_display
    spans: list[tuple[int, int, str, bool]] = []

    def _add(m: re.Match, *, display: bool, group: int = 1) -> None:
        latex = (m.group(group) or "").strip()
        if latex:
            spans.append((m.start(), m.end(), latex, display))

    for m in _RE_FORMULA_BLOCK.finditer(s):
        _add(m, display=True)
    for m in _RE_FORMULA_BRACKET.finditer(s):
        _add(m, display=True)
    for m in _RE_FORMULA_BEGIN.finditer(s):
        latex = (m.group(0) or "").strip()
        if latex:
            spans.append((m.start(), m.end(), latex, True))
    for m in _RE_FORMULA_PAREN.finditer(s):
        _add(m, display=False)
    for m in _RE_FORMULA_INLINE.finditer(s):
        inner = m.group(1) or ""
        # 跳过价格类 $5 / $30，避免拆碎表格 / 列表等块级 Markdown
        if _looks_like_currency_dollar_span(inner):
            continue
        _add(m, display=False)

    if not spans:
        return [("text", s, False)]

    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    picked: list[tuple[int, int, str, bool]] = []
    for st, en, latex, disp in spans:
        if any(not (en <= ps or st >= pe) for ps, pe, _, _ in picked):
            continue
        picked.append((st, en, latex, disp))
    picked.sort(key=lambda x: x[0])

    out: list[tuple[str, str, bool]] = []
    cur = 0
    for st, en, latex, disp in picked:
        if st > cur:
            out.append(("text", s[cur:st], False))
        out.append(("formula", latex, disp))
        cur = en
    if cur < len(s):
        out.append(("text", s[cur:], False))
    return out


def _md_body_to_plain_lines(text: str, max_chars: int = 42) -> list[str]:
    """matplotlib 消息路径：Markdown 粗略收成可换行纯文本。"""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    in_code = False
    for raw in s.split("\n"):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            plain = line
        else:
            plain = line
            # 水平线
            if re.match(r"^\s*-{3,}\s*$", plain) or re.match(r"^\s*\*{3,}\s*$", plain):
                out.append("")
                continue
            is_heading = False
            if plain.startswith("#"):
                is_heading = True
                plain = plain.lstrip("#").strip()
            if not is_heading:
                if plain.startswith(("- ", "* ", "+ ")):
                    plain = "· " + plain[2:]
                elif re.match(r"^\d+\.\s+", plain):
                    plain = re.sub(r"^\d+\.\s+", "· ", plain)
            if plain.startswith(">"):
                plain = plain.lstrip("> ").strip()
            # 去掉粗体/斜体/行内代码标记，保留内容
            plain = re.sub(r"`([^`]+)`", r"\1", plain)
            plain = re.sub(r"\*\*([^*]+)\*\*", r"\1", plain)
            plain = re.sub(r"\*([^*]+)\*", r"\1", plain)
            plain = re.sub(r"__([^_]+)__", r"\1", plain)
            plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", plain)
        if not plain.strip():
            out.append("")
            continue
        # 按字符宽粗 wrap（CJK 按 1，ASCII 约 0.5 估）
        buf = ""
        width = 0.0
        limit = float(max(12, max_chars))
        for ch in plain:
            w = 1.0 if ord(ch) > 0x2E80 else 0.55
            if buf and width + w > limit:
                out.append(buf)
                buf = ch
                width = w
            else:
                buf += ch
                width += w
        if buf:
            out.append(buf)
    # 压连续空行
    cleaned: list[str] = []
    for ln in out:
        if ln == "" and cleaned and cleaned[-1] == "":
            continue
        cleaned.append(ln)
    return cleaned


def _strip_math_delimiters(latex: str) -> str:
    one = (latex or "").strip()
    if one.startswith("\\[") and one.endswith("\\]"):
        one = one[2:-2].strip()
    if one.startswith("\\(") and one.endswith("\\)"):
        one = one[2:-2].strip()
    if one.startswith("$$") and one.endswith("$$"):
        one = one[2:-2].strip()
    if one.startswith("$") and one.endswith("$") and len(one) > 2:
        one = one[1:-1].strip()
    return one


def _mathtext_candidates(latex: str) -> list[str]:
    """生成 mathtext 候选。单个失败不影响其它公式。"""
    raw = _strip_math_delimiters(latex)
    raw = " ".join((raw or "").split())
    if not raw:
        return []
    cands: list[str] = []

    def add(s: str) -> None:
        s = re.sub(r"\s+", " ", (s or "").strip())
        if s and s not in cands:
            cands.append(s)

    add(raw)

    def normalize(s: str) -> str:
        # 转置：^{\mathsf T} / ^{\mathrm T} / ^\mathsf{T} → ^{\top}
        s = re.sub(
            r"\^\{\\mathsf\s*T\}|\^\{\\mathrm\s*T\}|\^\{\\mathsf\{T\}\}|\^\{\\mathrm\{T\}\}",
            r"^{\\top}",
            s,
        )
        s = re.sub(r"\^\\mathsf\{T\}|\^\\mathrm\{T\}", r"^{\\top}", s)
        # 字体命令
        s = s.replace(r"\mathsf", r"\mathrm")
        s = s.replace(r"\mathtt", r"\mathrm")
        s = s.replace(r"\boldsymbol", r"\mathbf")
        s = s.replace(r"\bm", r"\mathbf")
        # \mathrm T（无花括号）→ \mathrm{T}
        s = re.sub(r"\\mathrm\s+([A-Za-z])", r"\\mathrm{\1}", s)
        s = re.sub(r"\\mathbf\s+([A-Za-z])", r"\\mathbf{\1}", s)
        s = s.replace(r"\dfrac", r"\frac").replace(r"\tfrac", r"\frac")
        s = s.replace(r"\gtrsim", r"\geq").replace(r"\lesssim", r"\leq")
        s = s.replace(r"\geqslant", r"\geq").replace(r"\leqslant", r"\leq")
        s = s.replace(r"\neq", r"\ne")
        s = s.replace(r"\left\|", r"\left|").replace(r"\right\|", r"\right|")
        s = s.replace(r"\|", r"|")
        s = re.sub(r"(?<!\\)%", r"\\%", s)
        return s

    n1 = normalize(raw)
    add(n1)

    # 剥 Big 修饰与细间距
    n2 = re.sub(r"\\[Bb]igg?\s*", "", n1)
    for tok in (r"\!", r"\,", r"\;", r"\:", r"\quad", r"\qquad"):
        n2 = n2.replace(tok, " ")
    add(n2)

    # 再简化：\left \right 去掉
    n3 = re.sub(r"\\left\s*|\\right\s*", "", n2)
    add(n3)

    return cands


def render_math_to_pil(
    formula: str,
    *,
    fontsize: float = 18,
    dpi: int = 200,
    fg: str = "#14120f",
    bg: str | None = None,
    max_width_px: int = 0,
) -> "Image.Image | None":
    """单条公式 → 紧凑 PIL 图（gist 思路：极小画布 + tight bbox）。

    失败返回 None；调用方只替换这一段，不拖垮整张消息卡。
    formula 可不带外层 $。
    """
    if not matplotlib_available() or not _HAS_PILLOW:
        return None
    cands = _mathtext_candidates(formula)
    if not cands:
        return None

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None

    # 用 cm 字体更像 LaTeX；失败也不致命
    try:
        plt.rcParams["mathtext.fontset"] = "cm"
    except Exception:
        pass

    last_err: Exception | None = None
    for body in cands:
        expr = body if body.startswith("$") else f"${body}$"
        fig = None
        try:
            fig = plt.figure(figsize=(0.01, 0.01), dpi=dpi)
            if bg:
                fig.patch.set_facecolor(bg)
            else:
                fig.patch.set_alpha(0.0)
            text = fig.text(0, 0, expr, fontsize=fontsize, color=fg)
            # 先画一次拿 bbox
            fig.canvas.draw()
            renderer = fig.canvas.get_renderer()
            bbox = text.get_window_extent(renderer=renderer)
            pad_in = 0.04
            width_in = max(0.15, bbox.width / float(dpi) + pad_in * 2)
            height_in = max(0.12, bbox.height / float(dpi) + pad_in * 2)
            fig.set_size_inches(width_in, height_in)
            # 居中
            text.set_position((0.5, 0.5))
            text.set_ha("center")
            text.set_va("center")

            buf = io.BytesIO()
            fig.savefig(
                buf,
                format="png",
                dpi=dpi,
                facecolor=bg if bg else "none",
                edgecolor="none",
                transparent=bg is None,
                bbox_inches="tight",
                pad_inches=0.02,
            )
            data = buf.getvalue()
            if not data or len(data) < 32:
                continue
            im = Image.open(io.BytesIO(data))
            # 有透明则贴到背景色上，方便 paste 到 RGB 卡
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")
                if bg:
                    base = Image.new("RGBA", rgba.size, (*_hex_to_rgb(bg), 255))
                    base.alpha_composite(rgba)
                    im = base.convert("RGB")
                else:
                    # 默认贴到近白，避免透明糊在深色卡上
                    base = Image.new("RGBA", rgba.size, (247, 244, 234, 255))
                    base.alpha_composite(rgba)
                    im = base.convert("RGB")
            else:
                im = im.convert("RGB")

            if max_width_px > 0 and im.width > max_width_px:
                ratio = max_width_px / float(im.width)
                nh = max(1, int(im.height * ratio))
                resample = getattr(
                    getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS
                )
                im = im.resize((max_width_px, nh), resample)
            return im
        except Exception as e:
            last_err = e
            continue
        finally:
            if fig is not None:
                plt.close(fig)
    if last_err is not None:
        logger.debug(
            "render_math_to_pil failed: %s · %s",
            last_err,
            (formula or "")[:80],
        )
    return None


def _matplotlib_probe_font(path: Path | str | None) -> "Any | None":
    """探测 matplotlib 能否加载该字体。

    Pillow 能读的 .ttc / 可变字体，matplotlib 常报 SFNT table missing。
    失败返回 None，调用方换下一个候选，**不要求用户另下字体**。
    """
    if path is None:
        return None
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size <= 1024:
            return None
    except OSError:
        return None

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.font_manager import FontProperties
    except Exception:
        return None

    fp = FontProperties(fname=str(p))
    fig = None
    try:
        fig = plt.figure(figsize=(0.5, 0.35), dpi=72)
        # 必须实际 savefig 才会触发 FreeType 读表；构造 FontProperties 本身不报错
        # 同时塞中文 + 拉丁，筛掉只有 CJK 缺 Latin 的 fallback 字体
        fig.text(0.05, 0.55, "测Aa中文Agent", fontproperties=fp, fontsize=11)
        fig.text(0.05, 0.15, r"$x^{2}+y_{i}$", fontsize=11)
        buf = io.BytesIO()
        # 用 warning 当失败信号：缺大量拉丁字形的字体质量差
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fig.savefig(buf, format="png", dpi=72)
            # SFNT 等硬错误走 except；这里只拦「缺字太多」
            miss = [
                w
                for w in caught
                if "missing from font" in str(w.message).lower()
                or "glyph" in str(w.message).lower()
            ]
            # 缺字超过 3 个视为不合格（DroidSansFallback 缺整套拉丁）
            if len(miss) > 3:
                return None
        data = buf.getvalue()
        if data and len(data) > 32:
            return fp
    except Exception:
        return None
    finally:
        if fig is not None:
            plt.close(fig)
    return None


def _resolve_matplotlib_fonts(
    user_path: str | None = None,
) -> tuple[Any, Any, str]:
    """选 matplotlib 能真正画出中文的字体。

    返回 (fp_body, fp_title, note)。
    不额外下载；扫已有 user / assets/fonts / 系统字体。
    全部不可用时退回默认 FontProperties（公式仍可渲，中文可能缺字）。
    """
    from matplotlib.font_manager import FontProperties

    # 候选：用户指定 → 插件目录 → 系统；优先单字体 ttf/otf，ttc/可变字体靠后
    paths: list[Path] = []
    seen: set[str] = set()

    def add(p: Path | str | None) -> None:
        if not p:
            return
        path = Path(p)
        key = str(path)
        if key in seen:
            return
        try:
            if path.is_file() and path.stat().st_size > 1024:
                seen.add(key)
                paths.append(path)
        except OSError:
            return

    add(font_manager.resolve_user_font(user_path))
    try:
        scanned = font_manager.list_available_fonts().get("fonts") or []
        for ent in scanned:
            add(ent.get("path"))
    except Exception:
        pass
    add(font_manager.resolve_font_path(mono=False, user_path=user_path))

    # matplotlib 字体库按族名找（有时比硬塞 .ttc 路径更稳）
    try:
        from matplotlib import font_manager as mpl_fm

        for family in (
            "Noto Sans CJK SC",
            "Noto Sans SC",
            "Source Han Sans SC",
            "WenQuanYi Micro Hei",
            "WenQuanYi Zen Hei",
            "Microsoft YaHei",
            "PingFang SC",
            "SimHei",
            "Droid Sans Fallback",
        ):
            try:
                path = mpl_fm.findfont(
                    FontProperties(family=family),
                    fallback_to_default=False,
                )
                # findfont 失败时常退回 DejaVu；排除之
                if path and "dejavu" not in str(path).lower():
                    add(path)
            except Exception:
                continue
    except Exception:
        pass

    def rank(p: Path) -> tuple[int, str]:
        n = p.name.lower()
        score = 0
        # 单字体 ttf/otf 最稳
        if p.suffix.lower() in (".ttf", ".otf"):
            score -= 20
        if p.suffix.lower() in (".ttc", ".otc"):
            score += 10
        if "vf" in n or "variable" in n:
            score += 20  # 可变字体 matplotlib 最常 SFNT 炸
        if "fallback" in n and "droid" in n:
            score += 15  # 常缺拉丁字母
        if any(
            k in n
            for k in (
                "notosanssc",
                "noto sans sc",
                "sourcehansans",
                "source han sans",
                "wqy-microhei",
                "wqy-zenhei",
                "msyh",
                "yahei",
                "pingfang",
                "noto sans cjk",
                "notosanscjk",
            )
        ):
            score -= 8
        if "cjk" in n or "sc" in n or "cn" in n:
            score -= 2
        return (score, n)

    paths = sorted(paths, key=rank)

    for p in paths:
        fp = _matplotlib_probe_font(p)
        if fp is None:
            logger.debug("matplotlib 跳过不可用字体: %s", p)
            continue
        logger.info("matplotlib 正文字体: %s", p)
        return fp, fp, str(p)

    logger.warning(
        "matplotlib 未能加载合适的中文字体（.ttc/可变字体常不兼容）。"
        "公式仍渲染；中文可能缺字。无需额外下载依赖——"
        "把 NotoSansSC-Regular.otf 放到插件 assets/fonts/，或在 card_font_path 指定 .ttf/.otf 即可。"
    )
    fp = FontProperties()
    return fp, fp, ""


def render_message_matplotlib_png(
    data: dict[str, Any],
    style: "CardStyle",
) -> tuple[bytes, int, int] | None:
    """含公式时由 matplotlib 渲染整条 Agent 消息。失败返回 None。"""
    if not matplotlib_available():
        return None
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.offsetbox import AnchoredOffsetbox, HPacker, TextArea, VPacker
    except Exception as e:
        logger.debug("matplotlib import failed: %s", e)
        return None

    title = str(data.get("title") or "Agent 消息")
    subtitle = str(data.get("subtitle") or "")
    body = str(data.get("body") or data.get("text") or "")
    footer = str(data.get("footer") or "")

    width_px = max(400, min(1400, int(style.width or 720)))
    dpi = 140
    width_in = width_px / float(dpi)
    max_chars = max(28, int((width_px - 56) / 15))

    try:
        fp, fp_title, _font_note = _resolve_matplotlib_fonts(style.font_path or None)
    except Exception as e:
        logger.warning("matplotlib 字体探测失败，用默认字体: %s", e)
        from matplotlib.font_manager import FontProperties

        fp = FontProperties()
        fp_title = FontProperties()

    scale = float(style.font_scale or 1.0)
    fs_title = max(13.0, 15.5 * scale)
    fs_sub = max(9.5, 10.5 * scale)
    fs_body = max(10.5, 12.0 * scale)
    fs_math = max(11.0, 12.5 * scale)
    fs_foot = max(9.0, 10.0 * scale)

    fg = style.fg or "#14120f"
    bg = style.bg or "#f7f4ea"
    accent = style.accent or "#0f6b3c"
    muted = style.muted or "#3a362e"

    children: list[Any] = []

    def _text_area(
        txt: str,
        *,
        size: float,
        color: str,
        bold: bool = False,
        mono: bool = False,
        math: bool = False,
    ):
        props: dict[str, Any] = {
            "color": color,
            "fontsize": size,
        }
        if math:
            # mathtext 走默认 math 字体，不要塞 CJK FontProperties
            pass
        elif bold:
            props["fontproperties"] = fp_title
        else:
            props["fontproperties"] = fp
        if mono and not math:
            props["family"] = "monospace"
        return TextArea(txt if txt else " ", textprops=props, multilinebaseline=True)

    def _math_ok(expr: str) -> bool:
        """MathTextParser 校验（兼容带/不带外层 $）。"""
        try:
            from matplotlib import mathtext as _mt

            parser = _mt.MathTextParser("path")
            raw = expr.strip()
            # 部分版本 parse 不要外层 $
            inner = raw
            if inner.startswith("$") and inner.endswith("$") and len(inner) > 2:
                inner = inner[1:-1]
            try:
                parser.parse(inner)
                return True
            except Exception:
                parser.parse(raw)
                return True
        except Exception:
            return False

    def _math_area(latex: str, *, display: bool = False):
        size = fs_math * (1.08 if display else 1.0)
        # 1) mathtext 多级候选
        for expr in _formula_to_mathtext(latex):
            if _math_ok(expr):
                return _text_area(expr, size=size, color=fg, math=True)
        # 2) 可读近似（不要整段反斜杠源码糊脸）
        approx = _latex_approx_plain(latex)
        return _text_area(approx or "?", size=fs_body, color=fg)

    def _plain_one_line(s: str) -> str:
        lines = _md_body_to_plain_lines(s, max_chars=10_000)
        return " ".join(x for x in lines if x).strip()

    def _segs_to_rows(segs: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
        """按换行切成行；行内公式与前后文字同一行，块级公式独占一行。"""
        rows: list[list[tuple[str, str]]] = []
        cur: list[tuple[str, str]] = []

        def flush() -> None:
            nonlocal cur
            if cur:
                rows.append(cur)
                cur = []

        for kind, content in segs:
            if kind == "formula":
                is_display = (
                    ("\n" in content)
                    or content.lstrip().startswith("\\begin")
                    or bool(re.search(r"\\\\|\\qquad|\\quad", content))
                )
                # 块级里 \qquad/\quad 常用来并排两式 → 拆行；丢掉纯标点碎片（如 \quad,\quad 中间的 ","）
                if is_display and re.search(r"\\qquad|\\quad", content):
                    parts = re.split(r"\\qquad|\\quad", content)
                    parts = [p.strip(" \t\n\r,;，；") for p in parts]
                    parts = [p for p in parts if p and not re.fullmatch(r"[,;，；.]+", p)]
                    if len(parts) > 1:
                        flush()
                        for p in parts:
                            rows.append([("formula", p)])
                        continue
                if is_display:
                    flush()
                    rows.append([("formula", content)])
                else:
                    cur.append(("formula", content))
                continue
            parts = str(content or "").split("\n")
            for idx, part in enumerate(parts):
                if idx > 0:
                    flush()
                if part:
                    cur.append(("text", part))
                elif idx < len(parts) - 1:
                    flush()
                    rows.append([])
        flush()
        return rows

    children.append(_text_area(title, size=fs_title, color=fg, bold=True))
    if subtitle.strip():
        children.append(_text_area(subtitle.strip(), size=fs_sub, color=muted))
    children.append(_text_area("━" * min(18, max_chars // 2), size=fs_sub, color=accent))

    raw_segs = extract_formula_segments(body) if text_has_formula(body) else [("text", body, False)]
    if not raw_segs:
        raw_segs = [("text", body, False)]
    segs = [(k, c) for k, c, *_ in (s if len(s) >= 2 else ("text", "", False) for s in raw_segs)]

    for row in _segs_to_rows(segs):
        if not row:
            children.append(_text_area(" ", size=fs_body * 0.4, color=fg))
            continue
        only_text = all(k == "text" for k, _ in row)
        if only_text:
            joined = "".join(c for _, c in row)
            for ln in _md_body_to_plain_lines(joined, max_chars=max_chars):
                children.append(_text_area(ln if ln else " ", size=fs_body, color=fg))
            continue
        # 含公式：同行横排；块级公式单独一格
        if len(row) == 1 and row[0][0] == "formula":
            children.append(_math_area(row[0][1], display=True))
            continue
        row_items: list[Any] = []
        for kind, content in row:
            if kind == "text":
                plain = _plain_one_line(content)
                if plain:
                    row_items.append(_text_area(plain, size=fs_body, color=fg))
            else:
                row_items.append(_math_area(content, display=False))
        if not row_items:
            continue
        if len(row_items) == 1:
            children.append(row_items[0])
        else:
            children.append(
                HPacker(children=row_items, align="center", pad=0, sep=4)
            )

    if footer.strip():
        children.append(_text_area("─" * min(24, max_chars // 2), size=fs_sub, color=muted))
        for ln in _md_body_to_plain_lines(footer, max_chars=max_chars):
            children.append(_text_area(ln, size=fs_foot, color=accent))

    if not children:
        return None

    pack = VPacker(children=children, align="left", pad=10, sep=5)
    # 估算高度：行数 × 行高
    est_lines = max(6, len(children) + 2)
    height_in = max(1.6, min(28.0, 0.22 * est_lines + 0.6))

    fig = None
    try:
        fig = plt.figure(figsize=(width_in, height_in), dpi=dpi, facecolor=bg)
        ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
        ax.set_axis_off()
        ax.set_facecolor(bg)
        anchored = AnchoredOffsetbox(
            loc="upper left",
            child=pack,
            pad=0.6,
            borderpad=0.5,
            frameon=True,
            bbox_to_anchor=(0.0, 1.0),
            bbox_transform=ax.transAxes,
        )
        # 边框色贴近卡片
        anchored.patch.set_boxstyle("round,pad=0.4,rounding_size=0.4")
        anchored.patch.set_facecolor(bg)
        anchored.patch.set_edgecolor(style.border or "#c9c2b0")
        anchored.patch.set_linewidth(1.2)
        ax.add_artist(anchored)

        buf = io.BytesIO()
        fig.savefig(
            buf,
            format="png",
            dpi=dpi,
            facecolor=bg,
            edgecolor="none",
            bbox_inches="tight",
            pad_inches=0.12,
            transparent=False,
        )
        png = buf.getvalue()
        if not png or len(png) < 64:
            return None
        if not _HAS_PILLOW:
            # 无 Pillow 时也能返回，尺寸用估算
            return png, width_px, int(height_in * dpi)
        im = Image.open(io.BytesIO(png)).convert("RGB")
        # 限制最大宽
        if im.width > width_px + 40:
            ratio = width_px / float(im.width)
            nh = max(1, int(im.height * ratio))
            resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
            im = im.resize((width_px, nh), resample)
            out = io.BytesIO()
            im.save(out, format="PNG", optimize=True)
            png = out.getvalue()
            return png, width_px, nh
        return png, im.width, im.height
    except Exception as e:
        logger.warning("render_message_matplotlib_png failed: %s", e)
        return None
    finally:
        if fig is not None:
            plt.close(fig)
        # 清掉校验用残留
        plt.close("all")


def payload_has_formula(data: dict[str, Any] | None) -> bool:
    """payload 各文本字段是否含公式（用于 plain 模式决定是否放弃出卡）。"""
    if not data:
        return False
    chunks: list[str] = []
    for k in ("body", "text", "title", "subtitle", "footer", "detail"):
        v = data.get(k)
        if isinstance(v, str) and v:
            chunks.append(v)
    for row in data.get("rows") or []:
        if not isinstance(row, dict):
            continue
        for k in ("label", "detail", "title", "text"):
            v = row.get(k)
            if isinstance(v, str) and v:
                chunks.append(v)
    return text_has_formula("\n".join(chunks))


def should_render_card(
    *,
    kind: str,
    render_mode: str,
    kinds: list[str],
) -> bool:
    mode = normalize_render_mode(render_mode)
    if mode != "card":
        return False
    if kind not in CARD_KINDS:
        return False
    return kind in (kinds or [])


def render_card(
    kind: str,
    data: dict[str, Any] | None = None,
    style: CardStyle | None = None,
    *,
    formula_mode: str = "off",
) -> RenderResult:
    """Pillow 出卡。无 Pillow / 无字体时 ok=False，调用方回退文本。"""
    t0 = time.perf_counter()
    kind = kind if kind in CARD_KINDS else "session_list"
    data = data or sample_payload(kind)
    style = (style or CardStyle()).resolved()
    fallback = payload_to_fallback_text(kind, data)

    if not _HAS_PILLOW:
        return RenderResult(
            ok=False,
            ms=(time.perf_counter() - t0) * 1000,
            engine="none",
            error="未安装 Pillow。可在 WebUI 勾选安装，或 pip install Pillow",
            kind=kind,
            fallback_text=fallback,
        )

    font_file = font_manager.resolve_font_path(
        mono=style.mono,
        user_path=style.font_path or None,
    )
    try:
        png, w, h, engine = _render_with_pillow(
            kind, data, style, formula_mode=formula_mode
        )
        ms = (time.perf_counter() - t0) * 1000
        return RenderResult(
            ok=True,
            png=png,
            width=w,
            height=h,
            bytes_len=len(png),
            ms=ms,
            engine=engine or "pillow",
            kind=kind,
            fallback_text=fallback,
            font_path=str(font_file or ""),
        )
    except Exception as e:
        logger.warning("card render failed: %s", e)
        return RenderResult(
            ok=False,
            ms=(time.perf_counter() - t0) * 1000,
            engine="pillow",
            error=str(e),
            kind=kind,
            fallback_text=fallback,
            font_path=str(font_file or ""),
        )


def render_meta() -> dict[str, Any]:
    return {
        "render_modes": list(RENDER_MODES),
        "formula_modes": list(FORMULA_MODES),
        "card_kinds": list(CARD_KINDS),
        "default_kinds": list(DEFAULT_KINDS),
        "presets": [
            {
                "id": pid,
                "label": {
                    "terminal_light": "终端浅色",
                    "terminal_dark": "终端深色",
                    "clean": "简洁",
                    "compact": "紧凑（手机）",
                }.get(pid, pid),
                "style": PRESETS[pid],
            }
            for pid in PRESET_IDS
        ],
        "density_options": list(DENSITY_OPTIONS),
        "samples": list(CARD_KINDS),
        "default_css": DEFAULT_CARD_CSS,
        "css_vars": [
            "--card-bg",
            "--card-fg",
            "--card-accent",
            "--card-muted",
            "--card-border",
            "--card-code-bg",
            "--card-radius",
            "--card-pad",
            "--card-width",
            "--card-font-scale",
            "--card-title-size",
            "--card-sub-size",
            "--card-body-size",
            "--card-meta-size",
            "--card-foot-size",
            "--card-badge-h",
            "--card-badge-pad-x",
            "--card-badge-font",
            "--card-badge-dot",
            "--card-idx-w",
            "--card-idx-h",
            "--card-idx-font",
            "--card-idx-radius",
            "--card-idx-top",
            "--card-row-pad-y",
            "--card-row-pad-x",
            "--card-row-gap",
            "--card-section-gap",
            "--card-tool-bg",
            "--card-tool-fg",
            "--card-tool-accent",
            "--card-tool-border",
            "--card-tool-radius",
            "--card-tool-pad-y",
            "--card-tool-pad-x",
            "--card-tool-gap",
            "--card-tool-bar-w",
            "--card-diff-add",
            "--card-diff-del",
            "--card-ask-bg",
        ],
        "engine": engine_status(),
        "formula_subset": {
            "supported": [
                "off：关闭公式渲染",
                "detect：公式用 matplotlib 渲染",
                "formula_only：仅含公式消息渲染为图片（其它消息只发文字）",
                "plain：消息含公式时只发文字",
            ],
            "delimiters": ["$...$", "$$...$$", "\\(...\\)", "\\[...\\]", "\\begin{...}"],
            "note": "detect / formula_only 需 matplotlib；单个公式失败只显示源码，不拖垮整张卡。",
            "matplotlib": matplotlib_available(),
        },
        "formula_mode_options": [
            {"value": "off", "title": "关闭公式渲染"},
            {"value": "detect", "title": "公式用 matplotlib 渲染"},
            {
                "value": "formula_only",
                "title": "仅含公式消息渲染为图片（其他消息只发送文字）",
            },
            {"value": "plain", "title": "消息含公式时只发文字"},
        ],
    }


def config_defaults() -> dict[str, Any]:
    return {
        "render_mode": "text",
        "formula_mode": "off",
        "render_kinds": kinds_to_storage(list(DEFAULT_KINDS)),
        "card_style_preset": "terminal_light",
        "card_width": 720,
        "card_accent": "#0f6b3c",
        "card_bg": "#f7f4ea",
        "card_fg": "#14120f",
        "card_font_scale": 112,
        "card_density": "comfortable",
        "card_show_brand": False,
        "card_mono": False,
        "card_custom_css": "",
        "card_font_path": "",
    }


# ──── HTML 构建 ────


def _font_face_css(style: CardStyle) -> tuple[str, Path | None]:
    """生成 @font-face，返回 (css, font_path)。"""
    sans = font_manager.resolve_font_path(
        mono=False, user_path=style.font_path or None
    )
    mono = font_manager.resolve_font_path(
        mono=True, user_path=style.font_path or None
    )
    if sans is None:
        raise RuntimeError(
            font_manager.ensure_default_fonts().get("error")
            or "无可用中文字体"
        )
    mono = mono or sans

    def face(family: str, path: Path) -> str:
        # file:// 绝对路径，供 Playwright 加载
        uri = path.resolve().as_uri()
        fmt = "truetype"
        if path.suffix.lower() in (".otf",):
            fmt = "opentype"
        if path.suffix.lower() in (".ttc", ".otc"):
            # TTC：Playwright/Chromium 通常可加载
            fmt = "truetype"
        return (
            f"@font-face {{ font-family: '{family}'; "
            f"src: url('{uri}') format('{fmt}'); "
            f"font-weight: 100 900; font-style: normal; }}"
        )

    css = face("HapiCard", sans) + "\n" + face("HapiCardMono", mono)
    return css, sans


def _injected_root_vars(style: CardStyle) -> str:
    return f"""
:root {{
  --card-bg: {style.bg};
  --card-fg: {style.fg};
  --card-accent: {style.accent};
  --card-muted: {style.muted};
  --card-border: {style.border};
  --card-code-bg: {style.code_bg};
  --card-radius: {style.radius}px;
  --card-pad: {style.padding}px;
  --card-width: {style.width}px;
  --card-font-scale: {style.font_scale};
  --card-tool-bg: {style.tool_bg};
  --card-tool-fg: {style.tool_fg};
  --card-tool-accent: {style.tool_accent};
  --card-tool-border: {style.tool_border};
  --card-tool-radius: {style.tool_radius}px;
  --card-tool-pad-y: {style.tool_pad_y}px;
  --card-tool-pad-x: {style.tool_pad_x}px;
  --card-tool-gap: {style.tool_gap}px;
  --card-tool-bar-w: {style.tool_bar_w}px;
  --card-diff-add: {style.diff_add};
  --card-diff-del: {style.diff_del};
  --card-ask-bg: {style.ask_bg};
}}
"""


def build_card_html(
    kind: str,
    data: dict[str, Any],
    style: CardStyle,
    *,
    formula_mode: str = "off",
) -> tuple[str, Path | None]:
    font_css, font_path = _font_face_css(style)
    user_css = (style.custom_css or "").strip() or DEFAULT_CARD_CSS
    # 用户 CSS 在默认之后，可覆盖；再注入当前 token 变量保证滑块/配色生效
    css = (
        font_css
        + "\n"
        + DEFAULT_CARD_CSS
        + "\n"
        + user_css
        + "\n"
        + _injected_root_vars(style)
    )
    mono_cls = " mono" if style.mono else ""
    brand = (
        '<div class="card-brand">hapi connector</div>' if style.show_brand else ""
    )

    if kind == "message":
        body_md = str(data.get("body") or data.get("text") or "")
        body_html = markdown_to_html(body_md)
        title = html.escape(str(data.get("title") or "Agent 消息"))
        sub = html.escape(str(data.get("subtitle") or ""))
        foot = html.escape(str(data.get("footer") or ""))
        inner = f"""
        <div class="card-title">{title}</div>
        {f'<div class="card-sub">{sub}</div>' if sub else ''}
        <div class="card-bar"></div>
        <div class="md">{body_html}</div>
        {f'<div class="card-foot">{foot}</div>' if foot else ''}
        {brand}
        """
    else:
        title = html.escape(str(data.get("title") or kind))
        sub = html.escape(str(data.get("subtitle") or ""))
        foot = html.escape(str(data.get("footer") or ""))
        rows_html = []
        for row in data.get("rows") or []:
            rtype = str(row.get("type") or "row")
            label = html.escape(str(row.get("label") or ""))
            detail = html.escape(str(row.get("detail") or ""))
            if rtype == "section":
                sec = label + (f" ({detail})" if detail else "")
                rows_html.append(
                    f'<div class="row row-section"><span class="row-head">{sec}</span></div>'
                )
                continue
            idx = row.get("index") or 0
            sid = html.escape(str(row.get("sid_short") or ""))
            if idx and sid:
                head = f"[{idx} | {sid}] {label}"
            elif idx:
                head = f"[{idx}] {label}"
            else:
                head = label
            rows_html.append(
                f'<div class="row"><div class="row-head">{head}</div>'
                + (f'<div class="row-detail">{detail}</div>' if detail else "")
                + "</div>"
            )
        inner = f"""
        <div class="card-title">{title}</div>
        {f'<div class="card-sub">{sub}</div>' if sub else ''}
        <div class="card-bar"></div>
        {''.join(rows_html)}
        {f'<div class="card-foot">{foot}</div>' if foot else ''}
        {brand}
        """

    _ = formula_mode
    doc = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/>
<style>
{css}
</style>
</head>
<body>
<div class="card{mono_cls}" id="card">
{inner}
</div>
</body></html>"""
    return doc, font_path


_MD_SPECIAL = re.compile(r"([&<>])")


def _esc(s: str) -> str:
    return html.escape(s, quote=False)



def _is_md_table_sep(line: str) -> bool:
    """GFM 分隔行：| --- | :---: | ---: |"""
    s = (line or "").strip()
    if not s:
        return False
    body = s.strip().strip("|")
    cells = [c.strip() for c in body.split("|")]
    if not cells:
        return False
    return all(bool(re.match(r"^:?-{2,}:?$", c)) for c in cells)


def _split_md_table_row(line: str) -> list[str]:
    """按 | 分列；支持单元格内转义 \\|（GFM 常见写法）。"""
    s = (line or "").rstrip()
    if s.lstrip().startswith("|"):
        s = s.lstrip()[1:]
    if s.rstrip().endswith("|"):
        s = s.rstrip()[:-1]
    cells: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def _try_parse_md_table(lines: list[str], start: int) -> tuple[dict[str, Any], int] | None:
    """从 start 起解析 GFM 表格。成功返回 (block, next_index)。"""
    if start + 1 >= len(lines):
        return None
    head_line = lines[start]
    sep_line = lines[start + 1]
    if "|" not in head_line:
        return None
    if not _is_md_table_sep(sep_line):
        return None
    headers = _split_md_table_row(head_line)
    if not headers or all(not h for h in headers):
        return None
    seps = _split_md_table_row(sep_line)
    aligns: list[str] = []
    for s in seps:
        left = s.startswith(":")
        right = s.endswith(":")
        if left and right:
            aligns.append("center")
        elif right:
            aligns.append("right")
        else:
            aligns.append("left")
    while len(aligns) < len(headers):
        aligns.append("left")
    rows: list[list[str]] = []
    i = start + 2
    while i < len(lines):
        row_line = lines[i]
        if not row_line.strip():
            break
        if "|" not in row_line:
            break
        if re.match(r"^(#{1,3})\s+", row_line) or row_line.strip().startswith("```"):
            break
        cells = _split_md_table_row(row_line)
        if len(cells) < len(headers):
            cells = cells + [""] * (len(headers) - len(cells))
        elif len(cells) > len(headers):
            cells = cells[: len(headers) - 1] + [" | ".join(cells[len(headers) - 1 :])]
        rows.append(cells)
        i += 1
    return (
        {
            "type": "table",
            "headers": headers,
            "rows": rows,
            "aligns": aligns[: len(headers)],
            "text": "",
        },
        i,
    )


def _plain_inline(s: str) -> str:
    """Pillow 用：去掉 ** ` 等标记，保留纯文本（测宽 / 回退）。"""
    plain = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", s or "")
    plain = re.sub(r"\*\*([^*]+)\*\*", r"\1", plain)
    plain = re.sub(r"`([^`]+)`", r"\1", plain)
    plain = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", plain)
    plain = re.sub(r"__([^_]+)__", r"\1", plain)
    plain = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", plain)
    return plain


def markdown_to_html(text: str) -> str:
    """轻量 Markdown → HTML（标题/列表/代码/引用/表格/粗斜体/链接/hr）。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    in_ul = False
    in_ol = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    while i < len(lines):
        line = lines[i]
        # fenced code
        if line.strip().startswith("```"):
            close_lists()
            lang = line.strip()[3:].strip()
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            code = _esc("\n".join(code_lines))
            out.append(
                f'<pre><code class="lang-{_esc(lang)}">{code}</code></pre>'
            )
            continue

        # GFM table
        parsed = _try_parse_md_table(lines, i)
        if parsed is not None:
            close_lists()
            block, ni = parsed
            ths = "".join(f"<th>{_inline(h)}</th>" for h in block["headers"])
            trs = [f"<tr>{ths}</tr>"]
            for row in block["rows"]:
                tds = "".join(f"<td>{_inline(c)}</td>" for c in row)
                trs.append(f"<tr>{tds}</tr>")
            out.append("<table>\n" + "\n".join(trs) + "\n</table>")
            i = ni
            continue

        if not line.strip():
            close_lists()
            i += 1
            continue

        if re.match(r"^---+$|^\*\*\*+$|^___+$", line.strip()):
            close_lists()
            out.append("<hr/>")
            i += 1
            continue

        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            close_lists()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue

        if line.lstrip().startswith(">"):
            close_lists()
            q = re.sub(r"^\s*>\s?", "", line)
            out.append(f"<blockquote><p>{_inline(q)}</p></blockquote>")
            i += 1
            continue

        m = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if m:
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(m.group(1))}</li>")
            i += 1
            continue

        m = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if m:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{_inline(m.group(1))}</li>")
            i += 1
            continue

        close_lists()
        out.append(f"<p>{_inline(line)}</p>")
        i += 1

    close_lists()
    return "\n".join(out)


def _inline(s: str) -> str:
    s = _esc(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


# ──── Playwright 引擎 ────


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    s = h.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"bad color {h}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _load_font(size: int, mono: bool, style: CardStyle, *, bold: bool = False):
    return font_manager.load_image_font(
        size,
        mono=mono,
        user_path=style.font_path or None,
        bold=bold,
        weight=700 if bold else 400,
    )


def _text_size(draw, text: str, font, *, stroke_width: int = 0) -> tuple[int, int]:
    kwargs: dict[str, Any] = {}
    if stroke_width:
        kwargs["stroke_width"] = stroke_width
    try:
        bbox = draw.textbbox((0, 0), text or " ", font=font, **kwargs)
    except TypeError:
        bbox = draw.textbbox((0, 0), text or " ", font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_text(
    draw,
    xy: tuple[float, float],
    text: str,
    font,
    fill,
    *,
    bold_stroke: bool = False,
) -> None:
    """绘制文字；bold_stroke 时用 1px stroke 合成加粗（无粗体字文件时的兜底）。"""
    if not text:
        return
    if bold_stroke:
        try:
            draw.text(
                xy,
                text,
                font=font,
                fill=fill,
                stroke_width=1,
                stroke_fill=fill,
            )
            return
        except TypeError:
            pass
    draw.text(xy, text, font=font, fill=fill)


# 行内：图片 / 链接 / `code` / **bold** / __bold__ / *em*（不含嵌套）
# 图片须在链接前匹配，避免 ![alt](url) 被吃成 link
_RE_INLINE_RUNS = re.compile(
    r"(!\[([^\]]*)\]\(([^)]+)\))"
    r"|(\[([^\]]+)\]\(([^)]+)\))"
    r"|(`[^`]+`)"
    r"|(\*\*[^*]+\*\*)"
    r"|(__[^_]+__)"
    r"|(?<!\*)(\*[^*]+\*)(?!\*)"
)


def parse_inline_runs(text: str) -> list[dict[str, str]]:
    """Markdown 行内 → [{type, text, ...}]，去掉标记符。

    type: text|bold|code|em|link|image_ref
    image_ref 另带 src；真正位图由 _parse_md_blocks 的 image 块处理。
    """
    s = text or ""
    if not s:
        return []
    runs: list[dict[str, str]] = []
    pos = 0
    for m in _RE_INLINE_RUNS.finditer(s):
        if m.start() > pos:
            runs.append({"type": "text", "text": s[pos : m.start()]})
        raw = m.group(0)
        if raw.startswith("!["):
            alt = m.group(2) or ""
            src = (m.group(3) or "").strip()
            runs.append({"type": "image_ref", "text": alt or "image", "src": src})
        elif raw.startswith("[") and "](" in raw:
            label = m.group(5) or ""
            # 链接只显示可读文字，不画 URL（卡片宽度有限）
            runs.append({"type": "link", "text": label})
        elif raw.startswith("`"):
            runs.append({"type": "code", "text": raw[1:-1]})
        elif raw.startswith("**") or raw.startswith("__"):
            runs.append({"type": "bold", "text": raw[2:-2]})
        elif raw.startswith("*"):
            runs.append({"type": "em", "text": raw[1:-1]})
        else:
            runs.append({"type": "text", "text": raw})
        pos = m.end()
    if pos < len(s):
        runs.append({"type": "text", "text": s[pos:]})
    return runs or [{"type": "text", "text": s}]


def _font_for_run(run_type: str, font_body, font_bold, font_code):
    if run_type == "code":
        return font_code or font_body
    if run_type in ("bold", "link"):
        return font_bold or font_body
    return font_body


def _run_needs_stroke(run_type: str, font_body, font_bold) -> bool:
    """粗体 run 若字体未真正加粗（无字重轴/无 Bold 文件），用 stroke 合成。"""
    if run_type != "bold":
        return False
    if font_bold is None:
        return True
    # font_manager 打的标记：可变字重或 Bold 兄弟文件成功时为 True
    effective = getattr(font_bold, "hapi_bold_effective", None)
    if effective is False:
        return True
    if effective is True:
        return False
    # 兼容旧路径：同一对象则 stroke
    return font_bold is font_body


def _wrap_inline_runs(
    draw,
    runs: list[dict[str, str]],
    *,
    font_body,
    font_bold,
    font_code,
    max_width: int,
) -> list[list[dict[str, Any]]]:
    """把行内 runs 折成多行；每行是 [{type, text, font, stroke}]。"""
    if max_width <= 0:
        max_width = 1
    lines: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    cur_w = 0

    def flush() -> None:
        nonlocal cur, cur_w
        if cur:
            lines.append(cur)
        cur = []
        cur_w = 0

    def append_piece(rtype: str, piece: str) -> None:
        nonlocal cur_w
        if not piece:
            return
        font = _font_for_run(rtype, font_body, font_bold, font_code)
        stroke = _run_needs_stroke(rtype, font_body, font_bold)
        sw = 1 if stroke else 0
        w, _ = _text_size(draw, piece, font, stroke_width=sw)
        # 空行首不硬折单字符
        if cur and cur_w + w > max_width and piece.strip():
            flush()
            w, _ = _text_size(draw, piece, font, stroke_width=sw)
        cur.append({"type": rtype, "text": piece, "font": font, "stroke": stroke})
        cur_w += w

    for run in runs or []:
        rtype = str(run.get("type") or "text")
        text = str(run.get("text") or "")
        if not text:
            continue
        font = _font_for_run(rtype, font_body, font_bold, font_code)
        stroke = _run_needs_stroke(rtype, font_body, font_bold)
        sw = 1 if stroke else 0
        # 代码尽量整段；当前行放不下则换行；仍超宽再按字切
        if rtype == "code":
            w, _ = _text_size(draw, text, font, stroke_width=sw)
            if w <= max(1, max_width - cur_w):
                append_piece(rtype, text)
                continue
            if cur:
                flush()
            w2, _ = _text_size(draw, text, font, stroke_width=sw)
            if w2 <= max_width:
                append_piece(rtype, text)
                continue
            # 单段代码宽于整行：落入下方按字折
        # 按字符前进（CJK 友好）
        buf = ""
        for ch in text:
            trial = buf + ch
            tw, _ = _text_size(draw, trial, font, stroke_width=sw)
            if cur_w + tw <= max_width or not buf:
                buf = trial
            else:
                append_piece(rtype, buf)
                flush()
                buf = ch
        if buf:
            append_piece(rtype, buf)
    flush()
    return lines or [[]]


def _measure_inline_runs(
    draw,
    runs: list[dict[str, str]],
    *,
    font_body,
    font_bold,
    font_code,
    max_width: int,
    line_extra: int = 4,
) -> int:
    lines = _wrap_inline_runs(
        draw,
        runs,
        font_body=font_body,
        font_bold=font_bold,
        font_code=font_code,
        max_width=max_width,
    )
    h = 0
    for line in lines:
        lh = 0
        for piece in line:
            sw = 1 if piece.get("stroke") else 0
            _, ph = _text_size(draw, piece.get("text") or " ", piece["font"], stroke_width=sw)
            lh = max(lh, ph)
        h += max(lh, _text_size(draw, "测", font_body)[1]) + line_extra
    return h


def _draw_inline_runs(
    draw,
    x: int,
    y: int,
    runs: list[dict[str, str]],
    *,
    font_body,
    font_bold,
    font_code,
    fill,
    code_bg: tuple[int, int, int] | None = None,
    max_width: int,
    line_extra: int = 4,
    accent: tuple[int, int, int] | None = None,
) -> int:
    """绘制行内 runs，返回消耗的高度。"""
    lines = _wrap_inline_runs(
        draw,
        runs,
        font_body=font_body,
        font_bold=font_bold,
        font_code=font_code,
        max_width=max_width,
    )
    yy = y
    for line in lines:
        lh = 0
        pieces_m: list[tuple[dict[str, Any], int, int]] = []
        for piece in line:
            sw = 1 if piece.get("stroke") else 0
            pw, ph = _text_size(
                draw, piece.get("text") or " ", piece["font"], stroke_width=sw
            )
            pieces_m.append((piece, pw, ph))
            lh = max(lh, ph)
        if lh <= 0:
            lh = _text_size(draw, "测", font_body)[1]
        xx = x
        for piece, pw, ph in pieces_m:
            ty = yy + max(0, (lh - ph) // 2)
            txt = piece.get("text") or ""
            rtype = piece.get("type") or "text"
            if rtype == "code" and code_bg is not None and txt:
                pad_x, pad_y = 3, 1
                draw.rounded_rectangle(
                    (xx - pad_x, ty - pad_y, xx + pw + pad_x, ty + ph + pad_y),
                    radius=3,
                    fill=code_bg,
                )
            col = fill
            if rtype == "link" and accent is not None:
                col = accent
            _draw_text(
                draw,
                (xx, ty),
                txt,
                piece["font"],
                col,
                bold_stroke=bool(piece.get("stroke")),
            )
            # 链接轻下划线
            if rtype == "link" and txt and accent is not None:
                try:
                    draw.line(
                        (xx, ty + ph - 1, xx + max(1, pw), ty + ph - 1),
                        fill=accent,
                        width=1,
                    )
                except Exception:
                    pass
            xx += pw
        yy += lh + line_extra
    return max(0, yy - y)


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            trial = cur + ch
            w, _ = _text_size(draw, trial, font)
            if w <= max_width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines or [""]


def _draw_rounded_rect(draw, xy, radius: int, fill, outline=None, width: int = 1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _render_with_pillow(
    kind: str,
    data: dict[str, Any],
    style: CardStyle,
    *,
    formula_mode: str = "off",
) -> tuple[bytes, int, int, str]:
    """返回 (png, w, h, engine)。message+公式：pillow（公式段 matplotlib 嵌图）。"""
    if kind == "message":
        fmode = normalize_formula_mode(formula_mode)
        body = str((data or {}).get("body") or (data or {}).get("text") or "")
        want_math = fmode in ("detect", "formula_only") and text_has_formula(body)
        use_math = want_math and matplotlib_available() and _HAS_PILLOW
        if want_math and not matplotlib_available():
            logger.warning(
                "formula_mode=%s 但未安装 matplotlib，公式当普通字", fmode
            )
        png, w, h = _draw_message_png(
            data, style, formula_mode="detect" if use_math else "off"
        )
        return png, w, h, "pillow+math" if use_math else "pillow"
    if kind == "session_list" or data.get("layout") == "session_list":
        png, w, h = _draw_session_list_png(data, style)
        return png, w, h, "pillow"
    if kind == "status" or data.get("layout") == "status":
        png, w, h = _draw_status_png(data, style)
        return png, w, h, "pillow"
    png, w, h = _draw_struct_png(kind, data, style, formula_mode=formula_mode)
    return png, w, h, "pillow"


def _mix_rgb(
    a: tuple[int, int, int], b: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _status_color(
    status_key: str,
    accent: tuple[int, int, int],
    muted: tuple[int, int, int],
    fg: tuple[int, int, int],
) -> tuple[int, int, int]:
    sk = (status_key or "").lower()
    if sk in ("thinking", "think"):
        return accent
    if sk in ("active", "running", "run"):
        return (46, 125, 72)  # green-ish, readable on light/dark-ish
    if sk in ("closed", "idle", "inactive"):
        return muted
    return fg


def _draw_status_png(
    data: dict[str, Any],
    style: CardStyle,
) -> tuple[bytes, int, int]:
    """单 session 状态卡：大标题 + 状态徽章 + 键值网格。布局可走 CSS 变量。"""
    scale = style.font_scale
    pad = style.padding
    width = style.width
    content_w = width - pad * 2

    title_size = max(14, int(style.title_size * scale))
    sub_size = max(11, int(style.sub_size * scale))
    label_size = max(11, int(style.meta_size * scale))
    value_size = max(12, int(style.body_size * scale))
    badge_size = max(12, int(style.badge_font * scale))
    foot_size = max(10, int(style.foot_size * scale))

    tmp = Image.new("RGB", (width, 100), _hex_to_rgb(style.bg))
    d0 = ImageDraw.Draw(tmp)
    font_title = _load_font(title_size, False, style)
    font_sub = _load_font(sub_size, False, style)
    font_label = _load_font(label_size, False, style)
    font_value = _load_font(value_size, False, style)
    font_badge = _load_font(badge_size, False, style)
    font_foot = _load_font(foot_size, False, style)

    title = str(data.get("title") or "Session 状态")
    subtitle = str(data.get("subtitle") or "")
    footer = str(data.get("footer") or "")
    rows = list(data.get("rows") or [])
    status = str(data.get("status") or "")
    status_key = str(data.get("status_key") or "")

    bg = _hex_to_rgb(style.bg)
    fg = _hex_to_rgb(style.fg)
    accent = _hex_to_rgb(style.accent)
    muted = _hex_to_rgb(style.muted)
    sub_fg = _mix_rgb(muted, fg, 0.35)
    border = _hex_to_rgb(style.border)
    row_bg = _mix_rgb(bg, fg, 0.05)
    sc = _status_color(status_key, accent, muted, fg)
    badge_bg = _mix_rgb(bg, sc, 0.18)

    label_w = max(72, int(88 * scale))
    row_h_base = max(32, int(style.row_pad_y * 2 + value_size + 8))
    row_gap = style.row_gap
    badge_h = max(20, int(style.badge_h))
    badge_pad_x = max(8, int(style.badge_pad_x))
    badge_dot = max(2, int(style.badge_dot))

    # 预估高度
    y = pad
    y += _text_size(d0, title, font_title)[1] + 8
    if subtitle:
        for _ in _wrap_text(d0, subtitle, font_sub, content_w):
            y += _text_size(d0, "测", font_sub)[1] + 3
        y += 6
    if status:
        y += badge_h + 14
    y += 8
    for row in rows:
        detail = str(row.get("detail") or "")
        lines = _wrap_text(d0, detail, font_value, content_w - label_w - 24) or [""]
        h = max(
            row_h_base,
            16 + sum(_text_size(d0, ln or " ", font_value)[1] + 3 for ln in lines),
        )
        y += h + row_gap
    if footer:
        y += 24 + _text_size(d0, "测", font_foot)[1] * 2
    if style.show_brand:
        y += 14 + _text_size(d0, "hapi", font_foot)[1]
    y += pad
    height = max(int(y), 200)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    _draw_rounded_rect(
        draw,
        (1, 1, width - 2, height - 2),
        radius=style.radius,
        fill=bg,
        outline=border,
        width=2,
    )

    y = pad
    draw.text((pad, y), title, font=font_title, fill=fg)
    y += _text_size(draw, title, font_title)[1] + 8
    if subtitle:
        for line in _wrap_text(draw, subtitle, font_sub, content_w):
            draw.text((pad, y), line, font=font_sub, fill=sub_fg)
            y += _text_size(draw, line or " ", font_sub)[1] + 3
        y += 6

    if status:
        bw, bh = _text_size(draw, status, font_badge)
        badge_w = max(bw + badge_pad_x * 2 + badge_dot * 2 + 10, int(100 * scale))
        _draw_rounded_rect(
            draw,
            (pad, y, pad + badge_w, y + badge_h),
            radius=badge_h // 2,
            fill=badge_bg,
            outline=sc,
            width=2,
        )
        cr = badge_dot
        cy = y + badge_h // 2
        draw.ellipse((pad + badge_pad_x - 6, cy - cr, pad + badge_pad_x - 6 + cr * 2, cy + cr), fill=sc)
        draw.text(
            (pad + badge_pad_x - 6 + cr * 2 + 8, y + (badge_h - bh) // 2),
            status,
            font=font_badge,
            fill=sc,
        )
        y += badge_h + 14
    else:
        draw.rectangle((pad, y, pad + min(140, content_w // 3), y + 4), fill=accent)
        y += 14

    for row in rows:
        label = str(row.get("label") or "")
        detail = str(row.get("detail") or "")
        val_lines = _wrap_text(draw, detail, font_value, content_w - label_w - 24) or [""]
        h = max(
            row_h_base,
            16 + sum(_text_size(draw, ln or " ", font_value)[1] + 3 for ln in val_lines),
        )
        _draw_rounded_rect(
            draw,
            (pad, y, width - pad, y + h),
            radius=8,
            fill=row_bg,
            outline=None,
        )
        # 左标签
        lw, lh = _text_size(draw, label, font_label)
        draw.text(
            (pad + 14, y + (h - lh) // 2),
            label,
            font=font_label,
            fill=sub_fg,
        )
        # 右值（可多行）
        vx = pad + label_w + 8
        vy = y + 8
        for line in val_lines:
            draw.text((vx, vy), line, font=font_value, fill=fg)
            vy += _text_size(draw, line or " ", font_value)[1] + 3
        y += h + row_gap

    if footer:
        y += 4
        draw.line((pad, y, width - pad, y), fill=border, width=1)
        y += 12
        for line in _wrap_text(draw, footer, font_foot, content_w):
            draw.text((pad, y), line, font=font_foot, fill=accent)
            y += _text_size(draw, line or " ", font_foot)[1] + 2

    if style.show_brand:
        brand = "hapi connector"
        bw, bh = _text_size(draw, brand, font_foot)
        draw.text(
            (width - pad - bw, height - pad - bh), brand, font=font_foot, fill=sub_fg
        )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), width, height


def _draw_session_list_png(
    data: dict[str, Any],
    style: CardStyle,
) -> tuple[bytes, int, int]:
    """会话列表专用版式：分组头 + 卡片行 + 状态色点 + 当前高亮。布局可走 CSS 变量。"""
    scale = style.font_scale
    pad = style.padding
    width = style.width
    content_w = width - pad * 2

    title_size = max(14, int(style.title_size * scale))
    sub_size = max(11, int(style.sub_size * scale))
    body_size = max(12, int(style.body_size * scale))
    meta_size = max(10, int(style.meta_size * scale))
    foot_size = max(10, int(style.foot_size * scale))
    idx_size = max(11, int(style.idx_font * scale))

    row_pad_y = max(4, int(style.row_pad_y))
    row_pad_x = max(4, int(style.row_pad_x))
    section_gap = max(0, int(style.section_gap))
    row_gap = max(0, int(style.row_gap))
    idx_box_w = max(24, int(style.idx_w))
    idx_box_h_cfg = max(0, int(style.idx_h))
    idx_radius = max(0, int(style.idx_radius))
    idx_top = max(0, int(style.idx_top))

    tmp = Image.new("RGB", (width, 100), _hex_to_rgb(style.bg))
    d0 = ImageDraw.Draw(tmp)
    font_title = _load_font(title_size, False, style)
    font_sub = _load_font(sub_size, False, style)
    font_body = _load_font(body_size, False, style)
    font_meta = _load_font(meta_size, False, style)
    font_foot = _load_font(foot_size, False, style)
    font_idx = _load_font(idx_size, True, style)

    title = str(data.get("title") or "Session 列表")
    subtitle = str(data.get("subtitle") or "")
    rows = list(data.get("rows") or [])
    footer = str(data.get("footer") or "")

    bg = _hex_to_rgb(style.bg)
    fg = _hex_to_rgb(style.fg)
    accent = _hex_to_rgb(style.accent)
    muted = _hex_to_rgb(style.muted)
    sub_fg = _mix_rgb(muted, fg, 0.35)
    border = _hex_to_rgb(style.border)
    # 行底：略深/浅于背景，保证对比
    row_bg = _mix_rgb(bg, fg, 0.05)
    row_bg_cur = _mix_rgb(bg, accent, 0.14)
    section_bg = _mix_rgb(bg, accent, 0.08)

    # 与绘制共用度量，避免预估偏矮导致底部被裁
    sec_h = max(28, int(30 * scale))
    title_max_w = content_w - idx_box_w - row_pad_x * 2 - 10

    def _meta_line_for(row: dict) -> str:
        status = str(row.get("status") or "")
        if not status and row.get("detail"):
            status = str(row.get("detail"))
        flavor = str(row.get("flavor") or "")
        model = str(row.get("model") or "")
        pending = int(row.get("pending") or 0)
        is_current = bool(row.get("is_current"))
        bits = []
        if status:
            bits.append(status)
        if flavor or model:
            bits.append(f"{flavor}:{model}" if flavor else model)
        if pending:
            bits.append(f"待审 {pending}")
        if is_current:
            bits.append("当前")
        if not bits and row.get("detail"):
            bits.append(str(row.get("detail")))
        return "  ·  ".join(bits)

    def _session_row_h(label: str, meta_line: str) -> int:
        title_lines = _wrap_text(d0, label, font_body, title_max_w) or [""]
        meta_h_local = _text_size(d0, meta_line or "测", font_meta)[1]
        title_h = sum(_text_size(d0, ln or " ", font_body)[1] + 3 for ln in title_lines)
        return row_pad_y * 2 + title_h + 6 + meta_h_local

    # 预估高度（与绘制同一套公式 + 底部安全边距）
    y = pad
    y += _text_size(d0, title, font_title)[1] + 6
    if subtitle:
        for _ in _wrap_text(d0, subtitle, font_sub, content_w):
            y += _text_size(d0, "测", font_sub)[1] + 2
        y += 6
    y += 14  # accent bar + gap
    for row in rows:
        rtype = str(row.get("type") or "row")
        if rtype == "section":
            y += max(4, section_gap // 2) + sec_h + 8
            continue
        label = str(row.get("label") or "")
        y += _session_row_h(label, _meta_line_for(row)) + row_gap
    if footer:
        y += 8 + 12 + _text_size(d0, "测", font_foot)[1] * 3
    y += pad + 32
    height = max(int(y), 140)
    height = min(height, 12000)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    y = pad
    draw.text((pad, y), title, font=font_title, fill=fg)
    y += _text_size(draw, title, font_title)[1] + 6
    if subtitle:
        for line in _wrap_text(draw, subtitle, font_sub, content_w):
            draw.text((pad, y), line, font=font_sub, fill=sub_fg)
            y += _text_size(draw, line or " ", font_sub)[1] + 2
        y += 6
    draw.rectangle((pad, y, pad + min(140, content_w // 3), y + 4), fill=accent)
    y += 14

    for row in rows:
        rtype = str(row.get("type") or "row")
        if rtype == "section":
            y += max(4, section_gap // 2)
            label = str(row.get("label") or "")
            count = row.get("count")
            if count is None:
                detail = str(row.get("detail") or "").strip()
                count_txt = detail if detail else ""
            else:
                count_txt = f"{count}"
            _draw_rounded_rect(
                draw,
                (pad, y, width - pad, y + sec_h),
                radius=6,
                fill=section_bg,
                outline=None,
            )
            draw.rectangle((pad, y + 4, pad + 4, y + sec_h - 4), fill=accent)
            tx = pad + 14
            ty = y + (sec_h - _text_size(draw, "测", font_meta)[1]) // 2
            for line in _wrap_text(draw, label, font_meta, content_w - 90)[:1]:
                draw.text((tx, ty), line, font=font_meta, fill=accent)
            if count_txt:
                badge = f"{count_txt} 个" if not str(count_txt).endswith("个") else str(count_txt)
                bw, bh = _text_size(draw, badge, font_meta)
                bx = width - pad - bw - 12
                by = y + (sec_h - bh) // 2
                draw.text((bx, by), badge, font=font_meta, fill=sub_fg)
            y += sec_h + 8
            continue

        label = str(row.get("label") or "")
        idx = row.get("index") or 0
        sid = str(row.get("sid_short") or "")
        status_key = str(row.get("status_key") or "")
        is_current = bool(row.get("is_current"))
        meta_line = _meta_line_for(row)

        title_lines = _wrap_text(draw, label, font_body, title_max_w) or [""]
        meta_h = _text_size(draw, meta_line or "测", font_meta)[1]
        title_h = sum(_text_size(draw, ln or " ", font_body)[1] + 3 for ln in title_lines)
        row_h = row_pad_y * 2 + title_h + 6 + meta_h

        fill = row_bg_cur if is_current else row_bg
        outline = accent if is_current else border
        _draw_rounded_rect(
            draw,
            (pad, y, width - pad, y + row_h),
            radius=8,
            fill=fill,
            outline=outline,
            width=2 if is_current else 1,
        )
        if is_current:
            draw.rectangle((pad + 2, y + 6, pad + 6, y + row_h - 6), fill=accent)

        idx_txt = str(idx) if idx else "-"
        try:
            bbox = draw.textbbox((0, 0), idx_txt, font=font_idx)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            toff_x, toff_y = bbox[0], bbox[1]
        except Exception:
            tw, th = _text_size(draw, idx_txt, font_idx)
            toff_x, toff_y = 0, 0
        idx_box_h = idx_box_h_cfg if idx_box_h_cfg > 0 else max(th + 14, idx_box_w - 6)
        idx_box_top = y + idx_top
        idx_box_left = pad + row_pad_x
        _draw_rounded_rect(
            draw,
            (
                idx_box_left,
                idx_box_top,
                idx_box_left + idx_box_w,
                idx_box_top + idx_box_h,
            ),
            radius=idx_radius,
            fill=_mix_rgb(fill, accent, 0.22 if is_current else 0.12),
            outline=None,
        )
        ix = idx_box_left + (idx_box_w - tw) // 2 - toff_x
        iy = idx_box_top + (idx_box_h - th) // 2 - toff_y
        draw.text(
            (ix, iy),
            idx_txt,
            font=font_idx,
            fill=accent if is_current else sub_fg,
        )

        tx = pad + row_pad_x + idx_box_w + 12
        ty = y + row_pad_y
        for line in title_lines:
            draw.text((tx, ty), line, font=font_body, fill=fg)
            ty += _text_size(draw, line or " ", font_body)[1] + 3

        sc = _status_color(status_key, accent, muted, fg)
        my = ty + 3
        dot_r = 4
        try:
            mb = draw.textbbox((0, 0), meta_line or "测", font=font_meta)
            m_th = mb[3] - mb[1]
            m_toff = mb[1]
        except Exception:
            m_th, m_toff = meta_h, 0
        cy = my - m_toff + m_th // 2 + 10
        draw.ellipse((tx, cy - dot_r, tx + dot_r * 2, cy + dot_r), fill=sc)
        draw.text((tx + 14, my), meta_line, font=font_meta, fill=sub_fg)

        if sid:
            sw, sh = _text_size(draw, sid, font_meta)
            draw.text(
                (width - pad - row_pad_x - sw, y + row_pad_y),
                sid,
                font=font_meta,
                fill=sub_fg,
            )

        y += row_h + row_gap

    if footer:
        y += 8
        draw.line((pad, y, width - pad, y), fill=border, width=1)
        y += 12
        for line in _wrap_text(draw, footer, font_foot, content_w):
            draw.text((pad, y), line, font=font_foot, fill=accent)
            y += _text_size(draw, line or " ", font_foot)[1] + 2

    # 按实际内容高度裁剪多余空白；若内容超出预估则扩展
    content_bottom = int(y + pad)
    if content_bottom > height:
        bigger = Image.new("RGB", (width, content_bottom), bg)
        bigger.paste(img, (0, 0))
        img = bigger
        height = content_bottom
    elif content_bottom < height:
        height = max(content_bottom, 140)
        img = img.crop((0, 0, width, height))

    draw = ImageDraw.Draw(img)
    _draw_rounded_rect(
        draw,
        (1, 1, width - 2, height - 2),
        radius=style.radius,
        fill=None,
        outline=border,
        width=2,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), width, height


def _draw_struct_png(
    kind: str,
    data: dict[str, Any],
    style: CardStyle,
    *,
    formula_mode: str = "off",
) -> tuple[bytes, int, int]:
    """结构卡（pending / permission / routes 等）。

    高度预估与绘制共用同一套公式，并按实际内容扩展/裁剪（同 session_list），
    避免 footer 多行或长详情时底部被截断。
    """
    scale = style.font_scale
    dense = style.density == "compact"
    pad = style.padding
    width = style.width
    content_w = width - pad * 2

    title_size = max(14, int(22 * scale))
    sub_size = max(11, int(13 * scale))
    body_size = max(11, int(14 * scale))
    foot_size = max(10, int(12 * scale))
    line_gap = 6 if dense else 10
    row_gap = 10 if dense else 14  # 行间距略放宽，避免块之间挤
    row_pad_y = 8 if dense else 10  # 行内上下内边距（与绘制一致）
    foot_gap_before = 12  # 内容与 footer 分隔线间距
    foot_gap_after = 12  # 分隔线到 footer 文案

    tmp = Image.new("RGB", (width, 100), _hex_to_rgb(style.bg))
    d0 = ImageDraw.Draw(tmp)
    font_title = _load_font(title_size, style.mono, style)
    font_sub = _load_font(sub_size, style.mono, style)
    font_body = _load_font(body_size, style.mono, style)
    font_foot = _load_font(foot_size, style.mono, style)

    title = str(data.get("title") or kind)
    subtitle = str(data.get("subtitle") or "")
    rows = list(data.get("rows") or [])
    footer = str(data.get("footer") or "")

    def _row_head(row: dict) -> str:
        rtype = str(row.get("type") or "row")
        label = str(row.get("label") or "")
        detail = str(row.get("detail") or "")
        if rtype == "section":
            if detail:
                return (
                    f"{label}  ·  {detail}"
                    if not str(detail).startswith("(")
                    else f"{label} {detail}"
                )
            return label
        idx = row.get("index") or 0
        if idx:
            return f"{idx}.  {label}"
        return label

    def _block_h(row: dict) -> int:
        """普通行底条高度（与绘制 block_h 一致）。"""
        head = _row_head(row)
        detail = str(row.get("detail") or "")
        head_lines = _wrap_text(d0, head, font_body, content_w - 16) or [""]
        detail_lines = (
            _wrap_text(d0, detail, font_sub, content_w - 20) if detail else []
        )
        return (
            row_pad_y * 2
            + sum(_text_size(d0, ln or " ", font_body)[1] + 2 for ln in head_lines)
            + sum(_text_size(d0, ln or " ", font_sub)[1] + 2 for ln in detail_lines)
        )

    # 预估高度 = 与绘制同一套推进 + 底部安全边距
    y = pad
    y += _text_size(d0, title, font_title)[1] + 6
    if subtitle:
        for _ in _wrap_text(d0, subtitle, font_sub, content_w):
            y += _text_size(d0, "测", font_sub)[1] + 2
        y += 8
    y += 3 + line_gap  # accent bar
    for row in rows:
        rtype = str(row.get("type") or "row")
        if rtype == "section":
            head = _row_head(row)
            for _ in _wrap_text(d0, head, font_sub, content_w):
                y += _text_size(d0, "测", font_sub)[1] + 2
            y += row_gap // 2
            continue
        y += _block_h(row) + row_gap
    if footer:
        y += foot_gap_before + 1 + foot_gap_after
        for _ in _wrap_text(d0, footer, font_foot, content_w):
            y += _text_size(d0, "测", font_foot)[1] + 3
        y += 6
    if style.show_brand:
        y += 12 + _text_size(d0, "hapi connector", font_foot)[1]
    y += pad + 24  # 底部安全边距（同 session_list 思路）
    height = max(int(y), 140)
    height = min(height, 12000)

    bg = _hex_to_rgb(style.bg)
    fg = _hex_to_rgb(style.fg)
    accent = _hex_to_rgb(style.accent)
    muted = _hex_to_rgb(style.muted)
    border = _hex_to_rgb(style.border)
    row_bg = _mix_rgb(bg, fg, 0.04)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    y = pad
    draw.text((pad, y), title, font=font_title, fill=fg)
    y += _text_size(draw, title, font_title)[1] + 6
    if subtitle:
        for line in _wrap_text(draw, subtitle, font_sub, content_w):
            draw.text((pad, y), line, font=font_sub, fill=muted)
            y += _text_size(draw, line or " ", font_sub)[1] + 2
        y += 8
    draw.rectangle((pad, y, pad + min(120, content_w // 3), y + 3), fill=accent)
    y += 3 + line_gap

    for row in rows:
        rtype = str(row.get("type") or "row")
        head = _row_head(row)
        detail = str(row.get("detail") or "")
        if rtype == "section":
            for line in _wrap_text(draw, head, font_sub, content_w):
                draw.text((pad, y), line, font=font_sub, fill=accent)
                y += _text_size(draw, line or " ", font_sub)[1] + 2
            y += row_gap // 2
            continue
        head_lines = _wrap_text(draw, head, font_body, content_w - 16) or [""]
        detail_lines = (
            _wrap_text(draw, detail, font_sub, content_w - 20) if detail else []
        )
        block_h = (
            row_pad_y * 2
            + sum(_text_size(draw, ln or " ", font_body)[1] + 2 for ln in head_lines)
            + sum(_text_size(draw, ln or " ", font_sub)[1] + 2 for ln in detail_lines)
        )
        _draw_rounded_rect(
            draw,
            (pad, y, width - pad, y + block_h),
            radius=6,
            fill=row_bg,
            outline=None,
        )
        yy = y + row_pad_y
        for line in head_lines:
            draw.text((pad + 10, yy), line, font=font_body, fill=fg)
            yy += _text_size(draw, line or " ", font_body)[1] + 2
        for line in detail_lines:
            draw.text((pad + 14, yy), line, font=font_sub, fill=muted)
            yy += _text_size(draw, line or " ", font_sub)[1] + 2
        y += block_h + row_gap

    if footer:
        y += foot_gap_before
        draw.line((pad, y, width - pad, y), fill=border, width=1)
        y += foot_gap_after
        for line in _wrap_text(draw, footer, font_foot, content_w):
            draw.text((pad, y), line, font=font_foot, fill=accent)
            y += _text_size(draw, line or " ", font_foot)[1] + 3

    brand_h = 0
    if style.show_brand:
        brand = "hapi connector"
        bw, bh = _text_size(draw, brand, font_foot)
        brand_h = bh + 4

    # 按实际内容高度扩展/裁剪（与 session_list 一致）
    content_bottom = int(y + pad + brand_h + 8)
    if content_bottom > height:
        bigger = Image.new("RGB", (width, content_bottom), bg)
        bigger.paste(img, (0, 0))
        img = bigger
        height = content_bottom
        draw = ImageDraw.Draw(img)
    elif content_bottom < height:
        height = max(content_bottom, 140)
        img = img.crop((0, 0, width, height))
        draw = ImageDraw.Draw(img)

    if style.show_brand:
        brand = "hapi connector"
        bw, bh = _text_size(draw, brand, font_foot)
        draw.text(
            (width - pad - bw, height - pad - bh),
            brand,
            font=font_foot,
            fill=muted,
        )

    # 外框最后画，避免扩展后丢失
    _draw_rounded_rect(
        draw,
        (1, 1, width - 2, height - 2),
        radius=style.radius,
        fill=None,
        outline=border,
        width=2,
    )

    _ = formula_mode
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), width, height


def _parse_message_blocks_with_math(
    text: str,
    *,
    style: CardStyle,
    max_formula_w: int,
    formula_fontsize: float,
    formula_fontsize_display: float | None = None,
) -> list[dict[str, Any]]:
    """正文：行内公式嵌进同一行，块级公式单独居中。

    单个公式失败只退回源码文本，不拖垮整条消息。
    """
    segs = extract_formula_segments(text)
    if not segs or all(k == "text" for k, _c, _d in segs):
        return _parse_md_blocks(text, image_max_width=max_formula_w)

    fs_inline = formula_fontsize
    fs_disp = formula_fontsize_display or (formula_fontsize * 1.12)
    fg = style.fg or "#14120f"
    bg = style.bg or "#f7f4ea"

    def _render_one(latex: str, *, display: bool):
        return render_math_to_pil(
            latex,
            fontsize=fs_disp if display else fs_inline,
            dpi=200,
            fg=fg,
            bg=bg,
            max_width_px=max(80, max_formula_w - (8 if display else 48)),
        )

    out: list[dict[str, Any]] = []
    line_parts: list[dict[str, Any]] = []

    def flush_line() -> None:
        """冲刷当前行内缓冲。

        全是 text 时把多行拼回完整 Markdown 再交给 _parse_md_blocks，
        这样 GFM 表格（表头+分隔行+数据行）不会被逐行拆碎。
        混有行内公式图时走 rich_line。
        """
        nonlocal line_parts
        if not line_parts:
            return
        if all(p.get("type") == "text" for p in line_parts):
            joined = "".join(p.get("text") or "" for p in line_parts)
            if joined.strip():
                out.extend(_parse_md_blocks(joined, image_max_width=max_formula_w))
            line_parts = []
            return
        # rich_line 里的 text 不应再含换行；按行拆开
        mixed: list[dict[str, Any]] = []
        for p in line_parts:
            if p.get("type") != "text":
                mixed.append(p)
                continue
            t = str(p.get("text") or "")
            if "\n" not in t:
                if t:
                    mixed.append(p)
                continue
            chunks = t.split("\n")
            for i, chunk in enumerate(chunks):
                if i > 0:
                    if mixed:
                        out.append({"type": "rich_line", "parts": list(mixed)})
                        mixed = []
                if chunk:
                    mixed.append({"type": "text", "text": chunk})
        if mixed:
            if all(x.get("type") == "text" for x in mixed):
                joined = "".join(x.get("text") or "" for x in mixed)
                if joined.strip():
                    out.extend(_parse_md_blocks(joined, image_max_width=max_formula_w))
            else:
                out.append({"type": "rich_line", "parts": list(mixed)})
        line_parts = []

    for kind, content, is_display in segs:
        if kind == "text":
            # 保留换行在同一个 text part 里，flush 时整段交给 MD 块解析
            # （旧逻辑按行 flush，会把表格拆成单行 p）
            text_piece = str(content or "")
            if text_piece:
                line_parts.append({"type": "text", "text": text_piece})
            continue

        if is_display:
            flush_line()
            im = _render_one(content, display=True)
            if im is not None:
                out.append({
                    "type": "formula_img",
                    "image": im,
                    "text": content,
                    "display": True,
                })
            else:
                out.append({"type": "p", "text": content, "runs": parse_inline_runs(content)})
            continue

        im = _render_one(content, display=False)
        if im is not None:
            line_parts.append({
                "type": "formula_img",
                "image": im,
                "text": content,
                "display": False,
            })
        else:
            line_parts.append({"type": "text", "text": f"${content}$"})

    flush_line()
    return out or _parse_md_blocks(text, image_max_width=max_formula_w)


def _draw_message_png(
    data: dict[str, Any],
    style: CardStyle,
    *,
    formula_mode: str = "off",
) -> tuple[bytes, int, int]:
    """Pillow 对话卡：Markdown 子集；detect 时公式段嵌 matplotlib 紧凑图。"""
    fmode = normalize_formula_mode(formula_mode)
    use_math = fmode == "detect" and matplotlib_available() and _HAS_PILLOW
    scale = style.font_scale
    pad = style.padding
    width = style.width
    content_w = width - pad * 2
    line_extra = 4  # 行距

    # 手机聊天气泡里看：正文至少 ~16–18px 量级
    title_size = max(18, int(24 * scale))
    sub_size = max(13, int(15 * scale))
    body_size = max(15, int(17 * scale))
    code_size = max(13, int(14.5 * scale))
    h1_size = max(18, int(22 * scale))
    h2_size = max(16, int(19 * scale))
    foot_size = max(12, int(13 * scale))
    formula_fs = max(8.0, body_size * 0.52)

    tmp = Image.new("RGB", (width, 100), _hex_to_rgb(style.bg))
    d0 = ImageDraw.Draw(tmp)

    # 标题/正文优先非等宽；代码等宽；bold 供 **粗体** / 标题
    font_title = _load_font(title_size, False, style, bold=True)
    font_sub = _load_font(sub_size, False, style)
    font_body = _load_font(body_size, False, style)
    font_body_bold = _load_font(body_size, False, style, bold=True)
    font_code = _load_font(code_size, True, style)
    font_h1 = _load_font(h1_size, False, style, bold=True)
    font_h2 = _load_font(h2_size, False, style, bold=True)
    font_foot = _load_font(foot_size, False, style)

    title = str(data.get("title") or "Agent 消息")
    subtitle = str(data.get("subtitle") or "")
    body = str(data.get("body") or data.get("text") or "")
    footer = str(data.get("footer") or "")

    if use_math and text_has_formula(body):
        blocks = _parse_message_blocks_with_math(
            body,
            style=style,
            max_formula_w=content_w,
            formula_fontsize=max(7.0, body_size * 0.42),
            formula_fontsize_display=formula_fs,
        )
    else:
        blocks = _parse_md_blocks(body, image_max_width=content_w)

    def _table_col_widths(headers, rows) -> list[int]:
        """按真实像素测宽分配列宽。

        旧逻辑用 ``len(s)`` 估宽，ASCII 短词（true / bytes / file）与长路径
        混排时短列被压到 ~36px，单元格按字折成竖排（t/r/u/e）。现用
        font_body 实测最长单元格，并保证至少能放下约 4 个半角字符。
        """
        n = max(1, len(headers))
        cell_pad_x = 8
        # 最小列宽：约 4 个半角 + 左右 padding，避免 true/file 被竖折
        min_glyph = max(
            _text_size(d0, "WWWW", font_body)[0],
            _text_size(d0, "测测", font_body)[0],
        )
        min_col = max(48, min_glyph + cell_pad_x * 2)
        usable = max(min_col * n, content_w - 2)

        raw: list[int] = []
        for ci in range(n):
            samples = [str(headers[ci] if ci < len(headers) else "")]
            for r in rows:
                if ci < len(r):
                    samples.append(str(r[ci]))
            max_px = min_glyph
            for s in samples:
                plain = _plain_inline(s) or " "
                # 单元格可能含 `code` / **bold**；按 plain 用 body 字宽已够分配
                w, _ = _text_size(d0, plain, font_body)
                # 代码片段略宽：plain 已去反引号，再给一点余量
                if "`" in s:
                    w = int(w * 1.08) + 4
                max_px = max(max_px, w)
            raw.append(max_px + cell_pad_x * 2)

        # 先给每列最小宽度，剩余按「超出最小」的需求比例分
        base = [min_col] * n
        need = [max(0, raw[i] - min_col) for i in range(n)]
        remain = max(0, usable - min_col * n)
        total_need = sum(need) or 1
        cols = list(base)
        if remain > 0:
            if total_need <= remain:
                # 放得下：按实测需求加宽，多余给最后一列
                for i in range(n):
                    cols[i] = min_col + need[i]
                drift = usable - sum(cols)
                cols[-1] = max(min_col, cols[-1] + drift)
            else:
                # 总宽不够：按需求比例压缩，仍守 min_col
                allocated = 0
                for i in range(n - 1):
                    extra = int(remain * need[i] / total_need)
                    cols[i] = min_col + extra
                    allocated += extra
                cols[-1] = min_col + max(0, remain - allocated)
        else:
            # usable 极窄：均分
            each = max(min_col, usable // n)
            cols = [each] * n
            cols[-1] = max(min_col, usable - each * (n - 1))
        return cols

    def _measure_table(b) -> int:
        headers = list(b.get("headers") or [])
        rows = list(b.get("rows") or [])
        cols = _table_col_widths(headers, rows)
        cell_pad_x, cell_pad_y = 8, 6
        line_h = _text_size(d0, "测", font_body)[1] + 2

        def row_h(cells, is_header=False):
            # 表头/单元格都走正文（可粗体）；不再用等宽，避免表内 ** ** 只剩纯文本还发灰
            f_body = font_body_bold if is_header else font_body
            f_bold = font_body_bold
            max_lines = 1
            for ci, cell in enumerate(cells):
                cw = cols[ci] - cell_pad_x * 2 if ci < len(cols) else 40
                runs = parse_inline_runs(str(cell))
                # 表头整格默认粗体：无标记时包一层 bold
                if is_header and runs and all(r.get("type") == "text" for r in runs):
                    runs = [{"type": "bold", "text": _plain_inline(str(cell))}]
                lines = _wrap_inline_runs(
                    d0,
                    runs,
                    font_body=f_body,
                    font_bold=f_bold,
                    font_code=font_code,
                    max_width=max(20, cw),
                ) or [[]]
                max_lines = max(max_lines, len(lines))
            return max_lines * line_h + cell_pad_y * 2

        h = row_h(headers, True)
        for r in rows:
            h += row_h(r, False)
        return h + 16

    def _rich_line_height(parts) -> int:
        h = _text_size(d0, "测", font_body)[1]
        for p in parts or []:
            if p.get("type") == "formula_img" and p.get("image") is not None:
                h = max(h, int(getattr(p["image"], "height", h)))
            elif p.get("type") == "text":
                h = max(h, _text_size(d0, p.get("text") or " ", font_body)[1])
        return h + 6

    tool_pad_y = max(4, int(style.tool_pad_y))
    tool_pad_x = max(4, int(style.tool_pad_x))
    tool_gap = max(0, int(style.tool_gap))
    tool_inner_w = max(40, content_w - tool_pad_x * 2 - max(0, int(style.tool_bar_w)) - 8)

    def _measure_tool_block(b) -> int:
        """工具调用 / Ask 行：标签 + 名称 + 可选多行详情（含 Edit -/+）。读 style.tool_*。"""
        name = str(b.get("name") or b.get("text") or "tool")
        detail = str(b.get("detail") or "")
        tag = "Tool" if b.get("type") == "tool" else "Ask"
        head = f"[{tag}] {name}"
        h = tool_pad_y * 2 + _text_size(d0, head, font_body_bold)[1]
        if detail:
            h += 6
            for raw_ln in detail.split("\n"):
                for ln in _wrap_text(d0, raw_ln, font_code, tool_inner_w) or [""]:
                    h += _text_size(d0, ln or " ", font_code)[1] + 3
        return h + tool_gap

    def _measure_todo_block(b) -> int:
        content = str(b.get("text") or "")
        mark = {"done": "[x]", "run": "[~]", "todo": "[ ]"}.get(
            str(b.get("status") or "todo"), "[ ]"
        )
        line = f"{mark} {content}".rstrip()
        lines = _wrap_text(d0, line, font_body, content_w - 20) or [""]
        return sum(_text_size(d0, ln or " ", font_body)[1] + line_extra for ln in lines) + 6

    def _measure_subagent_block(b) -> int:
        """独立子代理小卡：标题栏 + 内嵌 blocks + 边距（偏宽松，避免挤）。"""
        label = str(b.get("label") or "子代理")
        head = f"子代理 · {label}"
        # 比 tool 条更宽的内边距与卡间距
        pad_y = max(14, tool_pad_y + 6)
        pad_x = max(16, tool_pad_x + 4)
        bar_w = max(4, int(style.tool_bar_w) or 4)
        card_gap = max(18, tool_gap + 8)
        head_gap = 12  # 标题下到分割线/正文
        inner_gap = 8  # 内嵌块之间
        inner_w = max(
            40,
            content_w - pad_x * 2 - bar_w - 12,
        )
        line_loose = line_extra + 4
        h = pad_y * 2 + _text_size(d0, head, font_body_bold)[1] + head_gap
        inner_blocks = list(b.get("blocks") or [])
        if inner_blocks:
            for idx, ib in enumerate(inner_blocks):
                ibt = ib.get("type")
                try:
                    if ibt in ("tool", "ask"):
                        # 内嵌 tool 用更窄宽度重算
                        name = str(ib.get("name") or ib.get("text") or "tool")
                        detail = str(ib.get("detail") or "")
                        tag = "Tool" if ibt == "tool" else "Ask"
                        head_t = f"[{tag}] {name}"
                        ih = tool_pad_y * 2 + _text_size(d0, head_t, font_body_bold)[1]
                        if detail:
                            ih += 8
                            for raw_ln in detail.split("\n"):
                                for ln in _wrap_text(
                                    d0, raw_ln, font_code, max(40, inner_w - 20)
                                ) or [""]:
                                    ih += _text_size(d0, ln or " ", font_code)[1] + 4
                        h += ih
                    elif ibt == "code":
                        code_txt = str(ib.get("text") or "")
                        clines = (
                            _wrap_text(d0, code_txt, font_code, max(40, inner_w - 16))
                            or [""]
                        )
                        h += (
                            sum(
                                _text_size(d0, ln or " ", font_code)[1] + 4
                                for ln in clines
                            )
                            + 16
                        )
                    elif ibt == "li":
                        raw = "● " + str(ib.get("text") or "")
                        for ln in _wrap_text(d0, raw, font_body, inner_w) or [""]:
                            h += _text_size(d0, ln or " ", font_body)[1] + line_loose
                        h += 6
                    else:
                        raw = str(ib.get("text") or "")
                        for ln in _wrap_text(d0, raw, font_body, inner_w) or [""]:
                            h += _text_size(d0, ln or " ", font_body)[1] + line_loose
                        h += 6
                except Exception:
                    h += _text_size(d0, "测", font_body)[1] + 16
                if idx < len(inner_blocks) - 1:
                    h += inner_gap
        else:
            body = str(b.get("text") or "")
            for ln in _wrap_text(d0, body, font_body, inner_w) or [""]:
                h += _text_size(d0, ln or " ", font_body)[1] + line_loose
        return h + card_gap

    def measure_block(b) -> int:
        h = 0
        if b["type"] == "rich_line":
            return _rich_line_height(b.get("parts") or []) + 6
        if b["type"] == "formula_img":
            im = b.get("image")
            if im is not None:
                return int(getattr(im, "height", 40)) + (18 if b.get("display") else 10)
            return 48
        if b["type"] == "table":
            return _measure_table(b)
        if b["type"] == "subagent":
            return _measure_subagent_block(b)
        if b["type"] in ("tool", "ask"):
            return _measure_tool_block(b)
        if b["type"] == "todo":
            return _measure_todo_block(b)
        if b["type"] == "code":
            for line in _wrap_text(d0, b["text"], font_code, content_w - 24) or [""]:
                h += _text_size(d0, line or " ", font_code)[1] + line_extra
            return h + 24
        if b["type"] in ("h1", "h2", "h3"):
            f = font_h1 if b["type"] == "h1" else font_h2
            runs = list(b.get("runs") or parse_inline_runs(b.get("text") or ""))
            return (
                _measure_inline_runs(
                    d0,
                    runs,
                    font_body=f,
                    font_bold=f,
                    font_code=font_code,
                    max_width=content_w,
                    line_extra=line_extra,
                )
                + 12
            )
        if b["type"] == "hr":
            return 18
        prefix = ""
        max_w = content_w
        if b["type"] == "image":
            im = b.get("image")
            if im is not None:
                # 绘制前再按 content_w 校正一次，保证测高与最终显示一致
                try:
                    fitted = _fit_pil_image_to_card(im, max_width=content_w)
                    if fitted is not None:
                        b["image"] = fitted
                        im = fitted
                except Exception:
                    pass
                return int(getattr(im, "height", 40)) + 20
            # 未解析到像素时按一行说明占位
            return _text_size(d0, "测", font_body)[1] + 20
        if b["type"] == "li":
            prefix = "● "
        elif b["type"] == "quote":
            prefix = ""
            max_w = content_w - 16
        runs = list(b.get("runs") or parse_inline_runs(b.get("text") or ""))
        if prefix:
            runs = [{"type": "text", "text": prefix}] + runs
        return (
            _measure_inline_runs(
                d0,
                runs,
                font_body=font_body,
                font_bold=font_body_bold,
                font_code=font_code,
                max_width=max_w,
                line_extra=line_extra,
            )
            + 10
        )

    y = pad
    y += _text_size(d0, title, font_title)[1] + 8
    if subtitle:
        for _ in _wrap_text(d0, subtitle, font_sub, content_w):
            y += _text_size(d0, "测", font_sub)[1] + line_extra
        y += 8
    y += 12  # bar
    for b in blocks:
        y += measure_block(b)
    if footer:
        y += 28
        for _ in _wrap_text(d0, footer, font_foot, content_w):
            y += _text_size(d0, "测", font_foot)[1] + line_extra + 2
    if style.show_brand:
        y += 14 + _text_size(d0, "hapi", font_foot)[1]
    # 工具条多时预估易偏矮：加底部安全边距，画完再按 content_bottom 校准
    y += pad + 48
    height = max(int(y), 160)
    height = min(height, 16000)

    bg = _hex_to_rgb(style.bg)
    fg = _hex_to_rgb(style.fg)
    accent = _hex_to_rgb(style.accent)
    muted = _hex_to_rgb(style.muted)
    # 副文再向正文靠一点，避免「发灰看不清」
    sub_fg = _mix_rgb(muted, fg, 0.35)
    border = _hex_to_rgb(style.border)
    code_bg = _hex_to_rgb(style.code_bg)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    _draw_rounded_rect(
        draw,
        (1, 1, width - 2, height - 2),
        radius=style.radius,
        fill=bg,
        outline=border,
        width=2,
    )

    y = pad
    draw.text((pad, y), title, font=font_title, fill=fg)
    y += _text_size(draw, title, font_title)[1] + 8
    if subtitle:
        for line in _wrap_text(draw, subtitle, font_sub, content_w):
            draw.text((pad, y), line, font=font_sub, fill=sub_fg)
            y += _text_size(draw, line or " ", font_sub)[1] + line_extra
        y += 8
    draw.rectangle((pad, y, pad + min(160, content_w // 3), y + 4), fill=accent)
    y += 14

    for b in blocks:
        # 高度按内容估全，不够就在贴图处扩画布；不截断正文
        if b["type"] == "rich_line":
            parts = b.get("parts") or []
            # 同行：文字 + 行内公式，左对齐、垂直居中
            line_h = _rich_line_height(parts)
            x = pad
            for p in parts:
                if p.get("type") == "formula_img" and p.get("image") is not None:
                    im = p["image"]
                    # 放不下则换行
                    if x > pad and x + im.width > width - pad:
                        y += line_h + 4
                        x = pad
                        line_h = max(line_h, im.height)
                    yy = y + max(0, (line_h - im.height) // 2)
                    if y + line_h > height - pad:
                        new_h = y + line_h + pad + 40
                        if new_h > height:
                            bigger = Image.new("RGB", (width, new_h), bg)
                            bigger.paste(img, (0, 0))
                            img = bigger
                            draw = ImageDraw.Draw(img)
                            height = new_h
                    try:
                        img.paste(im, (x, yy))
                    except Exception:
                        draw.text((x, y), str(p.get("text") or ""), font=font_body, fill=fg)
                    x += im.width + 3
                else:
                    t = str(p.get("text") or "")
                    if not t:
                        continue
                    # 同行内 ** / `：按 run 分段画；过长则从下一行 pad 起重新折
                    runs = parse_inline_runs(t)
                    remain_w = max(40, width - pad - x)
                    lines = _wrap_inline_runs(
                        draw,
                        runs,
                        font_body=font_body,
                        font_bold=font_body_bold,
                        font_code=font_code,
                        max_width=remain_w,
                    )
                    if len(lines) > 1 and x > pad:
                        # 先换行再按整行宽折，避免半截起笔
                        y += line_h + 4
                        x = pad
                        lines = _wrap_inline_runs(
                            draw,
                            runs,
                            font_body=font_body,
                            font_bold=font_body_bold,
                            font_code=font_code,
                            max_width=content_w,
                        )
                    for li, line in enumerate(lines):
                        if li > 0:
                            y += line_h + 4
                            x = pad
                            line_h = _text_size(draw, "测", font_body)[1]
                        for piece in line:
                            sw = 1 if piece.get("stroke") else 0
                            pw, ph = _text_size(
                                draw,
                                piece.get("text") or " ",
                                piece["font"],
                                stroke_width=sw,
                            )
                            line_h = max(line_h, ph)
                            yy = y + max(0, (line_h - ph) // 2)
                            txt = piece.get("text") or ""
                            if piece.get("type") == "code" and txt:
                                draw.rounded_rectangle(
                                    (x - 2, yy - 1, x + pw + 2, yy + ph + 1),
                                    radius=3,
                                    fill=code_bg,
                                )
                            _draw_text(
                                draw,
                                (x, yy),
                                txt,
                                piece["font"],
                                fg,
                                bold_stroke=bool(piece.get("stroke")),
                            )
                            x += pw
            y += line_h + 6
            continue
        if b["type"] == "formula_img":
            im = b.get("image")
            if im is not None:
                try:
                    # 块级：居中；行内残留：左对齐
                    if b.get("display", True):
                        x0 = pad + max(0, (content_w - im.width) // 2)
                    else:
                        x0 = pad
                    if y + im.height > height - pad:
                        new_h = y + im.height + pad + 40
                        if new_h > height:
                            bigger = Image.new("RGB", (width, new_h), bg)
                            bigger.paste(img, (0, 0))
                            img = bigger
                            draw = ImageDraw.Draw(img)
                            height = new_h
                    img.paste(im, (x0, y))
                    y += im.height + (14 if b.get("display", True) else 8)
                except Exception:
                    for line in _wrap_text(
                        draw, str(b.get("text") or ""), font_body, content_w
                    ):
                        draw.text((pad, y), line, font=font_body, fill=fg)
                        y += _text_size(draw, line or " ", font_body)[1] + line_extra
                    y += 8
            else:
                for line in _wrap_text(
                    draw, str(b.get("text") or ""), font_body, content_w
                ):
                    draw.text((pad, y), line, font=font_body, fill=fg)
                    y += _text_size(draw, line or " ", font_body)[1] + line_extra
                y += 8
            continue
        if b["type"] == "subagent":
            # 独立子代理小卡：更松的内边距 / 行距 / 卡间距
            label = str(b.get("label") or "子代理")
            head = f"子代理 · {label}"
            pad_y = max(14, tool_pad_y + 6)
            pad_x = max(16, tool_pad_x + 4)
            bar_w = max(4, int(style.tool_bar_w) or 4)
            card_gap = max(18, tool_gap + 8)
            head_gap = 12
            inner_gap = 8
            line_loose = line_extra + 4
            inner_blocks = list(b.get("blocks") or [])
            block_h = _measure_subagent_block(b)
            panel_h = max(40, block_h - card_gap)
            if y + block_h + pad + 40 > height:
                new_h = y + block_h + pad + 160
                bigger = Image.new("RGB", (width, new_h), bg)
                bigger.paste(img, (0, 0))
                img = bigger
                draw = ImageDraw.Draw(img)
                height = new_h
            # 子卡底：浅于正文区、略带 tool 绿意
            fill = _mix_rgb(_hex_to_rgb(style.bg), _hex_to_rgb(style.tool_bg), 0.42)
            outline = _hex_to_rgb(style.tool_border)
            bar = _hex_to_rgb(style.tool_accent)
            head_fg = _hex_to_rgb(style.tool_accent)
            r_card = max(10, int(style.tool_radius) + 4)
            box = (pad, y, width - pad, y + panel_h)
            _draw_rounded_rect(
                draw,
                box,
                radius=r_card,
                fill=fill,
                outline=outline,
                width=1,
            )
            # 左边强调条（上下内缩，更像独立卡）
            draw.rectangle(
                (pad + 4, y + 12, pad + 4 + bar_w, y + panel_h - 12),
                fill=bar,
            )
            text_x = pad + pad_x + bar_w + 8
            yy = y + pad_y
            _draw_text(draw, (text_x, yy), head, font_body_bold, head_fg)
            yy += _text_size(draw, head, font_body_bold)[1] + head_gap
            # 标题下分割线（左右留白）
            draw.line(
                (text_x, yy - 6, width - pad - pad_x, yy - 6),
                fill=_mix_rgb(outline, fill, 0.35),
                width=1,
            )
            inner_w = max(40, width - pad - text_x - pad_x)
            if not inner_blocks:
                body = str(b.get("text") or "")
                for ln in _wrap_text(draw, body, font_body, inner_w) or [""]:
                    draw.text((text_x, yy), ln, font=font_body, fill=fg)
                    yy += _text_size(draw, ln or " ", font_body)[1] + line_loose
            else:
                for idx, ib in enumerate(inner_blocks):
                    ibt = ib.get("type")
                    if ibt in ("tool", "ask"):
                        is_ask = ibt == "ask"
                        tag = "Ask" if is_ask else "Tool"
                        name = str(ib.get("name") or ib.get("text") or "tool")
                        detail = str(ib.get("detail") or "")
                        th = f"[{tag}] {name}"
                        fill_i = _hex_to_rgb(style.ask_bg if is_ask else style.tool_bg)
                        th_h = _text_size(draw, th, font_body_bold)[1]
                        dlines: list[str] = []
                        if detail:
                            for raw_ln in detail.split("\n"):
                                dlines.extend(
                                    _wrap_text(
                                        draw, raw_ln, font_code, max(40, inner_w - 20)
                                    )
                                    or [""]
                                )
                        ih = tool_pad_y * 2 + th_h + (
                            8
                            + sum(
                                _text_size(draw, ln or " ", font_code)[1] + 4
                                for ln in dlines
                            )
                            if dlines
                            else 0
                        )
                        _draw_rounded_rect(
                            draw,
                            (text_x, yy, width - pad - pad_x, yy + ih),
                            radius=max(6, int(style.tool_radius)),
                            fill=fill_i,
                            outline=outline,
                            width=1,
                        )
                        _draw_text(
                            draw,
                            (text_x + 10, yy + tool_pad_y),
                            th,
                            font_body_bold,
                            head_fg,
                        )
                        dyy = yy + tool_pad_y + th_h + 8
                        for ln in dlines:
                            col = fg
                            s = (ln or "").lstrip()
                            if s.startswith("+"):
                                col = _hex_to_rgb(style.diff_add)
                            elif s.startswith("-"):
                                col = _hex_to_rgb(style.diff_del)
                            draw.text((text_x + 10, dyy), ln, font=font_code, fill=col)
                            dyy += _text_size(draw, ln or " ", font_code)[1] + 4
                        yy += ih + inner_gap
                    elif ibt == "code":
                        code_txt = str(ib.get("text") or "")
                        clines = (
                            _wrap_text(
                                draw, code_txt, font_code, max(40, inner_w - 16)
                            )
                            or [""]
                        )
                        ch = (
                            sum(
                                _text_size(draw, ln or " ", font_code)[1] + 4
                                for ln in clines
                            )
                            + 16
                        )
                        code_bg = _mix_rgb(fill, (20, 20, 20), 0.06)
                        _draw_rounded_rect(
                            draw,
                            (text_x, yy, width - pad - pad_x, yy + ch),
                            radius=6,
                            fill=code_bg,
                            outline=outline,
                            width=1,
                        )
                        cyy = yy + 8
                        for ln in clines:
                            draw.text((text_x + 10, cyy), ln, font=font_code, fill=fg)
                            cyy += _text_size(draw, ln or " ", font_code)[1] + 4
                        yy += ch + inner_gap
                    else:
                        prefix = "●  " if ibt == "li" else ""
                        raw = prefix + str(ib.get("text") or "")
                        for ln in _wrap_text(draw, raw, font_body, inner_w) or [""]:
                            draw.text((text_x, yy), ln, font=font_body, fill=fg)
                            yy += (
                                _text_size(draw, ln or " ", font_body)[1] + line_loose
                            )
                        yy += 6 if idx < len(inner_blocks) - 1 else 2
            y += block_h
            continue
        if b["type"] in ("tool", "ask"):
            # 样式来自 --card-tool-* / --card-ask-bg / --card-diff-*（Pillow 可读）
            is_ask = b["type"] == "ask"
            tag = "Ask" if is_ask else "Tool"
            name = str(
                b.get("name") or b.get("text") or ("request" if is_ask else "tool")
            )
            detail = str(b.get("detail") or "")
            if is_ask and not b.get("name"):
                name = str(b.get("text") or "request_user_input")
                detail = ""
            head = f"[{tag}] {name}"
            detail_lines: list[str] = []
            if detail:
                for raw_ln in detail.split("\n"):
                    detail_lines.extend(
                        _wrap_text(draw, raw_ln, font_code, tool_inner_w) or [""]
                    )
            block_h = (
                tool_pad_y * 2
                + _text_size(draw, head, font_body_bold)[1]
                + (
                    6
                    + sum(
                        _text_size(draw, ln or " ", font_code)[1] + 3
                        for ln in detail_lines
                    )
                    if detail_lines
                    else 0
                )
            )
            if y + block_h + pad + 40 > height:
                new_h = y + block_h + pad + 80
                bigger = Image.new("RGB", (width, new_h), bg)
                bigger.paste(img, (0, 0))
                img = bigger
                draw = ImageDraw.Draw(img)
                height = new_h
            fill = _hex_to_rgb(style.ask_bg if is_ask else style.tool_bg)
            outline = _hex_to_rgb(style.tool_border)
            bar = _hex_to_rgb(style.tool_accent)
            head_fg = _hex_to_rgb(style.tool_accent)
            body_fg = _hex_to_rgb(style.tool_fg)
            add_fg = _hex_to_rgb(style.diff_add)
            del_fg = _hex_to_rgb(style.diff_del)
            r_tool = max(0, int(style.tool_radius))
            bar_w = max(0, int(style.tool_bar_w))
            _draw_rounded_rect(
                draw,
                (pad, y, width - pad, y + block_h),
                radius=r_tool,
                fill=fill,
                outline=outline,
                width=1,
            )
            if bar_w > 0:
                draw.rectangle(
                    (pad + 2, y + 6, pad + 2 + bar_w, y + block_h - 6),
                    fill=bar,
                )
            text_x = pad + tool_pad_x + (bar_w + 4 if bar_w else 0)
            yy = y + tool_pad_y
            _draw_text(draw, (text_x, yy), head, font_body_bold, head_fg)
            yy += _text_size(draw, head, font_body_bold)[1] + 6
            for ln in detail_lines:
                col = body_fg
                s = (ln or "").lstrip()
                if s.startswith("+"):
                    col = add_fg
                elif s.startswith("-"):
                    col = del_fg
                draw.text((text_x, yy), ln, font=font_code, fill=col)
                yy += _text_size(draw, ln or " ", font_code)[1] + 3
            y += block_h + tool_gap
            continue
        if b["type"] == "todo":
            status = str(b.get("status") or "todo")
            mark = {"done": "[x]", "run": "[~]", "todo": "[ ]"}.get(status, "[ ]")
            content = str(b.get("text") or "")
            line = f"{mark}  {content}".rstrip()
            mark_fg = accent if status == "run" else (sub_fg if status == "todo" else fg)
            for ln in _wrap_text(draw, line, font_body, content_w - 16) or [""]:
                # 完成项略淡
                col = _mix_rgb(fg, muted, 0.35) if status == "done" else mark_fg
                draw.text((pad + 8, y), ln, font=font_body, fill=col)
                y += _text_size(draw, ln or " ", font_body)[1] + line_extra
            y += 4
            continue
        if b["type"] == "code":
            lines = _wrap_text(draw, b["text"], font_code, content_w - 24) or [""]
            block_h = (
                sum(_text_size(draw, ln or " ", font_code)[1] + line_extra for ln in lines)
                + 20
            )
            if y + block_h + pad + 40 > height:
                new_h = y + block_h + pad + 80
                bigger = Image.new("RGB", (width, new_h), bg)
                bigger.paste(img, (0, 0))
                img = bigger
                draw = ImageDraw.Draw(img)
                height = new_h
            _draw_rounded_rect(
                draw,
                (pad, y, width - pad, y + block_h),
                radius=8,
                fill=code_bg,
                outline=border,
                width=1,
            )
            yy = y + 10
            for line in lines:
                draw.text((pad + 12, yy), line, font=font_code, fill=fg)
                yy += _text_size(draw, line or " ", font_code)[1] + line_extra
            y += block_h + 12
            continue
        if b["type"] == "table":
            headers = list(b.get("headers") or [])
            rows = list(b.get("rows") or [])
            cols = _table_col_widths(headers, rows)
            cell_pad_x, cell_pad_y = 8, 6
            line_h = _text_size(draw, "测", font_body)[1] + 2
            table_w = sum(cols)
            x0 = pad

            def _draw_row(cells, yy, is_header=False):
                f_body = font_body_bold if is_header else font_body
                f_bold = font_body_bold
                # 每格先折成「行内 run 行」
                wrapped_cols: list[list[list[dict[str, Any]]]] = []
                max_lines = 1
                for ci in range(len(cols)):
                    cell = cells[ci] if ci < len(cells) else ""
                    cw = max(20, cols[ci] - cell_pad_x * 2)
                    runs = parse_inline_runs(str(cell))
                    if is_header and runs and all(r.get("type") == "text" for r in runs):
                        runs = [{"type": "bold", "text": _plain_inline(str(cell))}]
                    lines = _wrap_inline_runs(
                        draw,
                        runs,
                        font_body=f_body,
                        font_bold=f_bold,
                        font_code=font_code,
                        max_width=cw,
                    ) or [[]]
                    wrapped_cols.append(lines)
                    max_lines = max(max_lines, len(lines))
                rh = max_lines * line_h + cell_pad_y * 2
                fill = code_bg if is_header else bg
                draw.rectangle((x0, yy, x0 + table_w, yy + rh), fill=fill, outline=border, width=1)
                cx = x0
                for ci, col_w in enumerate(cols):
                    if ci > 0:
                        draw.line((cx, yy, cx, yy + rh), fill=border, width=1)
                    lines = wrapped_cols[ci] if ci < len(wrapped_cols) else [[]]
                    ty = yy + cell_pad_y
                    for run_line in lines:
                        tx = cx + cell_pad_x
                        for run in run_line:
                            rtype = run.get("type") or "text"
                            rtext = str(run.get("text") or "")
                            if not rtext:
                                continue
                            font = run.get("font") or _font_for_run(
                                rtype, f_body, f_bold, font_code
                            )
                            stroke = bool(run.get("stroke")) or _run_needs_stroke(
                                rtype, f_body, f_bold
                            )
                            if rtype == "code":
                                tw, th = _text_size(draw, rtext, font)
                                draw.rectangle(
                                    (tx - 1, ty - 1, tx + tw + 1, ty + th + 1),
                                    fill=code_bg,
                                )
                            _draw_text(
                                draw, (tx, ty), rtext, font, fg, bold_stroke=stroke
                            )
                            tw, _ = _text_size(
                                draw, rtext, font, stroke_width=1 if stroke else 0
                            )
                            tx += tw
                        ty += line_h
                    cx += col_w
                draw.line((x0 + table_w, yy, x0 + table_w, yy + rh), fill=border, width=1)
                return rh

            y_row = y
            y_row += _draw_row(headers, y_row, True)
            for r in rows:
                y_row += _draw_row(r, y_row, False)
            y = y_row + 12
            continue
        if b["type"] == "hr":
            draw.line((pad, y + 8, width - pad, y + 8), fill=border, width=1)
            y += 18
            continue
        if b["type"] in ("h1", "h2", "h3"):
            f = font_h1 if b["type"] == "h1" else font_h2
            runs = list(b.get("runs") or parse_inline_runs(b.get("text") or ""))
            used = _draw_inline_runs(
                draw,
                pad,
                y,
                runs,
                font_body=f,
                font_bold=f,
                font_code=font_code,
                fill=fg,
                code_bg=code_bg,
                max_width=content_w,
                line_extra=line_extra,
                accent=accent,
            )
            y += used + 10
            continue
        if b["type"] == "quote":
            runs = list(b.get("runs") or parse_inline_runs(b.get("text") or ""))
            q_h = _measure_inline_runs(
                draw,
                runs,
                font_body=font_body,
                font_bold=font_body_bold,
                font_code=font_code,
                max_width=content_w - 16,
                line_extra=line_extra,
            )
            draw.rectangle((pad, y, pad + 4, y + max(q_h, 18)), fill=accent)
            used = _draw_inline_runs(
                draw,
                pad + 14,
                y,
                runs,
                font_body=font_body,
                font_bold=font_body_bold,
                font_code=font_code,
                fill=sub_fg,
                code_bg=code_bg,
                max_width=content_w - 16,
                line_extra=line_extra,
                accent=accent,
            )
            y += max(used, q_h) + 8
            continue
        if b["type"] == "image":
            im = b.get("image")
            alt = str(b.get("text") or b.get("alt") or "image")
            if im is not None:
                try:
                    # 绘制时再贴合当前 content_w（解析阶段可能用了默认 640）
                    im = _fit_pil_image_to_card(im, max_width=content_w) or im
                    b["image"] = im
                    frame = 6  # 外框留白，图略小于内容宽更贴「卡适应图」
                    draw_w = min(im.width, content_w - frame * 2)
                    if draw_w < im.width:
                        im = _fit_pil_image_to_card(im, max_width=draw_w) or im
                        b["image"] = im
                    need_h = im.height + pad + 48
                    if y + need_h > height:
                        new_h = y + need_h
                        bigger = Image.new("RGB", (width, new_h), bg)
                        bigger.paste(img, (0, 0))
                        img = bigger
                        draw = ImageDraw.Draw(img)
                        height = new_h
                    # 水平居中于内容区；外框贴图尺寸，避免大块空白框
                    x0 = pad + max(0, (content_w - im.width) // 2)
                    _draw_rounded_rect(
                        draw,
                        (x0 - 4, y - 4, x0 + im.width + 4, y + im.height + 4),
                        radius=10,
                        fill=code_bg,
                        outline=border,
                        width=1,
                    )
                    if getattr(im, "mode", "") == "RGBA":
                        img.paste(im, (x0, y), im)
                    else:
                        img.paste(im, (x0, y))
                    y += im.height + 18
                except Exception:
                    for line in _wrap_text(draw, f"[图片] {alt}", font_body, content_w):
                        draw.text((pad, y), line, font=font_body, fill=sub_fg)
                        y += _text_size(draw, line or " ", font_body)[1] + line_extra
                    y += 8
            else:
                for line in _wrap_text(draw, f"[图片] {alt}", font_body, content_w):
                    draw.text((pad, y), line, font=font_body, fill=sub_fg)
                    y += _text_size(draw, line or " ", font_body)[1] + line_extra
                y += 8
            continue
        prefix = "● " if b["type"] == "li" else ""
        runs = list(b.get("runs") or parse_inline_runs(b.get("text") or ""))
        if prefix:
            runs = [{"type": "text", "text": prefix}] + runs
        used = _draw_inline_runs(
            draw,
            pad,
            y,
            runs,
            font_body=font_body,
            font_bold=font_body_bold,
            font_code=font_code,
            fill=fg,
            code_bg=code_bg,
            max_width=content_w,
            line_extra=line_extra,
            accent=accent,
        )
        y += used + 8

    if footer:
        y += 8
        draw.line((pad, y, width - pad, y), fill=border, width=1)
        y += 12
        for line in _wrap_text(draw, footer, font_foot, content_w):
            # 画布不够时先扩，避免 footer 被裁
            need = y + _text_size(draw, line or " ", font_foot)[1] + pad + 24
            if need > height:
                bigger = Image.new("RGB", (width, need), bg)
                bigger.paste(img, (0, 0))
                img = bigger
                draw = ImageDraw.Draw(img)
                height = need
            draw.text((pad, y), line, font=font_foot, fill=accent)
            y += _text_size(draw, line or " ", font_foot)[1] + line_extra + 2

    brand_h = 0
    if style.show_brand:
        brand_h = _text_size(draw, "hapi connector", font_foot)[1] + 4

    # 按实际内容扩展/裁剪（与 session_list / 结构卡一致）
    content_bottom = int(y + pad + brand_h + 16)
    if content_bottom > height:
        bigger = Image.new("RGB", (width, content_bottom), bg)
        bigger.paste(img, (0, 0))
        img = bigger
        height = content_bottom
        draw = ImageDraw.Draw(img)
    elif content_bottom < height:
        height = max(content_bottom, 160)
        img = img.crop((0, 0, width, height))
        draw = ImageDraw.Draw(img)

    if style.show_brand:
        brand = "hapi connector"
        bw, bh = _text_size(draw, brand, font_foot)
        draw.text(
            (width - pad - bw, height - pad - bh), brand, font=font_foot, fill=sub_fg
        )

    # 外框最后画，扩展后不丢边框
    _draw_rounded_rect(
        draw,
        (1, 1, width - 2, height - 2),
        radius=style.radius,
        fill=None,
        outline=border,
        width=2,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), width, height


_RE_MD_IMAGE_ONLY = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$")
_RE_MD_IMAGE_ANY = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _is_tool_detail_continuation(line: str) -> bool:
    """判断是否为 [Tool] 块的 diff/缩进续行（须并入 tool.detail，不能当列表）。

    覆盖：
    - 缩进的 ``  + foo`` / ``  - bar`` / ``  · …``
    - 顶格 diff 行 ``+ foo`` / ``- bar``（Edit 预览常见；旧逻辑漏吃会变成 ● 列表）
    - 续行缩进的普通代码行（两空格起头）
    - 空行夹在 diff 中间时仍算续行（由调用方在遇到非续行时停止）
    """
    if line is None:
        return False
    # 空行：不主动吞；由 while 在下一条非续行时 break
    if not line.strip():
        return False
    # 新块起点：绝不能吞
    s = line.lstrip()
    if s.startswith("[Tool]") or s.startswith("[Ask]") or s.startswith("【"):
        return False
    if s.startswith("```") or s.startswith("#"):
        return False
    if re.match(r"^---+$|^\*\*\*+$|^___+$|^━{3,}$", s):
        return False
    # 缩进 + diff 标记
    if re.match(r"^\s+([+\-…]|\.\.\.|·)", line):
        return True
    # 顶格 diff（Edit 输出常见）
    if re.match(r"^[+\-](\s+|$)", line):
        return True
    # 至少两空格缩进的续行（代码块片段）
    if line.startswith("  ") and line.strip():
        return True
    return False


def _fit_pil_image_to_card(
    im: Any,
    *,
    max_width: int = 640,
    max_height: int | None = None,
) -> Any:
    """让嵌入图适配对话卡内容区：优先铺满内容宽，高度跟比例；过长再限高。

    旧逻辑 ``min(宽比, 高比, 1)`` 会让竖长截图先被高度卡住，左右大片留白，
    看起来像「没适应分辨率」。现策略：
    1. 目标宽 = content 宽（几乎铺满，不强制放大超小图超过 1.5×）
    2. 按宽等比缩放（可略放大中等图，让截图贴齐卡宽）
    3. 若高度仍超过上限，再整体缩小（保持比例，居中由绘制侧处理）
    """
    if im is None:
        return None
    try:
        w, h = im.size
    except Exception:
        return im
    if w <= 0 or h <= 0:
        return im

    tw = max(40, int(max_width))
    # 默认高上限放宽：优先「铺满内容宽」，卡片跟着图变高。
    # 仅极端长图（约 >3.2× 内容宽，或绝对像素）再等比压高度，避免一条消息无限长。
    if max_height is None or max_height <= 0:
        th = max(1200, int(tw * 3.2))
    else:
        th = max(40, int(max_height))

    # 目标：铺满内容宽（卡片适应图，而不是图被高上限挤窄）
    if w >= tw:
        scale = tw / float(w)
    elif w < tw * 0.45:
        # 很小的图标/表情：最多放大到 1.5×
        scale = min(tw / float(w), 1.5)
    else:
        # 中等宽度截图：放大到内容宽，避免左右大片留白
        scale = tw / float(w)

    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    # 仅极端长图限高（仍保持比例 → 会略窄于内容宽，属有意兜底）
    if nh > th:
        scale2 = th / float(nh)
        nw = max(1, int(round(nw * scale2)))
        nh = max(1, int(round(nh * scale2)))

    if nw == w and nh == h:
        return im
    try:
        resample = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
    except Exception:
        resample = Image.LANCZOS  # type: ignore[attr-defined]
    try:
        return im.resize((nw, nh), resample)
    except Exception:
        return im


def _load_pil_image_from_src(
    src: str,
    *,
    max_width: int = 640,
    max_height: int | None = None,
) -> Any | None:
    """从本地路径 / data URL 加载 PIL 图，并按卡片内容宽适配。失败返回 None。"""
    if not _HAS_PILLOW or not src:
        return None
    raw: bytes | None = None
    s = str(src).strip()
    try:
        if s.startswith("data:"):
            # data:image/png;base64,....
            comma = s.find(",")
            if comma < 0:
                return None
            meta, b64 = s[:comma], s[comma + 1 :]
            if ";base64" not in meta:
                return None
            import base64 as _b64

            raw = _b64.b64decode(b64, validate=False)
        elif s.startswith("file://"):
            from urllib.parse import unquote, urlparse

            path = unquote(urlparse(s).path or "")
            if path and Path(path).is_file():
                raw = Path(path).read_bytes()
        elif s.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", s):
            p = Path(s)
            if p.is_file():
                raw = p.read_bytes()
        # http(s) / hapi-genimg:// 由上层预下载成本地路径后再进来
        else:
            p = Path(s)
            if p.is_file():
                raw = p.read_bytes()
    except Exception as e:
        logger.debug("load image src failed: %s · %s", s[:80], e)
        return None
    if not raw:
        return None
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        return _fit_pil_image_to_card(
            im, max_width=max_width, max_height=max_height
        )
    except Exception as e:
        logger.debug("decode image failed: %s", e)
        return None


def _parse_md_blocks(
    text: str,
    *,
    image_max_width: int = 640,
) -> list[dict[str, Any]]:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    blocks: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            blocks.append({"type": "code", "text": "\n".join(buf)})
            continue
        # GFM table（须在空行/hr 判断前）
        parsed = _try_parse_md_table(lines, i)
        if parsed is not None:
            block, ni = parsed
            blocks.append(block)
            i = ni
            continue
        if not line.strip():
            i += 1
            continue
        if re.match(r"^---+$|^\*\*\*+$|^___+$", line.strip()):
            blocks.append({"type": "hr", "text": ""})
            i += 1
            continue
        # 独占一行的 Markdown 图：![alt](src)
        m_img = _RE_MD_IMAGE_ONLY.match(line)
        if m_img:
            alt = (m_img.group(1) or "").strip() or "image"
            src = (m_img.group(2) or "").strip()
            im = _load_pil_image_from_src(src, max_width=image_max_width)
            blocks.append(
                {
                    "type": "image",
                    "text": alt,
                    "alt": alt,
                    "src": src,
                    "image": im,
                }
            )
            i += 1
            continue
        # 子代理独立小卡：【子代理】/【子代理:名称】起一段，直到下一个子代理/主消息/分隔线
        m_sub = re.match(r"^【子代理(?::([^】]*))?】(.*)$", line)
        if m_sub:
            label = (m_sub.group(1) or "").strip() or "子代理"
            rest = (m_sub.group(2) or "").strip()
            buf: list[str] = [rest] if rest else []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if re.match(r"^【子代理(?::[^】]*)?】", nxt):
                    break
                # 主消息起新段，不并进子代理小卡
                if nxt.startswith("【消息】") or nxt.startswith("[Message]"):
                    break
                if re.match(r"^---+$|^\*\*\*+$|^___+$|^━{3,}$", nxt.strip()):
                    break
                buf.append(nxt)
                i += 1
            inner = "\n".join(buf).strip()
            inner_blocks = (
                _parse_md_blocks(inner, image_max_width=max(120, image_max_width - 28))
                if inner
                else []
            )
            blocks.append(
                {
                    "type": "subagent",
                    "label": label,
                    "text": inner,
                    "blocks": inner_blocks,
                }
            )
            continue
        # 主消息前缀：卡片上不画「【消息】」/「[Message]:」字样，只留正文
        if line.startswith("[Message]:"):
            line = line[len("[Message]:") :].lstrip()
            if not line.strip():
                i += 1
                continue
        elif line.startswith("[Message]"):
            line = line[len("[Message]") :].lstrip(" :：")
            if not line.strip():
                i += 1
                continue
        elif line.startswith("【消息】"):
            line = line[len("【消息】") :].lstrip()
            if not line.strip():
                i += 1
                continue
            # 用剥前缀后的行继续本轮解析
        # 卡片用工具/任务行（无 emoji；见 output_present.prepare_agent_body_for_card）
        m = re.match(r"^\[Tool\]\s*(.*)$", line)
        if m:
            rest = (m.group(1) or "").strip()
            name, _, detail = rest.partition(":")
            name = name.strip() or "tool"
            detail = detail.strip()
            # 吃掉后续 diff/缩进续行。
            # Edit 的 -/+ 行若没缩进（或仅 "+ foo"），不能当普通列表，
            # 否则会渲染成一堆 ● 意义不明的列表（用户截图问题）。
            i += 1
            cont: list[str] = []
            while i < len(lines):
                nxt = lines[i]
                if _is_tool_detail_continuation(nxt):
                    # 保留原行（含缩进语义），绘制侧再处理
                    cont.append(nxt.rstrip())
                    i += 1
                    continue
                break
            if cont:
                # 去掉仅用于对齐的前导双空格，保留 +/- 标记
                cleaned: list[str] = []
                for raw_c in cont:
                    s = raw_c
                    if s.startswith("  "):
                        s = s[2:]
                    cleaned.append(s if s.strip() else s)
                detail = (detail + "\n" if detail else "") + "\n".join(cleaned)
            blocks.append(
                {
                    "type": "tool",
                    "name": name,
                    "detail": detail,
                    "text": rest or name,
                }
            )
            continue
        m = re.match(r"^\[Ask\]\s*(.*)$", line)
        if m:
            rest = (m.group(1) or "").strip()
            blocks.append({"type": "ask", "text": rest or "request_user_input"})
            i += 1
            continue
        m = re.match(r"^(\s*)\[(done|run|todo)\]\s*(.*)$", line, re.I)
        if m:
            status = m.group(2).lower()
            content = (m.group(3) or "").strip()
            blocks.append(
                {
                    "type": "todo",
                    "status": status,
                    "text": content,
                    "indent": len(m.group(1) or ""),
                }
            )
            i += 1
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            raw = m.group(2)
            blocks.append(
                {
                    "type": f"h{level}",
                    "text": _plain_inline(raw),
                    "runs": parse_inline_runs(raw),
                }
            )
            i += 1
            continue
        if line.lstrip().startswith(">"):
            raw = re.sub(r"^\s*>\s?", "", line)
            blocks.append(
                {
                    "type": "quote",
                    "text": _plain_inline(raw),
                    "runs": parse_inline_runs(raw),
                }
            )
            i += 1
            continue
        # GFM 任务列表：- [x] / - [ ] / 1. [x]
        # 注意：不用 + 作列表标记，避免与 Edit diff 的 "+ code" 冲突
        m = re.match(r"^(\s*)(?:[-*]|\d+\.)\s+\[([ xX~])\]\s+(.*)$", line)
        if m:
            mark = (m.group(2) or " ").lower()
            content = (m.group(3) or "").strip()
            status = "done" if mark == "x" else ("run" if mark == "~" else "todo")
            blocks.append(
                {
                    "type": "todo",
                    "status": status,
                    "text": content,
                    "indent": len(m.group(1) or ""),
                }
            )
            i += 1
            continue
        # 无序列表仅 - / *（排除 +，否则 Edit 的 "+ foo" 会变成 ● 列表）
        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        if m:
            raw = m.group(1)
            blocks.append(
                {
                    "type": "li",
                    "text": _plain_inline(raw),
                    "runs": parse_inline_runs(raw),
                }
            )
            i += 1
            continue
        m = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if m:
            raw = m.group(1)
            blocks.append(
                {
                    "type": "li",
                    "text": _plain_inline(raw),
                    "runs": parse_inline_runs(raw),
                }
            )
            i += 1
            continue
        # 保留 ** / ` 标记到 runs，绘制时真正加粗；text 为纯文本兜底
        blocks.append(
            {
                "type": "p",
                "text": _plain_inline(line),
                "runs": parse_inline_runs(line),
            }
        )
        i += 1
    return blocks or [{"type": "p", "text": "", "runs": []}]
