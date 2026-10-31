#!/usr/bin/env python3
"""t800 -- the Flow census must include every hook named in TRACKED settings.

WHY (RCA 559ecdabbba7 SS2, DE-2/DE-5): `.claude/settings.json` is TRACKED, so a `hooks.*[].hooks[]
.command` script is a permanent process -- the next session gets it with nothing armed. The RCA's
finding is exactly this: "The context guard is a `UserPromptSubmit` hook in tracked settings, so the
next session gets it without arming anything."

Today scripts/flow_coverage.py builds its population from `scripts/library.json` alone
(`permanent()`), so a hook script enters no population any gate enumerates: a hook can be added,
removed, or pointed at an off-chart script and every gate stays green. This suite pins the
population, the report, and the zero-population rule.

RED TODAY BECAUSE: `flow_coverage` defines no `hooks()` and no `hooks_report()` (its module
attributes are EXEMPT_KINDS, FLOW, LIBRARY, Path, REPO, helper_paths, json, main, named, on_chart,
permanent, re, sys, uncovered), and `--json` prints only `permanent`/`uncovered`. Every test below
reaches the name through `getattr(module, name, None)` + `assert callable(...)`, so today's run
prints a FAIL naming the missing function -- never an AttributeError.

Real constants, verbatim from .claude/settings.json (the three scripts, the `$CLAUDE_PROJECT_DIR/`
prefix, the `--hook` argv): this suite is built on the tracked file, not on a synthetic shape.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FC_PATH = ROOT / "scripts" / "flow_coverage.py"
SETTINGS = ROOT / ".claude" / "settings.json"
sys.path.insert(0, str(ROOT / "scripts"))
FC = None

HOOKED = ["scripts/context_watch.py",
          "scripts/hooks/guard_process_waits.py",
          "scripts/hooks/guard_task_writes.py"]
PROBE = "scripts/hooks/guard_synthetic_probe.py"
EMPTY_FLOW = {"tasks": [], "links": [], "sources": [], "services": [], "helpers": []}


def _fc():
    """The real module, imported once, from scripts/ where it lives."""
    global FC
    if FC is None:
        import flow_coverage
        FC = flow_coverage
    return FC


def _flow_with(paths):
    return {"tasks": [], "links": [], "sources": [], "services": [],
            "helpers": [{"script": p, "serves": ["t800"], "what": "fixture"} for p in paths]}


def _settings_dir(td, files):
    """A throwaway directory holding only what this test wrote. Nothing real touched."""
    sd = Path(td) / "settings-dir"
    sd.mkdir(parents=True)
    for name, text in files.items():
        (sd / name).write_text(text, encoding="utf-8")
    return sd


def _hook_settings(path, event="PreToolUse", matcher="Write|Edit", argv="--hook"):
    """A settings file in the REAL shape: python3 + "$CLAUDE_PROJECT_DIR/" + the script + argv."""
    return json.dumps({"hooks": {event: [{"matcher": matcher,
                                          "hooks": [{"type": "command",
                                                     "command": 'python3 "$CLAUDE_PROJECT_DIR/%s" %s'
                                                     % (path, argv)}]}]}})


def test_the_real_tracked_file_yields_exactly_its_three_hook_scripts():
    """DE-2/DE-5, read from the REAL file. Set equality on three DISTINCT repo-relative paths."""
    hooks = getattr(_fc(), "hooks", None)
    assert callable(hooks), "flow_coverage needs a callable `hooks()`; none exists today"
    got = hooks()
    assert isinstance(got, list), f"hooks() must return a list; got {type(got).__name__}"
    assert set(got) == set(HOOKED), (
        f"the census must read the hooks block of the tracked settings file: the three scripts "
        f"registered today, no more and no less. got {sorted(got)!r}")
    for p in got:
        assert p.startswith("scripts/") and p.endswith(".py"), (
            f"entries are repo-relative script paths, not command strings: {p!r}")
        assert " " not in p and "$" not in p and "\\" not in p, (
            f"the command is NORMALISED: no argv, no shell quoting, no env prefix: {p!r}")
        assert not p.startswith("/"), f"absolute path in the census: {p!r}"
    for junk in ("python3", "--hook", "python3 -", "$CLAUDE_PROJECT_DIR"):
        assert not any(junk in p for p in got), (
            f"{junk!r} is shell syntax, not part of a script path: {got!r}")


def test_the_real_command_strings_normalise_to_the_real_paths():
    """Parses the tracked file. Asserts command -> path per command, so a substring/grep build that
    returns tokens cannot pass: three DISTINCT expected values, not one repeated placeholder."""
    hooks = getattr(_fc(), "hooks", None)
    assert callable(hooks), "flow_coverage needs a callable `hooks()`"
    obj = json.loads(SETTINGS.read_text(encoding="utf-8"))
    want = {}
    for event, groups in (obj.get("hooks") or {}).items():
        for group in groups or []:
            for h in group.get("hooks") or []:
                cmd = str(h.get("command") or "")
                if ".py" in cmd:
                    want[cmd] = [p for p in HOOKED if ('/' + p) in cmd]
    assert len(want) == 3, f"the tracked file registers three hook commands; found {len(want)}: {sorted(want)!r}"
    with tempfile.TemporaryDirectory() as td:
        sd = _settings_dir(td, {"settings.json": json.dumps(obj)})
        got = hooks(settings_dir=sd)
    for cmd, expected in sorted(want.items()):
        assert len(expected) == 1, f"command {cmd!r} matched {len(expected)} known hooks: {expected}"
        assert expected[0] in got, (
            f"{cmd!r} must normalise to {expected[0]!r}; census gave {sorted(got)!r}")


def test_a_hook_the_chart_does_not_name_is_uncovered_and_the_report_says_so():
    """The report's value is USED: an off-chart hook must move hooks_uncovered AND flip ok."""
    report = getattr(_fc(), "hooks_report", None)
    assert callable(report), "flow_coverage needs a callable `hooks_report()`"
    with tempfile.TemporaryDirectory() as td:
        sd = _settings_dir(td, {"settings.json": _hook_settings(PROBE)})
        rep = report(settings_dir=sd, flow=EMPTY_FLOW)
    assert isinstance(rep, dict), f"hooks_report must return a dict; got {type(rep).__name__}"
    for key in ("hooks_examined", "hooks_uncovered", "ok"):
        assert key in rep, f"hooks_report() must report {key!r}; keys={sorted(rep)!r}"
    assert rep["hooks_examined"] == 1, (
        f"one configured hook, one examined; got {rep['hooks_examined']!r}")
    assert PROBE in list(rep["hooks_uncovered"]), (
        f"the probe is in no flow node, so it must be uncovered; got {rep['hooks_uncovered']!r}")
    assert rep["ok"] is False, (
        f"an uncovered hook must fail the census, not merely be listed; ok={rep['ok']!r}")


def test_the_same_input_with_the_chart_informed_produces_no_gap():
    """The value is used downstream: the same settings, chart names the helper -> zero gaps, ok True."""
    report = getattr(_fc(), "hooks_report", None)
    assert callable(report), "flow_coverage needs a callable `hooks_report()`"
    with tempfile.TemporaryDirectory() as td:
        sd = _settings_dir(td, {"settings.json": _hook_settings(PROBE)})
        rep = report(settings_dir=sd, flow=_flow_with([PROBE]))
    assert list(rep["hooks_uncovered"]) == [], (
        f"the same hook named as a chart helper leaves NO gap; got {rep['hooks_uncovered']!r}")
    assert rep["ok"] is True, f"a census with no gap must report ok True; got {rep['ok']!r}"
    assert rep["hooks_examined"] == 1, f"examined is the population size; got {rep['hooks_examined']!r}"


def test_the_population_is_a_union_the_settings_file_contributes():
    """A hook script absent from scripts/library.json must still be enumerated: the population is
    library UNION tracked settings, not library alone (which is what permanent() gives today)."""
    hooks = getattr(_fc(), "hooks", None)
    assert callable(hooks), "flow_coverage needs a callable `hooks()`"
    with tempfile.TemporaryDirectory() as td:
        sd = _settings_dir(td, {"settings.json": _hook_settings(PROBE)})
        got = set(hooks(settings_dir=sd))
    assert PROBE in got, f"a settings-named script must join the population; got {sorted(got)!r}"
    assert PROBE not in set(_fc().permanent()), (
        f"{PROBE} is in NO library.json -- if the population is library-only the settings half is "
        f"not in the union; permanent() has {len(_fc().permanent())} entries")


def test_settings_local_json_counts_too():
    """The RCA names `.claude/settings.json` AND `.claude/settings.*.json`: a hook in
    settings.local.json must be enumerated, not just settings.json."""
    hooks = getattr(_fc(), "hooks", None)
    assert callable(hooks), "flow_coverage needs a callable `hooks()`"
    with tempfile.TemporaryDirectory() as td:
        sd = _settings_dir(td, {"settings.json": _hook_settings(HOOKED[0]),
                                "settings.local.json": _hook_settings(PROBE)})
        got = sorted(hooks(settings_dir=sd))
    assert PROBE in got, f"a hook in settings.local.json must be enumerated; got {got!r}"
    assert HOOKED[0] in got, f"the union must keep what settings.json contributed; got {got!r}"


def test_a_zero_hooks_population_is_a_failure_not_a_pass():
    """DE-5, on the t723 precedent: a census that read nothing must not report success."""
    hooks = getattr(_fc(), "hooks", None)
    report = getattr(_fc(), "hooks_report", None)
    assert callable(hooks) and callable(report), "needs callables `hooks()` and `hooks_report()`"
    with tempfile.TemporaryDirectory() as td:
        sd = _settings_dir(td, {"settings.json": json.dumps({"permissions": {"deny": []}})})
        rep = report(settings_dir=sd, flow=_flow_with(HOOKED))
    assert hooks(settings_dir=sd) == [], "no hook configured => examined 0"
    assert rep["hooks_examined"] == 0, f"no hooks configured => examined 0; got {rep['hooks_examined']!r}"
    assert rep["ok"] is False, f"a zero-size hooks population must FAIL, not pass; ok={rep['ok']!r}"
    joined = " ".join(str(x) for x in (rep.get("errors") or []))
    assert "hooks" in joined.lower() and "zero" in joined.lower(), (
        f"the zero-population reason must be recorded, not silent; got {joined!r}")


def test_a_settings_file_that_names_no_script_contributes_nothing():
    """Negative: a shell command with no .py is not a hook. A broad grep that returns any token fails."""
    hooks = getattr(_fc(), "hooks", None)
    assert callable(hooks), "flow_coverage needs a callable `hooks()`"
    obj = {"hooks": {"PreToolUse": [{"matcher": "Bash",
                                     "hooks": [{"type": "command", "command": "echo hello world"}]}]}}
    with tempfile.TemporaryDirectory() as td:
        sd = _settings_dir(td, {"settings.json": json.dumps(obj)})
        got = sorted(hooks(settings_dir=sd))
    assert got == [], f"a command naming no .py script must contribute nothing; got {got!r}"


def test_missing_settings_file_is_a_failure():
    report = getattr(_fc(), "hooks_report", None)
    assert callable(report), "flow_coverage needs a callable `hooks_report()`"
    with tempfile.TemporaryDirectory() as td:
        rep = report(settings_dir=Path(td), flow=_flow_with(HOOKED))
    assert rep["hooks_examined"] == 0, f"no settings files => 0 examined; got {rep['hooks_examined']!r}"
    assert rep["ok"] is False, f"an absent settings file must not read as a pass; ok={rep['ok']!r}"
    joined = " ".join(str(x) for x in (rep.get("errors") or []))
    assert "settings" in joined.lower(), (
        f"the report must name why the population is empty; got {joined!r}")


def test_unparseable_settings_file_names_the_file_and_is_not_swallowed():
    """A caught exception must be RECORDED with its filename, never swallowed into a silent pass."""
    report = getattr(_fc(), "hooks_report", None)
    assert callable(report), "flow_coverage needs a callable `hooks_report()`"
    with tempfile.TemporaryDirectory() as td:
        sd = _settings_dir(td, {"settings.json": "{ not json"})
        rep = report(settings_dir=sd, flow=_flow_with(HOOKED))
    assert rep["ok"] is False, f"an unparseable settings file must fail the census; ok={rep['ok']!r}"
    joined = " ".join(str(x) for x in (rep.get("errors") or []))
    assert "settings.json" in joined, (
        f"the failing file must be named verbatim in the errors; got {joined!r}")


def test_the_real_repo_census_is_green_today():
    """DE-2/T3 is closed: the three hooks are already chart helpers, so the census is non-empty
    AND green on day one -- proving the data half exists and the wiring will not fire spuriously.
    NOTE: this pins the live file's count. Adding a hook to .claude/settings.json makes it red on
    purpose, until the chart gains the node (or the count is updated in the same commit)."""
    report = getattr(_fc(), "hooks_report", None)
    assert callable(report), "flow_coverage needs a callable `hooks_report()`"
    rep = report(flow=_flow_with(HOOKED))
    assert rep["hooks_examined"] == 3, (
        f"the REAL .claude/settings.json registers three hooks; examined={rep['hooks_examined']!r}")
    assert list(rep["hooks_uncovered"]) == [], (
        f"with the chart's real helpers the census is clean; got {rep['hooks_uncovered']!r}")


def test_cli_json_reports_the_hook_fields():
    """DE-5's collector command is `python3 scripts/flow_coverage.py --json`: it must carry the
    hook fields, so the census can be recorded over time."""
    r = subprocess.run([sys.executable, str(FC_PATH), "--json"], capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, f"--json on this repo must exit 0; rc={r.returncode} err={r.stderr[:400]}"
    try:
        data = json.loads(r.stdout)
    except Exception as e:
        raise AssertionError(f"--json must print one JSON object; {type(e).__name__}: {e} "
                             f"on {r.stdout[:400]!r}")
    assert "hooks_examined" in data, f"--json must report hooks_examined; keys={sorted(data)!r}"
    assert "hooks_uncovered" in data, f"--json must report hooks_uncovered; keys={sorted(data)!r}"
    assert data["hooks_examined"] == 3, (
        f"the real file registers three hooks; got {data['hooks_examined']!r}")
    assert isinstance(data["hooks_uncovered"], list), (
        f"hooks_uncovered must be a list; got {type(data['hooks_uncovered']).__name__}")
    assert data["hooks_uncovered"] == [], (
        f"the three real hooks are chart helpers today; got {data['hooks_uncovered']!r}")


def test_the_union_still_covers_the_library_scripts():
    """The union keeps what `permanent()` already did -- the existing gate must not lose population
    when the settings half is added."""
    fc = _fc()
    assert callable(getattr(fc, "permanent", None)), "flow_coverage.permanent() must stay"
    lib = fc.permanent()
    assert "scripts/jsonx.py" in lib, f"library.json scripts must stay in the population; {len(lib)}"
    hooks = getattr(fc, "hooks", None)
    assert callable(hooks), "flow_coverage needs a callable `hooks()`"
    for p in lib[:5]:
        assert p.endswith(".py") and p.startswith("scripts/"), f"library paths keep their shape: {p!r}"


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
