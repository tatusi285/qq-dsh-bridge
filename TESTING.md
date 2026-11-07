# 测试说明（给帮忙测试的人）

这是一个**只读**的 QQ ↔ DeepSeek Harness 桥：手机上用 QQ 私聊，看桌面 DSH 当前会话跑到哪、
跑完收到通知。它不写会话、不连 DSH 进程，最坏情况是"看不到"，不会改坏东西。

替人测试时，请先确认你愿意承担的部分：**A 部分只装 AstrBot 插件，安全**；
**B 部分是实验性的 DSH 宿主插件，会跑在桌面进程里**，可以跳过。

## 环境要求

- Windows（只在这上面测过）
- DeepSeek Harness 桌面版已安装并至少跑过一个会话
- AstrBot 4.x，且配好了 QQ 适配（aiocqhttp / NapCat / SnowLuma 都行）
- Python 3.10+；Node.js（DSH 自带的那个就够）

## A. AstrBot 插件（推荐先测）

1. 把 `astrbot_plugin_dsh_status/` 整个目录放进 `<AstrBot>/data/plugins/`。
2. 在 `<AstrBot>/data/config/astrbot_plugin_dsh_status_config.json` 写配置：

   ```json
   {
     "allowed_uins": ["你的QQ号"],
     "private_only": true,
     "poll_seconds": 3,
     "compact": true
   }
   ```

3. 重载 AstrBot，私聊 bot 依次发：

   | 步骤 | 期望 |
   |---|---|
   | `/status` | 两行以内的进度卡片（跑着：轮次/步骤/当前工具/用时；空闲：上次完成/耗时） |
   | `/watch` | 回复"已开启完成推送" |
   | 随便发一句话触发一轮工作 | 这一轮结束时收到 `✅ 跑完了 · …` 推送 |
   | `/unwatch` | 回复已关闭 |

4. 顺手试几个边界，有异常就记下来：
   - 桌面没有任何会话时发 `/status`
   - 非白名单 QQ 私聊（应该毫无反应）
   - 群聊里发（`private_only: true` 时应该没反应）
   - 会话跑到一半时发 `/status`，看"当前工具"是否准确

## B. 宿主插件（可选，实验性）

`dsh-plugin-qq-bridge/` 会在 DSH 宿主进程里开一个只监听 `127.0.0.1:8770` 的小接口，
用来把一句话**排队**进当前活着的 agent。它没有经过完整测试，**装之前请确认你能接受桌面进程
可能被插件影响**。

1. 在 `~/.dsh/qq-bridge/config.json` 写：

   ```json
   { "port": 8770, "token": "随机字符串" }
   ```

2. 用 DSH 的插件管理安装这个目录（`plugin_manager` 的 `install_bundle`，target 指向
   `dsh-plugin-qq-bridge` 的绝对路径），或参考 [dsh-plugin-qq-bridge/README.md](dsh-plugin-qq-bridge/README.md)。
3. 验证只读接口：

   ```bash
   curl -H "X-Bridge-Token: 随机字符串" http://127.0.0.1:8770/ping
   curl -H "X-Bridge-Token: 随机字符串" http://127.0.0.1:8770/agents
   ```

4. 再试写接口（会给当前会话排一句话，下一轮生效）：

   ```bash
   curl -X POST http://127.0.0.1:8770/say \
     -H "Content-Type: application/json" \
     -H "X-Bridge-Token: 随机字符串" \
     -d '{"text":"请用一句话总结当前进度"}'
   ```

5. 卸载：`plugin_manager remove_bundle`，或在 profile 的 patch 里 `disabled: true`。

## 反馈时请带上

- 系统版本、DSH 版本、AstrBot 版本
- 复现步骤和实际输出（截图最好）
- AstrBot 日志里 `[dsh-status]` 开头的行
- 如果 `/status` 报"读不到会话状态"，附上 `~/.dsh/sessions/` 下会话文件的路径和大小

## 请不要提交

- 你的 QQ 号、AstrBot 配置、`~/.dsh` 下的任何文件
- OneBot / SnowLuma / NapCat 的 token
- 会话日志原文（里面有你的对话内容）

## 已知限制

- 只能看，不能插话、不能审批。
- 会话审批策略是 `ask` 时，人不在会卡住。
- 只解析 DSH 当前的会话日志格式（v4 / zstd 分帧）；DSH 升级后可能需要改 `session_status.cjs`。
