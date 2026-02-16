"""Session 操作函数：异步封装多步 API 调用"""

from .hapi_client import AsyncHapiClient


async def fetch_sessions(client: AsyncHapiClient) -> list[dict]:
    """获取所有 session 列表"""
    resp = await client.get("/api/sessions")
    resp.raise_for_status()
    data = await resp.json()
    return data.get("sessions", [])


async def fetch_session_detail(client: AsyncHapiClient, sid: str) -> dict:
    """获取单个 session 详情"""
    resp = await client.get(f"/api/sessions/{sid}")
    resp.raise_for_status()
    data = await resp.json()
    return data.get("session", data)


async def fetch_messages(client: AsyncHapiClient, sid: str, limit: int = 10) -> list[dict]:
    """获取 session 的最近消息"""
    resp = await client.get(f"/api/sessions/{sid}/messages", params={"limit": limit})
    resp.raise_for_status()
    data = await resp.json()
    return data.get("messages", [])


async def send_message(client: AsyncHapiClient, sid: str, text: str) -> tuple[bool, str]:
    """发送消息到 session，返回 (成功, 描述)"""
    resp = await client.post(f"/api/sessions/{sid}/messages", json={"text": text})
    if resp.ok:
        return True, f"已发送 -> [{sid[:8]}]"
    else:
        body = await resp.text()
        return False, f"发送失败: {resp.status} {body[:200]}"


async def set_permission_mode(client: AsyncHapiClient, sid: str, mode: str) -> tuple[bool, str]:
    """设置权限模式"""
    resp = await client.post(f"/api/sessions/{sid}/permission-mode", json={"mode": mode})
    if resp.ok:
        return True, f"权限模式已切换为: {mode}"
    else:
        body = await resp.text()
        return False, f"切换失败: {resp.status} {body[:200]}"


async def set_model_mode(client: AsyncHapiClient, sid: str, model: str) -> tuple[bool, str]:
    """设置模型模式（仅 Claude）"""
    resp = await client.post(f"/api/sessions/{sid}/model", json={"model": model})
    if resp.ok:
        return True, f"模型已切换为: {model}"
    else:
        body = await resp.text()
        return False, f"切换失败: {resp.status} {body[:200]}"


async def approve_permission(client: AsyncHapiClient, sid: str, rid: str) -> tuple[bool, str]:
    """批准权限请求"""
    resp = await client.post(f"/api/sessions/{sid}/permissions/{rid}/approve", json={})
    if resp.ok:
        return True, "已批准"
    else:
        body = await resp.text()
        return False, f"批准失败: {resp.status} {body[:200]}"


async def deny_permission(client: AsyncHapiClient, sid: str, rid: str) -> tuple[bool, str]:
    """拒绝权限请求"""
    resp = await client.post(f"/api/sessions/{sid}/permissions/{rid}/deny", json={})
    if resp.ok:
        return True, "已拒绝"
    else:
        body = await resp.text()
        return False, f"拒绝失败: {resp.status} {body[:200]}"


async def abort_session(client: AsyncHapiClient, sid: str) -> tuple[bool, str]:
    """中断活跃的 session"""
    resp = await client.post(f"/api/sessions/{sid}/abort", json={})
    if resp.ok:
        return True, f"已中断 [{sid[:8]}]"
    else:
        body = await resp.text()
        return False, f"中断失败: {resp.status} {body[:200]}"


async def archive_session(client: AsyncHapiClient, sid: str) -> tuple[bool, str]:
    """归档 session（PATCH 要求 name 必填，先取当前名称）"""
    detail = await fetch_session_detail(client, sid)
    name = detail.get("metadata", {}).get("summary", {}).get("text", "") or sid[:8]
    resp = await client.patch(f"/api/sessions/{sid}", json={"name": name, "active": False})
    if resp.ok:
        return True, f"归档成功 [{sid[:8]}]"
    else:
        body = await resp.text()
        return False, f"归档失败: {resp.status} {body[:200]}"


async def rename_session(client: AsyncHapiClient, sid: str, new_name: str) -> tuple[bool, str]:
    """重命名 session"""
    resp = await client.patch(f"/api/sessions/{sid}", json={"name": new_name})
    if resp.ok:
        return True, f"重命名成功 [{sid[:8]}]"
    else:
        body = await resp.text()
        return False, f"重命名失败: {resp.status} {body[:200]}"


async def delete_session(client: AsyncHapiClient, sid: str) -> tuple[bool, str]:
    """删除 session"""
    resp = await client.delete(f"/api/sessions/{sid}")
    if resp.ok:
        return True, f"删除成功 [{sid[:8]}]"
    else:
        body = await resp.text()
        return False, f"删除失败: {resp.status} {body[:200]}"


async def fetch_machines(client: AsyncHapiClient) -> list[dict]:
    """获取在线机器列表"""
    resp = await client.get("/api/machines")
    resp.raise_for_status()
    data = await resp.json()
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
                        yolo: bool = False, worktree_name: str = "") -> tuple[bool, str]:
    """创建新 session"""
    body = {
        "directory": directory,
        "agent": agent,
        "sessionType": session_type,
        "yolo": yolo,
    }
    if worktree_name:
        body["worktreeName"] = worktree_name

    resp = await client.post(f"/api/machines/{machine_id}/spawn", json=body)
    if resp.status != 200:
        body_text = await resp.text()
        return False, f"创建失败: {resp.status} {body_text[:300]}"

    result = await resp.json()
    if result.get("type") == "success":
        sid = result["sessionId"]
        return True, f"创建成功! Session ID: {sid}"
    else:
        return False, f"创建失败: {result.get('message', '未知错误')}"
