"""HAPI 常量定义"""

# 各 flavor 对应的权限模式
PERMISSION_MODES = {
    "claude": ["default", "acceptEdits", "bypassPermissions", "plan"],
    "codex": ["default", "read-only", "safe-yolo", "yolo"],
    "gemini": ["default", "read-only", "safe-yolo", "yolo"],
    "opencode": ["default", "yolo"],
}

# Claude 可用的模型模式
MODEL_MODES = ["default", "sonnet", "opus"]

# Codex 可用的思考深度；None 表示继承 Codex 默认设置
CODEX_REASONING_EFFORT_OPTIONS = [
    (None, "继承 Codex 默认设置（推荐）"),
    ("none", "none"),
    ("minimal", "minimal"),
    ("low", "low"),
    ("medium", "medium"),
    ("high", "high"),
    ("xhigh", "xhigh"),
]
CODEX_REASONING_EFFORT_VALUES = [value for value, _ in CODEX_REASONING_EFFORT_OPTIONS if value]

# 支持的 Agent 类型
AGENTS = ["claude", "codex", "gemini", "opencode"]

# Session 类型
SESSION_TYPES = ["simple", "worktree"]
