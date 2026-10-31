#!/usr/bin/env python3
"""Read facts out of Python source with the parser Python already ships.

    from pysource import constant, defaults
    constant(path, "EXPECTED_CERT_SHA256")     -> the value, or None
    constant(path, "DENIED_PREFIXES")          -> a tuple/list/set, as a real Python value
    defaults(path)                             -> {func: {arg: value}}, {unparseable}

WHY THIS EXISTS (2026-08-25)
----------------------------
Three separate checks in this repository were reading Python source with regular expressions:

    check_deployed.py   r"^def (\\w+)\\(([^)]*)\\)"        function signatures
    check_workspace.py  name + r"\\s*=\\s*\\(([^)]*)\\)"    tuple constants
    check_apps.py       r'NAME\\s*=\\s*"([0-9a-f]{64})"'   a string constant

All three have the same flaw and two of them had it in the identical form: `[^)]*` stops at
the **first** close paren, so a value containing parentheses truncates the match and
everything after it becomes invisible.

**The cost of not having this tool is measurable.** `t147`, `t148` and `t149` were three
separate agent tasks spent patching the first of those regexes — non-string defaults, then
unparseable values, then a hand-rolled balanced-paren counter. Every one of those patches was
competent. Every one was unnecessary: `ast` parses Python correctly and has shipped in the
standard library for as long as this repository has existed.

**That was a supervisor failure, not an agent one.** I framed each task as *"fix the regex"*
and never asked *"why are we parsing Python with a regex"*. A well-shaped task inside a bad
design is still wasted work, and the agent has no way to tell.

WHAT IT DOES NOT DO
-------------------
It reads **source**, not a running program. A constant assembled at runtime, or one built by
a function call, is reported as unparseable rather than guessed at. That is the right answer
for a checker: the alternative is importing the module under test, which runs it.
"""

import ast
from pathlib import Path


def _tree(src):
    return ast.parse(src if isinstance(src, str) else Path(src).read_text(encoding="utf-8"))


def constant(src, name, default=None):
    """The value of a module-level assignment, as a real Python object.

    Handles the shapes a regex gets wrong: parentheses inside the value, multi-line
    literals, implicit string concatenation across lines, and negative numbers.
    """
    for node in _tree(src).body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name and node.value is not None:
                try:
                    return ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    return default
    return default


def defaults(src):
    """({function: {arg: value}}, [unparseable]) for every def in the source.

    Covers positional and keyword-only arguments. A default that is not a literal -- a name,
    a call -- is reported with its source text rather than dropped, because a check that
    silently omits what it could not read is the `L-087` shape.
    """
    out, unparsed = {}, []
    for node in ast.walk(_tree(src)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        pos = node.args.posonlyargs + node.args.args
        pairs = list(zip(pos[len(pos) - len(node.args.defaults):], node.args.defaults))
        pairs += [(a, d) for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults)
                  if d is not None]
        got = {}
        for arg, d in pairs:
            try:
                got[arg.arg] = ast.literal_eval(d)
            except (ValueError, SyntaxError):
                unparsed.append(f"{node.name}({arg.arg}=) {ast.unparse(d)}")
        if got:
            out[node.name] = got
    return out, unparsed


SELF_TEST = [
    ("string constant", 'X = "abc"', lambda: constant('X = "abc"', "X") == "abc"),
    ("tuple with parens inside",
     'P = ("a(b", "c)d")', lambda: constant('P = ("a(b", "c)d")', "P") == ("a(b", "c)d")),
    ("multi-line tuple",
     'P = (\n "a",\n "b",\n)', lambda: constant('P = (\n "a",\n "b",\n)', "P") == ("a", "b")),
    ("implicit concatenation",
     'X = ("ab"\n     "cd")', lambda: constant('X = ("ab"\n     "cd")', "X") == "abcd"),
    ("negative number", "N = -1", lambda: constant("N = -1", "N") == -1),
    ("missing name is None", "X = 1", lambda: constant("X = 1", "Y") is None),
    ("runtime value is not guessed",
     "X = compute()", lambda: constant("X = compute()", "X") is None),
    ("defaults incl. keyword-only",
     'def f(a: int = 1, *, b: str = "z"): pass',
     lambda: defaults('def f(a: int = 1, *, b: str = "z"): pass')[0]
     == {"f": {"a": 1, "b": "z"}}),
    ("call default is reported, not dropped",
     "def f(a=list()): pass",
     lambda: defaults("def f(a=list()): pass")[1] == ["f(a=) list()"]),
    ("a call before another arg does not hide it",
     'def f(a=list(), c="ok"): pass',
     lambda: defaults('def f(a=list(), c="ok"): pass')[0] == {"f": {"c": "ok"}}),
]


def main():
    bad = 0
    print("\npysource self-test\n")
    for name, _src, fn in SELF_TEST:
        try:
            ok = fn()
        except Exception as e:                       # noqa: BLE001
            ok, name = False, f"{name} ({type(e).__name__}: {e})"
        bad += not ok
        print(f"  {'[ok]  ' if ok else '[BAD] '} {name}")
    print(f"\n{len(SELF_TEST) - bad} passed, {bad} failed\n")
    return 1 if bad else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
