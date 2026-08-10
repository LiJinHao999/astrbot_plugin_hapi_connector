/**
 * 由 webui_settings_schema.export_config_schema 生成 — 勿手改结构。
 * 重新生成: python -c "from webui_settings_schema import export_config_schema_js_module; open('pages/console/settings_schema_fallback.js','w').write(export_config_schema_js_module())"
 */
export const CONFIG_SCHEMA_FALLBACK = {
  "groups": [
    {
      "id": "connection",
      "title": "连接 HAPI",
      "nav": "连接",
      "desc": "第一步：填 HAPI 服务地址和访问令牌，连上后其它功能才能用。改动连接类配置后插件会自动重连。",
      "fields": [
        {
          "key": "hapi_endpoint",
          "label": "HAPI 服务地址",
          "type": "text",
          "help": "HAPI Hub 的访问地址。本机一般是 http://127.0.0.1:3006；装在别的机器就写那台的地址和端口。",
          "default": "",
          "schema_type": "string",
          "need": true,
          "placeholder": "http://127.0.0.1:3006"
        },
        {
          "key": "access_token",
          "label": "Access Token",
          "type": "text",
          "help": "HAPI 的访问口令（部署 HAPI 时设置的那个）。支持 token:namespace 写法。注意此处明文显示。",
          "default": "",
          "schema_type": "string",
          "need": true
        },
        {
          "key": "proxy_url",
          "label": "代理（可选）",
          "type": "text",
          "help": "仅当 AstrBot 访问 HAPI 必须走代理时填写。支持 http:// 与 socks5h://。能直连请留空。",
          "default": "",
          "schema_type": "string",
          "placeholder": "socks5h://127.0.0.1:1080"
        }
      ],
      "advanced": {
        "title": "高级：Cloudflare Access / 重连 / JWT",
        "note": "自建直连多数不用改。HAPI 挂在 CF Access 后面，或 SSE 总断线，再展开。",
        "fields": [
          {
            "key": "cf_access_client_id",
            "label": "CF Access Client ID",
            "type": "text",
            "help": "Cloudflare Zero Trust Service Token 的 Client ID。未使用请留空。",
            "default": "",
            "schema_type": "string"
          },
          {
            "key": "cf_access_client_secret",
            "label": "CF Access Client Secret",
            "type": "password",
            "help": "与 Client ID 配对。不想改已有密钥就留空。",
            "default": "",
            "schema_type": "string",
            "sensitive": true
          },
          {
            "key": "max_reconnect_attempts",
            "label": "断线最大重连次数",
            "type": "number",
            "help": "连接断开后自动重试的次数，用完就休眠省资源。设 0 表示一直重试。休眠后在聊天里发 /hapi list 可唤醒。",
            "default": 10,
            "schema_type": "int"
          },
          {
            "key": "jwt_lifetime",
            "label": "JWT 有效期（秒）",
            "type": "number",
            "help": "登录凭证的有效时长，到期自动续。默认 900，一般不用改。",
            "default": 900,
            "schema_type": "int"
          },
          {
            "key": "refresh_before_expiry",
            "label": "JWT 提前刷新（秒）",
            "type": "number",
            "help": "凭证过期前多久去换新的。要小于上面的有效期，一般不用改。",
            "default": 180,
            "schema_type": "int"
          }
        ]
      }
    },
    {
      "id": "push",
      "title": "推送通知",
      "nav": "推送",
      "desc": "AI 干活时，聊天里推多少内容、以什么形式显示。快捷前缀、戳一戳、图片样式细调在「交互优化」页。",
      "fields": [
        {
          "key": "output_level",
          "label": "消息推送详细程度",
          "type": "enum_cards",
          "help": "有新输出时推到绑定窗口。越详细越容易刷屏；拿不准选「简洁」。",
          "default": "simple",
          "schema_type": "string",
          "need": true,
          "options": [
            {
              "value": "silence",
              "title": "静默",
              "desc": "平时不打扰，只在 AI 需要你批准操作或任务完成时提醒。"
            },
            {
              "value": "simple",
              "title": "简洁（推荐）",
              "desc": "推送 AI 说的话和重要事件，不推工具调用细节。"
            },
            {
              "value": "summary",
              "title": "摘要",
              "desc": "AI 干完一轮活后，把最后几条回复一起推给你（条数见下一项）。"
            },
            {
              "value": "detail",
              "title": "详细",
              "desc": "AI 的每条输出都实时推送，信息全但很刷屏。"
            }
          ]
        },
        {
          "key": "summary_msg_count",
          "label": "摘要条数",
          "type": "number",
          "help": "推送级别为「摘要」时，收尾推送 LLM 最后几条消息的条数。",
          "default": 5,
          "schema_type": "int",
          "showIf": {
            "key": "output_level",
            "eq": "summary"
          }
        },
        {
          "key": "render_mode",
          "label": "推送渲染模式",
          "type": "enum_cards",
          "help": "推到聊天里的内容以什么形式显示。图片模式对代码块、表格更友好（需安装 Pillow，可在「交互优化」页一键装）。",
          "default": "text",
          "schema_type": "string",
          "need": true,
          "options": [
            {
              "value": "text",
              "title": "纯文本",
              "desc": "全部以文字发送，兼容性最好。"
            },
            {
              "value": "card",
              "title": "图片",
              "desc": "把勾选的内容类型渲染成图片发送，排版更清晰。"
            }
          ]
        },
        {
          "key": "render_kinds",
          "label": "以下类型渲成图片",
          "type": "kind_checks",
          "help": "勾选哪些内容用图片显示：会话列表、待审批、状态、权限请求、推送路由、AI 对话、托管汇总、git 状态/统计。没勾的仍发文字。",
          "default": "session_list,pending,status,permission,routes,message,auto_approve_summary,git_status",
          "schema_type": "string",
          "showIf": {
            "key": "render_mode",
            "eq": "card"
          }
        },
        {
          "key": "auto_approve_silent",
          "label": "托管静默汇总",
          "type": "bool",
          "help": "开启后，忙时托管时段内的自动批准 / 自动压缩不再逐条推送，改为按下方策略汇总推送（如早晨一版）。关闭则保持现状逐条推。静默不等于关闭托管：AI 仍会自动执行，只是不刷屏。",
          "default": false,
          "schema_type": "bool",
          "warn": "静默只是把通知改成汇总推送，不会关掉自动批准本身。托管时段 AI 仍会自主执行全部操作。",
          "boolLabels": [
            "关闭（逐条推送）",
            "开启（汇总推送）"
          ]
        },
        {
          "key": "auto_approve_summary_mode",
          "label": "汇总方式",
          "type": "enum_cards",
          "help": "按托管时段：每次进入托管窗一个桶，窗结束结算（推荐）；按天：自然日一个桶；每次触发：每次自动动作立即推一版（较吵）。开启「托管静默汇总」后生效。",
          "default": "window",
          "schema_type": "string",
          "options": [
            {
              "value": "daily",
              "title": "按天",
              "desc": "自然日内的事件归一天，随时可手动推。"
            },
            {
              "value": "window",
              "title": "按托管时段（推荐）",
              "desc": "夜间托管结束一次性结算，最贴合睡眠场景。"
            },
            {
              "value": "per_event",
              "title": "每次触发",
              "desc": "每个自动动作立即推一版，接近实时但较吵。"
            }
          ]
        },
        {
          "key": "auto_approve_summary_push",
          "label": "推送时机",
          "type": "enum_cards",
          "help": "托管结束时：窗口结束边沿自动推送；每天固定时间：每天到点推「当前已积累」的一版。两种都可以随时用 /hapi summary 手动提前推。开启「托管静默汇总」后生效。",
          "default": "on_window_end",
          "schema_type": "string",
          "options": [
            {
              "value": "on_window_end",
              "title": "托管结束时（推荐）",
              "desc": "23:00–07:00 这种窗结束后立刻推一版。"
            },
            {
              "value": "at_fixed_time",
              "title": "每天固定时间",
              "desc": "每天在下方设置的时间推送，如 08:00。"
            }
          ]
        },
        {
          "key": "auto_approve_summary_time",
          "label": "固定推送时间",
          "type": "time",
          "help": "仅在推送时机为「每天固定时间」时生效。到点对每个有内容的 session 各推一版；没内容不推。",
          "default": "08:00",
          "schema_type": "string",
          "placeholder": "08:00",
          "showIf": {
            "key": "auto_approve_summary_push",
            "eq": "at_fixed_time"
          }
        },
        {
          "key": "auto_approve_summary_include_failures",
          "label": "汇总含失败明细",
          "type": "bool",
          "help": "开启时失败项在汇总里列明细（置顶展示）；关闭时只计失败次数、不列明细。开启「托管静默汇总」后生效。",
          "default": true,
          "schema_type": "bool",
          "boolLabels": [
            "关闭",
            "开启"
          ]
        },
        {
          "key": "auto_approve_summary_max_detail_lines",
          "label": "明细行数上限",
          "type": "number",
          "help": "单个 session 汇总里成功明细最多显示多少条，超出折叠为「另有 N 条」。开启「托管静默汇总」后生效。",
          "default": 30,
          "schema_type": "int"
        }
      ],
      "advanced": null
    },
    {
      "id": "approve",
      "title": "权限审批与托管",
      "nav": "审批",
      "desc": "AI 要跑命令、改文件前会先请求你批准。这里设置超时提醒和定时自动放行。",
      "fields": [
        {
          "key": "remind_pending",
          "label": "待审批超时提醒",
          "type": "bool",
          "help": "AI 的操作请求放着没批时，每隔一段时间在聊天里提醒你一次，免得忘了导致 AI 一直干等。",
          "default": true,
          "schema_type": "bool",
          "boolLabels": [
            "关闭",
            "开启"
          ]
        },
        {
          "key": "remind_interval",
          "label": "提醒间隔（秒）",
          "type": "number",
          "help": "两次提醒之间的秒数。间隔内处理完则不再提醒。",
          "default": 180,
          "schema_type": "int",
          "showIf": {
            "key": "remind_pending",
            "eq": true
          }
        },
        {
          "key": "auto_approve_enabled",
          "label": "定时自动批准（托管）",
          "type": "bool",
          "help": "设定一个时间段（比如睡觉时间），期间 AI 的操作请求自动放行，不用你起来批。",
          "default": false,
          "schema_type": "bool",
          "warn": "开启后，时段内 AI 的所有操作都会自动批准，包括改文件、跑命令。请确认你信任正在跑的任务。",
          "boolLabels": [
            "关闭（更安全）",
            "开启"
          ]
        },
        {
          "key": "auto_approve_start",
          "label": "托管开始时间",
          "type": "time",
          "help": "整段输入 24 小时制 HH:MM，如 23:00。",
          "default": "23:00",
          "schema_type": "string",
          "placeholder": "23:00",
          "showIf": {
            "key": "auto_approve_enabled",
            "eq": true
          }
        },
        {
          "key": "auto_approve_end",
          "label": "托管结束时间",
          "type": "time",
          "help": "整段输入 HH:MM；可跨午夜，如 23:00–07:00。",
          "default": "07:00",
          "schema_type": "string",
          "placeholder": "07:00",
          "showIf": {
            "key": "auto_approve_enabled",
            "eq": true
          }
        }
      ],
      "advanced": null
    }
  ],
  "defaults": {
    "hapi_endpoint": "",
    "access_token": "",
    "proxy_url": "",
    "cf_access_client_id": "",
    "cf_access_client_secret": "",
    "max_reconnect_attempts": 10,
    "jwt_lifetime": 900,
    "refresh_before_expiry": 180,
    "output_level": "simple",
    "summary_msg_count": 5,
    "quick_prefix": ">",
    "poke_approve": true,
    "poke_action": "approve",
    "cmd_keyword_maps": "[{\"keywords\":[\"stop\",\"停\"],\"command\":\"stop\"},{\"keywords\":[\"sw\"],\"command\":\"sw\"},{\"keywords\":[\"cl\"],\"command\":\"send\",\"args\":\"/clear\"},{\"keywords\":[\"继续\"],\"command\":\"send\",\"args\":\"继续\"},{\"keywords\":[\"专注\"],\"command\":\"focus\",\"args\":\"on\"},{\"keywords\":[\"退出专注\"],\"command\":\"focus\",\"args\":\"off\"},{\"keywords\":[\"hapi指令别名\"],\"command\":\"alias\"}]",
    "remind_pending": true,
    "remind_interval": 180,
    "auto_approve_enabled": false,
    "auto_approve_start": "23:00",
    "auto_approve_end": "07:00",
    "auto_approve_silent": false,
    "auto_approve_summary_mode": "window",
    "auto_approve_summary_push": "on_window_end",
    "auto_approve_summary_time": "08:00",
    "auto_approve_summary_include_failures": true,
    "auto_approve_summary_max_detail_lines": 30,
    "default_notification_window": "",
    "render_mode": "text",
    "formula_mode": "off",
    "render_kinds": "session_list,pending,status,permission,routes,message,auto_approve_summary,git_status",
    "card_style_preset": "terminal_light",
    "card_width": 720,
    "card_accent": "#0f6b3c",
    "card_bg": "#f7f4ea",
    "card_fg": "#14120f",
    "card_font_scale": 112,
    "card_density": "comfortable",
    "card_show_brand": false,
    "card_mono": false,
    "card_custom_css": "",
    "card_font_path": ""
  },
  "field_keys": [
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
    "render_mode",
    "render_kinds",
    "auto_approve_silent",
    "auto_approve_summary_mode",
    "auto_approve_summary_push",
    "auto_approve_summary_time",
    "auto_approve_summary_include_failures",
    "auto_approve_summary_max_detail_lines",
    "remind_pending",
    "remind_interval",
    "auto_approve_enabled",
    "auto_approve_start",
    "auto_approve_end"
  ]
};
