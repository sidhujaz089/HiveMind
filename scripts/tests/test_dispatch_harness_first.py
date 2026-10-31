"""dispatch_loop: a HARNESS task's chunk builds before other ready work (Jas, 2026-10-19)."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]
import dispatch_loop as DL  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


tmp = Path(tempfile.mkdtemp())
(tmp / "workspace" / "tasks").mkdir(parents=True)
(tmp / "workspace" / "tasks" / "t2--harness-coder-py-crashed.md").write_text("x")
(tmp / "workspace" / "tasks" / "t1--add-a-pill.md").write_text("x")
saved = (DL.REPO, DL.task_of)
DL.REPO = tmp
DL.task_of = lambda b: {"b1.txt": "t1", "b2.txt": "t2"}.get(b)
try:
    report("a HARNESS task's brief is recognised", DL.is_harness_brief("b2.txt"), True)
    report("another task's is not", DL.is_harness_brief("b1.txt"), False)
    report("a brief with no task is not", DL.is_harness_brief("none.txt"), False)
    ready = [{"brief": "b1.txt", "chunk": "p1"}, {"brief": "b2.txt", "chunk": "p2"}]
    ready.sort(key=lambda c: not DL.is_harness_brief(c["brief"]))
    report("the harness chunk sorts first", [c["chunk"] for c in ready], ["p2", "p1"])
    src = (ROOT / "scripts" / "dispatch_loop.py").read_text()
    report("classify() applies that sort to ready work",
           'ready.sort(key=lambda c: not is_harness_brief(c["brief"]))' in src, True)
finally:
    DL.REPO, DL.task_of = saved
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
