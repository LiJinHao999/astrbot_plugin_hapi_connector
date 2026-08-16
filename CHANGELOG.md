# 更新日志

## v3.3.0（补充 7）— 操作记录卡白话页脚，且不带窗外历史

1. **页脚白话**：`mode=按托管时段 push=托管结束时` 改为「按托管时段统计汇总，托管结束时推送」。
2. **最近消息一块**：不再按行拆成多张卡片；系统噪声（如 Context was reset）不展示。
3. **只认窗内活动**：占桶看 thinking，不把单纯 active 当干活；最近消息按 `createdAt` 限制在本统计窗内；窗内既无审批也无运行也无消息则不出空卡。

## v3.3.0（补充 6）— 窗内有会话活动就出操作记录

1. **不再要求必须有审批/压缩**：托管窗内 session 一旦思考/运行即占桶；窗结束 / 定点 / `/hapi summary` 对「只聊了天、没弹批」的 session 也会推一张（最近消息 + 运行时长 + git）。
2. **结算条件**：`flush_all` / 归档 / 命令筛选看「当前桶活动」，不是只看 `pending` 审批事件。

## v3.3.0（补充 5）— 托管结束主动推送不再被漏掉

1. **窗结束 / 定点结算不再被「同日同指纹」跳过**：窗内手动 `/hapi summary` 只算预览，07:00 托管结束仍会再推一张并归档；`need_flush` 仅约束 stop / 关开关 / 热更新补发，避免重载连推。
2. **重启补发错过的窗结束**：`was_in_window` / `window_enter_day` 写入 KV；启动时若上次停在窗内、现在已出窗（或窗外仍有未结算 pending），按错过边沿补发。
3. **跨午夜 enter_day**：07:00 前进入的窗记为「昨天 23:00 起」，避免桶描述日期错位。

## v3.3.0（补充 4）— 操作记录卡补全 session 身份与最近消息

1. **单 session 操作记录必显身份**：标题不再用 cwd 路径顶替会话名（无标题时显示「(无标题)」）；副标题 / 文本头显式带 `flavor · sid 前 8 位`；卡片内新增「路径」「会话」kv 行，避免多 session 汇总时只剩目录路径看不出是谁。
2. **附带最近消息预览**：flush / 手动 `/hapi summary` 时拉取该 session 最近一条可展示 agent 回复（无则退用户输入），文本与结构卡均增加「最近消息」区块（不入指纹、不落 KV，失败静默省略）。

## v3.3.0（补充 3）— 操作记录统计翻新

1. **改名**：「Agent 操作记录汇总」统一改称「**Agent 操作记录统计**」（`auto_approve_silent` 键名不变），覆盖 WebUI 设置 / 概览页 / 配置 schema / README / 使用指南。
2. **推送时机新增「不主动推送」**：`auto_approve_summary_push` 增加 `manual` 档——不自动推，窗结束仅归档快照，需要时 `/hapi summary` 手动重发（WebUI 与 schema 均可选）。
3. **未开启统计提示**：`/hapi summary` 在 `auto_approve_silent` 关闭时提示「操作记录统计未开启」并指引开启，不再静默回「无记录」；`status` 仍可查配置。

## v3.3.0（补充 2）— 忙时托管免打扰归位审批页

1. **文案与分组**：`busy_agent_push_level` 对外改称「**忙时托管免打扰**」，从「推送」迁到「审批」→ 忙时托管审批时段下方；操作记录汇总同组。三档文案写清：只压 AI 对话、权限由托管自动批不弹批、仅 AI 提问必须作答时仍提醒。
2. **联动显示**：托管关时隐藏免打扰与操作记录汇总相关子项（概览页对应控件 disabled）。

## v3.3.0（补充）— 忙时 Agent 消息等级 + 操作记录可重发

> 过程文档：`dev-docs/busy-hours-agent-push.md`

1. **忙时托管免打扰**（`busy_agent_push_level`，原称「忙时消息」）：托管开启且在忙时段内，Agent 对话推送可为 `none`（不推）/ `summary`（仅完成摘要）/ `inherit`（跟随 `output_level`）。与操作记录汇总正交；AI 提问仍推。
2. **`/hapi summary` 可重发**：优先上一统计窗快照，否则当前桶；推送成功不再作为销毁数据的条件。窗结束归档 `last_closed_snapshot`。
3. **推送失败可感知**：`NotificationManager` / `present_push` 返回是否发出；无路由或全失败时汇总不记「已推」。
4. **运行时长**：thinking 边沿累加，操作记录中一行展示。
5. **统计窗重构**：汇总方式只保留 `window` 按托管时段 / `rolling_24h` 最近24小时（移除「按天」「手动触发」——统计窗只代表分桶，与推送无关）；rolling 模式事件超 24h 自动过期、命令直接发当前滚动窗数据。
6. **推送时机修复**：`auto_approve_summary_push` 此前未生效（窗结束边沿与定点都会推）；现在 `on_window_end` 只在窗结束推、`at_fixed_time` 只在每天定点推。
7. **设置项联动**：关闭「Agent 操作记录汇总」开关时，汇总方式 / 推送时机 / 固定推送时间 / 失败明细 / 行数上限 5 项隐藏。

## v3.3.0 — 忙时托管「Agent 操作记录汇总」

> 对应 Issue [#34](https://github.com/LiJinHao999/astrbot_plugin_hapi_connector/issues/34)，完整设计见 `dev-docs/auto-approve-silent-summary.md`。

1. **新增 Agent 操作记录汇总**（`auto_approve_silent`，键名保留兼容，默认关闭，行为与旧版一致）  
   开启后，忙时托管时段内 agent 的**全部操作**（自动批准、手动批准的请求、拒绝、自动压缩，含工具与参数摘要）**不再逐条推送**，改为按策略收集汇总推送，并附带 git 变更快照。夜间托管（如 23:00–07:00）不再刷屏，微信等平台也不会被连续主动消息限流。

2. **汇总方式与推送时机可配置**（WebUI「设置 → 审批」托管时段下方；v3.3.0 补充 2 从推送页迁入）  
   - `auto_approve_summary_mode`：`按托管时段`（默认，窗结束结算）/ `最近24小时`（滚动窗口，事件超 24h 过期）
   - `auto_approve_summary_push`：`托管结束时`（默认）/ `每天固定时间`（`auto_approve_summary_time`，默认 08:00）
   - 高级：`auto_approve_summary_include_failures`（失败明细，默认开）、`auto_approve_summary_max_detail_lines`（明细行数上限，默认 30）

3. **严格隔离与防漏发**  
   - 汇总按 session 分开，**每个有变更的 session 各推一张**（文本或结构卡），带 `session_id` 走既有窗口路由（session 绑定 → flavor 默认 → 用户默认窗口），不做全局广播
   - `last_pushed_at + 内容指纹（sha256）` 去重：同日同内容不重复推；事件增删、失败转成功都会改变指纹触发再推
   - 补发路径：插件 terminate / SSE stop / 关操作记录或关托管 / WebUI 热改 mode·push·time / 进程重启后 KV 恢复 pending
   - pending 与 `last_*` 持久化到 AstrBot KV（键 `auto_approve_summary_v1`），重启不丢

4. **命令触发** `/hapi summary`  
   - `summary`：推送当前窗口可见且有变更的 session 汇总
   - `summary all`：全部有变更的 session（各回各窗口）
   - `summary <序号|ID>`：指定 session；`summary status`：查看队列与上次推送时间
   - 手动触发与自动 flush 共用同一套「推送成功 → 清 pending → 更新 last_*」；无变更时提示「无新的操作记录」，不会空卡刷屏
   - 帮助归类「审批」；`/hapi help 审批` 可查

5. **汇总卡渲染**  
   新增 `render_kinds` 选项 `auto_approve_summary`（默认勾选）；`render_mode=card` 时汇总出结构卡（统计 + 失败置顶 + 成功明细折叠），`text` 或未勾选时纯文本，未安装 Pillow 自动回退文本。

6. **WebUI 热更新**  
   改操作记录开关 / mode / push / time 无需重连 SSE，保存后立即生效；关操作记录或关托管时先把已收集的汇总补发（防漏发）。设置项位于「审批」分组（托管相关，v3.3.0 补充 2）。

7. **git 状态 / 变更统计 / 文件 diff 查看（只读）**（dev-docs §10 关联能力落地）  
   - `/hapi git`：当前 session 工作区 git 状态（porcelain 解析为可读列表，含 修改/新增/删除/重命名/冲突/未跟踪）
   - `/hapi diffstat [staged|unstaged]`：变更统计（`+新增 -删除` 对齐，可只看暂存或未暂存）
   - `/hapi diff <路径> [staged|unstaged]`：单文件完整 diff（统一 diff 格式）
   - 走 HAPI `GET /api/sessions/:id/git-status` / `git-diff-numstat` / `git-diff-file`；**只读**，不做提交/暂存/回滚
   - `render_mode=card` 时状态/统计出结构卡（新增出图类型 `git_status`，默认勾选），diff 走对话卡代码块；纯文本模式原样发送
   - 帮助归类「文件」；需较新 HAPI 版本（含 git 路由）

8. **操作记录附带 git 变更快照**  
   每次汇总推送时，对该 session 实时拉取 git 状态与变更统计（30s 缓存防高频重复查询），随汇总展示「N 个文件变更（+a -d）」与文件明细；非 git 仓库 / HAPI 无 git 路由时自动省略该区块，不影响汇总主体。想随时看更细的 diff 用 `/hapi diff <路径>`。

## v3.2.6

1. **修复 SSE 权限请求收不到的问题（兼容 hapi v0.27.0+ 事件结构）**  
   hapi v0.27.0 起，SSE `session-updated` 事件的 `agentState` 改为 `{version, value}` 嵌套结构，权限请求位于 `agentState.value.requests`，旧代码只读顶层导致收不到授权申请通知。现已兼容嵌套（优先）与扁平两种结构。

## v3.2.5

1. **修复纯文本模式下 agent 图片退化成 hapi-genimg 文本的问题**  
   纯文本模式（`render_mode=text`）下，agent 推送的图片回退文案仍保留原始 `hapi-genimg://` 标记，导致 QQ 等平台只显示图片链接文本而不发图。现已对回退文案同样解析图片标记，图片会以真实图片随正文一起发送。

## v3.2.4

1. **新增 `/hapi sync`：将 Codex Session 同步到 HAPI**  
   支持按序号或 ID 前缀指定会话，未传参时同步当前选中会话。同步成功后自动刷新 Session 缓存；结果展示导入会话数与新增消息数（支持二次同步增量统计）。失败时聊天侧仅显示一行压缩原因，不暴露 HAPI 原始错误与本地路径。WebUI 会话页同步入口、API 与 `/hapi help` 已一并打通。  
   推荐 HAPI v0.23.4 或更高版本；Codex 建议使用最新版本。

2. **优化 `/hapi msg` 中 wait 等工具调用的显示格式**  
   非 command 类工具（纯 JSON 参数）由 `🛠️ wait: {...}` 裸 JSON 单行，改为与 command 类一致的代码块包裹；长参数自动截断并转义反引号，避免破坏 Markdown 渲染。

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
