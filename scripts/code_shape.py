#!/usr/bin/env python3
"""code_shape.py -- two mechanical facts about a Python change, before and after.

LEARNED FROM THE REJECTED BUILD BRANCHES (2026-10-01). Seventeen coder/* branches were never
merged. Twelve were duplicate deliveries (fixed in 6f67734). Of the rest, two were rejected for
defects a machine can see without judgement:

  coder/brief-3c1c1ede   run.py: `OLLAMA_URL = settings.request` -- `settings` never imported,
                         a NameError on import. The file could not run at all.
  coder/brief-b698d36b   check_gate_closures.py: implemented check() and DELETED MARKER_RE and
                         the STATE.md comparison, which the brief never mentioned.

So: a change may not introduce a name that resolves to nothing, and may not remove a top-level
definition the brief did not name. Both are checked by coder.py before it accepts an attempt
and by auto_merge.py before it merges. The branches themselves are archived in
/mnt/nas/Public/HiveMind/ai-workspace-corpora/rejected-coder-branches-2026-10-01.bundle.

    undefined_names(src) -> sorted names read but never bound anywhere in the module
    top_level(src)       -> {name: kind} of module-level defs, classes and assignments
    regressions(before, after, brief) -> [reasons], empty when the change is clean
"""
import ast
import builtins
import re
import sys


def _bound(tree):
    """Every name the module binds anywhere: assignments, args, imports, defs, handlers,
    comprehension targets, globals. Deliberately module-wide rather than scope-exact -- the
    defect this hunts is a name bound NOWHERE, and scope-exactness would trade that certainty
    for false alarms on closures."""
    names = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__builtins__", "__spec__",
                                  "__loader__", "__package__", "__path__", "__dict__", "__class__"}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
        elif isinstance(n, ast.MatchAs) and n.name:
            names.add(n.name)
        elif isinstance(n, ast.MatchStar) and n.name:
            names.add(n.name)
    return names


def undefined_names(src):
    """Names read somewhere and bound nowhere. A star import makes the answer unknowable, so it
    returns [] rather than guess."""
    tree = ast.parse(src)
    if any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
           for n in ast.walk(tree)):
        return []
    bound = _bound(tree)
    return sorted({n.id for n in ast.walk(tree)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound})


def top_level(src):
    """{name: kind} for the module's own top-level definitions."""
    out = {}
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[n.name] = "def"
        elif isinstance(n, ast.ClassDef):
            out[n.name] = "class"
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(t, ast.Name):
                    out[t.id] = "name"
    return out


def regressions(before, after, brief=""):
    """Reasons a Python change is not clean, or [] when it is. `before` may be None (new file)."""
    out = []
    try:
        new_undef = set(undefined_names(after))
    except SyntaxError as e:
        return [f"does not parse: {e}"]
    try:
        old_undef = set(undefined_names(before)) if before else set()
        gone = set(top_level(before)) - set(top_level(after)) if before else set()
    except SyntaxError:
        old_undef, gone = set(), set()
    added = sorted(new_undef - old_undef)
    if added:
        out.append(f"introduces name(s) bound nowhere in the file: {', '.join(added)} "
                   f"(a NameError when that line runs -- coder/brief-3c1c1ede)")
    words = set(re.findall(r"[A-Za-z_]\w*", brief or ""))
    unnamed = sorted(g for g in gone if g not in words)
    if unnamed:
        out.append(f"removes top-level definition(s) the brief never names: {', '.join(unnamed)} "
                   f"(coder/brief-b698d36b deleted MARKER_RE this way)")
    return out


if __name__ == "__main__":
    a = open(sys.argv[1]).read() if len(sys.argv) > 2 else None
    b = open(sys.argv[-1]).read()
    for r in regressions(a, b):
        print(r)
