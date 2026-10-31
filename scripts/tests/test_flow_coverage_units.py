#!/usr/bin/env python3
"""t1206 -- flow_coverage answers "is this standing process on the chart?" by NAME.

The hourly check failed at 2026-10-14T22:04:44Z on 708ab41f with one finding,
`auditor-scoping.service`, and adding a chart row naming it would not have fixed the
measurement. check_workspace.py:1741 decides on-chart-ness as
`unit.rsplit(".", 1)[0] not in flow_text` -- a raw substring over the whole chart, which
any sentence satisfies and no other code can reuse. flow_coverage's own resolver,
named() at flow_coverage.py:48-51, matches only names ending .py/.sh and `name.py(` call
sites, so the services[] rows -- which is how the chart spells a standing process: 14
entries, each {"id","name","unit"}, e.g. {"id": "svc_manager", "name": "Manager queue",
"unit": "manager-queue.service"} -- are invisible to it.

This suite pins the resolver the RCA asked for: on_chart_unit(name, flow) resolved
extension-insensitively against services[].unit and services[].name only, and
census(units, cron, flow) returning the off-chart list. Every case hands its chart in as a
dict: no FLOW.json, no systemd, no crontab, no model, no file written.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import flow_coverage  # noqa: E402


def _chart(services, tasks=None):
    return {"tasks": tasks or [], "links": [], "sources": [], "services": services,
            "helpers": [], "procedures": []}


def test_the_module_answers_for_a_unit():
    """The resolver exists, takes (name, chart), answers a bool, and is empty-chart clean."""
    assert hasattr(flow_coverage, "on_chart_unit"), \
        "flow_coverage has no on_chart_unit(name, flow=None) -- nothing can ask the chart by name"
    assert flow_coverage.on_chart_unit("auditor-scoping.service", _chart([])) is False


def test_a_service_row_naming_the_unit_resolves():
    """The shape the real chart uses, and the extension-insensitivity the caller needs."""
    flow = _chart([{"id": "svc_auditor_scoping", "name": "Auditor scoping",
                    "unit": "auditor-scoping.service"}])
    assert flow_coverage.on_chart_unit("auditor-scoping.service", flow) is True
    # The caller hands over the full unit name from `systemctl --user list-unit-files`; a
    # chart row may carry a .timer; a caller may ask with the stem. All three are the same
    # standing process, so all three must resolve to the same row.
    assert flow_coverage.on_chart_unit("auditor-scoping", flow) is True
    assert flow_coverage.on_chart_unit("night-watch.service",
                                      _chart([{"id": "x", "name": "X",
                                               "unit": "night-watch.timer"}])) is True
    # A row of the shape production uses, so the rule is pinned against the real schema.
    assert flow_coverage.on_chart_unit("manager-queue.service",
                                      _chart([{"id": "svc_manager", "name": "Manager queue",
                                               "unit": "manager-queue.service"}])) is True


def test_a_service_named_by_its_display_name_resolves():
    """`"name": "Auditor scoping"` names the unit `auditor-scoping.service`: spaces and
    case are spelling, not identity -- the chart spells `Manager queue` and systemd spells
    `manager-queue.service`, and a resolver that cannot connect them reports a standing
    process the chart already shows."""
    assert flow_coverage.on_chart_unit("auditor-scoping.service",
                                      _chart([{"id": "svc_auditor_scoping",
                                               "name": "Auditor scoping"}])) is True


def test_prose_on_the_chart_does_not_count():
    """THE DEFECT. A substring over FLOW.json's text is satisfied by a sentence: a task
    note mentioning the unit turns the hourly check green while no services[] row carries
    it. Today check_workspace.py:1741 accepts exactly this, so this assertion is RED on
    main -- and it is the assertion that makes the check trustworthy as evidence."""
    flow = _chart([{"id": "svc_manager", "name": "Manager queue", "unit": "manager-queue.service"}],
                  tasks=[{"id": "t1206", "name": "Hourly check",
                          "note": "auditor-scoping.service runs scripts/manager_queue.sh auditor"}])
    assert flow_coverage.on_chart_unit("auditor-scoping.service", flow) is False, \
        ("the unit appears only inside a task NOTE and was counted as on-chart: a chart "
         "sentence is not a service row, and the substring rule is what let the check fire "
         "on a unit the chart never registered")
    # Same chart, a unit a services[] row carries: so the False above is prose not counting,
    # not a resolver that resolves nothing.
    assert flow_coverage.on_chart_unit("manager-queue.service", flow) is True


def test_a_unit_the_chart_does_not_name_is_not_invented():
    flow = _chart([{"id": "svc_manager", "name": "Manager queue", "unit": "manager-queue.service"}])
    assert flow_coverage.on_chart_unit("auditor-scoping.service", flow) is False
    assert flow_coverage.on_chart_unit("svc_manager", flow) is False   # a row's id is not a unit
    assert flow_coverage.on_chart_unit("", flow) is False
    assert flow_coverage.on_chart_unit(None, flow) is False


def test_a_malformed_chart_is_answered_not_raised():
    """The check runs this over the chart as shipped; a junk row must not cost the hour's
    run. flow_coverage.py:43-44 raises AttributeError on a non-dict entry today."""
    assert flow_coverage.on_chart_unit("auditor-scoping.service",
                                      _chart(["auditor-scoping.service", None,
                                              {"id": "svc_no_unit"}, 7])) is False
    assert flow_coverage.on_chart_unit("svc_no_unit",
                                      _chart([{"id": "svc_no_unit"}])) is False
    assert flow_coverage.on_chart_unit("auditor-scoping.service", {"services": None}) is False
    assert flow_coverage.on_chart_unit("auditor-scoping.service", {}) is False


def test_census_lists_what_the_chart_does_not_name():
    """The census the check needs: off-chart units, plus cron lines whose script is off-chart."""
    assert hasattr(flow_coverage, "census"), \
        "flow_coverage has no census(units, cron, flow=None) returning the off-chart list"
    flow = _chart([{"id": "svc_auditor_scoping", "name": "Auditor scoping",
                    "unit": "auditor-scoping.service"}],
                  tasks=[{"id": "t1206", "name": "Night work", "command": "night_queue.py"}])
    units = ["auditor-scoping.service", "night-watch.service", "memory-watch.timer"]
    cron = ["# 1 2 * * * python3 /home/user/ai-workspace/scripts/ghost_script.py",
            "",
            "17 4 * * * python3 /home/user/ai-workspace/scripts/night_queue.py",
            "5 3 * * * python3 /home/user/ai-workspace/scripts/ghost_script.py"]
    got = flow_coverage.census(units, cron, flow)
    assert isinstance(got, list)
    assert "night-watch.service" in got, f"off-chart unit missing from {got!r}"
    assert "memory-watch.timer" in got, f"off-chart timer missing from {got!r}"
    assert any(x.startswith("cron: ") and "ghost_script.py" in x for x in got), \
        f"the off-chart cron line is not reported as a cron finding: {got!r}"
    assert not any("auditor-scoping" in x for x in got), f"on-chart unit reported off-chart: {got!r}"
    assert not any("night_queue" in x for x in got), \
        f"a script the chart's tasks row names was reported off-chart: {got!r}"
    assert sum("ghost_script" in x for x in got) == 1, \
        f"a commented-out cron line was reported too: {got!r}"
    assert len(got) == len(set(got)), f"census repeats a finding: {got!r}"
    assert got == sorted(got), f"census is not in a stable order: {got!r}"


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
