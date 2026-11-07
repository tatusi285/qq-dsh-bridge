# dsh-qq-bridge（宿主插件，未安装）

在 DeepSeek Harness **宿主进程内**开一个只监听 127.0.0.1 的小接口，让 QQ 桥能把
一句话排进**当前活着的 agent**。它不 resume、不抢会话写锁——因为用的就是桌面正在
用的那个 agent（`ctx.agents`）。

## 为什么不用 ACP

M0 的结论：桌面进程持有会话的写句柄（Windows 命名信号量），外部进程 resume 会被
拒绝。而宿主插件里 `agent.followup(...)` 就是输入框用的那条 inbox 路径，排队是原生行为。

## 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/ping` | 存活 + 当前 root agent 数 |
| GET | `/agents` | 列出 live root agent（id + status） |
| POST | `/say` | `{"text": "...", "sessionId": "..."}`，排队一条用户消息 |

`sessionId` 省略时选最近注册的 root agent。

## 配置

`~/.dsh/qq-bridge/config.json`（插件只读，缺省端口 8770、无 token）：

```json
{
  "port": 8770,
  "token": "换成一串随机值"
}
```

配了 `token` 时，所有请求必须带 `X-Bridge-Token` 头。

## 安装（尚未执行）

```
plugin_manager install_bundle
  target: <本仓库绝对路径>/dsh-plugin-qq-bridge
```

装完后用 `GET /ping` 验证；卸载用 `plugin_manager remove_bundle`，或用 patch 里的
`disabled: true` 关掉。

## 风险

- 插件跑在宿主进程里，**写崩会连带桌面**。目前只有 http server 与两个只读查询，风险面很小。
- 端口只绑 127.0.0.1；token 是可选的第二道门。
- `followup` 是排到下一轮，不会打断正在跑的回合。

## 还没做

- 审批（`session/request_permission` 在进程内是另一套事件，需要单独接）。
- 桌面端按钮/大眼睛交接（属于客户端插件，不是这个包）。
