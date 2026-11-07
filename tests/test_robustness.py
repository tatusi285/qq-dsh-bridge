"""Robustness checks for the log reader: torn frames, empty files, big sessions.

Run: python tests/test_robustness.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "astrbot_plugin_dsh_status"))

from dsh_status import dsh_home, node_path, read_status, STATUS_JS  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f" {detail}"))
    if not ok:
        failures.append(name)


def run_file(path: Path, timeout: float = 60) -> dict:
    proc = subprocess.run(
        [node_path(), str(STATUS_JS), "--file", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return {"code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def biggest_session() -> Path | None:
    root = dsh_home() / "sessions"
    best: tuple[int, Path] | None = None
    for path in root.glob("*/*/session.v4.jsonl.zstd"):
        size = path.stat().st_size
        if best is None or size > best[0]:
            best = (size, path)
    return best[1] if best else None


def main() -> int:
    source = biggest_session()
    check("找到会话日志", source is not None, str(source))
    if not source:
        return 1
    print(f"  source: {source} ({source.stat().st_size / 1024 / 1024:.1f} MB)")

    t0 = time.time()
    full = run_file(source)
    elapsed = time.time() - t0
    check("完整文件可解析", full["code"] == 0, full["stderr"][-200:])
    check("常用会话读取 < 2s", elapsed < 2.0, f"{elapsed:.2f}s")
    print(f"  read time: {elapsed * 1000:.0f} ms")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        torn = tmpdir / "torn.zstd"
        data = source.read_bytes()
        torn.write_bytes(data[:-200])  # simulate a half-written tail frame
        result = run_file(torn)
        check("截断的尾帧不崩溃", result["code"] == 0, result["stderr"][-200:])
        if result["code"] == 0:
            try:
                payload = json.loads(result["stdout"])
                check("截断后仍能给出状态", bool(payload.get("ok")), result["stdout"][:200])
            except json.JSONDecodeError:
                check("截断后输出仍是 JSON", False, result["stdout"][:200])

        empty = tmpdir / "empty.zstd"
        empty.write_bytes(b"")
        result = run_file(empty)
        check("空文件不崩溃", result["code"] == 0, result["stderr"][-200:])

        garbage = tmpdir / "garbage.zstd"
        garbage.write_bytes(b"\x28\xb5\x2f\xfd" + os.urandom(64))
        result = run_file(garbage)
        check("坏帧不崩溃", result["code"] == 0, result["stderr"][-200:])

    missing = read_status("session-does-not-exist")
    check("不存在的会话返回 ok=False", not missing.ok)

    print()
    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("all robustness checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
