import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROLES = ROOT / "projects" / "local-inference" / "models" / "ROLES.json"

def _fixture():
    tmp = Path(tempfile.mkdtemp(prefix="t1552-roles-"))
    dest = tmp / "ROLES.json"
    shutil.copyfile(ROLES, dest)
    return dest

def _embedder_tasks(path):
    with path.open() as f:
        roles = json.load(f)
    return {t["task"] for t in roles["roles"]["embedder"]["tasks"]}

def test_embedder_has_session_gap_filter():
    tasks = _embedder_tasks(_fixture())
    assert "session_gap_filter" in tasks, \
        "Embedder role tasks missing 'session_gap_filter'"

def test_embedder_has_intake():
    tasks = _embedder_tasks(_fixture())
    assert "intake" in tasks, \
        "Embedder role tasks missing 'intake'"

def test_embedder_still_has_corpus_index():
    tasks = _embedder_tasks(_fixture())
    assert "corpus_index" in tasks, "Embedder role lost its corpus_index task"

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
