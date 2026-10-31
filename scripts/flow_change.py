#!/usr/bin/env python3
"""Flow change requests: from the Flow tab, through the Manager, applied only when flow_lint is clean.

    python3 scripts/flow_change.py request <kind> <target> "<plain words>" [--who jas]
    python3 scripts/flow_change.py answer <task id> <manager answer file>   # manager_note calls this
    python3 scripts/flow_change.py log                                      # the changelog
    python3 scripts/flow_change.py revert <n> [--who jas]                   # restore entry n's BEFORE
    python3 scripts/flow_change.py helper <script> <task,...> "<what>" [--runs on-demand] [--who x]
    python3 scripts/flow_change.py task <task.json>                    # add a task node
    python3 scripts/flow_change.py apply <ops.json> ["what"]          # a set of ops, atomically
                                    # put a supporting script on the chart (applied if lint-clean)

WHY (Jas, 2026-09-29, plan step 2a-ii): "I am not technical enough to actually add or delete a
task. When I use these commands they use the schema we made and create a task for a local model to
make the edits ... If the change isn't clear the Manager can hold it for me." So nothing on the Flow
tab edits FLOW.json. A request becomes a task at the top of the Backlog; the Manager's queue takes
it with a flow-specific prompt (manager_prompt.py) and answers with a ```flow-edit block -- a list
of flow_lint operations -- and a proposal in plain words.

THE HARD RULE: the edit is applied only when flow_lint reports no error, no new warning and no
consequence. Then FLOW.json is rewritten, the change is logged with its before and after, and the
task is closed with a note that says so and how to undo it. Anything else -- one warning, one
consequence, a question, no block at all -- is Assign to: Jas with the lint output in plain words,
and nothing is applied. His reply goes back to the Manager like any other answered hold.

A REVERT is direct (it restores a snapshot that was already running) and is itself an entry, so a
revert can be undone the same way.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "projects" / "app-repo")]
import flow_lint  # noqa: E402

FLOW = flow_lint.FLOW
LOG = FLOW.parent / "FLOW-CHANGELOG.jsonl"
# The lane ledger: every lane-to-station switch, one JSON line each. Read at CALL time by
# record_lane_change, never baked into a default, so a suite can point it at a temp path.
LANE_LOG = REPO / "workspace" / "flow-lane-changes.jsonl"
AREA = "flow"
TITLE = "Flow change: "
KINDS = {"add_task": "add a task", "edit_task": "change a task", "delete_task": "delete a task",
         "edit_link": "change an arrow", "delete_link": "delete an arrow",
         "insert_task": "insert a task on an arrow", "describe": "described change"}
EDIT_BLOCK = re.compile(r"```[ \t]*(?:[\w-]+[ \t]+)*flow-edit(?:[ \t]+[\w-]+)*[ \t]*\n(.*?)\n```", re.S | re.I)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def request(kind, target, text, who="jas"):
    """File the request as a task at the top of the Backlog. Returns the task id."""
    import taskstore
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    text = (text or "").strip()
    if not text and kind in ("describe", "add_task", "edit_task", "edit_link", "insert_task"):
        raise ValueError("say in plain words what the change should be")
    label = KINDS[kind]
    head = f"{label}{(' -- ' + target) if target else ''}"
    title = TITLE + head + (f": {text[:60]}" if text else "")
    body = (f"A change to the Flow, requested from the Flow tab by {who} at {_now()}.\n\n"
            f"REQUEST: {label}\nTARGET: {target or '(none)'}\nIN {who.upper()}'S WORDS: {text or '(nothing added)'}\n\n"
            "HOW THIS IS HANDLED (plan step 2a-ii): the Manager drafts it as an edit to "
            "projects/local-inference/models/FLOW.json in a ```flow-edit block, and scripts/flow_lint.py "
            "decides. It is applied only when the lint finds no error, no warning and no consequence; "
            "otherwise it comes back to Jas with the lint output, and nothing is applied.\n\n"
            "flow-request: " + json.dumps({"kind": kind, "target": target, "text": text, "who": who}))
    return taskstore.add(title, area=AREA, body=body, source=f"flow tab ({who})", at_top=True,
                         kind="chore")


def is_flow(tid):
    import taskstore
    meta, _ = taskstore.find(tid)
    return bool(meta) and meta.get("area") == AREA and str(meta.get("title", "")).startswith(TITLE)


def extract_edit(text):
    """The last ```flow-edit block as a list of ops, None when there is none, or a string error."""
    blocks = EDIT_BLOCK.findall(text or "")
    if not blocks:
        return None
    try:
        ops = json.loads(blocks[-1])
    except ValueError as e:
        return f"the flow-edit block is not JSON: {e}"
    return ops if isinstance(ops, list) else "the flow-edit block must be a JSON list of operations"


def entries():
    rows = []
    if LOG.is_file():
        for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                print("flow_change: an unreadable changelog line (kept, not parsed)", file=sys.stderr)
    return rows


def _lock():
    """An exclusive lock beside the changelog, so two writers in ONE tree cannot interleave.

    It cannot span two checkouts -- each worktree has its own file -- which is why renumber() exists as well. A lock
    sold as protection it cannot give is worse than no lock at all.
    """
    import fcntl
    import hashlib
    # OUTSIDE THE REPOSITORY (2026-10-30): a lock file beside the changelog is a file inside the working tree, and
    # `git add -A` committed one within the hour. A lock is not content; it lives in the cache, keyed on the path it
    # protects so two checkouts still get different locks (which is the honest limit -- renumber() covers the rest).
    d = Path.home() / ".cache" / "hivemind-locks"
    d.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(LOG.resolve()).encode("utf-8")).hexdigest()[:16]
    fh = open(d / f"flow-changelog-{key}.lock", "w", encoding="utf-8")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    return fh


def _append_number(extra=None):
    """The next free number, allocated and RECORDED under the lock. Returns the number.

    The fault this closes (2026-10-30): `max(n) + 1` was read out of the file, computed, and only then written. Two
    writers in the same tree both read the same max and both took the same number, and nothing noticed -- a
    changelog with two different changes under one number cannot be reverted, because revert() looks a number up.
    The read and the write are now one critical section.
    """
    fh = _lock()
    try:
        rows = entries()
        taken = {r.get("n") for r in rows}
        n = max([r.get("n", 0) for r in rows] + [0]) + 1
        while n in taken:
            n += 1
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"n": n, **(extra or {})}, ensure_ascii=False) + "\n")
        return n
    finally:
        try:
            fh.close()                     # closing releases the flock
        except OSError as e:                                            # noqa: BLE001 -- said, not swallowed
            print(f"flow_change: the changelog lock did not close cleanly ({e})", file=sys.stderr)


def renumber():
    """Resolve duplicate change numbers deterministically. {old: new} for every number that moved.

    Two checkouts can each write 104 for a different change -- a worktree's changelog and the live one have separate
    files, so no lock covers them. The earliest claim by TIMESTAMP keeps the number; later claims take the next free
    number after the current maximum, in timestamp order, so the result does not depend on which file was read first.
    Rows are rewritten in timestamp order, which is also the order a reader expects.
    """
    fh = _lock()
    try:
        rows = entries()
        if not rows:
            return {}
        rows.sort(key=lambda r: (str(r.get("at") or ""), r.get("n") or 0))
        seen, moved, nxt = {}, {}, max([r.get("n", 0) for r in rows] + [0])
        for row in rows:
            n = row.get("n")
            if n is None or n in seen:
                nxt += 1
                moved[row.get("n")] = nxt
                row["n"] = nxt
            seen[row["n"]] = True
        tmp = LOG.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        tmp.replace(LOG)
        return {k: v for k, v in moved.items() if k is not None}
    finally:
        fh.close()


def _write(after, before, who, what, task="", ops=None):
    """Rewrite FLOW.json atomically and log {n, at, who, what, task, ops, before, after}."""
    n = _append_number({"at": _now(), "who": who, "what": what, "task": task})
    fd, tmp = tempfile.mkstemp(dir=str(FLOW.parent), prefix=".FLOW.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(after, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, FLOW)
    # THE ROW IS COMPLETED IN PLACE, under the same lock: _append_number() already reserved and wrote the number, so
    # the operation detail is filled into that row rather than written again beside it.
    fh = _lock()
    try:
        rows = entries()
        for row in rows:
            if row.get("n") == n:
                row.update({"ops": ops, "before": before, "after": after})
                break
        tmp2 = LOG.with_suffix(".jsonl.tmp")
        tmp2.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        tmp2.replace(LOG)
    finally:
        fh.close()
    return n


def guard():
    """Refuse a FLOW.json that this function did not write (C1).

    The chart's rule -- "nothing on the Flow chart is edited except through these commands" -- was prose
    in this module's docstring until 2026-10-26, and `grep -rl FLOW.json scripts/hooks/` found no hook at
    all. The changelog showed the rule being broken: six of its rows do not chain, so the file on disk was
    changed outside here at least six times.

    The test is the only thing a file can tell you about its own provenance: does it equal the newest
    row's `after`? This says nothing about whether a change was WISE -- it says whether it can be traced
    to a row, which is the property a replay needs and the one a hand edit destroys.
    """
    try:
        flow = json.loads(FLOW.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"{FLOW} does not exist -- nothing was checked"]
    except (OSError, ValueError) as e:
        return [f"FLOW.json could not be read ({type(e).__name__}: {e}) -- nothing was checked"]
    rows = entries()
    if not rows:
        return ["FLOW.json exists but FLOW-CHANGELOG.jsonl has no rows -- it was never written by "
                "flow_change.py. Use `flow_change.py request|answer|helper`."]
    last = rows[-1]
    if last.get("after") != flow:
        return [f"FLOW.json does not match the newest changelog row (n={last.get('n')}): the chart was "
                f"changed outside flow_change.py. Make the change through the function, or "
                f"`flow_change.py revert {last.get('n')}` to go back to the last logged state."]
    return []


def _repo_head(timeout=10):
    """The short HEAD of REPO, resolved at call time -- a stored hash rots on the next commit.

    WHY the cause is recorded and never swallowed (t812): a lane switch recorded against an
    unreadable head is still a switch; a switch that vanishes with the git call is the silence
    this writer exists to end. When git runs and prints nothing (the repo root is not a git
    repo, a detached worktree with no HEAD), empty is the head, so the row records git's own
    answer -- the same answer a reader gets by running git at review time.
    """
    try:
        if not REPO.is_dir():                       # unreadable repo: record the cause, write the row
            raise NotADirectoryError(f"{REPO} is not a directory")
        r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=timeout)
    except Exception as e:                          # noqa: BLE001 -- the cause goes in the row, not the bin
        return f"unavailable: {type(e).__name__}: {e}"
    return (r.stdout or "").strip()


def record_lane_change(actor, task, from_lane, from_station, to_lane, to_station,
                       command="", flow_version="", repo_head=None):
    """Append one JSONL row to LANE_LOG for a lane moving between stations, and return the row.

    WHY (workspace/rca/6a87ffecd80c.md, D3): the 2026-09-28 hand-over of station 2 was executed
    by hand and left no record at the endpoint where it happened, and the lane drifted three
    more times the same way. Nothing here infers a move: FLOW.json carries no station field on
    any of its 36 tasks and has no version key, so every value arrives from the caller.
    """
    row = {"ts": _now(),
           "actor": actor,
           "task": task,
           "from": {"lane": from_lane, "station": from_station},
           "to": {"lane": to_lane, "station": to_station},
           "command": command,
           "flow_version": flow_version,
           "repo_head": _repo_head() if repo_head is None else repo_head}
    LANE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LANE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def revert(n, who=None):
    who = who or _who()
    row = next((r for r in entries() if r.get("n") == n), None)
    if row is None:
        raise ValueError(f"no changelog entry {n}")
    before = flow_lint.load()
    return _write(row["before"], before, who, f"revert of change {n} ({row.get('what', '')})")


def _plain(res):
    parts = []
    if res["errors"]:
        parts.append("It could not be applied: " + "; ".join(res["errors"]) + ".")
    if res["warnings"]:
        parts.append("It would leave the flow with a problem it does not have now: " + "; ".join(res["warnings"]) + ".")
    if res["consequences"]:
        parts.append("It would also: " + "; ".join(res["consequences"]) + ".")
    return " ".join(parts)


def answer(tid, answer_path):
    """Route the Manager's answer on a Flow change. Returns (status, assigned) or None (retry)."""
    import taskstore
    import ask_q16
    import manager_note
    p = Path(answer_path)
    text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    if not text.strip() or ask_q16.is_deferral(text):
        return None                                   # nothing was decided: the queue tries again
    ops = extract_edit(text)
    if ops is None or isinstance(ops, str):
        step = manager_note.step_of(text)
        why = (ops if isinstance(ops, str) else
               "The Manager needs your answer before it can draft this change." if step == "b" else
               "The Manager answered without a ```flow-edit block, so nothing was changed.")
        taskstore.set_status(tid, "queued")
        # (b) is a question for Jas; a missing flow-edit block is the model's format slip (2026-10-19)
        taskstore.set_meta(tid, needs=__import__("role_store").escalate("vision" if step == "b" or isinstance(ops, str) else "technical"))
        taskstore.add_note(tid, f"{why} Its answer: {answer_path}. Reply here and it goes back to the Manager.",
                           who="manager")
        return "queued", "jas"
    before = flow_lint.load()
    res = flow_lint.lint(before, ops)
    if flow_lint.clean(res):
        n = _write(res["after"], before, "manager", f"{tid}: " + taskstore.find(tid)[0].get("title", ""),
                   task=tid, ops=ops)
        taskstore.add_note(tid, f"APPLIED as Flow change {n}: flow_lint found no error, no warning and no "
                                f"consequence. The chart shows it now. To undo it: Revert change {n} on the "
                                f"Flow tab (or scripts/flow_change.py revert {n}).", who="manager")
        taskstore.set_meta(tid, needs="")
        taskstore.set_status(tid, "done")
        return "done", ""
    taskstore.set_status(tid, "queued")
    taskstore.set_meta(tid, needs=__import__("role_store").escalate("vision"))
    taskstore.add_note(tid, "NOT APPLIED -- nothing was changed. " + _plain(res) + " The Manager's proposal is in "
                            f"{answer_path}. Reply here (for example 'go ahead', or what to do instead) and it goes "
                            "back to the Manager.", who="manager")
    return "queued", "jas"


def _who(default="unknown"):
    """Who is making this write. NEVER a guess that names a person (2026-10-27, L2).

    This module defaulted to who="jas" in main() and who="claude-code" in the helper/task builders,
    so an unattributed write was signed by whoever is most damaging to forge: two chart entries this
    session went in as claude-code and their reverts as jas, and neither of those parties made them
    (FLOW-CHANGELOG rows 89-92). AGENTS.md puts that first -- "Never write a note attributed to Jas"
    (L-091) -- so the default is now the caller's own name where the environment carries one, and
    `unknown` otherwise. An `unknown` in a title field is a fact; `jas` is a forgery.
    """
    if os.environ.get("DSH_SESSION_ID"):
        return "dsh-harness"                        # the harness signs itself (t994)
    return default


def add_helper(script, serves, what, runs=None, who=None):
    """Put a supporting script on the chart -- the step BEFORE library.py will register it (Jas,
    2026-10-01: "any new workflow or process must first be defined in the flowchart before it can be
    implemented"). Descriptive, so it is applied directly when flow_lint is clean, and logged like
    every change, so it can be reverted. Anything not clean is refused with the lint's words."""
    who = who or _who()
    h = {"script": script, "serves": [x for x in serves if x], "what": what}
    if not h["serves"]:
        raise ValueError("name the task or service it serves -- a script attached to nothing is a "
                         "process the chart does not show")
    if runs:
        h["runs"] = runs
    ops = [{"op": "add_helper", "helper": h}]
    before = flow_lint.load()
    res = flow_lint.lint(before, ops, layout=False)
    if not flow_lint.clean(res):
        raise ValueError(_plain(res))
    return _write(res["after"], before, who, f"helper {script} serves {', '.join(h['serves']) or 'nothing'}",
                  ops=ops)


def add_task_node(task, who=None):
    """Put a TASK NODE on the chart -- the step before any role may list it (P1, t8707).

    Until now the only way to add a task was `request` -> the Manager's answer -> applied, which meant a
    person could not declare one. That gap is why `skill_review` (a task type Jas wants assignable to any
    role) did not exist: `role_store.validate` refuses a role whose tasks are not in FLOW.json, so the node
    has to come first and there was no tool that could make it.

    Descriptive and applied directly when flow_lint is clean, logged like every other change so it can be
    reverted. Anything not clean is refused with the lint's own words.
    """
    if not isinstance(task, dict) or not task.get("id"):
        raise ValueError("a task node needs at least an id")
    ops = [{"op": "add_task", "task": task}]
    before = flow_lint.load()
    res = flow_lint.lint(before, ops)
    if not flow_lint.clean(res):
        raise ValueError(_plain(res))
    return _write(res["after"], before, who, f"task {task['id']}: {task.get('name', '')}"[:180], ops=ops)


def apply_ops(ops, who="claude-code", what=""):
    """Apply a LIST of flow_lint operations as ONE change (P1c, t8749).

    A single operation is usually not enough to be legal: a new task node needs the links into and out of
    it, and `flow_lint` refuses a node that nothing sends work to. Applying the node and then the links
    would mean committing an invalid chart in between, so the ops go in one lint and one write -- one
    changelog row, revertable as a unit.

    Same gate as everything else: no error, no new warning, no consequence, or nothing is written.
    """
    if not isinstance(ops, list) or not ops:
        raise ValueError("apply needs a non-empty LIST of flow_lint operations")
    before = flow_lint.load()
    res = flow_lint.lint(before, ops)
    if not flow_lint.clean(res):
        raise ValueError(_plain(res))
    return _write(res["after"], before, who, (what or f"applied {len(ops)} operation(s)")[:180], ops=ops)


def main(argv):
    who = _who()
    if "--who" in argv:
        i = argv.index("--who")
        who = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if len(argv) >= 3 and argv[0] == "request":
        print(request(argv[1], argv[2], argv[3] if len(argv) > 3 else "", who=who))
        return 0
    if len(argv) == 3 and argv[0] == "answer":
        r = answer(argv[1], argv[2])
        print(f"{argv[1]}: " + ("nothing decided -- stays queued for another pass" if r is None else
                                f"status {r[0]}, assigned {r[1] or '-'}"))
        return 0
    if len(argv) >= 2 and argv[0] == "apply":
        try:
            ops = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
            n = apply_ops(ops, who=who,
                          what=argv[2] if len(argv) > 2 else "")
        except (OSError, ValueError, flow_lint.EditError) as e:
            print(f"REFUSED: {e}")
            return 1
        print(f"on the chart as change {n}: {len(ops)} operation(s)")
        return 0
    if len(argv) >= 2 and argv[0] == "task":
        try:
            node = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
            n = add_task_node(node, who=who)
        except (OSError, ValueError, flow_lint.EditError) as e:
            print(f"REFUSED: {e}")
            return 1
        print(f"on the chart as change {n}: task {node.get('id')}")
        return 0
    if len(argv) >= 4 and argv[0] == "helper":
        rest = argv[4:]
        runs = rest[rest.index("--runs") + 1] if "--runs" in rest else None
        try:
            n = add_helper(argv[1], argv[2].split(","), argv[3], runs=runs,
                           who=who)
        except (ValueError, flow_lint.EditError) as e:
            print(f"REFUSED: {e}")
            return 1
        print(f"on the chart as change {n}: {argv[1]}")
        return 0
    if argv[:1] == ["log"]:
        for r in entries():
            print(f"  {r['n']:>3}  {r['at']}  {r['who']:8}  {r.get('what', '')}")
        return 0
    if len(argv) == 2 and argv[0] == "revert":
        print(f"reverted as change {revert(int(argv[1]), who=who)}")
        return 0
    if argv[:1] == ["guard"]:
        probs = guard()
        for x in probs:
            print(f"  {x}")
        return 1 if probs else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
