#!/usr/bin/env python3
"""Reply into a console thread, so Claude answers where the question was asked.

    python3 scripts/console_reply.py <thread-id> "text"
    python3 scripts/console_reply.py --list

WHY. Jas asked: "If I manually prompt you are you able to reply in the chat with Qwen and I
or do you message on the side?" Until now, on the side -- an escalation left the console,
reached a Claude session, and the answer came back in a different window. The thread recorded
the question and not the answer, so the one place that looks like the record of a decision was
missing half of it.

An escalation already carries the thread id it came from. This puts the answer back there.

APPEND-ONLY, same as every other write to a thread: two windows open at once cannot lose a
message, and a torn line renders as damage rather than taking the thread down.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
THREADS = REPO / "workspace" / "console" / "threads"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("thread", nargs="?")
    ap.add_argument("text", nargs="?")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    THREADS.mkdir(parents=True, exist_ok=True)
    if a.list or not a.thread:
        rows = sorted(THREADS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not rows:
            print("no threads yet")
            return 0
        for p in rows[:15]:
            n = sum(1 for l in p.read_text().splitlines() if l.strip())
            print(f"  {p.stem:<24} {n:>4} row(s)")
        return 0 if a.list else 2

    if not a.text:
        print("give the reply text", file=sys.stderr)
        return 2
    path = THREADS / f"{a.thread}.jsonl"
    if not path.is_file():
        print(f"no such thread: {a.thread}. --list shows them.", file=sys.stderr)
        return 1
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ"),
            "role": "claude", "text": a.text}) + "\n")
    print(f"replied in {a.thread}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
