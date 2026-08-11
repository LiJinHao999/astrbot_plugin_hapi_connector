/**
 * WebUI 静态常量：权限模式、路由层、帮助目录等（设置字段表见 meta.config_schema）
 */

const PERM = {
  claude: ["default", "acceptEdits", "bypassPermissions", "plan"],
  codex: ["default", "read-only", "safe-yolo", "yolo"],
  gemini: ["default", "read-only", "safe-yolo", "yolo"],
  grok: ["default", "yolo"],
  kimi: ["default", "yolo"],
  opencode: ["default", "yolo"],
  pi: ["default", "yolo"],
  cursor: ["default", "yolo"],
};

const LAYER = {
  session_bind: { text: "会话绑定", cls: "tag-layer-session_bind" },
  flavor_default: { text: "AI 代理推送窗口", cls: "tag-layer-flavor_default" },
  primary: { text: "默认推送窗口", cls: "tag-layer-primary" },
  none: { text: "未投递", cls: "tag-layer-none" },
};

const UMO = {
  private: "aiocqhttp:FriendMessage:10001",
  groupA: "aiocqhttp:GroupMessage:20001",
  groupB: "aiocqhttp:GroupMessage:20002",
};

const PAGE_META = {
  overview: { title: "概览", desc: "一眼看清连接是否正常、有没有事情等你处理" },
  sessions: { title: "会话管理", desc: "查看和管理 AI 会话，决定通知发到哪个聊天窗口" },
  interact: { title: "交互优化", desc: "戳一戳、快捷指令、会话模板，以及消息推送的显示样式" },
  help: { title: "命令帮助", desc: "所有 /hapi 聊天指令的用法（和聊天里 /hapi help 一样）" },
  settings: { title: "设置", desc: "插件全部配置项，和 AstrBot 官方设置页改的是同一份" },
  docs: { title: "使用文档", desc: "安装部署、使用指南、多窗口说明，随时翻阅" },
};



/** 推送出图类型标签（设置页 kind_checks + 交互页共用） */
const RENDER_KIND_LABELS = {
  session_list: "Session 列表",
  pending: "待审批列表",
  status: "状态",
  permission: "权限请求",
  routes: "推送路由",
  message: "AI 对话",
  auto_approve_summary: "操作记录",
  git_status: "git 状态/统计",
};

const FLAVOR_ROUTE_KEYS = ["claude", "codex", "cursor", "gemini", "grok", "kimi", "opencode", "pi"];

const OUTPUT_LEVELS = [
  { value: "silence", title: "静默" },
  { value: "simple", title: "简洁" },
  { value: "summary", title: "摘要" },
  { value: "detail", title: "详细" },
];

/** 忙时托管免打扰（托管窗内覆盖 output_level，压 AI 对话） */
const BUSY_AGENT_PUSH_LEVELS = [
  { value: "none", title: "不推送" },
  { value: "summary", title: "仅摘要" },
  { value: "inherit", title: "跟随默认" },
];

/* 与 formatters.HELP_COMMANDS / HELP_TOPICS 对齐 */
const HELP_TOPICS = [
  { id: "session", name: "会话", desc: "Session 管理" },
  { id: "chat", name: "对话", desc: "对话与消息" },
  { id: "approve", name: "审批", desc: "审批与回答" },
  { id: "push", name: "通知", desc: "多会话通知管理" },
  { id: "files", name: "文件", desc: "文件操作" },
  { id: "config", name: "配置", desc: "模式与配置" },
];

const HELP_COMMANDS = [
  { topic: "session", usage: "/hapi list [all]", summary: "查看当前窗口会接收通知的 session", example: null, home: true },
  { topic: "session", usage: "/hapi list all", summary: "查看所有 session 和全局绑定状态", example: null, home: false },
  { topic: "session", usage: "/hapi sw <序号|ID前缀>", summary: "切换当前 session", example: "/hapi sw 2", home: true },
  { topic: "session", usage: "/hapi create [模板名] [目录]", summary: "创建新 session：无参进交互向导；带模板名一步创建（模板在 WebUI 管理）", example: "/hapi create 主项目", home: true },
  { topic: "session", usage: "/hapi s", summary: "查看当前 session 状态（未绑定时回退默认窗口）", example: null, home: false },
  { topic: "session", usage: "/hapi abort [序号|ID前缀]", summary: "中断 session（默认当前，别名: /hapi stop）", example: "/hapi abort 1", home: true },
  { topic: "session", usage: "/hapi archive", summary: "归档当前 session", example: null, home: false },
  { topic: "session", usage: "/hapi resume [序号|ID前缀]", summary: "恢复已停掉的会话", example: "/hapi resume 1", home: true },
  { topic: "session", usage: "/hapi reopen [序号|ID前缀]", summary: "恢复已停掉的会话（resume 备用接口）", example: "/hapi reopen 1", home: true },
  { topic: "session", usage: "/hapi rename", summary: "重命名当前 session", example: null, home: false },
  { topic: "session", usage: "/hapi delete", summary: "删除当前 session", example: null, home: false },
  { topic: "session", usage: "/hapi clean [路径前缀]", summary: "批量清理 inactive sessions", example: "/hapi clean C:/work/project", home: false },
  { topic: "chat", usage: "> 内容", summary: "快速发送到当前 session", example: "> 帮我排查这个报错", home: true },
  { topic: "chat", usage: ">N 内容", summary: "快速发送到第 N 个 session", example: ">2 继续上一个任务", home: true },
  { topic: "chat", usage: "/hapi to <序号> <内容>", summary: "发送到指定 session", example: "/hapi to 2 继续上一个任务", home: false },
  { topic: "chat", usage: "/hapi send <内容>", summary: "发送到当前会话（适合做关键词映射，如 cl → send /clear）", example: "/hapi send /clear", home: false },
  { topic: "chat", usage: "/hapi retry", summary: "重发本窗口上一条发出的消息（AI 无响应时使用）", example: null, home: false },
  { topic: "chat", usage: "/hapi msg [轮数]", summary: "查看最近几轮消息（未绑定时回退默认窗口）", example: "/hapi msg 2", home: true },
  { topic: "approve", usage: "/hapi pending", summary: "查看当前窗口可见的待处理请求", example: null, home: true },
  { topic: "approve", usage: "/hapi a", summary: "批准全部非 question 请求，并继续回答 question", example: null, home: true },
  { topic: "approve", usage: "/hapi allow [序号]", summary: "批准全部或单个非 question 请求", example: "/hapi allow 2", home: false },
  { topic: "approve", usage: "/hapi answer [序号]", summary: "回答 question 请求", example: "/hapi answer 1", home: true },
  { topic: "approve", usage: "/hapi deny [序号]", summary: "拒绝请求", example: "/hapi deny 3", home: true },
  { topic: "approve", usage: "/hapi summary [all|<序号|ID>|status]", summary: "推送忙时托管操作记录：无参=当前窗口有变更的 session；all=全部；指定序号/ID 推单个；status 查看汇总队列", example: "/hapi summary", home: false },
  { topic: "approve", usage: "戳一戳机器人", summary: "执行 WebUI 配置的快捷动作（默认批准待审；可改为 list/stop 等，仅 QQ NapCat）", example: null, home: false },
  { topic: "push", usage: "/hapi bind [<flavor>]", summary: "设置当前聊天为默认推送窗口；带 flavor（如 claude/codex）时只对对应 AI 代理生效", example: "/hapi bind claude", home: false },
  { topic: "push", usage: "/hapi bind status", summary: "查看默认推送窗口、flavor 推送窗口和 session 绑定状态", example: null, home: false },
  { topic: "push", usage: "/hapi routes", summary: "查看当前生效的会话推送路由", example: null, home: false },
  { topic: "push", usage: "/hapi alias [过滤词]", summary: "查看快捷关键词映射（匹配规则与当前条目；可按关键词/命令过滤）", example: "/hapi alias to", home: true },
  { topic: "push", usage: "/hapi bind reset", summary: "清空会话路由和窗口状态，保留默认推送窗口和 flavor 推送窗口", example: null, home: false },
  { topic: "files", usage: "/hapi files [路径]", summary: "浏览远端目录", example: "/hapi files src", home: false },
  { topic: "files", usage: "/hapi files -l [路径]", summary: "浏览目录并显示文件大小", example: "/hapi files -l .", home: false },
  { topic: "files", usage: "/hapi find <关键词>", summary: "搜索远端文件", example: "/hapi find config", home: false },
  { topic: "files", usage: "/hapi download <路径>", summary: "下载远端文件到聊天（别名: /hapi dl）", example: "/hapi dl logs/app.log", home: false },
  { topic: "files", usage: "/hapi upload [cancel]", summary: "上传文件到当前 session，支持快捷前缀附件", example: "/hapi upload", home: false },
  { topic: "files", usage: "/hapi git", summary: "查看当前 session 工作区的 git 状态（只读）", example: null, home: false },
  { topic: "files", usage: "/hapi diffstat [staged|unstaged]", summary: "查看变更统计（+新增 -删除；staged=仅暂存，unstaged=仅未暂存）", example: "/hapi diffstat staged", home: false },
  { topic: "files", usage: "/hapi diff <路径> [staged|unstaged]", summary: "查看单文件 diff（统一 diff 格式，只读）", example: "/hapi diff src/main.py", home: false },
  { topic: "config", usage: "/hapi perm [模式]", summary: "查看或切换权限模式", example: null, home: false },
  { topic: "config", usage: "/hapi plan", summary: "切换 Plan 模式（toggle）。Claude 切换 permissionMode，Codex 切换 collaborationMode", example: null, home: false },
  { topic: "config", usage: "/hapi model [模式]", summary: "查看或切换当前使用的模型（Claude / Gemini）", example: null, home: false },
  { topic: "config", usage: "/hapi effort [值]", summary: "查看或切换推理强度。Claude：auto/medium/high/max；Codex：none/minimal/low/medium/high/xhigh", example: "/hapi effort high", home: false },
  { topic: "config", usage: "/hapi focus [on|off]", summary: "开启/关闭 Focus 模式（本窗口普通消息直发当前会话，快捷指令：专注 / 退出专注）", example: "/hapi focus on", home: true },
  { topic: "config", usage: "/hapi output [级别]", summary: "查看或切换推送级别 silence/simple/summary/detail", example: "/hapi output summary", home: false },
  { topic: "config", usage: "/hapi remote", summary: "切换当前 session 到 remote 托管模式", example: null, home: false },
  { topic: "config", usage: "/hapi help [主题]", summary: "查看帮助，可选主题：会话/对话/审批/通知/文件/配置/全部", example: "/hapi help 文件", home: false },
];

export {
  PERM,
  LAYER,
  UMO,
  PAGE_META,
  RENDER_KIND_LABELS,
  FLAVOR_ROUTE_KEYS,
  OUTPUT_LEVELS,
  BUSY_AGENT_PUSH_LEVELS,
  HELP_TOPICS,
  HELP_COMMANDS,
};
