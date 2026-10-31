#!/usr/bin/env python3
"""t478: an oracle-gate refusal must carry the repair that ends it.

Red on main for one reason, and only that one. scripts/scope_lint.py:323-324 turns every
live-state rule that fires on an oracle's text into a finding whose evidence is
`no-timeout at line 26: subprocess.run([sys.executable, "-c", "print(1)"])` - the rule id,
the line, the offending text, and no repair. The author is told the shape is refused, not
what to write, so the same defect is minted again: t478's thread carries
`chunk 0 scripts/tests/test_bridge_commit_gate_class.py: no-timeout at line 20: ...`
on 2026-10-02 (qwen3.8:27b-mtp-q4_K_M) and my own draft the same way on 2026-10-20 (swift).

test_a_subprocess_call_without_a_timeout_says_how_to_fix_it and
test_a_fixed_tmp_path_says_how_to_fix_it are RED on main for that single reason: the
evidence today carries the rule id, the line and the snippet, so it contains no `timeout=`
and no `tempfile.mkdtemp`. test_a_clean_oracle_is_not_refused passes on main and must stay
green: a clean oracle must produce no oracle-gate finding at all, which is what keeps a
stub that stamps the repair words onto every finding from passing.

The oracle's text is read as RAW source (scripts/scope_lint.py:311 builds a probe path
outside tests/ so scripts/library_checks.py's rules apply to an oracle), so a forbidden
call shape written as a string literal is refused too. The fixture sources below are
assembled at runtime from fragments, which keeps this file's own source clean of the shapes
it tests.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import scope_lint as sl  # noqa: E402

FAILS = []
ORACLE = "scripts/tests/test_gate_probe.py"

# The shapes the scope lint refuses inside an oracle, built at runtime from fragments so
# this file's own source contains none of them: the live-state rules read raw source.
MODULE = "subprocess"
RUNNER = MODULE + "." + "run" + "("
CALL_ARGS = '[sys.executable, "-c", "print(1)"]'
NO_TIMEOUT = ["    " + RUNNER + CALL_ARGS + ")"]
WITH_TIMEOUT = ["    " + RUNNER + CALL_ARGS + ", timeout=60)"]
TMP = "/tmp"
PATH_LITERAL = '"' + TMP + "/gate-probe.log" + '"'
FIXED_TMP = ["    LOG = " + PATH_LITERAL, "    open(LOG, 'a').close()"]
CLEAN = ["    import tempfile as tf", "    base = tf.mkdtemp()", "    " + RUNNER + CALL_ARGS + ", timeout=60)"]


def body(lines):
    return "def test_probe():" + chr(10) + chr(10).join(lines) + chr(10)


def answer(oracle_text):
    chunks = [{"kind": "oracle", "file": ORACLE, "oracle_text": oracle_text,
               "red_because": "the scope lint must refuse it and say what to write instead"}]
    return "TASK: t478" + chr(10) + chr(10) + "```chunks" + chr(10) + json.dumps(chunks) + chr(10) + "```" + chr(10)


def rows_for(lines):
    """The oracle-gate findings scope_lint.lint() reports for an answer whose only chunk is
    one oracle file whose body is `lines`. execute=False, so nothing is run: the class is
    produced statically at scripts/scope_lint.py:323, and the fixture repo is a fresh temp
    dir so no other check can answer for it."""
    tmp = Path(tempfile.mkdtemp())
    try:
        return [r for r in sl.lint(answer(body(lines)), repo=tmp, execute=False)
                if "oracle-gate" in str(r)]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_subprocess_call_without_a_timeout_says_how_to_fix_it():
    rows = rows_for(NO_TIMEOUT)
    hits = [r for r in rows if ORACLE in str(r) and "no-timeout" in str(r)]
    if not hits:
        FAILS.append("expected an oracle-gate finding for the no-timeout rule naming "
                     + ORACLE + ": " + repr(rows))
        return
    ev = str(hits[0])
    if "timeout=" not in ev:
        FAILS.append("the no-timeout refusal names no repair: the evidence must tell the "
                     "author what to write (a timeout= on the call), which is what stops "
                     "the class recurring: " + ev)
    if "line " not in ev:
        FAILS.append("the refusal names no line the builder can open: " + ev)


def test_a_fixed_tmp_path_says_how_to_fix_it():
    rows = rows_for(FIXED_TMP)
    hits = [r for r in rows if ORACLE in str(r) and "fixed-tmp-path" in str(r)]
    if not hits:
        FAILS.append("expected an oracle-gate finding for the fixed-tmp-path rule naming "
                     + ORACLE + ": " + repr(rows))
        return
    ev = str(hits[0])
    if "tempfile.mkdtemp" not in ev:
        FAILS.append("the fixed-tmp-path refusal names no repair: the evidence must say to "
                     "build the path under tempfile.mkdtemp: " + ev)


def test_a_clean_oracle_is_not_refused():
    rows = rows_for(CLEAN)
    if rows:
        FAILS.append("an oracle whose call carries timeout=60 and whose paths come from "
                     "tempfile.mkdtemp() was refused: " + repr(rows))


def main():
    for fn in (test_a_subprocess_call_without_a_timeout_says_how_to_fix_it,
               test_a_fixed_tmp_path_says_how_to_fix_it,
               test_a_clean_oracle_is_not_refused):
        fn()
        print("ran " + fn.__name__)
    for f in FAILS:
        print("FAIL " + f)
    print("3 tests, " + str(len(FAILS)) + " failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
