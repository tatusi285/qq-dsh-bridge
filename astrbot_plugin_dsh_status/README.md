# DSH 状态看板（AstrBot 插件）

在 QQ 里**只读**查看本机 DeepSeek Harness 当前会话的进度，跑完主动通知。

只读的意思是：不连 DSH 进程、不抢会话写锁、不产生任何写入。数据全部来自
`~/.dsh/sessions/<工作区>/<会话>/session.v4.jsonl.zstd` 这份日志。

## 命令

| 命令 | 作用 |
|---|---|
| `/status` 或 `/状态` | 看当前会话跑到哪一步、卡在哪个工具、已用多久 |
| `/watch` | 在当前私聊开启完成推送 |
| `/unwatch` | 关闭完成推送 |

## 卡片长这样

```
▶️ 正在跑 · 清理归档会话
第 13 轮 · 第 25 步 · pwsh：Sync cjs into plugin and re-test card
已用 2 分 45 秒 · 22:27 开始
你最后说：没错，先做只读版就好
```

跑完推送：

```
✅ 跑完了 · 清理归档会话
用时 4 分 12 秒
已清理完成。归档列表原有 24 个 ID……
📄 MANIFEST.md
```

## 配置

| 项 | 默认 | 说明 |
|---|---|---|
| `session_id` | 空 | 固定监听的会话；留空自动跟随最近更新的会话 |
| `allowed_uins` | `[]` | 允许使用的 QQ 号；**留空表示不限制**，建议填上 |
| `private_only` | `true` | 只响应私聊 |
| `watch_enabled` | `true` | 是否允许 `/watch` |
| `poll_seconds` | `3` | 轮询日志的间隔 |
| `node_path` | 空 | Node 可执行文件；留空用 DSH 安装目录里的那个 |

## 依赖

- Node：默认用 DSH 安装目录里的 `node.exe`（从 `%LOCALAPPDATA%` 推断），找不到则用 PATH 里的 `node`
- 无 Python 第三方依赖（只用标准库）

## 限制

- 只能看，不能插话、不能审批。
- 如果会话的审批策略是 `ask`，人不在时任务会卡住；出门前建议切到 `never`。
- 会话日志格式（v4、zstd 分帧）随 DSH 升级可能变化，届时 `session_status.cjs` 要跟着改。
