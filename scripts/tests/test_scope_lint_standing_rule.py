#!/usr/bin/env python3
"""t768 -- a brief that edits a standing-rule doc must name the Flow node it registers to.

RCA workspace/rca/4cad8165e8a8.md: the 2026-10-06 quantization rule was made permanent by editing
docs/PREFERENCES.md:96-107 with no FLOW.json node, no change row and no reader census. The scope
machinery already opens that file -- scope_lint.py reads every implementation chunk's target file
from disk for its anchor, window and scatter checks (scripts/scope_lint.py:214-215), and its lint
fired on docs/PREFERENCES.md for scope t567. So scope_lint is where the gate sits: an
implementation chunk whose target is a standing-rule file (scope_lint.STANDING_RULE_FILES) must
carry a top-level "flow" key naming a FLOW.json task node, and that id must resolve in the chart.

RED ON MAIN, for the missing change and nothing else: scope_lint has no STANDING_RULE_FILES and no
standing_rule_findings, so every test below raises AttributeError; the last test is red because
static() consults nothing about chart registration today.

Run: python3 scripts/tests/test_scope_lint_standing_rule.py
"""
import json
import pathlib
import sys
import tempfile
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]      # from scripts/tests/: the repo root
sys.path.insert(0, str(ROOT / "scripts"))

import scope_lint

PREFERENCES = "docs/PREFERENCES.md"
LESSONS = "docs/LESSONS.md"
ORDINARY = "scripts/scope_lint.py"
CHART = "projects/local-inference/models/FLOW.json"
REAL_NODE = "scoping"        # a task id in projects/local-inference/models/FLOW.json today
PHANTOM_NODE = "playbook"    # no such task id in that chart today

EXPECTED_POPULATION = ("docs/PREFERENCES.md", "docs/DECISIONS.md", "docs/LESSONS.md")


def _chart(tmp, ids):
    """A throwaway repo holding a FLOW.json whose task ids are exactly `ids`."""
    d = pathlib.Path(tmp) / "projects" / "local-inference" / "models"
    d.mkdir(parents=True, exist_ok=True)
    (d / "FLOW.json").write_text(json.dumps({"tasks": [{"id": i} for i in ids]}), encoding="utf-8")
    return pathlib.Path(tmp)


def _docs_chunk(extra=None):
    """A chunk that edits a standing-rule doc and declares nothing about the chart."""
    c = {"kind": "implementation", "file": PREFERENCES, "append": True,
         "single_change": "Withdraw the 2026-08-31 quantization rule (docs/PREFERENCES.md:96-107).",
         "what_is_wrong": "A permanent model-selection rule is made in prose, off the Flow chart.",
         "target": PREFERENCES,
         "done_when": "python3 scripts/tests/test_scope_lint_standing_rule.py exits 0"}
    c.update(extra or {})
    return c


def test_standing_rule_population_is_the_named_doc_files():
    assert tuple(scope_lint.STANDING_RULE_FILES) == EXPECTED_POPULATION
    assert ORDINARY not in scope_lint.STANDING_RULE_FILES


def test_standing_rule_target_without_a_flow_node_is_refused():
    got = scope_lint.standing_rule_findings([_docs_chunk()], ROOT)
    assert [g[0] for g in got] == ["brief-anchor"], got
    assert got[0][1] == 0, got
    assert got[0][2] == PREFERENCES, got
    assert "flow" in got[0][3] and "PREFERENCES.md" in got[0][3], got


def test_a_flow_node_that_resolves_in_the_chart_is_accepted():
    with tempfile.TemporaryDirectory() as td:
        repo = _chart(td, [REAL_NODE])
        assert scope_lint.standing_rule_findings([_docs_chunk({"flow": REAL_NODE})], repo) == []
    # the real chart, not a fixture: "scoping" is a task id in the file on disk
    assert scope_lint.standing_rule_findings([_docs_chunk({"flow": REAL_NODE})], ROOT) == []


def test_a_flow_node_that_does_not_resolve_is_refused_and_names_the_id():
    with tempfile.TemporaryDirectory() as td:
        repo = _chart(td, [REAL_NODE])
        got = scope_lint.standing_rule_findings([_docs_chunk({"flow": PHANTOM_NODE})], repo)
        assert [g[0] for g in got] == ["brief-anchor"], got
        assert "playbook" in got[0][3] and "FLOW.json" in got[0][3], got
        assert REAL_NODE not in got[0][3], got


def test_ordinary_files_are_not_gated():
    with tempfile.TemporaryDirectory() as td:
        repo = _chart(td, [REAL_NODE])
        assert scope_lint.standing_rule_findings(
            [{"kind": "implementation", "file": ORDINARY, "single_change": "x",
              "target": "scripts/tests/test_scope_lint_standing_rule.py"}], repo) == []
        assert scope_lint.standing_rule_findings(
            [{"kind": "implementation", "file": CHART, "single_change": "x"}], repo) == []
        assert scope_lint.standing_rule_findings(
            [{"kind": "implementation", "file": "scripts/tests/test_scope_lint_standing_rule.py",
              "single_change": "x"}], repo) == []


def test_a_docs_target_written_with_a_dot_prefix_is_still_gated():
    with tempfile.TemporaryDirectory() as td:
        repo = _chart(td, [REAL_NODE])
        got = scope_lint.standing_rule_findings(
            [{"kind": "implementation", "file": "./" + PREFERENCES, "append": True}], repo)
        assert [(g[0], g[2]) for g in got] == [("brief-anchor", PREFERENCES)], got


def test_an_unreadable_chart_is_recorded_not_swallowed():
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td)          # a repo with no FLOW.json under it at all
        got = scope_lint.standing_rule_findings([_docs_chunk({"flow": REAL_NODE})], repo)
        assert [g[0] for g in got] == ["brief-anchor"], got
        assert "unreadable" in got[0][3] and "FLOW.json" in got[0][3], got


def test_static_emits_the_finding_and_a_declared_flow_node_turns_it_off():
    plain = scope_lint.static([_docs_chunk()], ROOT)
    assert ("brief-anchor", PREFERENCES) in [(g[0], g[2]) for g in plain], plain
    wired = scope_lint.static([_docs_chunk({"flow": REAL_NODE})], ROOT)
    assert [g for g in wired if g[2] == PREFERENCES] == [], wired


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("ok   " + name)
        except Exception:
            failed += 1
            print("FAIL " + name)
            traceback.print_exc()
    print("%d/%d passed" % (len(tests) - failed, len(tests)))
    sys.exit(1 if failed else 0)
