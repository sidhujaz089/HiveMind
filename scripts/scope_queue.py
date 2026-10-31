#!/usr/bin/env python3
"""Q16 turns queued ideas into briefs a coding agent can actually work, or holds them
for the owner with the missing thing named.

    python3 scripts/scope_queue.py            scope the next 12 unscoped tasks
    python3 scripts/scope_queue.py --n 40     more of them

TWO LABELS, since 2026-09-18 (Jas; t248): SCOPED = a path to the goal exists (file,
checkable done, oracle -- the oracle may not exist yet); NEEDS_JAS = held, with exactly
what is missing named (a do / do-not recommendation is allowed). The old third label
plus the must-exist-on-disk suite rule made SCOPED unreachable: 0/70 proposals, while
the owner scoped 20 of the 35 golden items precisely because a DIRECTION existed. The
fix is the label set and the definition, not the model (t248, 2026-09-08).

WHY THIS IS THE BOTTLENECK
--------------------------
97 tasks are routed to the models. FIVE name a source file and THREE name a target test suite.
`coder.py` cannot accept a change without one -- its whole guarantee is "the target suite passes
and no other suite broke" -- so 94 of those tasks cannot be worked by any model, however capable.
The constraint is not model capacity. It is that nobody has said what "done" means.

`scoped_batch.py` records what happened the last time this was done by hand: scoping three
backlog items REJECTED TWO of them, and the rejection was most of the value. A scoper that
delegates everything has not been scoping.

WHAT IT WILL NOT DO
-------------------
It does not edit tasks. `taskstore.py` owns those files and a PreToolUse hook refuses hand
edits; this writes a proposal file for a human to act on. That is deliberate: a model deciding
unsupervised that a task needs Jas would move work into his queue without anyone looking.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import jsonx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
sys.path.insert(0, str(REPO / "scripts"))
import roles  # noqa: E402  -- the model behind a role comes from SERVING.json
import deliberate                                            # noqa: E402
import modellease                                           # noqa: E402
import runledger                                            # noqa: E402

OUT = REPO / "workspace" / "scoping-proposals.jsonl"
PROG = REPO / "workspace" / "scoping-progress.jsonl"


def unscoped(limit):
    """Tasks routed to a model that name no target suite, in QUEUE.md order.

    QUEUE.md is the plan's own order ("the top item is next in line", taskstore),
    so a pass scopes what the plan says is next. A filename sort takes the oldest
    id first regardless of the plan -- measured 2026-09-20: id order skipped
    t133/t135/t116/t114 (queue #18-#21) and took t082-t088 (#53-#56) instead.
    Falls back to filename order when QUEUE.md is absent or unreadable."""
    tasks_dir = REPO / "workspace" / "tasks"
    files = sorted(tasks_dir.glob("t*.md"))
    by_id = {}
    for f in files:
        m = re.match(r"t\d+", f.stem)
        if m:
            by_id[m.group(0)] = f
    order = []
    q = tasks_dir / "QUEUE.md"
    if q.is_file():
        try:
            order = re.findall(r"^\s*\d+\.\s+(t\d+)",
                               q.read_text(encoding="utf-8", errors="replace"), re.M)
        except OSError as e:
            print(f"  NOTE: could not read QUEUE.md ({type(e).__name__}: {e}); "
                  "falling back to filename order")
            order = []
    seq = order if order else sorted(by_id)
    seq = seq + [tid for tid in sorted(by_id) if tid not in set(seq)]
    out = []
    for tid in seq:
        f = by_id.get(tid)
        if f is None:
            continue
        s = f.read_text(encoding="utf-8", errors="replace")
        if "needs: model" not in s and "assigned: model" not in s:   # assigned: since 2026-09-29
            continue
        if re.search(r"scripts/tests/test_\w+\.py", s):
            continue                    # already dispatchable
        real_id = (re.search(r"^id: (\S+)", s, re.M) or [None, tid])[1]
        title = (re.search(r"^title: (.*)$", s, re.M) or [None, ""])[1]
        out.append((real_id, title.strip(), s))
        if len(out) >= limit:
            break
    return out


TASK = """You are scoping this repository's backlog so a smaller model can work it.

For the task below, decide ONE of two things. The test of SCOPED is whether YOU can find the
path to the goal -- a direction someone can check -- not whether every detail is already
written. A vague item is not a failure: it is held for the owner with the ONE missing thing
named.

  SCOPED      there is a path: you can name the file, say what "done" means, and give an
              oracle whose exit code decides it (RULE 2: the oracle may not exist yet)
  NEEDS_JAS   you cannot find the path, or it needs a decision only the owner can make;
              name exactly what is missing, and give a do / do-not recommendation if honest

THE RULES, EACH FROM A SPECIFIC FAILURE HERE

1. **The oracle must be a COMMAND, not a sentence.** On 2026-09-04 five of six oracles written
   in prose were broken in one pass by a reviewer; the ones that survived were exit-code checks.
   `python3 scripts/tests/test_x.py` exiting 0 is an oracle. "the code is correct" is not.

2. **The oracle does not have to exist yet -- "done" has to be checkable.** 2026-09-08
   golden-set measurement (t248): demanding a suite that EXISTED on disk made SCOPED
   unreachable on the whole backlog, while the owner scoped 20 of 35 items precisely because
   a direction existed. So name the command; if it does not exist yet, set oracle_exists to
   false and say in oracle_asserts what the suite must check (the oracle stage writes it
   first -- a suite nobody wrote is not a suite that ran). If you cannot say what "done"
   looks like at all, the honest answer is NEEDS_JAS with the missing piece named.

3. **Name the file.** A brief without a file is how a model spends six rounds reading. If the
   work touches several files, name the one the change is centred on.

THE TASK:
---
{task}
---

Answer with EXACTLY ONE JSON object:
  {{"id": "{tid}", "verdict": "SCOPED",
    "file": "the main file to change (must exist on disk)",
    "done_means": "one sentence a reviewer could check",
    "oracle": "the shell command whose exit code decides it",
    "oracle_exists": true,
    "oracle_asserts": "only when oracle_exists is false: what the suite must check",
    "risk": "what a careless change here would break"}}
  {{"id": "{tid}", "verdict": "NEEDS_JAS",
    "what_is_needed": "the single thing that is missing, or only he can decide",
    "recommendation": "optional: do / do-not / ask -- and why; say do-not and why if that is honest"}}
"""

SUCCESS = """One JSON object, one verdict. If SCOPED: a file that exists on disk, a checkable
"done", and an oracle that is a command -- existing, or named with what it must assert.
If NEEDS_JAS: the specific missing thing, not a general reason."""


def extract_proposal(answer):
    """Extract a verdict object from a model's answer via jsonx.

    Maps jsonx 'no-match' onto 'no-verdict'. Returns {'status', 'proposal', 'candidates'}.
    """
    if not answer:
        return {"status": "no-json", "proposal": None, "candidates": 0}
    r = jsonx.one(answer, ["id", "verdict"])
    status = r["status"]
    if status == "no-match":
        status = "no-verdict"
    candidates = len(jsonx.objects(answer))
    return {"status": status, "proposal": r["obj"], "candidates": candidates}


# ------------------------------------------------------------------ holding for Jas
#
# Jas, 2026-09-14: "Yes to needs Jas checks the hold box." Until now a NEEDS_JAS verdict was a
# JSON line with a question in workspace/scoping-proposals.jsonl, which nothing reads -- four
# questions (t021, t033, t044 twice) sat there unseen. The design is the one recorded in
# docs/PREFERENCES.md: the scoper writes its question ONTO the task and sets the hold (needs:
# jas, bottom of the queue) through taskstore, which owns the files; Jas's reply does not
# release the task, it makes it RE-SCOPABLE; the next pass adjudicates -- anything but
# NEEDS_JAS releases it back to the models, NEEDS_JAS re-holds with a fresh question.
HOLD_MARK = "SCOPER NEEDS JAS:"
SCOPER = "q16-scoper"


def _store():
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    return taskstore


def hold_for_jas(tid, question, store=None):
    """Write the question onto the task and set the hold. Returns True when both landed."""
    ts = store or _store()
    q = (question or "").strip() or "(the scoper gave no question -- that is itself a defect)"
    ts.add_note(tid, f"{HOLD_MARK} {q}", who=SCOPER)
    ok = ts.set_meta(tid, needs=__import__("role_store").escalate("vision"))
    try:
        ts.move(tid, "bottom")                  # a hold goes to the bottom (Jas, 2026-09-14)
    except Exception as e:                      # noqa: BLE001
        # the hold (note + needs:jas) is the load-bearing part and has already landed; a
        # failed move still leaves the task parked in the Review tab. Say so instead of
        # swallowing it -- a bare pass made a dead move indistinguishable from a done
        # one (library gate, 2026-09-20).
        print(f"  NOTE: hold landed but move to bottom failed for {tid}: {type(e).__name__}: {e}")
    return bool(ok)


def release_to_models(tid, verdict, why, store=None):
    ts = store or _store()
    ts.add_note(tid, f"SCOPER RE-SCOPED after Jas's reply: {verdict}. {why}".strip(), who=SCOPER)
    return bool(ts.set_meta(tid, needs="model"))


def _notes(text):
    """[(who, text)] in file order."""
    return re.findall(r"^\*\*\d{4}-\d{2}-\d{2} · ([^*]+)\*\* — (.*)$", text, re.M)


def held_by_scoper(text):
    # 'vision' is the same hold after the tier migration (Claude, 2026-10-29): matching only 'jas'
    # left every reply to a scoper hold unread from the moment the migration rewrote the field.
    return (re.search(r"^(needs|assigned): (jas|vision)\s*$", text, re.M) is not None
            and any(HOLD_MARK in t for _, t in _notes(text)))


def answered(text):
    """A scoper hold with a note from Jas AFTER the scoper's latest question."""
    notes = _notes(text)
    last_q = max((i for i, (_, t) in enumerate(notes) if HOLD_MARK in t), default=None)
    return last_q is not None and any(w.strip() == "jas" for w, _ in notes[last_q + 1:])


def answered_holds(limit):
    """Held tasks Jas has replied to -- picked up BEFORE fresh work, because he is waiting."""
    out = []
    for f in sorted((REPO / "workspace" / "tasks").glob("t*.md")):
        s = f.read_text(encoding="utf-8", errors="replace")
        if not (held_by_scoper(s) and answered(s)):
            continue
        tid = (re.search(r"^id: (\S+)", s, re.M) or [None, "?"])[1]
        title = (re.search(r"^title: (.*)$", s, re.M) or [None, ""])[1]
        out.append((tid, title.strip(), s))
        if len(out) >= limit:
            break
    return out


def act_on_verdict(tid, proposal, store=None, task_text=None):
    """Put a verdict where Jas can see it. Returns what was done, for the proposals row.

    Two labels (t248, Jas 2026-09-18): NEEDS_JAS holds with the missing thing named;
    SCOPED releases a held task (the path IS what scopes it) or, for fresh work, notes
    the path on the task so the review queue reads the direction, not a bare label.
    """
    v = (proposal or {}).get("verdict")
    if v == "NEEDS_JAS":
        note = (proposal.get("what_is_needed") or proposal.get("question") or "").strip()
        rec = (proposal.get("recommendation") or "").strip()
        if rec:
            note = f"{note} -- recommendation: {rec}" if note else f"recommendation: {rec}"
        return "held" if hold_for_jas(tid, note, store) else "hold-failed"
    if v == "SCOPED":
        ts = store or _store()
        bits = []
        if proposal.get("file"):
            bits.append(f"file={proposal['file']}")
        dm = (proposal.get("done_means") or "").strip()
        if dm:
            bits.append(f"done={dm}")
        o = (proposal.get("oracle") or "").strip()
        if o:
            tail = "" if proposal.get("oracle_exists") is not False else " (written first, D-35)"
            bits.append(f"oracle={o}{tail}")
        path = "; ".join(bits) or (proposal.get("why") or "").strip()
        ts.add_note(tid, f"SCOPER SCOPED: {path}".rstrip(": "), who=SCOPER)
        if task_text is not None and held_by_scoper(task_text):
            return "released" if ts.set_meta(tid, needs="model") else "release-failed"
        return None
    return None


def run(tasks, ask, out_path, ledger_path, lease_path=None, model=None, run_id=None,
        store=None):
    """Walk tasks, call ask on each, record to ledger and proposal file.

    Returns ledger.reconcile() unchanged.
    """
    out_path = Path(out_path)
    ledger_path = Path(ledger_path)
    walked = [tid for tid, title, body in tasks]
    collector_version = "scope_queue-v1"
    ledger = runledger.Ledger(ledger_path, run_id, walked, collector_version=collector_version)

    lease = None
    if lease_path and model:
        lease = modellease.acquire(lease_path, model, "scoping", 3600)

    try:
        for tid, title, body in tasks:
            ledger.start(tid, title=title, model=model or "")
            t0 = time.time()
            try:
                raw = ask(tid, body)
            except Exception as e:
                err_text = str(e)
                status = "http_5xx" if "500" in err_text else "error"
                ledger.finish(tid, status, error=err_text, model=model or "")
                continue
            result = extract_proposal(raw)
            row = {"id": tid, "title": title,
                   "seconds": round(time.time() - t0, 1),
                   "raw": raw, "status": result["status"],
                   "producer": "scripts/scope_queue.py", "collector_version": collector_version}
            if result["status"] == "ok":
                row["proposal"] = result["proposal"]
            p = row.get("proposal", {})
            if p.get("verdict") == "SCOPED":
                f_ = (p.get("file") or "").strip()
                p["file_exists"] = bool(f_) and (REPO / f_).is_file()
                o = (p.get("oracle") or "").strip()
                m = re.search(r"\b[\w./-]+\.(?:py|sh)\b", o)
                # A "existing" oracle must point at something that is there: a typo is truthy
                # is the 2026-09-04 failure this rule exists for.
                if o and m:
                    p["oracle_file"] = m.group(0)
                    if p.get("oracle_exists") is True and not (REPO / m.group(0)).is_file():
                        p["oracle_exists"] = False
                if not (p["file_exists"] and (p.get("done_means") or "").strip()):
                    # Not a rejection by construction, and not an invented scope: the scoper
                    # did not find the path, so the task holds with the gap named (t248).
                    p["verdict"] = "NEEDS_JAS"
                    p["verdict_downgraded"] = ("SCOPED without an on-disk file or a checkable "
                                               "'done' -- refiled NEEDS_JAS")
                    p.setdefault("what_is_needed",
                                 "the scoper could not find the file or what done looks like -- "
                                 "confirm the path or name the missing detail")
            if row.get("proposal") and not p.get("verdict_downgraded"):
                try:
                    row["acted"] = act_on_verdict(tid, row["proposal"], store=store,
                                                  task_text=body)
                except Exception as e:                              # noqa: BLE001
                    row["acted"] = f"failed: {type(e).__name__}: {e}"
            with out_path.open("a") as f:
                f.write(json.dumps(row) + "\n")
            ledger.finish(tid, result["status"], model=model or "")
    finally:
        if lease is not None:
            modellease.release(Path(lease_path), lease)

    return ledger.reconcile()


def backfill_holds(store=None):
    """The NEEDS_JAS verdicts written before holding existed. Latest proposal per task wins."""
    latest = {}
    for line in OUT.read_text(encoding="utf-8", errors="replace").splitlines() if OUT.exists() else []:
        try:
            r = json.loads(line)
        except ValueError:
            r = {}          # a torn line has no proposal; not `continue` (test_scope_run's guard)
        if r.get("proposal"):
            latest[r["id"]] = r["proposal"]
    n = 0
    for tid, prop in latest.items():
        if prop.get("verdict") != "NEEDS_JAS":
            continue
        f = next((REPO / "workspace" / "tasks").glob(f"{tid}--*.md"), None)
        if f is None or held_by_scoper(f.read_text(encoding="utf-8", errors="replace")):
            continue
        ok = hold_for_jas(tid, prop.get("question"), store)
        print(f"  {tid}: {'held' if ok else 'COULD NOT HOLD'} -- {prop.get('question')}")
        n += ok
    print(f"  {n} task(s) held for Jas")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--model", default=roles.for_job("scoping", "qwen3.8:27b-mtp-bf16"))
    ap.add_argument("--hold-from-proposals", action="store_true",
                    help="hold every task whose LATEST proposal is NEEDS_JAS and is not held yet")
    a = ap.parse_args()

    if a.hold_from_proposals:
        return backfill_holds()
    held = answered_holds(a.n)
    tasks = held + unscoped(a.n - len(held))
    if held:
        print(f"  {len(held)} held task(s) Jas has answered -- re-scoping those first")
    print(f"  {len(tasks)} unscoped task(s) to consider\n", flush=True)

    def ask(tid, body):
        r = deliberate.deliberate(a.model, TASK.format(task=body, tid=tid), SUCCESS,
                                  budget=16000, max_chunks=2,
                                  progress=str(REPO / "workspace" / "scope-progress.jsonl"),
                                  log=lambda m: print(f"    {m.strip()}", flush=True))
        return r.answer

    ledger_path = REPO / "workspace" / "scoping-ledger.jsonl"
    lease_path = REPO / "workspace" / "scoping-lease.json"
    rep = run(tasks, ask, OUT, ledger_path, lease_path=lease_path,
              model=a.model, run_id=f"scope-{int(time.time())}")
    print(f"proposals -> {OUT.relative_to(REPO)}", flush=True)
    print(f"ledger: {rep}", flush=True)
    return 0


MAX_BATCH = 5


def shared_targets(tasks):
    """{file: [task_id, ...]} for every target file named by TWO OR MORE tasks.

    A file a single task names is not a key: the cost this measures is the second
    and later read of the same file, never the first.
    """
    by_file = {}
    for t in tasks:
        for f in t.get("files") or []:
            by_file.setdefault(f, set()).add(t["id"])
    return {f: sorted(ids) for f, ids in by_file.items() if len(ids) > 1}


def _components(tasks):
    """Task indices grouped into the connected components of the shared-file graph.

    Two tasks join when they name a common target file, so a chain t1-t2-t3
    batches together even where t1 and t3 share nothing -- that chain is read
    once. A task sharing no file with anyone stays on its own, which is the
    point: batching unrelated tasks pays the 15.2 min per scope for nothing.
    """
    parent = list(range(len(tasks)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first = {}
    for i, t in enumerate(tasks):
        for f in t.get("files") or []:
            if f in first:
                a, b = find(first[f]), find(i)
                if a != b:
                    parent[b] = a
            else:
                first[f] = i
    groups = {}
    for i in range(len(tasks)):
        groups.setdefault(find(i), []).append(i)
    return [sorted(g) for g in groups.values()]


def build_batches(tasks, max_size=MAX_BATCH):
    """Group tasks by shared target file; one dict per batch, no task dropped.

    Each connected component is cut into consecutive chunks of at most max_size
    tasks, split by ascending id, so a component bigger than one sitting is split
    without ever pairing tasks that share nothing. Within a chunk 'files' lists
    only the target files two or more tasks IN THAT CHUNK touch -- a chunk of one
    task saves no read, so its files is [] -- and 'saved_reads' is the reads not
    paid twice: per file, one per task beyond the first. Task dicts are only read.

    A batch with files comes before one without: those are the batches that save
    anything, and the owner reads the head of the list. Sorting by files[0] would
    put the empty list first, which is the order this task forbids.
    """
    batches = []
    for comp in _components(tasks):
        ordered = sorted(comp, key=lambda i: (str(tasks[i].get("id")), i))
        for start in range(0, len(ordered), max_size):
            chunk = [tasks[i] for i in ordered[start:start + max_size]]
            counts = {}
            for t in chunk:
                for f in t.get("files") or []:
                    counts[f] = counts.get(f, 0) + 1
            files = sorted(f for f, n in counts.items() if n > 1)
            batches.append({"task_ids": sorted(str(t.get("id")) for t in chunk),
                            "files": files,
                            "saved_reads": sum(n - 1 for n in counts.values() if n > 1)})
    batches.sort(key=lambda b: (1 if not b["files"] else 0, b["task_ids"][0]))
    return batches


if __name__ == "__main__":
    sys.exit(main())
