#!/usr/bin/env python3
"""t800 -- check_workspace's flow check must consult the tracked-settings hooks population.

WHY (RCA 559ecdabbba7 SS2, DE-5): the session-end blocker `check_workspace.py` answers "Every
permanent script is on the Flow chart" from scripts/library.json alone (`check_flow_record`,
scripts/check_workspace.py:1486-1509). `.claude/settings.json` is TRACKED, so a hook is a permanent
process the next session gets with nothing armed -- and no gate reads it. A hook added, removed, or
pointed off-chart keeps the blocker green.

RED TODAY BECAUSE: check_workspace.py defines no `hooks_flow_findings` (grep -rn
'hooks_flow_findings' scripts/ returns no hits). The suite imports the real module by its real path
-- it imports in 0.04s and prints nothing -- and calls the real function; a stub satisfies nothing.

THE CONTRACT (build exactly this name -- do not invent another):
    hooks_flow_findings(repo, settings_dir=None, flow=None) -> list[str]
One finding per hook that is not on the chart; a finding naming the empty population when there are
no hooks; a finding naming the file when a settings file cannot be read or parsed.

`flow` is a FLOW.json dict. Omitting it reads the real one, exactly as `flow_coverage.uncovered()`
does, so the real repo answers with real data.
"""
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CW_PATH = ROOT / "scripts" / "check_workspace.py"
PROBE = "scripts/hooks/guard_synthetic_probe.py"
DECOYS = ("context_watch", "guard_task_writes", "guard_process_waits")

_spec = importlib.util.spec_from_file_location("check_workspace_under_test", CW_PATH)
CW = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CW)

EMPTY_FLOW = {"tasks": [], "links": [], "sources": [], "services": [], "helpers": []}


def _flow_with(path):
    return {"tasks": [], "links": [], "sources": [], "services": [],
            "helpers": [{"script": path, "serves": ["t800"], "what": "fixture"}]}


def _repo(td, settings_text, flow):
    """A throwaway repo: .claude/settings.json, scripts/library.json, FLOW.json. Nothing real touched."""
    r = Path(td)
    (r / ".claude").mkdir(parents=True)
    (r / ".claude" / "settings.json").write_text(settings_text, encoding="utf-8")
    (r / "scripts").mkdir()
    (r / "scripts" / "library.json").write_text(json.dumps({"scripts": {}}), encoding="utf-8")
    (r / "projects" / "local-inference" / "models").mkdir(parents=True)
    (r / "projects" / "local-inference" / "models" / "FLOW.json").write_text(
        json.dumps(flow), encoding="utf-8")
    return r


def _findings(settings_text, flow):
    with tempfile.TemporaryDirectory() as td:
        return CW.hooks_flow_findings(_repo(td, settings_text, flow))


_ONE_HOOK = json.dumps({"hooks": {"PreToolUse": [{"matcher": "Write|Edit", "hooks": [
    {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR/%s" --hook' % PROBE}]}]}})


def test_the_function_exists_and_returns_a_list_of_strings():
    fn = getattr(CW, "hooks_flow_findings", None)
    assert callable(fn), (
        "check_workspace.py needs a module-level callable `hooks_flow_findings(repo, "
        "settings_dir=None, flow=None)` -- there is no such name today")
    with tempfile.TemporaryDirectory() as td:
        out = fn(_repo(td, json.dumps({"permissions": {"deny": []}}), EMPTY_FLOW))
    assert isinstance(out, list), f"must return a list of finding strings; got {type(out).__name__}"
    assert all(isinstance(x, str) and x.strip() for x in out), (
        f"every finding must be a non-empty string: {out!r}")


def test_a_hook_that_is_not_on_the_chart_is_a_finding():
    out = _findings(_ONE_HOOK, EMPTY_FLOW)
    joined = "\n".join(out)
    assert len(out) == 1, f"one unregistered hook is exactly one finding; got {out!r}"
    assert PROBE in joined, f"the finding must name the off-chart script verbatim; got {joined!r}"
    assert "flow" in joined.lower(), (
        f"the finding must say what is wrong (not on the Flow chart); got {joined!r}")


def test_a_hook_named_as_a_chart_helper_is_not_a_finding():
    """The SAME input with the chart informed -> no finding. Proves the check reads the chart;
    a fixed message cannot pass both this test and the one above."""
    out = _findings(_ONE_HOOK, _flow_with(PROBE))
    assert out == [], f"a hook the chart names as a helper must produce NO finding; got {out!r}"


def _decoys_absent(joined):
    """Negative assertion, both directions: a build that greps prose, hardcodes today's three
    hooks, or reports the chart's whole helper list fails here. The fixture named ONE script."""
    for decoy in DECOYS:
        assert decoy not in joined, (
            f"the finding must report what the settings file says -- {decoy!r} is not configured "
            f"in this fixture; got {joined!r}")


def test_the_finding_names_the_configured_script_and_nothing_invented():
    joined = "\n".join(_findings(_ONE_HOOK, EMPTY_FLOW))
    assert "guard_synthetic_probe" in joined, (
        f"the finding must name the configured hook; got {joined!r}")
    _decoys_absent(joined)


def test_the_finding_carries_the_remedy_the_other_flow_checks_use():
    """Asserted on BOTH sides so a paraphrase cannot pass: the finding must tell a reader how to
    put the hook on the chart, in the form the permanent-script check's FAIL detail uses."""
    joined = "\n".join(_findings(_ONE_HOOK, EMPTY_FLOW))
    assert "flow_change.py" in joined and "helper" in joined, (
        f"the finding must carry the remedy `flow_change.py helper <path> <task-id>`; got {joined!r}")
    _decoys_absent(joined)


def test_a_zero_hooks_population_is_a_finding_not_a_pass():
    """t723 precedent: a census that read nothing must not report success. The task's own words:
    'FAIL on a zero-size hooks population'."""
    out = _findings(json.dumps({"permissions": {"deny": []}}), EMPTY_FLOW)
    joined = " ".join(out)
    assert len(out) == 1, f"an empty hooks population is exactly one finding; got {out!r}"
    assert "hooks" in joined.lower(), f"the finding must say hooks; got {joined!r}"
    assert "zero" in joined.lower(), f"the finding must say the population is zero; got {joined!r}"


def test_an_unparseable_settings_file_is_a_finding_that_names_the_file():
    """swallowed-exception-introduced: the caught failure must reach the finding text, naming the
    file. A build with `except Exception: pass` returns [] and fails here."""
    out = _findings("{ not json", EMPTY_FLOW)
    joined = " ".join(out)
    assert out, "an unparseable settings file must produce a finding, not silence"
    assert "settings.json" in joined, f"the failing file must be named; got {joined!r}"


def test_the_census_is_wired_into_the_blocking_check():
    """A function nobody calls is a written instruction wearing a lab coat
    (docs/skills/planning-for-sustainment). Requiring the name at least TWICE in the source pins
    definition AND call site: one occurrence would be a definition that is never run."""
    src = CW_PATH.read_text(encoding="utf-8")
    assert src.count("hooks_flow_findings(") >= 2, (
        "hooks_flow_findings must be DEFINED and CALLED from inside check_flow_record "
        "(scripts/check_workspace.py:1486) -- a census nothing runs fires on nothing")


if __name__ == "__main__":
    _tests = sorted((n, f) for n, f in list(globals().items())
                    if n.startswith("test_") and callable(f))
    _bad = []
    for _n, _f in _tests:
        try:
            _f()
            print(f"ok   {_n}")
        except AssertionError as _e:
            print(f"FAIL {_n}: {_e}")
            _bad.append(_n)
        except Exception as _e:  # noqa: BLE001
            print(f"ERROR {_n}: {type(_e).__name__}: {_e}")
            _bad.append(_n)
    print(f"{len(_tests) - len(_bad)} of {len(_tests)} passed")
    raise SystemExit(1 if _bad else 0)
