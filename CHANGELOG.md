# 更新日志

## v3.2.3

1. **支持了agent侧的图片发送和渲染**
对于agent推送的消息，图片渲染模式下，对话卡可嵌图片，并按卡宽适配；纯文本模式也支持发送图片。
现在你可以让agent为你截图，并将图片发送给你了。  


2. 修复和维护了图片与文本的一些可能遇到的渲染问题


3. **过滤 MCP / SDK 进度噪音；纯文本推送恢复 emoji 标签**  
   `tool_progress` 心跳、session 元数据信封等不再整段 JSON 当正文推到对话卡。  

4. **修复了多 agent（sidechain）子代理渲染支持**  
   simple / summary 默认不展示子代理正文；detail 以 `【子代理:名称】` 标记，对话卡渲染为**独立小卡**（标题可识别子 agent）。子代理任务完成时不会再错误发送”任务已完成“消息。

5. **调整了 Focus 模式下发送图片时的去重逻辑**

## v3.2.1

1. **修复 Focus 模式下 `/` 开头消息的拦截判断失效**  
   AstrBot 的 `WakingCheckStage` 会在插件处理器之前剥离唤醒前缀（`/help` 到插件手里已是 `help`），导致 Focus 的「`/` 开头命令不转发」判断实际从未生效——其它 AstrBot 指令会被误转发给 AI 并被吞掉。现改为回看真正的原文判断（兼容自定义唤醒前缀）。

2. **修复 Focus / 快捷前缀发送图片时同一张图被上传两次**  
   AstrBot 常把同一附件同时落成 `media_image_*.jpg` 与 `download.jpg` 两份本地缓存（路径不同、内容相同），旧逻辑只按 path/url 去重，会把一份图当成两个 attachments 发出。现上传前按内容 SHA-256 去重，重复项直接跳过。

3. **修复focus模式下附件直接发送，不随文字消息发送的问题**

## v3.2.0 — 支持 Focus 模式完全接管 Astrbot 会话、支持在 webui 设置快速创建agent的模板

1. **新增 Focus 模式 用于让 HAPI 完全接管 astrbot 会话**  
   - `/hapi focus on`：开启专注模式，当前窗口的普通消息自动发送到当前选中的 session，无需快捷前缀 `>`（`/` 开头命令、`hapi` 开头消息、关键词别名仍按原样处理）
   - `/hapi focus off`：关闭专注模式；状态持久化，重启后自动恢复
   - 快捷指令：`专注` → `/hapi focus on`，`退出专注` → `/hapi focus off`
   - 仅对单个聊天窗口的当前 session 生效，不影响其他窗口或 session
   - WebUI 会话管理页：窗口列表显示 Focus 标签，面板标题旁可直接开关
   - **此模式下直接发送文件附件/图片都会直接移交给 HAPI agent****

2. **新增创建 Agent 模板功能**  
   - WebUI「交互优化 → 会话模板」可以把常用组合（代理 / 目录 / 机器 / 类型 / YOLO / 思考深度）存成命名模板，用于快速启动
   - 在聊天中使用命令 `/hapi create <模板名> [目录]` 一步创建，跳过向导；目录参数可覆盖模板默认，模板目录留空时必须传参
   - 不带参数的 `/hapi create` 仍走交互向导，有模板时会先列出可用模板

3. **新增 `/hapi retry`**  命令
   重发本窗口上一条发出的消息（快捷前缀 / Focus / send / to / LLM 工具发的都会记录），AI 无响应或断线重连后使用；记录在内存，重启后清空

4. **新增《插件使用指南》文档**  
   `docs/usage-guide.md`：从上手流程、Focus 模式、审批、通知路由到常见问题的完整使用说明；WebUI「部署文档」页可直接阅读

5. **代码质量改进**  
   - 修复 `session_ops.py` 中 `send_message` 重复定义 bug（英文版覆盖中文版，导致发送回执显示英文）
   - 提取公共 helper 方法（`_visible_sids`、`_require_sid`、`_resolve_target_verbose` 等），消除重复代码
   - 统一推送标签格式：`[System]` → 【系统】，`[Summary]` → 【总结】，`[Message]` → 【消息】
   - 中文化错误提示与操作反馈（`✗` 前缀 + 失败原因说明）
   - 符号标准化：`◀ 当前`、`⚠️`（变体符）、待审批计数格式

## v3.1.0

1. **修复主动通知 Markdown 显示异常**（见 pr [#25](https://github.com/LiJinHao999/astrbot_plugin_hapi_connector/pull/25)）  
   修改推送链路，尝试解决了主动推过来的消息，在部分平台（比如 QQ 官方机器人）上会把 `# 标题`、`**加粗**` 这类 Markdown 原样显示、不渲染的问题

2. **WebUI 增加消息发送测试**（同上 PR）  
   在管理页中可以自己填一段文字/Markdown，用不同方式试发到聊天窗口，方便检查显示效果。

## v3.0.0 大更新 - webui支持、可个性化的交互优化、推送图片渲染支持
感谢还在使用插件的各位朋友，本次更新维护的主题是配置直观性和使用体验的调整优化。

1、Web 管理面板（AstrBot Plugin Pages）
插件添加了设置面板，请将astrbot提升至支持webui的版本，以更好地管理各项设置与session情况。欢迎使用和体验

2、引入了将 AI 输出的 Markdown 文字/公式渲染为图片的初步支持。可在webui中预览和启用。依赖包与字体可在webui页进行可选下载。
注：公式渲染目前在较复杂公式的情况下可能仍有渲染不佳/识别不到位的问题。

3、添加了对指令别名的支持。现在你可以将任意字符设置为指令的别名，以个性化地精简调用和输入。

默认开启的关键词监听映射有：
stop，停 -> /hapi stop 命令
sw -> /hapi sw 命令
cl -> /hapi send /clear 命令，即对当前agent发送/clear指令清除会话上下文
继续 -> /hapi send 继续 ，即对当前agent直接发送一条“继续”的消息
hapi指令别名 -> /hapi alias 命令，即查看当前别名关键词映射情况

关键词只会在当前有交互中的session，并且用户是管理员时被触发。无须担心误触。

## v2.3.0 — 同步 HAPI 0.21–0.23 遥控器能力

对齐上游 HAPI Hub API（约 0.21.0 ~ 0.23.0），补齐聊天侧遥控缺口：

1. **新增 `/hapi fast [on|off]`**：Codex Fast mode（`POST /api/sessions/:id/service-tier`，`fast` / `standard`）
2. **新增 `/hapi reopen [序号|ID前缀]`**：`POST /api/sessions/:id/reopen`（resume 备用接口）
3. **OpenCode 支持 reasoning effort**：与 Codex 同走 `/model-reasoning-effort`；列表外值可透传（上游动态 options）
4. **Effort / 模型枚举对齐上游**：
   - Claude effort：`low` / `medium` / `high` / `xhigh` / `max`（+ auto）
   - Pi thinking：`off` / `minimal` / `low` / `medium` / `high` / `xhigh` / `max`
   - Codex/OpenCode reasoning：补 `max`，允许动态透传
   - Claude 模型预设：补充了 `fable` / `fable[1m]`

## v2.2.0 — Agent 类型兼容拓展

对齐 HAPI 最新版本，远程控制管理支持 `claude/codex/cursor/gemini/grok/kimi/opencode/pi`

## v2.1.4

修复 `/hapi create` 向导在 Windows 上输入盘符路径（如 `C:\Users\...`）时被错误添加前缀 `/` 的问题

## v2.1.3

修复 `formatters.py` 中 `format_bind_status` 函数的 bug：修复 `owner` 变量未定义导致的 `NameError`

## v2.1.2

修复 `/hapi resume` 相关问题，并在hapi服务端出现错误时补充更完善的报错说明 

## v2.1.1 
1. 支持与 Codex 对话时的交互模式问答（选项 + 可选备注）
2. 完善和优化了交互问答流程，逐题回答完成后将会显示汇总消息。

## v2.1.0 — 同步 HAPI 特性，新增 Plan 模式

1. **新增 `/hapi plan` 指令**：切换 Plan 模式（toggle）（对于codex，需 HAPI版本 >= 0.16.3）
   - Claude session：切换 `permissionMode` 在 `plan` ↔ `default` 之间
   - Codex session：切换 `collaborationMode` 在 `plan` ↔ `default` 之间
   - 若处于Plan Mode中，消息推送通知中将会新增 `📋Plan Mode` 标记

2. **新增 `/hapi effort` 指令**：查看/切换推理强度（需 HAPI版本 >= 0.16.4）
   - Claude：`auto`、`medium`、`high`、`max`
   - Codex：`none`、`minimal`、`low`、`medium`、`high`、`xhigh`

3. **`/hapi model` 指令改动**：
   - 新增 Gemini 模型列表并支持远程gemini cil切换：`gemini-2.5-pro`、`gemini-2.5-flash`、`gemini-2.5-flash-lite`、`gemini-3-flash-preview`、`gemini-3.1-pro-preview`
   - Claude 模型列表补充 `sonnet[1m]`、`opus[1m]`

## v2.0.6 - 新增 `/hapi resume` 指令，用于恢复已经存档的session

## v2.0.5 — Codex 思考深度支持

1. 新增 Codex 会话创建时的思考深度选项（需 HAPI 服务端 >= 0.16.2）

## v2.0.0 大更新 — 支持自然语言操作远程会话

**此版本提供了 Astrbot 原生 Function Calling 能力的集成，现在你可以用自然语言管理远程 vibe 会话了**

利用v1.6.0大版本的会话管理机制，相关 Function Calling 工具将动态选择注册。

如果你在当前群组/私聊窗口没有对 hapi 相关远程服务进行管理，管理相关的工具将不会注册，避免污染上下文

如果与 astrbot 对话的不是管理员，hapi 相关工具完全不会为其注册

1. **新增 LLM 工具支持**：为 Astrbot 提供 10 个工具，实现 AI 代理远程管理 HAPI coding sessions
   - 查询类工具（4个）：获取 session 列表、状态、配置、可用命令
   - 操作类工具（6个）：发送消息、切换 session、创建 session、停止消息、修改配置、执行任意 HAPI 命令
   - 为了管理会话，建议至少激活查询可用命令、执行任意 HAPI 命令两个工具 ( 即 hapi_coding_list_commands 和 hapi_coding_execute_command )，执行 HAPI 命令的工具可以为你主动执行任一 hapi 命令，其它工具的存在仅是为了方便管理和快速调用。
   - 所有操作类工具均复用了审批命令和审批逻辑，需管理员审批，依然支持 `/hapi a` 快捷批准、`/hapi deny` 拒绝，依然支持戳一戳快速批准（QQ NapCat），防止模型呆傻误操作之类的给人添乱

2. **审批机制优化**
   - 序号管理系统：每个待审批请求分配唯一序号，删除后自动回收复用
   - 优化审批通知格式：显示"当前共 x 个待审批，此请求审批序号：x"

## v1.6.0 — 多会话通知管理机制改进

1. 修复 Codex SSE 完成态判定，修复部分情况会出现的codex延迟通知问题

2. 支持多窗口（多会话）推送机制，现在可以借助群聊、私聊、不同管理员账户的对话窗口区分通知消息

### 多会话更新管理机制改进介绍

这是一次兼容性更新，如果你没有这类需求，可以忽略此功能更新，照常使用插件。相关的配置，插件将会自动迁移和兼容

**在不同 AstrBot 会话中（比如 QQ 的私聊、群聊）， session 会话的管理将会互相独立**

根据 AstrBot 的对话窗口 id 进行区分，每个对话窗口只会看到和管理属于自己的 session。

在某个对话窗口使用 `sw` / `create` 命令后，将会自动把对应 session 的通知路由到当前会话。

点击跳转github查看详细图文说明：
https://github.com/LiJinHao999/astrbot_plugin_hapi_connector/blob/master/docs/session-isolation.md



## v1.5.1 — 命令体验优化 & bug修复 & 文件上传支持

1. 新增 `/hapi clean [路径前缀]` 命令，批量清理已归档 sessions
2. SSE 连接支持最大重试次数限制，避免无限重连，并增加了相关配置项
3. 优化所有命令输出格式与提示文本，消除歧义，提升可读性
4. 修复了手机端在开启输入状态感知情况下，napcat发送的心跳消息等空消息导致交互式命令异常退出的问题
5. 支持了 hapi upload 命令，现在可以上传文件了。使用快捷发送时也可以直接在消息中附上图片。

## v1.5.0 — 文件列表 & 文件下载

1. 新增 `/hapi files [关键词]` 命令，搜索远端 session 工作目录下的文件
2. 新增 `/hapi download <路径>` 命令（别名 `dl`），下载远端文件并发送到聊天，支持图片预览
3. 大文件（>10MB）下载前自动弹出确认提示

## v1.4.3

1. 新增 Cloudflare Zero Trust Access 认证配置支持，以便连接公网HAPI服务
2. 新增 CF Access 配置指南文档（含截图）

## v1.4.2

1. 增强了 SSE 连接错误处理的提示逻辑
2. 优化了 Session 列表格式

## v1.4.0 — 交互视觉优化

1. 优化消息输出格式，提升交互可读性：
   - 工具调用提醒统一改为 `🛠️ 工具名: 参数` 格式，替代原 `[Function Calling - 调用 XXX]`，提升直观性
   - `TodoWrite` 工具调用渲染为任务清单，支持 ✅ / 🔄 / ⬜ 状态符号

## v1.3.1

1. 新增上下文压缩支持：检测到 `Prompt is too long` 时复用权限审批流，忙时自动发送 `/compact`，非忙时推送审批提示；压缩完成后自动发送「继续」恢复会话
2. 修复了session当前上下文过长时导致SSE请求流崩溃的问题

## v1.3.0 — 自动化托管支持

1. 新增忙时托管审批功能：
   - 新增 `auto_approve_enabled` 配置项（默认关闭），开启后在指定时间范围内自动批准所有非 question 权限请求
   - 新增 `auto_approve_start` / `auto_approve_end` 配置项（默认 `23:00` ~ `07:00`），支持跨午夜时间段
   - 自动批准触发时，即使 `silence` 模式也会推送 `[忙时托管审批] 已自动批准` 通知
2. 新增 `/hapi remote` 命令，切换当前 session 到 remote 远程托管模式
3. 修复 `/hapi msg` 命令输出内容过多后下次调用失效的问题（超长消息自动按行边界分片发送）
4. 修复 `/hapi msg` 命令无法解析部分消息格式的问题
5. 修复 `silence` 模式下的 TOCTOU 竞态问题（推送前二次检查 `output_level`）

## v1.2.3

1. 新增待审批请求超时提醒功能：
   - 新增 `remind_pending` 配置项（默认关闭），开启后若 pending 请求在指定时间内未被处理，发送一次提醒
   - 新增 `remind_interval` 配置项（默认 180 秒），倒计时内处理完则不提醒
2. `poke_approve` 默认改为开启

## v1.2.1

1. 新增 `AskUserQuestion` 类型权限请求的识别与处理：
   - SSE 推送时自动识别 question 类型，展示问题标题、题目和选项
   - 新增 `/hapi answer [序号]` 命令，交互式逐题回答（支持多问题步进、自定义输入）
   - 新增 `/hapi allow [序号]` 命令，仅批准普通权限请求（跳过 question）
   - `/hapi a` 调整为：先批准所有普通权限请求，再交互式处理所有 question
   - 戳一戳审批与 `/hapi a` 行为一致：批准普通权限请求后交互式处理 question

## v1.2.0 — 基础功能完善

1. 清理了无用 JSON，优化了交互内容输出，debug 输出模式重构为 detail，统一使用语义标签格式推送：
   - `[Message]: AI 回复文本`
   - `[Function Calling - 调用 Bash]: ls -la`
   - `[System]: Context was reset`
   - `[User Input]: 用户消息`
2. 重构了 msg 命令，现在不按条数计算消息，而是按交互轮数（`/hapi msg [轮数]`）
3. 新增了 abort（别名 stop）命令，用于打断会话（`/hapi abort [序号|ID前缀]`）
