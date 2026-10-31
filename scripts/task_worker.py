#!/usr/bin/env python3
"""The worker for MODEL TASKS -- the tasks added on the Flow tab -- and their queues.

    python3 scripts/task_worker.py                          # every model task, its owner and queue
    python3 scripts/task_worker.py send <task> "text" [--from who]   # put an item in a task's queue
    python3 scripts/task_worker.py run <task> <item.json> --model M  # work one item (the dispatcher does this)

Jas, 2026-09-29 (Models / Roles / Flow plan, step 4): "model tasks get a worker (task_worker.py)".
A task added on the Flow tab has no script of its own: it is a PROMPT, a list of OUTCOMES, and links
that say where each outcome sends the item next. Until this file nothing ran them, and flow_lint
said so on every one.

HOW AN ITEM MOVES
  * It waits as a file in workspace/task-queues/<task>/. `send` puts it there -- from the Hub, a
    person, another model task, or any mover script that learns to (built-in movers still send work
    only where their code does; flow_lint says so when a link would need one to change).
  * The dispatcher's tick lists the waiting items as priority work for the ROLE WHOSE TASK LIST HAS
    THE TASK (task_routing.owner), on that role's model, in that role's list order -- exactly like a
    built-in task. No role lists it: the item waits, and the tick says "no role has this task".
  * `run` asks that model with the task's prompt, the item, and the outcomes it may choose, and
    requires the answer to END with `OUTCOME: <one of them>`.
  * The link from that outcome decides where the item goes: another model task's queue; the Backlog
    (filed as a task); Assigned to Jas / Assigned to Claude (filed, assigned). An outcome in the
    task's `ends` finishes it. A built-in task with its own script queue cannot take an item from
    here yet: the item is filed Assigned to Claude with that said, not dropped.
  * Every answer is kept next to the item in done/ -- the output is an input to whoever reads why.

A BAD ANSWER IS NOT AN OUTCOME. No OUTCOME line, or one the task does not declare, or a cut-off
answer: the item stays queued with the attempt counted, and after MAX_ATTEMPTS it is filed Assigned
to Claude with the answers attached, because the prompt is what needs fixing, not the next roll.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
FLOW = REPO / "projects" / "local-inference" / "models" / "FLOW.json"
QUEUES = REPO / "workspace" / "task-queues"
MAX_ATTEMPTS = 2
OUTCOME_LINE = re.compile(r"^\s*OUTCOME:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
def person_queue(task):
    """The person/agent role whose own queue a Flow task is ("assigned_<role key>"), or None. Read from
    the role store, never a fixed list (Jas, 2026-10-19: one source of truth for roles)."""
    import role_store
    d = role_store.load()
    k = task[len("assigned_"):] if str(task).startswith("assigned_") else None
    return k if k and d["roles"].get(k, {}).get("kind") in ("person", "agent") else None
CARRY_CHARS = 20_000     # per carried artifact; a longer one is cut AND says where the rest is (dossier.content)


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def flow(path=None):
    return json.loads(Path(path or FLOW).read_text(encoding="utf-8"))


def model_tasks(f=None):
    f = f or flow()
    return {t["id"]: t for t in f["tasks"] if t.get("model_task")}


def queue_dir(task, root=None):
    return Path(root or QUEUES) / task


def send(task, text, who="", came_from=None, root=None, f=None, ref=None, carry=None):
    """Put an item in a model task's queue. -> the item file. `ref` is the work item (a task id) it is
    about, whose dossier travels with it; `carry` the dossier artifacts the incoming arrow carries."""
    if task not in model_tasks(f):
        raise KeyError(f"{task} is not a model task on the Flow (only those have a queue here)")
    d = queue_dir(task, root)
    d.mkdir(parents=True, exist_ok=True)
    item = {"id": f"{int(time.time() * 1000)}-{os.getpid()}", "at": now(), "task": task,
            "text": text, "from": came_from or who, "attempts": 0,
            **({"ref": ref} if ref else {}), **({"carry": list(carry)} if carry else {})}
    p = d / f"{item['id']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(item, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def pending(root=None, f=None):
    """[(task, item_path)] for every item waiting, oldest first per task."""
    out = []
    for tid in model_tasks(f):
        d = queue_dir(tid, root)
        if d.is_dir():
            out += [(tid, p) for p in sorted(d.glob("*.json"))]
    return out


def prompt_for(t, item):
    outcomes = list(t.get("outcomes") or [])
    lines = [f"You are working the task \"{t.get('name', t['id'])}\" in the HiveMind workflow.", ""]
    if t.get("note"):
        lines += [f"What this task is for: {t['note']}", ""]
    if t.get("prompt"):
        lines += ["Instructions for this task:", t["prompt"], ""]
    if t.get("inputs"):
        lines += ["You are given: " + ", ".join(t["inputs"]) + "."]
    if t.get("outputs"):
        lines += ["You produce: " + ", ".join(t["outputs"]) + "."]
    # THE DOSSIER (plan 2a): an item about a task carries that task's dossier -- its index always, and
    # the CONTENT of whatever the incoming arrow's Carry names (dossier.py artifact names).
    if item.get("ref"):
        try:
            import dossier
            lines += ["", dossier.index_text(item["ref"]).rstrip()]
            if item.get("carry"):
                lines += ["", "CARRIED FROM THE DOSSIER (" + ", ".join(item["carry"]) + "):",
                          dossier.content(dossier.gather(item["ref"]), set(item["carry"]), limit=CARRY_CHARS)]
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            lines += ["", f"(The dossier of {item['ref']} could not be read: {type(e).__name__}: {e})"]
    lines += ["", "=== THE ITEM ===", item.get("text", ""), "=== END OF ITEM ===", "",
              "Write your answer. Then, as the LAST line, choose exactly one outcome:",
              "  OUTCOME: <" + " | ".join(outcomes) + ">",
              "An answer without that line, or with an outcome not in this list, is not accepted."]
    return "\n".join(lines) + "\n"


def outcome_of(text, t):
    """The declared outcome the answer ends with, or None. The LAST OUTCOME line wins (a model
    that quotes the format earlier and then answers is still read correctly)."""
    found = OUTCOME_LINE.findall(text or "")
    if not found:
        return None
    got = found[-1].strip().strip("<>`*\"'").strip()
    for o in t.get("outcomes") or []:
        if got.lower() == str(o).lower():
            return o
    return None


def route(t, outcome, f):
    """Where the item goes: ('end', None) | ('task', id) | ('backlog', None) | ('person', who) |
    ('stuck', why)."""
    if outcome in (t.get("ends") or []):
        return "end", None
    link = next((l for l in f.get("links", []) if l.get("from") == t["id"] and l.get("outcome") == outcome), None)
    if not link:
        return "stuck", f"no link leaves {t['id']} for the outcome {outcome!r}"
    to = link.get("to")
    if to in model_tasks(f):
        return "task", to
    if to == "backlog":
        return "backlog", None
    if person_queue(to):
        return "person", person_queue(to)
    return "stuck", (f"{to} is a built-in task with its own script queue; nothing hands it an item from "
                     f"a model task yet")


def _file(title, body, assigned=""):
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    return taskstore.add(title, area="flow", body=body, source="task_worker", needs=assigned)


def deliver(t, item, outcome, answer, f, root=None, filer=_file):
    """Move a finished item on. -> a line saying where it went."""
    where, arg = route(t, outcome, f)
    title = f"{t.get('name', t['id'])}: {item.get('text', '').strip().splitlines()[0][:70] if item.get('text') else item['id']}"
    body = (f"From the Flow task {t['id']} ({t.get('name', '')}), outcome **{outcome}**.\n\n"
            f"The item:\n\n{item.get('text', '')}\n\nThe model's answer:\n\n{answer}\n")
    if where == "end":
        return f"finished ({outcome})"
    if where == "task":
        link = next((l for l in f.get("links", []) if l.get("from") == t["id"] and l.get("outcome") == outcome
                     and l.get("to") == arg), {})
        send(arg, answer, came_from={"task": t["id"], "outcome": outcome, "item": item["id"]}, root=root, f=f,
             ref=item.get("ref"), carry=[c for c in link.get("carry", []) if c != "-"])
        return f"sent to {arg}"
    if where == "backlog":
        return f"filed {filer(title, body)} on the Backlog"
    if where == "person":
        return f"filed {filer(title, body, assigned=arg)} assigned to {arg}"
    import role_store
    up = role_store.escalate("technical")
    return f"filed {filer(title, body + chr(10) + 'Why it is here: ' + arg + chr(10), assigned=up)} " \
           f"assigned to {up}: {arg}"


def _ask(prompt, model, task):
    """Ask through ask_q16.py -- the one path with the lease, the cut-off escalation and the ledger.
    No repository tools: the item and the task's prompt are the input (a task that needs tools is
    a later decision, per role, on the Models tab). -> (rc, text)."""
    # PRESSING PAUSE HOLDS THIS (Jas, 2026-10-30: "when I press pause it stops every model from working on
    # tasks"). This is where a model actually starts, so the pause is asked HERE rather than only in the loop that
    # called in: must_hold answers for the production screen, for ALL devices, and for this work's device.
    try:
        import scheduler_commands as _sc
        _hold, _why = _sc.must_hold()
        if _hold:
            print(f"  PAUSED: {_why}")
            return (1, '')
    except Exception as _e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: could not read the pause flags ({type(_e).__name__}: {_e}) -- continuing")
    with tempfile.TemporaryDirectory(prefix="task-worker-") as d:
        pin, pout = Path(d) / "prompt.txt", Path(d) / "answer.md"
        pin.write_text(prompt, encoding="utf-8")
        rc = subprocess.call([sys.executable, str(REPO / "scripts" / "ask_q16.py"), "--prompt", str(pin),
                              "--out", str(pout), "--no-tools", "--model", model,
                              "--purpose", f"flow-task:{task}"], cwd=str(REPO), stdin=subprocess.DEVNULL,
                             timeout=4 * 3600)
        return rc, (pout.read_text(encoding="utf-8") if pout.exists() else "")


def run(task, item_path, model, root=None, f=None, ask=_ask, filer=_file):
    """Work one item. Exit 0 = delivered; 4 = the model was busy (nothing asked, retry); 1 = the
    answer was not usable (attempt counted)."""
    f = f or flow()
    t = model_tasks(f).get(task)
    item_path = Path(item_path)
    if t is None or not item_path.is_file():
        print(f"  nothing to do: {task} is not a model task, or {item_path} is gone")
        return 1
    item = json.loads(item_path.read_text(encoding="utf-8"))
    import skills
    prompt, _sk = skills.with_skills(prompt_for(t, item), task)     # plan 3c-i; --no-tools: cannot open more
    rc, answer = ask(prompt, model, task)
    if rc == 4:
        print("  the model's lease is held -- nothing was asked; the item stays queued")
        return 4
    done = item_path.parent / "done"
    done.mkdir(exist_ok=True)
    outcome = outcome_of(answer, t) if rc == 0 else None
    item["attempts"] = item.get("attempts", 0) + 1
    item.setdefault("answers", []).append({"at": now(), "model": model, "rc": rc, "text": answer})
    if outcome is None:
        why = (f"ask_q16 exit {rc}" if rc else "the answer has no declared OUTCOME line")
        if item["attempts"] >= MAX_ATTEMPTS:
            tid = filer(f"Flow task {t['id']} could not work an item",
                        f"{item['attempts']} attempts on {model}, none usable ({why}). The item and every "
                        f"answer are in {done.relative_to(REPO) if done.is_relative_to(REPO) else done}/"
                        f"{item_path.name}. Fix the task's prompt or outcomes, then `task_worker.py send` "
                        f"it again.\n\nThe item:\n\n{item.get('text', '')}\n",
                        assigned=__import__("role_store").escalate("technical"))
            item.update(result="held", why=why, filed=tid)
            (done / item_path.name).write_text(json.dumps(item, indent=1) + "\n", encoding="utf-8")
            item_path.unlink()
            print(f"  NOT USABLE ({why}); attempt {item['attempts']} of {MAX_ATTEMPTS} -- filed {tid} for Claude")
        else:
            item_path.write_text(json.dumps(item, indent=1) + "\n", encoding="utf-8")
            print(f"  NOT USABLE ({why}); attempt {item['attempts']} of {MAX_ATTEMPTS}, it stays queued")
        return 1
    where = deliver(t, item, outcome, answer, f, root=root, filer=filer)
    item.update(result=outcome, went=where, finished=now())
    (done / item_path.name).write_text(json.dumps(item, indent=1) + "\n", encoding="utf-8")
    item_path.unlink()
    print(f"  {task}: outcome {outcome} -> {where}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("send")
    s.add_argument("task")
    s.add_argument("text")
    s.add_argument("--from", dest="who", default="")
    s.add_argument("--ref", help="the task this item is about; its dossier travels with it")
    r = sub.add_parser("run")
    r.add_argument("task")
    r.add_argument("item")
    r.add_argument("--model", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "send":
        print(send(a.task, a.text, who=a.who, ref=a.ref).relative_to(REPO))
        return 0
    if a.cmd == "run":
        return run(a.task, a.item, a.model)
    import task_routing
    d = task_routing.load()
    tasks = model_tasks()
    if not tasks:
        print("  no model tasks on the Flow yet (add one on the Flow tab)")
    for tid, t in tasks.items():
        n = len(list(queue_dir(tid).glob("*.json"))) if queue_dir(tid).is_dir() else 0
        print(f"  {tid:20} owner {task_routing.owner(d, tid) or '(no role lists it)':18} {n} waiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
