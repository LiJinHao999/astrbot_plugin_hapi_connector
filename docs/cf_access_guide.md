# Cloudflare Zero Trust Access 配置指南

本文档记录如何为 HAPI 服务配置 Cloudflare Zero Trust Access 认证，使插件能通过 CF Access 保护层连接 HAPI。

## 前置条件

- HAPI 服务已部署并通过 Cloudflare Tunnel 或其他方式接入 Cloudflare
- 已在 Cloudflare Zero Trust 中为 HAPI 服务创建并托管了 Access Application

若未完成此这两步，建议先参考[HAPI 官方部署文档](https://github.com/tiann/hapi/blob/main/docs/guide/installation.md#self-hosted-tunnels)

## 步骤一：创建 Service Token（服务令牌）

插件是后台程序，需要使用Cloudflare的 **Service Token** 方式认证。

1. 登录 [Cloudflare Zero Trust 控制台](https://one.dash.cloudflare.com/)
2. 左侧菜单进入 **Access（访问控制）** → **服务凭据（Service Auth）** → **服务令牌（Service Tokens）**
3. 点击 **添加服务令牌**

![服务令牌页面](pics/01_service_tokens_page.png)

4. 输入令牌名称（如 `astrbot_hapi`），选择有效期（建议选较长期限）
5. 点击生成令牌

![创建令牌](pics/02_create_token.png)

6. **立即复制保存** 页面显示的 **Client ID** 和 **Client Secret**

> ⚠️ Client Secret 仅在创建时显示一次，关闭页面后无法再查看！

![复制凭证](pics/03_copy_credentials.png)

## 步骤二：添加 Service Auth 策略

有了 Service Token 还不够，需要在 HAPI 的 Access Application 中添加一条策略来放行它。

1. 在 Zero Trust 控制台，进入 **Access（访问控制）** → **应用程序（Applications）**
2. 找到你为 HAPI 创建的应用，点击进入编辑
![找到你创建的应用](pics/04_find_your_app.png)
3. 切换到 **策略（Policies）** 标签页
4. 点击 **创建新策略**，配置如下：
   - **策略名称**：如 `astrbot-service-auth`
   - **操作（Action）**：选择 **Service Auth**
   - **Include 规则**：选择器选 **Service Token**，值选你刚创建的令牌（如 `astrbot_hapi`）

![创建新策略](pics/05_find_policy.png)

5. 保存策略

![添加策略](pics/06_add_policy.png)

6.  检查
回到步骤4处的策略配置页，如果已有策略中没有刚刚创建的Service Auth，请手动添加，点击图中的选择现有策略，勾上service_auth后保存
![检查](pics/07_check_policy.png)

> 💡 Service Auth 策略会自动优先于 Allow（邮箱验证）策略执行，无需手动调整顺序。这样，浏览器访问你的域名时仍走邮箱验证，插件用 Service Token 直接通过。