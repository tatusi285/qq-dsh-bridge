"""Self-tests for the read-only status reader.

Run with any Python 3.10+:
    python tests/test_status.py

Uses whatever sessions exist under ~/.dsh/sessions; skips checks that need a
finished (or a running) session when none is present.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "astrbot_plugin_dsh_status"))

from dsh_status import (  # noqa: E402
    dsh_home,
    find_latest_session,
    read_status,
    render_card,
    render_done,
)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def session_ids() -> list[str]:
    root = dsh_home() / "sessions"
    ids: list[str] = []
    if not root.is_dir():
        return ids
    for bucket in root.iterdir():
        if not bucket.is_dir():
            continue
        for session in bucket.iterdir():
            if (session / "session.v4.jsonl.zstd").is_file():
                ids.append(session.name)
    return ids


def main() -> int:
    print(f"DSH home: {dsh_home()}")
    latest = find_latest_session()
    check("find_latest_session 返回会话", bool(latest), str(latest))
    if not latest:
        return 1

    status = read_status(latest)
    check("最新会话可读", status.ok)
    check("sessionId 一致", status.session_id == latest)
    check("running 是布尔值", isinstance(status.running, bool))
    check("cwd 非空", bool(status.cwd), status.cwd)

    card = render_card(status)
    print("\n--- /status 卡片 ---")
    print(card)
    print("--- end ---\n")
    check("卡片包含会话名或 ID", (status.title or status.session_id) in card)
    check("卡片不以 <goal_round> 开头", "<goal_round>" not in card)
    check("卡片包含轮次或空闲提示", ("轮" in card) or ("空闲" in card) or ("完成" in card))

    compact = render_card(status, compact=True)
    print("--- 紧凑卡片 ---")
    print(compact)
    print("--- end ---\n")
    check("紧凑模式不超过 140 字", len(compact) <= 140, f"len={len(compact)}")
    check("紧凑模式仍在描述状态", ("轮" in compact) or ("完成" in compact) or ("空闲" in compact))

    finished = None
    for sid in session_ids():
        candidate = read_status(sid)
        if candidate.ok and not candidate.running and candidate.last_turn_end:
            finished = candidate
            break
    if finished:
        check("已完成会话有结束原因", bool(finished.last_turn_end))
        done = render_done(finished)
        print("--- 完成推送 ---")
        print(done)
        print("--- end ---\n")
        check("推送含跑完/结束字样", ("跑完" in done) or ("结束" in done) or ("中断" in done))
    else:
        print("  skip 没有已完成的会话来验证推送文案")

    running = read_status(latest)
    if running.running:
        check("进行中会话有当前步骤", running.step is not None)
        check("进行中会话有开始时间", running.turn_started_at is not None)
    else:
        print("  skip 当前没有进行中的会话")

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
