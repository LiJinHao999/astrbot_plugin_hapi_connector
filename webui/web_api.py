"""AstrBot Plugin Pages 后端 API。

注册方式：main.__init__ 中调用 register_pages(plugin)（与官方 plugin-pages 示例一致）。
规范见 dev-docs/plugin-pages.md 与 dev-docs/webui开发计划.md。
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

from astrbot.api import logger

from ..render import formatters
from ..chat import flavor_profiles
PLUGIN_NAME = "astrbot_plugin_hapi_connector"

# 与 _conf_schema.json 对齐的可读写键
CONFIG_KEYS = (
    "hapi_endpoint",
    "access_token",
    "proxy_url",
    "cf_access_client_id",
    "cf_access_client_secret",
    "max_reconnect_attempts",
    "jwt_lifetime",
    "refresh_before_expiry",
    "output_level",
    "summary_msg_count",
    "quick_prefix",
    "poke_approve",
    "poke_action",
    "cmd_keyword_maps",
    "remind_pending",
    "remind_interval",
    "auto_approve_enabled",
    "auto_approve_start",
    "auto_approve_end",
    "default_notification_window",
    # 推送呈现（卡片可选依赖 Pillow / Playwright）
    "render_mode",
    "formula_mode",
    "render_kinds",
    "card_style_preset",
    "card_width",
    "card_accent",
    "card_bg",
    "card_fg",
    "card_font_scale",
    "card_density",
    "card_show_brand",
    "card_mono",
    "card_custom_css",
    "card_font_path",
)

SENSITIVE_KEYS = frozenset({"cf_access_client_secret"})  # access_token 按用户要求明文回显

# 改这些后需要重建 client / 重启 SSE
RECONNECT_KEYS = frozenset({
    "hapi_endpoint",
    "access_token",
    "proxy_url",
    "cf_access_client_id",
    "cf_access_client_secret",
    "jwt_lifetime",
    "refresh_before_expiry",
    "max_reconnect_attempts",
})

OUTPUT_LEVELS = ("silence", "simple", "summary", "detail")
RENDER_MODES = ("text", "card")
FORMULA_MODES = ("off", "detect", "formula_only", "plain")
CARD_PRESETS = ("terminal_light", "terminal_dark", "clean", "compact")
CARD_DENSITY = ("comfortable", "compact")

BOOL_KEYS = frozenset({
    "poke_approve",
    "remind_pending",
    "auto_approve_enabled",
    "card_show_brand",
    "card_mono",
})

INT_KEYS = frozenset({
    "max_reconnect_attempts",
    "jwt_lifetime",
    "refresh_before_expiry",
    "summary_msg_count",
    "remind_interval",
    "card_width",
    "card_font_scale",
})


def _command_catalog_safe() -> dict:
    try:
        from ..chat.keyword_maps import export_command_catalog

        return export_command_catalog()
    except Exception as e:
        logger.warning("command catalog failed: %s", e)
        return {"topics": [], "commands": []}


def register_pages(plugin) -> None:
    """在插件 Context 上注册全部 Page API。"""
    ctx = plugin.context
    prefix = f"/{PLUGIN_NAME}"
    api = WebApi(plugin)

    routes = [
        (f"{prefix}/meta", api.meta, ["GET"], "WebUI meta"),
        (f"{prefix}/overview", api.overview, ["GET"], "WebUI overview"),
        (f"{prefix}/config", api.get_config, ["GET"], "WebUI get config"),
        (f"{prefix}/config", api.post_config, ["POST"], "WebUI save config"),
        (f"{prefix}/help", api.help_data, ["GET"], "WebUI help"),
        (f"{prefix}/docs", api.docs_list, ["GET"], "WebUI docs list"),
        (f"{prefix}/docs/<doc_id>", api.docs_get, ["GET"], "WebUI doc body"),
        (f"{prefix}/machines", api.machines_list, ["GET"], "WebUI machines health"),
        (f"{prefix}/connection/wake", api.connection_wake, ["POST"], "WebUI wake SSE"),
        (f"{prefix}/connection/reconnect", api.connection_reconnect, ["POST"], "WebUI reconnect HAPI"),
        (f"{prefix}/sessions/snapshot", api.sessions_snapshot, ["GET"], "WebUI sessions snapshot"),
        (f"{prefix}/sessions/batch", api.sessions_batch, ["POST"], "WebUI batch lifecycle"),
        (f"{prefix}/codex/sync-session", api.codex_sync_session, ["POST"], "WebUI sync codex session"),
        (f"{prefix}/sessions/<sid>/permission", api.session_permission, ["POST"], "WebUI set permission"),
        (f"{prefix}/sessions/<sid>/bind", api.session_bind, ["POST"], "WebUI bind session"),
        (f"{prefix}/sessions/<sid>/lifecycle", api.session_lifecycle, ["POST"], "WebUI session lifecycle"),
        (f"{prefix}/sessions/<sid>", api.session_detail, ["GET"], "WebUI session detail"),
        (f"{prefix}/routes/primary", api.routes_primary, ["POST"], "WebUI set primary route"),
        (f"{prefix}/routes/flavor", api.routes_flavor, ["POST"], "WebUI set flavor route"),
        (f"{prefix}/ui/hidden-windows", api.get_hidden_windows, ["GET"], "WebUI hidden windows"),
        (f"{prefix}/ui/hidden-windows", api.post_hidden_windows, ["POST"], "WebUI save hidden windows"),
        (f"{prefix}/ui/session-templates", api.get_session_templates, ["GET"], "WebUI session templates"),
        (f"{prefix}/ui/session-templates", api.post_session_templates, ["POST"], "WebUI save session templates"),
        (f"{prefix}/windows/focus", api.post_window_focus, ["POST"], "WebUI toggle window focus mode"),
        (f"{prefix}/hub/launch", api.hub_launch, ["GET"], "WebUI HAPI Web launch URL"),
        (f"{prefix}/render/meta", api.render_meta, ["GET"], "WebUI render meta"),
        (f"{prefix}/render/preview", api.render_preview, ["POST"], "WebUI card preview"),
        (f"{prefix}/render/text-test", api.render_text_test, ["POST"], "WebUI text test send"),
        (f"{prefix}/render/install", api.render_install, ["POST"], "WebUI install font/deps"),
    ]
    for route, handler, methods, desc in routes:
        ctx.register_web_api(route, handler, methods, desc)
    logger.info("HAPI Connector WebUI API registered (%d routes)", len(routes))


class WebApi:
    """持有 plugin 引用的 handler 集合。"""

    def __init__(self, plugin):
        self.plugin = plugin

    # ──── handlers ────

    async def meta(self):
        from astrbot.api.web import error_response, json_response

        try:
            from ..render import card_render
            from ..chat.poke_actions import poke_actions_meta

            profiles = flavor_profiles.export_profiles_meta()
            try:
                render_meta = card_render.render_meta()
            except Exception as e:
                logger.warning("card_render.render_meta failed: %s", e)
                render_meta = {"engine": {"pillow": False}, "error": str(e)}
            try:
                from ..render import font_manager
                render_meta["installable"] = font_manager.installable_items()
            except Exception as e:
                logger.warning("font_manager installable failed: %s", e)
                render_meta.setdefault("installable", [])

            try:
                from .webui_settings_schema import export_config_schema

                config_schema = export_config_schema()
            except Exception as e:
                logger.warning("config_schema export failed: %s", e)
                config_schema = {"groups": [], "defaults": {}, "field_keys": []}

            return json_response({
                "plugin_name": PLUGIN_NAME,
                "plugin_version": _plugin_version(self.plugin),
                "output_levels": list(OUTPUT_LEVELS),
                "render": render_meta,
                "poke_actions": poke_actions_meta(),
                "command_catalog": _command_catalog_safe(),
                "config_schema": config_schema,
                **profiles,
            })
        except Exception as e:
            logger.exception("WebUI meta failed")
            return error_response(f"meta 失败: {type(e).__name__}: {e}", status_code=500)

    async def render_meta(self):
        from astrbot.api.web import error_response, json_response

        try:
            from ..render import card_render
            meta = card_render.render_meta()
            try:
                from ..render import font_manager
                meta["installable"] = font_manager.installable_items()
            except Exception as e:
                logger.warning("render_meta font_manager: %s", e)
                meta["installable"] = []
            return json_response(meta)
        except Exception as e:
            logger.exception("WebUI render_meta failed")
            return error_response(f"render/meta 失败: {type(e).__name__}: {e}", status_code=500)

    async def render_text_test(self):
        """从 WebUI 测试不同消息输出链路。

        mode:
        - direct_window:
            使用 context.send_message 主动发送到指定窗口
        - bound_plain:
            使用 NotificationManager，按照 Session 绑定路由推送纯文本
        - sse_message:
            模拟 SSE Agent 消息呈现链路，遵循 render_mode/render_kinds
        - command_reply:
            模拟 /hapi msg，使用缓存事件调用 cmd_msg
        """
        from astrbot.api.event import MessageChain
        from astrbot.api.web import error_response, json_response, request

        allowed_modes = {
            "command_reply",
            "direct_window",
            "bound_plain",
            "sse_message",
        }

        mode_labels = {
            "direct_window": "直接主动发送链路",
            "bound_plain": "绑定路由纯文本通知链路",
            "sse_message": "SSE Agent 消息呈现链路",
            "command_reply": "/hapi msg 命令回复链路",
        }

        try:
            # ------------------------------------------------------------
            # 1. 读取请求参数
            # ------------------------------------------------------------
            payload = await request.json(default={})

            if not isinstance(payload, dict):
                return error_response(
                    "请求体必须是 JSON 对象",
                    status_code=400,
                )

            mode = str(
                payload.get("mode") or "command_reply"
            ).strip().lower()

            text = str(payload.get("text") or "")
            umo = str(payload.get("umo") or "").strip()
            sid = str(payload.get("sid") or "").strip()
            rounds = str(payload.get("rounds") or "1").strip()

            if mode not in allowed_modes:
                return error_response(
                    f"未知发送方式: {mode}",
                    status_code=400,
                )

            # command_reply 实际调用 /hapi msg，不直接使用 text。
            if mode != "command_reply":
                if not text.strip():
                    return error_response(
                        "测试内容不能为空",
                        status_code=400,
                    )

                if len(text) > 20000:
                    return error_response(
                        "测试内容过长，最多 20000 个字符",
                        status_code=400,
                    )

            # ------------------------------------------------------------
            # 2. 获取窗口、Session 和插件组件
            # ------------------------------------------------------------
            snap = build_sessions_snapshot(self.plugin)

            window_options = snap.get("window_options") or []
            session_options = snap.get("sessions") or []

            allowed_umos = {
                str(item.get("umo") or "").strip()
                for item in window_options
                if isinstance(item, dict)
                and str(item.get("umo") or "").strip()
            }

            allowed_sids = {
                str(item.get("id") or "").strip()
                for item in session_options
                if isinstance(item, dict)
                and str(item.get("id") or "").strip()
            }

            notification_mgr = getattr(
                self.plugin,
                "notification_mgr",
                None,
            )

            state_mgr = getattr(
                self.plugin,
                "state_mgr",
                None,
            )

            sessions_cache = list(
                getattr(self.plugin, "sessions_cache", None) or []
            )

            # ------------------------------------------------------------
            # 3. 校验目标窗口或 Session
            # ------------------------------------------------------------
            if mode in {"command_reply", "direct_window"}:
                if not umo:
                    return error_response(
                        "该发送方式需要选择目标窗口",
                        status_code=400,
                    )

                if umo not in allowed_umos:
                    return error_response(
                        "目标窗口不在当前可用窗口列表中，请刷新页面后重试",
                        status_code=400,
                    )

            if mode in {"bound_plain", "sse_message"}:
                if not sid:
                    return error_response(
                        "该发送方式需要选择 HAPI Session",
                        status_code=400,
                    )

                if sid not in allowed_sids:
                    return error_response(
                        "目标 Session 不在当前会话列表中，请刷新页面后重试",
                        status_code=400,
                    )

                if state_mgr is None:
                    return error_response(
                        "state_mgr 尚未初始化",
                        status_code=503,
                    )

            # ------------------------------------------------------------
            # 4. 准备文本分片
            # ------------------------------------------------------------
            if mode == "command_reply":
                # command_reply 不直接发送 text。
                chunks = []

            elif notification_mgr is not None:
                chunks = notification_mgr.split_message(
                    text,
                    max_len=4200,
                )

            else:
                chunks = [
                    text[index:index + 4200]
                    for index in range(0, len(text), 4200)
                ]

            sent = 0
            target_desc = umo or sid

            # ------------------------------------------------------------
            # 5. 直接主动发送到指定窗口
            # ------------------------------------------------------------
            if mode == "direct_window":
                context = getattr(
                    self.plugin,
                    "context",
                    None,
                )

                if context is None:
                    return error_response(
                        "插件 context 尚未初始化",
                        status_code=503,
                    )

                for chunk in chunks:
                    chain = MessageChain().message(chunk)
                    await context.send_message(umo, chain)
                    sent += 1

            # ------------------------------------------------------------
            # 6. 按 Session 绑定路由推送纯文本
            # ------------------------------------------------------------
            elif mode == "bound_plain":
                if notification_mgr is None:
                    return error_response(
                        "notification_mgr 尚未初始化",
                        status_code=503,
                    )

                targets = state_mgr.select_notification_targets(
                    sid,
                    sessions_cache,
                )

                if not targets:
                    return error_response(
                        "该 Session 当前没有绑定窗口、"
                        "Agent 默认路由或全局默认窗口",
                        status_code=409,
                    )

                await notification_mgr.push_notification(
                    text,
                    sid,
                    sessions_cache,
                )

                sent = len(chunks)
                target_desc = ", ".join(
                    map(str, targets)
                )

            # ------------------------------------------------------------
            # 7. 模拟 SSE Agent 消息呈现链路
            # ------------------------------------------------------------
            elif mode == "sse_message":
                listener = getattr(
                    self.plugin,
                    "sse_listener",
                    None,
                )

                push_card = (
                    getattr(listener, "_push_message_card", None)
                    if listener is not None
                    else None
                )

                if not callable(push_card):
                    return error_response(
                        "SSE 消息呈现链路尚未初始化",
                        status_code=503,
                    )

                session = next(
                    (
                        item
                        for item in sessions_cache
                        if isinstance(item, dict)
                        and str(item.get("id") or "") == sid
                    ),
                    None,
                )

                label = formatters.session_label_short(
                    sid,
                    sessions_cache,
                )

                title = (
                    formatters.get_session_title(session)
                    if session
                    else "WebUI 消息测试"
                )

                targets = state_mgr.select_notification_targets(
                    sid,
                    sessions_cache,
                )

                if not targets:
                    return error_response(
                        "该 Session 当前没有可用推送路由",
                        status_code=409,
                    )

                await push_card(
                    session_id=sid,
                    label=label,
                    body=text,
                    fallback_text=f"{label}\n{text}",
                    title=title,
                    footer="WebUI 测试",
                )

                sent = 1
                target_desc = ", ".join(
                    map(str, targets)
                )

            # ------------------------------------------------------------
            # 8. 模拟 /hapi msg 命令回复链路
            # ------------------------------------------------------------
            elif mode == "command_reply":
                if notification_mgr is None:
                    return error_response(
                        "notification_mgr 尚未初始化",
                        status_code=503,
                    )

                event_cache = getattr(
                    notification_mgr,
                    "_event_cache",
                    None,
                )

                if not isinstance(event_cache, dict):
                    logger.warning(
                        "notification_mgr._event_cache 类型异常: %s",
                        type(event_cache).__name__,
                    )

                    return error_response(
                        "事件缓存尚未初始化",
                        status_code=503,
                    )

                cached_event = event_cache.get(umo)

                if cached_event is None:
                    logger.info(
                        "WebUI command_reply 无缓存事件: "
                        "umo=%r cache_keys=%r",
                        umo,
                        list(event_cache.keys()),
                    )

                    return error_response(
                        "该窗口暂无缓存事件。请先在目标窗口执行一次 "
                        "/hapi msg 或触发一次快捷前缀命令，再重试。",
                        status_code=409,
                    )

                cmd_handlers = getattr(
                    self.plugin,
                    "cmd_handlers",
                    None,
                )

                cmd_msg = (
                    getattr(cmd_handlers, "cmd_msg", None)
                    if cmd_handlers is not None
                    else None
                )

                if not callable(cmd_msg):
                    return error_response(
                        "cmd_msg 命令处理器尚未初始化",
                        status_code=503,
                    )

                logger.info(
                    "开始模拟 /hapi msg: "
                    "umo=%r rounds=%r event_type=%s",
                    umo,
                    rounds,
                    type(cached_event).__name__,
                )

                # cmd_msg 内部使用了 yield，因此它是异步生成器。
                async for result in cmd_msg(
                    cached_event,
                    rounds,
                ):
                    if result is None:
                        continue

                    await cached_event.send(result)
                    sent += 1

            # ------------------------------------------------------------
            # 9. 返回测试结果
            # ------------------------------------------------------------
            logger.info(
                "WebUI 消息测试成功: "
                "mode=%s target=%s text_chars=%d sent=%d",
                mode,
                str(target_desc)[:120],
                len(text),
                sent,
            )

            return json_response({
                "ok": True,
                "mode": mode,
                "mode_label": mode_labels.get(mode, mode),
                "target": target_desc,
                "chars": len(text),
                "chunks": sent,
                "message": "测试消息已提交",
            })

        except Exception as exc:
            logger.exception(
                "WebUI render_text_test failed: "
                "mode=%r umo=%r sid=%r",
                locals().get("mode"),
                locals().get("umo"),
                locals().get("sid"),
            )

            return error_response(
                "render/text-test 失败: "
                f"{type(exc).__name__}: {exc}",
                status_code=500,
            )

    async def render_install(self):
        """WebUI 手动安装：字体下载到 assets/fonts/，依赖 pip install Pillow。

        参考 self_learning 的「点按钮再装」：必须显式 POST，不自动执行。
        body: { "ids": ["font_noto_sc","dep_pillow"], "force": false }
        """
        import asyncio
        from astrbot.api.web import error_response, json_response, request
        from ..render import card_render
        from ..render import font_manager
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体必须是对象", status_code=400)

        ids = payload.get("ids") or payload.get("items") or []
        if isinstance(ids, str):
            ids = [x.strip() for x in ids.replace("，", ",").split(",") if x.strip()]
        if not isinstance(ids, list) or not ids:
            return error_response("请勾选要安装的项（ids 数组）", status_code=400)

        force = bool(payload.get("force"))
        id_list = [str(x).strip() for x in ids if str(x).strip()]

        def _run():
            return font_manager.install_selected(id_list, force_font=force)

        try:
            result = await asyncio.to_thread(_run)
        except Exception as e:
            logger.exception("render install failed")
            return error_response(f"安装失败: {type(e).__name__}: {e}", status_code=500)

        try:
            result["engine"] = card_render.engine_status(
                user_font_path=str(
                    _cfg_get(getattr(self.plugin, "config", None), "card_font_path", "")
                    or ""
                )
                or None
            )
        except Exception as e:
            logger.warning("engine_status after install: %s", e)
            result["engine"] = {"pillow": False}

        # 兼容前端：success / output 字段
        result.setdefault("success", bool(result.get("ok")))
        result.setdefault("output", "\n".join(result.get("log") or []))
        return json_response(result)

    async def render_preview(self):
        """按当前或请求内样式生成结构卡/对话卡 PNG（base64）。"""
        import base64
        from astrbot.api.web import error_response, json_response, request
        from ..render import card_render
        try:
            payload = await request.json(default={})
            if not isinstance(payload, dict):
                return error_response("请求体必须是对象", status_code=400)

            kind = str(payload.get("kind") or "session_list").strip()
            if kind not in card_render.CARD_KINDS:
                return error_response(
                    f"kind 必须是 {'/'.join(card_render.CARD_KINDS)}", status_code=400
                )

            cfg_view = dict(public_config(self.plugin))
            style_patch = payload.get("style")
            if isinstance(style_patch, dict):
                alias = {
                    "preset": "card_style_preset",
                    "width": "card_width",
                    "accent": "card_accent",
                    "bg": "card_bg",
                    "fg": "card_fg",
                    "font_scale": "card_font_scale",
                    "density": "card_density",
                    "show_brand": "card_show_brand",
                    "mono": "card_mono",
                    "custom_css": "card_custom_css",
                    "font_path": "card_font_path",
                }
                for k, v in style_patch.items():
                    mapped = alias.get(k, k)
                    if mapped in CONFIG_KEYS or k in CONFIG_KEYS:
                        cfg_view[mapped] = v

            if payload.get("card_custom_css") is not None:
                cfg_view["card_custom_css"] = payload.get("card_custom_css")
            if payload.get("card_font_path") is not None:
                cfg_view["card_font_path"] = payload.get("card_font_path")

            formula_mode = str(
                payload.get("formula_mode")
                or cfg_view.get("formula_mode")
                or "off"
            ).strip()
            style = card_render.style_from_config(cfg_view)
            data = payload.get("data")
            if not isinstance(data, dict):
                data = card_render.sample_payload(kind)

            result = card_render.render_card(
                kind, data, style, formula_mode=formula_mode
            )
            body: dict[str, Any] = {
                "ok": result.ok,
                "kind": result.kind,
                "engine": result.engine,
                "ms": round(result.ms, 1),
                "error": result.error,
                "fallback_text": result.fallback_text,
                "font_path": result.font_path,
                "engine_status": card_render.engine_status(),
                "style": card_render.style_to_public(style),
            }
            if result.ok and result.png:
                body.update({
                    "mime": result.mime,
                    "width": result.width,
                    "height": result.height,
                    "bytes": result.bytes_len,
                    "png_base64": base64.b64encode(result.png).decode("ascii"),
                })
            return json_response(body)
        except Exception as e:
            logger.exception("WebUI render_preview failed")
            return error_response(
                f"render/preview 失败: {type(e).__name__}: {e}", status_code=500
            )

    async def overview(self):
        from astrbot.api.web import error_response, json_response, request

        try:
            # 默认读缓存；?fresh=1 才强制拉 HAPI。绝不 wake SSE。
            force = _query_truthy(request, "fresh")
            await soft_refresh_sessions(self.plugin, force=force)
            await ensure_umo_name_map(self.plugin, force=force)
            machines = await soft_refresh_machines(self.plugin, force=force)
            snap = build_sessions_snapshot(self.plugin)
            return json_response({
                "connection": snap["connection"],
                "metrics": snap["metrics"],
                "machines": machines,
                "config": snap.get("config") or public_config(self.plugin),
                "plugin_version": _plugin_version(self.plugin),
                "cache": snap.get("cache"),
            })
        except Exception as e:
            logger.exception("WebUI overview failed")
            return error_response(f"overview 失败: {type(e).__name__}: {e}", status_code=500)

    async def machines_list(self):
        from astrbot.api.web import error_response, json_response, request

        try:
            force = _query_truthy(request, "fresh")
            machines = await soft_refresh_machines(self.plugin, force=force)
            cache_ts = float(getattr(self.plugin, "_machines_cache_ts", 0) or 0)
            import time

            age = (time.monotonic() - cache_ts) if cache_ts else None
            return json_response({
                "machines": machines,
                "cache": {
                    "age_sec": None if age is None else round(age, 1),
                    "refresh_ttl_sec": MACHINES_REFRESH_TTL,
                },
            })
        except Exception as e:
            logger.exception("WebUI machines failed")
            return error_response(f"machines 失败: {type(e).__name__}: {e}", status_code=500)

    async def get_config(self):
        from astrbot.api.web import error_response, json_response

        try:
            return json_response({"config": public_config(self.plugin)})
        except Exception as e:
            logger.exception("WebUI get_config failed")
            return error_response(f"读取配置失败: {type(e).__name__}: {e}", status_code=500)

    async def post_config(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象", status_code=400)

        try:
            result = await save_plugin_config(self.plugin, payload)
        except ConfigValidationError as e:
            return error_response(str(e), status_code=400)
        except Exception as e:
            logger.exception("WebUI save config failed")
            return error_response(f"保存失败: {e}", status_code=500)

        return json_response(result)

    async def help_data(self):
        from astrbot.api.web import json_response

        return json_response(formatters.export_help_data())

    async def docs_list(self):
        from astrbot.api.web import json_response

        from .docs_content import list_docs

        return json_response(list_docs())

    async def docs_get(self, doc_id: str):
        from astrbot.api.web import error_response, json_response

        from .docs_content import get_doc

        doc = get_doc(doc_id)
        if not doc:
            return error_response(f"文档不存在: {doc_id}", status_code=404)
        return json_response(doc)

    async def connection_wake(self):
        from astrbot.api.web import error_response, json_response

        try:
            sse = self.plugin.sse_listener
            was = bool(getattr(sse, "_hibernated", False))
            sse.wake_up()
            return json_response({
                "woken": was,
                "connection": connection_view(self.plugin),
            })
        except Exception as e:
            logger.exception("WebUI wake failed")
            return error_response(f"唤醒失败: {type(e).__name__}: {e}", status_code=500)

    async def sessions_snapshot(self):
        from astrbot.api.web import error_response, json_response, request

        try:
            force = _query_truthy(request, "fresh")
            await soft_refresh_sessions(self.plugin, force=force)
            await ensure_umo_name_map(self.plugin, force=force)
            # 机器负载与 session 同 TTL 节流刷新，供概览页展示
            await soft_refresh_machines(self.plugin, force=force)
            return json_response(build_sessions_snapshot(self.plugin))
        except Exception as e:
            logger.exception("WebUI sessions_snapshot failed")
            # 降级：尽量返回连接 + 空会话，避免前端整页 mock 默认值
            try:
                partial = {
                    "connection": connection_view(self.plugin),
                    "metrics": {
                        "active": 0, "thinking": 0, "pending": 0, "unrouted": 0, "total": 0,
                    },
                    "sessions": [],
                    "machines": list(getattr(self.plugin, "machines_cache", None) or []),
                    "columns": [],
                    "defaults": {
                        "primary": None, "flavor": {}, "writable": False,
                        "writable_reason": f"snapshot 失败: {e}",
                        "known_user_count": 0,
                    },
                    "window_options": [],
                    "hidden_windows": (
                        getattr(self.plugin, "state_mgr", None).get_webui_hidden_windows()
                        if getattr(self.plugin, "state_mgr", None)
                        else []
                    ),
                    "config": public_config(self.plugin),
                    "plugin_version": _plugin_version(self.plugin),
                    "error": f"{type(e).__name__}: {e}",
                    "cache": {"from_memory": True, "sessions_age_sec": None, "refresh_ttl_sec": SESSIONS_REFRESH_TTL},
                }
                return json_response(partial)
            except Exception as e2:
                return error_response(
                    f"sessions/snapshot 失败: {type(e).__name__}: {e}; fallback: {e2}",
                    status_code=500,
                )

    async def session_detail(self, sid: str):
        from astrbot.api.web import error_response, json_response
        from ..core import session_ops
        sid = (sid or "").strip()
        if not sid:
            return error_response("缺少 session id", status_code=400)
        try:
            detail = await session_ops.fetch_session_detail(self.plugin.client, sid)
        except Exception as e:
            logger.warning("WebUI session detail failed: %s", e)
            return error_response(f"获取详情失败: {e}", status_code=502)

        snap = build_sessions_snapshot(self.plugin)
        row = next((s for s in snap["sessions"] if s["id"] == sid or s["id"].startswith(sid)), None)
        flavor = (row or {}).get("flavor") or (detail.get("metadata") or {}).get("flavor") or "unknown"
        modes = flavor_profiles.permission_modes_for(flavor)
        return json_response({
            "session": row,
            "detail": detail,
            "permission_modes": modes,
            "allows_any_permission_mode": flavor_profiles.allows_any_permission_mode(flavor),
        })

    async def session_permission(self, sid: str):
        from astrbot.api.web import error_response, json_response, request
        from ..core import session_ops
        sid = (sid or "").strip()
        payload = await request.json(default={})
        mode = str((payload or {}).get("mode") or "").strip()
        if not sid or not mode:
            return error_response("需要 sid 与 mode", status_code=400)

        session = _find_session(self.plugin, sid)
        if not session:
            return error_response("session 不存在", status_code=404)
        flavor = str((session.get("metadata") or {}).get("flavor") or "").strip().lower() or "unknown"
        if flavor_profiles.profile_for(flavor).permission_modes is not None and not flavor_profiles.permission_modes_for(flavor):
            return error_response(f"{flavor} 不支持运行时权限切换", status_code=400)
        if not flavor_profiles.allows_any_permission_mode(flavor) and not flavor_profiles.is_permission_mode_allowed(flavor, mode):
            modes = flavor_profiles.permission_modes_for(flavor)
            return error_response(f"无效权限模式: {mode}（可用: {', '.join(modes)}）", status_code=400)

        try:
            ok, msg = await session_ops.set_permission_mode(self.plugin.client, sid, mode)
        except Exception as e:
            logger.exception("set permission failed")
            return error_response(f"切换失败: {e}", status_code=502)
        if not ok:
            return error_response(msg, status_code=502)
        try:
            await self.plugin._refresh_sessions()
        except Exception:
            pass
        return json_response({"ok": True, "message": msg, "session": _session_row(self.plugin, sid)})

    async def session_bind(self, sid: str):
        from astrbot.api.web import error_response, json_response, request

        sid = (sid or "").strip()
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体必须是对象", status_code=400)
        if "umo" not in payload:
            return error_response("需要 umo 字段（string 或 null）", status_code=400)
        umo = payload.get("umo")
        if umo is not None:
            umo = str(umo).strip() or None

        session = _find_session(self.plugin, sid)
        if not session:
            # 仍允许解绑已不在列表中的 id
            if umo is not None:
                return error_response("session 不存在", status_code=404)

        try:
            if umo is None:
                await self.plugin.state_mgr.unbind_session(sid)
                message = "已解绑，通知将按推送设置投递"
            else:
                if len(umo) > 256 or ".." in umo:
                    return error_response("非法 umo", status_code=400)
                flavor = "unknown"
                if session:
                    flavor = str((session.get("metadata") or {}).get("flavor") or "unknown")
                await self.plugin.state_mgr.capture_window(sid, umo, flavor)
                message = f"已绑定到 {window_display_title(umo)}"
        except Exception as e:
            logger.exception("bind session failed")
            return error_response(f"绑定失败: {e}", status_code=500)

        return json_response({
            "ok": True,
            "message": message,
            "session": _session_row(self.plugin, sid),
            "snapshot": build_sessions_snapshot(self.plugin),
        })

    async def session_lifecycle(self, sid: str):
        from astrbot.api.web import error_response, json_response, request

        sid = (sid or "").strip()
        payload = await request.json(default={})
        action = str((payload or {}).get("action") or "").strip().lower()
        if action not in ("resume", "archive", "delete", "abort"):
            return error_response("action 必须是 resume|archive|delete|abort", status_code=400)

        try:
            result = await run_lifecycle(self.plugin, sid, action)
        except LifecycleError as e:
            return error_response(str(e), status_code=e.status)
        except Exception as e:
            logger.exception("lifecycle failed")
            return error_response(f"操作失败: {e}", status_code=502)

        return json_response(result)

    async def sessions_batch(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体必须是对象", status_code=400)
        ids = payload.get("ids") or []
        action = str(payload.get("action") or "").strip().lower()
        if action not in ("resume", "archive", "delete", "abort"):
            return error_response("action 必须是 resume|archive|delete|abort", status_code=400)
        if not isinstance(ids, list) or not ids:
            return error_response("ids 必须是非空数组", status_code=400)
        if len(ids) > 50:
            return error_response("单次最多 50 个 session", status_code=400)

        results = []
        for raw in ids:
            sid = str(raw or "").strip()
            if not sid:
                results.append({"id": raw, "ok": False, "message": "空 id"})
                continue
            try:
                r = await run_lifecycle(self.plugin, sid, action)
                results.append({
                    "id": sid,
                    "ok": True,
                    "message": r.get("message") or f"{action} 成功",
                    **{k: v for k, v in r.items() if k not in ("snapshot", "ok", "message")},
                })
            except LifecycleError as e:
                results.append({"id": sid, "ok": False, "message": str(e)})
            except Exception as e:
                logger.exception("batch lifecycle %s failed for %s", action, sid)
                results.append({"id": sid, "ok": False, "message": f"{type(e).__name__}: {e}"})

        # 批量结束后强制刷新一次，避免 snapshot 仍是旧 active 状态
        try:
            await self.plugin._refresh_sessions()
        except Exception as e:
            logger.warning("batch refresh after lifecycle failed: %s", e)

        ok_n = sum(1 for r in results if r.get("ok"))
        fail_n = len(results) - ok_n
        if fail_n == 0:
            msg = f"{action} 成功 {ok_n}/{len(results)}"
        elif ok_n == 0:
            first = next((r.get("message") for r in results if not r.get("ok")), "失败")
            msg = f"{action} 全部失败 0/{len(results)} · {first}"
        else:
            fails = [f"{(r.get('id') or '')[:8]}: {r.get('message')}" for r in results if not r.get("ok")]
            msg = f"{action} 完成 {ok_n}/{len(results)}，失败 {fail_n} · " + "；".join(fails[:3])

        return json_response({
            "ok": fail_n == 0,
            "action": action,
            "results": results,
            "message": msg,
            "snapshot": build_sessions_snapshot(self.plugin),
        })

    async def codex_sync_session(self):
        """WebUI 同步 Codex Session 到 HAPI（POST /api/codex/sync-session）。

        - 缓存找不到时先刷新一次，仍无则 404
        - 从 Session 对象/metadata 补充 machineId、cwd
        - 成功刷新 sessions_cache；失败透传 HAPI 原始错误
        - 运行中会话被 HAPI 拒绝时原样展示冲突信息，不强制同步
        """
        from astrbot.api.web import error_response, json_response, request
        from ..core import session_ops

        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象", status_code=400)

        session_id = str((payload.get("sessionId") or "").strip())
        if not session_id:
            return error_response("需要选择 Session（sessionId 不能为空）", status_code=400)

        # 1. 从缓存查找（支持前缀）；找不到先刷新一次再查
        session = _find_session(self.plugin, session_id)
        if session is None:
            try:
                await self.plugin._refresh_sessions()
            except Exception as e:
                logger.warning("sync codex pre-refresh failed: %s", e)
            session = _find_session(self.plugin, session_id)
        if session is None:
            return error_response(f"Session 不存在: {session_id[:16]}", status_code=404)
        sid = str(session.get("id") or session_id)

        # 2. 从 Session 对象 / metadata 补充 machineId、cwd（请求体显式值优先）
        meta = session.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        machine_id = (
            payload.get("machineId") or session.get("machineId")
            or session.get("machine_id") or meta.get("machineId")
            or meta.get("machine_id") or None
        )
        cwd = (
            payload.get("cwd") or session.get("cwd")
            or meta.get("cwd") or meta.get("path")
            or meta.get("workingDirectory") or None
        )

        # 3. 调用底层同步
        try:
            result = await session_ops.sync_codex_session(
                self.plugin.client,
                sid,
                machine_id=machine_id,
                cwd=cwd,
                model=payload.get("model"),
                model_reasoning_effort=payload.get("modelReasoningEffort"),
                service_tier=payload.get("serviceTier"),
                collaboration_mode=payload.get("collaborationMode") or "default",
                yolo=bool(payload.get("yolo") or False),
            )
        except session_ops.SyncCodexError as e:
            logger.warning("sync codex session failed sid=%s: %s", sid[:8], e)
            status = e.status if isinstance(e.status, int) and 400 <= e.status < 600 else 502
            msg = str(e)
            if e.status != 409:
                msg += "\n详情请查看 HAPI 日志。"
            return error_response(msg, status_code=status)
        except Exception as e:
            logger.exception("sync codex session unexpected error sid=%s", sid[:8])
            return error_response(f"同步失败: {type(e).__name__}: {e}", status_code=502)

        # 4. 成功后刷新缓存；刷新失败不误报同步失败
        refresh_failed = False
        try:
            await self.plugin._refresh_sessions()
        except Exception as e:
            refresh_failed = True
            logger.warning("sync codex session refresh after success failed: %s", e)

        synced = result.get("syncedCount") if isinstance(result, dict) else None
        if synced:
            message = f"Codex Session 同步完成：已导入 {synced} 个会话"
        else:
            message = "Codex Session 同步完成"
        if refresh_failed:
            message += "（Session 列表刷新失败，请稍后手动刷新）"
        return json_response({
            "ok": True,
            "sessionId": sid,
            "message": message,
            "result": result,
        })

    async def routes_primary(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        umo = payload.get("umo")
        if umo is not None:
            umo = str(umo).strip() or None
        try:
            result = await set_primary_route(self.plugin, umo, payload.get("user_id"))
        except LifecycleError as e:
            return error_response(str(e), status_code=e.status)
        return json_response(result)

    async def routes_flavor(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        flavor = str(payload.get("flavor") or "").strip().lower()
        if not flavor:
            return error_response("需要 flavor", status_code=400)
        umo = payload.get("umo")
        if umo is not None:
            umo = str(umo).strip() or None
        try:
            result = await set_flavor_route(self.plugin, flavor, umo, payload.get("user_id"))
        except LifecycleError as e:
            return error_response(str(e), status_code=e.status)
        return json_response(result)

    async def get_hidden_windows(self):
        """WebUI 会话页「管理可见窗口」隐藏列表（读 AstrBot KV）。"""
        from astrbot.api.web import json_response

        sm = getattr(self.plugin, "state_mgr", None)
        hidden = []
        if sm is not None:
            try:
                hidden = sm.get_webui_hidden_windows()
            except Exception:
                hidden = list(getattr(sm, "_webui_hidden_windows", None) or [])
        return json_response({"hidden": list(hidden or [])})

    async def post_hidden_windows(self):
        """保存 WebUI 隐藏窗口列表到 AstrBot KV（iframe 无 localStorage）。"""
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象", status_code=400)
        raw = payload.get("hidden")
        if raw is None:
            raw = payload.get("umos")
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            return error_response("hidden 须为字符串数组", status_code=400)

        sm = getattr(self.plugin, "state_mgr", None)
        if sm is None:
            return error_response("StateManager 未初始化", status_code=503)
        try:
            hidden = await sm.set_webui_hidden_windows(raw)
        except Exception as e:
            logger.exception("save webui_hidden_windows failed")
            return error_response(f"保存失败: {type(e).__name__}: {e}", status_code=500)
        return json_response({
            "ok": True,
            "hidden": hidden,
            "message": f"已隐藏 {len(hidden)} 个窗口" if hidden else "已全部显示",
        })

    async def get_session_templates(self):
        """会话创建模板列表（读 AstrBot KV，聊天 /hapi create <模板名> 同源）。"""
        from astrbot.api.web import json_response

        sm = getattr(self.plugin, "state_mgr", None)
        templates = []
        if sm is not None:
            try:
                templates = sm.get_session_templates()
            except Exception:
                templates = list(getattr(sm, "_session_templates", None) or [])
        return json_response({"templates": templates})

    async def post_session_templates(self):
        """保存会话创建模板（全量覆盖，normalize 后落盘并返回清洗结果）。"""
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象", status_code=400)
        raw = payload.get("templates")
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            return error_response("templates 须为对象数组", status_code=400)

        sm = getattr(self.plugin, "state_mgr", None)
        if sm is None:
            return error_response("StateManager 未初始化", status_code=503)
        try:
            templates = await sm.set_session_templates(raw)
        except Exception as e:
            logger.exception("save session_templates failed")
            return error_response(f"保存失败: {type(e).__name__}: {e}", status_code=500)

        dropped = len(raw) - len(templates)
        msg = f"已保存 {len(templates)} 个模板"
        if dropped > 0:
            msg += f"（{dropped} 条因名称为空/重复或代理不可创建被忽略）"
        return json_response({"ok": True, "templates": templates, "message": msg})

    async def post_window_focus(self):
        """切换某窗口的 Focus 模式（与聊天 /hapi focus on|off 同源状态）。"""
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象", status_code=400)
        umo = str(payload.get("umo") or "").strip()
        if not umo:
            return error_response("缺少 umo", status_code=400)
        enabled = bool(payload.get("enabled"))

        binding = getattr(self.plugin, "binding_mgr", None)
        sm = getattr(self.plugin, "state_mgr", None)
        if binding is None or sm is None:
            return error_response("插件尚未初始化完成", status_code=503)

        if enabled and not binding.get_window_session(umo):
            return error_response(
                "该窗口还没有选中的 session。请先在该聊天窗口里 /hapi sw 选择"
                "（或直接发 /hapi focus on，聊天侧支持回退默认窗口的 session）",
                status_code=409,
            )

        # 关闭一个本就无状态的窗口：直接返回，避免创建垃圾 window_state
        if not enabled and umo not in getattr(binding, "_window_states", {}):
            return json_response({"ok": True, "umo": umo, "enabled": False,
                                  "message": "Focus 模式已关闭"})

        try:
            binding.set_window_focus_mode(umo, enabled)
            await sm.persist_window_state(umo)
            if not enabled:
                clear_fn = getattr(self.plugin, "_clear_staged_attachments", None)
                if callable(clear_fn):
                    clear_fn(umo)
        except Exception as e:
            logger.exception("set window focus failed")
            return error_response(f"保存失败: {type(e).__name__}: {e}", status_code=500)

        result = {
            "ok": True,
            "umo": umo,
            "enabled": enabled,
            "message": (
                "此聊天窗口的 Focus 模式已开启。"
                "当前窗口文字消息、附件、图片等消息将会自动发送到 Hapi agent。"
            )
            if enabled
            else "Focus 模式已关闭",
        }
        try:
            result["snapshot"] = build_sessions_snapshot(self.plugin)
        except Exception:
            pass
        return json_response(result)

    async def connection_reconnect(self):
        from astrbot.api.web import error_response, json_response

        try:
            result = await reconnect_hapi(self.plugin)
        except Exception as e:
            logger.exception("reconnect failed")
            return error_response(f"重连失败: {e}", status_code=500)
        return json_response(result)

    async def hub_launch(self):
        """生成 HAPI 官方 Web 的启动 URL（可选带 ?token= 自动登录）。

        HAPI Web（useAuthSource）支持 query：
        - token: access token（会写入 localStorage 并从地址栏剥离）
        - hub: 可选 Hub 源（同源部署时一般不需要）
        """
        from astrbot.api.web import error_response, json_response, request
        from urllib.parse import urlencode, urljoin

        endpoint = str(self.plugin.config.get("hapi_endpoint") or "").strip()
        token = str(self.plugin.config.get("access_token") or "").strip()
        autologin = _query_truthy(request, "autologin") if request.query.get("autologin") is not None else True
        # path: 仅允许站内相对路径，默认打开会话列表根
        raw_path = str(request.query.get("path") or "/").strip() or "/"
        if not raw_path.startswith("/"):
            raw_path = "/" + raw_path
        if ".." in raw_path or "\\" in raw_path or raw_path.startswith("//"):
            return error_response("非法 path", status_code=400)

        if not endpoint:
            return error_response(
                "未配置 hapi_endpoint。请先在设置中填写 HAPI 地址。",
                status_code=400,
            )

        base = endpoint.rstrip("/")
        try:
            parsed = urlparse(base if "://" in base else f"http://{base}")
        except Exception:
            return error_response("hapi_endpoint 无法解析为合法 URL", status_code=400)

        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return error_response("hapi_endpoint 须为 http(s)://host[:port]", status_code=400)

        origin = f"{parsed.scheme}://{parsed.netloc}"
        if raw_path == "/":
            page = origin + "/"
        else:
            # urljoin 保证 path 拼在 origin 下
            page = urljoin(origin + "/", raw_path.lstrip("/"))

        warnings: list[str] = []
        host = (parsed.hostname or "").lower()
        loopback = host in ("127.0.0.1", "localhost", "::1") or host.startswith("127.")
        if loopback:
            warnings.append(
                "当前地址是本机回环（127.0.0.1/localhost）。"
                "iframe 由你的浏览器加载：仅当浏览器所在机器也能访问该地址时才能打开。"
                "若 AstrBot 在服务器、HAPI 在另一台电脑，请把 endpoint 改成浏览器可达的局域网/域名。"
            )
        if not token:
            warnings.append("未配置 access_token：只能打开登录页，无法自动登录。")
            autologin = False
        cf_id = str(self.plugin.config.get("cf_access_client_id") or "").strip()
        if cf_id:
            warnings.append(
                "已配置 Cloudflare Access Service Token（仅插件服务端调用有效）。"
                "浏览器嵌入官方 HAPI Web 时不会自动带上 CF 头；"
                "若 Hub 在 Access 后，需浏览器侧已通过 Access，或改用可直达的内网地址。"
            )
        if autologin:
            warnings.append(
                "自动登录会把 access_token 放进一次性启动链接（?token=）。"
                "HAPI 页面加载后会写入其自身 localStorage 并尽量从地址栏去掉 token；"
                "请勿把完整启动链接发给他人或贴到公开场合。"
            )

        query: dict[str, str] = {}
        if autologin and token:
            query["token"] = token
        # 跨源嵌入时显式 hub，避免 HAPI Web 误用 iframe 父页 origin
        if origin:
            query["hub"] = origin

        launch_url = page
        if query:
            sep = "&" if "?" in page else "?"
            launch_url = f"{page}{sep}{urlencode(query)}"

        warnings.append(
            "推荐用「新窗口打开」。AstrBot 管理面板与 HAPI 若 IP/端口不同，"
            "iframe 内嵌官方 HAPI Web 会触发跨源模块 CORS，页面空白属浏览器限制，不是 token 错误。"
        )

        return json_response({
            "ok": True,
            "url": launch_url,
            "url_display": page,  # 不带 token，供界面展示
            "origin": origin,
            "path": raw_path,
            "autologin": bool(autologin and token),
            "token_configured": bool(token),
            "loopback": loopback,
            "warnings": warnings,
            "note": (
                "官方 HAPI Web 支持 ?token= / ?hub=。"
                "跨源时请新窗口打开；面板内嵌仅同源或 HAPI 允许跨源脚本时可用。"
            ),
        })


# ──── session ops helpers ────


class LifecycleError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _find_session(plugin, sid: str) -> dict | None:
    for s in plugin.sessions_cache or []:
        if s.get("id") == sid:
            return s
    # prefix unique match
    matches = [s for s in (plugin.sessions_cache or []) if str(s.get("id") or "").startswith(sid)]
    if len(matches) == 1:
        return matches[0]
    return None


def _session_row(plugin, sid: str) -> dict | None:
    snap = build_sessions_snapshot(plugin)
    for s in snap["sessions"]:
        if s["id"] == sid or s["id"].startswith(sid):
            return s
    return None


async def run_lifecycle(plugin, sid: str, action: str) -> dict:
    """执行 resume|archive|delete|abort，返回结果 dict。

    与聊天 /hapi archive|resume|delete 共用 session_ops，不在这里硬编码 HAPI 路径。
    """
    from ..core import session_ops
    action = str(action or "").strip().lower()
    raw_sid = str(sid or "").strip()
    if not raw_sid:
        raise LifecycleError("空 session id", 400)
    if action not in ("resume", "archive", "delete", "abort"):
        raise LifecycleError(f"未知 action: {action}", 400)

    # 缓存可能过期：找不到时强制刷新再查一次
    session = _find_session(plugin, raw_sid)
    if session is None:
        try:
            await plugin._refresh_sessions()
        except Exception as e:
            logger.warning("lifecycle pre-refresh failed: %s", e)
        session = _find_session(plugin, raw_sid)

    if session:
        sid = str(session.get("id") or raw_sid)
    else:
        sid = raw_sid
        if action != "delete":
            raise LifecycleError(f"session 不存在: {raw_sid[:16]}", 404)

    new_id = None
    msg = ""

    if action == "abort":
        ok, msg = await session_ops.abort_session(plugin.client, sid)
        if not ok:
            raise LifecycleError(msg or "中断失败", 502)

    elif action == "archive":
        # 与聊天指令一致：直接调 HAPI archive；已归档时把上游错误原样返回
        ok, msg = await session_ops.archive_session(plugin.client, sid)
        if not ok:
            raise LifecycleError(msg or "归档失败", 502)

    elif action == "delete":
        # 与 /hapi delete 一致：运行中先归档再删
        if session and (session.get("active") or session.get("thinking")):
            ok_arc, msg_arc = await session_ops.archive_session(plugin.client, sid)
            if not ok_arc:
                # 有的 HAPI 对已停会话 archive 失败仍可删
                logger.warning("delete: archive first failed sid=%s: %s", sid[:12], msg_arc)
        ok, msg = await session_ops.delete_session(plugin.client, sid)
        if not ok:
            raise LifecycleError(msg or "删除失败", 502)
        try:
            await plugin.state_mgr.unbind_session(sid)
        except Exception as e:
            logger.warning("unbind after delete failed sid=%s: %s", sid, e)

    elif action == "resume":
        if session and session.get("active"):
            raise LifecycleError("session 已是运行中，无需恢复", 400)
        old_bound = None
        old_flavor = "unknown"
        try:
            old_bound = plugin.binding_mgr._session_owners.get(sid)
        except Exception:
            old_bound = None
        if session:
            old_flavor = str((session.get("metadata") or {}).get("flavor") or "unknown")
        ok, msg, resumed_sid = await session_ops.resume_session(plugin.client, sid)
        if not ok:
            raise LifecycleError(msg or "恢复失败", 502)
        new_id = resumed_sid or sid
        if old_bound:
            try:
                if new_id != sid:
                    await plugin.state_mgr.unbind_session(sid)
                await plugin.state_mgr.capture_window(new_id, old_bound, old_flavor)
            except Exception as e:
                logger.warning("rebind after resume failed: %s", e)

    try:
        await plugin._refresh_sessions()
    except Exception as e:
        logger.warning("refresh after lifecycle failed: %s", e)

    return {
        "ok": True,
        "action": action,
        "id": sid,
        "new_id": new_id,
        "message": msg or f"{action} 成功",
        "session": _session_row(plugin, new_id or sid),
        "snapshot": build_sessions_snapshot(plugin),
    }


def _resolve_writable_user(plugin, user_id=None) -> str:
    states = getattr(plugin.state_mgr, "_user_states_cache", {}) or {}
    known = list(states.keys())
    if user_id is not None:
        uid = str(user_id).strip()
        if uid not in states:
            raise LifecycleError(f"未知 user_id: {uid}", 400)
        return uid
    if len(known) == 0:
        raise LifecycleError("尚无已知用户；请先在聊天中使用 /hapi bind", 400)
    return known[0]


async def set_primary_route(plugin, umo: str | None, user_id=None) -> dict:
    uid = _resolve_writable_user(plugin, user_id)
    state = dict(plugin.state_mgr._user_states_cache.get(uid, {}))
    if umo:
        if len(umo) > 256:
            raise LifecycleError("umo 过长", 400)
        state["primary_umo"] = umo
        message = f"已设置默认推送窗口为 {window_display_title(umo)}"
    else:
        state.pop("primary_umo", None)
        message = "已清除默认推送窗口"
    plugin.state_mgr._user_states_cache[uid] = state
    await plugin.put_kv_data(f"user_state_{uid}", state)
    return {
        "ok": True,
        "message": message,
        "defaults": aggregate_route_defaults(plugin),
        "snapshot": build_sessions_snapshot(plugin),
    }


async def set_flavor_route(plugin, flavor: str, umo: str | None, user_id=None) -> dict:
    from ..chat.flavor_profiles import is_bindable_flavor, normalize_flavor

    flavor = normalize_flavor(flavor)
    if not is_bindable_flavor(flavor):
        raise LifecycleError(f"非法 flavor: {flavor}", 400)
    uid = _resolve_writable_user(plugin, user_id)
    state = dict(plugin.state_mgr._user_states_cache.get(uid, {}))
    routes = plugin.state_mgr.normalized_flavor_primary_umos(state)
    if umo:
        if len(umo) > 256:
            raise LifecycleError("umo 过长", 400)
        routes[flavor] = umo
        message = f"已设置 {flavor} 推送窗口为 {window_display_title(umo)}"
    else:
        routes.pop(flavor, None)
        message = f"已清除 {flavor} 推送窗口"
    state["flavor_primary_umos"] = routes
    plugin.state_mgr._user_states_cache[uid] = state
    await plugin.put_kv_data(f"user_state_{uid}", state)
    return {
        "ok": True,
        "message": message,
        "defaults": aggregate_route_defaults(plugin),
        "snapshot": build_sessions_snapshot(plugin),
    }


async def reconnect_hapi(plugin) -> dict:
    """按当前已落盘配置重建 client 并重启 SSE。"""
    from ..core.hapi_client import AsyncHapiClient
    from ..core.cf_access import CfAccessManager

    endpoint = str(plugin.config.get("hapi_endpoint") or "").strip()
    token = str(plugin.config.get("access_token") or "")
    proxy = str(plugin.config.get("proxy_url") or "").strip() or None
    jwt_life = int(plugin.config.get("jwt_lifetime", 900) or 900)
    refresh_before = int(plugin.config.get("refresh_before_expiry", 180) or 180)

    if not endpoint:
        raise ValueError(
            "hapi_endpoint 为空，无法连接 HAPI。"
            "请填写完整地址（如 http://127.0.0.1:3006）后保存。"
        )
    if "://" not in endpoint:
        raise ValueError(
            f"hapi_endpoint 缺少协议前缀: {endpoint!r}。"
            "请使用 http:// 或 https:// 开头的完整 URL。"
        )
    if not str(token).strip():
        raise ValueError("access_token 为空，无法鉴权。请填写 HAPI Access Token 后保存。")

    cf_id = str(plugin.config.get("cf_access_client_id") or "").strip()
    cf_secret = str(plugin.config.get("cf_access_client_secret") or "").strip()
    if cf_id.lower().startswith("cf-access-client-id:"):
        cf_id = cf_id.split(":", 1)[1].strip()
    if cf_secret.lower().startswith("cf-access-client-secret:"):
        cf_secret = cf_secret.split(":", 1)[1].strip()
    cf_mgr = None
    if cf_id and cf_secret:
        cf_mgr = CfAccessManager(client_id=cf_id, client_secret=cf_secret)

    # stop SSE
    try:
        await plugin.sse_listener.stop()
    except Exception as e:
        logger.warning("stop SSE before reconnect: %s", e)

    # close old client
    try:
        await plugin.client.close()
    except Exception as e:
        logger.warning("close client before reconnect: %s", e)

    new_client = AsyncHapiClient(
        endpoint=endpoint,
        access_token=token,
        proxy_url=proxy,
        jwt_lifetime=jwt_life,
        refresh_before=refresh_before,
        cf_access_mgr=cf_mgr,
    )
    await new_client.init()
    plugin.client = new_client
    # SSE 持有独立 client 引用，必须同步；命令/LLM 路径经 property 读 plugin.client
    plugin.sse_listener.client = new_client

    # restart SSE with current runtime flags
    output_level = plugin.config.get("output_level", "simple")
    remind = plugin.config.get("remind_pending", True)
    remind_interval = plugin.config.get("remind_interval", 180)
    auto_approve = plugin.config.get("auto_approve_enabled", False)
    auto_approve_start = plugin.config.get("auto_approve_start", "23:00")
    auto_approve_end = plugin.config.get("auto_approve_end", "07:00")
    max_reconnect = plugin.config.get("max_reconnect_attempts", 30)
    summary_msg_count = plugin.config.get("summary_msg_count", 5)

    plugin.sse_listener._hibernated = False
    plugin.sse_listener.conn_fail_count = 0
    plugin.sse_listener.conn_error = None
    if hasattr(plugin.sse_listener, "_stream_live"):
        plugin.sse_listener._stream_live = False
    plugin.sse_listener.start(
        output_level,
        remind_pending=remind,
        remind_interval=remind_interval,
        auto_approve_enabled=auto_approve,
        auto_approve_start=auto_approve_start,
        auto_approve_end=auto_approve_end,
        summary_msg_count=summary_msg_count,
        max_reconnect_attempts=max_reconnect,
    )

    try:
        await plugin._refresh_sessions()
    except Exception as e:
        logger.warning("refresh after reconnect: %s", e)

    return {
        "ok": True,
        "message": "已按当前配置重建连接并重启 SSE",
        "connection": connection_view(plugin),
        "config": public_config(plugin),
    }


# ──── config helpers ────


class ConfigValidationError(ValueError):
    """配置校验失败。"""


def _cfg_get(cfg: Any, key: str, default: Any = None) -> Any:
    """兼容 AstrBotConfig / dict / 类属性。"""
    if cfg is None:
        return default
    if isinstance(cfg, dict) or hasattr(cfg, "get"):
        try:
            val = cfg.get(key, default)
        except Exception:
            val = default
        return default if val is None else val
    try:
        val = cfg[key]  # type: ignore[index]
    except Exception:
        val = getattr(cfg, key, default)
    return default if val is None else val


def public_config(plugin) -> dict[str, Any]:
    """配置视图（给前端）。

    读的是**插件运行时** `plugin.config`（与官方设置页同源）。
    access_token 明文返回（管理面板仅管理员可见）；CF secret 仍不回显。
    附带 hapi_web_url：可点击的官方 HAPI 启动链（含 token 自动登录）。
    """
    from ..render import card_render
    from ..chat.poke_actions import normalize_poke_action, poke_actions_meta

    cfg = getattr(plugin, "config", None)
    token = str(_cfg_get(cfg, "access_token", "") or "")
    ns = None
    if ":" in token:
        ns = token.split(":", 1)[1].strip() or None

    cf_id = str(_cfg_get(cfg, "cf_access_client_id", "") or "").strip()
    defaults = card_render.config_defaults()
    # 默认值优先 _conf_schema.json，卡片类再叠 card_render.config_defaults
    try:
        from .webui_settings_schema import schema_defaults as _schema_defaults

        schema_defaults: dict[str, Any] = {**_schema_defaults(), **defaults}
    except Exception:
        schema_defaults = {
            "hapi_endpoint": "http://127.0.0.1:3006",
            "proxy_url": "",
            "cf_access_client_id": "",
            "max_reconnect_attempts": 30,
            "jwt_lifetime": 900,
            "refresh_before_expiry": 180,
            "output_level": "simple",
            "summary_msg_count": 5,
            "quick_prefix": ">",
            "poke_approve": True,
            "poke_action": "approve",
            "cmd_keyword_maps": None,
            "remind_pending": True,
            "remind_interval": 180,
            "auto_approve_enabled": False,
            "auto_approve_start": "23:00",
            "auto_approve_end": "07:00",
            "default_notification_window": "",
            **defaults,
        }
    out: dict[str, Any] = {}
    for key in CONFIG_KEYS:
        if key in SENSITIVE_KEYS:
            continue
        fallback = schema_defaults.get(key)
        val = _cfg_get(cfg, key, fallback)
        if val is None and key in schema_defaults:
            val = schema_defaults[key]
        out[key] = val

    # 前端方便：kinds 同时给数组；失败时不拖垮整个 config
    try:
        out["render_mode"] = card_render.normalize_render_mode(out.get("render_mode"))
        out["render_kinds_list"] = card_render.parse_kinds(out.get("render_kinds"))
    except Exception:
        out["render_kinds_list"] = list(card_render.DEFAULT_KINDS)
    try:
        out["render_engine"] = card_render.engine_status(
            user_font_path=str(out.get("card_font_path") or "") or None
        )
    except Exception:
        out["render_engine"] = {
            "pillow": False,
            "install_hint": "pip install Pillow",
            "installable": [],
        }
    # 保证前端总能拿到勾选项（即使 engine_status 旧缓存/异常）
    if not out["render_engine"].get("installable"):
        try:
            from ..render import font_manager
            out["render_engine"]["installable"] = font_manager.installable_items()
        except Exception:
            out["render_engine"]["installable"] = [
                {
                    "id": "font_noto_sc",
                    "group": "font",
                    "label": "中文字体 Noto Sans SC",
                    "desc": "下载到插件 assets/fonts/（约 8MB）",
                    "installed": False,
                    "approx_mb": 8,
                    "approx_label": "约 8MB",
                },
                {
                    "id": "dep_pillow",
                    "group": "dep",
                    "label": "Pillow（出图引擎）",
                    "desc": "pip install Pillow — 低延迟出图（约 3MB）",
                    "installed": False,
                    "approx_mb": 3,
                    "approx_label": "约 3MB",
                },
                {
                    "id": "dep_matplotlib",
                    "group": "dep",
                    "label": "matplotlib（公式）",
                    "desc": "pip install matplotlib — 含 numpy 等（约 40MB）",
                    "installed": False,
                    "approx_mb": 40,
                    "approx_label": "约 40MB",
                },
            ]
    try:
        out["card_style"] = card_render.style_to_public(
            card_render.style_from_config(out)
        )
    except Exception:
        out["card_style"] = {}

    try:
        out["poke_action"] = normalize_poke_action(out.get("poke_action"))
        out["poke_actions"] = poke_actions_meta()
    except Exception:
        out["poke_action"] = "approve"
        out["poke_actions"] = []

    try:
        from ..chat.keyword_maps import (
            DEFAULT_KEYWORD_MAPS,
            maps_to_storage,
            normalize_maps,
        )

        raw_maps = out.get("cmd_keyword_maps")
        maps = normalize_maps(raw_maps)
        # 未配置或仍是空数组：使用内置默认（stop/停、sw、cl、继续、专注/退出专注）
        if not maps and (
            raw_maps is None
            or (isinstance(raw_maps, str) and raw_maps.strip() in ("", "[]"))
            or raw_maps == []
        ):
            maps = normalize_maps(DEFAULT_KEYWORD_MAPS)
        out["cmd_keyword_maps"] = maps_to_storage(maps)
        out["cmd_keyword_maps_list"] = maps
    except Exception:
        from ..chat.keyword_maps import DEFAULT_KEYWORD_MAPS, maps_to_storage, normalize_maps

        maps = normalize_maps(DEFAULT_KEYWORD_MAPS)
        out["cmd_keyword_maps"] = maps_to_storage(maps)
        out["cmd_keyword_maps_list"] = maps

    out["access_token"] = token  # 明文（面板内使用）
    out["access_token_configured"] = bool(token.strip())
    out["access_token_namespace"] = ns
    out["cf_access_client_secret_configured"] = bool(
        str(_cfg_get(cfg, "cf_access_client_secret", "") or "").strip()
    )
    out["cf_access_enabled"] = bool(cf_id)

    # 可点击的官方 HAPI 启动链（浏览器新标签打开，不依赖 window.open）
    try:
        from urllib.parse import urlencode, urljoin

        endpoint = str(out.get("hapi_endpoint") or "").strip()
        if endpoint:
            base = endpoint.rstrip("/")
            parsed = urlparse(base if "://" in base else f"http://{base}")
            if parsed.scheme in ("http", "https") and parsed.netloc:
                origin = f"{parsed.scheme}://{parsed.netloc}"
                page = origin + "/"
                q: dict[str, str] = {"hub": origin}
                if token.strip():
                    q["token"] = token.strip()
                out["hapi_web_url"] = f"{page}?{urlencode(q)}"
                out["hapi_web_url_safe"] = page
            else:
                out["hapi_web_url"] = ""
                out["hapi_web_url_safe"] = ""
        else:
            out["hapi_web_url"] = ""
            out["hapi_web_url_safe"] = ""
    except Exception:
        out["hapi_web_url"] = ""
        out["hapi_web_url_safe"] = ""
    return out


async def save_plugin_config(plugin, patch: dict) -> dict:
    """校验 → 写 AstrBotConfig → save_config(_async) 落盘 → 热更新。

    落盘失败则整单失败，不半热更新。
    连接类配置（endpoint / token / 代理 / CF / JWT 等）变更后会自动
    调用 reconnect_hapi，避免 SSE 继续挂在旧地址/旧凭证上。
    """
    cleaned = validate_config_patch(patch)
    if not cleaned:
        return {
            "saved": False,
            "changed": [],
            "reconnect_required": False,
            "reconnected": False,
            "config": public_config(plugin),
            "message": "没有变更",
        }

    prev = {k: plugin.config.get(k) for k in cleaned}
    for k, v in cleaned.items():
        plugin.config[k] = v

    await _persist_config(plugin)

    try:
        apply_runtime_config(plugin, cleaned)
    except Exception:
        # 落盘已成功；热更新失败只记日志，仍返回成功并提示可能需重载
        logger.exception("apply_runtime_config failed after save")

    # 仅当 RECONNECT_KEYS 的实际值发生变化时才重建 client / SSE
    reconnect_keys_changed = sorted(
        k for k in cleaned
        if k in RECONNECT_KEYS and prev.get(k) != cleaned[k]
    )
    reconnected = False
    reconnect_error: str | None = None
    connection = None
    if reconnect_keys_changed:
        try:
            reconnect_result = await reconnect_hapi(plugin)
            reconnected = True
            connection = reconnect_result.get("connection")
            logger.info(
                "config save auto-reconnect ok, changed=%s",
                reconnect_keys_changed,
            )
        except Exception as e:
            # 配置已落盘；自动重连失败不回滚，交给前端/用户点「重连」
            logger.exception(
                "config save auto-reconnect failed, changed=%s",
                reconnect_keys_changed,
            )
            reconnect_error = f"{type(e).__name__}: {e}"
            try:
                connection = connection_view(plugin)
            except Exception:
                connection = None

    if reconnected:
        message = "已保存，并按新配置重建连接与 SSE"
    elif reconnect_keys_changed and reconnect_error:
        message = (
            f"已保存，但自动重连失败: {reconnect_error}"
            "（可点概览「重连」重试）"
        )
    else:
        message = "已保存"

    return {
        "saved": True,
        "changed": sorted(cleaned.keys()),
        # 仍需手动重连时为 True（自动重连失败）
        "reconnect_required": bool(reconnect_keys_changed) and not reconnected,
        "reconnected": reconnected,
        "reconnect_error": reconnect_error,
        "reconnect_keys": reconnect_keys_changed,
        "connection": connection,
        "config": public_config(plugin),
        "previous": {k: _mask_if_sensitive(k, prev[k]) for k in cleaned},
        "message": message,
    }


async def _persist_config(plugin) -> None:
    """调用 AstrBotConfig 官方落盘 API。"""
    cfg = plugin.config
    save_async = getattr(cfg, "save_config_async", None)
    if callable(save_async):
        result = save_async()
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            await result
        return
    save = getattr(cfg, "save_config", None)
    if not callable(save):
        raise RuntimeError("AstrBotConfig 无 save_config / save_config_async，无法持久化")
    result = save()
    if asyncio.iscoroutine(result) or asyncio.isfuture(result):
        await result


def validate_config_patch(patch: dict) -> dict[str, Any]:
    """返回清洗后的变更字典；空敏感字段跳过。"""
    if not isinstance(patch, dict):
        raise ConfigValidationError("请求体必须是对象")

    cleaned: dict[str, Any] = {}
    for raw_key, raw_val in patch.items():
        key = str(raw_key)
        if key not in CONFIG_KEYS:
            # 忽略前端附带的 *_configured / 派生只读字段
            if key.endswith("_configured") or key in (
                "access_token_namespace",
                "cf_access_enabled",
                "render_kinds_list",
                "cmd_keyword_maps_list",
                "render_engine",
                "card_style",
            ):
                continue
            raise ConfigValidationError(f"未知配置项: {key}")

        if key in SENSITIVE_KEYS:
            if raw_val is None:
                continue
            s = str(raw_val).strip()
            if not s:
                continue
            cleaned[key] = s
            continue

        if key in BOOL_KEYS:
            cleaned[key] = _as_bool(raw_val, key)
            continue

        if key in INT_KEYS:
            cleaned[key] = _as_int(raw_val, key)
            continue

        if key == "output_level":
            val = str(raw_val or "").strip()
            if val not in OUTPUT_LEVELS:
                raise ConfigValidationError(
                    f"output_level 必须是 {'/'.join(OUTPUT_LEVELS)}"
                )
            cleaned[key] = val
            continue

        if key in ("auto_approve_start", "auto_approve_end"):
            cleaned[key] = _as_hhmm(raw_val, key)
            continue

        if key == "quick_prefix":
            s = str(raw_val if raw_val is not None else "")
            if not s.strip():
                raise ConfigValidationError("quick_prefix 不能为空")
            if len(s) > 16:
                raise ConfigValidationError("quick_prefix 过长")
            cleaned[key] = s
            continue

        if key == "poke_action":
            from ..chat.poke_actions import POKE_ACTIONS, normalize_poke_action

            val = normalize_poke_action(raw_val)
            if val not in POKE_ACTIONS:
                raise ConfigValidationError(
                    f"poke_action 必须是 {'/'.join(POKE_ACTIONS)}"
                )
            cleaned[key] = val
            continue

        if key == "cmd_keyword_maps":
            from ..chat.keyword_maps import maps_to_storage, normalize_maps

            cleaned[key] = maps_to_storage(normalize_maps(raw_val))
            continue

        if key == "render_mode":
            from ..render import card_render
            val = card_render.normalize_render_mode(raw_val)
            if val not in RENDER_MODES:
                raise ConfigValidationError(
                    f"render_mode 必须是 {'/'.join(RENDER_MODES)}"
                )
            cleaned[key] = val
            continue

        if key == "formula_mode":
            from ..render import card_render
            val = card_render.normalize_formula_mode(raw_val)
            if val not in FORMULA_MODES:
                raise ConfigValidationError(
                    f"formula_mode 必须是 {'/'.join(FORMULA_MODES)}"
                )
            cleaned[key] = val
            continue

        if key == "render_kinds":
            from ..render import card_render
            kinds = card_render.parse_kinds(raw_val)
            cleaned[key] = card_render.kinds_to_storage(kinds)
            continue

        if key == "card_style_preset":
            val = str(raw_val or "").strip()
            if val not in CARD_PRESETS:
                raise ConfigValidationError(
                    f"card_style_preset 必须是 {'/'.join(CARD_PRESETS)}"
                )
            cleaned[key] = val
            continue

        if key == "card_density":
            val = str(raw_val or "").strip()
            if val not in CARD_DENSITY:
                raise ConfigValidationError(
                    f"card_density 必须是 {'/'.join(CARD_DENSITY)}"
                )
            cleaned[key] = val
            continue

        if key == "card_custom_css":
            s = str(raw_val if raw_val is not None else "")
            if len(s) > 200_000:
                raise ConfigValidationError("card_custom_css 过长（上限 200KB）")
            cleaned[key] = s
            continue

        if key == "card_font_path":
            s = str(raw_val if raw_val is not None else "").strip()
            if len(s) > 512:
                raise ConfigValidationError("card_font_path 过长")
            cleaned[key] = s
            continue

        if key in ("card_accent", "card_bg", "card_fg"):
            s = str(raw_val or "").strip()
            if not s:
                raise ConfigValidationError(f"{key} 不能为空")
            if not s.startswith("#"):
                s = "#" + s
            if len(s) not in (4, 7):
                raise ConfigValidationError(f"{key} 须为 #RGB 或 #RRGGBB")
            try:
                from ..render.card_render import _hex_to_rgb

                _hex_to_rgb(s)
            except Exception as e:
                raise ConfigValidationError(f"{key} 不是合法颜色") from e
            cleaned[key] = s
            continue

        if key == "access_token":
            s = "" if raw_val is None else str(raw_val).strip()
            if not s:
                continue  # 留空不改
            cleaned[key] = s
            continue

        # 字符串类
        cleaned[key] = "" if raw_val is None else str(raw_val)

    if "jwt_lifetime" in cleaned and "refresh_before_expiry" in cleaned:
        if cleaned["refresh_before_expiry"] >= cleaned["jwt_lifetime"]:
            raise ConfigValidationError("refresh_before_expiry 必须小于 jwt_lifetime")
    if "summary_msg_count" in cleaned:
        n = cleaned["summary_msg_count"]
        if n < 1 or n > 50:
            raise ConfigValidationError("summary_msg_count 范围 1–50")
    if "remind_interval" in cleaned and cleaned["remind_interval"] < 30:
        raise ConfigValidationError("remind_interval 至少 30 秒")
    if "card_width" in cleaned:
        n = cleaned["card_width"]
        if n < 400 or n > 1400:
            raise ConfigValidationError("card_width 范围 400–1400")
    if "card_font_scale" in cleaned:
        n = cleaned["card_font_scale"]
        if n < 75 or n > 150:
            raise ConfigValidationError("card_font_scale 范围 75–150（百分数）")

    return cleaned


def apply_runtime_config(plugin, patch: dict) -> None:
    """把已落盘的配置同步到运行时对象（不写盘）。"""
    sse = plugin.sse_listener

    if "output_level" in patch:
        sse.output_level = patch["output_level"]
    if "summary_msg_count" in patch:
        plugin._summary_msg_count = patch["summary_msg_count"]
        sse._summary_msg_count = patch["summary_msg_count"]
    if "quick_prefix" in patch:
        plugin._quick_prefix = patch["quick_prefix"]
    if "poke_approve" in patch:
        plugin._poke_approve = patch["poke_approve"]
    if "poke_action" in patch:
        from ..chat.poke_actions import normalize_poke_action

        plugin._poke_action = normalize_poke_action(patch["poke_action"])
    if "cmd_keyword_maps" in patch:
        from ..chat.keyword_maps import DEFAULT_KEYWORD_MAPS, normalize_maps

        maps = normalize_maps(patch["cmd_keyword_maps"])
        # 保存空数组表示清空；运行时仍允许空
        plugin._cmd_keyword_maps = maps
    if "remind_pending" in patch:
        sse._remind_enabled = patch["remind_pending"]
    if "remind_interval" in patch:
        sse._remind_interval = patch["remind_interval"]
    if "auto_approve_enabled" in patch:
        sse._auto_approve_enabled = patch["auto_approve_enabled"]
    if "auto_approve_start" in patch:
        sse._auto_approve_start = patch["auto_approve_start"]
    if "auto_approve_end" in patch:
        sse._auto_approve_end = patch["auto_approve_end"]
    if "max_reconnect_attempts" in patch:
        sse._max_reconnect = patch["max_reconnect_attempts"]


def _as_bool(val: Any, key: str) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        low = val.strip().lower()
        if low in ("true", "1", "yes", "on", "开启"):
            return True
        if low in ("false", "0", "no", "off", "关闭"):
            return False
    raise ConfigValidationError(f"{key} 必须是布尔值")


def _as_int(val: Any, key: str) -> int:
    try:
        n = int(val)
    except (TypeError, ValueError) as e:
        raise ConfigValidationError(f"{key} 必须是整数") from e
    if n < 0:
        raise ConfigValidationError(f"{key} 不能为负")
    return n


def _as_hhmm(val: Any, key: str) -> str:
    s = str(val or "").strip()
    parts = s.split(":")
    if len(parts) != 2:
        raise ConfigValidationError(f"{key} 格式应为 HH:MM")
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError as e:
        raise ConfigValidationError(f"{key} 格式应为 HH:MM") from e
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ConfigValidationError(f"{key} 时间非法")
    return f"{h:02d}:{m:02d}"


def _mask_if_sensitive(key: str, val: Any) -> Any:
    if key in SENSITIVE_KEYS:
        return "***" if val else ""
    return val


def _plugin_version(plugin) -> str:
    """版本号：instance 属性 → metadata.yaml → 内置默认。"""
    for attr in ("version", "VERSION"):
        ver = getattr(plugin, attr, None)
        if ver:
            return str(ver).lstrip("v")
    cfg_ver = plugin.config.get("_version") if getattr(plugin, "config", None) is not None else None
    if cfg_ver:
        return str(cfg_ver).lstrip("v")
    try:
        from pathlib import Path

        text = (Path(__file__).resolve().parent.parent / "metadata.yaml").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip().startswith("version:"):
                return line.split(":", 1)[1].strip().strip("\"'").lstrip("v")
    except Exception:
        pass
    return "3.0.1"


def _session_permission_mode(session: dict) -> str:
    """HAPI Session 上 permissionMode 在顶层；metadata 仅作兜底。"""
    meta = session.get("metadata") or {}
    return str(
        session.get("permissionMode")
        or session.get("permission_mode")
        or meta.get("permissionMode")
        or meta.get("permission_mode")
        or "default"
    )


def _session_model_label(session: dict) -> str:
    """模型字段：顶层 model / modelMode 优先，与 formatters 对齐。"""
    meta = session.get("metadata") or {}
    return str(
        session.get("modelMode")
        or session.get("model")
        or meta.get("model")
        or meta.get("modelMode")
        or "default"
    )


# ──── snapshot / routing ────


# 全量拉 HAPI sessions 的最小间隔（秒）。WebUI 轮询走缓存，不频繁打 Hub。
SESSIONS_REFRESH_TTL = 20.0
MACHINES_REFRESH_TTL = 15.0


def _query_truthy(request, key: str) -> bool:
    raw = request.query.get(key)
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


async def soft_refresh_sessions(plugin, *, force: bool = False) -> bool:
    """按需刷新 sessions_cache。

    - force=False：仅当缓存超过 TTL 才打 HAPI
    - 绝不调用 wake_up / 不碰 SSE 休眠状态
    返回是否实际发起了 fetch。
    """
    import time

    if not force:
        ts = float(getattr(plugin, "_sessions_cache_ts", 0) or 0)
        if ts and (time.monotonic() - ts) < SESSIONS_REFRESH_TTL:
            return False
        # 从未成功刷新过且 cache 非空：仍允许 TTL 节流；空 cache 则尝试一次
        if ts == 0 and plugin.sessions_cache:
            # 启动后 SSE 可能已增量更新 cache，不必立刻全量拉取
            plugin._sessions_cache_ts = time.monotonic()
            return False
    try:
        await plugin._refresh_sessions()
        return True
    except Exception as e:
        logger.warning("soft_refresh_sessions failed: %s", e)
        return False


def _platform_label(platform: str | None) -> str:
    p = (platform or "").strip().lower()
    if p in ("linux",):
        return "Linux"
    if p in ("darwin", "macos", "osx"):
        return "macOS"
    if p in ("win32", "windows", "win"):
        return "Windows"
    return platform.strip() if platform and platform.strip() else "未知系统"


def _as_float(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v, default=None):
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def normalize_machine(raw: dict) -> dict:
    """HAPI Machine → WebUI 精简视图（对齐官方 health 字段）。"""
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    health_raw = raw.get("health") if isinstance(raw.get("health"), dict) else None
    runner = raw.get("runnerState") if isinstance(raw.get("runnerState"), dict) else {}

    host = str(meta.get("host") or "").strip()
    display = str(meta.get("displayName") or "").strip()
    label = display or host or str(raw.get("id") or "")[:12] or "machine"
    platform = str(meta.get("platform") or "").strip()

    health = None
    if health_raw:
        cpu_count = _as_int(health_raw.get("cpuCount"))
        load1m = _as_float(health_raw.get("load1m"))
        cpu_percent = _as_float(health_raw.get("cpuPercent"))
        memory_percent = _as_float(health_raw.get("memoryPercent"))
        uptime_seconds = _as_int(health_raw.get("uptimeSeconds"))
        collected_at = _as_int(health_raw.get("collectedAt"))
        # 裁剪非法百分比
        if cpu_percent is not None:
            cpu_percent = max(0.0, min(100.0, cpu_percent))
        if memory_percent is not None:
            memory_percent = max(0.0, min(100.0, memory_percent))
        health = {
            "collected_at": collected_at,
            "cpu_count": cpu_count,
            "load1m": load1m,
            "cpu_percent": round(cpu_percent) if cpu_percent is not None else None,
            "memory_percent": round(memory_percent) if memory_percent is not None else None,
            "uptime_seconds": uptime_seconds if uptime_seconds is not None and uptime_seconds >= 0 else None,
        }

    return {
        "id": str(raw.get("id") or ""),
        "label": label,
        "host": host or None,
        "platform": platform or None,
        "platform_label": _platform_label(platform),
        "active": bool(raw.get("active")),
        "active_at": _as_int(raw.get("activeAt")),
        "runner_status": str(runner.get("status") or "").strip() or None,
        "runner_started_at": _as_int(runner.get("startedAt")),
        "health": health,
        "cli_version": str(meta.get("happyCliVersion") or "").strip() or None,
    }


async def soft_refresh_machines(plugin, *, force: bool = False) -> list[dict]:
    """拉取 HAPI GET /api/machines（在线 runner 机器 + health）。

    TTL 节流；失败时返回上次缓存。不唤醒 SSE。
    """
    import time

    cached = list(getattr(plugin, "machines_cache", None) or [])
    if not force:
        ts = float(getattr(plugin, "_machines_cache_ts", 0) or 0)
        if ts and (time.monotonic() - ts) < MACHINES_REFRESH_TTL:
            return cached

    client = getattr(plugin, "client", None)
    if client is None:
        return cached

    try:
        data = await client.get_json("/api/machines")
        rows = data.get("machines") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            rows = []
        view = [normalize_machine(m) for m in rows if isinstance(m, dict)]
        # 在线优先，再按名称
        view.sort(key=lambda m: (0 if m.get("active") else 1, str(m.get("label") or "").lower()))
        plugin.machines_cache = view
        plugin._machines_cache_ts = time.monotonic()
        return view
    except Exception as e:
        logger.warning("soft_refresh_machines failed: %s", e)
        # 标记错误但不清缓存
        plugin._machines_cache_error = f"{type(e).__name__}: {e}"
        return cached


def _collect_known_umos(plugin) -> set[str]:
    """从绑定/默认路由/窗口状态收集已知 UMO。"""
    umos: set[str] = set()
    binding = getattr(plugin, "binding_mgr", None)
    owners = dict(getattr(binding, "_session_owners", {}) or {}) if binding else {}
    umos.update(str(v) for v in owners.values() if v)
    for umo in getattr(binding, "_window_states", {}) or {}:
        if umo:
            umos.add(str(umo))
    try:
        defaults = aggregate_route_defaults(plugin)
        if defaults.get("primary"):
            umos.add(str(defaults["primary"]))
        umos.update(str(v) for v in (defaults.get("flavor") or {}).values() if v)
    except Exception:
        pass
    umos.discard("")
    return umos


async def ensure_umo_name_map(plugin, *, force: bool = False) -> dict[str, str]:
    """异步解析 UMO 展示名，缓存到 plugin._umo_name_map。

    同步 snapshot 只读缓存；调用方在 async handler 里先 await 本函数。
    """
    import time

    cache: dict[str, str] = dict(getattr(plugin, "_umo_name_map", None) or {})
    ts = float(getattr(plugin, "_umo_name_map_ts", 0) or 0)
    umos = _collect_known_umos(plugin)
    missing = [u for u in umos if u not in cache]
    if not force and ts and (time.monotonic() - ts) < 60 and not missing:
        return {k: v for k, v in cache.items() if v}

    try:
        from ..render.umo_display import resolve_umo_names

        ctx = getattr(plugin, "context", None)
        to_query = list(umos) if force else missing or list(umos)
        fresh = await resolve_umo_names(ctx, to_query)
        if fresh:
            cache.update(fresh)
        # 对没有别名的也记空标记，避免反复打库
        for u in to_query:
            cache.setdefault(u, "")
        plugin._umo_name_map = cache
        plugin._umo_name_map_ts = time.monotonic()
    except Exception as e:
        logger.debug("ensure_umo_name_map failed: %s", e)
        plugin._umo_name_map = cache
    return {k: v for k, v in cache.items() if v}


def build_sessions_snapshot(plugin) -> dict:
    """全局快照。只读内存，不触发网络。

    数据来源：插件进程内 sessions_cache / SSE / 绑定表 / plugin.config。
    """
    import time

    sessions_raw = list(getattr(plugin, "sessions_cache", None) or [])
    binding = getattr(plugin, "binding_mgr", None)
    owners = dict(getattr(binding, "_session_owners", {}) or {}) if binding else {}

    sse = getattr(plugin, "sse_listener", None)
    counts_fn = getattr(sse, "pending_counts", None) if sse is not None else None
    if callable(counts_fn):
        pending_counts = counts_fn()
    elif sse is not None:
        pending_counts = {
            sid: len(reqs) for sid, reqs in getattr(sse, "pending", {}).items() if reqs
        }
    else:
        pending_counts = {}

    defaults = aggregate_route_defaults(plugin)
    conn = connection_view(plugin)
    cache_ts = float(getattr(plugin, "_sessions_cache_ts", 0) or 0)
    cache_age = (time.monotonic() - cache_ts) if cache_ts else None

    sessions = []
    for s in sessions_raw:
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        if not sid:
            continue
        meta = s.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        flavor = str(meta.get("flavor") or "").strip().lower() or "unknown"
        try:
            title = formatters.get_session_title(s)
        except Exception:
            title = meta.get("name") or meta.get("title") or str(sid)[:8]
        path = meta.get("path") or meta.get("cwd") or meta.get("workingDirectory") or ""
        bound = owners.get(sid)
        eff_umo, layer = resolve_route_layer(sid, flavor, owners, defaults)
        pending_n = int(pending_counts.get(sid) or 0)
        sessions.append({
            "id": sid,
            "id_short": str(sid)[:8],
            "title": title,
            "flavor": flavor,
            "path": path,
            "active": bool(s.get("active")),
            "thinking": bool(s.get("thinking")),
            "pending": pending_n,
            "permissionMode": _session_permission_mode(s),
            "modelMode": _session_model_label(s),
            "bound_umo": bound,
            "effective_umo": eff_umo,
            "layer": layer,
        })

    # 展示名：由 ensure_umo_name_map 异步预热写入 plugin._umo_name_map
    # 空串 = 查过但没有别名，展示时当 None
    raw_names = dict(getattr(plugin, "_umo_name_map", None) or {})
    name_map = {k: v for k, v in raw_names.items() if v}

    columns = build_columns(sessions, defaults, name_map=name_map)
    window_options = build_window_options(
        owners, defaults, plugin, sessions, name_map=name_map
    )

    try:
        cfg_view = public_config(plugin)
    except Exception as e:
        logger.exception("public_config in snapshot failed")
        cfg_view = {"_error": f"{type(e).__name__}: {e}"}

    machines = list(getattr(plugin, "machines_cache", None) or [])
    machines_ts = float(getattr(plugin, "_machines_cache_ts", 0) or 0)
    machines_age = (time.monotonic() - machines_ts) if machines_ts else None

    hidden_windows: list[str] = []
    sm = getattr(plugin, "state_mgr", None)
    if sm is not None:
        try:
            hidden_windows = sm.get_webui_hidden_windows()
        except Exception:
            hidden_windows = list(getattr(sm, "_webui_hidden_windows", None) or [])

    # Focus 模式开启的窗口列表（供会话管理页展示与切换）
    focus_windows: list[str] = []
    if binding is not None:
        for umo, ws in (getattr(binding, "_window_states", {}) or {}).items():
            if isinstance(ws, dict) and ws.get("focus_mode"):
                focus_windows.append(str(umo))

    return {
        "connection": conn,
        "metrics": {
            "active": sum(1 for x in sessions if x["active"]),
            "thinking": sum(1 for x in sessions if x["thinking"]),
            "pending": sum(x["pending"] for x in sessions),
            "unrouted": sum(1 for x in sessions if x["layer"] == "none"),
            "total": len(sessions),
            "machines": len(machines),
        },
        "sessions": sessions,
        "machines": machines,
        "columns": columns,
        "defaults": defaults,
        "window_options": window_options,
        "hidden_windows": list(hidden_windows or []),
        "focus_windows": focus_windows,
        "config": cfg_view,
        "plugin_version": _plugin_version(plugin),
        "cache": {
            "sessions_age_sec": None if cache_age is None else round(cache_age, 1),
            "refresh_ttl_sec": SESSIONS_REFRESH_TTL,
            "machines_age_sec": None if machines_age is None else round(machines_age, 1),
            "machines_ttl_sec": MACHINES_REFRESH_TTL,
            "from_memory": True,
        },
    }


def connection_view(plugin) -> dict:
    """插件侧 SSE 连接视图（读插件运行时，不直连 HAPI）。"""
    endpoint = str(_cfg_get(getattr(plugin, "config", None), "hapi_endpoint", "") or "").strip()
    host = _endpoint_host(endpoint)
    sse = getattr(plugin, "sse_listener", None)
    if sse is None:
        return {
            "sse_status": "disconnected",
            "endpoint_host": host,
            "endpoint": endpoint,
            "conn_fail_count": 0,
            "conn_error": "SSEListener 未初始化",
            "hibernated": False,
            "task_running": False,
            "stream_live": False,
            "source": "plugin_sse",
        }
    try:
        status_fn = getattr(sse, "get_connection_status", None)
        if callable(status_fn):
            status = status_fn()
        else:
            task = getattr(sse, "_task", None)
            task_running = bool(task and not task.done())
            hibernated = bool(getattr(sse, "_hibernated", False))
            stream_live = bool(getattr(sse, "_stream_live", False))
            fail = int(getattr(sse, "conn_fail_count", 0) or 0)
            if hibernated:
                st = "hibernated"
            elif task_running and stream_live and fail == 0:
                st = "connected"
            elif task_running:
                st = "reconnecting"
            else:
                st = "disconnected"
            status = {
                "sse_status": st,
                "conn_fail_count": fail,
                "conn_error": getattr(sse, "conn_error", None),
                "hibernated": hibernated,
                "task_running": task_running,
                "stream_live": stream_live,
            }
    except Exception as e:
        logger.warning("connection_view failed: %s", e)
        status = {
            "sse_status": "disconnected",
            "conn_fail_count": 0,
            "conn_error": f"{type(e).__name__}: {e}",
            "hibernated": False,
            "task_running": False,
            "stream_live": False,
        }
    return {
        "sse_status": status.get("sse_status") or "disconnected",
        "endpoint_host": host,
        "endpoint": endpoint,
        "conn_fail_count": int(status.get("conn_fail_count") or 0),
        "conn_error": status.get("conn_error"),
        "hibernated": bool(status.get("hibernated")),
        "task_running": bool(status.get("task_running")),
        "stream_live": bool(status.get("stream_live")),
        "source": "plugin_sse",  # 明确：状态来自插件 SSE，不是网页直连 HAPI
    }


def _endpoint_host(endpoint: str) -> str:
    if not endpoint:
        return "—"
    try:
        u = urlparse(endpoint)
        if u.netloc:
            return u.netloc
    except Exception:
        pass
    return endpoint[:40]


def aggregate_route_defaults(plugin) -> dict:
    """聚合 known users 的 primary / flavor 路由。

    writable: known_users 非空时可写。
    """
    states = getattr(plugin.state_mgr, "_user_states_cache", {}) or {}
    known = list(states.keys())
    primary = None
    flavor: dict[str, str] = {}
    for st in states.values():
        p = st.get("primary_umo")
        if p and not primary:
            primary = str(p)
        for fk, umo in plugin.state_mgr.normalized_flavor_primary_umos(st).items():
            flavor.setdefault(fk, umo)

    # 有已知用户即可在 Web 改；多用户时仍可写，不再弹「写入第一个」提示
    writable = len(known) >= 1
    reason = ""
    if len(known) == 0:
        reason = "尚无已知用户；请先在聊天里 /hapi bind 一次"
        writable = False

    return {
        "primary": primary,
        "flavor": flavor,
        "writable": writable,
        "writable_reason": reason,
        "known_user_count": len(known),
    }


def resolve_route_layer(
    session_id: str,
    flavor: str,
    owners: dict[str, str],
    defaults: dict,
) -> tuple[str | None, str]:
    """返回 (effective_umo, layer)。"""
    if session_id in owners:
        return owners[session_id], "session_bind"
    fumo = (defaults.get("flavor") or {}).get(flavor)
    if fumo:
        return fumo, "flavor_default"
    primary = defaults.get("primary")
    if primary:
        return primary, "primary"
    return None, "none"


def build_columns(
    sessions: list[dict],
    defaults: dict,
    *,
    name_map: dict[str, str] | None = None,
) -> list[dict]:
    names = name_map or {}
    map_: dict[str, dict] = {}

    def ensure(umo: str | None) -> dict:
        key = umo or "__none__"
        if key not in map_:
            map_[key] = {
                "umo": umo,
                "title": (
                    window_display_title(umo, name=names.get(str(umo)))
                    if umo
                    else "未投递"
                ),
                "is_primary": bool(umo and umo == defaults.get("primary")),
                "flavors": [
                    f for f, u in (defaults.get("flavor") or {}).items() if u == umo
                ],
                "sessions": [],
            }
        return map_[key]

    if defaults.get("primary"):
        ensure(defaults["primary"])
    for u in (defaults.get("flavor") or {}).values():
        ensure(u)
    for s in sessions:
        # ensure() 返回 dict，必须用 ["sessions"]，不能 .sessions
        ensure(s.get("effective_umo"))["sessions"].append(s)

    cols = list(map_.values())
    cols.sort(key=lambda c: (0 if c["umo"] else 1, -len(c["sessions"])))
    return cols


def build_window_options(
    owners: dict,
    defaults: dict,
    plugin,
    sessions: list[dict] | None = None,
    *,
    name_map: dict[str, str] | None = None,
) -> list[dict]:
    umos: set[str] = set()
    if defaults.get("primary"):
        umos.add(str(defaults["primary"]))
    umos.update(str(v) for v in (defaults.get("flavor") or {}).values() if v)
    umos.update(str(v) for v in owners.values() if v)
    # window states
    for umo in getattr(getattr(plugin, "binding_mgr", None), "_window_states", {}) or {}:
        if umo:
            umos.add(str(umo))
    # session 绑定 / 有效投递窗口（曾经能选到的窗口不应丢）
    for s in sessions or []:
        if not isinstance(s, dict):
            continue
        for k in ("bound_umo", "effective_umo"):
            u = s.get(k)
            if u:
                umos.add(str(u))
    umos.discard("")
    names = name_map or {}
    return [
        {
            "umo": u,
            "title": window_display_title(u, name=names.get(u)),
            "name": names.get(u) or "",
        }
        for u in sorted(umos)
    ]


def window_display_title(umo: str | None, name: str | None = None) -> str:
    """Bot:平台-群聊/私聊-名称|ID（与 umo_display 一致）。"""
    from ..render.umo_display import format_umo_title

    return format_umo_title(umo, name=name)
