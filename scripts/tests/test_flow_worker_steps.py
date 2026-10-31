#!/usr/bin/env python3
"""t1108 -- a FLOW task's .command must name every step of the script its .worker names.

workspace/rca/bc03ca25da18.md: session_end.py grew the step
"3b. Indexing this session into the corpus" (scripts/session_end.py:514) in commit cef3b02e,
and projects/local-inference/models/FLOW.json tasks[26].command never mentioned it. Nothing
could have noticed: scripts/flow_lint.py validates a FLOW EDIT against the chart (its
problems() at flow_lint.py:231 checks ids, links, outcomes, skills, helpers) and never opens
the script a task names. That is the rung-1 gap this suite pins.

A worker script's section("...") call sites, in file order, are its canonical step list; a
FLOW task's command string NAMES a step when it contains the step's leading number token
(3b, 5c) as a whole word. Every chart fixture below is synthetic and lives in a temp dir, so
updating FLOW.json cannot break this suite; the one real artefact it reads is session_end.py,
read-only.
"""
import tempfile
import traceback
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import flow_lint

REAL_SCRIPT = REPO / "scripts" / "session_end.py"
REAL_TITLE = "3b. Indexing this session into the corpus"
BEFORE_REAL = "3a. Did this session teach anything reusable?"
AFTER_REAL = "4. Full workspace check"


def _script(titles):
    """Source text of a fake worker script with one section() call per title, in order."""
    return "def main():\n" + "".join(f'    section("{t}")\n' for t in titles)


def _chart(tasks):
    return {"tasks": tasks, "links": []}


def test_worker_steps_finds_the_real_session_end_steps():
    """The script's own section() calls are the step list, read from the real file."""
    assert hasattr(flow_lint, "worker_steps"), "flow_lint has no worker_steps(source_text)"
    steps = flow_lint.worker_steps(REAL_SCRIPT.read_text(encoding="utf-8", errors="replace"))
    assert REAL_TITLE in steps, f"{REAL_TITLE!r} is a section() in session_end.py: {steps}"
    assert steps[0] == "1. What changed this session", f"first step is not step 1: {steps[0]!r}"
    assert steps[-1] == "8. Commit and push", f"last step is not step 8: {steps[-1]!r}"
    assert steps.index(BEFORE_REAL) < steps.index(REAL_TITLE) < steps.index(AFTER_REAL), \
        f"steps are not in call order: {steps}"


def test_worker_steps_keeps_call_order_and_dedupes():
    steps = flow_lint.worker_steps(_script(["1. Suites", "2. Handover", "2a. Audit", "3. Commit",
                                            "1. Suites"]))
    assert steps == ["1. Suites", "2. Handover", "2a. Audit", "3. Commit"], repr(steps)


def test_step_drift_reports_a_step_the_command_does_not_name():
    """The incident, as a property: a step exists in the script, the prose says nothing of it."""
    assert hasattr(flow_lint, "step_drift"), "flow_lint has no step_drift(command, steps)"
    titles = ["1. Suites", REAL_TITLE]
    command = ("session_end.py: suites (only those whose inputs changed, t461), handover drafted "
               "and refused while unfilled, commit, NAS bundle")
    findings = flow_lint.step_drift(command, titles)
    assert len(findings) == 1, f"only 3b is un-named by that command: {findings!r}"
    assert "3b" in findings[0], findings[0]
    assert REAL_TITLE in findings[0], f"the finding must name the step's title: {findings[0]!r}"
    assert "1. Suites" not in findings[0], f"step 1 is named by 'suites' prose: {findings[0]!r}"


def test_step_drift_is_silent_when_the_command_names_every_step():
    titles = ["1. Suites", REAL_TITLE, "5c. Claims that must still hold"]
    command = ("session_end.py: 1. suites whose inputs changed, 3b. corpus indexing, "
               "5c. claims that must still hold")
    findings = flow_lint.step_drift(command, titles)
    assert findings == [], f"a command that names every step must report nothing: {findings!r}"


def test_a_longer_number_does_not_name_a_shorter_step():
    """'13b' is not '3b'. Substring matching would pass a command that names a different step."""
    findings = flow_lint.step_drift("runs 13b of the audit", [REAL_TITLE])
    assert len(findings) == 1, f"13b must not count as naming 3b: {findings!r}"
    findings = flow_lint.step_drift("runs 3b of the audit", [REAL_TITLE])
    assert findings == [], f"3b is named by '3b': {findings!r}"


def test_step_drift_accepts_an_empty_command():
    titles = ["1. Suites", "2. Handover"]
    findings = flow_lint.step_drift("", titles)
    assert [f.split("'")[1] for f in findings] == ["1", "2"], repr(findings)


def test_worker_drift_checks_every_script_worker_on_the_chart():
    """A synthetic chart: the task, its script, and the finding that names all three."""
    assert hasattr(flow_lint, "worker_drift"), "flow_lint has no worker_drift(flow, repo)"
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / "scripts").mkdir()
        (repo / "scripts" / "my_worker.py").write_text(
            _script(["1. Suites", "2. Rebuild the index", "2a. Index the corpus"]),
            encoding="utf-8")
        flow = _chart([
            {"id": "session_end", "name": "Session end", "worker_kind": "script",
             "worker": "scripts/my_worker.py", "command": "my_worker.py: suites, commit"},
            {"id": "intake", "name": "Intake", "worker_kind": "script",
             "worker": "taskstore.add", "command": "feedback.py forms, triage.py"},
            {"id": "backlog", "name": "Backlog", "worker_kind": "store",
             "worker": "QUEUE.md", "command": "the backlog file"},
            {"id": "gone", "name": "Gone", "worker_kind": "script",
             "worker": "scripts/does_not_exist.py", "command": "nothing"},
        ])
        findings = flow_lint.worker_drift(flow, repo)
        assert len(findings) == 2, f"steps 2 and 2a are un-named by that command: {findings!r}"
        for f, key in zip(findings, ("2", "2a")):
            assert f.startswith("session_end (Session end): "), f
            assert "my_worker.py" in f, f"a finding must name the worker script: {f!r}"
            assert key in f, f
            assert "2. Rebuild the index" in f or "2a. Index the corpus" in f, f


def test_worker_drift_accepts_a_bare_script_name_and_is_quiet_when_the_chart_is_right():
    """FLOW.json writes worker as 'session_end.py', not a repo-relative path."""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / "scripts").mkdir()
        (repo / "scripts" / "my_worker.py").write_text(
            _script(["1. Suites", "3b. Index the corpus"]), encoding="utf-8")
        flow = _chart([{"id": "session_end", "name": "Session end", "worker_kind": "script",
                        "worker": "my_worker.py",
                        "command": "my_worker.py: 1. suites, 3b. corpus indexing"}])
        findings = flow_lint.worker_drift(flow, repo)
        assert findings == [], f"the command names 1 and 3b: {findings!r}"


def test_worker_drift_records_an_unreadable_script_instof_staying_quiet():
    """A handler that swallows the reason is the defect this repository already has a class for."""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / "scripts").mkdir()
        (repo / "scripts" / "dir_worker.py").mkdir()      # a path that exists but cannot be read
        flow = _chart([{"id": "audit", "name": "Script audit", "worker_kind": "script",
                        "worker": "scripts/dir_worker.py", "command": "1. audit"}])
        findings = flow_lint.worker_drift(flow, repo)
        assert len(findings) == 1, f"an unreadable worker must be reported, not skipped: {findings!r}"
        assert "dir_worker.py" in findings[0], findings[0]
        assert "IsADirectoryError" in findings[0] or "Error" in findings[0], \
            f"the finding must say WHY it could not read the script: {findings[0]!r}"


if __name__ == "__main__":
    failed = 0
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"PASS {_name}")
            except Exception as _exc:                      # noqa: BLE001 -- reported, never hidden
                failed += 1
                print(f"FAIL {_name}: {type(_exc).__name__}: {_exc}")
                traceback.print_exc()
    print(f"{failed} failed")
    sys.exit(1 if failed else 0)
