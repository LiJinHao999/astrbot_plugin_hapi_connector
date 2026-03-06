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

只需在机器上通过 NPM 安装 HAPI，启动 AI 编码会话时加上 `hapi` 前缀，会话即自动接入 Hub 管理：

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
如果你想在手机上远程启动一个 session ，使用 /hapi create 命令，会立刻启动交互式会话并辅助你将session挂在后台

同时，也支持你通过astrbot远程启动claudecode/codex等vibe代理会话。
点击查看[部署＆连接插件教程](docs/install.md)


> **一句话总结**：AI 编码会话的远程控制台。

![架构图展示](docs/pics/Architecture.png)

---

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
2. AI 有新消息、权限请求、任务完成时，自动推送到你的聊天窗口
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
| `output_level` | SSE 推送级别：`silence` / `simple` / `summary` / `detail` | detail |
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

## ⌨️ 指令大全

所有指令以 `/hapi` 开头，**仅管理员可用**。

### 📋 会话查看

| 指令 | 说明 |
|------|------|
| `/hapi list` | 列出所有会话（别名 `ls`） |
| `/hapi sw <序号或ID前缀>` | 切换当前会话 |
| `/hapi s` | 查看当前会话状态（别名 `status`） |
| `/hapi msg [轮数]` | 查看最近消息，默认 1 轮（别名 `messages`） |

### 💬 消息发送

| 指令 | 说明 |
|------|------|
| `/hapi to <序号> <内容>` | 发送消息到指定会话 |
| `> 消息内容` | 快捷发送到当前会话 |
| `>N 消息内容` | 快捷发送到第 N 个会话 |

> 快捷前缀可在配置中修改，默认为 `>`

### 🛠️ 会话管理

| 指令 | 说明 |
|------|------|
| `/hapi create` | 创建新会话（5 步交互向导） |
| `/hapi abort [序号\|ID前缀]` | 中断会话，默认当前（别名 `stop`） |
| `/hapi remote` | 切换当前会话到 remote 远程托管模式 |
| `/hapi archive` | 归档当前会话 |
| `/hapi rename` | 重命名当前会话 |
| `/hapi delete` | 删除当前会话 |

### ✅ 权限审批

| 指令 | 说明 |
|------|------|
| `/hapi pending` | 查看待审批请求列表 |
| `/hapi a` | 批准所有权限请求 + 交互式回答 question（别名 `approve`） |
| `/hapi allow [序号]` | 仅批准普通权限请求（跳过 question） |
| `/hapi answer [序号]` | 交互式回答 question 请求 |
| `/hapi deny` | 全部拒绝 |
| `/hapi deny <序号>` | 拒绝单个请求 |
| 戳一戳机器人 | 批准所有普通权限请求 + 交互式回答 question（仅 QQ NapCat，需开启 `poke_approve`） |

### 🔧 模式切换

| 指令 | 说明 |
|------|------|
| `/hapi perm [模式]` | 查看/切换权限模式（不带参数则交互选择） |
| `/hapi model [模式]` | 查看/切换模型（仅 Claude，不带参数则交互选择） |
| `/hapi output [级别]` | 查看/切换 SSE 推送级别（别名 `out`） |
| `/hapi help` | 显示帮助信息 |

---

## 📡 SSE 推送级别说明

| 级别 | 说明 |
|------|------|
| `silence` | 仅推送权限请求和等待输入提醒，其余静默 |
| `simple` | AI 思考完成后推送纯文本 agent 消息及系统事件（过滤工具调用） |
| `summary` | AI 思考完成后推送最近 N 条 agent 消息（N 由 summary_msg_count 控制，过滤工具调用） |
| `detail` | 实时推送所有新消息（信息量较大，默认） |

---

## 🤖 支持的 AI 代理

| 代理 | 可用权限模式 |
|------|-------------|
| Claude Code | `default` / `acceptEdits` / `bypassPermissions` / `plan` |
| Codex | `default` / `read-only` / `safe-yolo` / `yolo` |
| Gemini | `default` / `read-only` / `safe-yolo` / `yolo` |
| OpenCode | `default` / `yolo` |

---

## 📁 插件结构

```
astrbot_plugin_hapi_connector/
├── main.py              # 插件入口：指令组、前缀处理、生命周期
├── hapi_client.py       # 异步 HAPI HTTP 客户端 + JWT 管理
├── cf_access.py         # Cloudflare Zero Trust Access 认证
├── sse_listener.py      # 后台 SSE 监听 + 消息推送
├── session_ops.py       # Session CRUD 操作
├── file_ops.py          # 文件查询与下载
├── approval_ops.py      # 审批业务逻辑
├── create_wizard.py     # 创建会话向导状态机
├── formatters.py        # 格式化输出
├── constants.py         # 常量定义
├── _conf_schema.json    # 配置 schema
└── metadata.yaml        # 插件元信息
```

---

## 📌 TODO

- ✅ 优化输出格式，提升交互可读性
- ✅ 完善部署文档与使用教程
- [ ] 支持文件上传与下载逻辑 - 预计将在1.5.0版本实现
- [ ] 支持将 Markdown 文字、AI编辑等影响观感长上下文渲染为图片（依赖库独立，可选下载）
- [ ] 通过 AstrBot 自然语言触发指令，让聊天 LLM 感知当前编码任务
- [ ] 支持多用户独立会话状态

---

## 🙏 致谢

- [HAPI](https://github.com/tiann/hapi) — 本插件连接的后端服务，由 [@tiann](https://github.com/tiann) 开发
- [AstrBot](https://github.com/Soulter/AstrBot) — 跨平台聊天机器人框架

---

## 👥 贡献指南

- 🌟 Star 本项目
- 🐛 提交 Issue 报告问题
- 💡 提出新功能建议
- 🔧 提交 Pull Request 改进代码

