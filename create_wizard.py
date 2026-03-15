"""创建 Session 向导状态机：步骤推进、输入校验、提示文本构建"""

from .constants import AGENTS


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
    """5 步创建 Session 向导，纯状态机，不依赖 AstrBot 事件系统"""

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

    def _step2_prompt(self, prefix: str = "") -> str:
        """构建步骤 2 的提示文本"""
        s = self.state
        lines = []
        if prefix:
            lines.extend([prefix, ""])
        lines.append("步骤 2/5 — 工作目录:")
        if s["recent_paths"]:
            lines.append("最近使用的目录:")
            for i, p in enumerate(s["recent_paths"], 1):
                lines.append(f"  [{i}] {p}")
            lines.append("回复序号选择，或直接输入新路径")
        else:
            lines.append("请输入完整路径")
        return "\n".join(lines)

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
        recent = s["recent_paths"]
        if raw.isdigit() and recent and 1 <= int(raw) <= len(recent):
            s["directory"] = recent[int(raw) - 1]
        elif raw:
            # 修复：如果路径看起来像绝对路径但开头的 / 或 \ 被命令前缀吃掉，自动补回
            if raw and not raw.startswith(("/", "\\")):
                # Windows 盘符路径 (C:, D: 等) 或 Unix 根路径被吃掉的情况
                if len(raw) >= 2 and raw[1] == ":" or raw.startswith(("home", "Users", "root", "opt", "var", "usr")):
                    raw = "/" + raw
            s["directory"] = raw
        else:
            return WizardResult("目录不能为空，请重新输入")

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
        lines = [prefix, "", "步骤 4/5 — 选择 Vibe Coding 代理:"]
        for i, a in enumerate(AGENTS, 1):
            lines.append(f"  [{i}] {a}")
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
        if raw.isdigit() and 1 <= int(raw) <= len(AGENTS):
            s["agent"] = AGENTS[int(raw) - 1]
        elif raw in AGENTS:
            s["agent"] = raw
        else:
            return WizardResult(f"请输入 1~{len(AGENTS)} 的数字或代理名")

        s["step"] = 5
        lines = [
            f"代理: {s['agent']}",
            "",
            "步骤 5/5 — 启用 YOLO 模式?",
            "  [1] 否 — 正常审批流程",
            "  [2] 是 — 跳过审批和沙箱 (危险)",
        ]
        return WizardResult("\n".join(lines))

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
            f"  YOLO:     {'是' if s['yolo'] else '否'}",
        ]
        if s["worktree_name"]:
            lines.append(f"  工作树名: {s['worktree_name']}")
        if s["agent"] == "codex" and s["yolo"]:
            lines.append(f"\n⚠ 提醒: Codex YOLO 模式需要在.codex配置文件中设置信任文件夹，否则可能无法使用 tools:")
            lines.append(f'  [projects."{s["directory"]}"]')
            lines.append('  trust_level = "trusted"')
        lines.append("\n回复 y 确认创建，其他取消")
        return WizardResult("\n".join(lines))

    def _step6(self, raw: str) -> WizardResult:
        """步骤 6: 确认创建"""
        if raw.lower() != "y":
            return WizardResult("已取消", cancelled=True)
        return WizardResult("正在创建 ...", confirmed=True)
