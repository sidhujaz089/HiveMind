#!/usr/bin/env python3
"""The Models tab's Roles pages (Jas, 2026-09-29, plan step 2), driven through their form handler on
a COPY of the role store in a temp directory -- never the live one."""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import role_store as rs  # noqa: E402
import roles_pages as rp  # noqa: E402

# THE STORE THIS SUITE RUNS ON, and no role name is assumed to still be in it (2026-10-25, t4122).
# This suite named the role "senior" outright until that role was deleted from ROLES.json, and then
# every run died with KeyError: 'senior' -- red on main, declared in the failure ceiling, and the
# ceiling is why the cause stayed nobody's. A live sample is not a premise (t3947, t2112).
# ROLES_SRC lets the same suite be run against a store WITH that role and one WITHOUT it, so neither
# shape is baked in; the default is the live store.
SRC_STORE = Path(os.environ.get("ROLES_SRC") or rs.STORE)

PASSED = FAILED = 0


def switchable(d, tags):
    """The role this suite drives: a MODEL role the store actually has, preferring one whose model is
    live so that model can be swapped for another live one. Never a hard-coded key."""
    roles = rs.model_roles(d)
    live = [k for k, r in roles.items() if r.get("model") in (tags or {})]
    return (live or list(roles) or [None])[0]


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def post(path, **form):
    f = {k: (v if isinstance(v, list) else [v]) for k, v in form.items()}
    return rp.handle_post(path, lambda k, d="": (f.get(k) or [d])[0].strip(), f)


with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    # WHICH FILES THE LIVE CHECKOUT STARTED WITH, so "the live store was not touched" is measured
    # rather than asserted: check() is not a witness to that (see the note at the end).
    LIVE_BEFORE = {p: (p.read_bytes() if p.exists() else None)
                   for p in (rs.STORE, rs.SERVING, rs.LOG, rs.FLOW_WORKERS)}
    for src, name in ((SRC_STORE, "ROLES.json"), (rs.SERVING, "SERVING.json")):
        shutil.copy(src, t / name)
    # FLOW_WORKERS IS REDIRECTED TOO (2026-10-25): role_store._save() calls write_projection() on every
    # save, and that writes the module's FLOW_WORKERS constant -- so a run of this suite from the live
    # checkout overwrote models/FLOW-WORKERS.json with the PINNED temp store's projection, which
    # check_workspace fails on as a stale projection. Measured: the file's mtime moves on a save.
    real = (rs.STORE, rs.SERVING, rs.LOG, rs.FLOW_WORKERS)
    rs.STORE, rs.SERVING, rs.LOG, rs.FLOW_WORKERS = (t / "ROLES.json", t / "SERVING.json",
                                                    t / "ROLES-CHANGELOG.jsonl", t / "FLOW-WORKERS.json")
    # THE MANAGER'S LIST IS PINNED, not whatever Jas left on the Models tab. Third break from a live
    # edit (09-30 entry 26; 10-02 he removed supervision, which the move-before case names). A red
    # suite now holds every build (t650), so a Models tab edit must not be able to turn this red.
    _d = json.loads((t / "ROLES.json").read_text())
    _d["roles"]["manager"]["tasks"] = [{"task": k, "interrupt": False, "teamwork": False}
                                       for k in ("supervision", "scoping", "console")]
    (t / "ROLES.json").write_text(json.dumps(_d))
    # BUILDS GO WHERE BUILDING IS LISTED (2026-10-02): not SERVING.json's hand-kept production_lanes.
    report("build lanes are the roles with Building on their list",
           rs.building_roles(_d), [k for k, r in rs.model_roles(_d).items() if r.get("model")
                                   and r.get("lanes") != 0     # 0 lanes = off (2026-10-19)
                                   and any(x.get("task") == "building" for x in r.get("tasks") or [])])
    report("  the Manager, without Building, gets no builds", "manager" in rs.building_roles(_d), False)
    try:
        tags, _ = rs.live_models()
        store0 = rs.load()
        target = switchable(store0, tags)
        # BOTH SHAPES, asserted here rather than assumed: the picker names a role the store HAS,
        # whether or not the role this suite used to name outright is still in it.
        for _label, _fixture in (("with the deleted Senior still in it",
                                 {"roles": {"senior": {"kind": "model", "model": "a"},
                                            "manager": {"kind": "model", "model": "b"}}}),
                                ("without it, as the live store is",
                                 {"roles": {"manager": {"kind": "model", "model": "b"}}})):
            _pick = switchable(_fixture, {"a", "b"})
            report(f"the driven role is read from the store, never a hard-coded name ({_label}): {_pick}",
                   bool(_pick) and _pick in _fixture["roles"], True)
        report(f"a role holding a live model exists to drive the Model page: {target}",
               bool(target) and target in store0["roles"], True)
        model0 = store0["roles"][target]["model"]
        other = next((x for x in sorted(tags) if x != model0), None)
        report("a second live model exists to switch the role to", other is not None, True)
        report("model: saved, and SERVING.json regenerated",
               (post(f"/models/{target}/model", model=other)[1], rs.load()["roles"][target]["model"],
                json.loads((t / "SERVING.json").read_text())["roles"][target]["tag"]), (None, other, other))
        post(f"/models/{target}/tools", tool=["read_file", "search_repo"])
        report("tools: exactly the ticked ones are on",
               sorted(k for k, v in rs.load()["roles"][target]["tools"].items() if v), ["read_file", "search_repo"])
        post("/models/manager/attributes", attr_num_ctx="65536", attr_keep_alive="5m", attr_temperature="0.7", attr_think="")
        report("attributes: numbers stay numbers, choices stay text, blank = default",
               rs.load()["roles"]["manager"]["attributes"], {"num_ctx": 65536, "keep_alive": "5m", "temperature": 0.7})
        # TWO TASKS NOT ALREADY ON THE MANAGER'S LIST, read from the store: this named script_review and
        # rca, and the test broke the day Jas put both on the Manager's list (2026-09-30, entry 26).
        have = {x["task"] for x in rs.load()["roles"]["manager"]["tasks"]}
        extra = [t for t in ("script_review", "rca", "preflight", "corpus_index", "done_check") if t not in have][:2]
        post("/models/manager/tasks", op="add", add=extra)
        report("tasks: + Add Task appends the ticked tasks at the bottom",
               [x["task"] for x in rs.load()["roles"]["manager"]["tasks"]][-2:], extra)
        post("/models/manager/tasks", op="move", task="rca", how="top")
        post("/models/manager/tasks", op="move", task="script_review", how="before", before="supervision")
        report("tasks: to top, then Move before...",
               [x["task"] for x in rs.load()["roles"]["manager"]["tasks"]][:3], ["rca", "script_review", "supervision"])
        post("/models/manager/tasks", op="flags", task="rca", teamwork="on")
        r = next(x for x in rs.load()["roles"]["manager"]["tasks"] if x["task"] == "rca")
        report("tasks: a stale teamwork field is ignored and not stored (Team Work removed 2026-10-14)",
               ("teamwork" in r, r["interrupt"]), (False, False))
        post("/models/manager/tasks", op="remove", task="rca")
        report("tasks: minus removes it", "rca" in [x["task"] for x in rs.load()["roles"]["manager"]["tasks"]], False)
        # WHICHEVER ROLE IS THE ONLY ONE DOING SOME TASK, read from the store (2026-10-01: the Senior
        # now builds too, so "coder" was no longer the only builder and the test went stale).
        _d = rs.load()
        _only = next(((k, t) for k in rs.model_roles(_d) for t in rs.orphaned_by_delete(_d, k)), None)
        if _only:
            where, err = post(f"/models/{_only[0]}/delete")
            report("delete refused while the role is the only one doing a task", (_only[1] in (err or "")), True)
        where, err = post("/models/new", name="Reviewer", kind="model", model=other)
        report("new role: added, and lands on its page", (err, where, "reviewer" in rs.load()["roles"]), (None, "/models/reviewer", True))
        report("  a model role appears in SERVING.json", "reviewer" in json.loads((t / "SERVING.json").read_text())["roles"], True)
        where, err = post("/models/new", name="Blank", kind="model")
        report("a model role can be created without a model, and lands on its Model page",
               (err, where, rs.load()["roles"]["blank"]["model"]), (None, "/models/blank/model", ""))
        report("  it is not written to SERVING.json and the store still checks clean",
               ("blank" in json.loads((t / "SERVING.json").read_text())["roles"],
                # THE ROLE-LEVEL PASS ONLY, the one check_workspace.py:1603 runs (fk=False). The
                # foreign-key pass fails on FLOW.json still naming the deleted role Senior, which is
                # Jas's chart and not something this suite can fix; asking for it here made the
                # assertion unachievable on main, which is how it stayed red unnoticed (t4122).
                rs.check(serving=t / "SERVING.json", fk=False)), (False, []))
        post("/models/blank/delete")
        where, err = post("/models/reviewer/delete")
        report("delete allowed when nothing is orphaned", (err, "reviewer" in rs.load()["roles"]), (None, False))
        # THE ENTRY IS FOUND BY ITS WRITE, not by the role's name: role_store logs a model change as
        # "model <old> -> <new>" with no role in the text, so the old startswith("Senior") matched
        # nothing even on a store that still had the role. The suite starts with an empty changelog.
        n = next(e["n"] for e in rs.entries()
                 if e["what"].startswith(f"{store0['roles'][target].get('name', target)}: model "))
        post(f"/models/{target}/revert", n=str(n))
        report(f"revert restores the model {target} had", rs.load()["roles"][target]["model"],
               json.loads(SRC_STORE.read_text())["roles"][target]["model"])
        report("every change is in the changelog", len(rs.entries()) >= 10, True)
        for page in (rp.index_page, rp.new_page):
            page()
        # EVERY ROLE PAGE THAT HAS A ROLE BEHIND IT, read from the store: this named manager, senior
        # and claude, and senior no longer exists (t4122).
        _d = rs.load()
        render = [k for k in ("manager", target) if k in _d["roles"]]
        person = next((k for k, r in _d["roles"].items() if r.get("kind") == "person"), None)
        if person and person not in render:
            render.append(person)
        for k in render:
            rp.role_page(k)
            rp.tasks_page(k)
            rp.tasks_page(k, moving=(rs.load()["roles"][k]["tasks"] or [{"task": None}])[0]["task"])
        # The Model/Tools/Attributes pages only exist for a role that has a model.
        for k in [k for k, r in _d["roles"].items()
                  if r.get("kind", "model") == "model" and r.get("model")][:2]:
            rp.model_page(k)
            rp.tools_page(k)
            rp.attributes_page(k)
        report("every page renders on the edited store", True, True)
    finally:
        rs.STORE, rs.SERVING, rs.LOG, rs.FLOW_WORKERS = real
# MEASURED, not asked of check(): this assertion used to be check() == [], which was never a witness to
# "not touched" and could not pass either -- role_store.check() runs the FLOW foreign-key pass that
# check_workspace deliberately runs with fk=False, and FLOW.json still names the deleted role Senior.
report("the live store, its changelog and the chart projection are untouched",
       {p: (p.read_bytes() if p.exists() else None) for p in LIVE_BEFORE}, LIVE_BEFORE)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
