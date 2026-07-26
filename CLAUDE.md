# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

AstrBot 插件：**HAPI 编程助手遥控器**。通过聊天指令（QQ / 微信 / Telegram 等）远程操控 [HAPI](https://github.com/tiann/hapi) 上的 Claude / Codex / Cursor / Grok / Kimi / OpenCode / Pi 等 AI 编程会话。

- 运行环境：AstrBot 3.4+（插件 API），Python 3.10+
- 许可证：AGPLv3
- 版本以 `metadata.yaml` 与 `main.py` 的 `@register(..., "x.y.z")` 为准（当前 v3.2.0）
- 后端：HAPI Hub（默认 `http://127.0.0.1:3006`），部署说明见 `docs/install.md`

本仓库是**纯插件包**，无独立 build / lint / test 脚本；依赖由 AstrBot 按 `requirements.txt` 自动安装。

## 常用开发方式

```bash
# 依赖（通常由 AstrBot 安装；本地也可）
pip install -r requirements.txt

# 语法检查（无专用 lint 配置时可用）
python -m py_compile *.py

# 运行：在 AstrBot 中安装/重载本插件
# 插件市场：hapi_connector
# 或填仓库：https://github.com/LiJinHao999/astrbot_plugin_hapi_connector
```

改代码后一般在 AstrBot 管理面板**重载插件**即可；无需单独启动本仓库。联调需要本机或远程 HAPI Hub 可访问，并在插件配置中填写 `hapi_endpoint` 与 `access_token`。

配置 schema：`_conf_schema.json`。插件元信息：`metadata.yaml`。

## 架构总览

```
聊天平台 ──► AstrBot ──► HapiConnectorPlugin (main.py)
                              │
          ┌───────────────────┼───────────────────┬──────────────────┐
          ▼                   ▼                   ▼                  ▼
   CommandHandlers      LLMIntegration      快捷前缀 / 戳一戳   web_api + pages/console
   (/hapi 路由)         (FC 工具 + 审批)     (quick_prefix / poke)  (Dashboard 管理面板)
          │                   │                                      │
          └─────────┬─────────┘                                      │
                    ▼                                                │
            session_ops / file_ops / approval_ops / create_wizard ◄───┘
                    ▼
            AsyncHapiClient ──REST/SSE──► HAPI Hub
                    ▲
            SSEListener ──事件──► NotificationManager ──► 聊天窗口
                    │
            PendingManager（权限/问答/LLM 工具审批序号）
                    │
            StateManager + BindingManager（窗口隔离与路由，KV 持久化）
```

### 源码目录（3.0 版本后对 py 文件进行了分包）

| 目录 | 职责 |
|------|------|
| `main.py` / `constants.py` | 插件入口与兼容常量 |
| `core/` | HAPI 客户端、SSE、绑定 / 状态、session / file / approval ops |
| `chat/` | 指令处理、创建向导、关键词、戳一戳、LLM 工具、flavor 能力表 |
| `render/` | 文案 formatters、卡片 / 字体 / 出图、UMO 展示 |
| `webui/` | Plugin Pages API 与设置 schema |
| `pages/console/` | Web 管理面板静态资源（扫描路径仍为根级 `pages/`） |

### 模块职责

| 模块 | 职责 |
|------|------|
| `main.py` | 插件入口 `HapiConnectorPlugin(Star)`：组装依赖、生命周期、`/hapi` 命令、LLM 工具代理、戳一戳、快捷前缀；注册 WebUI `register_pages`；含 AstrBot File 组件 setattr 兼容补丁 |
| `chat/command_handlers.py` | 所有 `/hapi` 子命令路由与实现（最大业务文件） |
| `chat/llm_integration.py` | Function Calling：可见性裁剪、操作类工具审批、工具实现 |
| `core/hapi_client.py` | 异步 HTTP：JWT 获取 / 缓存 / 刷新、401 重试、代理、`/api/events` SSE |
| `core/cf_access.py` | Cloudflare Zero Trust Service Token 请求头 |
| `core/sse_listener.py` | 后台 SSE：权限请求、消息推送级别、自动审批时段、重连 / 休眠、待审批序号池；`get_connection_status` / `pending_counts` 供 WebUI |
| `core/notification_manager.py` | 按路由推送到 UMO 窗口；去重；长消息分片；被动回复 fallback |
| `core/binding_manager.py` | 内存绑定：`session → 唯一窗口`、`窗口 → 多 session`、窗口当前 session / flavor |
| `core/state_manager.py` | 用户状态 + 通知路由策略 + AstrBot KV 持久化 / 迁移 |
| `core/pending_manager.py` | 待审批扁平化、批准 / 拒绝、question 交互式问答、LLM 工具假权限请求 |
| `core/session_ops.py` | HAPI session REST 封装（列表、发消息、resume、权限 / 模型 / effort 等） |
| `core/file_ops.py` | 远端列目录 / 搜索 / 上传 / 下载；从聊天消息抽附件 |
| `core/approval_ops.py` | 审批 API 调用封装 |
| `chat/create_wizard.py` | `/hapi create` 交互向导（按 profile 动态步骤，如 reasoning effort） |
| `render/formatters.py` | 用户可见文案、帮助、session 标题 `get_session_title`、审批 / 列表格式；`export_help_data` 供 WebUI |
| `chat/flavor_profiles.py` | AI 代理 flavor 能力表：权限 / 模型 / effort / plan / 可创建；未知类型降级；`export_profiles_meta` 供 WebUI |
| `webui/web_api.py` | AstrBot Plugin Pages 后端：`register_pages`、配置落盘、snapshot、会话运维、路由写、重连、docs 读盘、render 预览 |
| `pages/console/` | Web 管理面板静态资源（`index.html` / `app.js` / `api.js` / `style.css` 等） |
| `constants.py` | 兼容导出 + `SESSION_TYPES` |

### 数据流要点

1. **指令路径**：管理员发 `/hapi ...` 或快捷前缀 `>` → `CommandHandlers` / `main.quick_prefix_handler` → `session_ops` 调 HAPI API。
2. **事件路径**：`SSEListener` 订阅 `GET /api/events` → 按 `output_level`（silence/simple/summary/detail）格式化 → `NotificationManager.push_notification` 按路由投递到窗口。
3. **发送前恢复**：`ensure_session_for_send` 对 inactive session 尝试 `resume`，成功后 `capture_window` 绑定当前窗口。

### 多窗口会话隔离（核心领域模型）

按 AstrBot 的 `unified_msg_origin`（UMO）隔离。详图见 `docs/session-isolation.md`。

通知目标选择（`StateManager.select_notification_targets`，同一通知只打一个窗口）：

1. **Session 绑定窗口**（`sw` / `create` / 发送后 `capture_window`）— 最高优先  
2. **Flavor 默认窗口**（`/hapi bind <flavor>`，如 claude/codex/cursor）  
3. **用户默认通知窗口**（`/hapi bind` → `primary_umo`）

可见列表：`/hapi list` = 当前窗口可见；`/hapi list all` / `bind status` = 全局。

KV 键（经 `Star.put_kv_data` / `get_kv_data`）：`known_users`、`user_state_{uid}`、`session_owners`、`window_state_{umo}`。`migrate_to_capture_model` 负责旧字段（`notify_umo` 等）迁移。

### 权限与 LLM 工具

- **所有** `/hapi` 与快捷前缀仅管理员（`context.get_config(...).admins_id`）。
- LLM 工具名以 `hapi_coding_` 为前缀；`on_llm_request_hook` 动态裁剪：  
  - 非管理员：全部移除  
  - 窗口无可见 session：仅保留 `list_sessions` / `list_commands` / `execute_command`  
- 操作类工具走 `PendingManager.add_llm_tool_request`，与 HAPI 权限请求共用 `/hapi a` / `deny` / 戳一戳审批。

### HAPI 客户端约定

- Auth：`POST /api/auth` + `accessToken` → JWT；SSE 用 query `token`。
- `access_token` 支持 `token:namespace`（与 HAPI namespace 一致，见上游文档）。
- 可选 CF Access 头；SSE 校验 `Content-Type: text/event-stream`，否则 `ContentTypeError`。
- 响应使用后需 `resp.release()`（见 `session_ops` 模式）。

### 支持的 AI 代理（flavor）

能力与权限模式集中在 `chat/flavor_profiles.py`（`constants.py` 仅做兼容导出），不要硬编码散落：

- 全量：claude / codex / cursor / gemini / grok / kimi / opencode / pi  
- 可新建：除 gemini 外（Gemini CLI 已 sunset，仅兼容旧 session）  
- 未知 flavor：通用操作可用，创建允许尝试，差异能力按 profile 降级  
- Plan / effort 指令依赖较新的 HAPI 版本（见 `CHANGELOG.md` v2.1.0 / v2.2.0）

## 文档地图

| 路径 | 内容 |
|------|------|
| `README.md` | 功能、配置项、Web 管理面板、完整指令、SSE 级别、插件结构 |
| `CHANGELOG.md` | 版本行为变更（WebUI、resume、plan / effort、FC 工具、隔离模型） |
| `docs/install.md` | HAPI Hub 安装与连接 |
| `docs/session-isolation.md` | 多窗口隔离规则 |
| `docs/cf_access_guide.md` | Cloudflare Access 配置 |
| `dev-docs/plugin-pages.md` | AstrBot 插件页面 API |
| `dev-docs/webui开发计划.md` | WebUI 信息架构与 API checklist |
| `dev-docs/web-api.md` | HAPI Hub REST / SSE 协议对照 |

## 修改时注意

- **版本号**：同时改 `metadata.yaml` 的 `version` 与 `main.py` `@register` 第四参；重要变更写 `CHANGELOG.md`。  
- **管理员门禁**：新指令 / 新事件处理器必须保留 `_is_admin` 检查。  
- **窗口路由**：改绑定或推送逻辑时对照 `visible_sessions_for_window` / `select_notification_targets`，避免跨窗口串通知。  
- **文案**：用户可见输出集中在 `render/formatters.py`；session 展示标题用 `get_session_title`。  
- **中英混用**：部分 `session_ops.send_message` 等返回串为英文，命令层提示多为中文；新增用户可见消息优先中文并与现有风格一致。  
- **WebUI**：新 Page API 放 `webui/web_api.py`；同步 `api.js` 封装；敏感配置与落盘策略见开发计划 §4.2。  
- **无自动化测试**：改审批、SSE 完成态、resume、绑定路由、Web snapshot 等路径时，按场景做手动联调（active / inactive、question vs 普通权限、多窗口、面板改配置后重载仍生效）。
