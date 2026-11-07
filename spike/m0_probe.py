"""M0 probe: can an out-of-process ACP client see, resume and prompt DSH sessions?

Usage:
  python m0_probe.py list
  python m0_probe.py prompt  --session <id> --cwd <dir> [--text "..."] [--hold <sec>]
  python m0_probe.py hold    --session <id> --cwd <dir> --seconds N
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from acp_client import AcpClient, summarize_update  # noqa: E402

HERE = Path(__file__).parent
LOG = HERE / "m0-stderr.log"


def show(update: dict) -> None:
    print(f"  <- {summarize_update(update)}")


async def cmd_list(args: argparse.Namespace) -> int:
    client = AcpClient(cwd=args.cwd or os.getcwd(), stderr_log=LOG)
    await client.start()
    try:
        init = await client.initialize()
        print(f"agent: {init.get('agentInfo')} protocol={init.get('protocolVersion')}")
        sessions = await client.list_sessions()
        print(f"sessions: {len(sessions)}")
        for s in sessions[: args.limit]:
            print(
                json.dumps(
                    {
                        k: s.get(k)
                        for k in ("sessionId", "title", "cwd", "updatedAt", "parentSession")
                        if k in s
                    },
                    ensure_ascii=False,
                )
            )
        if sessions:
            print("\nfirst session keys:", sorted(sessions[0].keys()))
    finally:
        await client.close()
    return 0


async def cmd_prompt(args: argparse.Namespace) -> int:
    client = AcpClient(cwd=args.cwd, stderr_log=LOG)
    client.on_update = show
    await client.start()
    try:
        await client.initialize()
        t0 = time.time()
        print(f"resume {args.session} (cwd={args.cwd}) ...")
        resumed = await client.resume(args.session, args.cwd)
        print(f"resumed in {time.time() - t0:.1f}s: {json.dumps(resumed, ensure_ascii=False)[:400]}")
        if args.hold:
            print(f"holding open for {args.hold}s ...")
            await asyncio.sleep(args.hold)
        if args.text:
            t1 = time.time()
            print("prompt ...")
            result = await client.prompt(args.session, args.text, timeout=args.timeout)
            print(f"prompt done in {time.time() - t1:.1f}s: {json.dumps(result, ensure_ascii=False)}")
            print(f"updates received: {len(client.notes)}")
            print(f"agent requests: {len(client.requests)}")
        await client.close_session(args.session)
    finally:
        await client.close()
    return 0


async def cmd_hold(args: argparse.Namespace) -> int:
    client = AcpClient(cwd=args.cwd, stderr_log=LOG)
    await client.start()
    try:
        await client.initialize()
        await client.resume(args.session, args.cwd)
        print(f"HELD {args.session} (pid={os.getpid()})", flush=True)
        await asyncio.sleep(args.seconds)
        await client.close_session(args.session)
        print("released", flush=True)
    finally:
        await client.close()
    return 0


async def cmd_scan(args: argparse.Namespace) -> int:
    """Try to resume every listed session, reporting which ones are free."""
    client = AcpClient(cwd=args.cwd or os.getcwd(), stderr_log=LOG)
    await client.start()
    try:
        await client.initialize()
        sessions = await client.list_sessions()
        for s in sessions:
            sid, cwd = s["sessionId"], s["cwd"]
            try:
                await client.resume(sid, cwd, timeout=120)
                print(f"FREE      {sid}")
                try:
                    await client.close_session(sid)
                except Exception as exc:  # noqa: BLE001
                    print(f"          close failed: {exc}")
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                short = msg.split('"details":')[-1].strip('"}') if '"details"' in msg else msg[:160]
                print(f"LOCKED    {sid}  -> {short}")
    finally:
        await client.close()
    return 0


async def cmd_new(args: argparse.Namespace) -> int:
    client = AcpClient(cwd=args.cwd, stderr_log=LOG)
    await client.start()
    try:
        await client.initialize()
        result = await client.request("session/new", {"cwd": args.cwd, "mcpServers": []}, timeout=120)
        sid = result.get("sessionId")
        print(f"NEW {sid}")
        print(json.dumps(result, ensure_ascii=False)[:600])
        if args.close:
            await client.close_session(sid)
            print("closed")
    finally:
        await client.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list")
    p_list.add_argument("--cwd", default=None)
    p_list.add_argument("--limit", type=int, default=10)
    p_list.set_defaults(func=cmd_list)

    p_prompt = sub.add_parser("prompt")
    p_prompt.add_argument("--session", required=True)
    p_prompt.add_argument("--cwd", required=True)
    p_prompt.add_argument("--text", default="")
    p_prompt.add_argument("--hold", type=int, default=0)
    p_prompt.add_argument("--timeout", type=float, default=300)
    p_prompt.set_defaults(func=cmd_prompt)

    p_hold = sub.add_parser("hold")
    p_hold.add_argument("--session", required=True)
    p_hold.add_argument("--cwd", required=True)
    p_hold.add_argument("--seconds", type=int, default=60)
    p_hold.set_defaults(func=cmd_hold)

    p_scan = sub.add_parser("scan")
    p_scan.add_argument("--cwd", default=None)
    p_scan.set_defaults(func=cmd_scan)

    p_new = sub.add_parser("new")
    p_new.add_argument("--cwd", required=True)
    p_new.add_argument("--close", action="store_true")
    p_new.set_defaults(func=cmd_new)

    args = parser.parse_args()
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
