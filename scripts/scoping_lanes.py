#!/usr/bin/env python3
"""Parallel scoping (Jas, 2026-10-08: "Why is scoping capped at 1?" -> "Yes", build it).

manager_queue.sh scoped one task at a time. Now it runs up to N workers, and N comes from ONE
place: the scheduler's lane rotation (dispatch_loop), where Scoping takes its turn beside RCA,
session review and the rest, counts against the model's lanes, and honours Scoping's Max lanes on
the Models tab. The scheduler writes the number it granted; the queue starts workers up to it.

    python3 scripts/scoping_lanes.py live            # count of live workers
    python3 scripts/scoping_lanes.py target          # how many workers the queue may run now
    python3 scripts/scoping_lanes.py claim <task>    # exit 0 claimed, 1 already claimed
    python3 scripts/scoping_lanes.py release <task> [<task> ...]
    python3 scripts/scoping_lanes.py claim-batch <pid> <task> [<task> ...]   # all or none (t1177)

CLAIMS are files in manager-queue/claims/<task> holding the worker's pid, made with O_EXCL so two
workers can never take the same task; a claim whose pid is dead is stale and pruned on read (a
restart kills the workers without an END). At least ONE worker always runs -- the lane Scoping
always had -- so a dead scheduler can slow scoping but never stop it.
"""
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
Q = REPO / "workspace" / "run-logs" / "manager-queue"
GRANT_MAX_AGE_S = 1800          # a scheduler silent this long is dead; fall back to one worker


def _alive(pid):
    """Read /proc, never signal: a zombie (State Z) is finished, not alive. (os.kill(pid, 0) was
    the first version; callers that stub os.kill to count signals saw it as one.)"""
    try:
        st = Path(f"/proc/{int(pid)}/stat").read_text()
    except (OSError, ValueError):
        return False
    return st.rsplit(")", 1)[-1].split()[:1] != ["Z"]


def live(q=None, role=None):
    """[(task, pid)] of live workers; stale claims are removed.

    ONE QUEUE PER SCOPING ROLE (Jas, 2026-10-14: "I want them both to scope"). A claim reads
    "<pid> <role>"; one with no role is the Manager's (every claim before this). With `role`, only
    that role's workers are returned -- each role's lanes are its own model's."""
    d = Path(q or Q) / "claims"
    out = []
    for f in sorted(d.glob("t*")) if d.is_dir() else []:
        try:
            words = f.read_text().split()
            pid = int(words[0])
        except (OSError, ValueError, IndexError):
            words, pid = [], 0
        if pid and _alive(pid):
            # A claim with no role word belongs to the role holding the scheduler's grant (t9537:
            # looked up, never the literal "manager", so a replaced role keeps its old claims).
            if role is None or (words[1] if len(words) > 1 else grant_role()) == role:
                out.append((f.name, pid))
        else:
            f.unlink(missing_ok=True)
    return out


def claim(task, pid=None, q=None):
    d = Path(q or Q) / "claims"
    d.mkdir(parents=True, exist_ok=True)
    live(q)                                     # a dead worker's claim must not block its task
    try:
        fd = os.open(d / task, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(f"{pid or os.getppid()}\n")
    return True


def release(task, q=None):
    (Path(q or Q) / "claims" / task).unlink(missing_ok=True)


def claim_batch(tasks, pid=None, q=None):
    """Claim EVERY task in a scoping batch, or none (t1177). Each claim is the same O_EXCL file as
    claim(), so a task held alone by another worker -- or inside another worker's batch -- makes this
    fail and release what it had taken: two lanes can never hold overlapping batches."""
    taken = []
    for t in tasks:
        if not claim(t, pid, q):
            for x in taken:
                release(x, q)
            return False
        taken.append(t)
    return True


def write_grant(n, q=None, now=None):
    p = Path(q or Q) / "GRANT"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"workers": int(n), "at": int(now or time.time())}) + "\n")


def granted(q=None, now=None):
    """The scheduler's last grant, or None when it is missing or older than GRANT_MAX_AGE_S."""
    try:
        g = json.loads((Path(q or Q) / "GRANT").read_text())
    except (OSError, ValueError):
        return None
    if (now or time.time()) - int(g.get("at", 0)) > GRANT_MAX_AGE_S:
        return None
    return int(g.get("workers", 1))


def cap(d=None):
    """Scoping's Max lanes from the role store; 0 = no limit."""
    sys.path.insert(0, str(REPO / "scripts"))
    import task_routing
    d = d or task_routing.load()
    return task_routing.max_lanes(d, "scoping")


def grant_role(d=None):
    """The role the scheduler's scoping GRANT is for: whoever holds Scoping in the store (t9537).
    Was the literal "manager"; delete that role, give Scoping to a new one, and this follows."""
    sys.path.insert(0, str(REPO / "scripts"))
    import task_routing
    return task_routing.owner(d or task_routing.load(), "scoping")


def target(q=None, now=None, d=None, role=None):
    """Workers the queue may run: the grant, at least 1, never above Max lanes."""
    if role is not None and role != grant_role(d):
        return role_target(role, d)
    # THE FLOOR OF ONE IS ONLY FOR A SILENT SCHEDULER (2026-10-08, lanes 6 -> 1): with one lane a
    # permanent scoping worker would hold it forever. A fresh grant -- even 0 -- is obeyed.
    g = granted(q, now)
    n = 1 if g is None else g
    c = cap(d)
    n = min(n, c) if c else n
    # HARD BACKSTOP (2026-10-08: 4 builds + 2 scoping workers on 4 lanes filled swap; the box was
    # rebooted). Whatever the grant says, never run more workers than the model's lanes leave free
    # beside the OTHER jobs actually running now (builds, preflights, reviews -- read from /proc).
    room = free_lanes()
    return n if room is None else min(n, room)


def lane_model(argv):
    """The model a live job runs on, from its own command line: --model, else the model of its --role
    in the role store; "" for a job that is not real work (a test suite's sandbox copy outside the
    repository); None when it cannot be told -- counted against the model, the safe side.

    2026-10-14: free_lanes counted EVERY live job against Swift's lanes -- four Q4 RCAs on the ThinkPad
    and a test suite's fake coder.py included -- so 7 'others' on 4 lanes left 0 for scoping and the
    Manager scoped nothing while Swift was idle."""
    script = next((a for a in argv if a.endswith(".py")), "")
    if script and not script.startswith(str(REPO)):
        return ""
    for flag in ("--model", "--role"):
        if flag in argv and argv.index(flag) + 1 < len(argv):
            v = argv[argv.index(flag) + 1]
            if flag == "--model":
                return v
            try:
                import roles
                return roles.tag(v, "") or None
            except Exception:                                       # noqa: BLE001 -- unknown: counted
                return None
    return None


def role_target(role, d=None):
    """Workers a scoping role OTHER than the Manager may run (2026-10-14): its own lanes on the Models
    tab, never above its Scoping Max lanes, minus every other live job on its model. The scheduler's
    GRANT is the Manager's rotation on Swift and says nothing about this role's model. 0 when the
    role is stopped (lanes 0, e.g. the ThinkPad cycle in Frontline mode) or Scoping is off its list."""
    sys.path.insert(0, str(REPO / "scripts"))
    import task_routing
    d = d or task_routing.load()
    if not task_routing.entry(d, role, "scoping"):
        return 0
    n = int((d.get("roles", {}).get(role) or {}).get("lanes") or 0)
    c = task_routing.max_lanes(d, "scoping", role)
    n = min(n, c) if c else n
    model = (d.get("roles", {}).get(role) or {}).get("model") or ""
    try:
        import lane_status
        others = sum(1 for L in lane_status.lanes()
                     if not (L.get("script") == "ask_q16.py" and "manager:" in " ".join(L.get("argv", [])))
                     and lane_model(L.get("argv") or []) == model)
    except Exception as e:                                          # noqa: BLE001 -- said; lanes stand
        print(f"  NOTE: live jobs unknown ({type(e).__name__}: {e})", file=sys.stderr)
        others = 0
    return max(0, n - others)


def free_lanes(model="swift"):
    """Lanes left for scoping workers = the model's lanes minus every live NON-scoping job (the
    workers' own ask_q16 calls are not subtracted: they are what this number bounds). None when
    unreadable."""
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import lane_status
        import modellease
        lanes = modellease._engine_slots(model)
        if not lanes:
            return None
        live = lane_status.lanes()
        others = sum(1 for L in live if not (L.get("script") == "ask_q16.py"
                                           and "manager:" in " ".join(L.get("argv", [])))
                     and lane_model(L.get("argv") or []) in (model, None))
        return max(0, lanes - others)
    except Exception as e:                                          # noqa: BLE001 -- said; the grant stands
        print(f"  NOTE: free lanes unknown ({type(e).__name__}: {e})", file=sys.stderr)
        return None


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "live":
        print(len(live(role=argv[1] if len(argv) > 1 else None)))
    elif cmd == "target":
        print(target(role=argv[1] if len(argv) > 1 else None))
    elif cmd == "claim" and len(argv) > 1:
        return 0 if claim(argv[1], int(argv[2]) if len(argv) > 2 else None) else 1
    elif cmd == "claim-batch" and len(argv) > 2:
        return 0 if claim_batch(argv[2:], int(argv[1])) else 1
    elif cmd == "release" and len(argv) > 1:
        for t in argv[1:]:
            release(t)
    elif cmd == "claimed":
        for t, _ in live():
            print(t)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
