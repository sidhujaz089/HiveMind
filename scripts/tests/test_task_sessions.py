#!/usr/bin/env python3
"""Oracle for t386: task-keyed session logs any model can resume from.

Jas, 2026-09-24: "linked to a task and any model that works on the task has the full history of
sessions. This way if overnight a lot of Q4's work is bad and we switch to Q16, it doesn't start
from scratch." Every case runs in a temp root; nothing touches the live workspace.

CONTRACT (scripts/task_sessions.py):
  start(task, model, purpose, root=None) -> Path of a NEW .jsonl under <root>/<task>/ (root
      defaults to workspace/task-sessions); its first row is {"kind": "start", "task", "model",
      "purpose", "at"}. The directory is keyed by TASK; the model is a field, never a folder.
  append(path, kind, **fields) -> None. Appends ONE json row {"kind": kind, "at": ..., **fields}.
      Content is stored WHOLE -- never sliced.
  history(task, root=None) -> list of sessions, oldest first, each
      {"path": str, "model": str, "purpose": str, "started": str, "rows": [every row]}.
      A task with no sessions -> [].
  digest(task, max_chars=40000, root=None) -> str for the NEXT model: for each session, oldest
      first, a header line naming its model and purpose, then its "answer" rows and its last
      "reasoning" row. If the text exceeds max_chars it keeps the NEWEST part and says so with
      the word "TRUNCATED" and how many characters were dropped. No sessions -> "".
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import task_sessions as TS  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def check(name, fn, want):
    try:
        got = fn()
    except Exception as e:                                          # noqa: BLE001
        got = f"{type(e).__name__}: {e}"
    report(name, got, want)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        check("no sessions -> empty history", lambda: TS.history("t9", root=root), [])
        check("no sessions -> empty digest", lambda: TS.digest("t9", root=root), "")

        a = b = None
        try:
            a = TS.start("t9", "qwen3.8:27b-mtp-q4_K_M", "scope t9", root=root)
            TS.append(a, "tool_result", tool="read_file", content="X" * 100_000)
            TS.append(a, "reasoning", text="Q4 thinks p05 maps to t272")
            TS.append(a, "answer", text="Q4 ANSWER: p05 -> t272")
            time.sleep(1.1)
            b = TS.start("t9", "qwen3.8:27b-mtp-bf16", "re-scope t9", root=root)
            TS.append(b, "answer", text="Q16 ANSWER: confirmed")
        except Exception as e:                                      # noqa: BLE001
            print(f"  (start/append raised {type(e).__name__}: {e})")

        check("sessions live under <root>/<task>/, not per model",
              lambda: sorted(p.parent.name for p in root.rglob("*.jsonl")), ["t9", "t9"])
        check("each start() makes a new file", lambda: a is not None and b is not None and a != b,
              True)
        check("the first row is the start row with the model as a FIELD",
              lambda: {k: json.loads(Path(a).read_text().splitlines()[0])[k]
                       for k in ("kind", "task", "model")},
              {"kind": "start", "task": "t9", "model": "qwen3.8:27b-mtp-q4_K_M"})
        check("content is stored whole (100,000 chars)",
              lambda: len(json.loads(Path(a).read_text().splitlines()[1])["content"]), 100_000)
        check("history lists both models' sessions, oldest first",
              lambda: [s["model"] for s in TS.history("t9", root=root)],
              ["qwen3.8:27b-mtp-q4_K_M", "qwen3.8:27b-mtp-bf16"])
        check("history carries every row",
              lambda: [r["kind"] for r in TS.history("t9", root=root)[0]["rows"]],
              ["start", "tool_result", "reasoning", "answer"])
        check("another task's history is separate", lambda: TS.history("t10", root=root), [])

        d = ""
        try:
            d = TS.digest("t9", root=root)
        except Exception as e:                                      # noqa: BLE001
            print(f"  (digest raised {type(e).__name__}: {e})")
        report("the digest names each session's model",
               ("qwen3.8:27b-mtp-q4_K_M" in d, "qwen3.8:27b-mtp-bf16" in d), (True, True))
        report("  and carries both answers", ("Q4 ANSWER" in d, "Q16 ANSWER" in d), (True, True))
        report("  and the last reasoning", "p05 maps to t272" in d, True)
        report("  oldest session first", 0 <= d.find("Q4 ANSWER") < d.find("Q16 ANSWER"), True)
        check("a capped digest keeps the NEWEST part and says it cut",
              lambda: (lambda s: ("Q16 ANSWER" in s, "TRUNCATED" in s, len(s) <= 300))(
                  TS.digest("t9", max_chars=200, root=root)), (True, True, True))

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
