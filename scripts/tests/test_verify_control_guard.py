#!/usr/bin/env python3
"""Importing scripts/verify_control.py must not run the experiment.

The narrow, SATISFIABLE oracle for one file. `test_import_is_free.py` is the class ratchet and
covers five scripts; pointing a one-file brief at it asks for something a one-file change
cannot deliver, and a requirement that cannot be met honestly gets met dishonestly or not at
all. It was not met: four attempts, one of them 1387 seconds into the output ceiling.

The defect: verify_control.py has no `__main__` guard, so its module level IS the experiment.
Importing it loops over TRAPS calling Q16 AND Q4 per item -- measured at 74.4s and 47.9s on
one item -- and writes a results file. That makes it untestable, and makes it an Ollama
consumer that never takes the model lease.
"""
import ast
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "scripts" / "verify_control.py"
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


tree = ast.parse(TARGET.read_text(encoding="utf-8", errors="replace"))

print("\nThe module level defines things; it does not do things:")
doing = []
for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
                         ast.ClassDef, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Try)):
        continue
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        continue
    if isinstance(node, ast.Expr) and "sys.path" in ast.unparse(node):
        continue
    if isinstance(node, ast.If):
        t = node.test
        if "sys.path" in ast.unparse(t):
            continue
        if isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "__name__":
            continue
    doing.append(f"line {node.lineno}: {type(node).__name__}")
check("nothing executes at import time", doing, [])

print("\nThere is a main guard, and it calls a main():")
src = TARGET.read_text()
check("the file has a __main__ guard", '__name__ == "__main__"' in src
      or "__name__ == '__main__'" in src, True)
check("and a main() to call", bool([n for n in tree.body
                                    if isinstance(n, ast.FunctionDef) and n.name == "main"]), True)

print("\nThe work is still THERE -- this is a guard, not a deletion:")
body = "\n".join(ast.unparse(n) for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "main")
for token in ("TRAPS", "json.dump"):
    check(f"main() still contains {token}", token in body, True)

# THE EXPENSIVE PROOF, RUN ONLY WHEN THE CHEAP ONE PASSES. Importing this file is the very
# thing under test, so doing it while the defect is present starts a two-model run -- which is
# how this suite burned one on its first execution, the third accidental run in a day. If the
# AST says the module level still does work, we already have the answer and must not pay for
# it again.
if doing:
    print("\n(skipping the import test: the module level still does work, so importing it")
    print(" would start the model run this suite exists to prevent)")
else:
    print("\nAnd importing it is actually FAST, which is the point:")
    t0 = time.time()
    r = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.insert(0, %r); import verify_control"
                        % str(ROOT / "scripts")],
                       capture_output=True, text=True, timeout=30)
    took = time.time() - t0
    check("the import succeeds", r.returncode, 0)
    check(f"and takes under 20s (took {took:.1f}s)", took < 20, True)
    check("and prints nothing", (r.stdout or "").strip(), "")

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
