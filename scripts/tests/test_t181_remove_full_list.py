#!/usr/bin/env python3
"""t181: the showall parameter and 'Show the full list' link are removed from feedback.py.

Red because: _apps still accepts showall, the showall conditional is present,
do_GET still passes showall, and the link text is in the source.

Green because: all four sites are removed.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEEDBACK = ROOT / "projects" / "app-repo" / "feedback.py"


def _func_src(src: str, name: str):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    return None


def test_apps_no_showall():
    src = FEEDBACK.read_text(encoding="utf-8")
    seg = _func_src(src, "_apps")
    assert seg is not None, "_apps method not found in feedback.py"
    assert "showall" not in seg, "_apps still references showall"


def test_do_get_no_showall():
    src = FEEDBACK.read_text(encoding="utf-8")
    seg = _func_src(src, "do_GET")
    assert seg is not None, "do_GET method not found in feedback.py"
    assert "showall" not in seg, "do_GET still references showall"


def test_link_gone():
    src = FEEDBACK.read_text(encoding="utf-8")
    assert "Show the full list" not in src, "'Show the full list' still in feedback.py"


if __name__ == "__main__":
    tests = [("apps_no_showall", test_apps_no_showall),
             ("do_get_no_showall", test_do_get_no_showall),
             ("link_gone", test_link_gone)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS: {name}")
        except AssertionError as e:
            print(f"FAIL: {name}: {e}")
            failures += 1
        except Exception as e:
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
            failures += 1
    if failures:
        print(f"\n{failures}/{len(tests)} failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed")
    sys.exit(0)
