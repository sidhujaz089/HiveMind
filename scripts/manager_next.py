#!/usr/bin/env python3
"""Which task does the manager take next? The first one in QUEUE.md that needs manager work.

    python3 scripts/manager_next.py            # the next task id, or nothing
    python3 scripts/manager_next.py --list 5   # the next five, with why earlier ones were skipped

Jas, 2026-09-24: "Q4 takes what's at the top of queue.md. Then there is no disconnect with either
of our views." Until now the manager worked a job list Claude picked, in filename order, and never
read QUEUE.md -- so Q4 was on the queue's position 279 while Jas read the top of it. The queue
is the one list; this walks it in order and returns the first task that is:
  * not waiting on Jas (needs: jas)                 -- a person has to answer first
  * not in Gemma's hands (an enrolled chunk for it whose target is still red and not parked)
  * not worked by the manager in the last 24 hours  -- read from workspace/task-sessions/<task>/
"""
import argparse
import calendar
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QUEUE = REPO / "workspace" / "tasks" / "QUEUE.md"
TASKS = REPO / "workspace" / "tasks"
SESSIONS = REPO / "workspace" / "task-sessions"
LOOP = REPO / "scripts" / "autonomy_loop.sh"
BRIEFS = REPO / "workspace" / "briefs"
RECENT_S = 24 * 3600


def queue_ids(queue=None):
    return re.findall(r"^\d+\. (t\d+) ", Path(queue or QUEUE).read_text(encoding="utf-8"), re.M)


def meta(tid, tasks=None):
    f = next(Path(tasks or TASKS).glob(f"{tid}--*.md"), None)
    if f is None:
        return {}
    head = f.read_text(encoding="utf-8", errors="replace").split("\n---", 1)[0]
    m = dict(re.findall(r"^(\w+):[ \t]*(.*)$", head, re.M))
    # THE HOLD FIELD WAS RENAMED `assigned:` on 2026-09-29 (taskstore reads both). This parser
    # read only `needs:`, so from then every held task was worked again -- the Manager itself
    # reported it while answering t485 (2026-09-30).
    if m.get("assigned") is not None:
        m["needs"] = m["assigned"].strip()
    return m


def _big_project(tid, tasks=None):
    f = next(Path(tasks or TASKS).glob(f"{tid}--*.md"), None)
    return bool(f) and re.search(r"^- \[[xX]\] big project", f.read_text(encoding="utf-8", errors="replace"),
                                 re.M) is not None


def _model_roles():
    """Model role keys from the role store; empty if it cannot be read (then nothing extra is skipped)."""
    try:
        d = json.loads((REPO / "projects" / "local-inference" / "models" / "ROLES.json").read_text(encoding="utf-8"))
        return {k for k, r in d["roles"].items() if r.get("kind", "model") == "model"}
    except (OSError, ValueError, KeyError) as e:
        print(f"  NOTE: role store unreadable ({e}); tasks assigned to model roles are not skipped",
              file=sys.stderr)
        return set()


TICK = Path(__file__).resolve().parent.parent / "workspace" / "run-logs" / "dispatch-tick-latest.json"


def spent_chunks(tick=None):
    """Chunks the scheduler's last tick found finished: 'target green -- already on main'."""
    try:
        d = json.loads(Path(tick or TICK).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"manager_next: last tick unreadable ({type(e).__name__}); no chunk counted as spent",
              file=sys.stderr)
        return set()
    out = set()
    for x in d.get("skipped") or []:
        name, why = (x.get("chunk"), x.get("why")) if isinstance(x, dict) else (x[0], x[1])
        if str(why).startswith("target green"):
            out.add(name)
    return out


def in_production(loop=None, briefs=None, spent=None):
    """Task ids that have an enrolled build chunk (its brief carries TASK: tNNN) still to build.

    A SPENT CHUNK IS NOT WORK (2026-10-13, Jas: "I cant find t1115 on the production or review tabs").
    t1115's two chunks were done by Claude directly, so their targets went green and the scheduler
    skips them as "target green -- already on main"; but they stayed enrolled, so this said "in
    production": the manager skipped the task ("a build chunk for it is enrolled") and the board, which
    keeps in-production tasks out of the backlog, showed it nowhere."""
    out = set()
    loop, briefs = Path(loop or LOOP), Path(briefs or BRIEFS)
    if not loop.is_file():
        return out
    spent = spent_chunks() if spent is None else set(spent)
    import withdrawals                  # withdrawn for a rescope (t1071): not work, so the task is free
    spent |= set(withdrawals.load())
    for line in loop.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^p\d+-[A-Za-z0-9_-]+\|([^|]+)\|", line)   # any case: bridge keeps the file stem (t124)
        if m and line.split("|", 1)[0] in spent:
            continue
        if m and (briefs / m.group(1)).is_file():
            t = re.search(r"^\s*TASK:\s*(t\d+)", (briefs / m.group(1)).read_text(encoding="utf-8"),
                          re.M)
            if t:
                out.add(t.group(1))
    return out


SERVER_FAIL_S = 15 * 60


def server_failure(f):
    """A session the model server killed is not work: it must not hide the task for 24h.

    2026-09-25: a reboot failed 22 manager runs in a minute, and each left a session file
    that made the task read as "worked in the last 24h" -- so none would have been retried.
    Counted out: no non-empty answer, an answer row with done_reason "timeout" (the socket/HTTP
    failure path in ask_q16), and under 15 minutes from start to last write. A real model
    failure (done_reason length) or a full-length timeout still counts as worked.
    """
    try:
        rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (ValueError, OSError):
        return False
    answers = [r for r in rows if r.get("kind") == "answer"]
    if any(r.get("text") for r in answers):
        return False
    # No answer row at all = the run died before asking (2026-09-26: 181 runs crashed on a
    # held lease and each hid its task for 24h). "held" = it yielded the lease. Both, like a
    # socket/HTTP "timeout", are the harness failing, not the model working.
    if answers and not any(r.get("done_reason") in ("timeout", "held") for r in answers):
        return False
    try:
        t0 = calendar.timegm(time.strptime(rows[0]["at"], "%Y-%m-%dT%H:%M:%SZ"))
    except (KeyError, ValueError):
        return False
    return f.stat().st_mtime - t0 < SERVER_FAIL_S


def worked_recently(tid, sessions=None, now=None):
    d = Path(sessions or SESSIONS) / tid
    now = now or time.time()
    if not d.is_dir():
        return False
    for f in d.glob("*.jsonl"):
        try:
            first = json.loads(f.read_text(encoding="utf-8").splitlines()[0])
        except (ValueError, IndexError, OSError) as e:
            print(f"  NOTE: unreadable session {f.name}: {e}", file=sys.stderr)
            continue
        if first.get("role") == "builder":
            continue
        if server_failure(f):
            continue
        if now - f.stat().st_mtime < RECENT_S:
            return True
    return False


def last_worked(tid, sessions=None):
    """mtime of the newest session in which the manager really worked tid; 0 if never."""
    d = Path(sessions or SESSIONS) / tid
    if not d.is_dir():
        return 0
    newest = 0
    for f in d.glob("*.jsonl"):
        try:
            first = json.loads(f.read_text(encoding="utf-8").splitlines()[0])
        except (ValueError, IndexError, OSError) as e:
            print(f"  NOTE: unreadable session {f.name}: {e}", file=sys.stderr)
            continue
        if first.get("role") == "builder" or server_failure(f):
            continue
        newest = max(newest, f.stat().st_mtime)
    return newest


def upcoming(n=1, queue=None, tasks=None, sessions=None, loop=None, briefs=None, now=None, claims=None):
    """[(task, None)] for the next n, plus [(task, why skipped)] for those passed over.

    ROTATION, NOT A 24-HOUR HOLD (Jas, 2026-09-26): "Instead of holding for 24 hours, what
    about putting it to the bottom of the list?" Tasks the manager has never worked go first,
    in QUEUE.md order; then the ones it has worked, the longest-ago first. QUEUE.md itself is
    never reordered -- his priority order stays his. The hold existed to stop the manager
    re-answering the top task forever; rotation stops that too, and when the queue is short a
    worked task comes round again sooner instead of waiting out a clock.
    """
    busy = in_production(loop, briefs)
    # PARALLEL SCOPING (2026-10-08): a task another worker has claimed is not offered again.
    try:
        import scoping_lanes
        claimed = {t for t, _ in scoping_lanes.live()} if claims is None else set(claims)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"manager_next: claims unreadable ({type(e).__name__}: {e})", file=sys.stderr)
        claimed = set()
    fresh, worked, skipped = [], [], []
    for tid in queue_ids(queue):
        if tid in claimed:
            skipped.append((tid, "being scoped by another worker"))
            continue
        m = meta(tid, tasks)
        # Only QUEUED work is the manager's: doing = scoped and on the Status board, blocked =
        # waiting on a person (Jas, 2026-09-26: none of those warrants another review).
        if m.get("status", "queued") != "queued":
            continue
        # HELD IS HELD (Jas, 2026-09-26): "if Q4 looks at a task and can't work on it shouldn't
        # it be held for me or you? If so I don't want it to review held items. That's a waste
        # of GPU capacity." A hold for either of us takes the task out of the manager's view
        # until the hold is released.
        who = (m.get("needs") or "").strip()
        who = "production" if who in ("", "model") else who
        if who == "jas":
            skipped.append((tid, "held for Jas"))
        elif who != "production" and who != "claude" and who not in _model_roles():
            # ONLY PRODUCTION IS WORK (Jas, 2026-10-03): anything assigned to someone is in their
            # Review section until they set it back to Production.
            skipped.append((tid, f"assigned to {who}, not Production"))
        elif who == "claude":
            skipped.append((tid, "held for Claude"))
        elif _big_project(tid, tasks):
            # THE BIG-PROJECT FLAG (taskstore.BIG_PROJECT_LINE): Jas ticked it to say "not before
            # we have talked" -- no work starts until he unticks it.
            skipped.append((tid, "big project: waiting for Jas's go-ahead"))
        elif m.get("needs") in _model_roles():
            # ASSIGNED TO A MODEL ROLE (plan 3b, 2026-09-30): assigned_worker.py answers the note as
            # that role; its OUTCOME: done unassigns it, and only then does it come back here. Without
            # this skip a task Assigned to: Manager was scoped here AND answered there.
            skipped.append((tid, f"assigned to {m['needs']} (Assigned items)"))
        elif tid in busy:
            skipped.append((tid, "a build chunk for it is enrolled"))
        else:
            # NEW INFORMATION MAKES IT FRESH: a task file changed after the manager last worked
            # it (a hold released, a note added) goes back in line in QUEUE.md order, not
            # behind the rotation. Only an unchanged, unusably-answered task rotates.
            w = last_worked(tid, sessions)
            f = next(Path(tasks or TASKS).glob(f"{tid}--*.md"), None)
            changed = f is not None and f.stat().st_mtime > w
            (worked if w and not changed else fresh).append((w, tid))
    order = [t for _, t in fresh] + [t for _, t in sorted(worked, key=lambda x: x[0])]
    picked = order[:n]
    for w, tid in worked:
        if tid not in picked:
            skipped.append((tid, "no usable answer yet -- back of the rotation"))
    return picked, skipped


# EQUAL DATA FROM BOTH SCOPERS (Jas, 2026-10-19: "I'll put scoping on both ... and hopefully collect equal
# data"). First come, the faster scoper takes most tasks and the comparison is skewed by task mix. Each
# scoper prefers its own half of the task numbers; when its half is empty it takes any task, so neither
# idles. scope-quality.jsonl's model field then compares like with like.
SCOPER_SLOT = {"manager": 0, "auditor": 1}


def is_harness(tid, tasks=None):
    """A HARNESS task (the supervisor's title prefix), by its file name: one glob, no read."""
    return next(Path(tasks or TASKS).glob(f"{tid}--harness-*.md"), None) is not None


def prefer_slot(order, scoper, harness=is_harness):
    """HARNESS tasks first, whoever's half they are in (Jas, 2026-10-19: a harness bug in production
    goes before the other tasks); then this scoper's half of the task numbers; then the rest."""
    first = [t for t in order if harness(t)]
    rest = [t for t in order if t not in first]
    slot = SCOPER_SLOT.get(scoper)
    if slot is None:
        return first + rest
    mine = [t for t in rest if t[1:].isdigit() and int(t[1:]) % 2 == slot]
    return first + mine + [t for t in rest if t not in mine]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", type=int, default=0)
    ap.add_argument("--scoper", default="")
    a = ap.parse_args(argv)
    picked, skipped = upcoming(10**6)
    if True:
        picked = prefer_slot(picked, a.scoper)[:max(1, a.list)]
    if a.list:
        for t in picked:
            print(t)
        for t, why in skipped[:20]:
            print(f"  skipped {t}: {why}", file=sys.stderr)
    elif picked:
        print(picked[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
