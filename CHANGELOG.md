# 更新日志

## v1.3.0

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

## v1.2.0

1. 清理了无用 JSON，优化了交互内容输出，debug 输出模式重构为 detail，统一使用语义标签格式推送：
   - `[Message]: AI 回复文本`
   - `[Function Calling - 调用 Bash]: ls -la`
   - `[System]: Context was reset`
   - `[User Input]: 用户消息`
2. 重构了 msg 命令，现在不按条数计算消息，而是按交互轮数（`/hapi msg [轮数]`）
3. 新增了 abort（别名 stop）命令，用于打断会话（`/hapi abort [序号|ID前缀]`）
