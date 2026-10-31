#!/usr/bin/env python3
"""t1593 -- the Role tab offers only the tasks the role's MODEL can do.

Jas, 2026-10-25: "The Role tab should only allow tasks a model can do. For example the embedder
model cant do things other roles can and vice versa."

The offered list is built in ONE place, task_routing.tasks_for_model(), and the Models tab's
"+ Add Task" form reaches it through roles_pages._model_tasks(). A filter one caller uses and
another does not is the defect this suite exists to catch, so the last two checks drive the real
Tasks-page HTML rather than the helper alone.

Seeded, not live: the two capability tags below are added to the map for the length of the run,
and the two roles live only in a temp copy of the store. The live ROLES.json is never written.
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import role_store as rs  # noqa: E402
import roles_pages as rp  # noqa: E402
import task_routing as t  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  (got {got!r}, want {want!r})"))


# ---- the two capability shapes, seeded so the check does not depend on what Jas has on the Models
# tab today. EMBEDDING_TASKS / GENERATIVE_TASKS are the declaration's own vocabulary: if the module
# stops declaring them the suite says so instead of silently testing a copy.
seeded = {"embcap:1b": t.EMBEDDING_TASKS, "gencap:7b": t.GENERATIVE_TASKS}
t.MODEL_CAPABILITY.update(seeded)

emb = t.tasks_for_model("embcap:1b")
gen = t.tasks_for_model("gencap:7b")
report("an embedding-only model is offered corpus_index and nothing else", emb, frozenset({"corpus_index"}))
report("a generative model is offered no corpus_index", "corpus_index" in gen, False)
report("  and is offered the rest of the model task list",
       gen, frozenset(t.MODEL_TASKS - {"corpus_index"}))
report("  every offered task is still one the Flow tab gives a model", gen <= t.MODEL_TASKS, True)

err = io.StringIO()
with contextlib.redirect_stderr(err):
    unknown = t.tasks_for_model("mystery:9b")
report("a model with no declared capability keeps today's full list", unknown, t.MODEL_TASKS)
report("  and the fallback is said out loud, naming the model", "mystery:9b" in err.getvalue(), True)

# THE LIVE DEFECT, measured 2026-10-25: swift-junior:latest is a generative model whose REGISTRY.json
# row has no type, so it was offered corpus_index (the embedder's task). The declaration is what
# fixes it -- a tag the registry cannot classify is a tag this map must name.
report("the live embedder is offered corpus_index only",
       t.tasks_for_model("qwen3-embedding:0.6b"), frozenset({"corpus_index"}))
report("swift-junior:latest (no type in REGISTRY.json) is not offered corpus_index",
       "corpus_index" in t.tasks_for_model("swift-junior:latest"), False)

# THE CALL SITE (the point of the task): the Models tab must ask the same function, not carry a
# copy of the list. Swapping the function for a sentinel is the only way to see the call itself.
real = t.tasks_for_model
seen = []


def spy(tag):
    seen.append(tag)
    return {"SENTINEL-TASK"}


t.tasks_for_model = spy
try:
    via_pages = rp._model_tasks("gencap:7b")
finally:
    t.tasks_for_model = real
report("the Models tab's task list comes from task_routing.tasks_for_model",
       (via_pages, seen), ({"SENTINEL-TASK"}, ["gencap:7b"]))

# ---- END TO END through the tab itself. Two seeded roles whose only difference is the model.
live_before = (rs.STORE.read_bytes(), rs.SERVING.read_bytes())
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    shutil.copy(rs.STORE, tmp / "ROLES.json")
    doc = json.loads((tmp / "ROLES.json").read_text(encoding="utf-8"))
    doc["roles"]["embtest"] = {"name": "EmbTest", "kind": "model", "model": "embcap:1b", "tasks": []}
    doc["roles"]["gentest"] = {"name": "GenTest", "kind": "model", "model": "gencap:7b", "tasks": []}
    (tmp / "ROLES.json").write_text(json.dumps(doc), encoding="utf-8")
    (tmp / "SERVING.json").write_text(json.dumps({"roles": rs.serving_block(doc)}), encoding="utf-8")
    real_paths = (rs.STORE, rs.SERVING, rs.LOG)
    rs.STORE, rs.SERVING, rs.LOG = tmp / "ROLES.json", tmp / "SERVING.json", tmp / "ROLES-CHANGELOG.jsonl"
    try:
        emb_html = rp.tasks_page("embtest")
        gen_html = rp.tasks_page("gentest")
    finally:
        rs.STORE, rs.SERVING, rs.LOG = real_paths
report("the Tasks page offers the embedder's role corpus_index",
       'name="add" value="corpus_index"' in emb_html, True)
report("  and offers it no Building", 'name="add" value="building"' in emb_html, False)
report("the Tasks page offers a generative role Building",
       'name="add" value="building"' in gen_html, True)
report("  and offers it no corpus_index", 'name="add" value="corpus_index"' in gen_html, False)
report("the live store and its generated copy were not touched",
       (rs.STORE.read_bytes(), rs.SERVING.read_bytes()), live_before)

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
