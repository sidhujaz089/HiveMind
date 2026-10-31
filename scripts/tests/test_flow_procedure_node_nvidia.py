"""t811 -- the NVIDIA playbook lane is registered on the Flow chart.

The off-flow RCA workspace/rca/6b4a0c36391f.md found a permanent second coding lane
on the box: `ollama run qwen3.6` -> `ollama launch claude --model qwen3.6` in
~/cli-agent-demo -> make a failing test pass (rca/6b4a0c36391f.md:19-21). It is
documented ONLY in prose (projects/nvidia-playbook/STATUS.md:17-20, 122-132,
208-213, cited from the RCA -- that file's step table at :50-60 is the builder's
source of truth for the eight steps). projects/local-inference/models/FLOW.json is
the declared system of record (FLOW.json:2, "Read by scripts/flow_layout.py and the
Hub's Flow tab") and it has no lane a procedure can sit in: the string "procedure"
occurs nowhere in its 3167 lines, and worker_kind takes only script/role/person/store.
So the lane has no owner, no scope, no chart edge, and the finding re-mints on every
re-read of any session that runs a documented procedure.

This suite pins the INSTANCE -- the playbook lane's recorded content. The schema rung
(a 'procedures' kind at all) is t780 and has its own suite,
scripts/tests/test_flow_procedure_node.py; this one does not duplicate it, and it
deliberately makes no assertion about the `links` array.

Every assertion below uses a constant that appears verbatim in the RCA: qwen3.6,
`ollama launch claude`, cli-agent-demo, http://127.0.0.1:11434/v1/chat/completions,
acceptEdits, 2026-08-26. They are distinct values on purpose: a build that repeats one
placeholder string cannot pass, and a build that records a live-state probe as
"present" cannot pass either, because nothing has run those probes (RCA thread C10).

Run: python3 scripts/tests/test_flow_procedure_node_nvidia.py
Red on main for one reason: FLOW.json has no "procedures" lane, so there is no node
to assert on.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / "projects" / "local-inference" / "models" / "FLOW.json"

LANE_ID = "nvidia-playbook"
MODEL = "qwen3.6"
ENTRYPOINT = "ollama launch claude"
DEMO_DIR = "cli-agent-demo"
CODER_ENDPOINT = "http://127.0.0.1:11434/v1/chat/completions"
PERMISSION_MODE = "acceptEdits"
CREATED_AT = "2026-08-26"
STEP_COUNT = 8


def _chart():
    return json.loads(FLOW.read_text(encoding="utf-8"))


def _procedures(chart):
    lane = chart.get("procedures")
    assert isinstance(lane, list), (
        "FLOW.json has no 'procedures' lane (top-level keys: "
        + ", ".join(sorted(chart)) + ") -- a multi-step procedure has nothing on "
        "the chart to be registered as, which is the defect t811 closes")
    return lane


def _lane(chart):
    nodes = [n for n in _procedures(chart) if str(n.get("id")) == LANE_ID]
    assert len(nodes) == 1, (
        "expected exactly one procedures node with id " + repr(LANE_ID)
        + ", found " + str(len(nodes)))
    return nodes[0]


def _collect(value, out):
    if isinstance(value, dict):
        for v in value.values():
            _collect(v, out)
    elif isinstance(value, list):
        for v in value:
            _collect(v, out)
    elif isinstance(value, str):
        out.append(value)
    return out


def _node_text(node):
    return "\n".join(_collect(node, []))


def test_the_lane_is_registered_exactly_once_as_a_procedure():
    chart = _chart()
    _lane(chart)
    collisions = []
    for key in ("tasks", "services", "helpers", "sources"):
        rows = chart.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and str(row.get("id")) == LANE_ID:
                collisions.append(key)
    assert not collisions, (
        "id " + LANE_ID + " also appears in the " + ", ".join(sorted(set(collisions)))
        + " lane(s); a session-run procedure is not a script, a service, a store or "
        "a helper script -- smuggling it into a lane that exists is the same defect "
        "wearing a chart node")


def test_the_lane_records_the_eight_documented_steps_as_distinct_text():
    steps = _lane(_chart()).get("steps")
    assert isinstance(steps, list), "steps must be a list, one entry per playbook step"
    assert len(steps) == STEP_COUNT, (
        "the playbook's state table has steps 1-8 (projects/nvidia-playbook/STATUS.md"
        ":50-60, cited at rca/6b4a0c36391f.md:19); recorded " + str(len(steps)))
    cleaned = [str(s).strip() for s in steps]
    assert all(cleaned), "a recorded step is empty"
    assert len(set(cleaned)) == STEP_COUNT, (
        "the steps repeat a placeholder string: " + str(sorted(set(cleaned))))
    text = "\n".join(cleaned)
    for token in ("ollama run", MODEL, ENTRYPOINT, DEMO_DIR):
        assert token in text, (
            "the recorded steps never mention " + repr(token)
            + " -- the lane as run was: ollama run " + MODEL + " -> " + ENTRYPOINT
            + " in ~/" + DEMO_DIR + " (rca/6b4a0c36391f.md:19-20)")


def test_the_lane_records_the_real_entrypoint_and_the_path_shim():
    entrypoint = str(_lane(_chart()).get("entrypoint", ""))
    assert ENTRYPOINT in entrypoint, (
        "entrypoint does not record the launch command: " + repr(entrypoint))
    assert "PATH" in entrypoint, (
        "the lane was made runnable by a `claude` shim placed on PATH, a permanent "
        "machine change (rca/6b4a0c36391f.md:22); a node that records only the "
        "launch command hides the change that makes it runnable")


def test_the_lane_records_the_coder_endpoint_and_the_permission_mode():
    node = _lane(_chart())
    text = _node_text(node)
    assert CODER_ENDPOINT in text, (
        "the lane's standing route into our own harness -- CODER_ENDPOINT="
        + CODER_ENDPOINT + " into coder.py (rca/6b4a0c36391f.md:21,76) -- is not "
        "recorded; that cross-component coupling is the reason the lane is a chart "
        "object and not prose")
    assert PERMISSION_MODE in text, (
        "the lane runs --permission-mode " + PERMISSION_MODE + " with no sandbox "
        "(rca/6b4a0c36391f.md:23); leaving it out makes the node read as safe")
    assert MODEL in text, "the lane's model " + MODEL + " is not recorded"


def test_the_d31_gates_are_recorded_as_open_conditions():
    conds = _lane(_chart()).get("open_conditions")
    assert isinstance(conds, list) and len(conds) >= 2, (
        "open_conditions must list at least the D-31 step-2 (cut network) and step-3 "
        "(sandbox) gates (rca/6b4a0c36391f.md:21,23); found " + repr(conds))
    cleaned = [str(c).strip() for c in conds]
    assert all(cleaned), "an open_condition is empty"
    assert len(set(cleaned)) == len(cleaned), "open_conditions repeat one placeholder"
    joined = " ".join(cleaned).lower()
    assert "network" in joined, "no network-isolation gate recorded (D-31 step 2)"
    assert "sandbox" in joined, "no sandbox gate recorded (D-31 step 3)"
    for c in cleaned:
        low = c.lower()
        assert "done" not in low and "closed" not in low, (
            "open_condition says it is settled: " + c + " -- the lane has no network "
            "isolation and no sandbox today; recording a gate as met is a lie about "
            "the lane, not a plan to build one")


def test_the_deferred_authorisation_is_recorded_as_jas_s_open_decision():
    needs = str(_lane(_chart()).get("needs_jas", ""))
    assert "real work" in needs.lower(), (
        "STATUS.md:211-213 defers the decision 'may this lane be pointed at real "
        "work?' to a deliberate future decision, and the RCA leaves it DECLARED-OPEN "
        "to Jas (rca/6b4a0c36391f.md:55,125); the node must carry it as an open "
        "question, not resolve it. found needs_jas=" + repr(needs))


def test_the_live_state_census_row_records_probes_that_have_not_been_run():
    live = _lane(_chart()).get("live_state")
    assert isinstance(live, dict), (
        "no live_state census row -- today the lane's live presence exists only as "
        "prose in STATUS.md, which no gate reads (rca/6b4a0c36391f.md:73, C10)")
    for key, token in (("path_shim", "command -v claude"),
                       ("demo_dir", DEMO_DIR),
                       ("coder_endpoint", "11434")):
        row = live.get(key)
        assert isinstance(row, dict), "live_state has no " + key + " row"
        probe = str(row.get("probe", "")).strip()
        assert probe, ("live_state." + key + ".probe is empty -- a census row with "
                       "no command that reads it is prose in chart clothing")
        assert token in probe, (
            "live_state." + key + ".probe does not name " + repr(token)
            + "; found " + repr(probe))
        state = str(row.get("status", "")).strip().lower()
        assert state, "live_state." + key + " records no status"
        assert state in ("unmeasured", "not measured", "unverified"), (
            "live_state." + key + ".status says " + repr(state) + " -- nothing has "
            "run this probe, and a census row asserting the shim is present is a "
            "claim no recorder supports")


def test_the_lane_is_attributable_dated_and_states_what_it_serves():
    node = _lane(_chart())
    assert str(node.get("installed_by_task", "")).strip(), (
        "no installed_by_task -- registration must be attributable, the same "
        "requirement t780's suite puts on every procedures node")
    assert str(node.get("created_at", "")).startswith(CREATED_AT), (
        "the lane was built 2026-08-26 (rca/6b4a0c36391f.md:19); created_at is "
        + repr(node.get("created_at")))
    assert str(node.get("status", "")).strip() == "active", (
        "the lane is a standing path kept alongside the vLLM stack "
        "(STATUS.md:17-20 via rca/6b4a0c36391f.md:48), so its status is 'active', "
        "not 'done' or 'archived'")
    assert str(node.get("owner", "")).strip(), (
        "no owner -- the finding is that the lane has no owner (t811 body:17)")
    serves = node.get("serves")
    assert isinstance(serves, list) and serves, (
        "serves is empty -- an unowned, unserved lane is the defect, not the shape "
        "of a registration")
    cleaned = [str(s).strip() for s in serves]
    assert all(cleaned), "a serves entry is empty"
    assert len(set(cleaned)) == len(cleaned), "serves repeats a placeholder"


if __name__ == "__main__":
    tests = [
        test_the_lane_is_registered_exactly_once_as_a_procedure,
        test_the_lane_records_the_eight_documented_steps_as_distinct_text,
        test_the_lane_records_the_real_entrypoint_and_the_path_shim,
        test_the_lane_records_the_coder_endpoint_and_the_permission_mode,
        test_the_d31_gates_are_recorded_as_open_conditions,
        test_the_deferred_authorisation_is_recorded_as_jas_s_open_decision,
        test_the_live_state_census_row_records_probes_that_have_not_been_run,
        test_the_lane_is_attributable_dated_and_states_what_it_serves,
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
