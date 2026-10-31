#!/usr/bin/env python3
"""t1033 -- the t1033 oracle's own fixture must be a valid chunk, anchored on real code.

Why this suite exists (read 2026-10-19 from scripts/tests/test_chunks_predicate_single_source.py
and by running it at HEAD ad5676c9: 4 of 5 failed). Two of those four cannot be cured by any edit
to scripts/scope_lint.py, the one file the task's implementation chunk may touch:
  * IMPL_CHUNK declares "target": "scripts/tests/test_scope_lint.py" while its done_when names
    test_chunks_predicate_single_source.py, and bridge.validate (scripts/bridge.py:143) refuses a
    chunk whose done_when does not contain the target suite's file name. Every answer built from
    that fixture is therefore chunks-invalid (exit 2), so the oracle's two tests that demand a
    clean lint (exit 0) are unsatisfiable from any file. That is the park's root cause.
  * its ratchet test counts scripts/tests/test_scope_batch.py line 50 as a second definition. That
    file assembles a fence to feed scope_batch.split (its line 50 asserts a section keeps its own
    chunks block); a fixture that BUILDS the thing under test is not a consumer asking the
    question, and no edit to scope_lint.py can remove it, so the suite can never go green.

This suite reads both committed files from disk and pins the repair as VALUES, so the oracle's
repair is checkable and the scope_lint chunk is judged by a target that can go green.

Red on main today: test 1 (the fixture is not a valid chunk), test 2 (it cites scope_lint.py:522,
outside def main) and test 4 (no FENCE_TESTERS). Test 3 passes on main and must keep passing -- it
is a preservation assertion: the repaired fixture must be lint-clean.

Time-independent by construction: no writes, no subprocess, no model call, no live state, and the
one stored expectation (FENCE_TESTERS) names a file this task does not edit.
"""
import ast
import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import bridge      # noqa: E402
import scope_lint  # noqa: E402

ORACLE = REPO / "scripts" / "tests" / "test_chunks_predicate_single_source.py"
TARGET = "scripts/tests/test_chunks_predicate_single_source.py"
# The one tracked file outside bridge.py that asks the question with the fence string today, as
# reported by the oracle's own AST scan: scripts/tests/test_scope_batch.py, line 50. It builds a
# fence for scope_batch.split, so it is a fixture, not a second definition of the predicate.
FIXTURES = ("scripts/tests/test_scope_batch.py",)


def _oracle():
    """The committed t1033 oracle, loaded by path; its tests stay behind its __main__ guard."""
    spec = importlib.util.spec_from_file_location("t1033_oracle", ORACLE)
    assert spec is not None and spec.loader is not None, "cannot load " + str(ORACLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _main_range():
    """(first, last) line of def main in scripts/scope_lint.py, read from its own AST."""
    tree = ast.parse((REPO / "scripts" / "scope_lint.py").read_text(encoding="utf-8",
                                                                   errors="replace"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node.lineno, node.end_lineno
    raise AssertionError("def main is not in scripts/scope_lint.py: this suite's premise moved")


def test_the_fixture_chunk_is_a_valid_chunk():
    """The defect, as a value: bridge.py refuses the fixture, so the oracle is unsatisfiable."""
    m = _oracle()
    errs = bridge.validate([m.IMPL_CHUNK], REPO)
    assert errs == [], ("IMPL_CHUNK is refused by bridge.validate, so every answer built from it "
                        "is chunks-invalid and the oracle's exit-0 tests can never pass: "
                        + repr(errs))
    assert m.IMPL_CHUNK.get("target") == TARGET, ("the fixture's target must be the suite that "
                                                  "judges it, got " + repr(m.IMPL_CHUNK.get("target")))
    dw = str(m.IMPL_CHUNK.get("done_when"))
    assert Path(TARGET).name in dw, ("done_when must name the target suite (the rule at "
                                     "scripts/bridge.py:143): " + repr(dw))


def test_the_fixture_anchors_on_the_code_the_change_touches():
    """A brief anchored outside the function it changes is the brief-anchor class (t672): every
    line the fixture cites for scope_lint.py must sit inside def main, where the predicate lives.
    Computed from the real file's AST, never stored -- 522 is a line of some other function."""
    m = _oracle()
    first, last = _main_range()
    cites = set()
    for field in ("single_change", "what_is_wrong"):
        cites |= {int(x) for x in re.findall(r"scope_lint\.py:(\d{1,4})",
                                            str(m.IMPL_CHUNK.get(field, "")))}
    assert cites, ("the fixture cites no line of scope_lint.py, so its builder has no window to "
                   "anchor the edit to (t502)")
    outside = sorted(c for c in cites if not first <= c <= last)
    assert not outside, ("the fixture cites scope_lint.py lines " + repr(outside) + ", which are "
                         "outside def main (lines " + str(first) + "-" + str(last) + "); the "
                         "literal fence test this task removes is inside main")


def test_the_fixture_chunk_is_lint_clean():
    """The oracle's exit-0 assertions need a fixture that scope_lint's own static checks accept."""
    m = _oracle()
    findings = scope_lint.static([m.IMPL_CHUNK], REPO)
    assert findings == [], ("the fixture chunk trips scope_lint's own checks, so a clean lint is "
                            "unreachable for any implementation: " + repr(findings))


def test_the_ratchet_exempts_named_fixtures_and_nothing_else():
    """The inversion keeps its teeth: exemptions are enumerated by name, and each named file is
    checked to really be a fence-string user. Red today: FENCE_TESTERS does not exist at all."""
    m = _oracle()
    assert getattr(m, "FENCE_TESTERS", None) == FIXTURES, (
        "FENCE_TESTERS must be the tuple " + repr(FIXTURES) + " -- an exemption is named and "
        "attributable, never a wildcard; got " + repr(getattr(m, "FENCE_TESTERS", None)))
    text = ORACLE.read_text(encoding="utf-8", errors="replace")
    assert "rel in FENCE_TESTERS" in text, ("FENCE_TESTERS must be wired into the ratchet's loop, "
                                           "not merely declared: the scan must skip a rel when "
                                           "'rel in FENCE_TESTERS'")
    for rel in FIXTURES:
        p = REPO / rel
        assert p.is_file(), "the exempt path must exist: " + rel
        assert m._fence_tests(p), ("exempting " + rel + " is lawful only because it really does "
                                   "test the fence string as a fixture")


TESTS = (
    test_the_fixture_chunk_is_a_valid_chunk,
    test_the_fixture_anchors_on_the_code_the_change_touches,
    test_the_fixture_chunk_is_lint_clean,
    test_the_ratchet_exempts_named_fixtures_and_nothing_else,
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
    print(str(len(failed)) + " of " + str(len(TESTS)) + " failed")
    sys.exit(1 if failed else 0)
