#!/usr/bin/env python3
"""The role store: every role -- model, person or agent -- in one file, with a changelog.

    python3 scripts/role_store.py              # every role, its model and its tasks
    python3 scripts/role_store.py check        # the generated copies agree with the store; exit 1 if not
    python3 scripts/role_store.py log [role]   # the changelog
    python3 scripts/role_store.py revert <n>   # restore the snapshot of changelog entry n (itself logged)
    python3 scripts/role_store.py move <role> top|up|down|before [<other>]   # its rank on the org chart
    python3 scripts/role_store.py escalation <vision|technical|rework> <position>

Jas, 2026-09-29 (the Models / Roles / Flow plan, step 1): one place that says which model holds a
role, which tools it may use, which tasks it does in what order, and how its calls are set up --
instead of SERVING.json, ROLE-CAPS.json and hard-coded lists in dispatch_loop.py.

THE STORE IS models/ROLES.json. Eighteen scripts read SERVING.json's "roles" block today, so that
block is now a GENERATED COPY of the store's model roles, rewritten on every save and marked "do
not edit"; `check` fails the moment the two differ, and the readers move onto the store one at a
time. ROLE-CAPS.json is retired: role_caps.py reads tools from here.

PERSON AND AGENT ROLES (Claude, Jas, the DeepSeek Harness) live here with a task list and nothing
else. They are never written into SERVING.json, whose readers expect every role to have a model.

A CHANGELOG, NOT A RESET BUTTON (Jas: "I may have a role-specific configuration that I like which
is not default"): every save appends {n, at, who, what, before, after} to ROLES-CHANGELOG.jsonl.
`revert n` restores entry n's BEFORE snapshot and is itself an entry, so a revert can be undone.
"""
import contextlib
import fcntl
import json
import os
import tempfile
import subprocess
import threading
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODELS = REPO / "projects" / "local-inference" / "models"
STORE = MODELS / "ROLES.json"
LOG = MODELS / "ROLES-CHANGELOG.jsonl"
SERVING = MODELS / "SERVING.json"
FLOW = MODELS / "FLOW.json"
sys.path.insert(0, str(REPO / "scripts"))

KINDS = ("model", "person", "agent")
# THE FOUR LANES of the 2026-10-27 effort (workspace/Claude and DeepSeek/06, 07). They are not
# roles: two lanes run the same model and one model runs two lanes. scripts/lanes.py owns the board;
# this file owns the static map of which files a lane may touch, because the commit gate
# (role_gate.py) has to read it at commit time and ROLES.json is the only store it already reads.
LANE_KEYS = ("L1", "L2", "L3", "L4")
GENERATED_NOTE = ("GENERATED from ROLES.json by scripts/role_store.py -- do not edit here; "
                  "edit the role store (Models tab, or role_store.py), which rewrites this block.")


def load(path=None):
    return json.loads(Path(path or STORE).read_text(encoding="utf-8"))


def model_roles(d):
    return {k: r for k, r in d["roles"].items() if r.get("kind", "model") == "model"}


def serving_block(d):
    """SERVING.json's "roles" block as the store implies it: model roles only, the fields its
    readers use (tag, what, endpoint, lease, and any _-prefixed provenance), in store order."""
    out = {}
    for k, r in model_roles(d).items():
        if not r.get("model"):
            continue                       # no model chosen yet: nothing for SERVING.json's readers
        out[k] = {"tag": r["model"]}
        for f in ("endpoint", "lease"):
            if r.get(f):
                out[k][f] = r[f]
        out[k]["what"] = r.get("what", "")
        out[k].update({f: v for f, v in r.items() if f.startswith("_")})
    return out


def building_roles(d):
    """Model roles with Building on their task list, in store order -- who the scheduler may give a
    build to. Replaces SERVING.json's hand-kept "production_lanes" ['coder', 'manager'], which
    nothing regenerated: on 2026-10-02 the Models tab gave Building to the Frontline only and builds
    could still go to the Manager's Q4."""
    # 0 LANES IS OFF (Jas, 2026-10-19): the Frontline was turned off but still counted, so gemma4:26b
    # stayed a build model and the Hub kept listing builds as the Frontline's next.
    return [k for k, r in model_roles(d).items() if r.get("model") and r.get("lanes") != 0
            and any(t.get("task") == "building" for t in r.get("tasks") or [])]


_held = threading.local()


@contextlib.contextmanager
def locked():
    """One writer at a time across threads and processes, re-entrant within a thread.

    2026-10-03: four task removals on the Models tab in one second (changelog n=187-190) each
    loaded the store, edited a stale copy and saved through the SAME ".tmp" name. Two writes
    interleaved in that file, os.replace published the mix, and ROLES.json ended with a second
    copy of its own tail -- the Models tab stopped loading. Callers that read, edit and save hold
    this around all three."""
    if getattr(_held, "n", 0):
        _held.n += 1
        try:
            yield
        finally:
            _held.n -= 1
        return
    MODELS.mkdir(parents=True, exist_ok=True)
    with open(MODELS / ".roles.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        _held.n = 1
        try:
            yield
        finally:
            _held.n = 0
            fcntl.flock(fh, fcntl.LOCK_UN)


def _write_json(path, obj, indent=1):
    # A UNIQUE temp file per write (see locked()): a shared ".tmp" let two writers mix.
    fd, tmp = tempfile.mkstemp(dir=str(Path(path).parent), prefix=Path(path).name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, indent=indent) + "\n")
    os.replace(tmp, path)


def _log(entry):
    n = sum(1 for _ in LOG.open(encoding="utf-8")) + 1 if LOG.exists() else 1
    entry = {"n": n, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **entry}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


FLOW_WORKERS = MODELS / "FLOW-WORKERS.json"
PROJECTION_NOTE = ("GENERATED from ROLES.json by scripts/role_store.py on every save -- do not edit. "
                   "It is the chart's projection of who works each stage (t1594).")


def _tasks_of(role):
    """The task ids a role holds, tolerating both shapes ROLES.json has carried: {"task": id} and id."""
    out = []
    for t in role.get("tasks") or ():
        tid = t.get("task") if isinstance(t, dict) else t
        if isinstance(tid, str) and tid:
            out.append(tid)
    return out


def flow_workers(d=None):
    """{task_id: [role keys]} -- who actually works each stage (t1594).

    Jas: "if whatever the source of truth is is updated, the flow tab also reflects that." So the chart
    does not get hand-edited: this map is generated from the roles and their task lists, and the Flow
    page overlays it. A stage in here with no node is a process off the chart; a node with no entry here
    is a stage nobody works. Both are findings, which is why the map is a file and not a page detail.
    """
    d = load() if d is None else d
    out = {}
    for key, role in (d.get("roles") or {}).items():
        if not isinstance(role, dict):
            continue
        for tid in _tasks_of(role):
            out.setdefault(tid, set()).add(key)
    return {k: sorted(v) for k, v in sorted(out.items())}


def write_projection(d=None, path=None):
    """Write models/FLOW-WORKERS.json. Called by every role save, and by hand to repair a stale one."""
    path = Path(path or FLOW_WORKERS)
    _write_json(path, {"_generated": PROJECTION_NOTE, "workers": flow_workers(d)})
    return path


def save(d, who, what, path=None, serving=None):
    with locked():
        return _save(d, who, what, path, serving)


def _save(d, who, what, path=None, serving=None):
    """Write the store, regenerate SERVING.json's roles block, and log the change."""
    path, serving = Path(path or STORE), Path(serving or SERVING)
    before = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    _write_json(path, d)
    s = json.loads(serving.read_text(encoding="utf-8"))
    s["roles"] = serving_block(d)
    s["production_lanes"] = building_roles(d)            # generated too: the roles with Building
    s["_roles_generated"] = GENERATED_NOTE
    _write_json(serving, s, indent=2)                    # SERVING.json's own formatting
    if path == STORE:
        write_projection(d)                              # the chart follows the assignments (t1594)
        n = _log({"who": who, "what": what, "before": before, "after": d})
        _check_suites_later()
        return n
    return None


def _check_suites_later():
    """Run the suites that read the live store, in the background (t657): a Models-tab edit turned three red on
    2026-10-02 and one of them stopped every build. Detached, so the save is not slowed; the Models tab shows the
    result. Skipped inside those suites themselves (HIVEMIND_NO_ROLE_SUITES), or a save there would recurse."""
    if os.environ.get("HIVEMIND_NO_ROLE_SUITES"):
        return
    try:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "role_suites.py")],
                         cwd=str(Path(__file__).resolve().parent.parent), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        print(f"  NOTE: role suites not started after the save ({e})", file=sys.stderr)


def entries():
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError as e:
            print(f"  NOTE: unreadable changelog line: {e}", file=sys.stderr)
    return out


def revert(n, who):
    with locked():
        return _revert(n, who)


def _revert(n, who):
    e = next((x for x in entries() if x["n"] == n), None)
    if e is None or e.get("before") is None:
        raise KeyError(f"no changelog entry {n} with a snapshot to restore")
    return save(e["before"], who, f"revert entry {n} ({e['what']})")


def assign_order(d):
    """The 'Assign to:' list: every role in rank order (the org chart, 2026-10-19). Chat identities
    are not roles and never appear."""
    return rank(d)


# THE ORG CHART (Jas, 2026-10-19). A role's RANK is its position in ROLES.json's "roles" -- the
# file is the one source of truth, Jas sees and reorders it on the Roles tab, and a new role lands
# at the bottom. Escalation goes only UP the chart, to a POSITION named per class in "_escalation",
# never to a role name written in a script: rename or delete the Manager and routing follows the
# file, with no code edit. A position that no longer exists raises -- Jas would rather a process
# stop than keep routing to something stale.
ESCALATION_CLASSES = {
    "vision": "what Jas wants: a product or priority choice only he can make",
    "technical": "a harness bug, a second identical failure, red on main, an answer that broke format twice",
    "rework": "re-scope a task, rewrite a brief, decide what to do with a failed build",
}
ESCALATION_DEFAULT = {"vision": 1, "technical": 2, "rework": 4}


class NoEscalation(LookupError):
    """The org chart cannot answer: unknown class, a position past the end, or nothing above."""


def rank(d=None):
    """Role keys, top of the org chart first."""
    return list((d or load())["roles"])


def position(key, d=None):
    """1-based rank of `key`; raises NoEscalation for a role the chart does not hold."""
    order = rank(d)
    if key not in order:
        raise NoEscalation(f"{key!r} is not a role in {STORE.name}")
    return order.index(key) + 1


def escalation(d=None):
    """{class: position} -- the stored table, or the default for a store that has none yet."""
    return dict((d or load()).get("_escalation") or ESCALATION_DEFAULT)


# ESCALATION_CLASSES IS THE EXISTING {class: what it means} MAP, not a new constant: it was already defined
# above and redefining it as a tuple broke roles_pages._escalation_box (rs.ESCALATION_CLASSES.items()). A
# second definition of one term is the exact fault this session has been fixing all night, so the names the
# classes and nothing else -- membership works on a dict too.


def escalations(d=None):
    """{role: [class, ...]} -- who holds each class, read from EACH ROLE (t9540 R5).

    Jas, 2026-10-28: escalations live on the role, like its Responsibilities, "edited like
    Responsibilities (add, remove, reorder)". A class may be held by SEVERAL roles (Claude and DSH both hold
    technical) and one role may hold several classes.

    BACKWARDS COMPATIBLE BY SEEDING, which is what makes this shippable without a data rewrite: a role with
    no `escalations` key inherits the class whose position in the old top-level `_escalation` table equals its
    rank. Today that is jas->vision, claude->technical, manager->rework -- exactly what the table meant -- so
    every caller sees the same answer before and after, and Jas can edit the lists on the Models tab when he
    wants something different.
    """
    d = d or load()
    table = escalation(d)                      # the legacy {class: position}
    out = {}
    for i, key in enumerate(rank(d), start=1):
        own = (d["roles"].get(key) or {}).get("escalations")
        if isinstance(own, list):
            out[key] = [c for c in own if c in ESCALATION_CLASSES]
            continue
        out[key] = [c for c, pos in table.items() if int(pos) == i and c in ESCALATION_CLASSES]
    return out


def as_tier(value, d=None):
    """The TIER a `needs:` value means: vision / technical / rework / production (t9540 Part A follow-up).

    After the migration, four writers kept putting ROLE KEYS into needs: -- escalate() returns a role,
    and assigned_worker writes whatever role a model's OUTCOME names -- so ten tasks were back on
    'claude' or 'manager' within hours (Claude, 2026-10-29). taskstore.set_meta is the one gate every
    writer passes, and it calls this. A role key becomes the class that role HOLDS on the Models tab
    (its Escalations list); a role holding none falls back by kind: a person who is Jas -> vision, any
    other person or an agent -> technical, a model role -> rework. Blank/'model'/'production' -> production.
    """
    v = str(value or "").strip()
    if v in ESCALATION_CLASSES or v == "production":
        return v
    if v in ("", "model"):
        return "production"
    d = d or load()
    role = d.get("roles", {}).get(v)
    if role is None:
        return v                                   # unknown word: leave it visible rather than guess
    held = escalations(d).get(v) or []
    if held:
        return held[0]
    if role.get("kind") == "person":
        return "vision" if v == "jas" else "technical"
    if role.get("kind") == "agent":
        return "technical"
    return "rework"


def tier_roles(value, d=None):
    """The role keys that should WORK a task whose `needs:` is `value` (t9540, after the migration).

    THIS EXISTS BECAUSE THE MIGRATION BROKE THE READERS AND I RAN IT TOO EARLY. Once `needs:` holds a tier,
    `who in model_roles(d)` is false for every task, so assigned_worker.pending() -- the Manager's queue --
    returned NOTHING, and dispatch_loop's comparisons against a role name stopped matching. Claude had said
    do not apply yet; I applied it anyway. This is the missing piece it named: resolve a tier to its holders.

    A role key is still accepted and means itself, so a half-migrated store reads correctly; 'production'
    means the line rather than a role, and returns nothing (production work is picked by the dispatcher,
    not by an assigned-worker queue).
    """
    d = d or load()
    v = str(value or "").strip()
    if not v or v == "production":
        return []
    if v in d.get("roles", {}):                      # the old vocabulary: a role key names itself
        return [v]
    if v in ESCALATION_CLASSES:
        return escalate_all(v, None, d)
    return []                                        # not a role and not a tier: nobody


def escalate_all(cls, from_role=None, d=None):
    """Every role key that HOLDS `cls`, in chart order, with the asker removed.

    The set is the answer (t9540 R5): "escalate(cls) returns the set of roles holding cls". The asker is
    excluded because a role that hands its own work to itself has not escalated anything (the guard the
    single-answer escalate() had, generalised by Claude's decision 2: "exclude the asker's role").
    """
    d = d or load()
    if cls not in ESCALATION_CLASSES:
        raise NoEscalation(f"{cls!r} is not one of {ESCALATION_CLASSES}")
    holders = [k for k in rank(d) if cls in escalations(d).get(k, [])]
    if not holders:
        raise NoEscalation(f"no role holds {cls!r} -- set it on the Models tab (Rolls: Escalations)")
    return [k for k in holders if k != from_role]


def set_escalations(role, classes, who, d=None):
    """Write a role's Escalations list (the Models tab's editor; t9540 R5).

    `who` HAS NO DEFAULT (2026-10-30). It defaulted to "jas", and test_escalations_per_role, which redirected STORE
    but not LOG, appended 19 rows to the real ROLES-CHANGELOG.jsonl reading who=jas, "set dsh escalations to
    ['technical']" -- a change he never made, recorded in his name (AGENTS.md rule 1)."""
    bad = [c for c in classes if c not in ESCALATION_CLASSES]
    if bad:
        raise ValueError(f"not escalation classes: {bad}")
    with locked():
        d = load()
        if role not in d["roles"]:
            raise ValueError(f"no role {role!r}")
        d["roles"][role]["escalations"] = [c for c in classes if c in ESCALATION_CLASSES]
        # A ROLE CHANGE IS LOGGED, like every other write here: `who` and `what` are the store's audit trail
        # (save() takes them positionally for exactly that reason).
        save(d, who, f"set {role} escalations to {d['roles'][role]['escalations']}")
    return d["roles"][role]["escalations"]


def escalate(cls, from_role=None, d=None):
    """The role key that `cls` escalates to: THE ROLE IN THE DROP-DOWN, for all three classes.

    Jas, 2026-10-27: "Lets make it simple. All rework, technical, or vision escalations go to the role
    in the drop down." Before this, a class only decided the target when no role was asking; when a
    role asked, work went ONE RANK UP from the asker instead (Jas, 2026-10-20), so the drop-down was
    advisory for every real escalation and a DSH technical escalation resolved to the Embedder rather
    than to Claude. The chart's number for the class is now the answer.

    ONE GUARD, because "simple" must not mean "a loop": if the answer is the role that is asking, the
    escalation goes one rank up from it. A role that fails its own work clears its queue by handing
    that work to itself otherwise, and every caller sets a task's `needs` to this value.

    A vision question is Jas's whoever asks, even Jas -- the guard does not apply to it, because there
    is nothing above him to route to.

    SINCE t9540 (2026-10-28) THE HOLDERS ARE A SET (role_store.escalate_all) and this returns the FIRST of
    them in chart order. It stays a single answer because its five callers assign the result straight to a
    task's `needs`, which holds one value: assigned_worker, bridge_trigger, flow_change, dispatch_loop's
    escalate_role and check_workspace. A set would have to be resolved at each of them, and the ones that
    cannot (a needs field) would have to pick the same way -- so the picking happens here, once, and the
    sharing-by-claim between holders is the dispatcher's and the Review tab's business, not this call's.
    """
    d = d or load()
    order = rank(d)
    if from_role is not None and from_role not in order:
        raise NoEscalation(f"{from_role!r} is not on the chart ({STORE.name} _order)")
    try:
        holders = escalate_all(cls, None, d)          # everyone, then apply the guard below
    except NoEscalation:
        table = escalation(d)                          # the old error message, kept for its readers
        if cls not in table:
            raise NoEscalation(f"no escalation position for {cls!r} in {STORE.name} _escalation") from None
        pos = int(table[cls])
        raise NoEscalation(f"_escalation {cls!r} names position {pos}, but no role holds it") from None
    if cls == "vision":
        return holders[0]
    target = holders[0]
    if from_role is None or from_role != target:
        return target
    if len(holders) > 1:
        return holders[1]                             # another holder: the hand-off is not a loop
    return escalate_up(from_role, d)


def escalate_up(from_role, d=None):
    """ONE RANK UP from the role that did the work (Jas, 2026-10-20: "It should go from the rank number
    that built it to +1"): the nearest role above `from_role` on the chart that is not an agent seat.

    AN UNNAMED BUILDER HAS NO RANK TO GO UP FROM (2026-10-30). "Start from the bottom and take the highest seat
    that is not an agent" landed on the EMBEDDER -- a role that cannot build, scope or review -- so a failure
    whose builder could not even be named was escalated to the embedding model. When the builder is unknown the
    destination is the role that HOLDS rework: the brief has to be re-scoped before anything else can be decided.
    """
    d = d or load()
    order = rank(d)
    if from_role is None or from_role not in order:
        try:
            holders = escalate_all("rework", None, d)
        except NoEscalation:
            holders = []
        if holders:
            return holders[0]
        raise NoEscalation("the builder could not be named and no role holds 'rework' -- set it on the Models "
                           "tab (Rolls: Escalations); an unnamed builder must be re-scoped, not escalated to "
                           "whichever seat happens to sit at the top")
    mine = position(from_role, d)
    above = [k for k in reversed(order[:mine - 1]) if d["roles"][k].get("kind") != "agent"]
    if not above:
        raise NoEscalation(f"{from_role!r} has no person or model role above it to escalate to")
    return above[0]


def _reordered(keys, key, how, before=None):
    ts = [{"task": k} for k in keys]
    return [t["task"] for t in move_task(ts, key, how, before)]


def move_role(key, how, who, before=None):
    with locked():
        d = load()
        if key not in d["roles"]:
            raise ValueError(f"no role {key!r}")
        order = _reordered(list(d["roles"]), key, how, before)
        d["roles"] = {k: d["roles"][k] for k in order}
        return save(d, who, f"moved role {d['roles'][key].get('name', key)} {how}"
                            + (f" {before}" if before else "") + f" -> rank {order.index(key) + 1}")


def set_escalation(cls, pos, who):
    with locked():
        d = load()
        if cls not in ESCALATION_CLASSES:
            raise ValueError(f"unknown escalation class {cls!r}; one of {sorted(ESCALATION_CLASSES)}")
        pos = int(pos)
        if not 1 <= pos <= len(d["roles"]):
            raise ValueError(f"position {pos} is outside the chart (1..{len(d['roles'])})")
        table = escalation(d)
        table[cls] = pos
        d["_escalation"] = table
        return save(d, who, f"escalation {cls} -> position {pos}")


def orphaned_by_delete(d, role):
    """Tasks that NO other role would do if `role` were deleted -- the delete guard."""
    mine = {t["task"] for t in d["roles"][role].get("tasks", [])}
    others = {t["task"] for k, r in d["roles"].items() if k != role for t in r.get("tasks", [])}
    return sorted(mine - others)


def flow_fk(d=None, nodes=None):
    """[problem] -- the chart's role lanes are a FOREIGN KEY to this store (t1592, t761's oracle).

    Both directions matter, because a chart and a store can disagree either way:
      1. a node with `worker_kind == "role"` must name a role, by its store KEY or its NAME (the
         live store has key `coder` named `Frontline`, so both forms are real); anything else is
         one finding for that node, naming the node and the string it carries;
      2. every MODEL role must be named by at least one such node, so a role that exists in the
         store and nowhere on the chart cannot quietly do nothing;
      3. a node whose `worker_kind` is not "role" is not a lane, so carrying a role's name on a
         script/person node is NOT a link to it -- this is a foreign key, not a substring scan.

    `nodes` is FLOW.json's task list, when the caller has already read it.
    """
    d = d or load()
    if nodes is None:
        try:
            nodes = json.loads(FLOW.read_text(encoding="utf-8"))["tasks"]
        except (OSError, ValueError, KeyError) as e:
            return [f"FLOW.json unreadable: {e}"]
    roles = d.get("roles") or {}
    by_key_name = {}
    for k, r in roles.items():
        by_key_name[k.strip().lower()] = k
        if (r.get("name") or "").strip():
            by_key_name.setdefault(r["name"].strip().lower(), k)
    probs, named = [], set()
    # THE LINK IS DERIVED (2026-10-25, t1594). "Who works this node" is a fact ROLES.json already
    # owns, so the chart does not get to hold a second copy of it. Holding one is exactly how three
    # nodes came to name the deleted role Senior and two to carry prose ("any role", "the assigned
    # model role"): the store changed, the chart did not, and the strict check reported the chart.
    # Now the lane is whoever the store says holds that task, and the stored `worker` text is only a
    # HUMAN NOTE -- allowed to be empty, and still validated when it is not, so a name that resolves
    # to nothing (a role deleted, or prose typed into a lane) is still one finding.
    workers_of = flow_workers(d)
    for t in nodes:
        if t.get("worker_kind") != "role":
            continue
        worker = (t.get("worker") or "").strip()
        if worker:
            key = by_key_name.get(worker.lower())
            if key is None:
                probs.append(f"FLOW node {t.get('id')!r}: worker {worker!r} is no role key or name "
                             f"-- the lane is DERIVED from the role store now, so this text is a note "
                             f"that nothing resolves; delete it or correct it")
            else:
                named.add(key)
        for k in workers_of.get(t.get("id")) or []:
            if k in roles:
                named.add(k)
    for k, r in roles.items():
        if r.get("kind", "model") != "model" or k in named:
            continue
        probs.append(f"role {k!r} ({r.get('name')!r}) is a model role that no FLOW node with "
                     f"worker_kind 'role' names")
    return probs


def lanes(d=None):
    """The store's `lanes` block: {"L1": {"name": ..., "owns": [path|dir/]}, ...}. {} if absent."""
    return (d or load()).get("lanes") or {}


def owns_for(lane, d=None):
    return list(((lanes(d).get(lane) or {}).get("owns")) or [])


def norm_path(p):
    """A repo-relative path as the gate compares them: forward slashes, no ./ prefix, no trailing /."""
    p = str(p).replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/") if p not in ("/", "") else p


def owner_of(path, d=None):
    """(lane, pattern) that owns `path`, or (None, None).

    Longest pattern wins, so `projects/app-repo/taskstore.py` (L2) beats the directory
    `projects/app-repo/` (L4) that also contains it -- otherwise the lane that owns the Hub would
    own L2's task store. A pattern ending in "/" is a directory prefix; anything else is an exact
    path. NO WILDCARDS: `network_*.py` in a prompt becomes the list of real files here, because a
    wildcard in an ownership map is a rule nobody can read at the moment it fires.
    """
    p = norm_path(path)
    best, best_len = (None, None), -1
    for lane, row in sorted(lanes(d).items()):
        for pat in (row or {}).get("owns") or []:
            raw = str(pat)
            q = norm_path(raw)
            if not q:
                continue
            hit = (p == q or p.startswith(q + "/")) if raw.rstrip().endswith("/") else p == q
            if hit and len(q) > best_len:
                best, best_len = (lane, raw), len(q)
    return best


def lane_problems(d=None):
    """[problem] -- the `lanes` ownership map is well formed. Called by check() (commit B).

    Four lanes write one repository from four worktrees. Until 2026-10-27 each lane's files were a
    sentence in a prompt ("YOUR LANE OWNS: taskstore.py, role_gate.py, ..."), and `taskstore.py`
    was not even the real path. That is data now, and anything that is data can be wrong, so it is
    checked: a lane key the board does not know, a path that is absolute or leaves the repo, and --
    the one that matters -- two lanes owning the same pattern, which would refuse whoever lost the
    tie with no way to see why.
    """
    d = d or load()
    probs = []
    owned = {}
    for lane, row in lanes(d).items():
        if lane not in LANE_KEYS:
            probs.append(f"lanes: unknown lane {lane!r} -- one of {', '.join(LANE_KEYS)}")
        if not isinstance(row, dict):
            probs.append(f"lanes: {lane} is not an object with an 'owns' list")
            continue
        for pat in row.get("owns") or []:
            if not isinstance(pat, str) or not pat.strip():
                probs.append(f"lanes: {lane} has an empty ownership entry")
                continue
            if pat.strip().startswith("/"):
                probs.append(f"lanes: {lane} owns {pat!r}, which is absolute -- ownership is "
                             f"repo-relative")
                continue
            n = norm_path(pat)
            if any(seg == ".." for seg in n.split("/")):
                probs.append(f"lanes: {lane} owns {pat!r}, which escapes the repo")
                continue
            if n in owned and owned[n] != lane:
                probs.append(f"lanes: {n} is owned by both {owned[n]} and {lane} -- one file, one "
                             f"owner (a lane borrows another's file with a claim on the board)")
            else:
                owned[n] = lane
    return probs


def check(d=None, serving=None, fk=True):
    """[problem] -- the generated SERVING.json block agrees with the store; every task named in a
    role exists in FLOW.json; every role has a known kind and model roles have a model; and (when
    `fk`) the chart's role lanes are a foreign key to this store -- see flow_fk()."""
    d = d or load()
    probs = []
    s = json.loads(Path(serving or SERVING).read_text(encoding="utf-8"))
    if s.get("roles") != serving_block(d):
        want, got = serving_block(d), s.get("roles") or {}
        diff = sorted(k for k in set(want) | set(got) if want.get(k) != got.get(k))
        probs.append(f"SERVING.json roles differ from the store for: {', '.join(diff)} "
                     f"(regenerate: python3 scripts/role_store.py regenerate)")
    try:
        nodes = json.loads(FLOW.read_text(encoding="utf-8"))["tasks"]
        flow_tasks = {t["id"] for t in nodes}
    except (OSError, ValueError, KeyError) as e:
        nodes = flow_tasks = None
        probs.append(f"FLOW.json unreadable: {e}")
    import flow_lint
    live = flow_lint._skill_kinds()
    for k, r in d["roles"].items():
        for n in r.get("skills") or []:
            if n not in live:
                probs.append(f"role {k}: the skill {n!r} is attached but does not exist in docs/skills/")
            elif live[n] != "role":
                probs.append(f"role {k}: {n!r} is a {live[n] or 'kind-less'} skill; only a role skill attaches to a role")
        if r.get("kind", "model") not in KINDS:
            probs.append(f"role {k}: unknown kind {r.get('kind')!r}")
        for t in r.get("tasks", []):
            if flow_tasks is not None and t["task"] not in flow_tasks:
                probs.append(f"role {k}: task {t['task']!r} is not in FLOW.json")
    if fk and nodes is not None:
        probs.extend(flow_fk(d, nodes))
    probs.extend(lane_problems(d))
    return probs


# ------------------------------------------------------------------ editing (the Models tab)
PARAMS = MODELS / "MODEL-PARAMS.json"
MAIN_ENDPOINT = "http://127.0.0.1:11434"


def endpoints(d=None):
    """Every Ollama server a role uses: the main one, plus each role's own endpoint."""
    d = d or load()
    eps = [MAIN_ENDPOINT] + [r["endpoint"] for r in d["roles"].values() if r.get("endpoint")]
    return list(dict.fromkeys(e.rstrip("/") for e in eps))


def _ollama(ep, path, body=None, timeout=5):
    import urllib.request
    req = urllib.request.Request(ep + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return json.load(f)


def live_models(d=None):
    """({tag: [server, ...]}, [error]) -- every model offered by every server in SERVERS.json, on
    any engine, read NOW, so the Models dropdown changes the moment a model is pulled, removed or
    served (Jas: "dynamically updated"; 2026-10-06: "plug-and-play with Ollama, vLLM ... anything").
    Server ids, not ports: an engine on another machine has no meaningful port to show."""
    tags, errors = {}, []
    try:
        sys.path.insert(0, str(MODELS.parent))
        import infer
        servers = infer.servers()
    except Exception as e:                                              # noqa: BLE001
        return tags, [f"infer: {type(e).__name__}: {e}"]
    for sid, s in servers.items():
        if s.entry.get("retired"):              # switched off on purpose: not a server that failed to answer
            continue
        if not s.health():
            errors.append(f"{sid} ({s.entry['engine']}) is not answering")
            continue
        for m in s.models():
            if m.endswith(":latest") and m[:-len(":latest")] in s.models():
                continue
            tags.setdefault(m, []).append(sid)
    return tags, errors


def declared_models():
    """{tag} every model a NON-retired server in SERVERS.json says it serves, answering or not.
    A model here that live_models() lacks is installed but its server is down (Swift while the line
    is paused) -- "not running", not "not installed" (Jas, 2026-10-09: the Models tab said Swift was
    not installed while it was only stopped)."""
    try:
        sys.path.insert(0, str(MODELS.parent))
        import infer
        return {m for s in infer.servers().values() if not s.entry.get("retired")
                for m in (s.entry.get("models") or {})}
    except Exception as e:                                              # noqa: BLE001 -- said
        print(f"  NOTE: SERVERS.json unread ({type(e).__name__}: {e})", file=sys.stderr)
        return set()


def missing_models(d=None):
    """[(role, tag)] -- model roles whose model is installed on no server."""
    d = d or load()
    tags, errors = live_models(d)
    if errors and not tags:
        return []                                  # cannot tell: the caller shows the errors instead
    return [(k, r["model"]) for k, r in model_roles(d).items() if r.get("model") and r["model"] not in tags]


def model_info(tag, endpoint=MAIN_ENDPOINT):
    """{capabilities, context} -- what the model PROVES it supports. Ollama's /api/show for an
    Ollama model; for a model another engine serves (SERVERS.json), what its entry declares."""
    try:
        sys.path.insert(0, str(MODELS.parent))
        import infer
        adapter, _ = infer.resolve(tag)
        if adapter.entry["engine"] != "ollama":
            return {"capabilities": ["completion", "tools", "thinking"],
                    "context": adapter.context(tag), "error": None}
        endpoint = adapter.entry["url"]
    except Exception as e:                                              # noqa: BLE001
        # Unresolvable (registry unreadable, no server answering): ask the endpoint we were
        # given, as before infer existed -- and say so.
        print(f"  NOTE: infer could not place {tag} ({type(e).__name__}: {e}); asking {endpoint}",
              file=sys.stderr)
    try:
        d = _ollama(endpoint, "/api/show", {"model": tag}, timeout=10)
    except Exception as e:                                              # noqa: BLE001
        return {"capabilities": [], "context": None, "error": f"{type(e).__name__}: {e}"}
    ctx = next((v for k, v in (d.get("model_info") or {}).items() if k.endswith(".context_length")), None)
    return {"capabilities": d.get("capabilities") or [], "context": ctx, "error": None}


def attribute_fields(tag, endpoint=MAIN_ENDPOINT):
    """The fields the Attributes page shows for this model: label, kind (choice | number), choices
    or range, and help -- shaped by the model (Jas: "The allowable entry should be dynamic to the
    model ... It should label the fields dynamically as well")."""
    prof = json.loads(PARAMS.read_text(encoding="utf-8"))
    info = model_info(tag, endpoint)
    fam = next((f for f in prof["families"].values() if any(m in tag.lower() for m in f["match"])), None)
    caps = info["capabilities"]
    # AN EMBEDDER NEVER WRITES TEXT. Ollama's /api/show reports qwen3-embedding:0.6b as
    # ['tools', 'thinking', 'embedding'] (a template artifact), so a model that embeds and cannot
    # complete gets only the settings that apply to it.
    embed_only = "embedding" in caps and "completion" not in caps
    fields = []
    for key, spec in prof["common"].items():
        if embed_only and key not in ("num_ctx", "keep_alive"):
            continue
        spec = dict(spec)
        if spec.get("max") == "trained_context":
            spec["max"] = info["context"]
        fields.append({"key": key, **spec})
    if "thinking" in caps and not embed_only:
        t = (fam or {}).get("fields", {}).get("think") or prof["unprofiled_think"]
        fields.insert(0, {"key": "think", **t})
    return {"fields": fields, "family": (fam or {}).get("name"), "info": info,
            "source": (fam or {}).get("source")}


def update_role(key, who, what, **changes):
    with locked():
        return _update_role(key, who, what, **changes)


def _update_role(key, who, what, **changes):
    """Set fields on one role (model, tools, attributes, tasks, name, what), save and log."""
    d = load()
    if key not in d["roles"]:
        raise KeyError(f"no role {key!r}")
    d["roles"][key].update(changes)
    return save(d, who, f"{d['roles'][key].get('name', key)}: {what}")


def add_role(key, name, kind, model, who):
    with locked():
        return _add_role(key, name, kind, model, who)


def _add_role(key, name, kind, model, who):
    import re
    d = load()
    key = re.sub(r"[^a-z0-9_]+", "_", key.lower()).strip("_")
    if not key or key in d["roles"]:
        raise ValueError(f"role key {key!r} is empty or already taken")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    r = {"name": name, "kind": kind, "what": "", "tasks": []}
    if kind == "model":
        # The model is chosen on the role's own Model page (Jas, 2026-09-29), so a new model role
        # may start without one; it is shown as "no model yet" and does no work until it has one.
        r.update(model=model or "", tools={}, profile_by_task={}, attributes={})
    d["roles"][key] = r
    save(d, who, f"added role {name} ({kind})")
    return key


def delete_role(key, who):
    with locked():
        return _delete_role(key, who)


def _delete_role(key, who):
    """Refused while this role is the only one doing some task (the task would be orphaned)."""
    d = load()
    orphan = orphaned_by_delete(d, key)
    if orphan:
        raise ValueError("no other role does: " + ", ".join(orphan) + " -- give those tasks to "
                         "another role first")
    name = d["roles"][key].get("name", key)
    del d["roles"][key]
    return save(d, who, f"deleted role {name}")


def move_task(tasks, task, how, before=None):
    """A new task list with `task` moved: top | up | down | before <other>."""
    ts = list(tasks)
    i = next(n for n, t in enumerate(ts) if t["task"] == task)
    item = ts.pop(i)
    if how == "top":
        ts.insert(0, item)
    elif how == "up":
        ts.insert(max(0, i - 1), item)
    elif how == "down":
        ts.insert(min(len(ts), i + 1), item)
    elif how == "before":
        j = next(n for n, t in enumerate(ts) if t["task"] == before)
        ts.insert(j, item)
    else:
        raise ValueError(how)
    return ts


def main(argv):
    if argv[:1] == ["owns"]:
        d = load()
        if len(argv) == 1:
            for lane in LANE_KEYS:
                print(f"  {lane}  " + (", ".join(owns_for(lane, d)) or "(nothing)"))
            return 0
        lane = argv[1]
        if lane not in LANE_KEYS:
            print(f"  unknown lane {lane!r} -- one of {', '.join(LANE_KEYS)}")
            return 2
        d.setdefault("lanes", {})[lane] = dict(lanes(d).get(lane) or {}, owns=list(argv[2:]))
        probs = lane_problems(d)
        if probs:
            for p in probs:
                print(f"  PROBLEM  {p}")
            print("  nothing written")
            return 1
        n = save(d, "role_store.py owns", f"{lane} owns {len(argv) - 2} path(s)")
        print(f"  {lane} owns: {', '.join(argv[2:]) or '(nothing)'}; logged as entry "
              f"{n['n'] if isinstance(n, dict) else n}")
        return 0
    if argv[:1] == ["check"]:
        probs = check()
        for p in probs:
            print(f"  PROBLEM  {p}")
        print(f"  role store: {len(probs)} problem(s)")
        return 1 if probs else 0
    if argv[:1] == ["regenerate"]:
        d = load()
        save(d, "role_store.py regenerate", "regenerate SERVING.json roles from the store")
        print("  SERVING.json roles regenerated from the store")
        return 0
    if argv[:1] == ["log"]:
        for e in entries():
            if len(argv) < 2 or argv[1] in (e.get("after") or {}).get("roles", {}):
                print(f"  {e['n']:>3}  {e['at']}  {e['who']}: {e['what']}")
        return 0
    if argv[:1] == ["revert"] and len(argv) == 2:
        e = revert(int(argv[1]), "role_store.py revert")
        print(f"  reverted; logged as entry {e['n']}")
        return 0
    if argv[:1] == ["move"] and len(argv) in (3, 4):
        n = move_role(argv[1], argv[2], "role_store.py move", argv[3] if len(argv) == 4 else None)
        print(f"  moved; logged as entry {n['n'] if isinstance(n, dict) else n}")
        return 0
    if argv[:1] == ["escalation"] and len(argv) == 3:
        n = set_escalation(argv[1], argv[2], "role_store.py escalation")
        print(f"  escalation set; logged as entry {n['n'] if isinstance(n, dict) else n}")
        return 0
    d = load()
    for i, (k, r) in enumerate(d["roles"].items(), 1):
        tasks = " > ".join(t["task"] + ("*" if t.get("interrupt") else "")
                           for t in r.get("tasks", [])) or "-"
        print(f"  #{i} {r.get('name', k):18} {r.get('kind', 'model'):7} {r.get('model') or '':26} {tasks}")
    print("  (* interrupts lower tasks)")
    print("  escalation: " + ", ".join(f"{c} -> #{p} {rank(d)[p - 1] if p <= len(d['roles']) else '?'}"
                                       for c, p in escalation(d).items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
