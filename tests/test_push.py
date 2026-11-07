"""Exercise the completion-push path without AstrBot or QQ.

Run with a Python that can import AstrBot (e.g. its venv):
    python tests/test_push.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from astrbot_plugin_dsh_status.dsh_status import Status  # noqa: E402
from astrbot_plugin_dsh_status.main import DshStatusPlugin  # noqa: E402

TARGET = "platform:FriendMessage:100000001"


class FakeContext:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = False

    async def send_message(self, target, chain) -> None:
        if self.fail:
            raise RuntimeError("Ban state forbit write req")
        text = "".join(getattr(component, "text", "") for component in chain.chain)
        self.sent.append((target, text))


def make_plugin(marker: int, running: bool = False, fail: bool = False) -> DshStatusPlugin:
    plugin = object.__new__(DshStatusPlugin)
    plugin.config = {"poll_seconds": 3}
    plugin._watch = {TARGET}
    plugin._last_end = {"session-x": 100}
    plugin._pending = []
    plugin._busy = False
    plugin.context = FakeContext()
    plugin.context.fail = fail
    plugin._kv = {}

    async def put_kv(key, value):
        plugin._kv[key] = value

    async def fake_read():
        return Status(
            ok=True,
            session_id="session-x",
            title="测试会话",
            running=running,
            turn=3,
            step=7,
            last_text="已清理完成。",
            last_turn_end={"turn": 3, "reason": {"kind": "completed"}, "at": marker},
            last_turn_duration_ms=120_000,
        )

    plugin.put_kv_data = put_kv  # type: ignore[method-assign]
    plugin._read = fake_read  # type: ignore[method-assign]
    return plugin


async def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f" {detail}"))
        if not ok:
            failures.append(name)

    # 1. A finished turn pushes once, with the right content.
    plugin = make_plugin(marker=200)
    await plugin._tick()
    check("第一轮结束推送一次", len(plugin.context.sent) == 1, str(plugin.context.sent))
    if plugin.context.sent:
        target, text = plugin.context.sent[0]
        check("推送目标正确", target == TARGET, target)
        check("推送含完成标记", "跑完了" in text, text)
        check("推送含摘要", "已清理完成" in text, text)
        check("推送含用时", "2 分钟" in text, text)

    await plugin._tick()
    check("同一结束标记不重复推送", len(plugin.context.sent) == 1)

    # 2. A new completion pushes again.
    plugin._last_end = {"session-x": 150}
    await plugin._tick()
    check("新的结束标记再次推送", len(plugin.context.sent) == 2)

    # 3. First sighting never announces an older completion.
    first_sight = make_plugin(marker=999)
    first_sight._last_end = {}
    await first_sight._tick()
    check("首次见到会话不补推历史", len(first_sight.context.sent) == 0)

    # 4. No subscribers: no read, no send.
    no_watch = make_plugin(marker=400)
    no_watch._watch = set()
    await no_watch._tick()
    check("没有订阅者时不读不推", len(no_watch.context.sent) == 0)

    # 5. Transport down: keep the notification for later.
    down = make_plugin(marker=500, fail=True)
    await down._tick()
    check("发送失败时不丢消息", len(down._pending) == 1, str(down._pending))
    check("发送失败也推进结束标记", down._last_end.get("session-x") == 500)
    check("待补发已落盘", bool(down._kv.get("pending_pushes")))

    # 6. Transport back: the next push also drains the backlog.
    down.context.fail = False
    down._last_end["session-x"] = 400
    await down._tick()
    check("恢复后先发当前再补发", len(down.context.sent) == 2, str(down.context.sent))
    check("补发后队列清空", down._pending == [], str(down._pending))

    # 7. /status mentions the backlog.
    down._pending = [{"at": 1, "text": "旧通知"}]
    text = await down._status_text()
    check("/status 提示未送达条数", "还有 1 条" in text, text)

    print()
    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("all push checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
