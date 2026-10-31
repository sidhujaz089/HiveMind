#!/usr/bin/env python3
"""Importing a script must not DO anything. Especially not call a model.

WHY, 2026-09-08. `scripts/verify_control.py` has no `__main__` guard: its module level is the
experiment. Importing it prints a banner, loops over the trap set calling BOTH Q16 and Q4 per
item, and writes a results file. Measured in the run I triggered by accident: 74.4s and 47.9s
on one item, 70.9s and 40.5s on the next.

Two harms. It is UNTESTABLE -- an oracle written for its jsonx migration correctly stubbed
`urllib.request.urlopen` and still could not run, because the experiment starts before any
assertion is reached. And it is an Ollama consumer that never takes the model lease, which is
worse than the cases in `t244`: those at least intend to call a model, while this one calls
two as a side effect of the word `import`.

This suite is the class fix rather than the file fix. Nothing today stops the next script
doing the same, and the failure is invisible until somebody imports it.

WHAT COUNTS AS A SIDE EFFECT. Imports, assignments to constants, `def`, `class`, docstrings
and `if __name__` blocks are fine -- that is what a module IS. A bare call, a loop, or a
conditional that is not the main guard is not. The `sys.path` shim several files use
(`if _SCRIPTS not in sys.path: ...`) is allowed: it is setup, it is cheap, and forbidding it
would break the layout this repository depends on.

SCOPE, and it took two wrong answers to get here. The first version flagged 45 files and would
have demanded a `main()` around every one-off analysis script -- audit.py, bench2.py,
make_results.py -- whose module level IS the script and which nothing imports. That is churn,
not safety. The second version scoped it to files something imports, which let
`verify_control.py` escape THE MOMENT its only test was removed: a rule that exempts a file
for having no importers exempts exactly the files that are too broken to import.

So the scope is the HAZARD, not the popularity: a file that touches a model AND does work at
module level. Importing one of those starts a model run, which is what happened. Files that
merely print or loop are listed as a warning and do not fail the suite -- they can be given
guards when someone is next in them.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PASSED = FAILED = 0
SKIP = {".venv", "node_modules", ".git", "__pycache__", "tests"}


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def side_effects(path):
    """Module-level statements that DO something. Empty list means importing is free."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    bad = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue                                          # a docstring
        if isinstance(node, ast.Expr) and "sys.path" in ast.unparse(node):
            continue                                          # the path shim, bare form
        if isinstance(node, ast.If):
            # the sys.path shim: cheap, necessary, and not what this is hunting
            if "sys.path" in ast.unparse(node.test):
                continue
            t = node.test
            # the standard guard: if __name__ == "__main__"
            if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                    and t.left.id == "__name__"):
                continue
        if isinstance(node, (ast.Try, ast.Pass)):
            continue                                          # optional-import shims
        bad.append(f"line {node.lineno}: {type(node).__name__}")
    return bad


sys.path.insert(0, str(ROOT / "scripts"))
import importgraph                                           # noqa: E402

everything = sorted(p for p in ROOT.rglob("*.py")
                    if not any(part in SKIP for part in p.parts))
# Who is imported by somebody? Those are the files whose import must be free. Tests count:
# a module that cannot be imported by a test cannot be tested, which is how this was found.
imported = set()
for p in ROOT.rglob("*.py"):
    if any(part in {".venv", "node_modules", ".git", "__pycache__"} for part in p.parts):
        continue
    try:
        imported |= importgraph.imports_of(str(p.relative_to(ROOT)))
    except Exception:                                        # noqa: BLE001
        pass
import re                                                    # noqa: E402
CALLS_MODEL = re.compile(r"11434|/api/(chat|generate|show|ps|tags)\b|\bollama\b", re.I)


def touches_model(p):
    return bool(CALLS_MODEL.search(p.read_text(encoding="utf-8", errors="replace")))


files = [p for p in everything if touches_model(p)]
warn = [p for p in everything if not touches_model(p)
        and str(p.relative_to(ROOT)) in imported]
print(f"\n{len(files)} of {len(everything)} scripts touch a model. Importing one of THOSE "
      "starts a model run,\nso they must be free to import:\n")

offenders = {}
for p in files:
    hits = side_effects(p)
    if hits:
        offenders[str(p.relative_to(ROOT))] = hits

# The one that prompted this. Named explicitly so its fix is visible in the diff.
check("scripts/verify_control.py imports cleanly",
      "scripts/verify_control.py" in offenders, False)

for path, hits in sorted(offenders.items()):
    check(f"{path} has no module-level side effects", hits, [])

check("no model-touching script runs work on import", len(offenders), 0)

soft = {str(p.relative_to(ROOT)): side_effects(p) for p in warn}
soft = {k: v for k, v in soft.items() if v}
if soft:
    print(f"\n  and {len(soft)} imported script(s) do module-level work without touching a "
          "model.\n  Not a failure -- give them a main guard when you are next in them:")
    for k in sorted(soft):
        print(f"    {k}")

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
