#!/usr/bin/env python3
"""mf1(b): flag tests that read a file under scripts/ and assert string literals in its TEXT.

Seen in review (2026-10-27): test_gate_timeout_message asserted 'echo 1800' in the hook's
source and passed while the hook could not read its setting -- and one check could never
fail, because 'rc -eq 1' is a substring of 'rc -eq 124'. A test that checks source text
instead of behaviour is not an oracle (CLAUDE.md).

What counts as an offender: inside a scripts/tests/*.py file, an assertion (assert, or a
check()/ck()-style call) whose test is `"<literal>" in <var>`, where <var> was assigned from
reading a path under scripts/ (read_text or open().read()). An assertion whose own line
carries `# text-assert: <reason>` is allowed: the reason is on the record.

This REPORTS offenders; it rewrites nothing. Exit 1 when offenders exist, 0 when clean.
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

MARKER = "# text-assert:"


def _reads_scripts_file(node, src):
    """True when `node` (an Assign value) reads a file whose path text mentions scripts/."""
    for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
        fn = call.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in ("read_text", "read", "open"):
            continue
        seg = ast.get_source_segment(src, call) or ""
        if "scripts/" in seg or "scripts'" in seg or 'scripts"' in seg:
            return True
    return False


def _file_offenders(path):
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [(str(path), e.lineno or 0, f"does not parse: {e.msg}")]
    lines = src.splitlines()
    tainted = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _reads_scripts_file(node.value, src):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    tainted.add(t.id)
        elif isinstance(node, ast.AnnAssign) and node.value is not None \
                and _reads_scripts_file(node.value, src) and isinstance(node.target, ast.Name):
            tainted.add(node.target.id)
    if not tainted:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1 \
                or not isinstance(node.ops[0], ast.In):
            continue
        if not (isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)):
            continue
        right = node.comparators[0]
        if not (isinstance(right, ast.Name) and right.id in tainted):
            continue
        line_text = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
        if MARKER in line_text:
            continue
        out.append((str(path), node.lineno, repr(node.left.value)[:60]))
    return out


def offenders(root=REPO):
    """[(file, line, literal)] for every unmarked text-assert in root's scripts/tests/."""
    root = Path(root)
    tdir = root / "scripts" / "tests"
    hits = []
    for p in sorted(tdir.glob("test_*.py")) if tdir.is_dir() else []:
        hits.extend(_file_offenders(p))
    return hits


def main(argv=None):
    hits = offenders()
    for f, line, lit in hits:
        print(f"  {Path(f).relative_to(REPO) if str(f).startswith(str(REPO)) else f}:{line}: "
              f"asserts {lit} in a scripts/ file's TEXT -- run the code, or mark the line "
              f"with '{MARKER} <reason>'")
    print(f"{len(hits)} unmarked text-assert(s)" if hits else "clean: no unmarked text-asserts")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
