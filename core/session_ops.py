"""Session 操作函数：异步封装多步 API 调用"""

import json

from .hapi_client import AsyncHapiClient


async def fetch_sessions(client: AsyncHapiClient) -> list[dict]:
    """获取所有 session 列表"""
    resp = await client.get("/api/sessions")
    resp.raise_for_status()
    data = await resp.json()
    resp.release()
    return data.get("sessions", [])


async def fetch_session_detail(client: AsyncHapiClient, sid: str) -> dict:
    """获取单个 session 详情"""
    resp = await client.get(f"/api/sessions/{sid}")
    resp.raise_for_status()
    data = await resp.json()
    resp.release()
    return data.get("session", data)


async def fetch_messages(client: AsyncHapiClient, sid: str, limit: int = 10) -> list[dict]:
    """获取 session 的最近消息"""
    resp = await client.get(f"/api/sessions/{sid}/messages", params={"limit": limit})
    resp.raise_for_status()
    data = await resp.json()
    resp.release()
    return data.get("messages", [])


async def _finish(resp, ok_msg: str, fail_prefix: str) -> tuple[bool, str]:
    """统一处理写操作响应：成功返回 ok_msg，失败返回中文错误 + 状态码与响应体片段"""
    if resp.ok:
        resp.release()
        return True, ok_msg
    body = await resp.text()
    resp.release()
    return False, f"{fail_prefix}: {resp.status} {body[:200]}"


async def fetch_slash_commands(client: AsyncHapiClient, sid: str) -> list[dict]:
    """获取 session 支持的斜杠命令列表（内置 + 用户/项目/插件自定义）。

    上游 GET /api/sessions/:id/slash-commands：优先 RPC 向 agent 实时查询，
    失败时 Hub 自身回退 metadata.slashCommands。命令 name 不带前导斜杠。
    """
    resp = await client.get(f"/api/sessions/{sid}/slash-commands")
    resp.raise_for_status()
    data = await resp.json()
    resp.release()
    if not data.get("success", True):
        return []
    return data.get("commands", []) or []


async def send_message(client: AsyncHapiClient, sid: str, text: str,
                       attachments: list[dict] | None = None) -> tuple[bool, str]:
    """发送消息到 session（可附带已上传的附件），返回 (成功, 描述)"""
    payload = {"text": text}
    if attachments:
        payload["attachments"] = attachments

    resp = await client.post(f"/api/sessions/{sid}/messages", json=payload)
    ok_msg = f"已发送 -> [{sid[:8]}]"
    if attachments:
        ok_msg += f"（附件 ×{len(attachments)}）"
    return await _finish(resp, ok_msg, "发送失败")


async def set_permission_mode(client: AsyncHapiClient, sid: str, mode: str) -> tuple[bool, str]:
    """设置权限模式"""
    resp = await client.post(f"/api/sessions/{sid}/permission-mode", json={"mode": mode})
    return await _finish(resp, f"权限模式已切换为: {mode}", "切换失败")


async def set_model_mode(client: AsyncHapiClient, sid: str, model: str) -> tuple[bool, str]:
    """设置模型模式（由 session flavor / HAPI 决定是否支持）"""
    resp = await client.post(f"/api/sessions/{sid}/model", json={"model": model})
    return await _finish(resp, f"模型已切换为: {model}", "切换失败")


async def set_effort(client: AsyncHapiClient, sid: str, effort: str | None) -> tuple[bool, str]:
    """设置推理强度（/effort，如 Claude / Grok / Pi）"""
    resp = await client.post(f"/api/sessions/{sid}/effort", json={"effort": effort})
    return await _finish(resp, f"推理强度已切换为: {effort or '默认'}", "切换失败")


async def set_codex_reasoning_effort(client: AsyncHapiClient, sid: str, effort: str | None) -> tuple[bool, str]:
    """设置 modelReasoningEffort（Codex / OpenCode 等）"""
    resp = await client.post(f"/api/sessions/{sid}/model-reasoning-effort", json={"modelReasoningEffort": effort})
    return await _finish(resp, f"推理强度已切换为: {effort or '默认'}", "切换失败")


async def set_service_tier(client: AsyncHapiClient, sid: str, tier: str) -> tuple[bool, str]:
    """设置 Codex Fast mode（service tier: fast | standard）"""
    resp = await client.post(f"/api/sessions/{sid}/service-tier", json={"serviceTier": tier})
    label = "Fast 已开启" if tier == "fast" else "Fast 已关闭（standard）"
    return await _finish(resp, label, "切换失败")


async def set_collaboration_mode(client: AsyncHapiClient, sid: str, mode: str) -> tuple[bool, str]:
    """设置协作模式（如 Codex plan）"""
    resp = await client.post(f"/api/sessions/{sid}/collaboration-mode", json={"mode": mode})
    return await _finish(resp, f"协作模式已切换为: {mode}", "切换失败")


async def approve_permission(client: AsyncHapiClient, sid: str, rid: str,
                             answers: dict | None = None) -> tuple[bool, str]:
    """批准权限请求；AskUserQuestion 需传 answers={"0": ["选项label"]}"""
    body = {"answers": answers} if answers else {}
    resp = await client.post(f"/api/sessions/{sid}/permissions/{rid}/approve", json=body)
    return await _finish(resp, "已批准", "批准失败")


async def answer_permission_question(client: AsyncHapiClient, sid: str, rid: str,
                                     answers: dict) -> tuple[bool, str]:
    """提交 AskUserQuestion 的回答。"""
    return await approve_permission(client, sid, rid, answers=answers)


async def deny_permission(client: AsyncHapiClient, sid: str, rid: str) -> tuple[bool, str]:
    """拒绝权限请求"""
    resp = await client.post(f"/api/sessions/{sid}/permissions/{rid}/deny", json={})
    return await _finish(resp, "已拒绝", "拒绝失败")


async def switch_to_remote(client: AsyncHapiClient, sid: str) -> tuple[bool, str]:
    """切换 session 到 remote 远程托管模式"""
    resp = await client.post(f"/api/sessions/{sid}/switch", json={})
    return await _finish(resp, "已切换到 remote 远程托管模式", "切换失败")


async def abort_session(client: AsyncHapiClient, sid: str) -> tuple[bool, str]:
    """中断活跃的 session"""
    resp = await client.post(f"/api/sessions/{sid}/abort", json={})
    return await _finish(resp, f"已中断 [{sid[:8]}]", "中断失败")


async def archive_session(client: AsyncHapiClient, sid: str) -> tuple[bool, str]:
    """归档 session"""
    resp = await client.post(f"/api/sessions/{sid}/archive", json={})
    return await _finish(resp, f"归档成功 [{sid[:8]}]", "归档失败")


async def resume_session(client: AsyncHapiClient, sid: str) -> tuple[bool, str, str | None]:
    """恢复已停掉的会话。返回 (成功, 描述, 恢复后的 session_id 或 None)。"""
    resp = await client.post(f"/api/sessions/{sid}/resume", json={})
    if resp.ok:
        data = await resp.json()
        resp.release()
        resumed_sid = data.get("sessionId") or sid
        return True, f"已恢复 [{resumed_sid[:8]}]", resumed_sid
    else:
        body = await resp.text()
        resp.release()
        return False, _format_resume_error(resp.status, body), None


async def reopen_session(client: AsyncHapiClient, sid: str) -> tuple[bool, str, str | None]:
    """恢复已停掉的会话（resume 备用接口）。返回 (成功, 描述, session_id 或 None)。"""
    resp = await client.post(f"/api/sessions/{sid}/reopen", json={})
    if resp.ok:
        data = await resp.json()
        resp.release()
        reopened_sid = (
            data.get("sessionId")
            or (data.get("session") or {}).get("id")
            or sid
        )
        return True, f"已恢复 [{reopened_sid[:8]}]", reopened_sid

    body = await resp.text()
    resp.release()
    return False, _format_reopen_error(resp.status, body), None


def _format_resume_error(status: int, body: str) -> str:
    """格式化 HAPI resume 错误，为已知的上游失败场景补充说明"""
    code = ""
    error = ""
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            code = str(data.get("code") or "")
            error = str(data.get("error") or "")
    except json.JSONDecodeError:
        pass

    if code == "resume_unavailable" and error == "Resume session ID unavailable":
        return (
            "恢复失败：HAPI 找到了这个会话，但会话 metadata 里没有原生恢复 ID "
            "（例如 claudeSessionId / codexSessionId）。\n"
            "这通常表示原生会话 ID 没来得及写入 HAPI，或写入前 CLI/runner 已断开；"
            "HAPI 前端此时一般也无法无损恢复。\n"
            "可尝试 /hapi reopen；"
            "或在原机器上用原生 CLI 按 session id 恢复。"
            "找不到的话只能在同目录新建会话，并手动补充摘要或关键上下文。"
        )

    detail = error or body[:200]
    return f"恢复失败: {status} {detail}"


def _format_reopen_error(status: int, body: str) -> str:
    """格式化 HAPI reopen 错误"""
    code = ""
    error = ""
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            code = str(data.get("code") or "")
            error = str(data.get("error") or data.get("message") or "")
    except json.JSONDecodeError:
        pass

    if code or error:
        detail = f"{code} {error}".strip() if code else error
        return f"恢复失败: {status} {detail}"
    return f"恢复失败: {status} {body[:200]}"


async def rename_session(client: AsyncHapiClient, sid: str, new_name: str) -> tuple[bool, str]:
    """重命名 session"""
    resp = await client.patch(f"/api/sessions/{sid}", json={"name": new_name})
    return await _finish(resp, f"重命名成功 [{sid[:8]}]", "重命名失败")


async def delete_session(client: AsyncHapiClient, sid: str) -> tuple[bool, str]:
    """删除 session"""
    resp = await client.delete(f"/api/sessions/{sid}")
    return await _finish(resp, f"删除成功 [{sid[:8]}]", "删除失败")


async def fetch_machines(client: AsyncHapiClient) -> list[dict]:
    """获取在线机器列表"""
    resp = await client.get("/api/machines")
    resp.raise_for_status()
    data = await resp.json()
    resp.release()
    machines = data.get("machines", [])
    return [m for m in machines if m.get("active")]


async def fetch_recent_paths(client: AsyncHapiClient) -> list[str]:
    """从已有 sessions 提取去重的最近工作目录"""
    sessions = await fetch_sessions(client)
    paths = []
    for s in sessions:
        p = s.get("metadata", {}).get("path", "")
        if p and p not in paths:
            paths.append(p)
    return paths


async def spawn_session(client: AsyncHapiClient, machine_id: str,
                        directory: str, agent: str, session_type: str = "simple",
                        yolo: bool = False, worktree_name: str = "",
                        model_reasoning_effort: str | None = None,
                        model: str | None = None,
                        effort: str | None = None,
                        permission_mode: str | None = None) -> tuple[bool, str, str | None]:
    """创建新 session，返回 (成功, 消息, session_id 或 None)

    额外可选参数对齐 HAPI SpawnSessionRequest：model / effort / permissionMode。
    现有调用方可不传，保持兼容。
    """
    body = {
        "directory": directory,
        "agent": agent,
        "sessionType": session_type,
        "yolo": yolo,
    }
    if worktree_name:
        body["worktreeName"] = worktree_name
    if model_reasoning_effort:
        body["modelReasoningEffort"] = model_reasoning_effort
    if model:
        body["model"] = model
    if effort:
        body["effort"] = effort
    if permission_mode:
        body["permissionMode"] = permission_mode

    resp = await client.post(f"/api/machines/{machine_id}/spawn", json=body)
    if resp.status != 200:
        body_text = await resp.text()
        resp.release()
        return False, f"创建失败: {resp.status} {body_text[:300]}", None

    result = await resp.json()
    resp.release()
    if result.get("type") == "success":
        sid = result["sessionId"]
        return True, f"创建成功！Session ID: {sid}", sid
    else:
        return False, f"创建失败: {result.get('message', '未知错误')}", None


async def list_files(client: AsyncHapiClient, sid: str,
                     query: str = "", limit: int = 200) -> list[dict]:
    """搜索 session 工作目录下的文件（ripgrep）"""
    params: dict = {"limit": limit}
    if query:
        params["query"] = query
    data = await client.get_json(f"/api/sessions/{sid}/files", params=params)
    return data.get("files", [])


async def list_directory(client: AsyncHapiClient, sid: str,
                         path: str = ".") -> list[dict]:
    """列出远端目录，每个条目含 name/type/size/modified"""
    data = await client.get_json(f"/api/sessions/{sid}/directory",
                                 params={"path": path})
    return data.get("entries", [])


async def read_file(client: AsyncHapiClient, sid: str,
                    path: str) -> tuple[bool, str]:
    """读取远端文件，返回 (成功, base64内容或错误信息)"""
    resp = await client.get(f"/api/sessions/{sid}/file", params={"path": path})
    if not resp.ok:
        body = await resp.text()
        resp.release()
        return False, f"读取失败: {resp.status} {body[:200]}"
    data = await resp.json()
    resp.release()
    if not data.get("success"):
        return False, f"读取失败: {data.get('error', data.get('message', '未知错误'))}"
    content = data.get("content", "")
    if not content:
        return False, "文件内容为空或不存在"
    return True, content
