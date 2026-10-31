#!/usr/bin/env python3
"""Which role takes which work, in what order: read from the role store, not hard-coded.

    python3 scripts/task_routing.py          # every dispatcher kind: its task, owner, interrupt, rank

Jas, 2026-09-29 (Models / Roles / Flow plan, step 4): "the dispatcher, whenever a role's server is
free, walks that role's task list top to bottom and takes the first task that has work waiting
and (unless Team Work is on) no other role working it. Interrupt replaces the hard-coded
preempt_pids rule. The manager queue's YIELD flag is raised when a higher task on the Manager's
list has work." Until this file, the task lists in models/ROLES.json were shown on the Models tab
and read by nothing that decides.

WHAT THE STORE DECIDES NOW (each one used to be a literal in dispatch_loop.py):
  * the OWNER of each kind of work -- the role whose list has the task, and so the model it runs on
    (was ROLES["manager"] / ROLES["auditor"] written per kind);
  * the order a role takes its own tasks in -- its list, top first (was LEVELS);
  * what the senior station takes when idle -- its list after the first task (was SENIOR_HELPS);
  * which work may stop a build on the model it needs -- the task's Interrupt box (was: all of it);
  * (Team Work -- may a role join a task another role is working -- removed 2026-10-14: it always may);
  * when the manager queue stands down between scopes -- a task above Scoping on the Manager's
    list has work (was: every non-script-review item).

WHAT IT DOES NOT DECIDE, said so the next reader does not assume it:
  * which models take BUILDS: still SERVING.json's production tags (gemma and Q4). The Frontline
    role is the only one with Building, but Q4 builds too when free, and moving that into the
    store changes behaviour -- a decision, not a migration.
  * the ORDER BETWEEN TWO ROLES SHARING ONE MODEL (Manager, Auditor and, with its station down,
    Senior are all Q4). The store has no cross-role order; Jas's cost-of-delay order (COST_OF_DELAY,
    2026-09-28) breaks that tie, and each role's own list then re-orders its items within the slots
    that tie-break gave it. With today's lists that is exactly the old LEVELS order.

A STORE THAT CANNOT BE READ ROUTES AS TODAY. This is imported by the 10-minute tick; a dispatcher
that stops because a JSON file is unreadable is worse than one routing by the defaults below, and
it prints when it falls back.
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

# The dispatcher's job kinds and the store's task ids. "manager" is the manager queue's own job
# (scoping); "production" is a build; "console" a Console message.
KIND_TASK = {"preflight": "preflight", "supervise": "supervision", "script-review": "script_review",
             "session-review": "session_review", "rca": "rca", "production": "building",
             "console": "console", "scoping": "scoping", "script-audit": "script_audit", "done-check": "done_check"}
TASK_KIND = {t: k for k, t in KIND_TASK.items()}

# WHAT A MODEL ROLE CAN ACTUALLY DO (Jas, 2026-10-07: "I want the task list for models to only show what
# they can do"). Each is run on the role's model by the dispatcher (KIND_TASK, assigned_items) or by
# manager_queue.sh (scoping); corpus indexing is the embedder's. Everything else on the Flow chart is
# a script, a person's, or a step inside one of these (triage is inside session review, scope check is
# scope_lint, Backlog is a second switch for the scoping queue -- manager_queue.sh still honours it).
# WHAT THE HARNESS CAN RUN ON A MODEL: the dispatcher's job kinds, Assigned items, and corpus indexing.
RUNNABLE_TASKS = frozenset(KIND_TASK.values()) | {"assigned_items", "corpus_index"}
FLOW = Path(__file__).resolve().parent.parent / "projects" / "local-inference" / "models" / "FLOW.json"


def flow_model_tasks(flow=FLOW):
    """THE FLOW TAB IS THE SOURCE (Jas, 2026-10-19: "The task list needs to be dynamic so the source of
    truth is the flow tab. Tasks there need to state the actor. Model actor tasks should appear in the
    role task list"). The Flow tab's tasks whose actor is a model role (worker_kind "role"), that the
    harness can actually run (RUNNABLE_TASKS) -- so a script's task, a person's (done_check is Claude's
    on the Flow tab), or a model step nothing dispatches (triage) is never offered to a role.
    test_flow_model_tasks.py names any Flow model task the harness cannot run, so the two stay in step.
    Falls back to RUNNABLE_TASKS, said, if FLOW.json cannot be read."""
    try:
        tasks = json.loads(Path(flow).read_text(encoding="utf-8"))["tasks"]
    except (OSError, ValueError, KeyError) as e:
        print(f"  NOTE: Flow tab unreadable ({type(e).__name__}: {e}); role tasks from the harness list",
              file=sys.stderr)
        return RUNNABLE_TASKS
    return frozenset(t["id"] for t in tasks if t.get("worker_kind") == "role") & RUNNABLE_TASKS


MODEL_TASKS = flow_model_tasks()

# WHICH TYPE OF MODEL EACH TASK TAKES (Jas, 2026-10-07: "embedder models get indexing, non embedder
# types don't ... if we have other types of models we should specify"). A model's type is in
# models/REGISTRY.json ("llm" / "embedder"). A task not listed here is an llm task. A new type of
# model gets its tasks named here.
TASK_MODEL_TYPES = {"corpus_index": frozenset({"embedder"})}


def task_types(task):
    return TASK_MODEL_TYPES.get(task, frozenset({"llm"}))


def model_type(tag):
    """'llm' / 'embedder' / ... from models/REGISTRY.json, or None for a model it does not list."""
    import json
    try:
        reg = json.loads((REPO / "projects" / "local-inference" / "models" / "REGISTRY.json").read_text())
    except (OSError, ValueError) as e:
        print(f"  NOTE: REGISTRY.json unreadable ({e}); model type unknown", file=sys.stderr)
        return None
    for m in reg.get("base_models", []) + reg.get("not_served", []):
        if m.get("tag") == tag:
            return m.get("type")
    return None


# WHAT EACH MODEL CAN DO (t1593. Jas, 2026-10-25: "The Role tab should only allow tasks a model
# can do. For example the embedder model cant do things other roles can and vice versa."). The
# offered list is built in TASKS_FOR_MODEL below, and every caller reaches it there -- the Models
# tab's "+ Add Task" through projects/app-repo/roles_pages.py -- so this is the one filter.
#
# WHY THE DECLARATION IS HERE AND NOT IN models/REGISTRY.json: the registry is Jas's Models surface,
# edited from the Models tab, and adding a required field to it is his call, not a worker's. It is
# the eventual home for a capability field; until then a model's capability is declared here, where
# a test can seed one. Measured 2026-10-25: swift-junior:latest is a generative model whose registry
# row has no type, so it was offered corpus_index (the embedder's task) -- a registry-only rule
# cannot tell a chat model from an embedder when the row is silent.
EMBEDDING_TASKS = frozenset({"corpus_index"})
GENERATIVE_TASKS = frozenset(MODEL_TASKS - EMBEDDING_TASKS)
MODEL_CAPABILITY = {
    # generative / chat models: everything a model role does except the embedder's indexing
    "swift": GENERATIVE_TASKS,
    "gemma4:26b": GENERATIVE_TASKS,
    "gemma4:e4b": GENERATIVE_TASKS,
    "qwen3.8:27b-mtp-bf16": GENERATIVE_TASKS,
    "qwen3.8:27b-mtp-q4_K_M": GENERATIVE_TASKS,
    "qwen3.8:27b-mtp-q4_K_M-56k": GENERATIVE_TASKS,
    "swift-junior:latest": GENERATIVE_TASKS,
    # embedding-only models: indexing, and nothing else
    "qwen3-embedding:0.6b": EMBEDDING_TASKS,
    "embeddinggemma-2": EMBEDDING_TASKS,
}


def tasks_for_model(tag):
    """The tasks a role running `tag` can be offered: its declared capability, else the type
    REGISTRY.json gives it, else today's full list said out loud -- offering nothing to a model
    nothing recognises is worse than offering too much. Never more than MODEL_TASKS, because a
    task the Flow tab does not give a model is not on the table whatever the model can do."""
    if not tag:
        return MODEL_TASKS
    if tag in MODEL_CAPABILITY:
        return frozenset(MODEL_CAPABILITY[tag]) & MODEL_TASKS
    t = model_type(tag)
    if t is None:
        print(f"  NOTE: {tag} has no declared capability and no type in REGISTRY.json; it is "
              f"offered every model task", file=sys.stderr)
        return MODEL_TASKS
    return frozenset(x for x in MODEL_TASKS if t in task_types(x))

# ORDER BY COST OF DELAY (Jas, 2026-09-28): preflight holds a brief back from every builder;
# supervise holds a failed brief's retry; a session review stalls nothing but its fixes compound
# and its context goes stale; a script review is advisory and waited on by nothing. Used ONLY to
# order work of DIFFERENT roles competing for one model; a role's own list orders its own work.
COST_OF_DELAY = {"preflight": 1, "supervise": 2, "session-review": 3, "rca": 3, "script-review": 4,
                 "script-audit": 4}
UNRANKED = 5                         # a kind added on the Flow tab: after every built-in one

# Today's store, for when the real one cannot be read (and for tests).
DEFAULT = {"roles": {
    "manager": {"kind": "model", "tasks": [{"task": "triage", "interrupt": False},
                                           {"task": "scoping", "interrupt": False},
                                           {"task": "console", "interrupt": False}]},
    "auditor": {"kind": "model", "tasks": [{"task": "script_review", "interrupt": True}]},
    "coder": {"kind": "model", "tasks": [{"task": "building", "interrupt": False}]},
    "embedder": {"kind": "model", "tasks": [{"task": "corpus_index", "interrupt": False}]},
    "senior": {"kind": "model", "endpoint": "http://127.0.0.1:11435",
               "tasks": [{"task": "preflight", "interrupt": True},
                         {"task": "session_review", "interrupt": False},
                         {"task": "rca", "interrupt": False}]},
}}


# WHAT THE MODELS TAB CLAIMS, AND WHERE THE CODE THAT DOES IT LIVES (2026-10-25).
#
# Jas: "Please update that header so it gets its information from a source of truth and updates
# dynamically." The banner above the task list used to be a sentence somebody typed once -- and it went
# stale in the wrong direction, telling him three settings were "not acting yet" when all three reach the
# dispatcher, which cost him a trip to the tab to look for a fault that was in the sentence.
#
# So the claim is data: each entry names the FILE and the CALL that consumes the setting, and `wired()`
# checks that the call is still there. A refactor that stops reading a setting turns its own banner line
# to "Not wired" instead of leaving a comfortable lie on the page.
WIRING = {
    "interrupt": ("dispatch_loop.py", "task_routing.interrupts(",
                  "stops a job at once for a higher-priority task, and only on a lower-priority one"),
    "series": ("dispatch_loop.py", "apply_series(",
               "moves the model to its next task after that many runs of one task in a row"),
    "max_lanes": ("task_routing.py", "def max_lanes(",
                  "caps how many of the role's lanes may work one task type"),
}


def wired(repo=None):
    """[{setting, live, where, what}] -- every banner claim, checked against the code that consumes it."""
    import pathlib
    base = pathlib.Path(repo) if repo else pathlib.Path(__file__).resolve().parent
    out = []
    for name in sorted(WIRING):
        fname, needle, what = WIRING[name]
        try:
            live = needle in (base / fname).read_text(encoding="utf-8", errors="replace")
        except OSError:
            live = False
        out.append({"setting": name, "live": live, "where": f"{fname}: {needle}", "what": what})
    return out


def load():
    try:
        import role_store
        return role_store.load()
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: role store unreadable ({type(e).__name__}: {e}); routing by the built-in "
              f"defaults, which are today's", file=sys.stderr)
        return DEFAULT


def tasks_of(d, role):
    return list((d["roles"].get(role) or {}).get("tasks") or [])


def model_roles(d):
    return [k for k, r in d["roles"].items() if r.get("kind", "model") == "model"]


def role_lanes(d, role):
    """LANES BELONG TO THE ROLE (Jas, 2026-10-08: "I want the ability to add lanes for a role, not
    model ... a lanes setting in the model configuration screen"). How many jobs this role may run at
    once across all its tasks. NOT SET = None = no role limit (the engine's ceiling governs, as before
    the setting existed). A role with no tasks holds no lanes."""
    r = d["roles"].get(role) or {}
    if not tasks_of(d, role):
        return 0
    if r.get("lanes") in (None, ""):
        return None
    try:
        return max(0, int(r["lanes"]))
    except (TypeError, ValueError):
        return None


def model_lanes(d, model):
    """The lanes a model serves = the sum of its roles' lanes, or None when no role uses it.
    The engine's own ceiling (SERVERS.json slots, vLLM's max_num_seqs) caps this in modellease."""
    roles = [k for k in model_roles(d) if (d["roles"][k] or {}).get("model") == model]
    lanes = [role_lanes(d, k) for k in roles]
    # Any working role without a setting leaves the model at its engine ceiling.
    if not roles or any(n is None for n in lanes):
        return None
    return sum(lanes)


def entry(d, role, kind):
    task = KIND_TASK.get(kind, kind)
    return next((t for t in tasks_of(d, role) if t.get("task") == task), None)


def owners(d, kind):
    """Every model role with this kind of work on its list, in store order."""
    return [k for k in model_roles(d) if entry(d, k, kind)]


def owner(d, kind, exclude=()):
    """The role that takes this kind of work on the main server: the first owner, in store order,
    that is not excluded (a role on its own live station is excluded -- it walks its own list)."""
    return next((k for k in owners(d, kind) if k not in exclude), None)


# ONE SOURCE OF TRUTH FOR WHO DOES WHAT (t9554, Jas 2026-10-29: "We need to stop referencing roles in our
# processes"). A process names a JOB and asks these four; none may name a role. A MISS IS LOUD, never a
# pass-through (DS's review): returning the input on a miss is how a mistyped job silently became a role name
# in holders_of. Callers show UNKNOWN on the Hub and the S5 check fails on it.
UNKNOWN = "UNKNOWN"


def who_does(job, d=None):
    """The role that takes `job` (a Responsibility on the Models tab), or UNKNOWN."""
    d = d or load()
    return owner(d, TASK_KIND.get(job, job)) or owner(d, job) or UNKNOWN


def model_for(job, d=None):
    """The model that does `job`: its role's model on the Models tab, or UNKNOWN."""
    d = d or load()
    role = who_does(job, d)
    model = ((d.get("roles") or {}).get(role) or {}).get("model") if role != UNKNOWN else None
    return model or UNKNOWN


def holders(cls, d=None):
    """Roles holding escalation class `cls` (vision/technical/rework), in chart order; [] when none does."""
    sys.path.insert(0, str(REPO / "scripts"))
    import role_store
    try:
        return role_store.escalate_all(cls, None, d)
    except role_store.NoEscalation:
        return []


def tools_for(role, d=None):
    """The tools a role may use, from its own `tools` entry on the store; UNKNOWN for a role that is not there."""
    d = d or load()
    r = (d.get("roles") or {}).get(role)
    if r is None:
        return UNKNOWN
    return {k: bool(v) for k, v in (r.get("tools") or {}).items()}


def interrupts(d, kind, role=None):
    """May work of this kind stop a build on the model it needs? The owner's Interrupt box."""
    role = role or owner(d, kind)
    e = entry(d, role, kind) if role else None
    return bool(e and e.get("interrupt"))


def position(d, role, kind):
    task = KIND_TASK.get(kind, kind)
    for i, t in enumerate(tasks_of(d, role)):
        if t.get("task") == task:
            return i
    return len(tasks_of(d, role))


def levels(d=None):
    """DISPLAY-ONLY. {kind: rank} for the Hub's Production columns and session review -- NOTHING ROUTES ON IT.

    t9540 R4 (Jas 2026-10-28): "A role serves its responsibilities top-down: the first item in its list is
    its highest priority. Two roles may order the same task differently, and each follows its own order.
    Replace any global or per-kind priority in schedule()/plan() with each role's order." This table was
    that global priority. It no longer decides anything: round_robin ranks by `position(d, role, kind)` --
    the role's OWN list -- and two roles with opposite lists get opposite picks, which is the property R4
    asks for. Kept because the Hub prints it and because a cost-of-delay order is a useful thing to LOOK at;
    a docstring that says "kept for its readers" invited the reader to assume a reader that routes.
    """
    d = d or load()
    out = dict(COST_OF_DELAY)
    for k in model_roles(d):
        for t in tasks_of(d, k):
            kind = TASK_KIND.get(t["task"], t["task"])
            out.setdefault(kind, UNRANKED)
    return out


def order(d, items):
    """Sort priority items: cost of delay across roles, then each role's own list within the slots
    its items got. A role whose list disagrees with the cost-of-delay order is obeyed for its own
    work -- that is the point of the list -- without jumping ahead of another role's work."""
    lv = levels(d)
    slots = sorted(items, key=lambda i: (lv.get(i["kind"], UNRANKED), i.get("created", "")))
    by_role = {}
    for n, i in enumerate(slots):
        by_role.setdefault(i.get("role"), []).append(n)
    out = list(slots)
    for role, idx in by_role.items():
        mine = sorted((slots[n] for n in idx),
                      key=lambda i: (position(d, role, i["kind"]) if role else 0,
                                     lv.get(i["kind"], UNRANKED), i.get("created", "")))
        for n, i in zip(idx, mine):
            out[n] = i
    return out


def apply_series(d, items, streak):
    """Honour each task's SERIES limit (Jas, 2026-10-01). `items` is the ordered priority list;
    `streak` is {role: (kind, how many of that kind it started in a row, most recent last)}.

    When the first item's kind has series N > 0 on its role's list and that role has already
    started N of it in a row, the first item of ANOTHER kind for the same role moves to the front
    -- once. Then the streak resets (a different kind was started) and the list order resumes.
    series 0 or absent = no limit, which is every task until Jas sets one."""
    if not items:
        return items
    head = items[0]
    role, kind = head.get("role"), head.get("kind")
    e = entry(d, role, kind) if role else None
    n = int((e or {}).get("series") or 0)
    last_kind, count = (streak or {}).get(role, (None, 0))
    if n <= 0 or last_kind != kind or count < n:
        return items
    for i, it in enumerate(items[1:], 1):
        if it.get("role") == role and it.get("kind") != kind:
            return [it] + items[:i] + items[i + 1:]
    return items


def next_task(role_tasks, waiting, others_on=()):
    """The task a free role takes: the first on its list that has work waiting.

    role_tasks: the role's list [{task, ...}] in order.
    waiting:    {task: number of items waiting}.
    others_on:  tasks another role is working right now -- NOT A REASON TO SKIP. Team Work is gone
                (Jas, 2026-10-14: "The team work thing isn't working. I want to delete that ... the
                models will work on tasks even if others are, just because it's in their list."). The
                argument is kept so callers need not change."""
    for t in role_tasks:
        if waiting.get(t.get("task")):
            return t.get("task")
    return None


def may_interrupt(role_tasks, arriving, running, checkpointable):
    """Does work for `arriving` pause this role's running job on `running`? Only when `arriving`'s
    Interrupt box is on, it is higher on the list, and the running job can checkpoint (a build).
    A model-call review always finishes first, and with Interrupt off the rule is finish first,
    then switch."""
    names = [t.get("task") for t in role_tasks]
    if arriving not in names or running not in names or not checkpointable:
        return False
    e = role_tasks[names.index(arriving)]
    return bool(e.get("interrupt")) and names.index(arriving) < names.index(running)


def yield_kinds(d, role="manager", below="scoping"):
    """The kinds that make the manager queue stand down between scopes: the tasks above Scoping on
    the Manager's list."""
    names = [t.get("task") for t in tasks_of(d, role)]
    if below not in names:
        return set()
    return {TASK_KIND.get(t, t) for t in names[:names.index(below)]}


def scoping_station(d=None):
    """Which role's server runs the manager queue's scoping: "senior" when the Senior's list has
    Scoping ABOVE Preflight (Jas, 2026-10-01, moving backlog/scoping/scope_check to the top of the
    Senior's list), else "manager". Until this, that move changed the list and nothing else -- the
    queue was pinned to the main server, where the Manager's own list ranks Scoping below RCA and
    session review, so it yielded for the whole day."""
    d = d or load()
    names = [t.get("task") for t in tasks_of(d, "senior")]
    if "scoping" not in names:
        return "manager"
    pf = names.index("preflight") if "preflight" in names else len(names)
    return "senior" if names.index("scoping") < pf else "manager"


def main():
    d = load()
    lv = levels(d)
    print(f"  {'kind':15} {'task':15} {'owner':10} {'interrupt':9} rank")
    for kind in sorted(lv, key=lambda k: (lv[k], k)):
        o = owner(d, kind) or "(none)"
        print(f"  {kind:15} {KIND_TASK.get(kind, kind):15} {o:10} {str(interrupts(d, kind)):9} {lv[kind]}")
    print(f"  manager queue yields for: {sorted(yield_kinds(d)) or 'nothing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


BUILD = "production"     # the kind a build lane is; its task on a role's list is "building"


def max_lanes(d, kind, role=None):
    """MAX LANES (Jas, 2026-10-08): the most lanes this task may hold at once; 0 = no limit.
    Building is fixed at 1 (one build per model) and is not editable on the Models tab."""
    if kind == BUILD:
        # EDITABLE SINCE 2026-10-08 (Jas: "I only said 1 build lane because earlier you said it has to
        # be 1. Yes let's make it editable."). One build per model was an Ollama-era rule (a build
        # beside a build ran at 0.92x); vLLM batches. Not set = 1, the old rule; 0 = no limit.
        e = entry(d, role or owner(d, kind), kind) if (role or owner(d, kind)) else None
        v = (e or {}).get("max_lanes")
        try:
            return 1 if v in (None, "") else max(0, int(v))
        except (TypeError, ValueError):
            return 1
    e = entry(d, role or owner(d, kind), kind) if (role or owner(d, kind)) else None
    try:
        return max(0, int((e or {}).get("max_lanes") or 0))
    except (TypeError, ValueError):
        return 0


def round_robin(d, items, running_kinds, free, build_waiting=False, running_roles=None):
    """(items to start, build_won) -- fill `free` lanes ONE PER TASK FIRST, then round again.

    Jas, 2026-10-07: "would it try to give everyone 1 task before doubling up?" -- yes, and with one
    role holding every task, top down by that role's list. Each lane goes to the task with the FEWEST
    jobs running or just picked (running_kinds counts what already holds a lane, so a task with 5
    running does not get a 6th while another has none); ties go to the higher task on its role's
    list, then to the order the items arrive in (already sorted by order() and Series). A task with nothing waiting is
    skipped, so its lanes go to the rest -- with only RCA waiting, RCA gets every lane.

    BUILDS TAKE A TURN TOO. A build is not a priority item -- plan() starts it after schedule() -- so
    `build_waiting` stands for one ready chunk. If Building wins a lane it is left free (build_won)
    for plan() to fill; at most one, as one build per model still holds."""
    # NO GLOBAL ORDER IS READ HERE (t9540 R4, Claude's decision 3). `levels()` used to be called on this
    # line and its result never used: the cost-of-delay table decided nothing, but a reader saw it computed
    # and concluded it mattered. Routing is each ROLE's own list order (position(), below); COST_OF_DELAY is
    # DISPLAY-ONLY and its docstring now says so.

    # THE ROLE'S LIST DECIDES (one role holding every task is the setup this is for); the
    # cost-of-delay rank only breaks a tie between two roles' tasks at the same list position.
    # Ties keep the order the items arrive in: the caller has already sorted them (order(), Series).
    def rank(kind, role, n):
        return (position(d, role, kind) if role else UNRANKED, n)
    queues, ranks = {}, {}
    for n, i in enumerate(items):
        k = i.get("kind")
        queues.setdefault(k, []).append(i)
        ranks.setdefault(k, rank(k, i.get("role"), n))
    if build_waiting:
        ranks[BUILD] = rank(BUILD, owner(d, BUILD), len(items))
        queues[BUILD] = [None] * int(build_waiting)     # one per ready build (True counts as 1)
    held = dict(running_kinds or {})
    roles = {i.get("kind"): i.get("role") for i in items}
    if build_waiting:
        roles[BUILD] = owner(d, BUILD)
    caps = {k: max_lanes(d, k, roles.get(k)) for k in queues}
    # A ROLE AT ITS LANES TAKES NOTHING MORE (role_lanes, 2026-10-08); its share goes to other roles.
    by_role = dict(running_roles or {})
    role_cap = {r: role_lanes(d, r) for r in set(roles.values()) if r}
    role_cap = {r: n for r, n in role_cap.items() if n is not None}
    out, build_won = [], 0
    for _ in range(max(0, free)):
        # A task at its Max lanes is skipped; its share goes to the rest.
        open_ = [k for k, q in queues.items() if q and not (caps[k] and held.get(k, 0) >= caps[k])
                 and not (roles.get(k) in role_cap and by_role.get(roles[k], 0) >= role_cap[roles[k]])]
        if not open_:
            break
        k = min(open_, key=lambda x: (held.get(x, 0), ranks[x]))
        it = queues[k].pop(0)
        held[k] = held.get(k, 0) + 1
        if roles.get(k):
            by_role[roles[k]] = by_role.get(roles[k], 0) + 1
        if k == BUILD:
            build_won += 1                  # lanes left free for plan() to fill with builds
        else:
            out.append(it)
    return out, build_won


# ONE SOURCE OF TRUTH, POINTERS EVERYWHERE (Jas, 2026-10-13: "I want 1 source of truth for anything and
# pointers everywhere as a design principle"). Text that names who does a task -- a status label, a
# note, a prompt -- never writes a role or model name; it asks the store, here, or carries the token
# {role:<task>} through render(). Q16 sat in labels for three weeks after the model that held those
# roles was replaced (2026-09-23), because the name was written in, not pointed at.
ROLE_TOKEN = re.compile(r"\{role:([a-z_]+)\}")


def role_name(task, d=None):
    """The display name of the role whose list holds `task` (a task or a dispatcher kind), from the
    store. No role holds it -> 'no role has <task>' -- never a default name (no fallback, Jas 10-13)."""
    d = d if d is not None else load()
    task = KIND_TASK.get(task, task)
    who = owner(d, task)
    if not who:
        return f"no role has {task}"
    return (d.get("roles", {}).get(who) or {}).get("name") or who


def render(text, d=None):
    """Resolve every {role:<task>} token in `text` against the store, once per call."""
    if "{role:" not in str(text):
        return text
    d = d if d is not None else load()
    return ROLE_TOKEN.sub(lambda m: role_name(m.group(1), d), str(text))

