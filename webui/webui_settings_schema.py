"""WebUI 设置页 schema：结构认 _conf_schema.json，详细文案/分组只在 overlay。

前端不再维护整份 SETTINGS 字段表；meta.config_schema 为唯一 UI 结构来源。
本地 mock 使用 pages/console/settings_schema_fallback.js（由本模块生成内容对齐）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_CONF_SCHEMA_PATH = _ROOT / "_conf_schema.json"

# access_token 在 WebUI 明文编辑（与 public_config 策略一致）；CF secret 仍敏感
SENSITIVE_UI_KEYS = frozenset({"cf_access_client_secret"})

# ── 仅 WebUI 多出来的：分组、长文案、控件形态、条件显示 ─────────────────────
# key 必须存在于 _conf_schema.json；未写的字段若被 groups 引用，会用 schema 的 description/hint。

GROUPS: list[dict[str, Any]] = [
    {
        "id": "connection",
        "title": "连接 HAPI",
        "nav": "连接",
        "desc": "第一步：填 HAPI 服务地址和访问令牌，连上后其它功能才能用。改动连接类配置后插件会自动重连。",
        "fields": ["hapi_endpoint", "access_token", "proxy_url"],
        "advanced": {
            "title": "高级：Cloudflare Access / 重连 / JWT",
            "note": "自建直连多数不用改。HAPI 挂在 CF Access 后面，或 SSE 总断线，再展开。",
            "fields": [
                "cf_access_client_id",
                "cf_access_client_secret",
                "max_reconnect_attempts",
                "jwt_lifetime",
                "refresh_before_expiry",
            ],
        },
    },
    {
        "id": "push",
        "title": "推送通知",
        "nav": "推送",
        "desc": "AI 干活时，聊天里推多少内容、以什么形式显示。快捷前缀、戳一戳、图片样式细调在「交互优化」页。",
        "fields": [
            "output_level",
            "summary_msg_count",
            "render_mode",
            "render_kinds",
            "auto_approve_silent",
            "auto_approve_summary_mode",
            "auto_approve_summary_push",
            "auto_approve_summary_time",
            "auto_approve_summary_include_failures",
            "auto_approve_summary_max_detail_lines",
        ],
    },
    {
        "id": "approve",
        "title": "权限审批与托管",
        "nav": "审批",
        "desc": "AI 要跑命令、改文件前会先请求你批准。这里设置超时提醒和定时自动放行。",
        "fields": [
            "remind_pending",
            "remind_interval",
            "auto_approve_enabled",
            "auto_approve_start",
            "auto_approve_end",
        ],
    },
]

# 字段级覆盖：只写与 schema 不同或 WebUI 专属的部分
FIELD_OVERLAY: dict[str, dict[str, Any]] = {
    "hapi_endpoint": {
        "label": "HAPI 服务地址",
        "help": "HAPI Hub 的访问地址。本机一般是 http://127.0.0.1:3006；装在别的机器就写那台的地址和端口。",
        "need": True,
        "placeholder": "http://127.0.0.1:3006",
        "control": "text",
    },
    "access_token": {
        "label": "Access Token",
        "help": "HAPI 的访问口令（部署 HAPI 时设置的那个）。支持 token:namespace 写法。注意此处明文显示。",
        "need": True,
        "control": "text",
    },
    "proxy_url": {
        "label": "代理（可选）",
        "help": "仅当 AstrBot 访问 HAPI 必须走代理时填写。支持 http:// 与 socks5h://。能直连请留空。",
        "placeholder": "socks5h://127.0.0.1:1080",
        "control": "text",
    },
    "cf_access_client_id": {
        "label": "CF Access Client ID",
        "help": "Cloudflare Zero Trust Service Token 的 Client ID。未使用请留空。",
        "control": "text",
    },
    "cf_access_client_secret": {
        "label": "CF Access Client Secret",
        "help": "与 Client ID 配对。不想改已有密钥就留空。",
        "control": "password",
        "sensitive": True,
    },
    "max_reconnect_attempts": {
        "label": "断线最大重连次数",
        "help": "连接断开后自动重试的次数，用完就休眠省资源。设 0 表示一直重试。休眠后在聊天里发 /hapi list 可唤醒。",
        "control": "number",
    },
    "jwt_lifetime": {
        "label": "JWT 有效期（秒）",
        "help": "登录凭证的有效时长，到期自动续。默认 900，一般不用改。",
        "control": "number",
    },
    "refresh_before_expiry": {
        "label": "JWT 提前刷新（秒）",
        "help": "凭证过期前多久去换新的。要小于上面的有效期，一般不用改。",
        "control": "number",
    },
    "output_level": {
        "label": "消息推送详细程度",
        "help": "有新输出时推到绑定窗口。越详细越容易刷屏；拿不准选「简洁」。",
        "need": True,
        "control": "enum_cards",
        "option_meta": {
            "silence": {
                "title": "静默",
                "desc": "平时不打扰，只在 AI 需要你批准操作或任务完成时提醒。",
            },
            "simple": {"title": "简洁（推荐）", "desc": "推送 AI 说的话和重要事件，不推工具调用细节。"},
            "summary": {"title": "摘要", "desc": "AI 干完一轮活后，把最后几条回复一起推给你（条数见下一项）。"},
            "detail": {"title": "详细", "desc": "AI 的每条输出都实时推送，信息全但很刷屏。"},
        },
    },
    "summary_msg_count": {
        "label": "摘要条数",
        "help": "推送级别为「摘要」时，收尾推送 LLM 最后几条消息的条数。",
        "control": "number",
        "show_if": {"key": "output_level", "eq": "summary"},
    },
    "render_mode": {
        "label": "推送渲染模式",
        "help": "推到聊天里的内容以什么形式显示。图片模式对代码块、表格更友好（需安装 Pillow，可在「交互优化」页一键装）。",
        "need": True,
        "control": "enum_cards",
        "option_meta": {
            "text": {"title": "纯文本", "desc": "全部以文字发送，兼容性最好。"},
            "card": {"title": "图片", "desc": "把勾选的内容类型渲染成图片发送，排版更清晰。"},
        },
    },
    "render_kinds": {
        "label": "以下类型渲成图片",
        "help": "勾选哪些内容用图片显示：会话列表、待审批、状态、权限请求、推送路由、AI 对话、操作汇总、git 状态/统计。没勾的仍发文字。",
        "control": "kind_checks",
        "show_if": {"key": "render_mode", "eq": "card"},
    },
    "auto_approve_silent": {
        "label": "托管操作汇总",
        "help": "开启后，忙时托管时段内的自动批准 / 自动压缩不再逐条推送，改为按下方策略汇总推送（如早晨一版）。关闭则保持现状逐条推。托管时段 AI 仍会自主执行全部操作，只是通知方式变了。",
        "control": "bool",
        "warn": "开启后托管时段的自动操作不再逐条推送，改为汇总推送；托管本身不受影响，AI 仍会自主执行全部操作。",
        "bool_labels": ["关闭（逐条推送）", "开启（汇总推送）"],
    },
    "auto_approve_summary_mode": {
        "label": "汇总方式",
        "help": "按托管时段：每次进入托管窗一个桶，窗结束结算（推荐）；按天：自然日一个桶；手动触发：不自动推送，每次执行 /hapi summary 命令时推当前积累的一版。开启「托管操作汇总」后生效。",
        "control": "enum_cards",
        "option_meta": {
            "window": {"title": "按托管时段（推荐）", "desc": "夜间托管结束一次性结算，最贴合睡眠场景。"},
            "daily": {"title": "按天", "desc": "自然日内的事件归一天，随时可手动推。"},
            "per_event": {"title": "手动触发", "desc": "不自动推送，每次手动 /hapi summary 命令推一版。"},
        },
    },
    "auto_approve_summary_push": {
        "label": "推送时机",
        "help": "托管结束时：窗口结束边沿自动推送；每天固定时间：每天到点推「当前已积累」的一版。两种都可以随时用 /hapi summary 手动提前推。开启「托管操作汇总」后生效。",
        "control": "enum_cards",
        "option_meta": {
            "on_window_end": {"title": "托管结束时（推荐）", "desc": "23:00–07:00 这种窗结束后立刻推一版。"},
            "at_fixed_time": {"title": "每天固定时间", "desc": "每天在下方设置的时间推送，如 08:00。"},
        },
    },
    "auto_approve_summary_time": {
        "label": "固定推送时间",
        "help": "仅在推送时机为「每天固定时间」时生效。到点对每个有内容的 session 各推一版；没内容不推。",
        "control": "time",
        "placeholder": "08:00",
    },
    "auto_approve_summary_include_failures": {
        "label": "汇总含失败明细",
        "help": "开启时失败项在汇总里列明细（置顶展示）；关闭时只计失败次数、不列明细。开启「托管操作汇总」后生效。",
        "control": "bool",
        "bool_labels": ["关闭", "开启"],
    },
    "auto_approve_summary_max_detail_lines": {
        "label": "明细行数上限",
        "help": "单个 session 汇总里成功明细最多显示多少条，超出折叠为「另有 N 条」。开启「托管操作汇总」后生效。",
        "control": "number",
    },
    "remind_pending": {
        "label": "待审批超时提醒",
        "help": "AI 的操作请求放着没批时，每隔一段时间在聊天里提醒你一次，免得忘了导致 AI 一直干等。",
        "control": "bool",
        "bool_labels": ["关闭", "开启"],
    },
    "remind_interval": {
        "label": "提醒间隔（秒）",
        "help": "两次提醒之间的秒数。间隔内处理完则不再提醒。",
        "control": "number",
        "show_if": {"key": "remind_pending", "eq": True},
    },
    "auto_approve_enabled": {
        "label": "定时自动批准（托管）",
        "help": "设定一个时间段（比如睡觉时间），期间 AI 的操作请求自动放行，不用你起来批。",
        "control": "bool",
        "warn": "开启后，时段内 AI 的所有操作都会自动批准，包括改文件、跑命令。请确认你信任正在跑的任务。",
        "bool_labels": ["关闭（更安全）", "开启"],
    },
    "auto_approve_start": {
        "label": "托管开始时间",
        "help": "整段输入 24 小时制 HH:MM，如 23:00。",
        "control": "time",
        "placeholder": "23:00",
        "show_if": {"key": "auto_approve_enabled", "eq": True},
    },
    "auto_approve_end": {
        "label": "托管结束时间",
        "help": "整段输入 HH:MM；可跨午夜，如 23:00–07:00。",
        "control": "time",
        "placeholder": "07:00",
        "show_if": {"key": "auto_approve_enabled", "eq": True},
    },
}


def load_conf_schema() -> dict[str, Any]:
    raw = json.loads(_CONF_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("_conf_schema.json 必须是对象")
    return raw


def schema_defaults() -> dict[str, Any]:
    """全部配置键的 default（给 mock / public_config 兜底）。"""
    out: dict[str, Any] = {}
    for key, spec in load_conf_schema().items():
        if isinstance(spec, dict) and "default" in spec:
            out[key] = spec["default"]
    return out


def _map_control(schema_type: str, overlay: dict[str, Any], has_options: bool) -> str:
    if overlay.get("control"):
        return str(overlay["control"])
    if schema_type == "bool":
        return "bool"
    if schema_type == "int":
        return "number"
    if has_options:
        return "enum"
    return "text"


def _resolve_field(key: str, conf: dict[str, Any]) -> dict[str, Any] | None:
    spec = conf.get(key)
    if not isinstance(spec, dict):
        return None
    ov = FIELD_OVERLAY.get(key) or {}
    schema_type = str(spec.get("type") or "string")
    options_raw = spec.get("options")
    has_options = isinstance(options_raw, list) and bool(options_raw)
    control = _map_control(schema_type, ov, has_options)

    label = ov.get("label") or spec.get("description") or key
    help_text = ov.get("help")
    if help_text is None:
        help_text = spec.get("hint") or spec.get("description") or ""

    field: dict[str, Any] = {
        "key": key,
        "label": label,
        "type": control,
        "help": help_text,
        "default": spec.get("default"),
        "schema_type": schema_type,
    }
    if ov.get("need"):
        field["need"] = True
    if ov.get("placeholder"):
        field["placeholder"] = ov["placeholder"]
    if ov.get("warn"):
        field["warn"] = ov["warn"]
    if ov.get("bool_labels"):
        field["boolLabels"] = list(ov["bool_labels"])
    sensitive = bool(ov.get("sensitive")) or key in SENSITIVE_UI_KEYS
    if sensitive:
        field["sensitive"] = True
        if control == "text":
            field["type"] = "password"

    show_if = ov.get("show_if")
    if isinstance(show_if, dict) and show_if.get("key") is not None:
        field["showIf"] = {"key": show_if["key"], "eq": show_if.get("eq")}

    option_meta = ov.get("option_meta") or {}
    if has_options:
        opts = []
        for val in options_raw:
            v = str(val)
            meta = option_meta.get(v) or {}
            opts.append(
                {
                    "value": v,
                    "title": meta.get("title") or v,
                    "desc": meta.get("desc") or "",
                }
            )
        field["options"] = opts

    return field


def export_config_schema() -> dict[str, Any]:
    """供 meta.config_schema：分组 + 已解析字段（前端可直接画表单）。"""
    conf = load_conf_schema()
    groups_out: list[dict[str, Any]] = []
    all_fields: list[dict[str, Any]] = []

    for g in GROUPS:
        fields = []
        for key in g.get("fields") or []:
            f = _resolve_field(str(key), conf)
            if f:
                fields.append(f)
                all_fields.append(f)
        advanced = None
        adv_in = g.get("advanced")
        if isinstance(adv_in, dict):
            adv_fields = []
            for key in adv_in.get("fields") or []:
                f = _resolve_field(str(key), conf)
                if f:
                    adv_fields.append(f)
                    all_fields.append(f)
            advanced = {
                "title": adv_in.get("title") or "高级",
                "note": adv_in.get("note") or "",
                "fields": adv_fields,
            }
        groups_out.append(
            {
                "id": g["id"],
                "title": g.get("title") or g["id"],
                "nav": g.get("nav") or g.get("title") or g["id"],
                "desc": g.get("desc") or "",
                "fields": fields,
                "advanced": advanced,
            }
        )

    return {
        "groups": groups_out,
        "defaults": schema_defaults(),
        # 扁平列表，供 save 时枚举 key（含 advanced）
        "field_keys": [f["key"] for f in all_fields],
    }


def export_config_schema_js_module() -> str:
    """生成前端 fallback 模块源码（本地无 bridge 时用）。"""
    data = export_config_schema()
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return (
        "/**\n"
        " * 由 webui_settings_schema.export_config_schema 生成 — 勿手改结构。\n"
        " * 重新生成: python -c \"from webui_settings_schema import export_config_schema_js_module; "
        "open('pages/console/settings_schema_fallback.js','w').write(export_config_schema_js_module())\"\n"
        " */\n"
        f"export const CONFIG_SCHEMA_FALLBACK = {body};\n"
    )
