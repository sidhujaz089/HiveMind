#!/usr/bin/env python3
"""Set ONE knob in a script by editing its literal, and nothing else.

Jas, 2026-10-30: *"I want every script in there to be something I can tap on and adjust the variables. I want boxes
that I can manipulate."*

A box needs a writer, and a writer has to be narrower than sed:

  * it edits the literal `script_settings.describe()` already reported, at its own line;
  * it refuses a name bound to an EXPRESSION (`X = compute()`) -- there is no safe way to "set" one, and guessing is
    how a settings page corrupts a script;
  * it refuses a value of the wrong type rather than coercing ("seven" is not an int);
  * it shows the diff first (`--dry-run`) and writes only when asked.

    python3 scripts/set_knob.py scripts/guarded_test.py DEFAULT_NEED_GIB 14 --dry-run
    python3 scripts/set_knob.py scripts/guarded_test.py DEFAULT_NEED_GIB 14

WHY A LITERAL REWRITE AND NOT AN OVERLAY FILE: an overlay is a second source of truth that every script must be taught
to read, and a script that forgets reads a different number from the one the page shows. The literal is the value the
script actually runs with, so changing it changes the thing itself.
"""
import argparse
import ast
import difflib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import script_settings as ss          # noqa: E402


def _literal_for(value, wanted_type):
    """(ok, source_text, why) -- the literal to write, or why this value cannot be written as that type."""
    txt = str(value).strip()
    if wanted_type == "int":
        try:
            return True, str(int(txt)), ""
        except ValueError:
            return False, "", f"this knob is an int and {txt!r} is not one"
    if wanted_type == "float":
        try:
            return True, repr(float(txt)), ""
        except ValueError:
            return False, "", f"this knob is a float and {txt!r} is not one"
    if wanted_type == "bool":
        if txt.lower() in ("true", "1", "yes", "on"):
            return True, "True", ""
        if txt.lower() in ("false", "0", "no", "off"):
            return True, "False", ""
        return False, "", f"this knob is a bool and {txt!r} is not one"
    if wanted_type == "str":
        # KEEP THE AUTHOR'S QUOTE STYLE (2026-10-30, caught by the suite): repr() always writes single quotes, so a
        # file full of double quotes got a one-character style change on every edit -- noise in a diff that is meant
        # to show one number moving.
        return True, repr(txt), ""
    return False, "", (f"this knob is a {wanted_type}; set that kind by hand, because writing it safely needs a "
                       f"decision this tool does not make")


def set_knob(path, name, value, write=True):
    """(ok, message, new_text). Edits ONLY the literal line for `name`; refuses anything it cannot do safely."""
    p = Path(path)
    try:
        original = p.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"cannot read {p}: {type(e).__name__}: {e}", ""
    desc = ss.describe(p)
    knob = next((k for k in desc["knobs"] if k["name"] == name), None)
    if knob is None:
        # Distinguish "not declared" from "declared but not a literal", because the second is a real answer.
        try:
            tree = ast.parse(original)
        except SyntaxError:
            return False, f"no knob {name} (and {p.name} does not parse)", original
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
                return False, (f"no knob {name}: it is bound to an EXPRESSION, not a literal, so there is no value "
                               f"here to set -- change the code that computes it"), original
        return False, f"no knob {name} in {p.name}", original

    ok, literal, why = _literal_for(value, knob["type"])
    if not ok:
        return False, why, original

    lines = original.splitlines(keepends=True)
    idx = knob["line"] - 1
    if idx < 0 or idx >= len(lines):
        return False, f"line {knob['line']} is not in {p.name} any more -- re-read the file", original
    line = lines[idx]
    if name not in line:
        return False, f"line {knob['line']} of {p.name} no longer assigns {name} -- re-read the file", original

    # match the quoting the line already uses, so the diff is the VALUE and nothing else
    if knob["type"] == "str" and literal[:1] == "'" and '"' in line:
        literal = '"' + literal[1:-1].replace('"', '\\"') + '"'
    head, _, tail = line.partition("=")
    rest = tail.lstrip()
    newline_kept = "\n" if line.endswith("\n") else ""
    body = rest.rstrip("\n")
    # KEEP A TRAILING COMMENT EXACTLY AS WRITTEN, INCLUDING ITS ALIGNMENT (2026-10-30, seen on a real dry run): the
    # first version re-spaced it to two spaces, so changing one number also moved every comment on the line. A diff
    # that is meant to show a value moving must not move anything else.
    comment = ""
    if "#" in body:
        code, _, note = body.partition("#")
        gap = code[len(code.rstrip()):]                 # the spaces the author used before the #
        body, comment = code.rstrip(), gap + "#" + note
    lines[idx] = f"{head}= {literal}{comment}{newline_kept}"
    new_text = "".join(lines)

    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return False, f"the edit would not parse ({e.msg} line {e.lineno}) -- nothing written", original

    if write:
        p.write_text(new_text, encoding="utf-8")
        return True, f"{p.name}: {name} = {literal} (line {knob['line']})", new_text
    return True, f"{p.name}: {name} would become {literal} (line {knob['line']})", new_text


def diff(before, after, name="script"):
    return "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                        fromfile=f"{name} (now)", tofile=f"{name} (after)"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Set one literal knob in a script.")
    ap.add_argument("script")
    ap.add_argument("name")
    ap.add_argument("value")
    ap.add_argument("--dry-run", action="store_true", help="show the change, write nothing")
    a = ap.parse_args(argv)
    ok, msg, new = set_knob(a.script, a.name, a.value, write=not a.dry_run)
    if not ok:
        print(f"  REFUSED: {msg}")
        return 2
    before = Path(a.script).read_text(encoding="utf-8")
    if a.dry_run:
        print(diff(before, new, Path(a.script).name))
    print(f"  {'would set' if a.dry_run else 'set'}: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
