# 更新日志

## v1.2.1

1. 新增 `AskUserQuestion` 类型权限请求的识别与处理：
   - SSE 推送时自动识别 question 类型，展示问题标题、题目和选项
   - 新增 `/hapi answer [序号]` 命令，交互式逐题回答（支持多问题步进、自定义输入）
   - 新增 `/hapi allow [序号]` 命令，仅批准普通权限请求（跳过 question）
   - `/hapi a` 调整为：先批准所有普通权限请求，再交互式处理所有 question
   - 戳一戳审批仅批准普通权限请求，不处理 question

## v1.2.0

1. 清理了无用 JSON，优化了交互内容输出，debug 输出模式重构为 detail，统一使用语义标签格式推送：
   - `[Message]: AI 回复文本`
   - `[Function Calling - 调用 Bash]: ls -la`
   - `[System]: Context was reset`
   - `[User Input]: 用户消息`
2. 重构了 msg 命令，现在不按条数计算消息，而是按交互轮数（`/hapi msg [轮数]`）
3. 新增了 abort（别名 stop）命令，用于打断会话（`/hapi abort [序号|ID前缀]`）
