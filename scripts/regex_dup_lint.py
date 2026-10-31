#!/usr/bin/env python3
"""mf3: flag the same regex literal appearing in two files under scripts/.

Seen: the Lane-trailer pattern lived in scripts/hooks/commit-msg's grep AND in
role_gate.LANE_RE, with length caps that disagreed. A rule copied into two files drifts;
the copy that fires depends on which gate ran first.

What is compared: the exact literal pattern string of
  * re.compile(<string constant>) / re.match / re.search / re.fullmatch first arguments
    in every scripts/**/*.py, and
  * the quoted pattern after `grep -E` / `grep -qE` (flags in any order) in every
    non-Python file under scripts/ (the hooks and *.sh).
A pattern shorter than 8 characters is ignored (too generic to be one rule). The lint
REPORTS duplicates and rewrites nothing. Exit 1 when duplicates exist, 0 when clean.
"""
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIN_LEN = 8
_RE_FUNCS = {"compile", "match", "search", "fullmatch"}
_GREP = re.compile(r"""grep\s+(?:-[a-zA-Z]*E[a-zA-Z]*\s+)+['"](.+?)['"]""")


def _py_patterns(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in _RE_FUNCS
                and isinstance(fn.value, ast.Name) and fn.value.id == "re"):
            continue
        a = node.args[0]
        if isinstance(a, ast.Constant) and isinstance(a.value, str) and len(a.value) >= MIN_LEN:
            out.append(a.value)
    return out


def _sh_patterns(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    return [m.group(1) for m in _GREP.finditer(text) if len(m.group(1)) >= MIN_LEN]


def duplicates(root=REPO):
    """[(pattern, sorted files)] for every regex literal found in two or more files."""
    sdir = Path(root) / "scripts"
    seen = defaultdict(set)
    if sdir.is_dir():
        for p in sorted(sdir.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts or "tests" in p.parts:
                continue
            pats = _py_patterns(p) if p.suffix == ".py" else _sh_patterns(p)
            for pat in pats:
                seen[pat].add(str(p))
    return sorted((pat, sorted(files)) for pat, files in seen.items() if len(files) > 1)


def main(argv=None):
    dups = duplicates()
    for pat, files in dups:
        print(f"  {pat!r} appears in {len(files)} files -- one rule, one place (mf3):")
        for f in files:
            rel = Path(f)
            try:
                rel = rel.relative_to(REPO)
            except ValueError:
                pass
            print(f"    {rel}")
    print(f"{len(dups)} duplicated regex literal(s)" if dups
          else "clean: no regex literal appears in two files under scripts/")
    return 1 if dups else 0


if __name__ == "__main__":
    sys.exit(main())
