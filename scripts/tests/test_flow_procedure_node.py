"""t780 -- the Flow chart can name a procedure.

The off-flow detector's own contract names "a repeated multi-step procedure" as a
class of finding, and fourteen session RCAs (workspace/tasks/t780-*.md notes) minted
the same object over and over: a named loop -- "probe env3 -> findings -> patch env2
-> re-clone env3 -> probe again" (workspace/rca/388878f76b79.md) -- with nothing on
the system of record to attach it to. projects/local-inference/models/FLOW.json today
has the lanes tasks / links / sources / services / helpers. A procedure is not a
script (helpers entries carry a `script` path), not a task (tasks carry a `worker`
script), and not a service (a systemd unit). So the finding is unstateable at the
chart and re-mints on every re-read of any session that runs a documented procedure.

This oracle pins the schema rung only: the chart has a `procedures` lane, its nodes
carry the fields a procedure needs (owner, steps, inputs, outputs, serves, status),
they are ATTACHED -- serves ids resolve to ids that exist in this same file -- and a
procedure is not smuggled into the helpers lane. It asserts the REAL procedure from
the finding, step by step, so a build that writes one repeated placeholder string
cannot pass.

Run: python3 scripts/tests/test_flow_procedure_node.py
Red on main for one reason: FLOW.json has no 'procedures' key.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / "projects" / "local-inference" / "models" / "FLOW.json"


def _chart():
    return json.loads(FLOW.read_text(encoding="utf-8"))


def _tasks(chart):
    return {str(t.get("id")) for t in chart.get("tasks", [])}


def _services(chart):
    return {str(s.get("id")) for s in chart.get("services", [])}


def _procedures(chart):
    return chart.get("procedures", [])


def test_chart_has_a_procedures_lane():
    chart = _chart()
    assert "procedures" in chart, (
        "FLOW.json top-level keys are " + ", ".join(sorted(chart)) +
        " -- there is no lane a procedure can be registered in")
    assert isinstance(chart["procedures"], list), "procedures must be a list"
    assert chart["procedures"], (
        "the procedures lane is empty: a declared kind with no registered node "
        "still leaves a session's procedure with nothing to attach to, which is "
        "the defect this task closes")


def test_procedure_nodes_carry_the_fields_a_procedure_needs():
    for node in _procedures(_chart()):
        nid = str(node.get("id") or "<no id>")
        for field in ("id", "name", "owner", "steps", "inputs",
                      "outputs", "serves", "status"):
            assert field in node, (
                "procedure " + nid + " is missing the field '" + field + "'")
        assert str(node["owner"]).strip(), (
            "procedure " + nid + " has no owner -- an unowned procedure is the "
            "off-flow shape this task is about")
        steps = node["steps"]
        assert isinstance(steps, list) and len(steps) >= 2, (
            "procedure " + nid + " has fewer than two steps -- a procedure is a "
            "multi-step procedure")
        cleaned = [str(s).strip() for s in steps]
        assert all(cleaned), "procedure " + nid + " has an empty step"
        assert len(set(cleaned)) == len(cleaned), (
            "procedure " + nid + " repeats the same step string; the procedures "
            "named in the RCAs have distinct steps")
        assert isinstance(node["inputs"], list), "inputs must be a list"
        assert isinstance(node["outputs"], list), "outputs must be a list"
        assert isinstance(node["serves"], list) and node["serves"], (
            "procedure " + nid + " serves nothing -- an unattached node is "
            "exactly the off-flow shape this task closes")
        assert str(node["status"]).strip(), (
            "procedure " + nid + " has no status")


def test_the_procedure_from_the_finding_is_registered():
    """The quote this task exists for (workspace/rca/388878f76b79.md):
    probe env3 -> findings -> patch env2 -> re-clone env3 -> probe again.
    Naming its real steps is what stops a single placeholder passing."""
    steps = []
    for node in _procedures(_chart()):
        steps.extend([str(s) for s in node.get("steps", [])])
    joined = " | ".join(steps)
    for want in ("probe env3", "patch env2", "re-clone env3"):
        assert want in joined, (
            "no registered procedure step mentions '" + want + "' -- the cycle "
            "named in the finding is not on the chart. Steps on record: " + joined)


def test_procedure_ids_do_not_collide_with_the_other_lanes():
    chart = _chart()
    ids = [str(n.get("id")) for n in _procedures(chart)]
    assert len(ids) == len(set(ids)), "duplicate ids in procedures: " + str(ids)
    taken = _tasks(chart) | _services(chart)
    clash = sorted(i for i in ids if i in taken)
    assert not clash, (
        "procedure ids collide with task/service ids: " + ", ".join(clash) +
        " -- ids are one namespace across the chart")


def test_a_procedure_is_not_smuggled_into_the_helpers_lane():
    """helpers entries are scripts: keys script, serves, what, runs (read from
    the file). A procedure recorded there has no steps and no owner, so the
    off-flow pass cannot see it and the finding re-mints."""
    for node in _procedures(_chart()):
        assert "script" not in node, (
            "procedure " + str(node.get("id")) + " carries a 'script' path -- it "
            "is registered as a helper script, not as a procedure")
    for h in _chart().get("helpers", []):
        assert "steps" not in h, (
            "helpers entry " + str(h.get("script")) + " carries steps -- "
            "procedures live in the procedures lane, not in helpers")


def test_procedure_serves_resolve_to_real_chart_ids():
    chart = _chart()
    known = _tasks(chart) | _services(chart)
    for node in _procedures(chart):
        for ref in node.get("serves", []):
            assert str(ref) in known, (
                "procedure " + str(node.get("id")) + " serves '" + str(ref) +
                "', which is not a task id or a service id in FLOW.json -- a "
                "reference that resolves to nothing is the off-flow shape")


def test_the_procedure_is_attributable_to_the_task_that_created_it():
    nodes = _procedures(_chart())
    assert any(str(n.get("installed_by_task", "")).strip() for n in nodes), (
        "no procedure records who installed it -- registration must be "
        "attributable so a later sweep can tell a real node from a guess")


if __name__ == "__main__":
    tests = [
        test_chart_has_a_procedures_lane,
        test_procedure_nodes_carry_the_fields_a_procedure_needs,
        test_the_procedure_from_the_finding_is_registered,
        test_procedure_ids_do_not_collide_with_the_other_lanes,
        test_a_procedure_is_not_smuggled_into_the_helpers_lane,
        test_procedure_serves_resolve_to_real_chart_ids,
        test_the_procedure_is_attributable_to_the_task_that_created_it,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print("FAIL " + fn.__name__ + ": " + str(exc))
        except Exception as exc:
            failed += 1
            print("ERROR " + fn.__name__ + ": " + type(exc).__name__ + ": " + str(exc))
    print("ran " + str(len(tests)) + " test(s), " + str(failed) + " failed")
    raise SystemExit(1 if failed else 0)
