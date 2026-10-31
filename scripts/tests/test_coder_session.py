#!/usr/bin/env python3
"""Oracle for t386 part 3 (p50): every coder.py build writes a task session, reasoning included.

When a model fails a brief, the next scope needs to see WHY -- the reasoning of each attempt.
coder.py kept outcomes and edits but never the thinking. Temp root only; never the live ledgers.

CONTRACT (projects/local-inference/coder.py):
  ask() appends the reasoning of EVERY call it makes to the module-level list `ask.reasoning`
      (the "thinking" text, or "" when there is none), in call order. `ask.reasoning = []` is
      defined at module level next to `ask.last = {}`.
  write_session(task, model, brief, tries, reasoning, outcome, root=None) -> path or None:
      None when task is falsy (nothing to key it by). Otherwise
      task_sessions.start(task, model, "build " + brief, root=root, brief=brief, role="builder"),
      then ONE "attempt" row per try: {"n", "outcome", "detail"} copied from the try,
      then ONE "reasoning" row per non-empty entry of `reasoning`: {"text": <entry>},
      then an "answer" row: {"text": "outcome: " + outcome}. Returns the session path.
  brief_task(path) -> the tNNN on the brief file's `TASK: tNNN` line, or None (file missing or
      no such line). coder.py gets --brief-file without --task, so record()'s `task` is None
      for every scheduled build; the session must still land under the brief's task.
  record() calls write_session(task or brief_task(<--brief-file path>), model, <brief file
      name from --brief-file in sys.argv, else "(no brief file)">, tries, list(ask.reasoning),
      outcome), inside a try/except that reports a failure to stderr and never lets it stop the
      run.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]
import coder  # noqa: E402

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
    check("ask.reasoning exists at module level as a list",
          lambda: isinstance(getattr(coder.ask, "reasoning", None), list), True)
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text()
    report("ask() appends every call's reasoning", src.count("ask.reasoning.append(") >= 2, True)
    report("record() writes the session", "write_session(" in src[src.find("def record("):], True)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tries = [{"n": 1, "outcome": "REFUSED", "detail": "path refused: ''"},
                 {"n": 2, "outcome": "TESTS-FAILED", "detail": "NameError: time"}]
        p = None
        try:
            p = coder.write_session("t9", "gemma4:26b", "brief-p48.txt", tries,
                                    ["first thought", "", "second thought"], "NOT-ACCEPTED",
                                    root=root)
        except Exception as e:                                      # noqa: BLE001
            print(f"  (write_session raised {type(e).__name__}: {e})")
        rows = [json.loads(x) for x in Path(p).read_text().splitlines()] if p else []
        report("the session is under the TASK", Path(p).parent.name if p else None, "t9")
        report("its start row names the brief and the builder role",
               {k: rows[0].get(k) for k in ("brief", "role")} if rows else None,
               {"brief": "brief-p48.txt", "role": "builder"})
        report("one attempt row per try, in order",
               [(r["n"], r["outcome"]) for r in rows if r["kind"] == "attempt"],
               [(1, "REFUSED"), (2, "TESTS-FAILED")])
        report("every non-empty reasoning is kept",
               [r["text"] for r in rows if r["kind"] == "reasoning"],
               ["first thought", "second thought"])
        report("the outcome is the answer row",
               [r["text"] for r in rows if r["kind"] == "answer"], ["outcome: NOT-ACCEPTED"])
        check("no task -> no session", lambda: coder.write_session(
            "", "m", "b", [], [], "X", root=root), None)
        bf = root / "brief-x.txt"
        bf.write_text("TASK: t386\n\nONE file: ...\n")
        check("brief_task reads the brief's TASK line", lambda: coder.brief_task(str(bf)), "t386")
        check("  and is None without one", lambda: coder.brief_task(str(root / "nope.txt")), None)
    report("record() falls back to the brief's task",
           "brief_task(" in src[src.find("def record("):], True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
