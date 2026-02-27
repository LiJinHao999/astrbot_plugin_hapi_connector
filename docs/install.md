# HAPI 安装与启动指南

本文档说明如何安装并启动 [HAPI](https://github.com/tiann/hapi) 服务，以便配合本插件使用。

---

## 1. 安装 HAPI CLI

**npm（推荐）：**

```bash
npm install -g @twsxtd/hapi
```

**Homebrew（macOS）：**

```bash
brew install tiann/tap/hapi
```

**npx（免安装，临时体验）：**

```bash
npx @twsxtd/hapi
```

---

## 2. 启动 Hub

Hub 是 HAPI 的**核心服务**，负责会话持久化、权限管理和远程访问。

首次启动时，HAPI 会自动创建 `~/.hapi/` 目录、生成 **Access Token** 并保存到 `~/.hapi/settings.json`。

**根据你的网络环境选择部署模式：**

| 模式 | 适用场景 | 跳转 |
|------|---------|------|
| **A. 纯本地** | AstrBot 与 HAPI 在同一内网 | [查看](#a-纯本地模式) |
| **B. 公共中继** | 使用 HAPI 官方中继，零配置外网访问 | [查看](#b-公共中继模式) |
| **C. 外网远程** | 自建隧道 / VPS / Cloudflare Tunnel 等 | [查看](#c-外网远程自建隧道) |

---

### A. 纯本地模式

> AstrBot 与 HAPI Hub 位于**同一内网**

Hub 默认只监听 `127.0.0.1:3006`，根据 AstrBot 部署方式选择：

**情况 1：AstrBot 与 HAPI 在同一宿主机（非 Docker）**

直接启动，无需额外配置：

```bash
hapi hub --no-relay
```

**情况 2：AstrBot 在 Docker 容器内，或二者在同一局域网**

需要先让 Hub 监听所有网卡。在 `~/.hapi/settings.json` 中添加（文件不存在则新建）：

```json
{
  "listenHost": "0.0.0.0"
}
```

然后启动：

```bash
hapi hub --no-relay
```

---

### B. 公共中继模式

> 使用 HAPI 官方中继，**零配置**即可外网访问

```bash
hapi hub --relay
```

不带参数时默认使用中继模式（`hapi hub` 等同于 `hapi hub --relay`）。启动后终端会显示**访问 URL 和二维码**，扫码即可从任意设备访问。

- **端对端加密**（WireGuard + TLS），无需额外配置，自动穿透 NAT 和防火墙
- 默认使用 UDP，如遇连接问题可强制 TCP：`HAPI_RELAY_FORCE_TCP=true hapi hub --relay`

---

### C. 外网远程（自建隧道）

> 适用于 HAPI 与 AstrBot **无法直接互访**的情况

可参考 [HAPI 官方部署文档](https://github.com/tiann/hapi/blob/main/docs/guide/installation.md#self-hosted-tunnels) 了解更多自建方案。

**Cloudflare Zero Trust 隧道（推荐）**

> ⚠️ **不支持** Cloudflare Quick Tunnels（TryCloudflare），因其不支持 SSE。请使用 **Named Tunnel**。

```bash
cloudflared tunnel create hapi
cloudflared tunnel route dns hapi hapi.yourdomain.com
cloudflared tunnel --protocol http2 run hapi
```

推荐配合 **Cloudflare Zero Trust Access** 保护你的服务。本插件已内置 CF Access 认证支持，配置方法见 👉 [CF Access 配置指南](cf_access_guide.md)

**Tailscale**

```bash
sudo tailscale up
hapi hub --no-relay
```

通过 Tailscale IP 访问：`http://100.x.x.x:3006`

**公网 IP / 反向代理**

直接通过 `http://your-server-ip:3006` 访问，生产环境**强烈建议**配合 Nginx / Caddy 启用 HTTPS。

### 后台持久运行

> 以上命令均为**前台运行**，关闭终端后 Hub 即停止。生产环境请选择以下方式之一：

<details>
<summary>nohup（快速临时方案）</summary>

```bash
# Hub
nohup hapi hub --relay > ~/.hapi/logs/hub.log 2>&1 &

# Runner（如需）
nohup hapi runner start --foreground > ~/.hapi/logs/runner.log 2>&1 &
```

查看日志：

```bash
tail -f ~/.hapi/logs/hub.log
tail -f ~/.hapi/logs/runner.log
```

停止：

```bash
pkill -f "hapi hub"
pkill -f "hapi runner"
```

</details>

<details>
<summary>pm2（推荐Node.js用户使用，支持崩溃自动重启和开机自启）</summary>

```bash
npm install -g pm2

pm2 start "hapi hub --relay" --name hapi-hub
pm2 start "hapi runner start --foreground" --name hapi-runner

# 查看状态和日志
pm2 status
pm2 logs hapi-hub
pm2 logs hapi-runner

# 开机自启
pm2 startup   # 按提示执行输出的命令
pm2 save
```

</details>

<details>
<summary>macOS：launchd</summary>

创建 `~/Library/LaunchAgents/com.hapi.hub.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.hapi.hub</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/hapi</string>
        <string>hub</string>
        <string>--relay</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>/Users/YOUR_USERNAME/.hapi/logs/hub.log</string>
    <key>StandardErrorPath</key><string>/Users/YOUR_USERNAME/.hapi/logs/hub.log</string>
</dict>
</plist>
```

加载/卸载：

```bash
launchctl load ~/Library/LaunchAgents/com.hapi.hub.plist
launchctl unload ~/Library/LaunchAgents/com.hapi.hub.plist
```

> macOS 休眠时可能挂起后台进程，可用 `caffeinate -dimsu hapi hub --relay` 防止休眠。

</details>

<details>
<summary>Linux：systemd</summary>

创建 `~/.config/systemd/user/hapi-hub.service`：

```ini
[Unit]
Description=HAPI Hub
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/hapi hub --relay
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

启用并启动：

```bash
systemctl --user daemon-reload
systemctl --user enable hapi-hub
systemctl --user start hapi-hub

# 查看状态/日志
systemctl --user status hapi-hub
journalctl --user -u hapi-hub -f
```

> 若需登出后仍保持运行：`loginctl enable-linger $USER`

</details>

---

## 3. 启动 Runner（可选）

Runner 是后台服务，允许从手机 / 网页 / AstrBot **远程创建新会话**，无需保持终端开启。不启动 Runner 的话，你需要手动通过 `hapi` 命令启动 session。

```bash
hapi runner start    # 启动
hapi runner status   # 查看状态
hapi runner logs     # 查看日志
hapi runner stop     # 停止
```

启动后，当前机器会出现在 HAPI 的"Machines"列表中，可远程派发会话。

---

## 4. CLI 认证配置（多机器场景）

如果 Hub **不在本机**（部署在其它服务器上），需要先配置连接信息：

**方式一：环境变量**

```bash
export HAPI_API_URL="http://your-hub-ip:3006"
export CLI_API_TOKEN="your-token-here"
```

**方式二：交互式登录**

```bash
hapi auth login
```

其他认证命令：

```bash
hapi auth status    # 查看当前认证状态
hapi auth logout    # 登出
```

配置完成后，可参考步骤 3 在该机器上启动 Runner，实现**同一个 Hub 管理多台机器**。

---

## 5. 获取 Access Token

首次启动 Hub 后，Token 会**打印在终端**，同时保存在 `~/.hapi/settings.json`。

查看 Token：

```bash
cat ~/.hapi/settings.json
```

找到 **`cliApiToken`** 字段的值，即为 Access Token。

---

## 6. 填写插件配置

在 AstrBot 管理面板的插件配置页填写以下**必填**字段：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| **`hapi_endpoint`** | HAPI Hub 的访问地址 | 见下表 |
| **`access_token`** | 上一步获取的 Access Token | `your-token-here` |

**`hapi_endpoint` 根据部署方式填写：**

| 场景 | 填写值 | 前置条件 |
|------|--------|----------|
| 同一宿主机（非 Docker） | `http://localhost:3006` | 无 |
| Docker 内，宿主机为 macOS / Windows | `http://host.docker.internal:3006` | `listenHost` → `0.0.0.0` |
| Docker 内，宿主机为 Linux | `http://172.17.0.1:3006` | `listenHost` → `0.0.0.0` |
| 同一内网 / Tailscale | `http://<HAPI机器IP>:3006` | `listenHost` → `0.0.0.0` |
| 公共中继 / 自建隧道 | 中继域名或你的域名，如 `https://xxx.hapi.run` | 对应模式已启动 |

> ⚠️ **Docker 用户注意**：容器内无法直接访问宿主机的 `localhost`，需先按第 2 节将 `listenHost` 改为 `0.0.0.0`，再用上表对应地址。Linux 的 `172.17.0.1` 是默认 docker0 网桥地址，如有自定义网络请替换为实际网关 IP。

配置完成后，发送 **`/hapi list`** 验证连接是否正常。
