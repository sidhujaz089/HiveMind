#!/usr/bin/env python3
"""t619: 'Copy last' copies the most recent command and its output -- not the whole screen.

Jas: "I don't want it to copy everything on the terminal screen ... it should copy the last return."
feedback.last_output is pure: called on fixture pane text, no tmux.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
sys.path.insert(0, str(ROOT / "scripts"))
import feedback as f  # noqa: E402

FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILS.append(label)


P = "user@HiveMind:~/ai-workspace$ "
pane = "\n".join([P + "ls", "a.txt", "b.txt", P + "echo cost \\$5", "cost $5", P, P, "", ""])
ck("the latest command with output, trailing empty prompts skipped",
   f.last_output(pane), P + "echo cost \\$5\ncost $5")
ck("an earlier command is not included", "a.txt" in f.last_output(pane), False)
ck("a command still running (no prompt after it) is copied to the bottom",
   f.last_output(P + "ls\na\n" + P + "sleep 5\nworking"), P + "sleep 5\nworking")
ck("no command on screen copies nothing", f.last_output(P + "\n" + P + "\n"), "")
ck("an empty pane copies nothing", f.last_output(""), "")
ck("a root prompt (#) counts", f.last_output("root@box:/# id\nuid=0\nroot@box:/# "), "root@box:/# id\nuid=0")
print("test_terminal_last_output:", "FAILED" if FAILS else "ok")
sys.exit(1 if FAILS else 0)
