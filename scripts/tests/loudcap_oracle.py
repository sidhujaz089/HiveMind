"""Shared checker for the truncation-sweep chunks (not a suite: no test_ prefix).

Each chunk's suite names ONE function in ONE file and what kind of site it is:
  record -- the value must be stored WHOLE: no fixed-width slice, and no cap() either;
  input  -- content shown to a model: any fixed-width slice must be gone, and if a limit is
            kept it must go through loudcap.cap(), which declares what it dropped.
Source-level on purpose: several of these modules start model runs on import
(test_import_is_free.py), so importing them in a baseline would call a model.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def function_source(rel, name):
    src = (ROOT / rel).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node)
    return None


def run(rel, func, kind, gone):
    """kind: whole  -- the value is used/stored WHOLE: the slice is gone and no cap() replaces it;
             input  -- shown to a model: the slice is gone and cap() declares any limit kept;
             mixed  -- one line is a record and one an input: slices gone, cap() used for the input.

    NARROW ON PURPOSE. The first version failed on every fixed-width slice anywhere in the
    function, and probe.py's main() is 248 lines of mostly harmless display slices -- so the
    suite demanded edits the brief forbids. A requirement that cannot be met honestly is the
    pattern this repository keeps finding in its own briefs. Only the named expressions, and
    the same variable re-sliced another way ([0:5000], [:4999]), are checked.
    """
    fails = []
    body = function_source(rel, func)
    if body is None:
        print(f"  [BAD]  {rel}:{func}() not found -- renaming the function is not a fix")
        return 1
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
    for expr in gone:
        if expr in code:
            fails.append(f"the silent cap `{expr}` is still in {func}()")
        base = expr[:expr.rindex("[")]
        again = re.compile(re.escape(base) + r"\[\s*\d*\s*:\s*\d+\s*\]")
        for m in again.finditer(code):
            if m.group(0) != expr:
                fails.append(f"`{m.group(0)}` re-slices the same value -- a different width is "
                             f"still a silent cap")
    if kind == "whole" and "cap(" in code:
        fails.append("this value must be WHOLE -- cap() declares a cut, it does not undo it")
    if kind in ("input", "mixed") and "cap(" not in code:
        fails.append(f"no loudcap.cap() call in {func}() -- a kept limit must declare itself")
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import loudcap
        long = "x" * 50
        out = loudcap.cap(long, 10, "fixture")
        if "40 characters are NOT shown" not in out or loudcap.cap("short", 10, "f") != "short":
            fails.append("loudcap.cap no longer declares what it dropped")
    except Exception as e:                                          # noqa: BLE001
        fails.append(f"loudcap not importable: {e}")
    for f in fails:
        print(f"  [BAD]  {f}")
    if not fails:
        print(f"  [ok]   {rel}:{func}() has no silent cap ({kind})")
    print(f"\n{0 if fails else 1} passed, {len(fails)} failed")
    return 1 if fails else 0
