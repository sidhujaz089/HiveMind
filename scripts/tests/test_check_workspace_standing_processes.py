#!/usr/bin/env python3
"""t1206 -- the hourly check asks the chart by name, not by substring.

check_workspace.py:1741 decides on-chart-ness with `unit.rsplit(".", 1)[0] not in
flow_text`: a substring over the whole 96 KB chart. A task note that mentions
`scoping-queue@auditor.service` therefore makes the check PASS with no services[] row for the
unit that made it fire -- a green light that means nothing (workspace/failure-schema.json,
check-satisfied-by-dead-path). This suite drives the real Checker over a throwaway repo and
pins what the finding list has to be built from: a unit named only in prose is a FINDING, a
unit on a services[] row is not.

No live state. The check's two live calls (systemctl --user list-unit-files, crontab -l) are
answered by a recording stand-in for the module's subprocess, so the suite runs in a sandbox
with no systemd and no cron and can say what the check asked. HOME is redirected for the
duration of each fixture, because check_workspace.py:1730 builds the unit directory from
Path.home() -- and restored in a finally block. Nothing outside the temp dir is written.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check_workspace  # noqa: E402

NAME = "Every standing process is on the Flow chart"
REPO_MARK = "/home/user/ai-workspace/scripts/scoping_queue.sh"


class _Done:
    def __init__(self, out, code=0):
        self.stdout = out
        self.stderr = ""
        self.returncode = code


class Recording:
    """Stands in for check_workspace.subprocess. No child process is ever started."""

    SubprocessError = Exception          # check_workspace.py:1748 names it in an except

    def __init__(self, units, cron):
        self.units = list(units)
        self.cron = list(cron)
        self.calls = []

    def run(self, argv, *args, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv and argv[0] == "systemctl":
            # The shape systemctl prints: "<unit> enabled <preset>".
            return _Done("".join(f"{u} enabled enabled\n" for u in sorted(self.units)))
        if argv and argv[0] == "crontab":
            return _Done("\n".join(self.cron) + "\n")
        return _Done("", 0)               # git and anything else: empty, success


def _chart(services, tasks):
    return {"tasks": tasks, "links": [], "sources": [], "services": services,
            "helpers": [], "procedures": []}


def _fixture(tmp, chart, units, cron):
    """A repo-shaped tree holding the chart the check reads and the units it deploys."""
    root = Path(tmp) / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "library.json").write_text(json.dumps({"scripts": {}}), encoding="utf-8")
    chart_path = root / "projects" / "local-inference" / "models" / "FLOW.json"
    chart_path.parent.mkdir(parents=True)
    chart_path.write_text(json.dumps(chart), encoding="utf-8")
    units_dir = Path(tmp) / "home" / ".config" / "systemd" / "user"
    units_dir.mkdir(parents=True)
    for name in units:
        # A unit the repo deploys: its ExecStart points into the checkout, which is the
        # filter at check_workspace.py:1740.
        (units_dir / name).write_text(f"[Service]\nExecStart={root}/scripts/scoping_queue.sh\n", encoding="utf-8")
    return root


def _verdict(chart, units=(), cron=()):
    """Run the real check_flow_record over the fixture; return (status, detail, calls)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _fixture(tmp, chart, units, cron)
        real_home = os.environ.get("HOME")
        real_sub = check_workspace.subprocess
        rec = Recording(units, cron)
        os.environ["HOME"] = str(Path(tmp) / "home")       # Path.home() honours $HOME
        check_workspace.subprocess = rec
        try:
            c = check_workspace.Checker(root, quick=True)
            out = {}
            c.result = lambda s, n, d=None: out.__setitem__(n, (s, d or ""))
            c.section = lambda t: None
            c.check_flow_record()
        finally:
            check_workspace.subprocess = real_sub
            if real_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = real_home
    for k, v in out.items():
        if "standing process" in k.lower():
            if k != NAME:
                raise AssertionError(
                    f"the check renamed itself to {k!r}; scripts/hourly_checks.py and the "
                    f"open task t1206 match the finding by its name {NAME!r}")
            return v[0], v[1], rec.calls
    raise AssertionError(f"check_flow_record recorded no standing-process verdict: {sorted(out)}")


def test_the_fixture_reaches_the_checks_live_enumeration():
    """Nothing examined is not a pass: the check must reach systemctl and crontab and
    record a verdict for the finding -- not bail out early as \"could not check\"."""
    out, _detail, calls = _verdict(_chart([], []), units=["night-watch.service"])
    asked = {c[0] for c in calls if c}
    assert "systemctl" in asked and "crontab" in asked, \
        f"the check never enumerated units and cron -- the fixture proves nothing: {sorted(asked)}"
    assert out in ("PASS", "FAIL"), f"no verdict for the finding: {out!r}"


def test_a_unit_named_only_in_prose_is_reported():
    """THE DEFECT, as behaviour. The chart's only mention of the unit is a task note. A
    substring over the chart text is satisfied by that sentence, so today the check says
    PASS with no services[] row for the unit that made it fire: this assertion is RED on
    main, and it is why the green check at 2026-10-14T22:04:44Z could not be trusted."""
    chart = _chart([{"id": "svc_manager", "name": "Manager queue", "unit": "scoping-queue@manager.service"}],
                   [{"id": "t1206", "name": "Hourly check",
                     "note": "scoping-queue@auditor.service runs scripts/scoping_queue.sh auditor"}])
    status, detail, _calls = _verdict(chart, units=["scoping-queue@auditor.service"])
    assert status == "FAIL", \
        (f"the chart names `scoping-queue@auditor.service` only in a task NOTE and the check said "
         f"{status!r}: prose on the chart is being taken for a service row. detail: {detail}")
    assert "scoping-queue@auditor.service" in detail, \
        f"the finding does not name the off-chart unit: {detail!r}"
    assert "manager-queue" not in detail, \
        f"a unit the chart's services[] row names is reported: {detail!r}"


def test_a_unit_named_on_the_services_row_is_not_reported():
    """The fix must close the instance: a services[] row naming the unit satisfies the
    check, so registering scoping-queue@auditor.service makes the next hourly run green."""
    chart = _chart([{"id": "svc_auditor_scoping", "name": "Auditor scoping",
                     "unit": "scoping-queue@auditor.service"}], [])
    status, detail, _calls = _verdict(chart, units=["scoping-queue@auditor.service"])
    assert status == "PASS", f"a services[] row naming the unit is still a finding: {detail!r}"
    assert "auditor-scoping" not in detail, f"the finding lists a charted unit: {detail!r}"


def test_a_unit_the_chart_never_names_is_reported():
    """The class the check exists for, and the value of the finding string."""
    chart = _chart([{"id": "svc_manager", "name": "Manager queue", "unit": "scoping-queue@manager.service"}],
                   [{"id": "t1206", "name": "Audit", "note": "no process names here"}])
    status, detail, _calls = _verdict(chart, units=["night-watch.service", "scoping-queue@manager.service"])
    assert status == "FAIL", f"an uncharted unit was not reported: {detail!r}"
    assert "night-watch.service" in detail, f"the off-chart unit is not named: {detail!r}"
    assert "manager-queue" not in detail, f"a charted unit was reported: {detail!r}"


def test_a_cron_line_is_measured_by_its_script():
    """The cron half travels with the unit half: an uncharted script's line is a finding
    written `cron: <line>`, a commented-out line is not, and a script the chart names is
    not a finding at all."""
    chart = _chart([], [{"id": "t1206", "name": "Night work", "command": "night_queue.py"}])
    status, detail, _calls = _verdict(
        chart,
        cron=["5 3 * * * python3 /home/user/ai-workspace/scripts/ghost_script.py",
              "# 1 2 * * * python3 /home/user/ai-workspace/scripts/ghost_script.py",
              "17 4 * * * python3 /home/user/ai-workspace/scripts/night_queue.py"])
    assert status == "FAIL", f"an uncharted cron script was not reported: {detail!r}"
    assert detail.count("ghost_script.py") == 1, \
        f"the commented-out cron line was reported, or the real one was not: {detail!r}"
    assert "night_queue" not in detail, f"a script the chart names was reported: {detail!r}"


if __name__ == "__main__":
    _tests = [(n, f) for n, f in sorted(globals().items())
              if n.startswith("test_") and callable(f)]
    _bad = 0
    for _name, _fn in _tests:
        try:
            _fn()
            print(f"  PASS  {_name}")
        except AssertionError as e:
            _bad += 1
            print(f"  FAIL  {_name}: {e}")
        except Exception as e:                                    # noqa: BLE001
            _bad += 1
            print(f"  CRASH {_name}: {type(e).__name__}: {e}")
    print(f"{len(_tests) - _bad}/{len(_tests)} passed")
    sys.exit(1 if _bad else 0)
