#!/usr/bin/env python3
"""--ratchet never drops a suite that still fails inside coder.py's sandbox (2026-09-30).

coder.py refuses every build when a suite fails in its sandbox and is declared nowhere. The ratchet
only sees main: it dropped test_notify.py (passes on main; in the sandbox py_vapid is missing,
because .venv is left out), and builds refused to start until it was declared in
sandbox-baseline.json. The runner is a stub here; the real one builds coder.sandbox().
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import run_tests as rt  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))


asked = []


def stub(names):
    asked.append(list(names))
    return [(n, "ModuleNotFoundError: No module named 'py_vapid'") for n in names if n == "test_notify.py"]


with tempfile.TemporaryDirectory() as tmp:
    b = Path(tmp) / "sandbox-baseline.json"
    b.write_text(json.dumps({"cannot_run_in_sandbox": {"test_workspace_mcp.py": "needs .venv"}}))
    keep, why = rt.ratchet_keep({"test_notify.py", "test_fine.py", "test_workspace_mcp.py"}, stub, b)
    report("a suite that fails in the sandbox is KEPT in the ceiling", keep, {"test_notify.py"})
    report("  one that passes there is dropped as before", "test_fine.py" in keep, False)
    report("  a suite already declared as cannot-run is not even run", asked[-1], ["test_fine.py", "test_notify.py"])
    report("  and the reason names what failed", "py_vapid" in why[0][1], True)
    keep, _ = rt.ratchet_keep(set(), stub, b)
    report("nothing to drop: nothing run, nothing kept", (keep, asked[-1]), (set(), []))
src = (ROOT / "scripts" / "run_tests.py").read_text()
report("--ratchet uses it before it writes the ceiling", "keep, why = ratchet_keep(allowed - failed)" in src
       and '"allowed_to_fail": sorted(failed | keep)' in src, True)
report("the real test_notify.py is declared as cannot-run in the sandbox",
       "test_notify.py" in json.loads((ROOT / "workspace" / "sandbox-baseline.json").read_text())["cannot_run_in_sandbox"],
       True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
