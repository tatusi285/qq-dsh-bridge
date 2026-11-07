"""Minimal ACP v1 client used by the M0 probe (and later by the bridge).

Speaks newline-delimited JSON-RPC 2.0 over stdio to `dsh --profile acp`.
No third-party dependency: the ACP surface we need is small.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

def _default_dsh_cmd() -> Path:
    """dsh launcher from the DSH desktop install, else `dsh` on PATH."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = (
            Path(local)
            / "Programs"
            / "DeepSeek Harness"
            / "resources"
            / "runtime"
            / "cli"
            / "bin"
            / "dsh.cmd"
        )
        if candidate.is_file():
            return candidate
    return Path("dsh")


DSH_CMD = Path(os.environ.get("DSH_CMD") or _default_dsh_cmd())

# Variables the desktop shell injects that would confuse a freshly booted profile.
_STRIP_ENV = ("DSH_SESSION_ID", "DSH_PROFILE", "DSH_PROFILE_DIR", "DSH_WEB_URL", "DSH_SHELL")


class AcpError(RuntimeError):
    pass


class AcpClient:
    def __init__(self, cwd: str, profile: str = "acp", stderr_log: Path | None = None):
        self.cwd = cwd
        self.profile = profile
        self.stderr_log = stderr_log
        self.proc: subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self.notes: list[dict] = []          # session/update notifications
        self.requests: list[dict] = []       # requests the agent sent us
        self.on_update = None                # callable(update_dict)

    # ---------- lifecycle ----------

    async def start(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
        env.setdefault("DSH_HOME", str(Path.home() / ".dsh"))
        args = ["cmd.exe", "/c", str(DSH_CMD), "--profile", self.profile]
        self.proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=self.cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        log = open(self.stderr_log, "ab") if self.stderr_log else None
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    return
                if log:
                    log.write(line)
                    log.flush()
        finally:
            if log:
                log.close()

    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                print(f"[non-json stdout] {text[:300]}", file=sys.stderr)
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: dict) -> None:
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(AcpError(json.dumps(msg["error"], ensure_ascii=False)))
                else:
                    fut.set_result(msg.get("result"))
            return
        if msg.get("method") == "session/update":
            params = msg.get("params", {})
            update = params.get("update", {})
            self.notes.append(update)
            if self.on_update:
                try:
                    self.on_update(update)
                except Exception:  # noqa: BLE001 - probe must not die on rendering
                    pass
            return
        if "method" in msg and "id" in msg:
            self.requests.append(msg)
            asyncio.create_task(self._answer_request(msg))

    async def _answer_request(self, msg: dict) -> None:
        method = msg.get("method")
        if method == "session/request_permission":
            options = (msg.get("params") or {}).get("options") or []
            allow = next((o for o in options if o.get("kind", "").startswith("allow")), None)
            outcome = (
                {"outcome": "selected", "optionId": allow["optionId"]}
                if allow
                else {"outcome": "cancelled"}
            )
            await self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {"outcome": outcome}})
        else:
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "error": {"code": -32601, "message": f"client does not support {method}"},
                }
            )

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=8)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        for task in (self._reader_task, self._stderr_task):
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if self.proc:
            for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                try:
                    if pipe and not pipe.is_closing():
                        pipe.close()
                except Exception:  # noqa: BLE001 - best-effort teardown
                    pass

    # ---------- rpc ----------

    async def _send(self, obj: dict) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def request(self, method: str, params: dict, timeout: float = 60) -> dict:
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    # ---------- ACP calls ----------

    async def initialize(self) -> dict:
        return await self.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
                "clientInfo": {"name": "qq-dsh-bridge", "version": "0.1.0"},
            },
        )

    async def list_sessions(self, cwd: str | None = None) -> list[dict]:
        result = await self.request("session/list", {"cwd": cwd} if cwd else {})
        return result.get("sessions", [])

    async def resume(self, session_id: str, cwd: str, timeout: float = 120) -> dict:
        return await self.request(
            "session/resume",
            {"sessionId": session_id, "cwd": cwd, "mcpServers": []},
            timeout=timeout,
        )

    async def prompt(self, session_id: str, text: str, timeout: float = 300) -> dict:
        return await self.request(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
            timeout=timeout,
        )

    async def close_session(self, session_id: str) -> dict:
        return await self.request("session/close", {"sessionId": session_id}, timeout=60)


def summarize_update(update: dict) -> str:
    kind = update.get("sessionUpdate", "?")
    if kind in ("agent_message_chunk", "agent_thought_chunk"):
        block = update.get("content") or {}
        text = (block.get("text") or "").strip()
        return f"{kind}: {text[:200]}"
    if kind == "tool_call":
        return f"tool_call: {update.get('title') or update.get('toolCallId')}"
    if kind == "tool_call_update":
        return f"tool_call_update: {update.get('toolCallId')} -> {update.get('status')}"
    return f"{kind}: {json.dumps(update, ensure_ascii=False)[:160]}"


async def demo() -> None:
    client = AcpClient(cwd=os.getcwd(), stderr_log=Path("m0-stderr.log"))
    await client.start()
    try:
        init = await client.initialize()
        print(json.dumps(init, ensure_ascii=False, indent=2))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(demo())
