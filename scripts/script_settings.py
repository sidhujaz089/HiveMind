#!/usr/bin/env python3
"""Describe a script from its OWN comments: what it does, and which of its numbers are knobs.

Jas, 2026-10-30: *"I have an idea we have a script library. I want every script in there to be something I can tap on
and adjust the variables. I want boxes that I can manipulate and I want a How this page works that derives the
functions of each script from the comments."*

This is the data a page needs, and nothing about the page:

  * `purpose`   -- the module docstring's own words, so a page explains a script the way its author did.
  * `functions` -- every def and class with a ONE-LINE summary taken from its docstring, or from the comment block
                   immediately above it. That is "derives the functions of each script from the comments" done
                   mechanically; a hand-written summary would be a second description that drifts.
  * `knobs`     -- module-level constants whose value is a LITERAL, with the value, its type and its line. A name bound
                   to an expression is deliberately not a knob: a page that offered it would be offering an edit it
                   cannot make safely.

    python3 scripts/script_settings.py scripts/guarded_test.py
    python3 scripts/script_settings.py --all | head

READ-ONLY. It never writes a script. Turning a knob is a separate decision with its own risk, and this module's job is
to make the knobs findable and honestly described.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "scripts" / "library.json"


def _clean(text, limit=240):
    """One sentence, whitespace collapsed, never a wall of prose."""
    if not text:
        return ""
    first = " ".join(str(text).split())
    for stop in (". ", "! ", "? "):
        if stop in first:
            first = first.split(stop)[0] + stop.strip()
            break
    return first[:limit]


def _comment_above(lines, lineno, first_line=False):
    """The comment block immediately above `lineno` (1-based), joined -- or just its first line."""
    out = []
    i = lineno - 2
    while i >= 0 and lines[i].lstrip().startswith("#"):
        out.append(lines[i].lstrip().lstrip("#").strip())
        i -= 1
    block = list(reversed(out))
    if first_line:
        return _clean(block[0]) if block else ""
    return _clean(" ".join(block))


def _literal(node):
    """(ok, value) for a literal that a page could show and a person could edit. Anything else is not a knob."""
    try:
        val = ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return False, None
    if isinstance(val, (str, int, float, bool)) or isinstance(val, (list, tuple, dict)) and len(repr(val)) <= 200:
        if isinstance(val, (list, tuple)):
            return True, list(val)
        return True, val
    return False, None


def describe(path):
    """{purpose, functions: [...], knobs: [...]} for one script. A missing or unparsable file describes as empty."""
    p = Path(path)
    empty = {"purpose": "", "functions": [], "knobs": []}
    # A SHELL SCRIPT IS NOT A PYTHON ONE (2026-10-30). The library holds .sh files too, and ast.parse raised on every
    # one of them -- five NOTEs before the first useful line of `--all`. Empty is the honest answer here; a page can
    # say "this is a shell script" without pretending to have read it as Python.
    if p.suffix in (".sh", ".bash"):
        return _describe_shell(p)
    if p.suffix != ".py":
        return empty
    try:
        src = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return empty
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print(f"  NOTE: {p} does not parse ({e.msg} line {e.lineno}) -- nothing described", file=sys.stderr)
        return empty
    lines = src.splitlines()

    purpose = _clean(ast.get_docstring(tree) or "")

    functions = []
    for node in tree.body:                      # MODULE LEVEL ONLY: a nested def is an implementation detail
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        summary = _clean(ast.get_docstring(node) or "") or _comment_above(lines, node.lineno)
        functions.append({"name": node.name, "kind": type(node).__name__, "summary": summary,
                          "line": node.lineno})

    knobs = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not target.id.isupper():
            continue                            # a knob is a CONSTANT by name, not merely a variable
        ok, val = _literal(node.value)
        if not ok:
            continue
        # A TRAILING COMMENT COUNTS TOO (2026-10-30, seen on guarded_test.py): DEFAULT_NEED_GIB = 12.0  # the prompt's
        # number... carries its reason on the SAME line, and the page showed a bare number with no explanation because
        # only the block above was read. The line's own comment is used when there is no block.
        line_text = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
        trailing = _clean(line_text.partition("#")[2]) if "#" in line_text[line_text.find("="):] else ""
        knobs.append({"name": target.id, "value": val, "type": type(val).__name__, "line": node.lineno,
                      "summary": _comment_above(lines, node.lineno) or trailing})
    return {"purpose": purpose, "functions": functions, "knobs": knobs}


_SH_ASSIGN = re.compile(r"^([A-Z][A-Z0-9_]{1,})=(.+)$")
_SH_FUNC = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{")
_SH_FLAG = re.compile(r"(?:^|\s)(--[a-z][a-z0-9-]*)[ =]\"?([0-9]{1,6})\b")


def _describe_shell(p):
    """{purpose, functions, knobs, flags} for a shell script, from its own comments and literal assignments.

    THE LIBRARY HOLDS SHELL SCRIPTS (2026-10-30: the scoping loop is one). Parsing them as Python raised on every one,
    so they described as empty and a page would have shown nothing for the loop Jas most wants to see. The rules are
    the shell's own: a leading comment block is the purpose, `NAME=literal` at the left margin is a knob, `name() {` is
    a function, and the numeric flags a script passes (`--timeout 5400`) are the numbers it actually runs with.
    """
    out = {"purpose": "", "functions": [], "knobs": [], "flags": []}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    lines = text.splitlines()
    lead = []
    for ln in lines:
        if ln.startswith("#!") or not ln.strip():
            continue
        if ln.lstrip().startswith("#"):
            lead.append(ln.lstrip().lstrip("#").strip())
            continue
        break
    out["purpose"] = _clean(" ".join(lead))
    for i, ln in enumerate(lines, 1):
        m = _SH_ASSIGN.match(ln)
        if m:
            raw = m.group(2).strip()
            val, kind = None, None
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
                val, kind = raw[1:-1], "str"
            elif re.fullmatch(r"-?[0-9]+", raw):
                val, kind = int(raw), "int"
            # `SCOPER="$1"` AND `Q=$R/...` ARE NOT KNOBS (2026-10-30, seen on the real loop). A value that reads an
            # argument or another variable is the script's plumbing; a page offering it would be offering to edit the
            # wiring, not a setting.
            if kind and "$" not in raw and "`" not in raw:
                out["knobs"].append({"name": m.group(1), "value": val, "type": kind, "line": i,
                                     "summary": _comment_above(lines, i)})
            continue
        f = _SH_FUNC.match(ln)
        if f:
            above = _comment_above(lines, i)          # the whole block, for a page that wants it
            first = _comment_above(lines, i, first_line=True)
            out["functions"].append({"name": f.group(1), "kind": "ShellFunction",
                                     "summary": first, "comment": above, "line": i})
        for fm in _SH_FLAG.finditer(ln):
            pair = {"flag": fm.group(1), "value": fm.group(2), "line": i}
            if pair not in out["flags"]:
                out["flags"].append(pair)
    return out


def library():
    """[{path, what}] for every registered script, so a page can list them without knowing the registry's shape."""
    try:
        data = json.loads(LIBRARY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:           # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: the script library is unreadable ({type(e).__name__}: {e})", file=sys.stderr)
        return []
    out = []
    for key, entry in (data.get("scripts") or data).items() if isinstance(data, dict) else []:
        if not isinstance(entry, dict):
            continue
        out.append({"path": key, "what": entry.get("what") or entry.get("description") or "",
                    "kind": entry.get("kind") or ""})
    return sorted(out, key=lambda e: e["path"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("script", nargs="?", help="a script to describe")
    ap.add_argument("--all", action="store_true", help="every script in the library, one line each")
    ap.add_argument("--json", action="store_true", help="the whole description as JSON")
    a = ap.parse_args(argv)
    if a.all:
        skipped = 0
        for e in library():
            if not e["path"].endswith(".py"):
                skipped += 1
                continue
            d = describe(REPO / e["path"])
            print(f"  {e['path'][:52]:54s} {len(d['knobs']):3d} knob(s)  {_clean(d['purpose'] or e['what'], 70)}")
        if skipped:
            print(f"  ({skipped} non-Python script(s) in the library are listed but not described here)")
        return 0
    if not a.script:
        ap.error("name a script, or use --all")
    d = describe(a.script)
    if a.json:
        print(json.dumps(d, indent=1))
        return 0
    print(f"  {a.script}")
    print(f"  what it does: {d['purpose'] or '(no module docstring)'}")
    if d["knobs"]:
        print(f"\n  knobs ({len(d['knobs'])}):")
        for k in d["knobs"]:
            print(f"    {k['name']:28s} = {k['value']!r:<18} {k['type']:5s} line {k['line']}"
                  + (f"  -- {k['summary']}" if k["summary"] else ""))
    if d.get("flags"):
        print(f"\n  numbers it runs with ({len(d['flags'])}):")
        for f in d["flags"]:
            print(f"    {f['flag']:22s} {f['value']:>8}  line {f['line']}")
    if d["functions"]:
        print(f"\n  what it holds ({len(d['functions'])}):")
        for f in d["functions"]:
            print(f"    {f['name']:28s} {f['kind']:10s} {f['summary'] or '(undescribed)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
