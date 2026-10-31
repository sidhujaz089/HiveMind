#!/usr/bin/env python3
"""Is any systemd user service running code older than the file on disk?

    python3 scripts/check_stale_services.py

WHY, AND WHY IT IS SEPARATE FROM check_deployed.py
--------------------------------------------------
`check_deployed.py` asks the BRIDGE whether the schema it advertises matches the source. That
is the right check for a service with an interrogable interface, and it exists because on
2026-08-25 a fix sat on disk while systemd kept serving the previous day's process.

**The same failure happened again on 2026-08-26, to a different service, and nothing caught
it.** `app-feedback` had been running since 21:02 the night before. Every accepted change to
`feedback.py` would have been invisible to Jas after merging, and the review tab built for
`t154` only reached him because the service was restarted by hand.

So the general form: a Python service started BEFORE the last change to the file it runs is
serving stale code. That needs no interface to detect, works for every service in the repo,
and would have caught both incidents.

WHAT IT DOES NOT CLAIM
----------------------
Matching timestamps prove the process started after the file changed. They do not prove the
process is healthy, or that it imported what you think. `check_deployed.py` is still the
stronger check where an interface exists; this is the floor, not the ceiling.
"""
import json
import os
import re
import subprocess
import time
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The census asks whether a running unit runs code OLDER than its file. It has never asked
# whether the service has any business existing: installing, exposing, stopping and
# uninstalling Open WebUI left no record anywhere (workspace/rca/39cc3df02e11.md), and the
# ollama.service install's only record is pasted terminal output (workspace/rca/50c99d891199.md).
# `register` is the endpoint that makes a permanent-service mutation a chart event with a
# ledger row, so a service can only come from the chart and only go with a written reason.
FLOW = REPO / 'projects' / 'local-inference' / 'models' / 'FLOW.json'
SERVICE_LEDGER = REPO / 'workspace' / 'service-changes.jsonl'
SERVICE_MUTATIONS = ('create', 'stop', 'remove')
SERVICE_READ_ONLY = ('status', 'note', 'restart', 'reload')



def proc_table():
    """[(pid, seconds running, FULL command line)] from /proc (p19). The ps listing it replaces cut
    the command line at 80 columns in a pipe, so a long `python3 /very/long/path/script.py --flags`
    read as a different -- or no -- script. Age is /proc/<pid>/stat's start time against uptime."""
    import os as _os
    hz = _os.sysconf("SC_CLK_TCK")
    uptime = float(Path("/proc/uptime").read_text().split()[0])
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            cmd = (d / "cmdline").read_bytes().decode("utf-8", "replace").replace("\0", " ").strip()
            stat = (d / "stat").read_text()
        except OSError:
            cmd = ""                        # the process exited while we looked: not a finding
        if not cmd:
            continue                        # a kernel thread has no command line
        start = int(stat.rsplit(")", 1)[1].split()[19])     # field 22, after the (comm) field
        out.append((int(d.name), int(uptime - start / hz), cmd))
    return out

def sh(*a):
    return subprocess.run(a, capture_output=True, text=True, timeout=120)


def user_services():
    """(name, script_path) for every user service whose ExecStart runs a file in this repo."""
    out = []
    r = sh("systemctl", "--user", "list-units", "--type=service", "--state=running",
           "--no-legend", "--plain")
    # Whether systemctl ANSWERED is a separate fact from what it said. Without this, a
    # systemctl that failed (no user bus, no session, running from cron) produced an empty
    # list, and an empty list read as "nothing stale" -- success. See main().
    out_ok = r.returncode == 0
    for line in r.stdout.splitlines():
        name = line.split()[0] if line.split() else ""
        if not name.endswith(".service"):
            continue
        ex = sh("systemctl", "--user", "show", name, "-p", "ExecStart", "--value").stdout
        m = [tok for tok in re.findall(r"[^\s;]+\.py", ex) if str(REPO) in tok]
        if m:
            out.append((name, Path(m[0])))
            continue
        # `python -m pkg.mod` has no .py in ExecStart at all. workspace-mcp is invoked that
        # way, and it is the very service check_deployed.py was written for -- missing it
        # here would leave the original incident uncovered by the general check.
        mod = re.search(r"-m\s+([A-Za-z_][\w.]*)", ex)
        if not mod:
            continue
        wd = sh("systemctl", "--user", "show", name, "-p", "WorkingDirectory",
                "--value").stdout.strip()
        roots = [Path(wd)] if wd else []
        roots += [REPO, REPO / "scripts", REPO / "projects"]
        rel = mod.group(1).replace(".", "/")
        for root in roots:
            for cand in (root / f"{rel}.py", root / rel / "__init__.py"):
                if cand.is_file() and str(REPO) in str(cand.resolve()):
                    out.append((name, cand.resolve()))
                    break
            else:
                continue
            break
    return out, out_ok


def started_at(name):
    v = sh("systemctl", "--user", "show", name, "-p",
           "ActiveEnterTimestampMonotonic", "--value").stdout.strip()
    try:
        mono = int(v) / 1e6
    except ValueError:
        return None
    up = float(Path("/proc/uptime").read_text().split()[0])
    return up - mono          # seconds ago the service started


def imported_locals(script, seen=None):
    """The script plus every sibling module it imports, transitively.

    NOT every .py in the directory. Globbing the folder flagged model-chat as stale because
    coder.py changed beside chatui.py -- and chatui.py does not import coder. A check that
    cries wolf on an unrelated file in the same folder gets ignored, which is worse than not
    having it.

    Still broader than the entry point alone: feedback.py imports taskstore.py, and a fix in
    the latter is exactly as invisible to a process that started before it. That was the case
    this check was built for and it must keep catching it.
    """
    import ast
    seen = seen if seen is not None else set()
    script = script.resolve()
    if script in seen or not script.is_file():
        return seen
    seen.add(script)
    try:
        tree = ast.parse(script.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return seen
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            names.add(n.module.split(".")[0])
    for name in names:
        cand = script.parent / f"{name}.py"
        if cand.is_file():
            imported_locals(cand, seen)
    return seen


def newest_change(script):
    """Seconds since the most recent mtime among the script and the modules it imports."""
    import time
    now = time.time()
    newest, who = None, None
    for p in sorted(imported_locals(script)):
        try:
            age = now - p.stat().st_mtime
        except OSError as e:
            print(f"  NOTE: {p} vanished mid-scan ({e}) -- not compared", file=sys.stderr)
            continue
        if newest is None or age < newest:
            newest, who = age, p
    return (newest if newest is not None else 0.0), (who or script)


def self_waiting_processes(min_age_s=180):
    """Processes waiting for a pattern that matches THEMSELVES. They never finish.

    `while pgrep -f foo.py >/dev/null; do sleep 30; done` is the obvious way to wait for a
    job, and it is wrong: the waiter's own command line contains "foo.py", so pgrep matches
    the waiter and the loop never exits. The job it was waiting for is long gone.

    Found six times on 2026-09-03, three of them alive simultaneously for 2h30m, 2h19m and
    1h37m -- six and a half hours of processes waiting for themselves. Nothing errored;
    each looked like a job still running, which is why they were not noticed.

    `check_workspace.py` already refuses this pattern in TRACKED shell files. These were typed
    straight into a shell by an agent and never written to disk, so that check could not see
    them. This one reads /proc, so it does not care where the command came from.

    ONE FALSE POSITIVE SHAPE, AND THE FIX FOR IT. A command that merely QUOTES this pattern --
    a heredoc writing this very function, for instance -- has the same text in its command
    line. It was flagged on the first run of this check. The discriminator is TIME: a script
    that writes a file exits in seconds, while a waiter stuck on itself accumulates hours. So
    nothing under `min_age_s` is reported, which also matches when this is worth acting on.

    Returns [(pid, minutes, pattern)].
    """
    import re as _re
    import subprocess as _sp
    ages = {pid: et for pid, et, _ in proc_table()}
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            cmd = (d / "cmdline").read_bytes().decode("utf-8", "replace").replace("\0", " ")
        except OSError as e:
            if d.exists():           # gone = exited mid-scan, normal; still there = said
                print(f"  NOTE: {d}/cmdline unreadable ({e}) -- not checked", file=sys.stderr)
            continue
        if "pgrep" not in cmd or "while" not in cmd:
            continue
        for m in _re.finditer(r"pgrep\s+(?:-\w+\s+)*['\"]?([^'\"|>\s]+)", cmd):
            pat = m.group(1).strip()
            if len(pat) < 4:
                continue
            # The pattern appears somewhere in this command line OTHER than the pgrep call
            # itself -- i.e. the waiter would match itself.
            # THE EXACT TEST, NOT A HEURISTIC (corrected 2026-09-03). The first version
            # guessed -- "the pattern appears more than once in the command line" -- and
            # missed a live self-waiter whose pattern appeared exactly once, which is the
            # commonest form. There is no need to guess: RUN THE WAITER'S OWN pgrep AND SEE
            # WHETHER ITS PID COMES BACK. If it does, the loop is waiting for itself.
            #
            # This also gets the anchored case right for free. `pgrep -f 'thing\.py$'` does
            # NOT match a waiter whose command line ends in `done`, which is exactly why the
            # anchor is the fix -- and a heuristic on text would have flagged it anyway.
            pid = int(d.name)
            age_s = ages.get(pid, 0)
            if age_s < min_age_s:
                continue
            try:
                hit = _sp.run(["pgrep", "-f", pat], capture_output=True, text=True, timeout=15)
                matches = {int(x) for x in hit.stdout.split() if x.isdigit()}
            except Exception as e:                                # noqa: BLE001
                print(f"  NOTE: pgrep {pat} failed ({e}) -- pid {pid} not matched", file=sys.stderr)
                continue
            if pid in matches:
                out.append((pid, age_s // 60, pat))
                break
    return out


def _ancestors(pid, depth=8):
    """{pid} of this process's parents, read from /proc (never by matching command lines)."""
    out = set()
    for _ in range(depth):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            pid = stat.rsplit(") ", 1)[1].split()[1]
        except (OSError, IndexError):
            break
        if pid in ("0", "1"):
            break
        out.add(pid)
    return out


def in_flight_dispatches():
    """{pid} of jobs the scheduler started and has not seen finish -- its ledger is the registry."""
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import dispatch_loop
        return {str(r.get("pid")) for r in dispatch_loop.ledger_running()}
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: dispatch ledger unreadable ({type(e).__name__}: {e}); in-flight jobs not excluded",
              file=sys.stderr)
        return set()


def sweep_holder(lock_path=None):
    """The pid of the process holding the SWEEP lock, or None (t2152, 2026-10-25).

    A TEST SWEEP IS NOT A STRAY while it holds the lock. The rule below flags a process that has been
    up an hour and whose script changed after it started -- correct for an orphan, and wrong for the
    sweep, which is bounded, recorded, and can legitimately run for hours. Measured 2026-10-25: merging
    `run_tests.py` while a 1h20m sweep was running made THE SWEEP the stray, so the gate read 1 failed
    until it ended -- the same failure that made the dispatch exemption necessary (t582/t586).

    The lock is the evidence that it is live work rather than a remainder: `run_tests.py` writes its
    pid there when it starts (scripts/run_tests.py:111). An orphan that still holds the lock is
    exempted too; the lock's own `started` stamp is what a later check can bound if that ever matters.
    """
    lock = Path(lock_path) if lock_path else (REPO / "workspace" / "run-logs" / "sweep.lock")
    try:
        row = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = row.get("pid")
    return str(pid) if pid else None


def stray_repo_processes():
    """Long-lived processes running THIS repo's code that are not systemd services.

    The service check above covers units. It cannot see a process someone started by hand or
    a session left behind -- and that is where this has actually bitten, twice:

      2026-08-27  an orphaned `context_watch.py --watch` from a previous session, re-bound by
                  newest_session() to the CURRENT session's transcript, ready to fire into a
                  harness that no longer existed.
      2026-08-31  the same script still running FOUR DAYS later, executing code that had
                  since been retired -- `--watch` now refuses to start, but a process that
                  began before that change cannot refuse anything.

    A process outliving the code it runs is the same defect the service check exists for. The
    only difference is who started it, which is not a property that makes stale code safe.

    Reported, never killed: this check states facts and a check that terminates processes is
    one nobody will run.
    """
    out = []
    live = in_flight_dispatches()
    sweep_pid = sweep_holder()
    for pid, etimes, args in proc_table():
        pid, etimes = str(pid), str(etimes)
        # AN IN-FLIGHT DISPATCH IS NOT A STRAY (t582/t586, 2026-10-01). A model job started by the
        # scheduler runs the code it started with until it finishes -- bounded, and recorded in the
        # dispatch ledger. Flagging it made session_end FAIL on a session review that had simply
        # been running for an hour when a commit touched dispatch_loop.py.
        if pid in live or _ancestors(pid) & live:      # the job, or what the job started
            continue
        if pid == sweep_pid:                           # the running sweep, which holds its own lock
            continue
        if not etimes.isdigit() or int(etimes) < 3600:      # under an hour is not stale
            continue
        # `watch -n 15 python3 scripts/x.py` names a script but runs a FRESH copy of it every
        # interval, so it is never running old code (2026-09-28: Jas's lane_status watch, up 55 h,
        # failed this check -- and a failed check makes chat-agent refuse to run at 09:00).
        if re.match(r"(\S*/)?watch\s", args):
            continue
        m = [tok for tok in re.findall(r"[^\s;'\"]+\.py", args) if str(REPO) in tok
             or tok.startswith("scripts/") or tok.startswith("projects/")]
        if not m:
            continue
        f = Path(m[0])
        if not f.is_absolute():
            f = REPO / f
        if not f.is_file():
            continue
        try:
            changed_ago = time.time() - f.stat().st_mtime
        except OSError as e:
            print(f"  NOTE: {f} vanished mid-scan ({e}) -- not compared", file=sys.stderr)
            continue
        if changed_ago < int(etimes):                       # file changed AFTER it started
            out.append((pid, int(etimes) // 3600, f.name, int(changed_ago) // 3600))
    return out


def chart_service_nodes():
    """Every id in FLOW.json's services[] and tasks[]. Read-only; nothing is copied to keep.

    An unreadable chart RAISES, after saying why on stdout. Returning an empty set would read
    as "no node exists", which refuses every registration -- a gate that fails closed on its
    own plumbing is one people route around, and routing around is how these services arrived
    unrecorded in the first place.
    """
    try:
        chart = json.loads(FLOW.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  NOTE: the Flow chart {FLOW} could not be read ({type(e).__name__}: {e})")
        raise
    nodes = set()
    for section in ('services', 'tasks'):
        for entry in chart.get(section) or []:
            node = (entry.get('id') if isinstance(entry, dict) else None) or ''
            if str(node).strip():
                nodes.add(str(node).strip())
    return nodes


def service_registration_problem(action, flow_node):
    """'' when the mutation may proceed, otherwise the reason it may not.

    The reason is RETURNED, not printed, so the API and the CLI say the same words in the same
    order: a refusal the caller cannot quote is a refusal nobody can act on.
    """
    action = str(action).strip()
    flow_node = str(flow_node).strip()
    if action not in SERVICE_MUTATIONS + SERVICE_READ_ONLY:
        return ("action " + repr(action) + " is not a service action; allowed are "
                + ", ".join(SERVICE_MUTATIONS + SERVICE_READ_ONLY))
    if action in SERVICE_MUTATIONS and not flow_node:
        return ("action " + action + " is a permanent-service mutation, so a flow node on "
                "FLOW.json is required for a permanent-service mutation")
    if action in SERVICE_MUTATIONS and flow_node not in chart_service_nodes():
        return ("flow node " + flow_node + " is not a node on the chart "
                "(FLOW.json services[]/tasks[] ids)")
    return ''


def record_service_change(action, target, actor='', task='', flow_node='', ledger=None) -> dict:
    """Append ONE whole row to the service ledger, and only if the chart accepts the mutation.

    The gate runs BEFORE the ledger is opened, and a refusal writes nothing at all -- not a
    row, not an empty file. A ledger that exists says "someone wrote here", so a refusal that
    touched the file first is indistinguishable from a mutation that recorded nothing.

    Append-only: every field is written whole, never sliced, and no recorded line is rewritten.
    """
    problem = service_registration_problem(action, flow_node)
    if problem:
        raise ValueError(problem + f" [target: {target}]")
    row = {
        'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'actor': actor,
        'action': action,
        'target': target,
        'task': task,
        'flow_node': flow_node,
    }
    path = SERVICE_LEDGER if ledger is None else Path(ledger)
    os.makedirs(str(path.parent), exist_ok=True)
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(row) + '\n')
    return row


def main(argv=None):
    argv = [] if argv is None else list(argv)
    if argv and argv[0] == 'register':
        # THE REGISTER VERB NEVER RUNS THE CENSUS. A service mutation is a chart question, not
        # a liveness question: shelling out to systemctl and walking /proc to answer "may I
        # create open-webui.service" makes the gate slow, and a slow gate gets skipped.
        usage = ("usage: check_stale_services.py register <action> <target> "
                 "[--flow-node ID] [--actor WHO] [--task ID] [--ledger PATH]; "
                 "action is one of: " + ", ".join(SERVICE_MUTATIONS + SERVICE_READ_ONLY))
        action = argv[1].strip() if len(argv) > 1 else ''
        target = argv[2].strip() if len(argv) > 2 else ''
        flow_node, actor, task, ledger = '', '', '', SERVICE_LEDGER
        i = 3
        while i < len(argv):
            flag = argv[i]
            if flag not in ('--flow-node', '--actor', '--task', '--ledger') or i + 1 >= len(argv):
                print(usage)
                return 2
            value = argv[i + 1]
            if flag == '--flow-node':
                flow_node = value
            elif flag == '--actor':
                actor = value
            elif flag == '--task':
                task = value
            else:
                ledger = Path(value)
            i += 2
        if not action or not target:
            print(usage)
            return 2
        if action not in SERVICE_MUTATIONS + SERVICE_READ_ONLY:
            print(usage)
            return 2
        if action in SERVICE_READ_ONLY:
            print(f"action {action} {target}: read-only, nothing recorded")
            return 0
        try:
            record_service_change(action, target, actor=actor, task=task,
                                  flow_node=flow_node, ledger=ledger)
        except ValueError as e:
            # The whole message, to stdout, at exit 1. A refusal that prints nothing, or that
            # is swallowed into a 0, is the same silence the Open WebUI uninstall left behind.
            print('refused: ' + str(e))
            return 1
        print('recorded ' + action + ' ' + target + ' -> ' + flow_node.strip())
        return 0

    svcs, systemctl_answered = user_services()

    # NOTHING EXAMINED IS NOT A PASS, and here it is worse than usual.
    #
    # `list-units --state=running` returns no rows when a service has CRASHED, so a dead
    # bridge produced "no running user service runs code from this repository" and exit 0 --
    # this check reporting success precisely when the thing it watches is gone. A systemctl
    # that could not answer at all (no user bus, run from cron) was indistinguishable from a
    # clean result for the same reason.
    #
    # Fourth vacuous pass found on 2026-08-27 (L-136, L-137, audit_compliance), and the one
    # with real teeth, because "no services" is a FAILURE STATE for this repository -- the
    # MCP bridge and the app services are meant to be running with linger.
    if not systemctl_answered:
        print("  systemctl --user could not be queried, so NOTHING was checked.")
        print("  That is not a pass: a stale service would look identical from here.")
        return 2
    if not svcs:
        print("  No running user service runs code from this repository.")
        print("  NOTHING was examined, so this is not a pass. `--state=running` also")
        print("  returns nothing when a service has CRASHED, which is the case this")
        print("  check would most need to report. Confirm with:")
        print("    systemctl --user list-units --type=service --all | grep -E "
              "'workspace-mcp|cloudflared|app-'")
        return 2
    stale = []
    for name, script in svcs:
        up = started_at(name)
        changed, who = newest_change(script)
        if up is None:
            print(f"  [?]    {name}: cannot read its start time")
            continue
        if changed < up:
            mins = int((up - changed) / 60)
            stale.append((name, who, mins))
            print(f"  [STALE] {name}\n"
                  f"          started {int(up/60)} min ago; {who.name} changed "
                  f"{int(changed/60)} min ago\n"
                  f"          fix: systemctl --user restart {name.removesuffix('.service')}")
        else:
            print(f"  [ok]   {name} (started after the last change to {script.parent.name}/)")
    strays = stray_repo_processes()
    if strays:
        print("\n  STRAY PROCESSES running this repo's code, started before it last changed:")
        for pid, up_h, name, chg_h in strays:
            print(f"  [STALE] pid {pid}: {name} — up {up_h}h, the file changed {chg_h}h ago")
        print("  These are not systemd units, so the service check above cannot see them.\n"
              "  An orphaned context_watch.py ran for FOUR DAYS this way, executing code that\n"
              "  had since been retired. Confirm what they are, then: kill <pid>")

    selfwait = self_waiting_processes()
    if selfwait:
        print("\n  PROCESSES WAITING FOR THEMSELVES -- these will never finish:")
        for pid, mins, pat in selfwait:
            print(f"  [STUCK] pid {pid}: `pgrep -f {pat}` also matches its OWN command line, "
                  f"so the loop cannot exit. Waiting {mins} min.")
        print("  The job it was waiting for finished long ago. Wait on a PID instead:\n"
              "    P=$(pgrep -f 'thing\\.py$' | head -1); while kill -0 $P 2>/dev/null; "
              "do sleep 30; done\n"
              "  Then: kill <pid>")

    if stale:
        print("\n  A service started before its code changed is serving the old behaviour.\n"
              "  Every other check in this repository reads files and cannot see this.")
    return 1 if (stale or strays or selfwait) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
