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


async def fetch_generated_image(
    client: AsyncHapiClient, sid: str, image_id: str
) -> tuple[bytes | None, str | None, str | None]:
    """下载 session 内 HAPI 生成图。

    对应 Hub ``GET /api/sessions/:id/generated-images/:imageId``（base64 字节流）。
    返回 (bytes, mime, error)；成功时 error 为 None。
    """
    image_id = (image_id or "").strip()
    if not sid or not image_id:
        return None, None, "missing sid or imageId"
    path = f"/api/sessions/{sid}/generated-images/{image_id}"
    resp = await client.get(path)
    try:
        if resp.status == 404:
            body = await resp.text()
            return None, None, f"not found: {body[:120]}"
        if resp.status >= 400:
            body = await resp.text()
            return None, None, f"HTTP {resp.status}: {body[:160]}"
        raw = await resp.read()
        mime = resp.headers.get("Content-Type") or "application/octet-stream"
        # 部分错误仍 200 + JSON
        if not raw:
            return None, None, "empty body"
        if raw[:1] == b"{" and b"error" in raw[:200]:
            try:
                import json as _json

                err = _json.loads(raw.decode("utf-8", "replace"))
                return None, None, str(err.get("error") or err)[:160]
            except Exception:
                pass
        return raw, mime.split(";")[0].strip(), None
    finally:
        resp.release()


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


async def _fetch_git_command(
    client: AsyncHapiClient,
    path: str,
    *,
    params: dict | None = None,
    fail_prefix: str,
) -> tuple[bool, str, dict]:
    """git 系列只读接口统一解析（上游返回 CommandResponse / {success, stdout, error}）。

    返回 (ok, stdout 或错误文本, 原始数据)。错误体只截断展示，不抛异常。
    """
    resp = await client.get(path, params=params)
    try:
        data = await resp.json()
    except Exception:
        body = await resp.text()
        return False, f"{fail_prefix}: HTTP {resp.status} {body[:200]}", {}
    if not resp.ok:
        error = data.get("error") if isinstance(data, dict) else None
        return False, f"{fail_prefix}: HTTP {resp.status} {str(error or data)[:200]}", data
    if not data.get("success", True):
        error = data.get("error") if isinstance(data, dict) else None
        return False, f"{fail_prefix}: {str(error or '未知错误')[:200]}", data
    stdout = data.get("stdout") if isinstance(data, dict) else None
    return True, str(stdout or "") if stdout is not None else "", data


def _staged_param(staged: bool | None) -> dict | None:
    """staged 三态 → query 参数：True=仅暂存 / False=仅未暂存 / None=不传（上游默认）"""
    if staged is None:
        return None
    return {"staged": "true" if staged else "false"}


async def fetch_git_status(client: AsyncHapiClient, sid: str) -> tuple[bool, str, dict]:
    """获取 session 工作区 git 状态（GET /api/sessions/:id/git-status）。"""
    return await _fetch_git_command(
        client, f"/api/sessions/{sid}/git-status", fail_prefix="获取 git 状态失败"
    )


async def fetch_git_diff_numstat(
    client: AsyncHapiClient, sid: str, staged: bool | None = None
) -> tuple[bool, str, dict]:
    """获取变更统计（GET /api/sessions/:id/git-diff-numstat?staged=）。"""
    return await _fetch_git_command(
        client,
        f"/api/sessions/{sid}/git-diff-numstat",
        params=_staged_param(staged),
        fail_prefix="获取变更统计失败",
    )


async def fetch_git_diff_file(
    client: AsyncHapiClient, sid: str, path: str, staged: bool | None = None
) -> tuple[bool, str, dict]:
    """获取单文件 diff（GET /api/sessions/:id/git-diff-file?path=&staged=）。"""
    params: dict = {"path": path}
    staged_params = _staged_param(staged)
    if staged_params:
        params.update(staged_params)
    return await _fetch_git_command(
        client,
        f"/api/sessions/{sid}/git-diff-file",
        params=params,
        fail_prefix="获取文件 diff 失败",
    )


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


class SyncCodexError(Exception):
    """Codex Session 同步失败。

    携带 HAPI 返回的原始状态码与响应体，供上层原样展示（不吞错误）。
    """

    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


async def resolve_codex_session_id(
    client: AsyncHapiClient,
    session_id: str,
    *,
    machine_id: str | None = None,
    cwd: str | None = None,
) -> tuple[str, str | None, str | None]:
    """把 HAPI 会话 id 解析成本机 codex transcript id。

    HAPI 会话 id（/api/sessions 的 id）与 codex transcript id
    （/api/codex/sessions 的 id）是两套独立体系，唯一桥梁是 HAPI 会话
    metadata.codexSessionId——但会话执行过 /clear 后该字段会被清空
    （cli 端 resetCodexThread）。本函数按以下顺序兜底解析：

    1. metadata.codexSessionId 非空 → 直接用
    2. 传入 id 已存在于本机 codex 会话列表 → 直接用（幂等）
    3. metadata.path == codex session.cwd → 匹配
    4. metadata.name == codex session.title → 匹配

    返回 (codex_id, cwd, machine_id)；无法解析时抛 SyncCodexError。
    """
    session_id = (session_id or "").strip()
    if not session_id:
        raise ValueError("session_id 不能为空")

    meta: dict = {}
    try:
        data = await client.get_json("/api/sessions")
        for s in data.get("sessions", []):
            if s.get("id") == session_id:
                meta = s.get("metadata") or {}
                if not isinstance(meta, dict):
                    meta = {}
                if not machine_id:
                    machine_id = (
                        s.get("machineId") or s.get("machine_id")
                        or meta.get("machineId") or meta.get("machine_id")
                    )
                if not cwd:
                    cwd = (
                        s.get("cwd")
                        or meta.get("cwd") or meta.get("path")
                        or meta.get("workingDirectory")
                    )
                break
    except Exception:
        pass  # 拉取失败不阻断后续兜底解析

    # 1) metadata.codexSessionId 非空 → 直接用
    if meta.get("codexSessionId"):
        return str(meta["codexSessionId"]), cwd, machine_id

    # 2) 拉本机 codex 会话列表
    codex_sessions: list[dict] = []
    try:
        url = "/api/codex/sessions"
        if machine_id:
            url = f"/api/codex/sessions?machineId={machine_id}"
        data = await client.get_json(url)
        if isinstance(data, dict):
            codex_sessions = data.get("sessions", []) or []
    except Exception:
        pass  # 拉取失败继续尝试其他匹配方式

    # 传入 id 本身就在 codex 列表 → 直接用（幂等）
    for s in codex_sessions:
        if s.get("id") == session_id:
            return session_id, cwd or s.get("cwd"), machine_id

    # 3) 按 cwd 匹配（HAPI metadata.path == codex session.cwd）
    if cwd:
        norm = cwd.rstrip("/")
        for s in codex_sessions:
            if (s.get("cwd") or "").rstrip("/") == norm:
                return str(s["id"]), s.get("cwd"), machine_id

    # 4) 按 title 匹配（HAPI metadata.name == codex session.title）
    name = (meta.get("name") or "").strip()
    if name:
        for s in codex_sessions:
            if (s.get("title") or "").strip() == name:
                return str(s["id"]), s.get("cwd"), machine_id

    raise SyncCodexError(
        f"无法解析 Codex Session: {session_id[:16]} 在本机 codex 会话中不存在"
        "（可能已归档、删除或机器离线），请检查后重试。"
    )


async def sync_codex_session(
    client: AsyncHapiClient,
    session_id: str,
    *,
    machine_id: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    model_reasoning_effort: str | None = None,
    service_tier: str | None = None,
    collaboration_mode: str = "default",
    yolo: bool = False,
) -> dict:
    """同步指定 Codex Session 到 HAPI（POST /api/codex/sync-session）。

    成功返回 HAPI 的完整 JSON；失败抛 SyncCodexError（含原始状态码/响应体）。
    model / modelReasoningEffort 未指定时不在请求体中携带，
    避免覆盖 HAPI 服务端默认值。
    """
    session_id = (session_id or "").strip()
    if not session_id:
        raise ValueError("session_id 不能为空")

    # HAPI 会话 id 与 codex transcript id 是两套体系：先解析成 codex id
    try:
        session_id, resolved_cwd, resolved_machine = await resolve_codex_session_id(
            client, session_id, machine_id=machine_id, cwd=cwd
        )
        if resolved_cwd:
            cwd = resolved_cwd
        if resolved_machine:
            machine_id = resolved_machine
    except SyncCodexError:
        raise
    except Exception:
        pass  # 解析失败时沿用原 session_id 继续

    if service_tier not in (None, "fast", "standard"):
        raise ValueError(f"service_tier 非法: {service_tier!r}（应为 fast|standard|None）")
    if collaboration_mode not in ("default", "plan"):
        raise ValueError(f"collaboration_mode 非法: {collaboration_mode!r}（应为 default|plan）")

    payload: dict = {
        "sessionIds": [session_id],
        "collaborationMode": collaboration_mode,
        "yolo": bool(yolo),
    }
    if machine_id:
        payload["machineId"] = machine_id
    if cwd:
        payload["cwd"] = cwd
    if model:
        payload["model"] = model
    if model_reasoning_effort:
        payload["modelReasoningEffort"] = model_reasoning_effort
    if service_tier:
        payload["serviceTier"] = service_tier

    try:
        resp = await client.post("/api/codex/sync-session", json=payload)
    except Exception as e:
        raise SyncCodexError(f"网络错误: {e}") from e

    try:
        if resp.ok:
            data = await resp.json()
            resp.release()
            # HAPI 业务失败时也返回 HTTP 200（success=false + error 字段），
            # 必须检查 data.success，不能只看 HTTP 状态码。
            if isinstance(data, dict) and data.get("success") is True:
                return data
            detail = ""
            if isinstance(data, dict):
                detail = str(data.get("error") or data.get("message") or "").strip()
                if not detail:
                    detail = str(data.get("output") or "").strip()
            raise SyncCodexError(
                f"HAPI 同步失败: {detail[:500]}" if detail
                else "HAPI 同步失败（success=false），请查看 HAPI 日志",
                status=200,
            )
        body = await resp.text()
        status = resp.status
        resp.release()
        raise SyncCodexError(
            f"HAPI 同步失败: HTTP {status} {body[:500]}",
            status=status,
            body=body,
        )
    except SyncCodexError:
        raise
    except Exception as e:
        raise SyncCodexError(f"响应解析失败: {e}") from e
