#!/usr/bin/env python3
"""t286: run_tests keeps a failing suite's WHOLE output, and a known failure's goes to a file.

The last suite in the failure ceiling failed under sweep load and nobody could see why: a known
failure's output was discarded, and every suite's output was cut to its last 600 characters --
which held only the FAIL lines, never what led to them.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import run_tests as RT
    fails = []
    d = Path(tempfile.mkdtemp())
    suite = d / "test_noisy.py"
    suite.write_text("import sys\nprint('CAUSE: the thing that went wrong first')\n"
                     "print('x' * 5000)\nprint('FAIL: symptom')\nsys.exit(1)\n")
    rc, _secs, out = RT._once(suite)
    for label, ok in (("the suite failed", rc == 1),
                      ("the first line survives", "CAUSE: the thing that went wrong first" in out),
                      ("the last line survives", "FAIL: symptom" in out),
                      ("nothing was cut", len(out) > 5000)):
        print(f"  [{'ok' if ok else 'BAD'}]  {label}")
        if not ok:
            fails.append(label)
    src = (ROOT / "scripts" / "run_tests.py").read_text(encoding="utf-8")
    ok = 'kept = KNOWN_LOGS / f"{name}.log"' in src and "kept.write_text(out" in src
    print(f"  [{'ok' if ok else 'BAD'}]  a KNOWN failure's output is written to workspace/run-logs/known-failures/")
    if not ok:
        fails.append("known-failure log")
    print("test_run_tests_keeps_output:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
