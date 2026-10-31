#!/usr/bin/env python3
"""t1033 -- bridge.py is the ONLY definition of 'this answer has a chunks block'.

The predicate has three implementations in HEAD: scripts/bridge.py owns it (NO_BLOCK at
scripts/bridge.py:25, parse() at scripts/bridge.py:36 -- and parse accepts a PLAIN ```json fence
whose list is all oracle/implementation chunks, scripts/bridge.py:47-51, the t264 shape),
scripts/bridge_trigger.py was pointed at it on 2026-09-30 (bridge_trigger.py:101-105), and
scripts/scope_lint.py:522 is the third copy, a literal string test, eleven days later. Its
consequence is what these tests pin: a scope whose plan is ```json is exited 1 with 'no chunks
block' and is NEVER LINTED, in silence -- scope_lint.py:522-524 prints and returns without
writing a row, so the skipped scope is a header with an empty body.

Five tests, run by the __main__ block below (pytest is not installed):
  1. a plain ```json chunks plan is LINTED (exit 0, no 'no chunks block' in its output)
  2. a genuine no-block answer exits 1, says so, and writes EXACTLY ONE scope_quality row with
     stage/outcome/task/basis/reason set and no defect_class (a count, not an RCA)
  3. a ```chunks fence that is not JSON is STILL linted (exit 2, chunks-invalid visible) --
     single-sourced must not mean 'skips more'
  4. an answer that has a block writes no skip row
  5. the inversion, so a fourth consumer cannot be written: no tracked .py file outside
     scripts/bridge.py asks the question with a fence string tested by `in`/`not in`

Red on main for the stated reason: 1 exits 1, 2 writes zero rows, 5 names scripts/scope_lint.py.
3 and 4 pass on main and must keep passing. No live state: temp dirs only, one subprocess with a
timeout, scope_quality.record monkeypatched so the real ledger is never written.
"""
import ast
import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import scope_lint      # noqa: E402
import scope_quality   # noqa: E402

TASK = "t1033-fixture"
FENCE_HOME = "scripts/bridge.py"
# A fence string used to ASK the question: a string literal that begins with a code fence and
# names chunks, on either side of an `in` / `not in` comparison. Prose and fixtures mention the
# fence; they do not test with it. bridge.py's own parse() uses a regex, not a membership test,
# so the allowlist entry is inert -- documented so nobody 'fixes' it into an exemption.
FENCE_TEST = re.compile(r"^```[\w-]*\s*chunks", re.I)

# A real, complete implementation chunk: what this very scope files. A chunk with no SYMBOLS and
# no cited line would trip scope_lint's own brief-unanchored check, so it carries both.
IMPL_CHUNK = {
    "kind": "implementation",
    "file": "scripts/scope_lint.py",
    "target": "scripts/tests/test_scope_lint.py",
    "single_change": "Replace the literal fence test at scope_lint.py:522 with bridge.parse, "
                     "comparing errors to bridge.NO_BLOCK exactly as bridge_trigger.py:103-105 does.",
    "what_is_wrong": "scope_lint.py:522 asks 'has a chunks block' with `\"```chunks\" not in "
                     "text:`, which drops an answer whose plan is a plain ```json chunks list -- "
                     "the shape bridge.parse accepts.",
    "done_when": "python3 scripts/tests/test_chunks_predicate_single_source.py exits 0",
    "related_entries": ["chunks-invalid"],
}


def _answer_text(block):
    """An answer whose plan is a plain ```json fence: t264's shape, which bridge.parse accepts."""
    return "## For Claude\n\nFiled below.\n\n```json\n" + json.dumps(block) + "\n```\n"


def _run_main(answer, extra):
    """scope_lint's entry point on a real answer file. Returns (exit code, everything it printed)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = scope_lint.main([str(answer), "--task", TASK] + extra)
    return rc, buf.getvalue()


_REAL_RECORD = {}


def _capture_records():
    """Replace scope_quality.record so the real ledger is never touched; collect its calls."""
    rows = []
    names = ["stage", "outcome", "task", "chunk", "defect_class", "evidence", "model", "scope",
             "source"]

    def fake(*args, **kwargs):
        rec = dict(zip(names, args))
        rec.update(kwargs)
        rows.append(rec)
        return rec

    _REAL_RECORD["record"] = scope_quality.record
    scope_quality.record = fake
    return rows


def _release_records():
    scope_quality.record = _REAL_RECORD.pop("record")


def _fence_tests(path):
    """Line numbers where `path` asks 'has a chunks block' with a fence string and a membership test."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return []
    hits = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            continue
        for side in [node.left] + list(node.comparators):
            if (isinstance(side, ast.Constant) and isinstance(side.value, str)
                    and FENCE_TEST.match(side.value)):
                hits.add(node.lineno)
    return sorted(hits)


def _tracked_py():
    out = subprocess.run(["git", "ls-files", "-z", "*.py"], cwd=str(REPO), capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, f"git ls-files exited {out.returncode}: {out.stderr!r}"
    return [p for p in out.stdout.split("\0") if p]


def test_plain_json_plan_is_linted_not_skipped():
    """THE DEFECT: the literal test drops the answer bridge.parse accepts (t264's shape)."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "answer.md"
        f.write_text(_answer_text([IMPL_CHUNK]), encoding="utf-8")
        rc, out = _run_main(f, ["--no-run"])
    assert rc == 0, ("scope_lint exited " + str(rc) + " on an answer whose plan is a plain "
                    "```json chunks list; bridge.parse accepts it, so the lint must run: "
                    + repr(out))
    assert "no chunks block" not in out, ("the literal predicate still fired on a ```json "
                                          "chunks plan: " + repr(out))


def test_genuine_no_block_answer_is_recorded_once():
    """A prose answer is not an error -- but a SKIPPED lint is a fact, so it writes one row."""
    captured = _capture_records()
    try:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "answer.md"
            f.write_text("## For Claude\n\nNo machine block in this answer.\n", encoding="utf-8")
            rc, out = _run_main(f, ["--record"])
    finally:
        _release_records()
    assert rc == 1, "a no-block answer must still exit 1, got " + str(rc) + ": " + repr(out)
    assert "no chunks block" in out, "the printed reason must survive: " + repr(out)
    rows = [r for r in captured if r.get("event") == "scope-lint-skipped"]
    assert len(rows) == 1, ("one skipped lint must write exactly one scope-lint-skipped row; "
                            "captured " + str(len(captured)) + " calls: " + repr(captured))
    row = rows[0]
    assert row.get("stage") == "lint", "stage must be 'lint', got " + repr(row.get("stage"))
    assert row.get("outcome") == "skipped", "outcome must be 'skipped', got " + repr(row.get("outcome"))
    assert row.get("task") == TASK, "the row must name the task, got " + repr(row.get("task"))
    assert row.get("basis") == "bridge.parse", ("basis must name who decided the skip, got "
                                                + repr(row.get("basis")))
    assert isinstance(row.get("reason"), str) and row.get("reason"), (
        "the row must carry a non-empty reason, got " + repr(row.get("reason")))
    assert not row.get("defect_class"), ("a skipped lint is a count, not a defect: a non-empty "
                                         "defect_class mints an RCA per prose answer: " + repr(row))


def test_a_malformed_chunks_fence_is_still_linted():
    """Single-sourced must not mean 'skips more': an unparseable ```chunks fence is a finding."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "answer.md"
        f.write_text("## For Claude\n\n```chunks\n[{not json}]\n```\n", encoding="utf-8")
        rc, out = _run_main(f, ["--no-run"])
    assert rc == 2, ("a ```chunks fence that is not JSON must be linted and refused (exit 2), got "
                    + str(rc) + ": " + repr(out))
    assert "chunks-invalid" in out, "the refusal must name its class: " + repr(out)


def test_an_answer_with_a_block_writes_no_skip_row():
    """The skip row is for the skip, not for every lint -- otherwise the count is noise."""
    captured = _capture_records()
    try:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "answer.md"
            f.write_text(_answer_text([IMPL_CHUNK]), encoding="utf-8")
            rc, out = _run_main(f, ["--record", "--no-run"])
    finally:
        _release_records()
    assert rc == 0, "expected a clean lint (exit 0), got " + str(rc) + ": " + repr(out)
    skipped = [r for r in captured if r.get("event") == "scope-lint-skipped"]
    assert skipped == [], ("an answer that HAS a block must not write a skip row: " + repr(skipped))


def test_no_tracked_file_outside_bridge_asks_with_the_string():
    """THE FIX THAT KEEPS ITSELF: the predicate is asked with a string in exactly one file.

    Not 'the string ```chunks appears in one file' -- it appears in ~25 tracked files, in
    docstrings, in ask_q16's prompt text, in manager_note's re-scope message and in the fixtures
    that build a fence to test the parser. What is unique, and is the defect, is USING the fence
    string as a test. That is the set this pins, and scripts/bridge.py is allowed to be empty in
    it because parse() asks with a regex.
    """
    offenders = {}
    for rel in _tracked_py():
        p = REPO / rel
        if not p.is_file() or rel == FENCE_HOME:
            continue
        hits = _fence_tests(p)
        if hits:
            offenders[rel] = hits
    assert offenders == {}, ("only " + FENCE_HOME + " may ask 'has a chunks block' by testing the "
                            "fence string; bridge.py owns the predicate, so route the consumer "
                            "through bridge.parse -- found: " + repr(offenders))


TESTS = (
    test_plain_json_plan_is_linted_not_skipped,
    test_genuine_no_block_answer_is_recorded_once,
    test_a_malformed_chunks_fence_is_still_linted,
    test_an_answer_with_a_block_writes_no_skip_row,
    test_no_tracked_file_outside_bridge_asks_with_the_string,
)


if __name__ == "__main__":
    failed = []
    for t in TESTS:
        try:
            t()
            print("PASS " + t.__name__)
        except Exception as e:  # noqa: BLE001 -- printed, counted, and the exit code says so
            failed.append(t.__name__)
            print("FAIL " + t.__name__ + ": " + type(e).__name__ + ": " + str(e))
            traceback.print_exc()
    print(str(len(failed)) + " of " + str(len(TESTS)) + " failed")
    sys.exit(1 if failed else 0)
