#!/usr/bin/env python3
"""The task store behind the HiveMind Hub — markdown files in git.

    workspace/tasks/<id>--<slug>.md   one file per task
    workspace/tasks/QUEUE.md          the order; top of the list is next in line
    workspace/tasks/done/             finished tasks, out of the queue and out of the way

WHY FILES AND NOT A DATABASE
----------------------------
Jas and Claude Code both read and write these tasks. Claude Code reads files at session
start; it does not open a database mid-session, and a store it cannot see is a store that
goes stale in a week. The feedback tool already proved the pattern -- notes land in a folder,
session_start.py lists them, no API and no parsing.

WHY THE ORDER LIVES IN ITS OWN FILE
-----------------------------------
A reorder rewrites one small file instead of renumbering thirty, the diff reads as a
reprioritisation, and QUEUE.md top to bottom *is* the plan. The failure mode it introduces is
drift -- a task absent from the queue, or a queue line pointing at nothing -- and that is
closed by check_tasks.py rather than by hoping.

TWO WRITERS
-----------
flock on .lock across every read-modify-write, and atomic replace on every write, so a reader
never sees half a file. Both local; GitHub is a push target and is not involved in any of it.
The lock only binds writers that take it, which is why this module has a CLI: a session that
mutates through `python3 taskstore.py` is serialised, and one that reaches for sed is not.
"""

import fcntl
import itertools
import json
import os
import threading
import re
import re as _re
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE / "scripts"))
import live_state_guard                    # noqa: E402  (one copy: is this a test?)
def _is_a_test_process(argv0=None):
    """One implementation, two names: scripts/live_state_guard.in_test (2026-10-27).

    This was a local copy while live_state_guard.py could not be registered -- it needed a
    Flow-chart write, which the board's claims were holding. It is on the chart now (change 94), so
    taskstore asks it: the id guard, the systemctl guard and the device-pause ledger guard must not
    each carry their own idea of what a test is. A fixture that vendors this file alone must vendor
    live_state_guard.py beside it -- the sandboxes that copy taskstore.py do, and test_task_cancel
    is the one that had to be told.
    """
    return live_state_guard.in_test(argv0)
# HIVEMIND_TASKS_DIR LETS A SELF-TEST MINT INTO A THROWAWAY DIRECTORY INSTEAD OF THE LIVE QUEUE
# (t4385). The write-guard self-test adds a real task to prove taskstore.py still works; when a
# run crashed or its cleanup missed, t4218--guard-self-test-delete-me.md and
# t4385--guard-self-test-delete-me.md were left in workspace/tasks, which FAILS "QUEUE.md and
# the task files name the same set" for whoever runs the check next, and burns a task id every
# run. Unset is the live path, so nothing changes for any other caller.
TASKS = Path(os.environ.get("HIVEMIND_TASKS_DIR") or (WORKSPACE / "workspace" / "tasks"))
DONE = TASKS / "done"
QUEUE = TASKS / "QUEUE.md"
LOCK = TASKS / ".lock"
ATTACH = WORKSPACE / "workspace" / "attachments"

# ONE ID SEQUENCE ACROSS EVERY CHECKOUT (t1586, 2026-10-25). A worktree's task directory is missing
# every task that only exists on main, so `max(id)+1` there minted t1554 over a live task on
# 2026-10-24. The LIVE checkout's store is the space, found the way check_workspace finds it -- the
# parent of `git rev-parse --git-common-dir` -- and each minted number is reserved there in .ids
# under that store's lock, so a second mint, here or in another checkout, cannot take it. The lock
# alone cannot do that: it orders the reads, and an id is only taken when the file is written, which
# for a worktree happens in another tree.
LIVE_ROOT = None        # None = ask git; a fixture sets it to a temp "live checkout"
ID_RESERVED = ".ids"    # the high-water mark in the space: gitignored runtime state

# "cancelled" ADDED 2026-09-14. Jas: "I want to cancel a task. I looked at some and they are
# old and no longer needed." It is deliberately NOT "done": done means the work happened and
# is the number the keep-rate and the Pareto are computed from, so filing an abandoned idea as
# done would inflate every one of them. A cancelled task leaves the queue exactly as a done
# one does and keeps its file, because WHY something was dropped is worth as much as why it
# was built -- and an id must never be reused for a different thing.
STATUSES = ["queued", "doing", "blocked", "done", "cancelled"]
LEAVES_QUEUE = ("done", "cancelled")

# Metadata Jas never touches. He types text and reorders; the filing is Claude Code's job,
# by his instruction 2026-08-21: "I want to put ideas in, then let you add whatever meta
# data you want to keep it organized." So `kind` and `area` default to unfiled on anything
# arriving from the phone, and an unfiled task is a visible signal that nobody has looked
# at it yet rather than a gap that hides.
KINDS = ["unfiled", "build", "bug", "decision", "question", "chore"]

# Who the task is actually waiting on. This is the one piece of metadata that earns its
# place on his screen: it separates "blocked on Jas" from "blocked on Claude Code", which
# is the difference between a queue he can unblock and one he can only watch.
_tmp_seq = itertools.count()

QUEUE_HEADER = """# QUEUE.md — the order of work. The top item is next in line.

Rewritten by the HiveMind Hub and by `taskstore.py`. The titles are derived from
the task files on every write and are here to make the list readable; the id is the only part
that identifies anything. Reorder from the phone, or with `taskstore.py move`.

Finished tasks leave this file and move to `done/`.

"""


# --------------------------------------------------------------------------- locking

@contextmanager
def _locked_at(path):
    """Advisory lock on any store's lock file -- the live checkout's when a worktree takes an id."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


@contextmanager
def locked():
    """Advisory lock held across a read-modify-write. Every mutating path takes it."""
    TASKS.mkdir(parents=True, exist_ok=True)
    with _locked_at(LOCK):
        yield


def write_atomic(path, text):
    """Temp file in the same directory, then replace. A reader never sees a partial file, and
    a crash mid-write leaves the previous version intact.

    The temp name carries pid + thread + a counter, not just pid. ThreadingHTTPServer serves
    every request from one process, so a pid-only name is the SAME name for concurrent
    writers -- one thread's replace pulls the file out from under another's. Found by running
    the concurrency fixture with the lock removed, which is the only reason it was found: the
    lock hides this completely until some future code path forgets to take it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.tmp{os.getpid()}-{threading.get_ident():x}-{next(_tmp_seq)}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- parsing

def slugify(text, limit=48):
    return re.sub(r"[^a-z0-9]+", "-", text.lower())[:limit].strip("-") or "task"


def parse(path):
    """Front matter plus body. Same minimal parser as feedback.py, for the same reason: a YAML
    dependency is a failure mode for six fields."""
    text = path.read_text(encoding="utf-8")
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = text[end + 4:]
    meta["path"] = path
    meta.setdefault("id", path.name.split("--")[0])
    meta.setdefault("title", meta["id"])
    meta.setdefault("status", "queued")
    meta.setdefault("area", "unfiled")
    meta.setdefault("kind", "unfiled")
    # "ASSIGN TO:" (Jas, 2026-09-29) replaces the fixed needs: jas / claude, and the field held a role key.
    # Since t9540 it holds a TIER (vision|technical|rework|production) and the FILE says `needs:`. `assigned:`
    # is still READ here for every task written before the rename -- and no longer written (_rewrite). In
    # memory both names carry the value, so every existing reader of meta["needs"] keeps working.
    if meta.get("assigned") is not None:
        meta["needs"] = meta["assigned"]
    meta.setdefault("needs", "")            # unowned means production, never a person (2026-10-19)
    meta["needs"] = assignee(meta["needs"])
    meta["assigned"] = meta["needs"]
    return meta, body.strip()


# PRODUCTION IS AN ASSIGNMENT (Jas, 2026-10-03): "by default, everything that is brought into the
# system gets assigned to production. Then when it goes to scoping, the scoper decides if it needs
# input from someone else ... and changes the assignment. And it stays in that assignment until the
# person who needs to review it ... changes it back to assigned to production." Blank -- the old
# "nobody" -- and scope_queue's internal "model" both mean Production, on read and on write, so the
# many callers that release a task by clearing the field keep working and the file says what it means.
PRODUCTION = "production"


def _vision_seat():
    """Who a question about what Jas wants goes to: the org chart's "vision" position (ROLES.json),
    not a name written here (Jas, 2026-10-19)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import role_store
    return role_store.escalate("vision")


def assignee(v):
    v = (v or "").strip()
    return PRODUCTION if v in ("", "model", PRODUCTION) else v


def tier_of(value):
    """The tier a needs:/assigned: value means, whichever vocabulary wrote it (Claude, 2026-10-29).

    The migration rewrote every 'jas' to 'vision' and 'claude' to 'technical', and every reader that
    still asked `needs == "jas"` went quietly blind -- the hold box, Needs-you, the board's column.
    Readers ask tier_of(v) == "vision" instead, which is true for both words."""
    v = assignee(value)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        import role_store
        return role_store.as_tier(v)
    except Exception as e:                                # noqa: BLE001 -- said, not swallowed (DS, lanes 6858c4)
        print(f"  NOTE: tier_of({v!r}) fell back to the legacy map: role store unreadable "
              f"({type(e).__name__}: {e})", file=sys.stderr)
        return {"jas": "vision", "claude": "technical", "dsh": "technical",
                "dsh-harness": "technical"}.get(v, v)


def in_production(meta):
    return assignee(meta.get("needs", meta.get("assigned"))) == PRODUCTION


def id_num(name):
    """The numeric part of a task id, for sorting.

    Ids are NOT zero-padded past three digits -- `:03d` is a minimum width, so t999 is
    followed by t1000 rather than rolling over, and nothing about the first 999 breaks.
    What does break is plain string sorting: "t1000" sorts before "t999". Everything that
    orders tasks sorts on this instead, so the day the queue passes 999 is uneventful.
    """
    m = re.match(r"t(\d+)", name)
    return int(m.group(1)) if m else 0


def task_files(include_done=False):
    out = list(TASKS.glob("t*--*.md")) if TASKS.is_dir() else []
    if include_done and DONE.is_dir():
        out += list(DONE.glob("t*--*.md"))
    return sorted(out, key=lambda f: (id_num(f.name), f.name))


def load_all(include_done=False):
    """id -> (meta, body) for every task on disk.

    A file may be DELETED between the glob and the read (t697: the task-guard self-test
    removes its tNNN--guard-self-test-delete-me.md probe while a live add sweeps the
    directory). A vanished file is not an error in this store's state -- it is a task
    that no longer exists -- so it is skipped, with a report, never a crash and never
    silently."""
    tasks = {}
    for f in task_files(include_done):
        try:
            meta, body = parse(f)
        except FileNotFoundError:
            print(f"  note: {f.name} vanished between glob and read (deleted by a "
                  f"concurrent process) -- skipped", file=sys.stderr)
            continue
        tasks[meta["id"]] = (meta, body)
    return tasks


def last_speaker(body):
    """Who wrote the most recent note: 'jas', 'claude-code', or None.

    Drives the "you replied, waiting on me" state, so a task he has answered stays visible
    without pretending it still needs him.
    """
    who = None
    for line in body.splitlines():
        m = re.match(r"\*\*\d{4}-\d{2}-\d{2} · ([a-z-]+)\*\*", line.strip())
        if m:
            who = m.group(1)
    return who


def find(task_id):
    for f in task_files(include_done=True):
        if f.name.startswith(f"{task_id}--"):
            try:
                return parse(f)
            except FileNotFoundError:
                # Deleted between glob and read (t697): the task no longer exists,
                # which is exactly what None, None already means to every caller.
                print(f"  note: {f.name} vanished between glob and read -- "
                      f"treated as no such task", file=sys.stderr)
                return None, None
    return None, None


# --------------------------------------------------------------------------- the queue

def read_queue():
    """Ordered ids. Lines that name no task on disk are dropped here and reported by the
    drift check -- this function's job is to return a usable order, not to hide a problem."""
    if not QUEUE.is_file():
        return []
    live = set(load_all())
    ids, seen = [], set()
    for line in QUEUE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*\d+\.\s+(t\d+)\b", line)
        if m and m.group(1) in live and m.group(1) not in seen:
            ids.append(m.group(1))
            seen.add(m.group(1))
    # A task on disk but missing from the queue goes to the bottom rather than vanishing.
    for tid in sorted(live - seen, key=id_num):
        ids.append(tid)
    return ids


def write_queue(ids):
    tasks = load_all()
    lines = [QUEUE_HEADER]
    for n, tid in enumerate(ids, 1):
        meta = tasks.get(tid, ({},))[0]
        title = meta.get("title", "(missing)")
        status = meta.get("status", "queued")
        marks = []
        if status != "queued":
            marks.append(status)
        if tier_of(meta.get("needs")) == "vision":
            marks.append("waiting on Jas")
        if meta.get("area", "unfiled") == "unfiled":
            marks.append("unfiled")
        mark = f"  _{', '.join(marks)}_" if marks else ""
        lines.append(f"{n}. {tid} — {title}{mark}")
    write_atomic(QUEUE, "\n".join(lines) + "\n")


def _resolved_path():
    """RESOLVED.md beside whatever TASKS currently is -- resolved at CALL time.

    It was `RESOLVED = TASKS / "RESOLVED.md"`, fixed at import. Every fixture that points
    this module at a temporary tree does it by reassigning TASKS, DONE, QUEUE and LOCK --
    test_task_queue.py does so four times -- and none of them knew a fifth path existed. So
    set_status() inside a fixture wrote the TEMPORARY tree's empty resolved view over the
    REAL workspace/tasks/RESOLVED.md: "0 done, 0 cancelled". Found 2026-09-14 by running
    that suite, the same day RESOLVED.md was added. Deriving it from TASKS means a fixture
    that redirects the store redirects this with it, and cannot forget to.
    """
    return TASKS / "RESOLVED.md"

RESOLVED_HEADER = """# RESOLVED.md — everything that has left the queue.

Generated by `taskstore.py`, rewritten on every status change and by
`taskstore.py resolved`. Nothing here is work: it is the record, kept so a
finished or abandoned item is OUT OF THE QUEUE without being out of reach.

Jas, 2026-09-14: "I don't want to see something if I've cancelled it. Instead
I want to have a resolved section where I can review all done and cancelled
items. That way it's out of sight, out of mind, unless I need to look for it."

DONE and CANCELLED are kept apart on purpose. `done` is the number the keep-rate
and the Pareto are computed from; filing an abandoned idea as done would inflate
every one of them. Cancelled carries its reason, because WHY something was
dropped is worth as much as why it was built.

"""


def _resolved_reason(body):
    """The CANCELLED note's reason, or '' — never a truncated prefix.

    `cancel` writes 'CANCELLED: <why>' as an attributed note, so the reason is
    already on disk in the task file; this only surfaces it. The whole line is
    carried through. A reason cut to fit a column is a reason that stops
    answering the question it was kept for (docs/skills/never-truncate-a-record).
    """
    for line in body.splitlines():
        m = re.search(r"CANCELLED:\s*(.+?)\s*$", line)
        if m:
            return m.group(1)
    return ""


def write_resolved():
    """Rewrite RESOLVED.md from the task files in done/.

    Reads the FILES, not a cached list. The queue is a list of ids and can go
    stale against the disk; this cannot, because the disk is its only input.
    """
    rows = []
    for f in sorted(DONE.glob("*.md")) if DONE.is_dir() else []:
        meta, body = parse(f)
        rows.append((meta.get("completed", ""), meta, body))
    rows.sort(key=lambda r: r[0], reverse=True)

    done = [r for r in rows if r[1].get("status") != "cancelled"]
    cancelled = [r for r in rows if r[1].get("status") == "cancelled"]

    lines = [RESOLVED_HEADER]
    lines.append(f"{len(done)} done · {len(cancelled)} cancelled "
                 f"· {len(rows)} resolved in total\n")

    lines.append("\n## Cancelled\n")
    if cancelled:
        for when, meta, body in cancelled:
            why = _resolved_reason(body)
            lines.append(f"- **{meta['id']}** — {meta.get('title','(missing)')}"
                         f"{'  · ' + when[:10] if when else ''}")
            if why:
                lines.append(f"  - why: {why}")
    else:
        lines.append("_Nothing cancelled yet._")

    lines.append("\n## Done\n")
    if done:
        for when, meta, _ in done:
            lines.append(f"- **{meta['id']}** — {meta.get('title','(missing)')}"
                         f"{'  · ' + when[:10] if when else ''}")
    else:
        lines.append("_Nothing done yet._")

    write_atomic(_resolved_path(), "\n".join(lines) + "\n")
    return len(done), len(cancelled)


def ordered(area=None):
    """The queue as (meta, body) pairs, in order. The list the Plan section renders."""
    tasks = load_all()
    out = [tasks[t] for t in read_queue() if t in tasks]
    if area:
        out = [t for t in out if t[0].get("area") == area]
    return out


# --------------------------------------------------------------------------- mutation

def live_root():
    """The live checkout's root -- the parent of this repo's common .git dir -- or None when git
    cannot say (a fixture in a temp tree, an unpacked copy). check_workspace finds it the same way."""
    if LIVE_ROOT is not None:
        return Path(LIVE_ROOT)
    return real_live_root()


def real_live_root():
    """The LIVE checkout's root, IGNORING the LIVE_ROOT override -- what a suite cannot repoint.

    Two different questions live here and they must not be answered by the same function: "which
    sequence should this mint from?" may be faked by a suite testing the worktree rule (t1586), while
    "is this the real store?" may not. The id guard asks the second, so a fixture that pretends to be
    a live checkout is allowed to mint, and the store every lane actually files into is not.
    """
    try:
        p = subprocess.run(["git", "-C", str(WORKSPACE), "rev-parse", "--git-common-dir"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode or not p.stdout.strip():
        return None
    common = Path(p.stdout.strip())
    return (common if common.is_absolute() else WORKSPACE / common).resolve().parent


def _task_files(tasks):
    tasks = Path(tasks)
    return list(tasks.glob("t*--*.md")) + list((tasks / "done").glob("t*--*.md"))


def high_water(tasks=None):
    """The highest id that store has handed out: its task files, or the .ids it reserved."""
    tasks = Path(tasks or TASKS)
    n = 0
    for f in _task_files(tasks):
        m = re.match(r"t(\d+)--", f.name)
        if m:
            n = max(n, int(m.group(1)))
    ledger = tasks / ID_RESERVED
    text = ""
    if ledger.is_file():                      # absent on a fresh store: the task files are the mark
        try:
            text = ledger.read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"  NOTE: {ledger} unreadable ({e}); the ids come from the task files alone",
                  file=sys.stderr)
    try:
        n = max(n, int(text or 0))
    except ValueError:
        print(f"  NOTE: {ledger} does not hold a number ({text!r}); the ids come from the task "
              f"files alone", file=sys.stderr)
    return n


def id_space():
    """(tasks_dir, lock_path) -- the one id space every checkout mints from (t1586).

    A checkout whose root is not the live one (a worktree) uses the LIVE store, so its ids are
    drawn from the sequence main actually holds. A fixture that repoints TASKS inside the live
    checkout is its own space, which is what every taskstore suite expects."""
    root = live_root()
    # A SUITE THAT REPOINTED TASKS IS ITS OWN SPACE, IN A WORKTREE TOO (2026-10-27). The docstring
    # below has promised this since t1586 and the code only honoured it in the live checkout -- so
    # every taskstore suite run from a worktree reserved its id out of the LIVE sequence and filed
    # the task in a temp dir. That is how workspace/tasks/.ids reached 9354 against a highest task
    # file of t8860. Comparing TASKS rather than the root states the rule the suites already rely on.
    if (root is None or root == WORKSPACE.resolve()
            or Path(TASKS).resolve() != (WORKSPACE / "workspace" / "tasks").resolve()):
        return TASKS, LOCK
    tasks = root / "workspace" / "tasks"
    return tasks, tasks / ".lock"


def reserve_id(space=None, lock_path=None):
    """The next id from `space`, recorded in it before it is used so no other mint can take it.

    lock_path=None means the caller already holds that store's lock -- the live checkout's own case,
    where the space IS this checkout's store and `locked()` is already held."""
    space = Path(space or TASKS)
    # A TEST MUST NOT MOVE THE LIVE ID COUNTER (2026-10-27, L2 Phase 0a). id_space() correctly
    # mints from the LIVE sequence for a worktree (t1586) -- but a suite that mints from a worktree
    # was reserving real numbers and never filing a task: workspace/tasks/.ids read 9354 against a
    # highest task file of t8860. The guard is at the primitive, so every caller inherits it, and it
    # needs BOTH facts: the space is the live store's, AND this process says it is a test. A lane's
    # own work in its worktree is neither.
    root = real_live_root()                  # NOT live_root(): a suite may repoint that one
    if root is not None and _is_a_test_process():
        live_tasks = Path(root) / "workspace" / "tasks"
        try:
            same = space.resolve() == live_tasks.resolve()
        except OSError:
            same = False
        if same:
            raise RuntimeError(
                f"refusing to touch LIVE state from a test: reserving a task id\n"
                f"  {space}\n"
                f"  Point the store at a temp dir before importing taskstore:\n"
                f"    HIVEMIND_TASKS_DIR=<tmp>/tasks python3 "
                f"{Path(sys.argv[0]).name if sys.argv else 'suite.py'}\n"
                f"  A fixture is its own space, so this suite then mints freely; without it every\n"
                f"  run burns a real id out of the live sequence -- 494 of them by 2026-10-27.")

    def mint():
        n = high_water(space) + 1
        write_atomic(space / ID_RESERVED, f"{n}\n")
        return f"t{n:03d}"

    if lock_path is None:
        return mint()
    with _locked_at(lock_path):
        return mint()


def _next_id():
    """The next id in THIS checkout's store, unreserved -- the number `add()` will take when this
    checkout is the live one. Kept for the callers and suites that name it; `add()` reserves."""
    return f"t{high_water(TASKS) + 1:03d}"



def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


# THE BIG-PROJECT FLAG (Jas, 2026-09-08): "some stuff on the list could be considered scoped but I
# think they are bigger projects so I want to be clear with you before they start." Delegable and
# committed are different questions, so it is a flag, not a status: in every task file and every
# idea from the phone, UNCHECKED by default so nothing is marked silently. Checked, no work starts
# (manager_next skips it) until Jas unticks it.
# NO LONGER WRITTEN BY DEFAULT (Jas, 2026-10-07): "The local models can start whatever they discover
# and the scoper can flag what I need to do." New tasks and phone ideas carry no box; a box Jas adds
# and ticks by hand still holds the task (is_big_project, manager_next), so the brake remains.
BIG_PROJECT_LINE = "- [ ] big project -- check this to require Jas's go-ahead before any work starts"
_BIG = re.compile(r"^- \[( |x|X)\] big project", re.M)


def is_big_project(task_id):
    """True when the task's big-project box is ticked; False when unticked or absent; None when no
    such task exists -- an unknown task is never silently 'not big'."""
    meta, body = find(task_id)
    if not meta:
        return None
    m = _BIG.search(body or "")
    return bool(m) and m.group(1) in "xX"


def feedback_template():
    """The text an idea from the phone starts from: the same flag, the same words, unchecked."""
    return "(your idea)\n"


def add(title, area="unfiled", body="", source="", at_top=False,
          kind="unfiled", needs="", author=""):
    """Create a task. The server names the file, never the caller -- same rule as inbox_post

    NO HOLD BY DEFAULT (Jas, 2026-09-27): `needs` defaulted to "claude", so every new task --
    the supervisor's HARNESS reports, his phone adds without a hold, app-feedback notes -- was
    held for Claude and the manager, which skips held tasks, never saw it. "I moved new harness
    tasks to the top and Q4 skipped them." A hold is now only ever set on purpose.
    and the feedback form: a caller that chooses paths can write anywhere (ROADMAP G1)."""
    space, space_lock = id_space()
    with locked():
        # The number comes from the LIVE store's space when this is a worktree (t1586); the file is
        # written here, in the checkout that filed it. The local lock is taken first and the live
        # one inside it, in that order everywhere, so two checkouts cannot deadlock on each other.
        tid = reserve_id(space, None if Path(space) == Path(TASKS) else space_lock)
        path = TASKS / f"{tid}--{slugify(title)}.md"
        front = (f"---\nid: {tid}\ntitle: {title}\narea: {area}\nkind: {kind}\n"
                 f"status: queued\nassigned: {assignee(needs)}\nsource: {source}\n"
                 f"{f'author: {author}\n' if author else ''}"
                 f"created: {now()}\n---\n\n")
        write_atomic(path, front + (body.strip() + "\n\n" if body.strip() else "")
                     + "## Notes\n\n## Attachments\n")
        ids = [i for i in read_queue() if i != tid]
        if at_top:
            ids = [tid] + ids
        elif not by_jas(source, author):
            # MACHINE TASKS GO ABOVE JAS'S (t264, Jas 2026-09-10: "if you or a local model make a
            # task, it goes above my tasks ... An exception to this is if I manually sort a task").
            # Inserted directly above his FIRST task, so his own order -- and anything he moved by
            # hand -- is untouched; only where the new task lands changes.
            first = next((n for n, i in enumerate(ids) if by_jas(*_source_author(i))), len(ids))
            ids = ids[:first] + [tid] + ids[first:]
        else:
            ids = ids + [tid]
        write_queue(ids)
        return tid


def by_jas(source="", author=""):
    """A task Jas made: from his phone or the Hub's forms, or explicitly his."""
    src = str(source or "").strip().lower()
    return (str(author or "").lower() == "jas" or src.startswith("workspace/app-feedback")
            or "from the phone" in src or src.startswith("jas") or src.startswith("human-jas")
            or src.startswith("human/jas"))


def _source_author(tid):
    meta, _ = find(tid)
    return ((meta or {}).get("source", ""), (meta or {}).get("author", ""))


def _rewrite(meta, body):
    # `wake` added 2026-09-11 (t256): the date a task asked to be looked at again.
    # session_start.py prints tasks whose wake date has arrived, which is what makes a
    # deferral survive -- fifteen "let's look at this in November" replies had nothing to
    # resurface them and sat in Jas's own queue looking like work he owed.
    # ONE FIELD, ONE NAME (t9540, after the tier migration). The file now says `needs:`; `assigned:` is READ
    # for every task written before this change (find() still honours it) and is no longer WRITTEN. It was
    # two names for one value, which is how a reader ends up comparing the wrong one -- the fault this work
    # exists to remove. scripts/migrate_needs_key.py rewrites the old files, once.
    keys = ["id", "title", "area", "kind", "status", "needs", "wake", "source", "author",
            "created", "completed"]
    meta = {**meta, "needs": meta.get("needs", meta.get("assigned", ""))}      # written as needs:
    # A KEY THIS LIST DOES NOT NAME IS SILENTLY DESTROYED, and that is how the first `wake`
    # field vanished: it was written by hand, a later `note` round-tripped the file, and the
    # rewrite dropped it without a word. Losing a field quietly is worse than refusing it, so
    # anything unrecognised is carried through and flagged rather than deleted.
    unknown = [k for k in meta if k not in keys and k not in ("path", "assigned")]
    front = "---\n" + "".join(f"{k}: {meta[k]}\n" for k in keys if k in meta)
    for k in unknown:
        front += f"{k}: {meta[k]}\n"
    front += "---\n\n"
    if unknown:
        print(f"  note: carried unrecognised front-matter field(s) {unknown} through "
              f"{meta.get('id', meta['path'])} -- add them to _rewrite's key list if they "
              f"are meant to be there", file=sys.stderr)
    write_atomic(meta["path"], front + body.strip() + "\n")


class Forged(Exception):
    """Raised when something other than the form tries to write in Jas's voice."""


def signature(who):
    """'<who> - <what>' for a note's author, derived, never typed: a person, a chat, or a model role
    with the model it runs NOW (written into the note, so it survives the role changing model)."""
    fixed = {"jas": "Jas - Human", "claude-code": "Claude - Code", "claude": "Claude - Code",
             "claude-chat": "Claude - Chat", "chat": "Claude - Chat"}
    if who in fixed:
        return fixed[who]
    if who.endswith("-chat"):
        return f"{who[:-5].title()} - Chat"
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
        import role_store
        r = role_store.load()["roles"].get(who)
        if r:
            return f"{r.get('name', who)} - {r.get('model') or r.get('kind', '')}"
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: no signature for {who!r}: {e}", file=sys.stderr)
    return ""


def caller():
    """Who is writing, from the harness that launched us (t994).

    The default used to be the literal "claude-code", so a DSH session that wrote a note
    without passing who signed it as Claude -- four times in one session on 2026-10-08.
    WORKSPACE_AGENT wins when a harness declares it; DSH exports DSH_SESSION_ID into its
    shells; Claude Code exports CLAUDECODE. A caller none of them identifies (a service)
    keeps the old default, so nothing in production starts failing because of this.
    """
    env = os.environ
    if env.get("WORKSPACE_AGENT"):
        return env["WORKSPACE_AGENT"].split("/")[0]
    if env.get("DSH_SESSION_ID") or env.get("DSH_HOME"):
        return "dsh-harness"
    return "claude-code"


# ---------------------------------------------------------------------------------------
# THE TWO THINGS A NOTE MUST NOT CARRY (t1581 + t1588, 2026-10-24)
#
# A note is the record, and a record may not contain a credential or a path that resolves to
# nothing. Both used to be repaired by hand days later:
#
#   - t1588: three records could not be COMMITTED at all on 2026-10-24 because they quoted a
#     test key -- the pre-commit hook scans the diff, so a record that quotes a credential is
#     indistinguishable from one that contains it.
#   - t1581: every probe a worker runs leaves its /tmp path in the note that describes it, so
#     the reference check grew from 2 undeclared references on 2026-10-14 to 38 on 2026-10-24
#     and none of the growth was a broken document.
#
# So the writer does both. The key shape is redacted with the SAME marker the action log and
# the task sessions use, and the raw value goes to the private, sensitivity-tagged ledger.
# A SCRATCH path (under /tmp or ~/.cache/hivemind-tmp) is declared in the pointer registry,
# because a note recording where it worked is exactly what that registry is for.
#
# A repository path that does not exist is NOT declared here. That is a claim about the repo,
# and leaving it to the check is the whole reason the check exists -- an auto-declaration
# there would silence the one case worth reading.
REGISTRY = WORKSPACE / "docs" / "EXTERNAL-POINTERS.md"
SECRET_HITS = WORKSPACE / "workspace" / "private" / "secret-hits.jsonl"
SCRATCH_PREFIXES = ("/tmp/", "~/.cache/hivemind-tmp/", str(Path.home() / ".cache" / "hivemind-tmp") + "/")
REGISTRY_SECTION = """
---

## Declared when the note was written — scratch paths a note records (t1581)

**Written by `taskstore.add_note`, not by a person.** A note that records where a probe ran is
recording a fact, and the path it names is expected to be gone: `/tmp` is RAM and probe trees
are throwaway. Declaring those at the moment the note is written is what stops the reference
check growing every time somebody investigates something. A repository path that does not
exist is deliberately NOT declared here — that is a claim about the repo, and the check is
what should see it.

| Reference in | Points at | Why it is gone |
|---|---|---|
"""


# THE FALLBACK EXISTS BECAUSE THIS MODULE RUNS IN TREES THAT HAVE NO scripts/ (2026-10-24).
# scripts/tests/test_task_cancel.py copies taskstore.py into a temp tree shaped like the
# repository -- projects/app-repo plus workspace/tasks -- and nothing else, so importing
# check_workspace there raised ModuleNotFoundError inside add_note and every cancel exited 1.
# A missing pattern must not mean "no redaction", so the shapes are repeated here as a last
# resort and a NOTE says the import failed. The oracle asserts the two are identical whenever
# the checker IS importable, which is where drift would show up first.
SECRET_PATTERN_FALLBACK = (r"sk-ant-[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{35}"
                           r"|ghp_[A-Za-z0-9]{36}|-----BEGIN [A-Z ]*PRIVATE KEY-----")


def _secret_pattern():
    """The ONE pattern, from the checker, imported lazily (falls back, loudly)."""
    try:
        sys.path.insert(0, str(WORKSPACE / "scripts"))
        import check_workspace
        return _re.compile(check_workspace.SECRET_PATTERN)
    except (ImportError, AttributeError) as e:
        print(f"  NOTE: the checker's secret pattern is not importable here ({e}); using the "
              "fallback in taskstore.py", file=sys.stderr)
        return _re.compile(SECRET_PATTERN_FALLBACK)


def _redact_text(text):
    """(clean text, raw matches). The same marker the action log writes."""
    import hashlib as _hashlib
    pattern = _secret_pattern()
    hits = []

    def _one(mt):
        raw = mt.group(0)
        digest = _hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
        hits.append({"sha256": digest, "len": len(raw), "tool": "taskstore.note",
                     "sensitivity": "secret-shaped", "raw": raw})
        return f"[redacted:secret-pattern] sha256:{digest[:8]} len:{len(raw)}"
    return pattern.sub(_one, text), hits


def _write_secret_hits(hits):
    """Raw matches, whole, to the private sensitivity-tagged ledger."""
    SECRET_HITS.parent.mkdir(parents=True, exist_ok=True)
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(SECRET_HITS, "a", encoding="utf-8") as fh:
        for h in hits:
            fh.write(json.dumps({"at": at, **h}) + "\n")


def _scratch_refs(text):
    """Backticked paths in the note that are SCRATCH and do not exist.

    Only what the reference check itself would read (a backticked path with a known extension),
    so the registry and the check speak about the same set.
    """
    found = []
    for mt in _re.finditer(r"`([A-Za-z0-9_\-./~]+\.(?:md|py|ps1|kt|kts|json|bat))`", text):
        ref = mt.group(1)
        if not ref.startswith(SCRATCH_PREFIXES):
            continue
        real = Path(ref).expanduser()
        if real.exists():
            continue
        if ref not in found:
            found.append(ref)
    return found


def _declare_scratch_refs(text, task_file):
    """Append one registry row per scratch path the note names. Idempotent."""
    refs = _scratch_refs(text)
    if not refs:
        return []
    existing = REGISTRY.read_text(encoding="utf-8") if REGISTRY.exists() else ""
    rel = (str(Path(task_file).relative_to(WORKSPACE))
           if str(task_file).startswith(str(WORKSPACE)) else str(task_file))
    rows, added = [], []
    for ref in refs:
        if ref in existing:
            continue
        rows.append(f"| `{rel}` | `{ref}` | A probe scratch path the note records; declared when "
                    f"the note was written (t1581) |")
        added.append(ref)
    if not rows:
        return []
    prefix = "" if "Declared when the note was written" in existing else REGISTRY_SECTION
    with open(REGISTRY, "a", encoding="utf-8") as fh:
        fh.write(prefix + "\n".join(rows) + "\n")
    return added


def add_note(task_id, text, who=None, _from_form=False):
    """Append-only, attributed. Both sides write to the same section; neither overwrites the
    other, which is the whole point of the shared file.

    A NOTE ATTRIBUTED TO JAS CAN ONLY COME FROM THE FORM (L-091).

    The default `who` moved from 'jas' to 'claude-code' at the same time, and that is
    part of the fix rather than tidying: a function whose DEFAULT is to speak as the
    user will eventually be called without thinking about it. The existing concurrency
    fixture did exactly that and started failing the moment the guard went in, which is
    the guard working.

    Twice on 2026-08-24, Code wrote a note in his voice while testing this very function --
    once a bug detail he never reported, once an answer he never gave. Both were caught by
    chance, because the same session happened to re-read them minutes later.

    Six months on there is no way to tell. There is no signature and no tell, and
    chat/PROTOCOL.txt puts Jas's confirmed statements at the TOP of a three-tier firewall
    specifically so they are not re-litigated -- so a forged one enters at the tier nobody
    questions again.

    The form passes _from_form=True because there the attribution is true by construction:
    he typed it. Everything else attributing to him raises. If a test needs his voice, the
    test is wrong -- assert on a note from claude-code instead.
    """
    who = who or caller()
    if who == "jas" and not _from_form:
        raise Forged(
            "refusing to write a note attributed to jas -- only the feedback form may do "
            "that (L-091). Use who='claude-code', or pass _from_form=True if you ARE the form.")
    with locked():
        meta, body = find(task_id)
        if not meta:
            return False
        # A note from Jas used to auto-clear `needs: jas`. REMOVED 2026-08-24, because it
        # silently emptied his review queue.
        #
        # The assumption was that a note from him means "answered". Often it does. But t065
        # is *"before deleting any project, rescue its memory"* -- a standing instruction he
        # needs AT THE MOMENT he deletes, not a question he retires by replying. He commented,
        # the flip fired, and it vanished from the one place he looks. He then asked why his
        # review queue was empty, which is how this was found.
        #
        # Whether his note resolves a task is a judgement, so Code makes it explicitly with
        # `taskstore.py file <id> <area> <kind> claude`. The task stays visible to him until
        # then, labelled "you replied" so he can see the ball is in my court. Clutter he can
        # see beats disappearance he cannot.
        # REDACTED AND DECLARED BEFORE THE NOTE LANDS (t1581 + t1588, 2026-10-24).
        # A key shape never enters a record, and a scratch path a note records is declared here
        # rather than by hand days later. Both are best-effort for the DECLARATION and not for
        # the redaction: a registry that cannot be written reports it and the note still lands
        # without the secret, because losing the record is worse than a dangling reference.
        text, _hits = _redact_text(text)
        if _hits:
            _write_secret_hits(_hits)
        try:
            _declare_scratch_refs(text, meta.get("path"))
        except OSError as _e:                                          # noqa: BLE001
            print(f"  NOTE: a scratch path in this note was not declared ({_e})", file=sys.stderr)
        stamp = datetime.now().strftime("%Y-%m-%d")
        # SIGNED (Jas, 2026-09-29): "<who> - <what>", e.g. "Manager - qwen3.8:27b-mtp-q4_K_M",
        # "Jas - Human", "Claude - Code". After the dash, so every parser that reads the author
        # between the middot and the ** (jas_replies, scope_queue, this file) sees what it always did.
        sig = signature(who)
        entry = f"\n**{stamp} · {who}** — {'(' + sig + ') ' if sig else ''}{text.strip()}\n"
        if "## Notes" in body:
            head, _, tail = body.partition("## Notes")
            # Insert at the end of the Notes section, before Attachments if it follows.
            if "## Attachments" in tail:
                notes, _, att = tail.partition("## Attachments")
                body = f"{head}## Notes{notes.rstrip()}\n{entry}\n## Attachments{att}"
            else:
                body = f"{head}## Notes{tail.rstrip()}\n{entry}"
        else:
            body = f"{body.rstrip()}\n\n## Notes\n{entry}"
        _rewrite(meta, body)
        return True


def add_attachment(task_id, filename):
    with locked():
        meta, body = find(task_id)
        if not meta:
            return False
        line = f"- {filename}"
        if "## Attachments" in body:
            head, _, tail = body.partition("## Attachments")
            body = f"{head}## Attachments{tail.rstrip()}\n{line}\n"
        else:
            body = f"{body.rstrip()}\n\n## Attachments\n{line}\n"
        _rewrite(meta, body)
        return True


def remove_attachment(task_id, filename):
    """Deletes the file as well as the reference. Outside git, so this is a real delete --
    which is the reason attachments are not tracked. See docs/HIVEMIND-CONSOLE.md."""
    with locked():
        meta, body = find(task_id)
        if not meta:
            return False
        safe = os.path.basename(filename)
        # Lines may carry provenance: "- name  (was x.zip, 123 bytes)". Match the
        # filename, not the whole line.
        body = "\n".join(l for l in body.splitlines()
                          if l.strip().split("  ", 1)[0].strip() != f"- {safe}")
        _rewrite(meta, body)
        f = ATTACH / safe
        if f.is_file() and f.resolve().parent == ATTACH.resolve():
            f.unlink()
        return True


def _caller():
    """The first frame outside this file: 'scripts/x.py:func' -- who asked for a reassignment."""
    import inspect
    for f in inspect.stack()[2:]:
        if Path(f.filename).resolve() != Path(__file__).resolve():
            try:
                where = Path(f.filename).resolve().relative_to(WORKSPACE)
            except ValueError:
                where = Path(f.filename).name
            return f"{where}:{f.function}"
    return "taskstore.py"


TIER_NOTE = "needs: {old} -> {new} (tier migration; NOT a reassignment -- nothing was handed to a person or a frontier model)"


def set_tier(task_id, tier, why=""):
    """Set needs: during the ONE-TIME tier migration, without writing an escalation marker.

    WHY THIS IS NOT set_meta(): set_meta writes "Reassigned <old> -> <new>", which is the exact sentence
    the 90% KPI counts as a hand-off (escalation_rate's esc_re). Folding `assigned:` into tiers touches 700+
    open tasks; doing it through set_meta would have written 700+ "Reassigned ... -> technical" notes and
    collapsed the rate it is supposed to measure -- a migration that destroys its own KPI. The note says
    what happened, and says it is not a reassignment, so the ledger stays honest AND the number stays
    comparable (t9540, Claude's decision 1: "the build is refused if the rate moves").
    """
    with locked():
        meta, body = find(task_id)
        if not meta:
            return False
        old = str(meta.get("needs") or "empty")
        meta["needs"] = tier
        if tier == PRODUCTION and meta.get("status") == "blocked":
            meta["status"] = "queued"       # releasing a hold unblocks, exactly as set_meta does
        _rewrite(meta, body)
    # The note is OURS, after the lock (add_note takes it itself, and a second flock in this process
    # blocks). It must not use set_meta's "Reassigned" verb, or the KPI counts this migration as 700+
    # escalations -- see this function's docstring.
    add_note(task_id, TIER_NOTE.format(old=old, new=tier) + (f". {why}" if why else ""),
             who="taskstore-migration")
    return True


def set_meta(task_id, why=None, who=None, **fields):
    """Set any of area / kind / needs / title. Claude Code's side of the arrangement.

    NO SILENT REASSIGNMENT (Jas, 2026-10-19). A change of who a task is assigned to always leaves a
    note: who made it (the calling code, read from the stack -- not trusted to the caller), and why
    (`why=`, or "no reason given", which is itself the finding). On 2026-10-03 one migration moved
    166 open tasks to Jas and ~300 more went to Claude across session commits, none with a note, and
    nobody could tell afterwards why any of them was where it was."""
    old_to = None
    with locked():
        meta, body = find(task_id)
        if not meta:
            return False
        old_to = assignee(meta.get("needs", meta.get("assigned")))
        for k, v in fields.items():
            if k not in ("area", "kind", "needs", "title"):
                continue
            # AN EMPTY `needs` CLEARS IT. Everything else still ignores a blank, because a
            # blank area or title is a caller mistake rather than an instruction. `needs` is
            # different: it is the field the Hold checkbox drives, and a checkbox has to be
            # able to say "no" -- a one-way hold cannot be released from the surface that set
            # it. 2026-09-14.
            if k == "needs":
                meta[k] = assignee(v)            # blank releases to Production (2026-10-03)
                # ALWAYS A TIER (Claude, 2026-10-29): a writer that still names a role -- escalate()
                # returns one, a model's OUTCOME names one -- is translated here, the one gate every
                # writer passes, so the old vocabulary cannot leak back in after the migration.
                try:
                    import role_store
                    meta[k] = role_store.as_tier(meta[k])
                except Exception as e:                        # noqa: BLE001 -- said, the raw value is kept
                    print(f"  NOTE: needs {meta[k]!r} not resolved to a tier ({type(e).__name__}: {e})",
                          file=sys.stderr)
                if meta[k] != PRODUCTION:
                    continue
                # RELEASING A HOLD UNBLOCKS (Jas, 2026-09-26: "When either of us unblock, what
                # status will it be then?"): blocked -> queued, back to the manager as new.
                if meta.get("status") == "blocked":
                    meta["status"] = "queued"
            elif v:
                meta[k] = v
        _rewrite(meta, body)
        new_to = assignee(meta.get("needs", meta.get("assigned")))
    # After the lock: add_note takes it itself, and a second flock on the file in this process blocks.
    if "needs" in fields and new_to != old_to:
        add_note(task_id, f"Reassigned {old_to} -> {new_to} by {_caller()}: "
                          f"{(why or '').strip() or 'no reason given'}", who=who or "taskstore")
    return True


def _wake_date(when):
    r"""`when` as YYYY-MM-DD, or ValueError naming why.

    The shape is not cosmetic: session_start.py:658 finds a wake date with the regex
    ^wake:\s*(\d{4}-\d{2}-\d{2})\s*$ , so anything else is stored and then never matches -- and
    "never matches" is indistinguishable from "not due yet". Junk is refused at the door instead.
    """
    from datetime import datetime
    s = (when or "").strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(f"a wake date is YYYY-MM-DD (got {s!r}) -- anything else would be stored "
                         f"and then silently never resurface the task") from None


def set_wake(task_id, when, who=None):
    """The date the queue resurfaces this task (t256, and t7521 for the writing half).

    wake was read at every session start from 2026-09-11 and nothing could write it: this tool had no
    command and a hand edit of a task file is refused by the pre-commit guard. So "let's look at this
    in November" stayed a sentence, and zero tasks carried a date. It is a DEFERRAL, not a schedule:
    nothing runs on the date, the task is printed at the top of the next session start. A note is left
    because a deferral is a decision, and the queue has to say who made it and until when.
    """
    day = _wake_date(when)
    with locked():
        meta, body = find(task_id)
        if not meta:
            return False
        meta["wake"] = day
        _rewrite(meta, body)
    # After the lock: add_note takes it itself, and a second flock on the file in this process blocks.
    add_note(task_id, f"Wake set to {day} by {_caller()}", who=who or "taskstore")
    return True


def clear_wake(task_id, who=None):
    """Remove the date. The KEY goes, not just its value: a bare wake: is a field a reader can see and
    misread, while the regex that matters ignores it either way."""
    with locked():
        meta, body = find(task_id)
        if not meta:
            return False
        old = meta.pop("wake", None)
        if old is None:
            return False
        _rewrite(meta, body)
    add_note(task_id, f"Wake cleared by {_caller()}; it was {old}", who=who or "taskstore")
    return True


CEILING = WORKSPACE / "workspace" / "test-failures-ceiling.json"


def owned_red_suites(task_id):
    """Suites the failure ceiling still allows to fail on this task's name (t660's owner rule).

    A task that leaves the queue while it owns one orphans it: test_ceiling_owned goes red, and
    red on main holds EVERY build. 2026-10-12: t1056 was closed owning
    test_dispatch_intake_counts.py, and 129 of 175 chunks then sat 'HELD: red on main'."""
    import json
    try:
        ceiling = json.loads(CEILING.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    reasons = {}
    for v in ceiling.values():
        if isinstance(v, dict):
            reasons.update(v)
    return [s for s in ceiling.get("allowed_to_fail") or []
            if re.match(r"\s*" + re.escape(task_id) + r"\b", str(reasons.get(s, "")))]


def set_status(task_id, status):
    if status not in STATUSES:
        return False
    if status in LEAVES_QUEUE:
        owned = owned_red_suites(task_id)
        if owned:
            why = (f"NOT {status.upper()}: this task still owns {len(owned)} red suite(s) in "
                   f"workspace/test-failures-ceiling.json ({', '.join(owned)}). Closing it would "
                   f"leave them ownerless and hold every build as red on main. Make them pass and "
                   f"remove them from the ceiling, or move them to the task that will.")
            print(why, file=sys.stderr)
            add_note(task_id, why, who="taskstore")
            return False
    with locked():
        meta, body = find(task_id)
        if not meta:
            return False
        meta["status"] = status
        if status in LEAVES_QUEUE:
            if "completed" not in meta:
                meta["completed"] = now()
        elif "completed" in meta:
            del meta["completed"]
        old = meta["path"]
        if status in LEAVES_QUEUE:
            DONE.mkdir(parents=True, exist_ok=True)
            meta["path"] = DONE / old.name
        elif old.parent == DONE:
            meta["path"] = TASKS / old.name
        _rewrite(meta, body)
        if meta["path"] != old:
            old.unlink()
        write_queue([i for i in read_queue() if i != task_id]
                    if status in LEAVES_QUEUE else read_queue())
        # RESOLVED.md is regenerated on EVERY status change, not only on the way
        # out. A task moving back from done/ to the queue has to leave the
        # resolved list too, or the two views disagree and the stale one is the
        # one Jas is reading.
        write_resolved()
        return True


def move(task_id, where, target=None):
    """where: 'up' | 'down' | 'top' | 'bottom' | 'before'.

    'before' is the one that matters -- it puts a task directly in front of another in a
    single step, which is how something goes from last to urgent without twenty taps."""
    with locked():
        ids = read_queue()
        if task_id not in ids:
            return False
        i = ids.index(task_id)
        ids.pop(i)
        if where == "up":
            ids.insert(max(0, i - 1), task_id)
        elif where == "down":
            ids.insert(min(len(ids), i + 1), task_id)
        elif where == "top":
            ids.insert(0, task_id)
        elif where == "bottom":
            ids.append(task_id)
        elif where == "before" and target in ids:
            ids.insert(ids.index(target), task_id)
        else:
            ids.insert(i, task_id)   # unknown move: put it back, change nothing
            return False
        write_queue(ids)
        return True

# --------------------------------------------------------------------------- the question ledger

# A task's body is where the asks live, and the body does not outlive the close: t488 carried
# three questions written as prose inside it, and once the file moved to done/ nothing read them
# again. The ledger is the one place a question survives the task that asked it. The close path
# calls write_question_ledger in the next chunk on this file; this chunk is the store alone.
QUESTION_LEDGER = "workspace/tasks/question-ledger.jsonl"

# A question is addressed to Jas when the text hands him a decision, not whenever a sentence
# ends in a question mark: "we decided to ship the 4-bit build" is prose, "your call" is an ask.
QUESTION_IDIOMS = ("your call", "recommendation", "should i", "should we", "do you want")

_OPTION_LINE = re.compile(r"^\s*([A-D])\)\s*(.*)$")
_FENCE = "```"
_ROW_KEYS = ("ts", "task", "question", "options", "asked_at", "due", "answered_by", "answer")


def find_open_questions(text):
    """Every question the body asks of Jas, in the order found.

    Two shapes count. `Decide: <question>` with A)/B)/C) lines under it is the shape the Hub
    already writes, and the options are what make it answerable in one tap. The second is prose:
    a line ending in "?" that contains an idiom such as "your call". A "?" with no idiom is a
    sentence, and a block of A)/B)/C) with no question above it is a list -- neither is an ask,
    and a ledger full of false rows is a ledger nobody reads.

    Lines inside a ``` fence are quoted material: a task quoting a transcript that contains a
    question did not ask it.
    """
    out = []
    lines = (text or "").splitlines()
    fenced = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith(_FENCE):
            fenced = not fenced
            i += 1
            continue
        if fenced:
            i += 1
            continue
        if "Decide:" in line:
            # Split on the FIRST marker: where a body quotes "Decide: ... Decide: ...", the
            # first marker is part of the question's own text.
            q = line.split("Decide:", 1)[1].strip().strip("`").strip()
            if q:
                options = []
                j = i + 1
                while j < len(lines):
                    nxt = lines[j]
                    if not nxt.strip() or "Decide:" in nxt:
                        break
                    m = _OPTION_LINE.match(nxt)
                    if not m:
                        break
                    options.append({"letter": m.group(1), "text": m.group(2).strip()})
                    j += 1
                out.append({"question": q, "options": options})
                i = j
                continue
        elif line.rstrip(" \t`*").endswith("?") and any(
                idiom in line.lower() for idiom in QUESTION_IDIOMS):
            out.append({"question": line.strip().strip("`").strip("*").strip(), "options": []})
        i += 1
    return out


def write_question_ledger(task_id, text, *, path=None, asked_at=""):
    """Append one row per open question in `text`; return the rows appended.

    A body that asks nothing appends nothing and creates no file -- an empty ledger in git would
    say "we looked" without saying what was found. Append-only on purpose: a question asked twice
    is a question asked twice, and answering it is a new row, so the history of an ask survives.
    """
    questions = find_open_questions(text)
    if not questions:
        return []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [{"ts": ts, "task": task_id, "question": q["question"], "options": q["options"],
             "asked_at": asked_at, "due": "", "answered_by": "", "answer": ""}
            for q in questions]
    path = Path(path) if path is not None else WORKSPACE / QUESTION_LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return rows


def unanswered_rows(path):
    """The rows whose (task, question) no row answers -- the list the close gate will read.

    A line that is not valid JSON, not an object, or missing one of the eight keys is a FINDING
    printed to stderr, and the row is treated as OPEN: a ledger line we cannot parse is a question
    we cannot prove was answered, and swallowing it silently is how a question goes addressless a
    second time.
    """
    rows, answered = [], set()
    with Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                print(f"FINDING: {path} line {lineno}: not valid JSON: {exc}", file=sys.stderr)
                rows.append({"raw": line.rstrip("\n"), "finding": str(exc), "line": lineno})
                continue
            if not isinstance(row, dict):
                why = f"not a JSON object, found {type(row).__name__}"
                print(f"FINDING: {path} line {lineno}: {why}", file=sys.stderr)
                rows.append({"raw": line.rstrip("\n"), "finding": why, "line": lineno})
                continue
            missing = [k for k in _ROW_KEYS if k not in row]
            if missing:
                print(f"FINDING: {path} line {lineno}: row is missing key(s): "
                      f"{', '.join(missing)}", file=sys.stderr)
            rows.append(row)
            if str(row.get("answer") or "").strip():
                answered.add((row.get("task"), row.get("question")))
    return [r for r in rows
            if "finding" in r or (r.get("task"), r.get("question")) not in answered]



# --------------------------------------------------------------------------- CLI

USAGE = """taskstore.py — the HiveMind Hub's queue, from a terminal

  list [area]                     the queue in order
  show <id>                       one task in full
  add "<title>" [area] [source]   append a task to the queue
  note <id> "<text>" [who]        append an attributed note (default who: the calling harness, t994)
  cancel <id> <why>               drop it -- not done, and the reason is required
  hold <id> [who]                 park it for Jas; shows in Review (sets needs: jas)
  ask <id> "<question>" [who]     note a question for Jas AND route the task to him, in one step
  release <id>                    un-park it
  status <id> <queued|doing|blocked|done|cancelled>
  resolved                        rewrite RESOLVED.md (done + cancelled)
  file <id> <area> [kind] [needs]  set the metadata Jas never has to think about
  move <id> <up|down|top|bottom|before> [target-id]
  wake <id> <YYYY-MM-DD>          the date the queue resurfaces it, not a schedule
  wake <id> --clear              remove the date

Use this rather than editing the files by hand: it takes the lock, and a hand edit does not.
"""


# WHAT COUNTS AS AN ESCALATION, in one place because two writers need it: the KPI, and the tier migration
# that must prove it did not create any (t9540). A TIER HAND-OFF IS STILL A HAND-OFF (Claude's decision 1):
# needs: becomes vision|technical|rework|production, so a trail now reads "Reassigned production ->
# technical"; without this the rate would silently IMPROVE when the vocabulary changed -- the escalations
# would stop being COUNTED, not stop happening. jas/claude stay for the notes written before the migration.
ESC_RE = re.compile(r"\bNEEDS_JAS\b|\bReady to merge\b|\bheld for Jas\b|\bHELD by \w|"
                    r"\bRE-HELD\b|Reassigned\s+\S+\s+->\s*(?:jas|claude|vision|technical|rework)\b",
                    re.I)


def escalation_rate(days=30, now=None):
    """How many tasks in a window went scope -> done with NO escalation (Jas's 90% line, 2026-10-27).

    AN ESCALATION IS ANY HAND-OFF TO A PERSON OR A FRONTIER MODEL, which is the definition 06 gives:
    an assignment that became `jas` or `claude`, a "Ready to merge" waiting on someone, a hold, or a
    NEEDS_JAS note. This measures the PAST, because no local model is running this week: the
    denominator is the tasks CREATED in the window and the numerator the ones that reached `done`
    with none of those markers anywhere in their notes.

    WHERE IT READS: the task files only -- the frontmatter and the notes. That is deliberate for a
    first number (it needs no new ledger) and it is also the first caveat: a decision taken in chat,
    by phone, or in a handover is invisible here, so a task whose escalation left no note reads as
    clean. The brief also names the dispatch/agent-run ledgers; they are NOT read yet, and the
    printed caveats say so rather than implying a completeness this does not have.
    """
    from datetime import datetime, timezone, timedelta
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=float(days))
    esc_re = ESC_RE
    created = done = escalated = cancelled = unreadable = 0
    ids = {"escalated": [], "clean": []}
    for tid, (meta, body) in load_all(include_done=True).items():
        raw = meta.get("created") or ""
        try:
            made = datetime.strptime(raw, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            # NOT SWALLOWED (the library check is right): a task file whose created: cannot be read
            # is a finding about the STORE, so it is counted and printed with the number rather than
            # dropped. It cannot be placed in a window, so it is outside the denominator either way.
            unreadable += 1
            continue
        if made < since or made > now:
            continue
        created += 1
        status = (meta.get("status") or "").strip()
        why = esc_re.search(body or "") or re.search(r"^assigned:\s*(jas|claude)\s*$",
                                                     "", re.M)
        to_person = tier_of(meta.get("needs") or meta.get("assigned")) in ("vision", "technical")
        if why or to_person:
            escalated += 1
            ids["escalated"].append(tid)
            continue
        if status == "done":
            done += 1
            ids["clean"].append(tid)
        elif status == "cancelled":
            cancelled += 1
    return {"days": float(days), "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "created": created,
            "done_clean": done, "escalated": escalated, "cancelled": cancelled,
            "unreadable_dates": unreadable,
            "rate": (100.0 * done / created) if created else 0.0,
            "ids": ids}


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    cmd, rest = argv[0], argv[1:]
    # before the flag guard below: this one takes --days
    if cmd == "escalation-rate":
        days = 30.0
        for i, a in enumerate(rest):
            if a == "--days" and i + 1 < len(rest):
                try:
                    days = float(rest[i + 1])
                except ValueError:
                    print("  --days takes a number of days")
                    return 2
        r = escalation_rate(days)
        print(f"  escalation rate: {r['rate']:.1f}%  ({r['done_clean']} of {r['created']} tasks "
              f"created since {r['since']} reached done with no escalation)")
        print("  created %d - done-clean %d - escalated %d - cancelled %d"
              % (r["created"], r["done_clean"], r["escalated"], r["cancelled"]))
        print(f"  CAVEATS: reads task files only (no dispatch/agent-run ledgers yet); an escalation "
              f"that left no note is invisible;")
        print(f"           'escalated' means ANY marker ever appeared, so it understates the rate. "
              f"No local model runs, so this is the PAST.")
        if r["unreadable_dates"]:
            print(f"  AND {r['unreadable_dates']} task file(s) have no readable created: date, so they "
                  f"are in no window -- fix the store, not this number.")
        return 0
    # A FLAG IS NEVER A TITLE OR AN ID (session-review finding 837fbc3cfdbf): `add --help`
    # created a task titled "--help". Every subcommand takes words, so a leading dash here is
    # someone asking for usage.
    if rest and rest[0].startswith("-"):
        print(USAGE)
        return 0 if rest[0] in ("-h", "--help") else 2

    if cmd == "list":
        area = rest[0] if rest else None
        rows = ordered(area)
        if not rows:
            print("queue is empty")
            return 0
        for n, (meta, _) in enumerate(rows, 1):
            bits = []
            if meta["status"] != "queued":
                bits.append(meta["status"])
            if tier_of(meta.get("needs")) == "vision":
                bits.append("WAITING ON JAS")
            flag = f"  [{', '.join(bits)}]" if bits else ""
            print(f"{n:3}. {meta['id']}  {meta['area']:<18} {meta.get('kind','?'):<9} "
                  f"{meta['title']}{flag}")
        return 0

    if cmd == "show" and rest:
        meta, body = find(rest[0])
        if not meta:
            print(f"no such task: {rest[0]}", file=sys.stderr)
            return 1
        print(f"{meta['id']}  [{meta['status']}]  {meta['area']}\n{meta['title']}\n")
        print(body)
        return 0

    if cmd == "add" and rest:
        tid = add(rest[0], rest[1] if len(rest) > 1 else "workspace",
                  source=rest[2] if len(rest) > 2 else "")
        print(tid)
        return 0

    if cmd == "note" and len(rest) >= 2:
        who = rest[2] if len(rest) > 2 else caller()
        try:
            return 0 if add_note(rest[0], rest[1], who) else 1
        except Forged as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 2

    if cmd == "file" and len(rest) >= 2:
        f = {"area": rest[1]}
        if len(rest) > 2:
            f["kind"] = rest[2]
        if len(rest) > 3:
            f["needs"] = rest[3]
        # Confirm like hold/release (t1543, P3-6): a silent `file` that lands and a `file` on an
        # id that is not on disk were indistinguishable -- the caller could not tell "it worked"
        # from "that id does not exist", which is the silent-tooling half of the issue-1 root
        # cause. One stdout line per outcome, in the house form "<id>: <verdict>".
        ok = set_meta(rest[0], **f)
        if not ok:
            print(f"{rest[0]}: no such task")
            return 1
        # Echo back exactly the fields the call supplied (2/3/4-arg forms), never the ones it
        # did not, so a minimal `file <id> <area>` does not lie about kind/needs it left alone.
        parts = [f"area={rest[1]}"]
        if len(rest) > 2:
            parts.append(f"kind={rest[2]}")
        if len(rest) > 3:
            parts.append(f"needs={rest[3]}")
        print(f"{rest[0]}: filed {' '.join(parts)}")
        return 0

    # HOLD / RELEASE. Jas, 2026-09-14: "You and local models and the Scoper can all hold a
    # task." A terminal verb rather than a form field, so every non-browser caller has the same
    # handle the checkbox has -- and it is the SAME `needs: jas` field the Review tab reads, so
    # a held task appears there without anyone moving it.
    # CANCEL takes a REASON, and refuses without one. A task dropped with no reason is
    # indistinguishable later from one dropped by mistake, and the id is gone either way.
    if cmd == "cancel" and len(rest) >= 1:
        why = " ".join(rest[1:]).strip()
        if not why:
            print("REFUSED: cancel needs a reason -- taskstore.py cancel <id> <why>")
            return 1
        if not find(rest[0])[0]:
            print(f"{rest[0]}: no such task")
            return 1
        # ATTRIBUTED TO THE CALLER, NOT TO JAS. This said who="jas", which the
        # forgery guard refuses for everyone except the feedback form (L-091) --
        # so `cancel` raised Forged for every caller and NOTHING WAS EVER
        # CANCELLED: 220 task files on disk, zero with status cancelled, on a
        # command that had shipped. Found on 2026-09-14 by Jas asking why t151
        # was still in the queue after he had cancelled it. The status vocabulary
        # and RESOLVED.md were both built against a command that could not run.
        #
        # The decision is still Jas's and the note must say so -- but it says so
        # as REPORTED SPEECH in the reason text, which is what the reason field
        # is for. Speaking in his voice was never needed to record his decision.
        add_note(rest[0], f"CANCELLED: {why}", who=caller())
        ok = set_status(rest[0], "cancelled")
        print(f"{rest[0]}: {'cancelled' if ok else 'could not cancel'}")
        return 0 if ok else 1

    if cmd == "ask" and len(rest) >= 2:
        # A QUESTION FOR JAS IS ROUTED TO JAS IN THE SAME STEP (t254). Twenty notes once ended in a
        # question to him on tasks routed elsewhere, so his Review list -- needs: jas -- never
        # showed them. Writing the question and routing the task were two acts by one hand; this
        # makes them one, so they cannot disagree.
        if not find(rest[0]):
            print(f"{rest[0]}: no such task")
            return 1
        who = rest[2] if len(rest) > 2 else caller()
        add_note(rest[0], f"QUESTION FOR JAS: {rest[1]}", who=who)
        set_meta(rest[0], needs=_vision_seat())
        print(f"{rest[0]}: asked, and routed to Jas (it shows in his Review list)")
        return 0

    if cmd == "hold" and len(rest) >= 1:
        ok = set_meta(rest[0], needs=_vision_seat(), why="held for Jas (taskstore.py hold)",
                      who=rest[1] if len(rest) > 1 else None)
        if ok:
            # HELD GOES TO THE BOTTOM. Jas, 2026-09-14: "If a held item will block the flow, we
            # need a rule that moved held items to the bottom." A hold means "not work to start"
            # -- so leaving it high in the queue is the worst of both: nobody may work it, and
            # it still sits in front of things that can move. It stays fully visible in Review,
            # which is where he actually reads held items; the queue position only governs what
            # the flow reaches next. release() does NOT restore the old position, deliberately:
            # a task coming off hold has been re-decided and should be placed on purpose.
            move(rest[0], "bottom")
            who = rest[1] if len(rest) > 1 else caller()
            add_note(rest[0], f"HELD by {who} -- parked for Jas to look at, not work to start.",
                     who=who)
        print(f"{rest[0]}: {'held' if ok else 'no such task'}")
        return 0 if ok else 1

    if cmd == "release" and len(rest) >= 1:
        ok = set_meta(rest[0], needs="", why="released (taskstore.py release)")
        print(f"{rest[0]}: {'released' if ok else 'no such task'}")
        return 0 if ok else 1

    if cmd == "resolved" and not rest:
        d, c = write_resolved()
        print(f"RESOLVED.md: {d} done, {c} cancelled")
        return 0

    if cmd == "status" and len(rest) == 2:
        return 0 if set_status(rest[0], rest[1]) else 1

    if cmd == "wake" and len(rest) >= 2:
        tid, when = rest[0], rest[1]
        clearing = when in ("--clear", "clear", "-")
        try:
            ok = clear_wake(tid) if clearing else set_wake(tid, when)
        except ValueError as e:
            print(f"REFUSED: {e}")
            return 2
        if not ok:
            print(f"no such task, or nothing to clear: {tid}")
            return 1
        print(f"  {tid}: wake {'cleared' if clearing else 'set to ' + when}")
        return 0
    if cmd == "move" and len(rest) >= 2:
        return 0 if move(rest[0], rest[1], rest[2] if len(rest) > 2 else None) else 1

    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
