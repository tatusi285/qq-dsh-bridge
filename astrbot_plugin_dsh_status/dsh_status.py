"""Read-only DSH session status for the AstrBot plugin.

The heavy lifting lives in `session_status.cjs` (Node, zstd + JSONL); this
module shells out to it, normalises the result and renders the QQ card.
No DSH process is touched: only the session log is read.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATUS_JS = HERE / "session_status.cjs"


def _default_node() -> str:
    """Node shipped with the DSH desktop app, else `node` from PATH."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = (
            Path(local)
            / "Programs"
            / "DeepSeek Harness"
            / "resources"
            / "runtime"
            / "primary-runtime"
            / "dependencies"
            / "node"
            / "bin"
            / "node.exe"
        )
        if candidate.is_file():
            return str(candidate)
    return "node"


DEFAULT_NODE = _default_node()


def dsh_home() -> Path:
    return Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))


def node_path(configured: str | None = None) -> str:
    return configured or os.environ.get("DSH_NODE") or DEFAULT_NODE


def find_latest_session() -> str | None:
    """Newest session log under ~/.dsh/sessions, by file mtime."""
    root = dsh_home() / "sessions"
    if not root.is_dir():
        return None
    best: tuple[float, str] | None = None
    for bucket in root.iterdir():
        if not bucket.is_dir():
            continue
        for session in bucket.iterdir():
            log = session / "session.v4.jsonl.zstd"
            if not log.is_file():
                continue
            try:
                mtime = log.stat().st_mtime
            except OSError:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, session.name)
    return best[1] if best else None


def find_session_file(session_id: str) -> Path | None:
    root = dsh_home() / "sessions"
    if not root.is_dir():
        return None
    for bucket in root.iterdir():
        candidate = bucket / session_id / "session.v4.jsonl.zstd"
        if candidate.is_file():
            return candidate
    return None


@dataclass
class ToolCall:
    name: str
    hint: str
    call_id: str
    at: int


@dataclass
class Status:
    ok: bool
    session_id: str = ""
    cwd: str = ""
    title: str | None = None
    running: bool = False
    turn: int | None = None
    step: int | None = None
    turn_started_at: int | None = None
    elapsed_ms: int | None = None
    current_tool: ToolCall | None = None
    pending_tools: list[ToolCall] = field(default_factory=list)
    last_text: str = ""
    last_user_message: str = ""
    last_turn_end: dict | None = None
    last_turn_duration_ms: int | None = None
    approval_policy: str | None = None
    permission_preset: str | None = None
    deliverables: list[dict] = field(default_factory=list)
    event_seq: int | None = None
    updated_at: int | None = None
    raw: dict = field(default_factory=dict)

    @property
    def end_marker(self) -> int:
        """Timestamp of the last finished turn (0 when the log has none)."""
        end = self.last_turn_end or {}
        return int(end.get("at") or 0)


def read_status(session_id: str, node: str | None = None, timeout: float = 25) -> Status:
    try:
        proc = subprocess.run(
            [node_path(node), str(STATUS_JS), "--session", session_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(HERE),
        )
    except Exception as exc:  # noqa: BLE001 - a missing node or a timeout is a status, not a crash
        return Status(ok=False, session_id=session_id, raw={"error": str(exc)[:300]})
    if proc.returncode != 0 or not proc.stdout.strip():
        return Status(ok=False, session_id=session_id, raw={"stderr": (proc.stderr or "")[-800:]})
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return Status(ok=False, session_id=session_id, raw={"stdout": proc.stdout[-300:]})
    return Status(
        ok=bool(data.get("ok")),
        session_id=data.get("sessionId") or session_id,
        cwd=data.get("cwd") or "",
        title=data.get("title"),
        running=bool(data.get("running")),
        turn=data.get("turn"),
        step=data.get("step"),
        turn_started_at=data.get("turnStartedAt"),
        elapsed_ms=data.get("elapsedMs"),
        current_tool=_tool(data.get("currentTool")),
        pending_tools=[t for t in (_tool(x) for x in data.get("pendingTools") or []) if t],
        last_text=data.get("lastText") or "",
        last_user_message=data.get("lastUserMessage") or "",
        last_turn_end=data.get("lastTurnEnd"),
        last_turn_duration_ms=data.get("lastTurnDurationMs"),
        approval_policy=data.get("approvalPolicy"),
        permission_preset=data.get("permissionPreset"),
        deliverables=data.get("deliverables") or [],
        event_seq=data.get("eventSeq"),
        updated_at=data.get("updatedAt"),
        raw=data,
    )


def _tool(value: dict | None) -> ToolCall | None:
    if not value:
        return None
    return ToolCall(
        name=value.get("name") or "?",
        hint=(value.get("hint") or "").strip(),
        call_id=value.get("callId") or "",
        at=int(value.get("at") or 0),
    )


def _dur(ms: int | None) -> str:
    if not ms or ms < 0:
        return "—"
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {seconds} 秒" if seconds else f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def _clock(ms: int | None) -> str:
    import datetime as dt

    if not ms:
        return "—"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def _snippet(text: str, limit: int = 120) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("```", "|---")):
            return line if len(line) <= limit else line[:limit] + "…"
    return ""


def render_card(st: Status, compact: bool = False) -> str:
    """The `/status` reply: compact, readable in QQ, no Markdown tables.

    ``compact`` keeps the whole reply under ~140 characters so an output
    pipeline that turns long text into images (e.g. a 150-char t2i threshold)
    still delivers it as plain text.
    """
    if not st.ok:
        return (
            f"⚠️ 读不到会话状态（{st.session_id}）\n"
            "日志可能正在轮转；稍后再试，或检查会话是否已被归档。"
        )

    name = st.title or st.session_id

    if compact:
        short = name[:22]
        if st.running:
            tool = st.current_tool
            what = f"{tool.name}：{tool.hint[:18]}" if tool else "模型思考中"
            text = f"▶ {short} · 第{st.turn}轮第{st.step}步\n{what} · {_dur(st.elapsed_ms)}"
        else:
            end = st.last_turn_end or {}
            reason = (end.get("reason") or {}).get("kind") if isinstance(end, dict) else None
            label = {"completed": "✅ 完成", "interrupted": "⏹ 中断"}.get(reason or "", "⏸ 空闲")
            text = f"{label} · {short}"
            if st.last_turn_duration_ms:
                text += f" · {_dur(st.last_turn_duration_ms)}"
        return text[:140]

    lines: list[str] = []

    if st.running:
        lines.append(f"▶️ 正在跑 · {name}")
        tool = st.current_tool
        if tool:
            hint = f"：{tool.hint}" if tool.hint else ""
            lines.append(f"第 {st.turn} 轮 · 第 {st.step} 步 · {tool.name}{hint}")
        else:
            lines.append(f"第 {st.turn} 轮 · 第 {st.step} 步 · 模型思考中")
        lines.append(f"已用 {_dur(st.elapsed_ms)} · {_clock(st.turn_started_at)} 开始")
        if len(st.pending_tools) > 1:
            lines.append(f"并行工具 {len(st.pending_tools)} 个")
    else:
        end = st.last_turn_end or {}
        reason = (end.get("reason") or {}).get("kind") if isinstance(end, dict) else None
        label = {"completed": "✅ 上一轮已完成", "interrupted": "⏹ 上一轮被中断"}.get(reason or "", "⏸ 空闲")
        lines.append(f"{label} · {name}")
        if st.last_turn_duration_ms:
            lines.append(f"耗时 {_dur(st.last_turn_duration_ms)} · {_clock(end.get('at'))} 结束")

    if st.last_user_message:
        text = " ".join(st.last_user_message.split())
        lines.append(f"你最后说：{text[:80]}")

    if st.last_text:
        snippet = _snippet(st.last_text)
        if snippet:
            lines.append(f"最近输出：{snippet}")

    if st.deliverables:
        names = "、".join(
            Path(f.get("path", "")).name for f in st.deliverables[:3] if f.get("path")
        )
        if names:
            lines.append(f"产出：{names}")

    if st.approval_policy and st.approval_policy != "never":
        lines.append(f"⚠️ 该会话审批策略是 {st.approval_policy}，人不在时可能卡住")

    return "\n".join(lines)


def render_done(st: Status, compact: bool = False) -> str:
    """The push sent when a turn finishes."""
    end = st.last_turn_end or {}
    reason = (end.get("reason") or {}).get("kind") if isinstance(end, dict) else None
    name = st.title or st.session_id
    if reason == "completed":
        head = f"✅ 跑完了 · {name}"
    elif reason == "interrupted":
        head = f"⏹ 被中断了 · {name}"
    elif st.running:
        head = f"▶️ 又跑起来了 · {name}"
    else:
        head = f"🏁 这一轮结束了（{reason or '未知原因'}）· {name}"

    if compact:
        parts = [f"✅ 跑完了 · {name[:18]}" if reason == "completed" else head[:34]]
        if st.last_turn_duration_ms:
            parts[-1] += f" · {_dur(st.last_turn_duration_ms)}"
        snippet = _snippet(st.last_text, 70)
        if snippet:
            parts.append(snippet)
        if st.deliverables:
            names = "、".join(
                Path(f.get("path", "")).name for f in st.deliverables[:2] if f.get("path")
            )
            if names:
                parts.append(f"📄 {names}")
        return "\n".join(parts)[:140]

    lines = [head]
    if st.last_turn_duration_ms:
        lines.append(f"用时 {_dur(st.last_turn_duration_ms)}")

    snippet = _snippet(st.last_text, 220)
    if snippet:
        lines.append(snippet)

    if st.deliverables:
        for item in st.deliverables[:3]:
            path = item.get("path")
            if path:
                lines.append(f"📄 {Path(path).name}")

    # A queued follow-up may already be running: say so instead of looking stuck.
    if st.running:
        lines.append(f"（已经又开始了：第 {st.turn} 轮 · 第 {st.step} 步）")
    return "\n".join(lines)
