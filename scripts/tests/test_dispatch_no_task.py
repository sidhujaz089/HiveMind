#!/usr/bin/env python3
"""dispatch_loop will not start a chunk whose brief names no task. Fixture briefs only."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import dispatch_loop as DL  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "workspace" / "briefs").mkdir(parents=True)
        (repo / "workspace" / "briefs" / "brief-a.txt").write_text("ONE file: x.py. no task line\n")
        (repo / "workspace" / "briefs" / "brief-b.txt").write_text("TASK: t123\n\nONE file: y.py\n")
        real = DL.REPO
        DL.REPO = repo
        try:
            chunks = [{"chunk": "pa-x", "brief": "brief-a.txt", "files": "x.py", "target": "ta.py"},
                      {"chunk": "pb-y", "brief": "brief-b.txt", "files": "y.py", "target": "tb.py"}]
            out = DL.classify(chunks, [], lambda target: (True, "red"), history=[], objections=[])
        finally:
            DL.REPO = real
    skipped = dict(out[2])
    report("a brief with no TASK line is refused, and says why",
           skipped.get("pa-x", "").startswith("NO TASK"), True)
    report("a brief with a TASK line is not refused for it",
           skipped.get("pb-y", "").startswith("NO TASK"), False)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
