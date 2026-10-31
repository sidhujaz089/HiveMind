#!/usr/bin/env python3
"""An edit with no path is aimed at the brief's ONLY file; with several files it is still refused.

p48, 2026-09-24: two of five gemma4:26b attempts were refused as "path refused: ''" although the
brief allowed exactly one file.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    ops = [{"path": None, "old": "a", "new": "b"}, {"old": "c", "new": "d"},
           {"path": "scripts/x.py", "old": "e", "new": "f"}]
    report("two pathless ops are filled", coder.fill_paths(ops, ["scripts/x.py"]), 2)
    report("  with the one allowed file", [o["path"] for o in ops], ["scripts/x.py"] * 3)
    ops2 = [{"path": "", "old": "a", "new": "b"}]
    report("with two allowed files nothing is guessed", coder.fill_paths(ops2, ["a.py", "b.py"]), 0)
    report("  and the path stays empty (so it is refused)", ops2[0]["path"], "")
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text()
    report("the loop fills paths before applying",
           0 < src.find("filled = fill_paths(obj[\"ops\"], a.files)")
           < src.find("ok, detail = apply_ops(root, obj[\"ops\"])"), True)
    report("an UNPARSEABLE attempt keeps the model's raw reply (p48 attempt 3 kept nothing)",
           '"detail": err, "raw": raw})' in src, True)
    ops3 = [{"path": "coder.py", "old": "a", "new": "b"}]
    report("a bare file name matching the one allowed file resolves to it",
           (coder.fill_paths(ops3, ["projects/local-inference/coder.py"]), ops3[0]["path"]),
           (1, "projects/local-inference/coder.py"))
    ops4 = [{"path": "other.py", "old": "a", "new": "b"}]
    report("a different bare name is left alone (and refused later)",
           (coder.fill_paths(ops4, ["projects/local-inference/coder.py"]), ops4[0]["path"]),
           (0, "other.py"))
    # END TO END: the raw reply must reach the LEDGER, not only the loop's `tries`.
    import tempfile, json
    with tempfile.TemporaryDirectory() as tmp:
        real = coder.RUNS
        coder.RUNS = Path(tmp) / "runs.jsonl"
        try:
            coder.record(None, "m", "NOT-ACCEPTED", [{"n": 1, "outcome": "UNPARSEABLE",
                                                      "detail": "bad", "raw": "RAW REPLY"}],
                         None, None)
            row = json.loads(coder.RUNS.read_text().splitlines()[-1])
        finally:
            coder.RUNS = real
    report("an UNPARSEABLE attempt's raw reply reaches the ledger row",
           row["trail"][0].get("raw"), "RAW REPLY")
    # the ledger row names the brief's task when --task was not given (210 of 292 had none)
    with tempfile.TemporaryDirectory() as tmp:
        bf = Path(tmp) / "brief.txt"
        bf.write_text("TASK: t386\n\nONE file\n")
        real_argv, real_runs = sys.argv, coder.RUNS
        sys.argv, coder.RUNS = ["coder.py", "--brief-file", str(bf)], Path(tmp) / "runs.jsonl"
        try:
            coder.record(None, "m", "NOT-ACCEPTED", [], None, None)
            row2 = json.loads(coder.RUNS.read_text().splitlines()[-1])
        finally:
            sys.argv, coder.RUNS = real_argv, real_runs
    report("the ledger row takes the task from the brief's TASK line", row2["task"], "t386")
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
