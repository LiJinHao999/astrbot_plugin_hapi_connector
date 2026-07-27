"""HAPI agent flavor 能力表。

对齐上游 HAPI:
- shared/src/modes.ts  (AGENT_FLAVORS / 各 flavor 权限模式 / CREATABLE)
- shared/src/flavors.ts (model-change / effort 能力)

设计原则:
1. Session 通用路径（list/sw/to/消息/审批/resume/SSE）不依赖白名单。
2. 差异能力走 profile 查询，避免散落 if flavor == "...".
3. 未知 flavor 降级：允许尝试创建/绑定，本地不做严格枚举校验，交给 HAPI 拒错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# 上游全量 flavor（含仅兼容旧 session 的 gemini）
KNOWN_FLAVORS: tuple[str, ...] = (
    "claude",
    "codex",
    "cursor",
    "gemini",
    "grok",
    "kimi",
    "opencode",
    "pi",
)

# Claude 模型预设（对齐 shared/src/models.ts；也可直接传任意模型字符串）
CLAUDE_MODEL_MODES = [
    "default",
    "sonnet",
    "sonnet[1m]",
    "opus",
    "opus[1m]",
    "fable",
    "fable[1m]",
]

# Gemini 模型预设（旧 session 兼容；上游已不可新建）
GEMINI_MODEL_MODES = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
]

# Claude effort；对齐 shared/src/effort.ts（None 表示 auto / 省略 --effort）
CLAUDE_EFFORT_OPTIONS: list[tuple[str | None, str]] = [
    (None, "auto（默认）"),
    ("low", "low"),
    ("medium", "medium"),
    ("high", "high"),
    ("xhigh", "xhigh"),
    ("max", "max"),
]
CLAUDE_EFFORT_VALUES = [v for v, _ in CLAUDE_EFFORT_OPTIONS if v]

# Codex / OpenCode modelReasoningEffort；None 表示继承默认
# 上游支持动态 options，本地列表作兜底；列表外值仍可透传
CODEX_REASONING_EFFORT_OPTIONS: list[tuple[str | None, str]] = [
    (None, "继承默认设置（推荐）"),
    ("none", "none"),
    ("minimal", "minimal"),
    ("low", "low"),
    ("medium", "medium"),
    ("high", "high"),
    ("xhigh", "xhigh"),
    ("max", "max"),
]
CODEX_REASONING_EFFORT_VALUES = [v for v, _ in CODEX_REASONING_EFFORT_OPTIONS if v]

# Pi thinking levels；对齐 shared/src/piThinkingLevel.ts
PI_EFFORT_OPTIONS: list[tuple[str | None, str]] = [
    (None, "auto（默认）"),
    ("off", "off"),
    ("minimal", "minimal"),
    ("low", "low"),
    ("medium", "medium"),
    ("high", "high"),
    ("xhigh", "xhigh"),
    ("max", "max"),
]
PI_EFFORT_VALUES = [v for v, _ in PI_EFFORT_OPTIONS if v]

# Grok 等通用 effort（上游可有动态 options，这里给常用固定项）
GENERIC_EFFORT_OPTIONS: list[tuple[str | None, str]] = [
    (None, "auto（默认）"),
    ("low", "low"),
    ("medium", "medium"),
    ("high", "high"),
]
GENERIC_EFFORT_VALUES = [v for v, _ in GENERIC_EFFORT_OPTIONS if v]

# Codex Fast mode（service tier）
SERVICE_TIER_OPTIONS: list[tuple[str, str]] = [
    ("standard", "standard（关闭 Fast）"),
    ("fast", "fast（开启 Fast）"),
]
SERVICE_TIER_VALUES = [v for v, _ in SERVICE_TIER_OPTIONS]


@dataclass(frozen=True)
class FlavorProfile:
    """单个 agent flavor 的能力描述。"""

    key: str
    label: str
    creatable: bool = True
    # None 表示本地不枚举校验，透传给 HAPI
    permission_modes: tuple[str, ...] | None = ("default",)
    supports_model: bool = False
    # 本地可选模型列表；空元组表示支持切换但无静态列表（允许自由输入）
    model_modes: tuple[str, ...] = ()
    # effort 走 /effort；reasoning_effort 走 /model-reasoning-effort（Codex/OpenCode）
    supports_effort: bool = False
    supports_reasoning_effort: bool = False
    # Codex Fast mode：POST /service-tier {serviceTier: fast|standard}
    supports_service_tier: bool = False
    # plan 实现方式
    plan_via_permission: bool = False
    plan_via_collaboration: bool = False
    notes: str = ""
    # 额外标签，便于未来扩展（如 codex 系）
    tags: frozenset[str] = field(default_factory=frozenset)

    @property
    def supports_plan(self) -> bool:
        return self.plan_via_permission or self.plan_via_collaboration


# 与 HAPI shared/src/modes.ts + flavors.ts 对齐
_FLAVOR_PROFILES: dict[str, FlavorProfile] = {
    "claude": FlavorProfile(
        key="claude",
        label="Claude",
        creatable=True,
        permission_modes=("default", "acceptEdits", "auto", "bypassPermissions", "plan"),
        supports_model=True,
        model_modes=tuple(CLAUDE_MODEL_MODES),
        supports_effort=True,
        plan_via_permission=True,
    ),
    "codex": FlavorProfile(
        key="codex",
        label="Codex",
        creatable=True,
        permission_modes=("default", "read-only", "safe-yolo", "yolo"),
        supports_model=True,
        model_modes=(),  # 动态模型，本地不静态枚举
        supports_reasoning_effort=True,
        supports_service_tier=True,
        plan_via_collaboration=True,
        tags=frozenset({"codex_family"}),
    ),
    "cursor": FlavorProfile(
        key="cursor",
        label="Cursor",
        creatable=True,
        permission_modes=("default", "plan", "ask", "debug", "autoReview", "yolo"),
        supports_model=True,
        model_modes=(),
        plan_via_permission=True,
    ),
    "gemini": FlavorProfile(
        key="gemini",
        label="Gemini",
        # 上游 2026-06 起 Gemini CLI 已 sunset，不可新建，仅兼容旧 session
        creatable=False,
        permission_modes=("default", "read-only", "safe-yolo", "yolo"),
        supports_model=True,
        model_modes=tuple(GEMINI_MODEL_MODES),
        tags=frozenset({"codex_family", "legacy"}),
        notes="Gemini CLI 已停止创建；仅可管理已有 session",
    ),
    "grok": FlavorProfile(
        key="grok",
        label="Grok Build",
        creatable=True,
        permission_modes=("default", "auto", "plan", "bypassPermissions"),
        supports_model=True,
        model_modes=(),
        supports_effort=True,
        plan_via_permission=True,
        tags=frozenset({"codex_family"}),
    ),
    "kimi": FlavorProfile(
        key="kimi",
        label="Kimi",
        creatable=True,
        permission_modes=("default", "read-only", "safe-yolo", "yolo"),
        supports_model=True,
        model_modes=(),
        tags=frozenset({"codex_family"}),
    ),
    "opencode": FlavorProfile(
        key="opencode",
        label="OpenCode",
        creatable=True,
        # 上游 OPENCODE_PERMISSION_MODES 含 plan
        permission_modes=("default", "plan", "yolo"),
        supports_model=True,
        model_modes=(),
        # Hub: model-reasoning-effort 适用于 Codex 与 OpenCode
        supports_reasoning_effort=True,
        plan_via_permission=True,
        tags=frozenset({"codex_family"}),
    ),
    "pi": FlavorProfile(
        key="pi",
        label="Pi",
        creatable=True,
        # 上游：Pi RPC 无运行时权限切换
        permission_modes=(),
        supports_model=True,
        model_modes=(),
        supports_effort=True,
        notes="Pi 不支持运行时权限模式切换；effort 对齐 Pi thinking levels",
    ),
}


# 内置斜杠命令兜底表（对齐 shared/src/slashCommands.ts BUILTIN_SLASH_COMMANDS）
# 仅存名字（不带 /）；实时列表优先走 GET /api/sessions/:id/slash-commands
BUILTIN_SLASH_COMMANDS: dict[str, tuple[str, ...]] = {
    "claude": ("clear", "compact", "context", "cost", "doctor", "plan", "stats", "status"),
    "codex": ("agent", "clear", "compact", "goal", "help", "plan", "default", "execute",
              "status", "model", "reasoning", "effort", "permissions", "permission"),
    "gemini": ("about", "clear", "compress", "stats"),
    "grok": ("compact", "context", "session-info", "goal", "always-approve", "auto"),
    "opencode": ("help", "status", "plan", "default", "init"),
    "cursor": ("compress",),
}


def builtin_slash_commands_for(flavor: str | None) -> tuple[str, ...]:
    """内置斜杠命令名（无 /）；未知 flavor 对齐上游 getBuiltinSlashCommands 回退 claude。"""
    key = normalize_flavor(flavor)
    return BUILTIN_SLASH_COMMANDS.get(key, BUILTIN_SLASH_COMMANDS["claude"])


def normalize_flavor(flavor: str | None) -> str:
    """标准化 flavor 字符串。"""
    return (flavor or "").strip().lower()


def is_known_flavor(flavor: str | None) -> bool:
    return normalize_flavor(flavor) in _FLAVOR_PROFILES


def profile_for(flavor: str | None) -> FlavorProfile:
    """返回 flavor 配置；未知类型使用自适应降级 profile。"""
    key = normalize_flavor(flavor)
    if key in _FLAVOR_PROFILES:
        return _FLAVOR_PROFILES[key]
    # 未知 flavor：允许尝试创建/绑定，能力保守，本地不拦权限枚举
    display = key or "unknown"
    return FlavorProfile(
        key=display,
        label=display,
        creatable=True,
        permission_modes=None,
        supports_model=False,
        supports_effort=False,
        supports_reasoning_effort=False,
        plan_via_permission=False,
        plan_via_collaboration=False,
        notes="未知 agent 类型：通用操作可用，差异能力交给 HAPI 校验",
        tags=frozenset({"unknown"}),
    )


def known_profiles() -> list[FlavorProfile]:
    return [_FLAVOR_PROFILES[k] for k in KNOWN_FLAVORS if k in _FLAVOR_PROFILES]


def creatable_agents() -> list[str]:
    """可新建 session 的 flavor 列表（向导 / FC 推荐列表）。"""
    return [p.key for p in known_profiles() if p.creatable]


def all_known_agents() -> list[str]:
    return list(KNOWN_FLAVORS)


def flavor_label(flavor: str | None) -> str:
    return profile_for(flavor).label


def permission_modes_for(flavor: str | None) -> list[str]:
    """返回本地可用的权限模式列表。

    - 已知且 modes 为空（如 pi）: []
    - 未知: ["default"] 作为展示回退（实际切换可透传任意值）
    """
    modes = profile_for(flavor).permission_modes
    if modes is None:
        return ["default"]
    return list(modes)


def allows_any_permission_mode(flavor: str | None) -> bool:
    """是否允许本地跳过枚举校验（透传给 HAPI）。"""
    return profile_for(flavor).permission_modes is None


def is_creatable(flavor: str | None) -> bool:
    p = profile_for(flavor)
    # 未知类型允许尝试；已知则看 creatable
    if "unknown" in p.tags:
        return True
    return p.creatable


def is_permission_mode_allowed(flavor: str | None, mode: str) -> bool:
    p = profile_for(flavor)
    if p.permission_modes is None:
        return bool(mode)
    if not p.permission_modes:
        return False
    return mode in p.permission_modes


def model_modes_for(flavor: str | None) -> list[str]:
    return list(profile_for(flavor).model_modes)


def supports_model_change(flavor: str | None) -> bool:
    return profile_for(flavor).supports_model


def supports_effort(flavor: str | None) -> bool:
    return profile_for(flavor).supports_effort


def supports_reasoning_effort(flavor: str | None) -> bool:
    return profile_for(flavor).supports_reasoning_effort


def supports_any_effort(flavor: str | None) -> bool:
    p = profile_for(flavor)
    return p.supports_effort or p.supports_reasoning_effort


def supports_plan(flavor: str | None) -> bool:
    return profile_for(flavor).supports_plan


def supports_service_tier(flavor: str | None) -> bool:
    return profile_for(flavor).supports_service_tier


def effort_options_for(flavor: str | None) -> list[tuple[str | None, str]]:
    p = profile_for(flavor)
    if p.supports_reasoning_effort:
        return list(CODEX_REASONING_EFFORT_OPTIONS)
    if p.key == "claude":
        return list(CLAUDE_EFFORT_OPTIONS)
    if p.key == "pi":
        return list(PI_EFFORT_OPTIONS)
    if p.supports_effort:
        return list(GENERIC_EFFORT_OPTIONS)
    return []


def effort_values_for(flavor: str | None) -> list[str]:
    return [v for v, _ in effort_options_for(flavor) if v]


def effort_allows_freeform(flavor: str | None) -> bool:
    """reasoning effort 上游可能动态扩展，列表外值允许透传。"""
    return supports_reasoning_effort(flavor)


def effort_none_aliases(flavor: str | None) -> tuple[str, ...]:
    p = profile_for(flavor)
    if p.supports_reasoning_effort:
        return ("inherit", "继承", "default", "auto")
    return ("auto", "default")


def effort_none_label(flavor: str | None) -> str:
    p = profile_for(flavor)
    if p.supports_reasoning_effort:
        return "继承默认"
    return "auto"


def service_tier_options() -> list[tuple[str, str]]:
    return list(SERVICE_TIER_OPTIONS)


def normalize_service_tier(value: str | None) -> str | None:
    """将 on/off/fast/standard 等别名规范为 fast|standard。"""
    if value is None:
        return None
    raw = value.strip().lower()
    if not raw:
        return None
    if raw in ("fast", "on", "true", "1", "yes", "开启", "开"):
        return "fast"
    if raw in ("standard", "off", "false", "0", "no", "关闭", "关", "normal", "default"):
        return "standard"
    return None


def format_creatable_agents_help() -> str:
    parts = []
    for p in known_profiles():
        if not p.creatable:
            continue
        parts.append(p.key)
    return "/".join(parts)


def format_bind_flavor_examples(limit: int = 4) -> str:
    """帮助文案中的 flavor 示例。"""
    samples = [p.key for p in known_profiles() if p.creatable][:limit]
    return "|".join(samples)


def reserved_bind_actions() -> frozenset[str]:
    """bind 子命令中的保留字（非 flavor）。"""
    return frozenset({"status", "reset", "help", "list", "all", "primary", "default"})


def is_bindable_flavor(token: str | None) -> bool:
    """任意非空、非保留字的 token 都可作为 flavor 绑定键。"""
    key = normalize_flavor(token)
    if not key:
        return False
    if key in reserved_bind_actions():
        return False
    # 拒绝明显非法字符，允许字母数字与 - _
    return all(c.isalnum() or c in "-_" for c in key)


def merge_seen_flavors(base: Iterable[str], seen: Iterable[str]) -> list[str]:
    """合并已知列表与运行时观察到的 flavor，去重保序。"""
    out: list[str] = []
    for item in list(base) + list(seen):
        key = normalize_flavor(item)
        if key and key not in out:
            out.append(key)
    return out


def export_profiles_meta() -> dict:
    """JSON-friendly flavor capability table for WebUI meta endpoint."""
    flavors = []
    permission_modes: dict[str, list[str]] = {}
    for p in known_profiles():
        flavors.append({
            "key": p.key,
            "label": p.label,
            "creatable": p.creatable,
            "permission_modes": list(p.permission_modes) if p.permission_modes is not None else None,
            "supports_model": p.supports_model,
            "model_modes": list(p.model_modes),
            "supports_effort": p.supports_effort,
            "supports_reasoning_effort": p.supports_reasoning_effort,
            "supports_service_tier": p.supports_service_tier,
            "supports_plan": p.supports_plan,
            "notes": p.notes,
        })
        if p.permission_modes is not None:
            permission_modes[p.key] = list(p.permission_modes)
    return {
        "known_flavors": list(KNOWN_FLAVORS),
        "creatable": creatable_agents(),
        "flavors": flavors,
        "permission_modes": permission_modes,
    }


# 兼容旧 constants 导入名
AGENTS = creatable_agents()  # 动态：不含 gemini
PERMISSION_MODES = {
    key: list(p.permission_modes or ())
    for key, p in _FLAVOR_PROFILES.items()
    if p.permission_modes is not None
}
MODEL_MODES = list(CLAUDE_MODEL_MODES)
