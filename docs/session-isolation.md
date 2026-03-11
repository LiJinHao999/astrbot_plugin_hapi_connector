# 多窗口会话隔离说明

**在不同 AstrBot 会话中（比如 QQ 的私聊、群聊）管理的不同 session 会话将会互相独立。**

根据 AstrBot 的窗口 id 进行区分，不同窗口里可见、可管理的 session 不同。

简单说应用场景：
- 你可以把 bot 再拉进一个群聊，为它取名为“xx任务”，群聊内的远程 coding session 的通知推送与审批，和你与 bot 的私聊会相互独立。这能让推送的通知根据群聊名称按场景区分开，多任务时更加整齐
- 如果有多位管理员 QQ 同时使用插件，不同管理员之间收到的通知同样独立

( 注: QQ 创建一个两人群的方法为：先拉一个你、bot、另一个人的三人群，再把另一个人踢掉，这样群聊就只有你和bot两个人了。 )

## 示例

### 群组列表示例

<p align="center">
  <img width="430" height="238" alt="群组列表示例" src="pics/隔离示例-群组列表.png" />
</p>

### 不同窗口可看到的 session 不同

| 未绑定 session 的窗口 | 私聊窗口 | 群聊窗口 |
| --- | --- | --- |
| <img src="pics/隔离示例-不存在session.png" width="100%" /> | <img src="pics/隔离示例-私聊.png" width="100%" /> | <img src="pics/隔离示例-群聊.png" width="100%" /> |

## 规则说明

在某个窗口使用 `sw` / `create` 命令后，将会自动把对应 session 的通知路由到当前会话。

- 使用 `hapi bind` 命令设置主要推送窗口。有通知时会默认发往此窗口，其它通知再根据绑定逻辑路由。
- 使用 `hapi bind claude|codex|gemini` 可以分别给不同 flavor 的 session 指定默认通知窗口。
- 使用 `hapi bind reset` 命令可以清除所有绑定路由关系。
- 不同对话窗口之间的通知、权限审批、所操作的窗口互相独立。
