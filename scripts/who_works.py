"""Who is working on what: the Manager, the Senior and the Frontline, each NOW and NEXT.

Jas, 2026-09-29: "I want to see what my manager is doing, Sr, and Frontline. I like how on the
backlog the task the manager is working on and working next are outlined and labeled. I want
that on the production tabs and other tabs where I would see work so I can see the exact item
being worked on."

TWO HALVES, BECAUSE ONE IS EXPENSIVE. What each role takes NEXT depends on the dispatcher's own
sorting (classify, priority_items), which runs target suites to learn which are red -- far too
slow for a page. So the dispatcher writes it once per tick (record(), from dispatch_loop.main)
into workspace/run-logs/who-works.json, using the very code that decides. The page reads that
file and re-checks every NOW against the process table (snapshot()), so a job that finished
after the tick reads as finished rather than as running. The Manager's own queue is read live
from its progress.log and scripts/manager_next.py, the same sources the Backlog marks use.

Roles, by where the work runs: the Senior is whatever runs on the senior station's port
(SERVING.json roles.senior); the Frontline is production builds (coder.py); the Manager is every
other job on the main server -- supervision, session reviews, RCAs, script reviews, and the
manager queue's own task.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SNAP = REPO / "workspace" / "run-logs" / "who-works.json"
PROGRESS = REPO / "workspace" / "run-logs" / "scoping-queue" / "progress.log"
ROLES = ("manager", "senior", "frontline", "auditor")
TASK = re.compile(r"\b(t\d{2,4})\b")

KIND_LABEL = {"production": "build", "preflight": "preflight", "supervise": "supervision",
              "session-review": "session review", "rca": "RCA", "script-review": "script review",
              "scoping-queue": "backlog task", "console": "console message", "script-audit": "script audit",
              "scoping": "scoping"}


def item(kind, key, model=None, pid=None, task=None):
    key = re.sub(r"^brief-(.*?)(\.txt)?$", r"\1", str(key or ""))   # a build is named by its brief
    if task is None:
        m = TASK.search(key)
        task = m.group(1) if m else None
    return {"kind": kind, "label": KIND_LABEL.get(kind, kind), "key": key, "task": task,
            "model": model, "pid": pid}


# THE STORE'S ROLE KEYS -> THIS VIEW'S NAMES. The dispatcher records the role that took a job; the
# port rules below are only for rows written before it did (2026-10-02: Gemma's session reviews
# would have shown as the Manager's, because only a build counted as the Frontline's).
# THE AUDITOR IS ITS OWN ROLE (2026-10-13, Jas: "The item the auditor is working on is labeled manager
# working in the RCA stage"). It was folded into the Manager when it shared the Manager's model; since
# 2026-10-12 it is the Q4 on the ThinkPad, a separate lane.
STORE_ROLE = {"coder": "frontline", "manager": "manager", "senior": "senior", "auditor": "auditor"}


def build_role():
    """The tile a build shows on: the role whose list has Building, read from the store. None when no
    role has it -- no default tile (Jas, 2026-10-18: "I want the scheduler to match the assignments
    of the Roles tab. If a thing has no fallback there shouldn't be assignment of that task")."""
    try:
        import task_routing
        return STORE_ROLE.get(task_routing.owner(task_routing.load(), "building"))
    except Exception as e:                                          # noqa: BLE001 -- said; no tile
        print(f"  NOTE: Building's role unread ({type(e).__name__}: {e}); the build shows on no tile")
        return None


def _role_by_model(model):
    """The store role whose model this is, when only one role runs it (the Auditor's Q4)."""
    try:
        import task_routing
        owners = [k for k, r in task_routing.load().get("roles", {}).items() if r.get("model") == model]
        return STORE_ROLE.get(owners[0]) if len(owners) == 1 else None
    except Exception as e:                                          # noqa: BLE001 -- said, port rules follow
        print(f"  NOTE: role by model unread ({type(e).__name__}: {e})")
        return None


def role_of(kind, port, senior_port, main_port, role=None, model=None):
    """The tile a job shows on, from the Roles tab only: the role the job carries; else the one role
    serving its model; else the role that owns its task. NO PORT GUESSES (2026-10-18): the senior
    station's port made a job the Senior's and the main port made anything the Manager's, which is
    how a task no role holds still landed on a tile. `port`, `senior_port`, `main_port` are kept for
    the callers and deliberately not read."""
    if role in STORE_ROLE:
        return STORE_ROLE[role]
    by_model = _role_by_model(model) if model else None
    if by_model:
        return by_model
    if kind == "production":
        return build_role()
    try:
        import task_routing
        return STORE_ROLE.get(task_routing.owner(task_routing.load(), kind))
    except Exception as e:                                          # noqa: BLE001 -- said; no tile
        print(f"  NOTE: owner of {kind} unread ({type(e).__name__}: {e}); no tile")
        return None



def _lanes_off(tile):
    """True when the store role behind this tile has 0 lanes (turned off on the Roles tab)."""
    try:
        import task_routing
        d = task_routing.load()
        role = next((k for k, v in STORE_ROLE.items() if v == tile), tile)
        return role in d.get("roles", {}) and task_routing.role_lanes(d, role) == 0
    except Exception as e:                                          # noqa: BLE001 -- said; shown as before
        print(f"  NOTE: lanes for {tile} unread ({type(e).__name__}: {e})")
        return False

def record(running, senior=None, senior_started=None, senior_waiting=(), manager_waiting=(),
           frontline_waiting=(), main_port=11434, path=SNAP, off=()):
    """Written by the dispatcher each tick. NEVER RAISES: a view must not take the tick down."""
    try:
        sp = senior["port"] if senior else None
        out = {r: {"now": [], "next": []} for r in ROLES}
        for r in running:
            # A BUILD IS A BRIEF ON DISK (Jas, 2026-09-29: "The Frontline is working but I don't see
            # that item in the building section"). lane_status sees every coder.py process, including
            # the fake ones the test suites launch (model faketest, a sentence for a brief) while a
            # merge gate runs; those are not the Frontline's work.
            if r.get("kind", "production") == "production" and not (
                    REPO / "workspace" / "briefs" / str(r.get("brief") or "")).is_file():
                continue
            role = role_of(r.get("kind", "production"), r.get("server_port", main_port), sp, main_port,
                           r.get("role"), r.get("model"))
            if role:
                out[role]["now"].append(item(r.get("kind", "production"),
                                             r.get("key") or r.get("chunk") or r.get("brief"),
                                             r.get("model"), r.get("pid")))
        if senior_started:
            out["senior"]["now"].append(item(senior_started.get("kind", "preflight"), senior_started["key"],
                                             senior_started.get("model"), senior_started.get("pid")))
        # ONE JOB, ONE LINE. lane_status sees the manager's model process (kind "manager") under the
        # dispatcher job that started it, and the manager queue's own task is read from progress.log
        # at render; the bare process row is kept only when nothing else explains it.
        mgr = out["manager"]["now"]
        if any(i["kind"] != "manager" for i in mgr):
            out["manager"]["now"] = [i for i in mgr if i["kind"] != "manager"]
        busy = {i["key"] for rr in out.values() for i in rr["now"]}
        # THE FRONTLINE TILE'S QUEUE IS THE FRONTLINE ROLE (t694): routing the queue through
        # build_role() sent production waiting to the Manager, so the Frontline's overflow was hidden.
        # THE CAP IS SAID, NOT HIDDEN (audit s10 J1): the number the [:3] slice cuts off travels with
        # the list as next_more -- queued work the Frontline had but nobody could see.
        for role, waiting in (("senior", senior_waiting), ("manager", manager_waiting),
                              ("frontline", frontline_waiting)):
            kept = [item(k, key) for k, key in waiting if key not in busy]
            # A ROLE TURNED OFF HAS NO "NEXT" (Jas, 2026-10-19: "It has had t1447 assigned next for a
            # while but I turned that role off"). 0 lanes on the Roles tab = off; the queue is not its.
            if role in off:                       # the caller says which tiles are off
                kept = []
            out[role]["next"] = kept[:3]
            out[role]["next_more"] = max(0, len(kept) - 3)
        doc = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "epoch": time.time(),
               "senior": {"tag": senior["tag"], "port": sp} if senior else None, "roles": out}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: who-works snapshot not written: {type(e).__name__}: {e}")


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def scoping_queue_all(progress=PROGRESS):
    """Every task a scoping worker holds now (parallel scoping, 2026-10-08): the live claims, else
    the one open START in progress.log (a queue from before claims existed)."""
    try:
        import sys
        if str(REPO / "scripts") not in sys.path:
            sys.path.insert(0, str(REPO / "scripts"))
        import scoping_lanes
        claimed = [t for t, _ in scoping_lanes.live(progress.parent)]
        if claimed:
            return claimed
    except Exception as e:                                          # noqa: BLE001 -- the log still answers
        print(f"who_works: scoping claims unreadable ({type(e).__name__}: {e})", file=sys.stderr)
    one = _scoping_queue_open(progress)
    return [one] if one else []


def scoping_queue_now(progress=PROGRESS):
    """The first task a scoping worker holds, or None (scoping_queue_all has every one)."""
    every = scoping_queue_all(progress)
    return every[0] if every else None


def _scoping_queue_open(progress=PROGRESS):
    """The task the manager queue has STARTed and not yet ENDed, or None."""
    cur = None
    try:
        lines = progress.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
    except OSError:
        return None
    # YIELD/IDLE/STOPPED are written only BETWEEN tasks, so any of them closes an open START. A
    # restart kills the running task without an END (t460, 2026-09-29: START at 05:35, the queue
    # restarted, then half an hour of YIELD lines while the Backlog still flagged t460 WORKING).
    for line in lines:
        m = re.match(r"\S+ (START|END|YIELD|IDLE|STOPPED)\b(?: (t\d+))?", line)
        if m:
            cur = m.group(2) if m.group(1) == "START" else None
    return cur


def scoping_queue_station(progress=PROGRESS):
    """The station (1 main, 2 the Senior's) the open manager-queue task runs on, or None.

    WHOSE TILE IS BY SERVER, NOT BY LAUNCHER (Jas, 2026-10-02: "They're both running on two different
    servers ... why does it look like their jobs are in the wrong lane?"). When scoping is the
    Senior's, scoping_queue.sh runs it on station 2 as the Senior and says so on the START line; the
    tile put it under the Manager anyway, and the Senior read idle while its server was busy."""
    cur = None
    try:
        lines = progress.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
    except OSError:
        return None
    for line in lines:
        m = re.match(r"\S+ (START|END|YIELD|IDLE|STOPPED)\b.*?(?:station (\d+))?$", line)
        if m:
            cur = int(m.group(2) or 1) if m.group(1) == "START" else None
    return cur


def snapshot(path=SNAP):
    """{at, age_s, roles: {role: {now: [...], next: [...]}}, note} for the page.

    NOW is re-checked live: a pid that has exited is dropped (and said so in `ended`), and the
    manager queue's task comes from its progress.log. The Manager's NEXT falls back to
    manager_next.upcoming() when the dispatcher has no priority work queued for it."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        doc = {"roles": {r: {"now": [], "next": []} for r in ROLES},
               "note": f"no dispatcher snapshot yet ({type(e).__name__}) -- it is written each tick"}
    doc["age_s"] = int(time.time() - doc["epoch"]) if doc.get("epoch") else None
    ended = []
    for role in ROLES:
        rr = doc["roles"].setdefault(role, {"now": [], "next": []})
        keep = []
        for i in rr["now"]:
            if i.get("pid") and not _alive(i["pid"]):
                ended.append(i)
            else:
                keep.append(i)
        rr["now"] = keep
    doc["ended"] = ended
    mgr = doc["roles"]["manager"]
    runs = doc["roles"]["senior" if scoping_queue_station() == 2 else "manager"]
    for mq in scoping_queue_all():
        # ONE LINE PER WORKER (2026-10-08): its model call is already listed as kind "manager"
        # ("manager: tNNN (top of QUEUE.md)"); adding the claim too showed 3 workers as 5.
        if not any(i.get("task") == mq for i in runs["now"]):
            runs["now"].append(item("scoping-queue", mq, task=mq))
    if not mgr["next"]:
        try:
            import sys
            if str(REPO / "scripts") not in sys.path:
                sys.path.insert(0, str(REPO / "scripts"))
            import task_routing
            # ONLY IF THE QUEUE'S WORK IS THE MANAGER'S (Models tab): Scoping or Backlog on its list.
            if not any(x.get("task") in ("scoping", "backlog")
                       for x in task_routing.tasks_of(task_routing.load(), "manager")):
                return doc
            import manager_next
            picked, _ = manager_next.upcoming(1)
            mgr["next"] = [item("scoping-queue", t, task=t) for t, _ in picked if t != mq]
        except Exception as e:                                      # noqa: BLE001
            doc["note"] = f"manager's next backlog task unreadable: {type(e).__name__}: {e}"
    return doc


def marks(doc=None):
    """{task or key: (kind, label)} -- 'now' wins over 'next'. For flagging cards on any tab."""
    doc = doc or snapshot()
    out = {}
    for which in ("next", "now"):
        for role in ROLES:
            for i in doc["roles"][role][which][:None if which == "now" else 1]:
                lab = f"{role.upper()} {'WORKING' if which == 'now' else 'NEXT'}"
                for k in {i.get("task"), i.get("key")} - {None, ""}:
                    out[k] = (which, lab)
    return out


if __name__ == "__main__":
    d = snapshot()
    age = d.get("age_s")
    print(f"who-works  (dispatcher snapshot {d.get('at', 'never')}"
          + (f", {age // 60} min old)" if age is not None else ")"))
    for role in ROLES:
        rr = d["roles"][role]
        now = ", ".join(f"{i['label']} {i['key']}" for i in rr["now"]) or "idle"
        nxt = ", ".join(f"{i['label']} {i['key']}" for i in rr["next"]) or "-"
        print(f"  {role.upper():9} NOW  {now}\n  {'':9} NEXT {nxt}")


# ---------------------------------------------------------------- tests in flight
# Jas, 2026-09-29: "If it's doing a test suite I want to see it. I just couldn't see where it was
# getting work from. Make the tests appear in the production stages so I can see those flowing
# through." A merge gate's suite launched a fake coder.py (model faketest) and the strip showed it
# as the Frontline's build. Tests are their own stage now, read live from /proc: which suite, what
# started it, how long, and what it is doing inside.
TEST_RE = re.compile(r"(?:^|/)scripts/tests/(test_[\w-]+\.py)")
HELPER_RE = re.compile(r"/tmp/[\w-]+/(fake\.py|[\w-]+\.py)")


def _procs():
    """{pid: {cmd, ppid, start}}. A process that exits between the listing and the read is counted
    in _procs.vanished, not dropped unsaid -- normal churn, but a number that says so."""
    out = {}
    _procs.vanished = 0
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            stat = (p / "stat").read_text()
        except OSError:
            _procs.vanished += 1
            continue
        if not cmd:
            continue
        rest = stat.rsplit(")", 1)[-1].split()
        if rest[0] == "Z":
            continue
        out[int(p.name)] = {"cmd": cmd, "ppid": int(rest[1]), "start": int(rest[19])}
    return out


def _launcher(pid, procs):
    """(kind, detail): the nearest known launcher above pid -- merge gate (its branches), a build
    checking its oracle (its chunk), session end, run_tests, the dispatcher, or by hand."""
    seen = set()
    p = procs.get(pid, {}).get("ppid")
    while p and p not in seen and p in procs:
        seen.add(p)
        c = procs[p]["cmd"]
        if "merge_gate.py" in c:
            return "merge-gate", re.findall(r"coder/brief-([0-9a-f]{8})", c)
        if "session_end.py" in c:
            return "session-end", None
        if "run_tests.py" in c:
            return "run-tests", None
        if "coder.py" in c and "--brief-file" in c:
            m = re.search(r"brief-(p\d+-[\w-]+)\.txt", c)
            return "oracle", m.group(1) if m else None
        if "dispatch_loop.py" in c:
            return "dispatcher", None
        p = procs[p].get("ppid")
    return "hand", None


def test_marks(runs=None, procs=None, repo=REPO):
    """{task: (label, detail)} -- a running test flagged on the card of the task it is testing.

    Jas, 2026-09-29: "If there is a test task at any stage it can just appear on that with the real
    tasks and show the role that is working on it." A merge gate is Claude's (D-34: Claude is the
    gate), its branches name their briefs, the briefs name their tasks; a build re-running its
    oracle is the Frontline's."""
    procs = procs if procs is not None else _procs()
    runs = runs if runs is not None else test_runs(procs)["runs"]
    try:
        import sys
        if str(repo / "scripts") not in sys.path:
            sys.path.insert(0, str(repo / "scripts"))
        import task_board
        by_chunk, by_sha = task_board.brief_tasks(repo)
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: test marks: briefs unreadable: {e}", file=sys.stderr)
        return {}
    out = {}
    for r in runs:
        kind, detail = _launcher(r["pid"], procs)
        mins = f"{r['elapsed_s'] // 60}m"
        what = r["test"] + (f" &rsaquo; {r['inside']}" if r.get("inside") else "")
        if kind == "merge-gate":
            for sha in detail or []:
                t = by_sha.get(sha)
                if t:
                    out[t] = ("CLAUDE TESTING", f"merge gate &middot; {what} &middot; {mins}")
        elif kind == "oracle" and detail and by_chunk.get(detail):
            out[by_chunk[detail]] = ("FRONTLINE TESTING", f"its oracle &middot; {what} &middot; {mins}")
    return out


def _origin(pid, procs):
    """What started this test: the nearest ancestor that is a known launcher."""
    seen = set()
    p = procs.get(pid, {}).get("ppid")
    while p and p not in seen and p in procs:
        seen.add(p)
        c = procs[p]["cmd"]
        if "merge_gate.py" in c:
            br = re.findall(r"coder/brief-[0-9a-f]{8}", c)
            return f"merge gate ({len(br)} branch{'es' if len(br) != 1 else ''})" if br else "merge gate"
        if "session_end.py" in c:
            return "session end"
        if "run_tests.py" in c:
            return "run_tests"
        if "coder.py" in c and "--brief-file" in c:
            m = re.search(r"brief-(p\d+-[\w-]+)\.txt", c)
            return f"build {m.group(1) if m else ''} checking its oracle"
        if "dispatch_loop.py" in c:
            return "dispatcher"
        p = procs[p].get("ppid")
    return "started by hand"


def test_runs(procs=None):
    """{runs: [...], orphans: [...]} -- every test suite running now, outermost first."""
    procs = procs if procs is not None else _procs()
    try:
        hz = os.sysconf("SC_CLK_TCK")
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        hz, uptime = 100, 0.0
    tests = {pid: TEST_RE.search(p["cmd"]).group(1) for pid, p in procs.items()
             if TEST_RE.search(p["cmd"]) and "python" in p["cmd"].split()[0]}

    def inside(pid):
        """The deepest test or coder step under this one -- what it is doing right now."""
        kids = [k for k, p in procs.items() if p["ppid"] == pid]
        for k in kids:
            deeper = inside(k)
            if deeper:
                return deeper
            if k in tests:
                return tests[k]
            if "coder.py" in procs[k]["cmd"]:
                m = re.search(r"--model (\S+)", procs[k]["cmd"])
                return f"coder.py ({m.group(1) if m else 'model?'})"
        return None

    runs = []
    for pid, name in tests.items():
        anc, outer = procs[pid]["ppid"], True
        while anc in procs:
            if anc in tests:
                outer = False
                break
            anc = procs[anc]["ppid"]
        if not outer:
            continue
        runs.append({"pid": pid, "test": name, "origin": _origin(pid, procs),
                     "elapsed_s": int(uptime - procs[pid]["start"] / hz), "inside": inside(pid)})
    orphans = [{"pid": pid, "cmd": p["cmd"], "elapsed_s": int(uptime - p["start"] / hz)}
               for pid, p in procs.items() if p["ppid"] in (1, *_user_managers(procs))
               and HELPER_RE.search(p["cmd"]) and "/tmp/" in p["cmd"]]
    return {"runs": sorted(runs, key=lambda r: -r["elapsed_s"]), "orphans": orphans}


def _user_managers(procs):
    """systemd --user: what a test helper is re-parented to when its test exits without it."""
    return {pid for pid, p in procs.items() if p["cmd"].startswith(("/usr/lib/systemd/systemd --user",
                                                                   "/lib/systemd/systemd --user"))}


# THE OLD NAME, KEPT ON PURPOSE (2026-10-30). dispatch_loop.py still calls manager_queue_now() at one site; renaming it
# there needs a signature on a guarded file. Rather than leave main with a call to a name that does not exist, the old
# name is an ALIAS: no behaviour change, and it goes the moment that caller is renamed.
manager_queue_now = scoping_queue_now
