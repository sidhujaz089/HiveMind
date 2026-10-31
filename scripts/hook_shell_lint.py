#!/usr/bin/env python3
"""mf2: no silent fallback, and no call to a subcommand that does not exist.

Seen: scripts/hooks/ called `gate_settings.py get` and `record` before those subcommands
existed, and `2>/dev/null || echo 1800` / `|| true` hid argparse's exit 2 -- the setting
was never read, no run was recorded, and nothing said so.

Two checks over scripts/hooks/* and *.sh:
(a) silent-fallback: inside a command substitution `$( ... )` -- output that IS used --
    a `2>/dev/null ... || echo <default>` or a `|| true` swallows the failure. Allowed only
    when the fallback prints a NOTE (the failure reaches a human). A bare `cmd || true`
    outside a substitution is legal: its output is not consumed.
(b) phantom-subcommand: `python3 .../X.py <word>` where <word> is a bare lowercase word and
    X.py (resolved under scripts/) does not define it -- as an argparse add_parser name, an
    add_argument choices= value, or an `argv[1] == "<word>"` comparison. A script in which
    no subcommand vocabulary can be found is not judged.

Reports (file, line, kind, reason); exit 1 when findings exist. Rewrites nothing.
"""
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_SUBST = re.compile(r"\$\(")
_TRUE = re.compile(r"\|\|\s*true\b")
_ECHO_FB = re.compile(r"\|\|\s*(?:\{\s*)?echo\b")
_CALL = re.compile(r"""python3\s+["']?[^\s"']*?(\w+)\.py["']?\s+([a-z][a-z0-9_-]*)(?=\s|$)""")


def _substitutions(line):
    """The text of each $( ... ) on this line, balanced, innermost content included."""
    out = []
    for m in _SUBST.finditer(line):
        depth, i = 1, m.end()
        while i < len(line) and depth:
            if line[i] == "(":
                depth += 1
            elif line[i] == ")":
                depth -= 1
            i += 1
        out.append(line[m.end():i - 1])
    return out


def _choices_of(py_path):
    """The subcommand vocabulary of a python CLI, or None when none is discoverable."""
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return None
    words = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "add_parser" and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    words.add(a.value)
            if isinstance(fn, ast.Attribute) and fn.attr == "add_argument":
                for kw in node.keywords:
                    if kw.arg == "choices":
                        for el in ast.walk(kw.value):
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                words.add(el.value)
    # argv-style dispatch: the compared name may be argv itself or a variable fed from it
    # (`mode = argv[1] ...`, `cmd, rest = argv[0], argv[1:]`). Over-collecting from a
    # same-named unrelated variable only makes the lint more permissive, never a false refusal.
    tracked = {"argv"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and getattr(node, "value", None) is not None:
            if any(isinstance(el, ast.Name) and el.id == "argv" or
                   isinstance(el, ast.Attribute) and el.attr == "argv"
                   for el in ast.walk(node.value)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    for el in ast.walk(t):
                        if isinstance(el, ast.Name):
                            tracked.add(el.id)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            left_names = {el.id for el in ast.walk(node.left) if isinstance(el, ast.Name)}
            left_names |= {el.attr for el in ast.walk(node.left) if isinstance(el, ast.Attribute)}
            if not (left_names & tracked):
                continue
            for cmp_ in node.comparators:
                for el in ast.walk(cmp_):
                    if isinstance(el, ast.Constant) and isinstance(el.value, str):
                        words.add(el.value)
    return words or None


def _resolve(root, name):
    for cand in (root / "scripts" / f"{name}.py",
                 root / "scripts" / "hooks" / f"{name}.py"):
        if cand.is_file():
            return cand
    return None


def _file_offenders(path, root):
    out = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return [(str(path), 0, "unreadable", f"{type(e).__name__}: {e}")]
    for n, line in enumerate(lines, 1):
        code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
        for sub in _substitutions(code):
            if "NOTE" in sub:
                continue
            if _TRUE.search(sub):
                out.append((str(path), n, "silent-fallback",
                            "'|| true' inside $(): the failure is swallowed and the empty "
                            "output is used. Print a NOTE in the fallback, or let it fail."))
            elif "2>/dev/null" in sub and _ECHO_FB.search(sub):
                out.append((str(path), n, "silent-fallback",
                            "'2>/dev/null || echo <default>' inside $(): argparse's exit 2 "
                            "is hidden and the default is used silently. Print a NOTE."))
        for m in _CALL.finditer(code):
            name, word = m.group(1), m.group(2)
            target = _resolve(root, name)
            if target is None:
                continue
            words = _choices_of(target)
            if words is not None and word not in words:
                out.append((str(path), n, "phantom-subcommand",
                            f"{name}.py has no subcommand {word!r} (it knows: "
                            f"{', '.join(sorted(words))})"))
    return out


def offenders(root=REPO, files=None):
    root = Path(root)
    if files:
        targets = [Path(f) if Path(f).is_absolute() else root / f for f in files]
    else:
        hooks = (root / "scripts" / "hooks")
        targets = sorted(p for p in hooks.iterdir()
                         if p.is_file() and p.suffix != ".py") if hooks.is_dir() else []
        targets += sorted(p for p in root.rglob("*.sh") if ".git" not in p.parts)
    out = []
    for p in targets:
        if p.is_file():
            out.extend(_file_offenders(p, root))
    return out


def main(argv=None):
    args = (argv if argv is not None else sys.argv[1:])
    hits = offenders(files=args or None)
    for f, line, kind, why in hits:
        rel = Path(f)
        try:
            rel = rel.relative_to(REPO)
        except ValueError:
            pass
        print(f"  {rel}:{line}: [{kind}] {why}")
    print(f"{len(hits)} finding(s)" if hits else "clean: no silent fallbacks or phantom subcommands")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
