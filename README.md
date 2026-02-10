# HAPI Connector

AstrBot 插件 —— 通过聊天指令管理远程 AI 编码会话。

连接 [HAPI](https://github.com/tiann/hapi) 服务，在任意聊天平台（QQ、微信、Telegram 等）上操控 Claude Code / Codex / Gemini / OpenCode 会话，随时随地 vibe coding，支持实时消息推送、快捷审批。

所有指令仅管理员可用。

## 功能

- 列出、切换、查看 vibe coding 远程会话
- 向会话发送消息（指令或快捷前缀）
- 创建新会话（5 步交互式创建）
- 一键审批权限请求（`/hapi a` 或戳一戳机器人）
- 切换权限模式和模型
- 归档、重命名、删除会话
- 后台 SSE 实时推送 AI 输出和权限请求到聊天窗口，推送级别可调：
  - **silence**（默认）— 仅推送权限请求和等待输入提醒
  - **summary** — AI 思考完成后推送最近消息摘要
  - **debug** — 实时推送所有新消息（信息量较大）

## 安装

在 AstrBot 管理面板中搜索 `hapi_connector` 安装，或通过仓库地址手动安装：

```
https://github.com/LiJinHao999/astrbot_plugin_hapi_connector
```

依赖会自动安装（`aiohttp`、`aiohttp-socks`）。

## 配置

安装后在 AstrBot 管理面板的插件配置页填写：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `hapi_endpoint` | HAPI 服务地址，如 `http://10.200.11.13:3006` | |
| `access_token` | HAPI Access Token，支持 `token:namespace` 格式 | |
| `proxy_url` | 代理地址，支持 `socks5h://` 和 `http://` | 空 |
| `jwt_lifetime` | JWT 有效期（秒） | 900 |
| `refresh_before_expiry` | JWT 提前刷新时间（秒） | 180 |
| `output_level` | SSE 推送级别（下拉框）：`silence` / `summary` / `debug` | silence |
| `quick_prefix` | 快捷发送前缀字符 | `>` |
| `poke_approve` | 戳一戳自动全部审批（仅 QQ NapCat 可用） | 关闭 |

## 指令

所有指令以 `/hapi` 开头，仅管理员可用。

### 会话操作

| 指令 | 说明 |
|------|------|
| `/hapi list` | 列出所有会话（按工作目录分组） |
| `/hapi sw <序号>` | 切换当前会话 |
| `/hapi s` | 查看当前会话状态 |
| `/hapi msg [数量]` | 查看最近消息（默认 10 条） |

### 消息发送

| 指令 | 说明 |
|------|------|
| `/hapi to <序号> <内容>` | 发送消息到指定会话 |
| `> 消息内容` | 快捷发送到当前会话 |
| `>N 消息内容` | 快捷发送到第 N 个会话 |

### 会话管理

| 指令 | 说明 |
|------|------|
| `/hapi create` | 创建新会话（交互向导） |
| `/hapi archive` | 归档当前会话 |
| `/hapi rename` | 重命名当前会话 |
| `/hapi delete` | 删除当前会话 |

### 审批

| 指令 | 说明 |
|------|------|
| `/hapi a` | 全部批准待审批请求 |
| `/hapi deny` | 全部拒绝待审批请求 |
| 戳一戳机器人 | 全部批准（仅 QQ NapCat，需开启 `poke_approve`） |

### 权限与模型

| 指令 | 说明 |
|------|------|
| `/hapi perm [模式]` | 查看/切换权限模式 |
| `/hapi model [模式]` | 查看/切换模型 |

### 其他

| 指令 | 说明 |
|------|------|
| `/hapi help` | 显示帮助信息 |

## 快捷前缀

默认前缀为 `>`（可在配置中修改），可快速将消息发送至指定会话：

```
> 请帮我重构这个函数      → 发送到当前会话
>2 查看项目结构            → 发送到第 2 个会话
```

## 支持的 AI 代理

| 代理 | 权限模式 |
|------|----------|
| Claude Code | default, acceptEdits, bypassPermissions, plan |
| Codex | default, read-only, safe-yolo, yolo |
| Gemini | default, read-only, safe-yolo, yolo |
| OpenCode | default, yolo |

## 开发

插件结构：

```
astrbot_plugin_hapi_connector/
├── main.py              # 插件入口：指令组、前缀处理、生命周期
├── hapi_client.py       # 异步 HAPI HTTP 客户端 (aiohttp)
├── sse_listener.py      # 后台 SSE 监听 + 推送
├── session_ops.py       # Session 操作函数
├── formatters.py        # 格式化输出
├── constants.py         # 常量定义
├── _conf_schema.json    # 配置 schema
├── requirements.txt     # Python 依赖
└── metadata.yaml        # 插件元信息
```

## 许可

见 [LICENSE](LICENSE) 文件。
