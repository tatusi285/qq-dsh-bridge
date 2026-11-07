"""Command-line status card, for checking the reader outside AstrBot.

    python tools/status_cli.py                # newest session
    python tools/status_cli.py --session <id>
    python tools/status_cli.py --session <id> --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "astrbot_plugin_dsh_status"))

from dsh_status import find_latest_session, read_status, render_card  # noqa: E402


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - not every console supports it
        pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    session_id = args.session or find_latest_session()
    if not session_id:
        print("no session found under ~/.dsh/sessions")
        return 2

    status = read_status(session_id)
    if args.json:
        print(json.dumps(status.raw, ensure_ascii=False, indent=2))
    else:
        print(render_card(status, compact=args.compact))
    return 0 if status.ok else 1


if __name__ == "__main__":
    sys.exit(main())
