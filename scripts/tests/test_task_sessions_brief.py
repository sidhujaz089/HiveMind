#!/usr/bin/env python3
"""Oracle for t386 part 2 (p49): a session records WHICH BRIEF and WHICH ROLE, and the digest says so.

One task has several briefs. Jas, 2026-09-24: when a model fails on a brief, its reasoning belongs
to that brief, inside the task's history. Temp root only.

CONTRACT (additions to scripts/task_sessions.py; everything in test_task_sessions.py still holds):
  start(task, model, purpose, root=None, brief=None, role=None) -- the start row gains
      "brief" and "role" (None when not given).
  history() sessions gain "brief" and "role" (from the start row).
  digest() header line becomes
      "=== session <started> by <model> [<role>] on <brief>: <purpose> ==="
      with " [<role>]" left out when role is None and " on <brief>" left out when brief is None.
"""
import json
import sys
import tempfile
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
        p = None
        try:
            p = TS.start("t9", "gemma4:26b", "build", root=root, brief="brief-p48-x.txt",
                         role="builder")
            TS.append(p, "answer", text="BUILT")
        except Exception as e:                                      # noqa: BLE001
            print(f"  (start raised {type(e).__name__}: {e})")
        check("the start row records the brief and the role",
              lambda: {k: json.loads(Path(p).read_text().splitlines()[0]).get(k)
                       for k in ("brief", "role")},
              {"brief": "brief-p48-x.txt", "role": "builder"})
        check("history exposes them",
              lambda: {k: TS.history("t9", root=root)[0].get(k) for k in ("brief", "role")},
              {"brief": "brief-p48-x.txt", "role": "builder"})
        check("the digest header names model, role and brief",
              lambda: TS.digest("t9", root=root).splitlines()[0].endswith(
                  "by gemma4:26b [builder] on brief-p48-x.txt: build ==="), True)
        try:
            TS.start("t10", "q4", "scope", root=root)
        except Exception as e:                                      # noqa: BLE001
            print(f"  (old-style start raised {type(e).__name__}: {e})")
        check("an old-style start still works, with brief and role None",
              lambda: {k: TS.history("t10", root=root)[0].get(k) for k in ("brief", "role")},
              {"brief": None, "role": None})
        check("  and its header has no role or brief",
              lambda: TS.digest("t10", root=root).splitlines()[0].endswith("by q4: scope ==="), True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
