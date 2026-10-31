#!/usr/bin/env python3
"""Copy ollama's warnings and errors out of the systemd journal into a file the error scanner reads.

    python3 scripts/ollama_events.py        # append everything new since the last run

Jas, 2026-09-14: "Does ollama's log go to the error checker? Would this count as an error that
can be analyzed?" It did not, and it would. error_scan.py reads files under workspace/; ollama
logs to the journal, which no file-based scanner reads -- the same gap the NAS backup had until
2026-09-11. That night ollama logged "llama-server model predicted to exceed available memory,
evicting" twice, unloaded the models under two running jobs, and nothing noticed for 85 minutes.

Rows go to workspace/run-logs/ollama-events.jsonl, one per event, the WHOLE message kept, with a
`status` that is never "ok" -- so error_scan surfaces every one as an unknown rather than needing
a new signature for each kind. A journald cursor file makes each run pick up exactly where the
last stopped: nothing read twice, nothing skipped. Run from dispatch_loop's tick.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "workspace" / "run-logs" / "ollama-events.jsonl"
CURSOR = REPO / "workspace" / "run-logs" / ".ollama-journal.cursor"
# What counts. level=WARN/ERROR from ollama's Go side, plus the llama.cpp runner's fatal shapes.
EVENT = re.compile(r'level=(WARN|ERROR)|evicting|out of memory|\bOOM\b|panic|fatal|segfault|'
                   r'runner (terminated|exited)|exit status', re.I)


def classify(msg):
    if "evicting" in msg:
        return "evicted"
    if re.search(r"out of memory|\bOOM\b", msg, re.I):
        return "out-of-memory"
    if re.search(r"panic|fatal|segfault|runner (terminated|exited)|exit status", msg, re.I):
        return "runner-crash"
    return "error" if "level=ERROR" in msg else "warning"


def read_journal(cursor, unit="ollama", since="-2d"):
    """The unit's journal since the last read, as JSON lines; the cursor file is advanced.

    systemd 259 REFUSES --since WITH --cursor-file (Jas's update, 2026-10-02: "Please specify
    only one of --since=, --cursor=, --cursor-file="). Every run that already had a cursor kept
    working, so the live collectors looked fine; the FIRST run failed -- which is every fresh
    sandbox, and test_slot_events refusing coder dispatches (t650). A first run now reads by
    --since alone and saves the cursor journalctl prints with --show-cursor."""
    cursor = Path(cursor)
    base = ["journalctl", "-u", unit, "-o", "json", "--no-pager"]
    if cursor.exists():
        return subprocess.run(base + [f"--cursor-file={cursor}"], capture_output=True, text=True,
                              timeout=120)
    r = subprocess.run(base + ["--since", since, "--show-cursor"], capture_output=True, text=True,
                       timeout=120)
    if r.returncode == 0:
        lines = r.stdout.splitlines()
        if lines and lines[-1].startswith("-- cursor: "):
            cursor.parent.mkdir(parents=True, exist_ok=True)
            cursor.write_text(lines.pop()[len("-- cursor: "):], encoding="utf-8")
        r.stdout = "\n".join(lines) + ("\n" if lines else "")
    return r


def main():
    r = read_journal(CURSOR)
    if r.returncode != 0:
        # NOT READING IS NOT "NOTHING HAPPENED". Record the failure as an event of its own.
        rows = [{"status": "collector-failed", "message": (r.stderr or "").strip(), "unit": "ollama"}]
    else:
        rows = []
        for line in r.stdout.splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                e = {}
            msg = str(e.get("MESSAGE", ""))
            if msg and EVENT.search(msg):
                ts = int(e.get("__REALTIME_TIMESTAMP", "0")) / 1e6
                rows.append({"at_epoch": ts, "unit": "ollama", "status": classify(msg), "message": msg})
    if rows:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(rows)} ollama event(s) recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
