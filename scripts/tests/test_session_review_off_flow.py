#!/usr/bin/env python3
"""t1236: 'off-flow' has ONE definition -- flow_coverage.uncovered() -- and it outranks the reviewer.

RCA workspace/rca/ce803b5625a8.md: a session review filed scripts/tests/test_infer.py as an
off-flow finding, calling a kind:test script a permanent test suite script -- which the system's
own definition (scripts/flow_coverage.py EXEMPT_KINDS, permanent()) forbids. This suite pins the
fix: a kind 'off-flow' finding whose script flow_coverage does not list is suppressed by code,
and the reviewer is shown the recorder's answer instead of asked to guess from chart prose.

Read-only by construction: nothing is written, no subprocess, no model call, no live state.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import flow_coverage        # noqa: E402  the system's own off-flow definition
import session_review       # noqa: E402


def _fn(name):
    """The symbol under test, named in the failure so a red suite says WHY it is red."""
    fn = getattr(session_review, name, None)
    if fn is None:
        raise AssertionError(
            f"session_review.{name} is not defined -- that is the change t1236 asks for: bind the "
            f"reviewer's kind 'off-flow' to flow_coverage.uncovered()")
    if not callable(fn):
        raise AssertionError(f"session_review.{name} exists but is not callable")
    return fn


def test_a_kind_test_script_is_suppressed_by_the_recorder():
    off = _fn("off_flow_findings")
    rec = flow_coverage.uncovered()
    assert "scripts/tests/test_infer.py" not in rec, (
        "fixture is stale: flow_coverage.uncovered() now lists scripts/tests/test_infer.py, so "
        "scripts/library.json no longer registers it as kind:test (flow_coverage.py:34 EXEMPT_KINDS)")
    rows = [{"kind": "off-flow",
             "quote": "A new 26-check suite for `infer` (scripts/tests/test_infer.py), where every check has a case that must fail.",
             "why_missed": "A new permanent test suite script was created to support the engine "
                           "layer, with no corresponding node on the Flow chart."}]
    kept, suppressed = off(rows, uncovered=[])
    assert kept == [], f"a kind:test script the recorder does not list must not be filed: {kept}"
    assert len(suppressed) == 1, f"a suppression must be recorded, not vanished: {suppressed}"
    why = str(suppressed[0].get("overruled", ""))
    assert "flow_coverage" in why, f"the arbiter must be cited by name: {why!r}"
    assert "test_infer.py" in why, f"the suppression must name the script it struck: {why!r}"


def test_a_script_the_recorder_does_list_is_kept():
    off = _fn("off_flow_findings")
    rows = [{"kind": "off-flow", "quote": "wired scripts/night_sweep_probe.py into the 02:00 timer",
             "why_missed": "the chart has no node for it"}]
    kept, suppressed = off(rows, uncovered=["scripts/night_sweep_probe.py"])
    assert len(kept) == 1, f"a script the recorder lists IS off-flow and must survive: {suppressed}"
    assert suppressed == []
    assert kept[0]["quote"] == rows[0]["quote"], "the kept finding must keep its own words"
    assert "overruled" not in kept[0], f"a kept finding must not be marked: {kept[0]}"


def test_a_script_that_is_on_the_chart_is_suppressed():
    off = _fn("off_flow_findings")
    on_chart = [p for p in flow_coverage.permanent() if flow_coverage.on_chart(p)]
    assert on_chart, "fixture: no permanent script is on the chart -- flow_coverage or FLOW.json changed"
    probe = on_chart[0]
    assert probe not in flow_coverage.uncovered(), f"{probe} is on the chart and off it at once"
    rows = [{"kind": "off-flow", "quote": f"changed {probe} to run the sweep nightly",
             "why_missed": "no node names it"}]
    kept, suppressed = off(rows)
    assert kept == [], f"a script on the chart is not off-flow, and it passed the arbiter: {kept}"
    assert len(suppressed) == 1


def test_a_finding_naming_no_script_survives():
    off = _fn("off_flow_findings")
    rows = [{"kind": "off-flow",
             "quote": "we now run the preflight on every brief before dispatching it",
             "why_missed": "no chart node for the procedure"}]
    kept, suppressed = off(rows, uncovered=[])
    assert len(kept) == 1, f"a procedure finding is the recorder's silence, not its veto: {suppressed}"
    assert suppressed == []


def test_only_kind_off_flow_is_arbitrated():
    off = _fn("off_flow_findings")
    rows = [{"kind": "deferral", "quote": "will wire scripts/night_sweep_probe.py next week",
             "why_missed": "parked"},
            {"kind": "for-jas", "quote": "should scripts/night_sweep_probe.py be a timer?",
             "why_missed": "waits on Jas"},
            {"kind": "unresolved", "quote": "scripts/night_sweep_probe.py failed at 02:00",
             "why_missed": "not fixed"}]
    kept, suppressed = off(rows, uncovered=[])
    assert len(kept) == 3, f"only kind 'off-flow' may be suppressed: {kept}"
    assert suppressed == []


def test_the_callers_rows_are_not_mutated():
    off = _fn("off_flow_findings")
    rows = [{"kind": "off-flow", "quote": "created scripts/tests/test_infer.py today",
             "why_missed": "not on the chart"}]
    off(rows, uncovered=[])
    assert all("overruled" not in r and "off_flow_arbiter" not in r for r in rows), (
        f"the caller's rows were written to; _keep files a finding from every row it is handed: {rows}")


def test_the_prompt_defines_off_flow_by_the_recorder():
    m = re.search(r'\(c\) "off-flow":(.*?)\n\s*\(d\) "for-jas"', session_review.EXTRACT, re.S)
    assert m, 'EXTRACT has no (c) "off-flow" clause ending at (d) "for-jas" -- re-read EXTRACT (:374)'
    clause = m.group(1)
    assert "flow_coverage" in clause, (
        f'the (c) clause still defines off-flow from the chart prose alone: {clause[:300]!r}')
    assert "/tests/" in clause and "kind" in clause, (
        f'the (c) clause must exempt a kind:test script (flow_coverage.py:34 EXEMPT_KINDS): {clause[:300]!r}')


def test_flow_summary_shows_the_recorders_answer():
    s = session_review.flow_summary()
    assert "TASKS:" in s and "SUPPORTING SCRIPTS:" in s, "flow_summary lost the lines the reviewer needs"
    assert "OFF-CHART" in s, f"flow_summary() does not show the reviewer the arbiter's answer: {s[:200]!r}"
    section = s.split("OFF-CHART", 1)[1]
    listed = set(re.findall(r"[A-Za-z0-9_./-]+\.(?:py|sh)", section))
    rec = set(flow_coverage.uncovered())
    assert listed == rec, (
        f"flow_summary lists {sorted(listed - rec)} that flow_coverage.uncovered() does not, and "
        f"omits {sorted(rec - listed)} -- the reviewer is shown a definition that is not the recorder's")
    assert "test_infer.py" not in section, "flow_summary tells the reviewer a kind:test suite is off-flow"


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    assert len(tests) >= 8, f"the suite lost tests: only {len(tests)} found"
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:                                       # a crash is a failure, with its type
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
