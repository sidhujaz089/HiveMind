#!/usr/bin/env python3
"""Installs the chat-agent watcher as a systemd USER timer.

Replaces install-chat-agent-task.ps1 (Windows Task Scheduler) after the move to
Linux.

RUNS AS JAS, NOT ROOT -- and this is the load-bearing detail, not a preference.
A root/system service cannot reliably read cloud-synced paths; wifi-watchdog
already records that a SYSTEM task could not, and workspace/commands/ is exactly
such a path. As root it would find an empty queue every run and report success --
a watcher that silently sees nothing, whose empty log reads as "no manifests"
rather than "cannot see manifests". A systemd *user* unit runs as the logged-in
user with their environment, which is the equivalent of the Interactive/Limited
principal the Windows version used.

This WRITES unit files but does not enable them unless you pass --enable, because
enabling changes persistent system configuration. Jas runs that step.

    python3 scripts/install_chat_agent_timer.py            # write units, show next steps
    python3 scripts/install_chat_agent_timer.py --enable   # write, enable and start
"""

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE = "chat-agent.service"
TIMER = "chat-agent.timer"
DEFAULT_AT = "09:00"

SERVICE_TEMPLATE = """\
[Unit]
Description=Claude Chat manifest watcher (chat-agent, dry run by default)
Documentation=file://{repo}/docs/CHAT-BRIDGE.md

[Service]
Type=oneshot
WorkingDirectory={repo}
ExecStart={python} {repo}/scripts/chat_agent.py
# Matches the 15-minute ExecutionTimeLimit the Windows task carried.
TimeoutStartSec=900
"""

TIMER_TEMPLATE = """\
[Unit]
Description=Run the chat-agent watcher daily at {at}

[Timer]
OnCalendar=*-*-* {at}:00
# If the machine was asleep at the scheduled time, run once on wake rather than
# skipping the day entirely.
Persistent=true
Unit={service}

[Install]
WantedBy=timers.target
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default=DEFAULT_AT, help=f"HH:MM, default {DEFAULT_AT}")
    ap.add_argument("--enable", action="store_true",
                    help="Also enable and start the timer (changes persistent config).")
    args = ap.parse_args()

    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    (UNIT_DIR / SERVICE).write_text(
        SERVICE_TEMPLATE.format(repo=REPO_ROOT, python=sys.executable), encoding="utf-8"
    )
    (UNIT_DIR / TIMER).write_text(
        TIMER_TEMPLATE.format(at=args.at, service=SERVICE), encoding="utf-8"
    )

    print()
    print(f"  wrote {UNIT_DIR / SERVICE}")
    print(f"  wrote {UNIT_DIR / TIMER}")
    print(f"  schedule: daily at {args.at}, as {Path.home().name}, not root")
    print()

    if not args.enable:
        print("  NOT enabled. Enabling changes persistent system configuration, so it is")
        print("  a deliberate step. To turn it on:")
        print()
        print("    systemctl --user daemon-reload")
        print(f"    systemctl --user enable --now {TIMER}")
        print()
        print("  And so it runs when you are not logged in:")
        print()
        print("    loginctl enable-linger $USER")
        print()
        print("  The agent stays in DRY RUN regardless: enabling the timer schedules the")
        print("  watcher, it does not arm it. Arming is a separate human-only commit (D-21).")
        print()
        return 0

    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", TIMER]):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f"  FAILED: {' '.join(cmd)}")
            print("  " + (r.stderr or "").strip())
            return 1
        print(f"  ran: {' '.join(cmd)}")

    print()
    print("  Timer enabled. Still DRY RUN — scheduling is not arming (D-21).")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
