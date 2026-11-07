# QQ ↔ DeepSeek Harness 桥（只读）

在手机 QQ 私聊里**只读**查看桌面 DeepSeek Harness 当前会话的进度，跑完自动通知。

- 设计定稿与踩坑记录：[DESIGN.md](DESIGN.md)
- 想帮忙测试：看 [TESTING.md](TESTING.md)
- 现状：只读版已可用（`/status`、`/watch`、完成推送）。插话/审批未实现，宿主插件原型在
  `dsh-plugin-qq-bridge/`，**未经充分测试，默认不装**。

## 原理

DSH 把会话写成 `~/.dsh/sessions/<工作区>/<会话>/session.v4.jsonl.zstd`：**多帧 zstd**，
每帧若干行 JSON。桥只读这个文件，从尾部向前解帧，直到覆盖最新的 `turn/start`，据此判断
"在跑 / 空闲 / 卡在哪个工具"。

不连 DSH 进程、不抢会话写锁、不产生任何写入。这也是它为什么能安全地和桌面同时开——
DSH 自己会用命名信号量锁住正在用的会话，任何外部进程 resume 都会被拒绝（见 DESIGN 的 M0 结论）。

## 目录

```
qq-dsh-bridge/
├─ DESIGN.md                          设计定稿、M0 结论、验收记录
├─ TESTING.md                         给别人测试用的说明
├─ tools/
│  ├─ session_status.cjs              只读解析会话日志（多帧 zstd → JSONL → 状态）
│  └─ status_cli.py                   命令行版状态卡片
├─ astrbot_plugin_dsh_status/         AstrBot 插件
│  ├─ main.py                         /status /状态 /watch /unwatch + 轮询推送
│  ├─ dsh_status.py                   状态归一化 + 卡片渲染
│  ├─ session_status.cjs              插件内自包含副本
│  ├─ _conf_schema.json               白名单 / 固定会话 / 轮询 / 紧凑模式
│  └─ metadata.yaml, README.md
├─ dsh-plugin-qq-bridge/              宿主插件原型（插话用，实验性）
├─ spike/                             M0 用的 ACP 探针
└─ tests/
   ├─ test_status.py                  日志解析与卡片文案
   ├─ test_push.py                    推送去重、补发、首次不补推
   └─ test_robustness.py              截断帧、空文件、坏数据、大文件耗时
```

## 部署

把 `astrbot_plugin_dsh_status/` 整个目录放进 AstrBot 的插件目录，例如
`<AstrBot>/data/plugins/astrbot_plugin_dsh_status`，重载 AstrBot 即可。

配置文件在 `<AstrBot>/data/config/astrbot_plugin_dsh_status_config.json`：

```json
{
  "allowed_uins": ["你的QQ号"],
  "private_only": true,
  "poll_seconds": 3,
  "compact": true
}
```

- `allowed_uins` 留空等于**不限制**，只建议在本机自用且 bot 只有你知道时这么做。
- `compact: true` 把 `/status` 压到 140 字以内。配合某些输出插件（例如把长文本转图片的
  `astrbot_plugin_outputpro`，阈值常是 150 字）时，紧凑模式能保证回复走纯文字。
- Node 默认用 DSH 安装目录里的 `node.exe`，也可以用配置项 `node_path` 或环境变量 `DSH_NODE` 指定。

改完 Python 或配置后需要重载 AstrBot。

## 测试

```bash
python tests/test_status.py        # 日志解析 + 卡片（需要本机已有 DSH 会话）
python tests/test_robustness.py    # 截断帧 / 空文件 / 坏数据 / 3.8MB 会话耗时
python tests/test_push.py          # 需要能 import astrbot 的 Python（例如 AstrBot 的 venv）
```

手工看卡片：

```bash
python tools/status_cli.py --session <session-id>
python tools/status_cli.py --session <session-id> --json
```

## 已知限制

- 只能看：不能插话、不能审批。
- 会话审批策略为 `ask` 时，人不在任务会卡住；出门前切换成 `never`。
- 日志格式（v4 / zstd 分帧）随 DSH 升级可能变，届时改 `session_status.cjs`；`tests/` 能第一时间发现。
- 轮询默认跟随"最近修改的会话"；多个工作区同时跑时，用配置里的 `session_id` 固定。
- 只测过 Windows + AstrBot 4.29 + aiocqhttp；其它平台未验证。
