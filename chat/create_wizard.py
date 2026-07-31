"""创建 Session 向导状态机：步骤推进、输入校验、提示文本构建"""

from .flavor_profiles import (
    CODEX_REASONING_EFFORT_OPTIONS,
    creatable_agents,
    flavor_label,
    is_creatable,
    normalize_flavor,
    profile_for,
    supports_reasoning_effort,
)


class WizardResult:
    """向导单步处理结果"""
    __slots__ = ("prompt", "need_recent_paths", "confirmed", "cancelled")

    def __init__(self, prompt: str = "", *,
                 need_recent_paths: bool = False,
                 confirmed: bool = False,
                 cancelled: bool = False):
        self.prompt = prompt
        self.need_recent_paths = need_recent_paths
        self.confirmed = confirmed
        self.cancelled = cancelled


class CreateWizard:
    """创建 Session 向导，纯状态机，不依赖 AstrBot 事件系统"""

    def __init__(self, machines: list, labels: list):
        self.state = {
            "step": 1,
            "machines": machines,
            "labels": labels,
            "machine_id": None,
            "machine_label": None,
            "directory": None,
            "session_type": "simple",
            "worktree_name": "",
            "agent": None,
            "model_reasoning_effort": None,
            "yolo": False,
            "recent_paths": [],
        }
        # 单机器时自动跳过步骤 1
        if len(machines) == 1:
            self.state["machine_id"] = machines[0]["id"]
            self.state["machine_label"] = labels[0]
            self.state["step"] = 2

    def set_recent_paths(self, paths: list):
        self.state["recent_paths"] = paths

    def _needs_reasoning_step(self) -> bool:
        return supports_reasoning_effort(self.state.get("agent"))

    def _total_steps(self) -> int:
        return 6 if self._needs_reasoning_step() else 5

    def _yolo_step_number(self) -> int:
        return 6 if self._needs_reasoning_step() else 5

    def _agent_choices(self) -> list[str]:
        return creatable_agents()

    def initial_prompt(self) -> WizardResult:
        """返回向导第一条提示（步骤 1 或自动跳到步骤 2）"""
        s = self.state
        if s["step"] == 1:
            lines = ["步骤 1/5 — 选择机器:"]
            for i, label in enumerate(s["labels"], 1):
                lines.append(f"  [{i}] {label}")
            lines.append("\n回复序号选择")
            return WizardResult("\n".join(lines))
        # 单机器，跳到步骤 2，需要先拉 recent_paths
        return WizardResult(
            f"自动选择机器: {s['machine_label']}",
            need_recent_paths=True)

    @staticmethod
    def format_directory_prompt(
        recent_paths: list | None = None,
        *,
        prefix: str = "",
        header: str = "步骤 2/5 — 工作目录:",
    ) -> str:
        """工作目录选择文案（向导步骤 2 / 模板缺省目录 共用）。"""
        lines: list[str] = []
        if prefix:
            lines.extend([prefix, ""])
        if header:
            lines.append(header)
        paths = list(recent_paths or [])
        if paths:
            lines.append("最近使用的目录:")
            for i, p in enumerate(paths, 1):
                lines.append(f"  [{i}] {p}")
            lines.append("回复序号选择，或直接输入新路径")
        else:
            lines.append("请输入完整路径")
        return "\n".join(lines)

    @staticmethod
    def resolve_directory_input(raw: str, recent_paths: list | None = None) -> str | None:
        """解析目录输入：序号选最近路径，或直接路径；空输入返回 None。

        顺带修 Unix 路径开头 / 被命令前缀吃掉的情况（home/... → /home/...）。
        """
        text = (raw or "").strip()
        if not text:
            return None
        recent = list(recent_paths or [])
        if text.isdigit() and recent and 1 <= int(text) <= len(recent):
            return recent[int(text) - 1]
        # 修复：如果 Unix 路径开头的 / 被命令前缀吃掉，自动补回
        # Windows 盘符路径 (C:\...) 不处理
        if not text.startswith(("/", "\\")) and not (len(text) >= 2 and text[1] == ":"):
            if text.startswith(("home", "Users", "root", "opt", "var", "usr")):
                text = "/" + text
        return text

    def _step2_prompt(self, prefix: str = "") -> str:
        """构建步骤 2 的提示文本"""
        return self.format_directory_prompt(
            self.state.get("recent_paths") or [],
            prefix=prefix,
            header="步骤 2/5 — 工作目录:",
        )

    def _step5_prompt(self) -> WizardResult:
        """构建 YOLO 步骤提示"""
        step_no = self._yolo_step_number()
        total = self._total_steps()
        lines = [
            f"步骤 {step_no}/{total} — 启用 YOLO 模式?",
            "  [1] 否 — 正常审批流程",
            "  [2] 是 — 跳过审批和沙箱 (危险)",
        ]
        return WizardResult("\n".join(lines))

    def _reasoning_prompt(self) -> WizardResult:
        """构建思考深度提示（当前用于 Codex reasoning effort）"""
        agent = self.state.get("agent") or "codex"
        label = flavor_label(agent)
        lines = [f"代理: {agent} ({label})", "", "步骤 5/6 — 选择思考深度:"]
        for i, (_, opt_label) in enumerate(CODEX_REASONING_EFFORT_OPTIONS, 1):
            lines.append(f"  [{i}] {opt_label}")
        lines.append("回复序号选择，或直接输入 none/minimal/low/medium/high/xhigh/max（也可透传上游动态值）")
        lines.append("注意：旧版本 HAPI 可能不支持 modelReasoningEffort，选择可能无效")
        return WizardResult("\n".join(lines))

    def process(self, raw: str) -> WizardResult:
        """处理用户输入，推进向导状态，返回下一步结果"""
        s = self.state
        step = s["step"]

        if step == 1:
            return self._step1(raw)
        elif step == 2:
            return self._step2(raw)
        elif step == 3:
            return self._step3(raw)
        elif step == 31:
            return self._step31(raw)
        elif step == 4:
            return self._step4(raw)
        elif step == 41:
            return self._step41(raw)
        elif step == 5:
            return self._step5(raw)
        elif step == 6:
            return self._step6(raw)
        return WizardResult("未知步骤")

    def _step1(self, raw: str) -> WizardResult:
        """步骤 1: 选择机器"""
        s = self.state
        if not raw.isdigit() or not (1 <= int(raw) <= len(s["machines"])):
            return WizardResult(f"请输入 1~{len(s['machines'])} 的数字")

        idx = int(raw) - 1
        s["machine_id"] = s["machines"][idx]["id"]
        s["machine_label"] = s["labels"][idx]
        s["step"] = 2
        return WizardResult(
            f"已选机器: {s['machine_label']}",
            need_recent_paths=True)

    def _step2(self, raw: str) -> WizardResult:
        """步骤 2: 工作目录"""
        s = self.state
        directory = self.resolve_directory_input(raw, s.get("recent_paths") or [])
        if not directory:
            return WizardResult("目录不能为空，请重新输入")
        s["directory"] = directory

        s["step"] = 3
        lines = [
            f"目录: {s['directory']}",
            "",
            "步骤 3/5 — 会话类型:",
            "  [1] simple  — 直接使用选定目录",
            "  [2] worktree — 在仓库旁创建新工作树",
        ]
        return WizardResult("\n".join(lines))

    def _agent_prompt(self, prefix: str) -> WizardResult:
        """构建步骤 4 代理选择提示"""
        agents = self._agent_choices()
        lines = [prefix, "", "步骤 4/5 — 选择 Vibe Coding 代理:"]
        for i, a in enumerate(agents, 1):
            p = profile_for(a)
            note = f" — {p.notes}" if p.notes else ""
            lines.append(f"  [{i}] {a} ({p.label}){note}")
        lines.append("也可直接输入代理名（含 HAPI 新类型）")
        return WizardResult("\n".join(lines))

    def _step3(self, raw: str) -> WizardResult:
        """步骤 3: 会话类型"""
        s = self.state
        if raw == "1":
            s["session_type"] = "simple"
        elif raw == "2":
            s["session_type"] = "worktree"
        else:
            return WizardResult("请输入 1 或 2")

        if s["session_type"] == "worktree":
            s["step"] = 31
            return WizardResult("工作树名称 (回复任意名称，或输入 - 自动生成):")

        s["step"] = 4
        return self._agent_prompt(f"类型: {s['session_type']}")

    def _step31(self, raw: str) -> WizardResult:
        """步骤 3.1: 工作树名称"""
        s = self.state
        if raw != "-":
            s["worktree_name"] = raw
        s["step"] = 4
        type_label = f"类型: {s['session_type']}"
        if s["worktree_name"]:
            type_label += f" (工作树: {s['worktree_name']})"
        return self._agent_prompt(type_label)

    def _step4(self, raw: str) -> WizardResult:
        """步骤 4: 选择代理"""
        s = self.state
        agents = self._agent_choices()
        token = normalize_flavor(raw)

        if raw.isdigit() and 1 <= int(raw) <= len(agents):
            chosen = agents[int(raw) - 1]
        elif token:
            if not is_creatable(token):
                p = profile_for(token)
                return WizardResult(f"❌ {p.label} 当前不可新建: {p.notes or '仅兼容已有 session'}")
            chosen = token
        else:
            return WizardResult(
                f"请输入 1~{len(agents)} 的数字，或代理名（推荐: {', '.join(agents)}）"
            )

        s["agent"] = chosen
        if supports_reasoning_effort(chosen):
            s["step"] = 41
            return self._reasoning_prompt()

        s["step"] = 5
        s["model_reasoning_effort"] = None
        return self._step5_prompt()

    def _step41(self, raw: str) -> WizardResult:
        """步骤 4.1: 选择思考深度（Codex/OpenCode reasoning effort）"""
        s = self.state
        if raw.isdigit() and 1 <= int(raw) <= len(CODEX_REASONING_EFFORT_OPTIONS):
            s["model_reasoning_effort"] = CODEX_REASONING_EFFORT_OPTIONS[int(raw) - 1][0]
        else:
            normalized = raw.strip().lower()
            # 列表外值允许透传（上游动态 reasoning effort）
            if not normalized:
                return WizardResult(
                    "请输入有效序号，或直接输入 none/minimal/low/medium/high/xhigh/max"
                )
            s["model_reasoning_effort"] = normalized

        s["step"] = 5
        return self._step5_prompt()

    def _step5(self, raw: str) -> WizardResult:
        """步骤 5: YOLO 模式"""
        s = self.state
        if raw == "1":
            s["yolo"] = False
        elif raw == "2":
            s["yolo"] = True
        else:
            return WizardResult("请输入 1 或 2")

        s["step"] = 6
        lines = [
            "即将创建 Session:",
            f"  机器:     {s['machine_label']}",
            f"  目录:     {s['directory']}",
            f"  类型:     {s['session_type']}",
            f"  代理:     {s['agent']}",
        ]
        if supports_reasoning_effort(s["agent"]):
            reasoning_text = s["model_reasoning_effort"] or "继承默认设置"
            lines.append(f"  思考深度: {reasoning_text}")
        lines.append(f"  YOLO:     {'是' if s['yolo'] else '否'}")
        if s["worktree_name"]:
            lines.append(f"  工作树名: {s['worktree_name']}")
        if s["agent"] == "codex" and s["yolo"]:
            lines.append("\n⚠️ 提醒: Codex YOLO 模式需要在 .codex 配置文件中设置信任文件夹，否则可能无法使用 tools:")
            lines.append(f'  [projects."{s["directory"]}"]')
            lines.append('  trust_level = "trusted"')
        p = profile_for(s["agent"])
        if p.notes:
            lines.append(f"\n备注: {p.notes}")
        lines.append("\n回复 y 确认创建，其他取消")
        return WizardResult("\n".join(lines))

    def _step6(self, raw: str) -> WizardResult:
        """步骤 6: 确认创建"""
        if raw.lower() != "y":
            return WizardResult("已取消", cancelled=True)
        return WizardResult("正在创建 ...", confirmed=True)
