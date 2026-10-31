#!/usr/bin/env python3
"""t401: a per-task page of model sessions -- what each was given, what it read, what it answered.

RED on main: scripts/task_sessions.py (which writes these logs) has no summarize() or render_html(),
GREEN when summarize() reads
<root>/<tid>/*.jsonl into one summary per session (root = the task-sessions folder, the module's own
convention) and render_html() shows them. The Hub wiring has its own oracle,
test_hub_sessions_route.py, so each chunk can be built and turned green alone.

Behaviour is checked on a fixture tree in a temp dir: no live state.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILS.append(label)


def fixture(root):
    d = root / "t999"
    d.mkdir(parents=True)
    rows = [
        {"kind": "start", "task": "t999", "model": "m-tag", "purpose": "manager: t999 <scoping>",
         "at": "2026-10-02T15:00:00Z", "brief": None, "role": "senior"},
        {"kind": "prompt", "at": "2026-10-02T15:00:00Z", "text": "P" * 1234},
        {"kind": "tool", "at": "2026-10-02T15:00:10Z", "round": "0", "tool": "read_file",
         "args": "{'path': 'scripts/a.py'}", "result": "x"},
        {"kind": "tool", "at": "2026-10-02T15:00:20Z", "round": "1", "tool": "read_file",
         "args": "{'path': 'docs/b.md', 'start': 40}", "result": "y"},
        {"kind": "tool", "at": "2026-10-02T15:00:30Z", "round": "1", "tool": "read_file",
         "args": "{'path': 'scripts/a.py', 'start': 200}", "result": "z"},
        {"kind": "tool", "at": "2026-10-02T15:00:40Z", "round": "2", "tool": "run_command",
         "args": "{'command': 'ls'}", "result": "exit: 0"},
        {"kind": "answer", "at": "2026-10-02T15:05:00Z", "text": "The answer.", "done_reason": "stop",
         "complete": True, "warning": None},
    ]
    lines = [json.dumps(r) for r in rows]
    lines.insert(3, "{not json")                                   # one malformed line
    (d / "20261002T150000Z-m-tag.jsonl").write_text("\n".join(lines) + "\n")
    (d / "20261002T160000Z-m-tag.jsonl").write_text(json.dumps(
        {"kind": "start", "task": "t999", "model": "m-tag", "purpose": "second",
         "at": "2026-10-02T16:00:00Z", "brief": None, "role": None}) + "\n")


def main():
    import task_sessions as ts
    missing = [f for f in ("summarize", "render_html") if not hasattr(ts, f)]
    if missing:
        print(f"  [BAD]  task_sessions has {missing}: the missing change")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="t401-"))
    try:
        fixture(tmp)
        s = ts.summarize("t999", root=tmp)
        ck("one summary per session file, oldest first", [x["purpose"] for x in s],
           ["manager: t999 <scoping>", "second"])
        a = s[0]
        ck("model, role and start time from the start row", (a["model"], a["role"], a["started"]),
           ("m-tag", "senior", "2026-10-02T15:00:00Z"))
        ck("what it was given: the prompt's size", a["prompt_chars"], 1234)
        ck("what it read: each read_file path once, in first-read order", a["reads"],
           ["scripts/a.py", "docs/b.md"])
        ck("every tool call is counted, reads or not", a["tool_calls"], 4)
        ck("what it answered", (a["answer"], a["done_reason"]), ("The answer.", "stop"))
        ck("a malformed line is counted, not dropped silently", a["unreadable"], 1)
        ck("a session with no answer says so", (s[1]["answer"], s[1]["done_reason"]), (None, None))
        ck("a task with no sessions is an empty list", ts.summarize("t000", root=tmp), [])

        h = ts.render_html("t999", s)
        ck("the page names the model and every read", all(x in h for x in ("m-tag", "scripts/a.py", "docs/b.md")),
           True)
        ck("the purpose is HTML-escaped", ("&lt;scoping&gt;" in h, "<scoping>" in h), (True, False))
        ck("an empty task renders a plain 'no sessions' line",
           "No model sessions" in ts.render_html("t000", []), True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("test_task_sessions_page:", "FAILED" if FAILS else "ok")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
