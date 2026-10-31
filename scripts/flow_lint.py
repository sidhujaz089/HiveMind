#!/usr/bin/env python3
"""Can this change to the Flow be applied safely? The lint every Flow change request passes first.

    python3 scripts/flow_lint.py <edit.json>          # the edit against the live FLOW.json
    python3 scripts/flow_lint.py <edit.json> --json   # the same, machine-readable

An EDIT is a JSON list of operations (below). The lint applies it to a COPY of FLOW.json and
reports three lists; it never writes anything:

  errors        the edit cannot be applied at all (a task that does not exist, a malformed op)
  warnings      the flow after the edit has a problem the flow before did not: an outcome with no
                link, an arrow to a task that does not exist, a task nothing sends work to, a link
                carrying something its task does not produce, a chart that no longer lays out clean
  consequences  what ELSE the edit changes: a task removed, the roles that list it, the links that
                go with it, a built-in task whose script would no longer match the chart

WHY (Jas, 2026-09-29, plan step 2a-ii): "HARD RULE -- an edit to the flow cannot break things."
A change is applied only when all three lists are empty; anything else is Assign to: Jas with this
output, and nothing is applied on a warning. Only NEW problems count, so the flow's existing
imperfections do not block every edit -- and a fix that removes one is visible as a smaller list.

BUILT-IN TASKS FOLLOW THE CODE, NOT THE CHART. Every task in FLOW.json today describes a script
that runs whether or not the chart says so ("nothing routes by it yet"). Editing how one DESCRIBES
itself (its name, note, queue text, a mover's label) is clean. Editing what it DOES -- its
outcomes, where a link goes, deleting it, switching it off -- is a consequence, because the chart
would then disagree with the script until someone changes the code. A task added from the Flow tab
is a MODEL task (`model_task: true`), run by task_worker.py for the role whose task list has it;
until a role lists it the lint says so as a consequence, rather than letting the chart promise work
nothing does.

THE OPERATIONS
  {"op": "add_task",    "task": {id, name, worker, worker_kind, command, queue, inputs, outputs,
                                 outcomes, ends, note, status, prompt}}
  {"op": "edit_task",   "id": "...", "set": {<field>: <value>, ...}}
  {"op": "delete_task", "id": "..."}
  {"op": "add_link",    "link": {from, outcome, to, mover, carry, kind?}}
  {"op": "edit_link",   "from": "...", "outcome": "...", "to": "...", "set": {to?, mover?, carry?, kind?}}
  {"op": "delete_link", "from": "...", "outcome": "...", "to": "..."}
  {"op": "add_helper",  "helper": {script, serves: [task ids], what, runs?}}
  {"op": "edit_helper", "script": "...", "set": {serves?, what?, runs?}}
  {"op": "delete_helper", "script": "..."}
     HELPERS (Jas, 2026-10-01: the chart is the system of record) are the supporting scripts each
     task relies on. Descriptive only -- adding one changes no routing -- so a clean helper edit
     carries no consequence. See scripts/flow_coverage.py.
  {"op": "insert_task", "from": "...", "outcome": "...", "to": "...", "task": {...},
                        "outcome_out": "<the new task's outcome that continues to the old target>"}
"""
import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FLOW = REPO / "projects" / "local-inference" / "models" / "FLOW.json"
ROLES = REPO / "projects" / "local-inference" / "models" / "ROLES.json"
SKILLS_DIR = REPO / "docs" / "skills"

TASK_FIELDS = ("id", "name", "worker", "worker_kind", "command", "queue", "inputs", "outputs",
               "outcomes", "triggers", "note", "ends", "status", "prompt", "model_task", "skills", "about")
# What a task SAYS about itself, as opposed to what it does. Editing these on a built-in is clean.
# `skills` is here because every worker asks skills.skills_for() at run time (plan 3c): attaching
# one changes what the model is told, never where the item goes, so the script follows the chart.
DESCRIPTIVE = {"name", "note", "queue", "command", "inputs", "outputs", "prompt", "skills", "about"}
LINK_DESCRIPTIVE = {"mover", "carry"}
WORKER_KINDS = ("role", "script", "person", "store")


class EditError(Exception):
    pass


def load(path=FLOW):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _task(d, tid):
    t = next((t for t in d["tasks"] if t["id"] == tid), None)
    if t is None:
        raise EditError(f"no task {tid!r}")
    return t


def _link(d, op):
    key = (op.get("from"), op.get("outcome"), op.get("to"))
    l = next((l for l in d["links"] if (l["from"], l["outcome"], l["to"]) == key), None)
    if l is None:
        raise EditError(f"no link {key[0]} --{key[1]}--> {key[2]}")
    return l


def _new_task(t):
    if not isinstance(t, dict) or not t.get("id") or not t.get("name"):
        raise EditError("a new task needs at least an id and a name")
    extra = set(t) - set(TASK_FIELDS)
    if extra:
        raise EditError(f"task {t['id']!r} has fields the flow does not know: {sorted(extra)}")
    out = {"id": t["id"], "name": t["name"], "worker": t.get("worker", ""),
           "worker_kind": t.get("worker_kind", "role"), "command": t.get("command", ""),
           "queue": t.get("queue", ""), "inputs": list(t.get("inputs", [])),
           "outputs": list(t.get("outputs", [])), "outcomes": list(t.get("outcomes", [])),
           "triggers": dict(t.get("triggers", {})), "note": t.get("note", ""),
           "ends": list(t.get("ends", [])), "status": t.get("status", "active"),
           "prompt": t.get("prompt", ""), "model_task": t.get("model_task", True),
           "skills": list(t.get("skills", [])),
           # `about` was a known field that add_task silently dropped (2026-10-13: Script audit
           # reached the chart with no description and test_hub_layout went red on main).
           "about": t.get("about", "")}
    if not out["about"]:
        del out["about"]
    if not out["skills"]:
        del out["skills"]
    if out["worker_kind"] not in WORKER_KINDS:
        raise EditError(f"worker_kind must be one of {WORKER_KINDS}, not {out['worker_kind']!r}")
    return out


def _new_link(l):
    need = ("from", "outcome", "to")
    if not isinstance(l, dict) or any(not l.get(k) for k in need):
        raise EditError("a link needs from, outcome and to")
    out = {"from": l["from"], "outcome": l["outcome"], "to": l["to"],
           "mover": l.get("mover", ""), "carry": list(l.get("carry", []))}
    if l.get("kind"):
        out["kind"] = l["kind"]
    elif l.get("back"):
        out["back"] = True
    return out


# "inactive" (Jas, 2026-10-06): works only with a model nothing serves now -- it posts to Ollama for
# Q4/Q16/Gemma, and every role is Swift on vLLM. Kept, mapped, and not counted as a loose process.
HELPER_RUNS = ("on-demand", "triggered", "scheduled", "service", "one-time", "library", "inactive")


def _new_helper(h):
    if not isinstance(h, dict) or not str(h.get("script") or "").strip():
        raise EditError("a helper needs its script path")
    serves = h.get("serves") or []
    if not isinstance(serves, list):
        raise EditError("serves is a list of task ids")
    if not str(h.get("what") or "").strip():
        raise EditError(f"{h['script']}: say in one line what it does")
    out = {"script": h["script"].strip(), "serves": serves, "what": str(h["what"]).strip()}
    if h.get("runs"):
        if h["runs"] not in HELPER_RUNS:
            raise EditError(f"runs is one of {HELPER_RUNS}")
        out["runs"] = h["runs"]
    return out


def _helper(d, script):
    for h in d.get("helpers", []):
        if h.get("script") == script:
            return h
    raise EditError(f"no helper {script!r} on the chart")


def apply(d, ops):
    """A new flow with the ops applied, or EditError. `d` is never modified."""
    if not isinstance(ops, list) or not ops:
        raise EditError("an edit is a non-empty JSON list of operations")
    d = copy.deepcopy(d)
    for i, op in enumerate(ops):
        kind = op.get("op") if isinstance(op, dict) else None
        try:
            if kind == "add_task":
                t = _new_task(op.get("task"))
                if any(x["id"] == t["id"] for x in d["tasks"]):
                    raise EditError(f"task {t['id']!r} already exists")
                d["tasks"].append(t)
            elif kind == "edit_task":
                t = _task(d, op.get("id"))
                sets = op.get("set") or {}
                bad = set(sets) - (set(TASK_FIELDS) - {"id"})
                if bad or not sets:
                    raise EditError(f"edit_task can set {sorted(set(TASK_FIELDS) - {'id'})}, got {sorted(sets) or 'nothing'}")
                t.update(copy.deepcopy(sets))
            elif kind == "delete_task":
                tid = _task(d, op.get("id"))["id"]
                d["tasks"] = [t for t in d["tasks"] if t["id"] != tid]
                d["links"] = [l for l in d["links"] if tid not in (l["from"], l["to"])]
                d["sources"] = [s for s in d.get("sources", []) if s["to"] != tid]
            elif kind == "add_link":
                d["links"].append(_new_link(op.get("link")))
            elif kind == "edit_link":
                l = _link(d, op)
                sets = op.get("set") or {}
                bad = set(sets) - {"to", "mover", "carry", "kind"}
                if bad or not sets:
                    raise EditError(f"edit_link can set to, mover, carry, kind; got {sorted(sets) or 'nothing'}")
                l.update(copy.deepcopy(sets))
            elif kind == "delete_link":
                l = _link(d, op)
                d["links"].remove(l)
            elif kind == "add_helper":
                h = _new_helper(op.get("helper"))
                d.setdefault("helpers", [])
                if any(x.get("script") == h["script"] for x in d["helpers"]):
                    raise EditError(f"helper {h['script']!r} is already on the chart")
                d["helpers"].append(h)
            elif kind == "edit_helper":
                h = _helper(d, op.get("script"))
                sets = op.get("set") or {}
                bad = set(sets) - {"serves", "what", "runs"}
                if bad or not sets:
                    raise EditError(f"edit_helper can set serves, what, runs; got {sorted(sets) or 'nothing'}")
                h.update(copy.deepcopy(sets))
            elif kind == "delete_helper":
                h = _helper(d, op.get("script"))
                d["helpers"].remove(h)
            elif kind == "insert_task":
                l = _link(d, op)
                t = _new_task(op.get("task"))
                cont = op.get("outcome_out")
                if not cont:
                    raise EditError("insert_task needs outcome_out: the new task's outcome that goes on to "
                                    f"{l['to']!r}")
                if cont not in t["outcomes"]:
                    t["outcomes"].append(cont)
                d["tasks"].append(t)
                after = dict(l, **{"from": t["id"], "outcome": cont})
                l["to"] = t["id"]
                d["links"].append(after)
            else:
                raise EditError(f"unknown op {kind!r}")
        except EditError as e:
            raise EditError(f"op {i + 1} ({kind}): {e}") from None
    return d


def problems(d):
    """Structural problems of one flow, as stable strings (so before/after can be compared)."""
    out = set()
    ids = [t["id"] for t in d["tasks"]]
    for t in {i for i in ids if ids.count(i) > 1}:
        out.add(f"two tasks share the id {t!r}")
    known = set(ids)
    by = {t["id"]: t for t in d["tasks"]}
    for l in d["links"]:
        for end in ("from", "to"):
            if l[end] not in known:
                out.add(f"a link {l['from']} --{l['outcome']}--> {l['to']} names a task that does not exist ({l[end]})")
        if l["from"] in by and l["outcome"] not in by[l["from"]]["outcomes"]:
            out.add(f"a link leaves {l['from']} on {l['outcome']!r}, which is not one of its outcomes")
    for s in d.get("sources", []):
        if s["to"] not in known:
            out.add(f"source {s['id']} sends work to {s['to']!r}, which does not exist")
    live = _skill_kinds()
    for t in d["tasks"]:
        for n in t.get("skills") or []:
            if n not in live:
                out.add(f"{t['name']}: the skill {n!r} is attached but does not exist in docs/skills/")
            elif live[n] != "task":
                out.add(f"{t['name']}: {n!r} is a {live[n] or 'kind-less'} skill; only a task skill attaches to a task")
    for h in d.get("helpers", []):
        if not (REPO / h["script"]).exists():
            out.add(f"helper {h['script']} does not exist on disk")
        for t in h.get("serves") or []:
            if t not in known and t not in {x.get("id") for x in d.get("services", [])}:
                out.add(f"helper {h['script']} serves {t!r}, which is not a task or service on the chart")
    hs = [h.get("script") for h in d.get("helpers", [])]
    for h in {x for x in hs if hs.count(x) > 1}:
        out.add(f"helper {h} is listed twice")
    linked = {(l["from"], l["outcome"]) for l in d["links"]}
    fed = {l["to"] for l in d["links"]} | {s["to"] for s in d.get("sources", [])}
    for t in d["tasks"]:
        for o in t["outcomes"]:
            if (t["id"], o) not in linked and o not in t.get("ends", []):
                out.add(f"{t['name']}: outcome {o!r} goes nowhere (no link, and not an end)")
        if t["id"] not in fed:
            out.add(f"{t['name']}: nothing sends work to it")
    return out


def _skill_kinds():
    """{skill name: kind} for every live skill (docs/skills/*/SKILL.md)."""
    import re
    out = {}
    for md in Path(SKILLS_DIR).glob("*/SKILL.md"):
        fm = re.match(r"^---\n(.*?)\n---\n", md.read_text(encoding="utf-8", errors="replace"), re.S)
        head = fm.group(1) if fm else ""                # the front matter only, found, not sliced
        n = re.search(r"^name:\s*(\S+)", head, re.M)
        k = re.search(r"^kind:\s*(\S+)", head, re.M)
        out[n.group(1) if n else md.parent.name] = k.group(1) if k else ""
    return out


def _carry_problems(d, links):
    out = set()
    by = {t["id"]: t for t in d["tasks"]}
    for l in links:
        src = by.get(l["from"])
        if not src:
            continue
        for c in l.get("carry", []):
            if c not in src.get("outputs", []) and c != "-":
                out.add(f"the link {l['from']} --{l['outcome']}--> {l['to']} carries {c!r}, which "
                        f"{src['name']} does not list as an output")
    return out


def _input_problems(d, tids):
    out = set()
    for t in d["tasks"]:
        if t["id"] not in tids:
            continue
        given = {c for l in d["links"] if l["to"] == t["id"] for c in l.get("carry", [])}
        for i in t.get("inputs", []):
            if i not in given:
                out.add(f"{t['name']} needs {i!r}, and no link into it carries that")
    return out


def _layout_problems(d):
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import flow_layout
        return set(flow_layout.check(flow_layout.layout(d), d))
    except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
        return {f"the chart could not be laid out: {type(e).__name__}: {e}"}


def _roles_listing(roles, tid):
    return sorted(r.get("name", k) for k, r in roles.items()
                  if any((x.get("task") if isinstance(x, dict) else x) == tid for x in r.get("tasks") or []))


def lint(before, ops, roles=None, layout=True):
    """{"errors", "warnings", "consequences", "after"} for applying ops to `before`."""
    res = {"errors": [], "warnings": [], "consequences": [], "after": None}
    try:
        after = apply(before, ops)
    except EditError as e:
        res["errors"].append(str(e))
        return res
    res["after"] = after
    if roles is None:
        try:
            roles = json.loads(ROLES.read_text(encoding="utf-8")).get("roles", {})
        except (OSError, ValueError) as e:
            res["warnings"].append(f"ROLES.json unreadable, so which roles an edit affects is unknown: {e}")
            roles = {}
    b_ids = {t["id"]: t for t in before["tasks"]}
    a_ids = {t["id"]: t for t in after["tasks"]}
    b_links = {(l["from"], l["outcome"], l["to"]): l for l in before["links"]}
    a_links = {(l["from"], l["outcome"], l["to"]): l for l in after["links"]}
    new_links = [l for k, l in a_links.items() if k not in b_links or l != b_links[k]]
    touched = {t for t in a_ids if t not in b_ids or a_ids[t] != b_ids[t]}

    warn = problems(after) - problems(before)
    warn |= _carry_problems(after, new_links) - _carry_problems(before, before["links"])
    warn |= _input_problems(after, touched) - _input_problems(before, touched)
    if layout:
        warn |= _layout_problems(after) - _layout_problems(before)
    res["warnings"] = sorted(warn)

    cons = []
    for tid, t in b_ids.items():
        if tid not in a_ids:
            who = _roles_listing(roles, tid)
            cons.append(f"deletes the task {t['name']}"
                        + (f"; it is on the task list of {', '.join(who)}" if who else "")
                        + ("; its script (" + t["command"] + ") still runs -- the chart would no longer show it"
                           if not t.get("model_task") else ""))
            continue
        a = a_ids[tid]
        if t.get("model_task"):
            continue
        changed = {k for k in set(t) | set(a) if t.get(k) != a.get(k)} - DESCRIPTIVE
        for k in sorted(changed):
            cons.append(f"changes what the built-in task {t['name']} does ({k}: {t.get(k)!r} -> {a.get(k)!r}); "
                        f"its script ({t['command']}) would not follow the chart until the code changes")
    for tid, t in a_ids.items():
        if tid not in b_ids and t.get("model_task"):
            # task_worker.py runs model tasks now (plan step 4), but only for a role whose task list
            # has the task: the dispatcher routes by the lists. No role lists it -> its items wait.
            who = [r.get("name", k) for k, r in roles.items() if any(x.get("task") == tid for x in r.get("tasks", []))]
            if not who:
                cons.append(f"adds the model task {t['name']}: no role has it on its task list, so work sent "
                            "to it would wait there -- add it to a role on the Models tab")
    for k, l in b_links.items():
        if k[0] not in a_ids or k[2] not in a_ids:
            cons.append(f"removes the link {k[0]} --{k[1]}--> {k[2]} (a task it joins is deleted)")
            continue
        src = b_ids.get(k[0], {})
        if k not in a_links:
            if not src.get("model_task"):
                cons.append(f"removes or re-routes the link {k[0]} --{k[1]}--> {k[2]}; the script that moves "
                            f"it ({l.get('mover', '?')}) still sends work there until the code changes")
            continue
        diff = {x for x in set(l) | set(a_links[k]) if l.get(x) != a_links[k].get(x)} - LINK_DESCRIPTIVE
        if diff and not src.get("model_task"):
            cons.append(f"changes the link {k[0]} --{k[1]}--> {k[2]} ({', '.join(sorted(diff))}); its mover "
                        f"({l.get('mover', '?')}) would not follow the chart until the code changes")
    for k in a_links:
        if k not in b_links and not b_ids.get(k[0], {"model_task": True}).get("model_task"):
            cons.append(f"adds a link out of the built-in task {b_ids[k[0]]['name']} on {k[1]!r}: its script "
                        "does not send work there until the code changes")
    res["consequences"] = cons
    return res


def clean(res):
    return not (res["errors"] or res["warnings"] or res["consequences"])


def report(res):
    lines = []
    for name in ("errors", "warnings", "consequences"):
        for x in res[name]:
            lines.append(f"  {name[:-1].upper():12} {x}")
    lines.append("  clean -- safe to apply" if clean(res) else
                 f"  NOT CLEAN: {len(res['errors'])} error(s), {len(res['warnings'])} warning(s), "
                 f"{len(res['consequences'])} consequence(s)")
    return "\n".join(lines)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    try:
        ops = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"  ERROR        the edit file is unreadable: {e}")
        return 2
    res = lint(load(), ops)
    if "--json" in argv:
        print(json.dumps({k: v for k, v in res.items() if k != "after"}, indent=1))
    else:
        print(report(res))
    return 0 if clean(res) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
