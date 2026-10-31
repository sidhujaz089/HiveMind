#!/usr/bin/env python3
"""The session-end checks, run through the day: a failing check becomes work the hour it is found (t1153).

    python3 scripts/hourly_checks.py run          # hourly-checks.timer: check, sweep, file, record
    python3 scripts/hourly_checks.py run --dry    # the same, printed; files and records nothing
    python3 scripts/hourly_checks.py run --dry --no-sweep   # the checks alone

WHY (Jas, 2026-10-13): "Is there a way to do these checks through the day instead of a batch at once?"
and "If the workplace checks catch something, will they just sit there until session end, or can they
create a high priority task for the Manager that gets actioned right after its current job? And a RCA
should be triggered that goes to the top of that queue for the auditor." The NAS backup failed from
10-10 and nobody saw it until a session end three days later.

EACH HOUR:
  1. check_workspace --quick. Every [FAIL] is a finding, named by the check's title.
  2. The test sweep with the per-suite cache (run_tests --cache: unchanged passes AND unchanged
     declared reds are not re-run), two at a time, only with MIN_FREE_GB available -- beside Swift the GX10 has ~20 GB
     of slack and a build peaks near 12. NEW failures are ONE finding, never one per suite. This is the
     ONLY full sweep: session_end starts this unit and does not wait for it (863bb241b).
  3. A finding with no open task: ONE task at the FRONT of the queue (backlog-must-shrink: a finding
     that already has an open task gets nothing new), and ONE RCA request at the front of the RCA
     queue (session_review.pending_findings sorts check findings first). If no role holds rca, or its
     role is at 0 lanes, the task says the RCA waits.
  4. A finding that was red and is green now: its task gets "check green at <time>, <commit>", once.
Nothing is interrupted: the task is taken after the job the Manager is on. One row per run in
workspace/hourly-checks.jsonl; the red/green state per finding in workspace/run-logs/hourly-checks-state.json.
"""
import hashlib
import json
import re
import subprocess
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "projects" / "app-repo")]
LEDGER = REPO / "workspace" / "hourly-checks.jsonl"
STATE = REPO / "workspace" / "run-logs" / "hourly-checks-state.json"
# HEAVY TEMP WORK ON DISK, WHOEVER STARTS THIS (2026-10-14): /tmp is RAM here. The unit sets TMPDIR,
# but a run from a shell did not, and the sweep's coder sandboxes filled /tmp beside two builds.
ENV = {**__import__("os").environ, "TMPDIR": str(Path.home() / ".cache" / "hivemind-tmp")}
MIN_FREE_GB = 16                   # one build (~12 GB peak) + the guard's 2 GB reserve + the sweep itself
SWEEP = "test sweep: new failures"
# HOW LONG A CLEAN SWEEP OF THIS EXACT TREE COUNTS AS RECENT (t1885). run_tests.py --assert-fresh
# existed and NOTHING called it: a sweep skipped, killed or silently failed left no finding, and
# silence reads as health. Same window the pre-push hook uses, so the two agree on "recent".
FRESH_MIN = 90


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


def stable(name):
    """The check's name without a trailing count, "(422 of 4472 disagree)": the counts change every run,
    and keyed on them each run filed a new task for the same finding (6 of them, t1460-t1486)."""
    return re.sub(r"\s*\([^()]*\d[^()]*\)\s*$", "", name)


def marker(name):
    return f"<!-- check-finding: {slug(stable(name))} -->"


def fails(text):
    """{title: detail} for every [FAIL] block in check_workspace's output. Pure."""
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"\s*\[(FAIL|PASS|WARN|SKIP)\]\s+(.*\S)", line)
        if m:
            cur = m.group(2) if m.group(1) == "FAIL" else None
            if cur:
                out[cur] = []
        elif cur and line.startswith("         "):
            out[cur].append(line.strip())
        elif cur and line.strip():
            cur = None
    return {k: "\n".join(v) for k, v in out.items()}


def new_failures(text):
    """The suites run_tests named as NEW failures, or []. Pure."""
    m = re.search(r"NEW FAILURES -- these are not in the ceiling: (\[.*?\])", text)
    return re.findall(r"'([^']+)'", m.group(1)) if m else []


def run_ceiling_sweep():
    """Keep the failure ceiling honest and its count true BEFORE the check that reads them.

    With production on, the list and its promised number drift every few minutes: a suite that goes
    green sits on the list warning about nothing, and a suite production declares failing grows the list
    past the committed count (which the "may not rise" check then reads as growth). Both are bookkeeping,
    and both cost a session minutes every hour they are left. Jas, 2026-10-27: "Yes make the sweep good
    idea" -- so it is a step here, and the script commits its own two files (ceiling_maintain.py).

    It NEVER adds a suite and never guesses a suite with no verdict, so a failure here cannot invent
    coverage. A refusal is reported, not retried: the next hour will see it again.
    """
    try:
        r = subprocess.run(["nice", "-n", "15", sys.executable, "scripts/ceiling_maintain.py"],
                           cwd=REPO, env=ENV, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return {"ceiling sweep did not finish": "timeout after 900s -- the ceiling was NOT repaired"}
    if r.returncode:
        return {"the ceiling needed a repair it could not make": (r.stdout + r.stderr).strip()[-600:]}
    return {}


def run_check():
    r = subprocess.run(["nice", "-n", "15", sys.executable, "scripts/check_workspace.py", "--quick"],
                       cwd=REPO, env=ENV, capture_output=True, text=True, timeout=1800)
    # A RUN THAT DID NOT FINISH IS NOT A GREEN ONE: only its closing tally says every check ran.
    done = re.search(r"\d+ passed, \d+ warning", r.stdout) is not None
    found = fails(r.stdout)
    if not done:
        found["check_workspace did not finish"] = (f"exit {r.returncode}; last lines:\n"
                                                   + "\n".join((r.stdout + r.stderr).strip().splitlines()[-8:]))
    return found, done


def assert_fresh(minutes=None):
    """(True, why) if a COMPLETE clean sweep of this exact tree ran within `minutes`.

    Asked of run_tests.py itself, which owns the definition (tree content hash + a completion row
    whose ok is true). Nothing is run by asking."""
    minutes = FRESH_MIN if minutes is None else minutes
    r = subprocess.run([sys.executable, "scripts/run_tests.py", "--assert-fresh", str(minutes)],
                       cwd=REPO, env=ENV, capture_output=True, text=True, timeout=600)
    line = "\n".join((r.stdout + r.stderr).strip().splitlines()[-1:])
    return r.returncode == 0, line


TMP_REAP_AGE_S = int(os.environ.get("HIVEMIND_TMP_REAP_AGE_S", "3600"))
# Where suites and jobs leave repo copies: /tmp (RAM) and the on-disk temp root they were moved to (t9566).
TMP_REAP_ROOTS = ("/tmp", str(Path.home() / ".cache" / "hivemind-tmp"))


def run_tmp_reaper(root="/tmp", max_age_s=None, ledger=None):
    """F3: remove ABANDONED REPO COPIES from the RAM-backed temp dir, every hour, without being asked.

    MEASURED 2026-10-29: /tmp on this box is tmpfs (61 GB, 23 GB used) and nine copies of this repository --
    1.6 GB each, 15 GB total, all made the same afternoon -- were sitting in it. `MemAvailable` was 20.9 GB
    against a build admission of 12 + 4 GB, so the pressure gate held every build while the machine's own test
    runs owned the memory.

    `sweep.sweep_tmp_repo_copies()` already existed for exactly this and says so in its own docstring, with the
    2026-10-18 incident in it. It was called from ONE place -- `sweep.py:648`, behind a CLI flag -- and nothing
    scheduled it, which is worse than no reaper: the comment reads as coverage. This runs it on the clock, with
    the horizon set just past the longest a suite may legitimately run (its own cap is 1200 s).
    """
    try:
        import sweep
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        return {"reaped": 0, "error": f"sweep unreadable: {type(e).__name__}: {e}"}
    try:
        rows = sweep.sweep_tmp_repo_copies(root, max_age_s=max_age_s or TMP_REAP_AGE_S, ledger=ledger)
    except Exception as e:                                          # noqa: BLE001
        return {"reaped": 0, "error": f"{type(e).__name__}: {e}"}
    rows = rows or []
    freed = sum(int(r.get("size_bytes") or 0) for r in rows)
    return {"reaped": len(rows), "freed_gb": round(freed / 2**30, 1), "rows": rows[:5]}


def run_worktree_reaper():
    """Remove merged worktrees under ~/.cache/hivemind-tmp that hold nobody's work (t9566, sweep.sweep_tmp_worktrees).

    206 had accumulated, 146 on merged branches, ~150 GB, because nothing scheduled this. Its own step in main(),
    not inside run_sweep: a sweep skipped as fresh must start nothing, and this runs git."""
    try:
        import sweep as _sw
        _wt = _sw.sweep_tmp_worktrees(str(Path.home() / ".cache" / "hivemind-tmp"), max_age_s=TMP_REAP_AGE_S)
        if _wt:
            print(f"  worktree reaper: removed {len(_wt)} merged/clean worktree(s) "
                  f"({round(sum(int(r.get('size_bytes') or 0) for r in _wt) / 2**30, 1)} GB back)")
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  worktree reaper: {type(e).__name__}: {e}")


def run_sweep():
    """({SWEEP: detail} or {}, what happened). The ONLY full sweep: session_end starts this run and
    does not wait (863bb241b, Jas 2026-10-19), so nothing reads a "passed for this code" record.

    IT ASKS BEFORE IT RUNS (t1885). A tree that already has a complete clean sweep inside FRESH_MIN
    does not need another: the answer is already true, and the second run would only contend with
    the build or the session_end sweep that produced it. This is also what run_tests.py
    --assert-fresh is for -- it was written for the pre-push hook and nothing else ever called it.

    AND IT ASKS AFTER (t1885): a sweep that returned 0 but left no fresh record of this tree is a
    finding, not a pass. That is the shape of a sweep killed between printing "No new failures" and
    writing its completion row."""
    # FREE WHAT IS FREEABLE BEFORE DECIDING THE BOX IS TOO FULL (2026-10-29). A sweep skipped for want of
    # memory while /tmp holds gigabytes of abandoned repo copies is the box reporting a shortage it is
    # causing. This runs first, so the answer to "is there room?" is about memory that is actually in use.
    # BOTH TEMP ROOTS (t9566, 2026-10-29). Suites were moved OFF /tmp onto ~/.cache/hivemind-tmp the same day, and
    # this reaper still swept /tmp alone -- so the leak moved from RAM to the SSD and nothing reaped it: 122 abandoned
    # copies, 111 GB, the disk at 98% and falling ~11 GB per half hour (DS's measurement, t9566).
    for _root in TMP_REAP_ROOTS:
        reaped = run_tmp_reaper(_root)
        if reaped.get("reaped"):
            print(f"  tmp reaper: removed {reaped['reaped']} abandoned repo copy/copies under {_root} "
                  f"({reaped.get('freed_gb')} GB back)")
        elif reaped.get("error"):
            print(f"  tmp reaper ({_root}): {reaped['error']}")

    import memory_guard
    avail = memory_guard.available_gb()
    if avail is not None and avail < MIN_FREE_GB:
        return None, f"skipped: {avail:.1f} GB available, under {MIN_FREE_GB}"
    fresh, why = assert_fresh()
    if fresh:
        return None, f"skipped: fresh -- {why}"
    try:
        r = subprocess.run(["nice", "-n", "15", sys.executable, "scripts/run_tests.py", "--cache", "--jobs", "2"],
                           cwd=REPO, env={**ENV, "SWEEP_BY": "hourly check"}, capture_output=True, text=True,
                           timeout=3 * 3600)
    except subprocess.TimeoutExpired:
        return {SWEEP: "the sweep hit its 3-hour cap and was killed, so it left no verdict. "
                       "Something is wrong with its duration, not with a suite."}, "killed at the 3-hour cap"
    if r.returncode == 3:                       # t1888: another sweep holds the lock
        holder = "\n".join((r.stdout + r.stderr).strip().splitlines()[-4:])
        return None, f"skipped: another sweep is running (run_tests exit 3) -- {holder}"
    new = new_failures(r.stdout)
    if r.returncode == 0:
        fresh2, why2 = assert_fresh()
        if not fresh2:
            return {SWEEP: "the sweep returned 0 but left no fresh record of this tree: " + why2}, \
                   "rc 0 but not fresh"
        return {}, "passed"
    if new:
        return {SWEEP: "New failing suites (not in the ceiling): " + ", ".join(new)
                + "\nRun: python3 scripts/run_tests.py -k <suite>"}, f"{len(new)} new failure(s)"
    tail = "\n".join(r.stdout.strip().splitlines()[-6:])
    return None, f"rc {r.returncode} with no new failure named -- not a finding:\n{tail}"


def names_in_detail(detail):
    """The suite names out of the sweep's detail line, or []. Pure."""
    m = re.search(r"New failing suites \(not in the ceiling\): (.*)", detail or "")
    return [n.strip() for n in m.group(1).split(",") if n.strip()] if m else []


def sweep_record(found, says):
    """The ledger fields for the sweep step -- the NAMES INCLUDED (t1885).

    The rows used to say "sweep: 7 new failure(s)" with "filed: []" and nothing else: the names had
    been computed, used to build a task body, and thrown away because a task for that check already
    existed. A finding whose content is discarded is one nobody can act on."""
    detail = (found or {}).get(SWEEP, "")
    return {"sweep": (says or "").split("\n")[0], "sweep_suites": names_in_detail(detail),
            "sweep_detail": detail}


def rca_waits():
    """Why an RCA request would wait, or '' when a role with lanes holds rca."""
    import task_routing
    d = task_routing.load()
    role = task_routing.owner(d, "rca")
    if not role:
        return "no role holds the rca task, so the RCA request waits until one does"
    if task_routing.role_lanes(d, role) == 0:
        return f"the role holding rca ({d['roles'][role].get('name', role)}) is at 0 lanes, so the RCA waits"
    return ""


def file_finding(name, detail, head, ts):
    """ONE task at the front of the queue + ONE RCA request at the front of its queue. -> task id."""
    import session_review
    import taskstore
    waits = rca_waits()
    body = (f"The hourly check found this at {ts} on {head} (scripts/hourly_checks.py, t1153).\n\n"
            f"CHECK: {name}\n\n```\n{detail or '(no detail printed)'}\n```\n\n"
            f"Fix the cause, then the next hourly run notes this task when the check is green.\n"
            + (f"\nRCA: {waits}.\n" if waits else "\nRCA: requested at the front of the RCA queue.\n")
            + f"\n{marker(name)}\n")
    tid = taskstore.add(f"Check failing: {name}"[:90], area="workspace", body=body,
                        source="hourly-checks", at_top=True, kind="build")
    key = "check-" + hashlib.sha256(f"{name}\n{ts}".encode()).hexdigest()[:12]
    session_review.FINDINGS.mkdir(parents=True, exist_ok=True)
    (session_review.FINDINGS / f"{key}.json").write_text(json.dumps({
        "kind": "check-failure", "quote": f"[FAIL] {name}", "context": detail, "where": "hourly check",
        "why_missed": f"Filed as {tid} by hourly_checks.py; name {tid} in covered_by for the fix itself.",
        "covered_by": tid, "disposition": "deferred", "key": key, "request": "hourly-checks",
        "session": "", "harness": "hourly-checks", "found": ts}, indent=1) + "\n", encoding="utf-8")
    return tid


def reconcile(found, ran, state, head, ts, open_task, file, note):
    """Pure decision + side effects through the callables. found: {name: detail} failing now; ran: the
    names whose checks actually ran (a check that did not run is neither red nor green). -> new state."""
    state = {stable(k): v for k, v in state.items()}
    full = dict(found)
    found = {stable(k): k for k in full}
    ran = {stable(k) for k in ran}
    for name, orig in found.items():
        detail = full[orig]
        prev = state.get(name) or {}
        digest = hashlib.sha256((detail or "").encode("utf-8")).hexdigest()[:12]
        tid = open_task(marker(name))
        if not tid:
            tid = file(orig, detail, head, ts)
        elif prev.get("detail_sha") != digest:
            # THE NAMES ARE NOT DISCARDED (t1885). They used to be: a check already red kept its
            # task, so the fresh detail was computed, built into a body, and thrown away. Appended
            # only when the content CHANGED, so an hourly run over the same finding stays quiet.
            note(tid, f"Still failing at {ts}, {head}. What it says now:\n{detail}")
        state[name] = {"red": True, "task": tid, "since": prev.get("since") or ts,
                       "detail_sha": digest}
    for name, s in list(state.items()):
        if name in found or not s.get("red") or name not in ran:
            continue
        if s.get("task"):
            note(s["task"], f"Check green at {ts}, {head} -- '{name}' passes in the hourly run. "
                            f"The done-check can close this on that evidence.")
        state[name] = {**s, "red": False, "green_at": ts}
    return state


def main(argv):
    if argv[:1] != ["run"]:
        print(__doc__)
        return 2
    dry = "--dry" in argv
    import scheduler_commands
    import session_review
    import taskstore
    ts = _now()
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, timeout=60,
                          text=True).stdout.strip()
    if scheduler_commands.is_paused():
        print("hourly-checks: the scheduler is paused; nothing run")
        row = {"at": ts, "head": head, "skipped": "scheduler paused"}
    else:
        run_worktree_reaper()                # disk first: 300 GB of worktrees filled the SSD (t9566)
        swept = run_ceiling_sweep()          # before the check that reads the ceiling and its count
        found, finished = run_check()
        found.update(swept)
        ran = set(found)
        try:
            prev = json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prev = {}
        if finished:                                     # every check ran: an earlier red not failing now is green
            ran |= {n for n in prev if n != SWEEP}
        sweep, sweep_says = (None, "skipped: --no-sweep") if "--no-sweep" in argv else run_sweep()
        if sweep is not None:
            found.update(sweep)
            ran.add(SWEEP)
        print(f"hourly-checks {ts} {head}: {len(found)} failing; sweep {sweep_says}")
        for n in found:
            print(f"  FAIL  {n}")
        if dry:
            return 0
        filed = []
        state = reconcile(found, ran, prev, head, ts, session_review._open_group_task,
                          lambda *a: filed.append(file_finding(*a)) or filed[-1],
                          lambda tid, text: taskstore.add_note(tid, text, who="hourly-checks"))
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")
        row = {"at": ts, "head": head, "failing": sorted(found), "filed": filed,
               **sweep_record(sweep, sweep_says)}
    if not dry:
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
