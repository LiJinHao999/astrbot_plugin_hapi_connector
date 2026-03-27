<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_hapi_connector?name=astrbot_plugin_hapi_connector&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# HAPI Vibe Coding 遥控器

_✨ 随时随地 Vibe Coding ✨_

[![License](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0.html)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-3.4%2B-orange.svg)](https://github.com/Soulter/AstrBot)
[![GitHub](https://img.shields.io/badge/作者-LiJinHao999-blue)](https://github.com/LiJinHao999)

</div>

## 📦 安装方法

在 AstrBot 插件市场搜索 **hapi_connector**，点击安装即可。

或手动填入仓库地址安装：

```
https://github.com/LiJinHao999/astrbot_plugin_hapi_connector
```

依赖（`aiohttp`、`aiohttp-socks`）会由 AstrBot 自动安装。

项目需要后端，项目的后端服务为[HAPI](https://github.com/tiann/hapi)

点击查看[部署＆连接插件教程](docs/install.md)

---

## 🤝 这是干嘛用的？

这是一个**通过聊天指令远程管理 AI 编码会话的插件**。

你在外面摸鱼，电脑在家跑代码——通过这个插件，你可以在 QQ、微信、Telegram 等任意聊天平台上，直接操控跑在远端机器上的 Claude Code / Codex / Gemini / OpenCode，发消息、审批权限、切换模型，一条指令,甚至拍一拍QQ机器人搞定。

**它连接的后端是 [HAPI](https://github.com/tiann/hapi)**，一个统一用于方便管理多个 AI 编码代理会话后台运行和管理的服务，是 [HAPPY CODER](https://github.com/slopus/happy?tab=readme-ov-file) 的开源本地实现版本——**数据全部留在本地**

如果你的机器上已经安装过claude code、codex等软件，安装会非常简单

只需在机器上通过 NPM 安装 HAPI，启动 AI 编码会话时加上 `hapi` 前缀，会话即自动接入 Hub 管理，关闭终端则会自动停止（inactive）：

```bash
hapi claude   # Claude Code
hapi codex    # OpenAI Codex
```
当然，如果你在服务器上使用，期望长时间挂在后台，需要使用screen命令

```bash
screen -S hapi
hapi codex    # Open Codex
然后 : 按 ctrl+A ，ctrl+D
```


同时，也支持你通过astrbot远程启动claudecode/codex等vibe代理会话。
点击查看[部署＆连接插件教程](docs/install.md)

如果你想在手机上远程启动一个 session ，使用 **/hapi create** 命令，会立刻启动交互式会话并辅助你将session挂在后台（需要参考上方配置教程启动runner服务哦）

> **一句话总结**：AI 编码会话的远程控制台。

![架构图展示](docs/pics/Architecture.png)

---

## ✨ 为什么选择此远程方案？
- **无缝切换**: 离开电脑后可以随时用手机接管
- **远程vibe**: 使用插件可以在远程随意启动claudecode/codex/geminicil，随时随地开启一个新的交互
- **本地部署**: 本地部署，极低延迟，同时不需要公网ip
- **充分利用聊天软件的聊天窗口**: 参考[窗口隔离特性介绍](docs/session-isolation.md)，你可以在群聊A、B、C中随意切换、在私聊/不同群聊聊天窗口中接收和管理不同的远程vibe会话，不局限于一个窗口下的交互和通知
- **astrbot 原生 FC 能力集成**: 自然语言即可管理会话，支持类CC/CX的工具调用审批，工具严格隔离，在没有远程vibe会话中的群聊自动关闭工具，不污染上下文
- **文件双向传输**: 支持利用astrbot进行小文件的下载、上传，方便查看日志或传递配置
- **兼容QQ、微信的官方bot**: 无法主动推送消息时将消息fallback伪装为被动回复，兼容QQ官方bot、微信clawbot
- **智能审批机制**: 支持戳一戳快速批准、忙时自动托管、超时提醒，灵活应对不同场景


## 💡 实际应用场景

- **离开电脑时继续推进任务**：手机上发一条消息，让 Claude Code 继续干活
- **快速审批权限请求**：AI 要执行危险操作时，戳一戳机器人或发 `/hapi a` 一键放行
- **忙时全自动审批**：忙时如睡眠时可以自动接管权限，一键放行与长时间托管
- **将vibe coding窗口切到后台时接收原生聊天软件的通知**：方便vibe时摸鱼、做其他事，提升效率
- **多会话并行管理**：同时跑多个项目，随时切换、查看进度
- **实时接收 AI 输出**：后台 SSE 推送，AI 说了什么、做了什么，第一时间推到聊天窗口

---

## 🧠 怎么工作的？

1. 插件启动后连接 HAPI 服务，建立 SSE 长连接监听所有事件
2. AI 有新消息、权限请求、任务完成时，按当前窗口绑定规则自动推送到对应聊天窗口
3. 你发指令 → 插件调用 HAPI REST API → 操作对应的 AI 会话
4. 快捷前缀（默认 `>`）让你不用打 `/hapi to` 长串命令也能快速发消息，同时和astrbot原生对话区分开

---

## ⚙️ 配置

安装后在 AstrBot 管理面板的插件配置页填写：

### 连接与认证

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `hapi_endpoint` | HAPI 服务地址，如 `http://0.0.0.0:3006` | |
| `access_token` | HAPI Access Token，支持 `token:namespace` 格式，关于namespace，见[官方文档说明](https://github.com/tiann/hapi/blob/main/docs/guide/namespace.md) | |
| `proxy_url` | 代理地址，支持 `socks5h://` 和 `http://` | 空 |
| `cf_access_client_id` | [Cloudflare Zero Trust](https://developers.cloudflare.com/cloudflare-one/identity/service-tokens/) Service Token 的 Client ID，详见[部署说明](docs/cf_access_guide.md) | 空 |
| `cf_access_client_secret` | Cloudflare Zero Trust Service Token 的 Client Secret | 空 |
| `jwt_lifetime` | JWT 有效期（秒） | 900 |
| `refresh_before_expiry` | JWT 提前刷新时间（秒） | 180 |

### 推送与交互

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `output_level` | SSE 推送级别：`silence` / `simple` / `summary` / `detail` | simple |
| `summary_msg_count` | summary 级别显示的 agent 消息条数 | 5 |
| `quick_prefix` | 快捷发送前缀字符 | `>` |
| `poke_approve` | 戳一戳自动全部审批（仅 QQ NapCat） | 开启 |

### 自动审批

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `remind_pending` | 待审批请求超时重复提醒，防止 AI 会话缓存失效 | 开启 |
| `remind_interval` | 待审批提醒间隔（秒），倒计时内处理完则不提醒 | 180 |
| `auto_approve_enabled` | 忙时托管审批：在指定时间范围内自动批准所有权限请求 | 关闭 |
| `auto_approve_start` | 忙时托管审批开始时间（HH:MM，24小时制） | `23:00` |
| `auto_approve_end` | 忙时托管审批结束时间（HH:MM，24小时制，支持跨午夜） | `07:00` |

---

## ⌨️ 使用指南

所有指令以 `/hapi` 开头，**仅管理员可用**。
**如果不想记指令也没关系，插件现已支持使用Astrbot原生function calling工具触发，请注意把相关工具打开**

### 🤖 LLM 工具集成（自然语言交互）

插件提供 11 个 Function Calling 工具，支持用自然语言管理远程会话：

| 工具名 | 说明 |
|--------|------|
| `hapi_coding_list_sessions` | 列出 session 列表（支持窗口/路径/代理过滤） |
| `hapi_coding_get_status` | 获取当前 session 状态 |
| `hapi_coding_message_history` | 查询历史消息 |
| `hapi_coding_get_config_status` | 查看插件配置 |
| `hapi_coding_list_commands` | 列出可用指令（按主题分类） |
| `hapi_coding_send_message` | 发送消息到当前 session |
| `hapi_coding_switch_session` | 切换 session |
| `hapi_coding_create_session` | 创建新 session |
| `hapi_coding_stop_message` | 停止消息生成 |
| `hapi_coding_change_config` | 修改插件配置 |
| `hapi_coding_execute_command` | 执行任意 /hapi 指令 |

**使用方式**：在 Astrbot 管理面板开启工具后，直接对话即可，如"切换到1号session"、"创建一个 Claude 会话"。

**Codex 创建补充**：`hapi_coding_create_session` 现在支持可选参数 `model_reasoning_effort`。留空时不会向 HAPI 显式传该字段，Codex 将继承默认设置；具体使用哪套默认设置取决于 Codex 进程实际运行的用户。只有显式填写 `none/minimal/low/medium/high/xhigh` 时才会覆盖默认值。

**推荐配置**：建议至少激活 `hapi_coding_list_commands`。如需覆盖尚未封装的 `/hapi` 子命令，再启用 `hapi_coding_execute_command`；常见操作优先使用结构化工具（如切换、创建、发消息、改配置）。

**审批机制**：操作类工具需管理员审批（支持 `/hapi a` 批准、`/hapi deny` 拒绝、戳一戳快速批准），防止模型误操作。

**智能隔离**：非管理员不会注册任何工具；当前窗口没有可见 HAPI 会话时，仅保留 `hapi_coding_list_sessions`、`hapi_coding_list_commands`、`hapi_coding_execute_command` 3 个基础工具，避免污染上下文。

---

### ⌨️ 指令列表

#### 📋 会话查看

| 指令 | 说明 |
|------|------|
| `/hapi list` | 查看当前聊天窗口可接收通知的 session（别名 `ls`） |
| `/hapi list all` | 查看全部 session 和全局绑定状态 |
| `/hapi sw <序号或ID前缀>` | 切换当前会话 |
| `/hapi s` | 查看当前会话状态（别名 `status`） |
| `/hapi msg [轮数]` | 查看最近消息，默认 1 轮（别名 `messages`） |

#### 💬 消息发送

| 指令 | 说明 |
|------|------|
| `/hapi to <序号> <内容>` | 发送消息到指定会话 |
| `> 消息内容` | 快捷发送到当前会话 |
| `>N 消息内容` | 快捷发送到第 N 个会话 |

> 快捷前缀可在配置中修改，默认为 `>`

#### 🛠️ 远程 session 管理

| 指令 | 说明 |
|------|------|
| `/hapi create` | 创建新会话（交互向导；Codex 为 6 步，其他为 5 步） |
| `/hapi abort [序号\|ID前缀]` | 中断会话，默认当前（别名 `stop`） |
| `/hapi remote` | 切换当前会话到 remote 远程托管模式 |
| `/hapi archive` | 归档当前会话 |
| `/hapi rename` | 重命名当前会话 |
| `/hapi delete` | 删除当前会话 |
| `/hapi clean [路径前缀]` | 批量清理 inactive session |

> Codex 创建补充：默认会继承 Codex 默认设置中的思考深度；只有你在创建时显式选择 `none/minimal/low/medium/high/xhigh` 时，插件才会覆盖默认值。

#### ✅ 权限审批

| 指令 | 说明 |
|------|------|
| `/hapi pending` | 查看待审批请求列表 |
| `/hapi a` | 批准所有权限请求 + 交互式回答 question（别名 `approve`） |
| `/hapi allow [序号]` | 仅批准普通权限请求（跳过 question） |
| `/hapi answer [序号]` | 交互式回答 question 请求 |
| `/hapi deny` | 全部拒绝 |
| `/hapi deny <序号>` | 拒绝单个请求 |
| 戳一戳机器人 | 批准所有普通权限请求 + 交互式回答 question（仅 QQ NapCat，需开启 `poke_approve`） |

#### 📁 文件操作

| 指令 | 说明 |
|------|------|
| `/hapi files [路径]` | 浏览当前 session 的远端目录 |
| `/hapi files -l [路径]` | 浏览目录并显示文件大小 |
| `/hapi find <关键词>` | 搜索当前 session 的远端文件 |
| `/hapi download <路径>` | 下载远端文件到当前聊天（别名 `dl`） |
| `/hapi upload [cancel]` | 上传文件到当前 session，支持交互上传和取消 |

#### 🔧 模式与帮助

| 指令 | 说明 |
|------|------|
| `/hapi perm [模式]` | 查看/切换权限模式（不带参数则交互选择） |
| `/hapi model [模式]` | 查看/切换模型（仅 Claude，不带参数则交互选择） |
| `/hapi output [级别]` | 查看/切换 SSE 推送级别（别名 `out`） |
| `/hapi help [主题]` | 显示帮助信息，主题可选：会话 / 对话 / 审批 / 通知 / 文件 / 配置 |

---

## 📡 SSE 推送级别说明

| 级别 | 说明 |
|------|------|
| `silence` | 仅推送权限请求和等待输入提醒，其余静默 |
| `simple` | AI 思考完成后推送纯文本 agent 消息及系统事件（过滤工具调用） |
| `summary` | AI 思考完成后推送最近 N 条 agent 消息（N 由 summary_msg_count 控制，过滤工具调用） |
| `detail` | 实时推送所有新消息（信息量较大） |

---

## 🤖 支持的 AI 代理

| 代理 | 可用权限模式 |
|------|-------------|
| Claude Code | `default` / `acceptEdits` / `bypassPermissions` / `plan` |
| Codex | `default` / `read-only` / `safe-yolo` / `yolo` |
| Gemini | `default` / `read-only` / `safe-yolo` / `yolo` |
| OpenCode | `default` / `yolo` |

---
---

## 🔔 通知路由特性

- **按聊天窗口隔离**：私聊、群聊、不同群之间互不影响，每个窗口只接收属于自己的会话通知与审批请求
- **支持默认通知窗口**：`/hapi bind` 把当前聊天窗口设为默认通知窗口
- **支持模型级默认窗口**：`/hapi bind claude|codex|gemini` 可以分别给不同类型的vibe coding 远程 session 指定默认通知窗口
- **会话绑定优先级最高**：某个 session 一旦被当前聊天窗口接管，后续通知优先回到该窗口
- **查看范围明确**：`/hapi list` 只展示当前窗口可见的 session，`/hapi list all` 和 `/hapi bind status` 用来查看全局状态


### 🔔 通知推送管理

| 指令 | 说明 |
|------|------|
| `/hapi bind` | 设置当前聊天窗口为默认通知窗口 |
| `/hapi bind claude` | 设置当前聊天窗口为 Claude 的默认通知窗口 |
| `/hapi bind codex` | 设置当前聊天窗口为 Codex 的默认通知窗口 |
| `/hapi bind gemini` | 设置当前聊天窗口为 Gemini 的默认通知窗口 |
| `/hapi bind status` | 查看默认窗口、模型默认窗口和 session 绑定状态 |
| `/hapi bind reset` | 清除 session 绑定和窗口状态，保留默认通知窗口配置 |
| `/hapi routes` | 查看当前生效的会话推送路由 |


## 📁 插件结构

```
astrbot_plugin_hapi_connector/
├── main.py                 # 插件入口：生命周期、LLM 工具注册、戳一戳/快捷前缀处理
├── command_handlers.py     # 所有 /hapi 子命令处理器
├── llm_integration.py      # LLM Function Calling 工具集成（10个工具）
├── state_manager.py        # 用户状态管理（当前 session、flavor、路由）
├── notification_manager.py # 通知推送与消息分发
├── pending_manager.py      # 待审批请求管理（序号分配、批准/拒绝）
├── binding_manager.py      # 聊天窗口与 session 绑定管理
├── hapi_client.py          # 异步 HAPI HTTP 客户端 + JWT 自动刷新
├── cf_access.py            # Cloudflare Zero Trust Access 认证
├── sse_listener.py         # 后台 SSE 监听 + 实时事件推送
├── session_ops.py          # Session CRUD 操作封装
├── file_ops.py             # 文件查询、上传、下载
├── approval_ops.py         # 审批业务逻辑
├── create_wizard.py        # 创建会话交互式向导
├── formatters.py           # 格式化输出工具
├── constants.py            # 常量定义（权限模式、模型、代理类型、Codex 思考深度）
├── _conf_schema.json       # 插件配置 schema
└── metadata.yaml           # 插件元信息
```

---

## 📌 TODO

- ✅ 优化输出格式，提升交互可读性
- ✅ 完善部署文档与使用教程
- ✅ 支持文件上传与下载逻辑
- ✅ 支持多用户独立会话状态，通知相互隔离
- ✅ 通过 AstrBot 自然语言触发指令
- [ ] 支持将 Markdown 文字、AI编辑等影响观感长上下文渲染为图片（依赖库独立，可选下载）

---

## 🙏 致谢

- [HAPI](https://github.com/tiann/hapi) — 本插件连接的后端服务，由 [@tiann](https://github.com/tiann) 开发
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) — 跨平台聊天机器人框架

---
## 💗 友情链接
- [linuxdo社区](https://linux.do/) — 极度优秀的 AI 知识分享社区
- [linuxdo上关于此插件的设计思路贴](https://linux.do/t/topic/1799761)
---

## 👥 贡献指南

- 🌟 Star 本项目
- 🐛 提交 Issue 报告问题
- 💡 提出新功能建议
- 🔧 提交 Pull Request 改进代码
