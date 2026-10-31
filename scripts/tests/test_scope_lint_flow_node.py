#!/usr/bin/env python3
"""t1207 -- a brief that creates standing behaviour must name its Flow node.

RCA workspace/rca/check-b06506c7eded.md: commit 708ab41f (2026-10-14 21:46Z) armed
scoping-queue@auditor.service, registered the scripts and the role, and wrote nothing to the Flow
chart; the hourly check "Every standing process is on the Flow chart" went red 20 minutes
later. scope_lint already refuses an unregistered edit to a standing-rule DOC
(standing_rule_findings, scripts/scope_lint.py:213, t768). Nothing on that path speaks about
standing BEHAVIOUR -- a systemd unit, a timer, a hook, a crontab, the script registry. This
oracle pins that clause: an implementation chunk whose file is a standing-behaviour target
must carry a flow key naming a node in projects/local-inference/models/FLOW.json, and a node
that is not in the chart is refused too.

RED ON MAIN for the missing change and nothing else: scope_lint has no
standing_behaviour_findings and no STANDING_BEHAVIOUR_TARGETS, so every test that calls them
raises AttributeError; the last test is red because static() consults nothing about chart
registration today. No live state is read: every chart is built inside a temp dir.

Run: python3 scripts/tests/test_scope_lint_flow_node.py
"""
import json
import pathlib
import sys
import tempfile
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]      # from scripts/tests/: the repo root
sys.path.insert(0, str(ROOT / "scripts"))

import scope_lint

CHART_IDS = ("scoping", "manager-queue")   # the ids placed in the throwaway chart below
PHANTOM = "auditor-scoping-loop"          # a plausible node id that is NOT in that chart

UNIT = "deploy/systemd/scoping-queue@auditor.service"
TIMER = "deploy/systemd/night-queue.timer"
HOOK = "scripts/hooks/pre-commit"
REGISTRY = "scripts/library.json"
CRON = "deploy/crontab"
ORDINARY = "scripts/scope_lint.py"


def _repo_with_chart(tmp, ids):
    """A throwaway repo root whose FLOW.json carries task ids exactly == ids."""
    d = pathlib.Path(tmp) / "projects" / "local-inference" / "models"
    d.mkdir(parents=True, exist_ok=True)
    (d / "FLOW.json").write_text(json.dumps({"tasks": [{"id": i} for i in ids]}), encoding="utf-8")
    return pathlib.Path(tmp)


def _chunk(file, **extra):
    c = {"kind": "implementation", "file": file, "target": ORDINARY,
         "single_change": "Install the Auditor scoping loop as a user unit.",
         "what_is_wrong": "A standing process is armed with no node on the Flow chart.",
         "done_when": "python3 scripts/tests/test_scope_lint_flow_node.py exits 0"}
    c.update(extra)
    return c


def _flow_findings(out):
    """Only the flow-registration findings: static() also reports file-existence rows."""
    return [f for f in out if f[0] == "brief-anchor" and "flow node" in f[3]]


def test_population_is_the_named_standing_behaviour_targets():
    assert tuple(scope_lint.STANDING_BEHAVIOUR_TARGETS) == (
        "deploy/systemd/", "scripts/hooks/", "scripts/library.json", "crontab")


def test_unit_write_with_no_flow_node_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo_with_chart(tmp, CHART_IDS)
        out = scope_lint.standing_behaviour_findings([_chunk(UNIT)], repo)
    assert [f[0] for f in out] == ["brief-anchor"]
    assert out[0][1] == 0
    assert out[0][2] == UNIT
    assert "flow node" in out[0][3]


def test_timer_hook_registry_and_crontab_all_trigger_the_requirement():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo_with_chart(tmp, CHART_IDS)
        chunks = [_chunk(TIMER), _chunk(HOOK), _chunk(REGISTRY), _chunk(CRON)]
        out = scope_lint.standing_behaviour_findings(chunks, repo)
    assert [f[1] for f in out] == [0, 1, 2, 3]
    assert [f[2] for f in out] == [TIMER, HOOK, REGISTRY, CRON]


def test_a_target_is_accepted_when_its_node_is_in_the_chart():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo_with_chart(tmp, CHART_IDS)
        out = scope_lint.standing_behaviour_findings([_chunk(UNIT, flow_node="scoping")], repo)
    assert out == []


def test_the_flow_key_is_accepted_as_the_parity_spelling():
    # standing_rule_findings reads the key "flow" (scripts/scope_lint.py:232): a chunk written
    # in that spelling (t768's contract) must not be refused by the new check for using it.
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo_with_chart(tmp, CHART_IDS)
        out = scope_lint.standing_behaviour_findings([_chunk(UNIT, flow="manager-queue")], repo)
    assert out == []


def test_a_flow_node_that_is_not_in_the_chart_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo_with_chart(tmp, CHART_IDS)
        out = scope_lint.standing_behaviour_findings([_chunk(UNIT, flow_node=PHANTOM)], repo)
    assert [f[0] for f in out] == ["brief-anchor"]
    assert PHANTOM in out[0][3]
    assert "not a task node" in out[0][3]


def test_a_missing_chart_is_recorded_not_silently_passed():
    with tempfile.TemporaryDirectory() as tmp:
        repo = pathlib.Path(tmp)          # a repo with no FLOW.json at all
        out = scope_lint.standing_behaviour_findings([_chunk(UNIT, flow_node="scoping")], repo)
    assert [f[0] for f in out] == ["brief-anchor"]
    assert "unreadable" in out[0][3]
    assert "FLOW.json" in out[0][3]


def test_an_ordinary_file_is_not_in_scope_for_the_requirement():
    # NEGATIVE assertion: a check that fired on every target would pass vacuously on the real one.
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo_with_chart(tmp, CHART_IDS)
        out = scope_lint.standing_behaviour_findings([_chunk(ORDINARY)], repo)
    assert out == []


def test_static_reports_the_finding_so_the_gate_actually_runs():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo_with_chart(tmp, CHART_IDS)
        out = scope_lint.static([_chunk(UNIT)], repo)
    hits = _flow_findings(out)
    assert len(hits) == 1, [f[3] for f in out]
    assert hits[0][1] == 0
    assert hits[0][2] == UNIT


def _main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = []
    for fn in tests:
        try:
            fn()
        except Exception:
            failed.append(fn.__name__)
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
