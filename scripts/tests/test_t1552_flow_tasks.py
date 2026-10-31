import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / "projects" / "local-inference" / "models" / "FLOW.json"

def _fixture():
    tmp = Path(tempfile.mkdtemp(prefix="t1552-flow-"))
    dest = tmp / "FLOW.json"
    shutil.copyfile(FLOW, dest)
    return dest

def _tasks(path):
    with path.open() as f:
        return json.load(f)["tasks"]

def test_session_gap_filter_is_a_flow_task():
    by_name = {t.get("name") for t in _tasks(_fixture())}
    assert "Session gap filter" in by_name, \
        "no Flow task named 'Session gap filter' in FLOW.json tasks"

def test_session_gap_filter_fields():
    match = [t for t in _tasks(_fixture()) if t.get("name") == "Session gap filter"]
    assert match, "no Flow task named 'Session gap filter' to check fields on"
    t = match[0]
    assert t["status"] == "active", "Session gap filter must be active (switchable)"
    assert t["worker"] == "Embedder", "Session gap filter belongs to the Embedder role"
    assert t["worker_kind"] == "role"
    for k in ("worker", "worker_kind", "queue", "inputs", "outputs", "outcomes", "triggers", "ends"):
        assert k in t, f"Session gap filter task missing key {k}"

def test_intake_still_a_flow_task():
    names = {t.get("name") for t in _tasks(_fixture())}
    assert "Intake" in names, "Intake task disappeared from FLOW.json"

if __name__ == "__main__":
    import sys
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failures = 0
    for f in fns:
        try:
            f()
            print("PASS", f.__name__)
        except AssertionError as e:
            print("FAIL", f.__name__, "-", e)
            failures += 1
    sys.exit(1 if failures else 0)
