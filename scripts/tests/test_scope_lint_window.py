#!/usr/bin/env python3
"""t941: a brief-window finding must carry the symbol names the brief needs.

Red on main for the missing change. scripts/scope_lint.py builds the brief-window finding at
scope_lint.py:264-266 as the sentence
  'big.py is 138636 bytes (> 40000) and the brief names no SYMBOLS'
and nothing more. That sentence has not stopped the class: 11 rows of class brief-window in
workspace/scope-quality.jsonl are all stage=lint, on six files (console.py 95575 B t262,
dispatch_loop.py 84884 B t348, coder.py 143308 B t398, docs/LESSONS.md 270149 B t668 and t689,
check_workspace.py 129401 B t677 and 138636 B t800, FLOW.json 83999 B t798), by two models
(qwen3.8:27b-mtp-q4_K_M, swift) under three prompt templates. Naming SYMBOLS means reading the
target file to find the enclosing definitions; a remedy the model must invent is the shape that
recurs. Green when the finding names real top-level definitions taken from the file's own text,
so the answer can be copied.
"""
import ast
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import scope_lint  # noqa: E402

HEADER = """import json
from pathlib import Path


def load_ledger(path):
    \"\"\"Read ledger rows from path.\"\"\"
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def write_ledger(path, rows):
    \"\"\"Write rows to path.\"\"\"
    Path(path).write_text("\\n".join(json.dumps(r) for r in rows))


def reconcile(rows):
    \"\"\"Account for every row, including a torn final line.\"\"\"
    return {"ok": all(r.get("done") for r in rows), "missing": []}


HELPER_LIMIT = 4096
"""

FILLER = "\n".join("PAD_%d = %d" % (i, i) for i in range(4000))
TAIL = '\n\ndef main(argv):\n    """Entry point."""\n    return 0\n'
BIG_TEXT = HEADER + FILLER + TAIL


def _top_level_names(text):
    """The names a top-level reader would see: defs, classes, module-level assignments."""
    tree = ast.parse(text)
    names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    for n in tree.body:
        if isinstance(n, ast.Assign):
            names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
    return names


REAL_NAMES = _top_level_names(BIG_TEXT)


def _chunks():
    return [{
        "kind": "implementation",
        "file": "big.py",
        "target": "scripts/tests/test_scope_lint_window.py",
        "single_change": "make reconcile() report a torn final line as a missing row",
        "what_is_wrong": "reconcile() near big.py:14 drops a torn final line silently",
        "done_when": "python3 scripts/tests/test_scope_lint_window.py exits 0",
        "related_entries": [],
        "symbols": [],
    }]


def _findings(repo):
    (repo / "big.py").write_text(BIG_TEXT, encoding="utf-8")
    return scope_lint.static(_chunks(), repo)


def _window(rows):
    return [r for r in rows if r[0] == "brief-window"]


def _quoted_names(text):
    return set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", text))


def test_finding_names_real_definitions_from_the_file():
    """The remedy must be IN the finding, and it must be the file's own names."""
    with tempfile.TemporaryDirectory(prefix="t941-") as td:
        rows = _findings(Path(td))
    hits = _window(rows)
    assert len(hits) == 1, "expected exactly one brief-window finding, got %d: %r" % (len(hits), hits)
    names = _quoted_names(hits[0][3])
    assert len(names) >= 2, "the finding names %d definitions; a 40 KB file needs a copyable list: %r" % (len(names), hits[0][3])
    assert names <= REAL_NAMES, ("the finding names %s, which the file does not define; the file defines %s"
                                 % (sorted(names - REAL_NAMES), sorted(REAL_NAMES)))
    assert len(names) <= scope_lint.MAX_EDIT_SITES, (
        "the finding names %d symbols, more than MAX_EDIT_SITES=%d: the remedy would itself be brief-scatter"
        % (len(names), scope_lint.MAX_EDIT_SITES))


def test_finding_keeps_the_size_sentence_it_records_today():
    """Names are APPENDED: the text already in workspace/scope-quality.jsonl must survive."""
    with tempfile.TemporaryDirectory(prefix="t941-") as td:
        rows = _findings(Path(td))
    size = len(BIG_TEXT.encode("utf-8"))
    ev = _window(rows)[0][3]
    assert ev.startswith("big.py is %d bytes (> %d)" % (size, scope_lint.WINDOW_BYTES)), ev
    assert "and the brief names no SYMBOLS" in ev, ev


def test_a_file_at_or_under_the_threshold_gets_no_finding():
    """The fix must not turn the check into a blanket over every brief."""
    with tempfile.TemporaryDirectory(prefix="t941-") as td:
        repo = Path(td)
        (repo / "big.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        rows = scope_lint.static(_chunks(), repo)
    assert _window(rows) == [], "a file under the threshold was reported: %r" % (_window(rows),)


def test_an_append_chunk_on_a_large_non_python_file_still_exempts():
    """The append case (docs/LESSONS.md t668/t689, FLOW.json t798) must keep its exemption."""
    with tempfile.TemporaryDirectory(prefix="t941-") as td:
        repo = Path(td)
        (repo / "notes.md").write_text("# heading\n" + ("pad\n" * 20000), encoding="utf-8")
        chunk = dict(_chunks()[0])
        chunk["file"] = "notes.md"
        chunk["append"] = True
        rows = scope_lint.static([chunk], repo)
    assert _window(rows) == [], "an append chunk was reported: %r" % (_window(rows),)


def test_static_still_returns_four_tuples_in_order():
    """The finding shape (class, chunk index, file, evidence) is what scope_quality records."""
    with tempfile.TemporaryDirectory(prefix="t941-") as td:
        rows = _findings(Path(td))
    for row in rows:
        assert isinstance(row, tuple) and len(row) == 4, "row is not a 4-tuple: %r" % (row,)
        assert row[0] == "brief-window" and row[1] == 0 and row[2] == "big.py", row


def _run():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("ok   %s" % name)
        except Exception as e:  # noqa: BLE001 -- the failure text is the record; never swallowed
            failed += 1
            print("FAIL %s: %s: %s" % (name, type(e).__name__, e))
    print("%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
