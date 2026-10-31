#!/usr/bin/env python3
"""What is happening right now, derived, with its disagreements shown.

    python3 scripts/agent_status.py              # the board
    python3 scripts/agent_status.py --json       # same, for the console to render
    python3 scripts/agent_status.py --self-test  # prove it can report a disagreement

WHY THIS FILE IS SHAPED THE WAY IT IS
-------------------------------------
Jas asked for a console where one agent can check whether another is still working. That is
the requirement most likely to LIE, and this repository has a precedent for every way it can:
a service `active` while an orphan answered on its port; a residency gate that summed
processes by name so a 0.5B model cleared a floor on a 35B model's memory (`L-113`); a check
that reported a clean tree for a file it could not read (`L-142`); an 87-hour probe whose main
result was that whole class. Every one has the same shape: **one source, and an ambiguous
signal read as a definite one.**

So four rules, and they are the design:

1. **DERIVE ON READ. STORE NOTHING.** No "currently running" flag that somebody has to
   remember to clear. Every answer below is computed when you ask for it.

2. **TWO INDEPENDENT SOURCES PER QUESTION, AND REPORT DISAGREEMENT RATHER THAN RESOLVING IT.**
   This is the part that is new. When `ps` and the sandbox directories disagree about whether
   a run is in flight, that disagreement IS the answer -- it means a run died and left its
   sandbox behind, which is how /tmp reached 55 GB. Today every surface here would silently
   pick one source and print a confident number.

3. **EVERY CELL CARRIES ITS SOURCE AND ITS FRESHNESS.** "2 running (ps, 0.1s ago)" is a
   different claim from "2 running", and only the first can be checked.

4. **"CANNOT TELL" IS A LEGITIMATE ANSWER**, and the only honest one when a source is
   unreachable. An agent will act on this; a confident wrong answer is worse than an absent
   one, which is `G8` exactly.

And the rule that governs all four: `--self-test` fabricates a disagreement and asserts this
file reports it. A status pane that has only ever agreed with itself cannot be told from one
that cannot disagree (`docs/CHECKS.md` rule 1).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "workspace" / "agent-runs.jsonl"

UNKNOWN = "cannot tell"


def _sh(*args, timeout=10):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


class Cell:
    """One answer, with where it came from and how old it is. Never a bare number."""

    def __init__(self, value, source, seconds, note=""):
        self.value, self.source, self.seconds, self.note = value, source, seconds, note

    @property
    def known(self):
        return self.value is not UNKNOWN

    def as_dict(self):
        return {"value": self.value, "source": self.source,
                "age_seconds": round(self.seconds, 2), "note": self.note}


def _console_role():
    """The role that owns the console, from the role store; "Console" when the store cannot say."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import task_routing
        owner = task_routing.owner(task_routing.load(), "console")
        return (owner or "Console").capitalize()
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: console role unknown ({type(e).__name__}: {e}); labelled 'Console'", file=sys.stderr)
        return "Console"


def _timed(fn, source):
    t0 = time.time()
    try:
        v, note = fn()
    except Exception as e:
        return Cell(UNKNOWN, source, time.time() - t0, f"{type(e).__name__}: {e}")
    return Cell(v, source, time.time() - t0, note)


def _proc_processes():
    """Live processes as (pid, cmdline, elapsed_seconds), read from /proc.

    /proc rather than ps: without a terminal ps cuts its columns at 80, so a live
    dispatch with a long command line came back as no match at all. /proc has no
    width, and every field is a file read whole.
    """
    ticks = os.sysconf("SC_CLK_TCK")
    btime = None
    for line in Path("/proc/stat").read_text().splitlines():
        if line.startswith("btime "):
            btime = int(line.split()[1])
            break
    if btime is None:
        raise RuntimeError("could not read btime from /proc/stat")
    procs = []
    for entry in sorted(Path("/proc").iterdir()):
        if not entry.name.isdigit():
            continue
        raw_cmd = _read_optional(entry / "cmdline")
        raw_stat = _read_optional(entry / "stat")
        if raw_cmd is None or raw_stat is None:
            continue
        argv = [a.decode("utf-8", "replace") for a in raw_cmd.split(b"\0") if a]
        # field 22 is starttime, ticks since boot; comm (field 2) may contain spaces,
        # so the fixed fields begin after the first ')'
        tail = raw_stat.split(b")", 1)[1].split()
        started = int(tail[19]) // ticks
        procs.append((int(entry.name), " ".join(argv),
                      max(0, int(time.time()) - (btime + started))))
    return procs


def _read_optional(path):
    """Bytes, or None if the process is gone by the time we get to it."""
    try:
        return path.read_bytes()
    except OSError:
        return None


# --------------------------------------------------------------- the two-source questions

def runs_in_flight(tmp=None):
    """ps says one thing; the sandbox directories say another. Both, and any disagreement.

    coder.py builds /tmp/coder-<ts>-<pid> per run and removes nothing on a hard kill. So a
    sandbox with no live process is a run that DIED, and that is the state worth seeing: 279
    of them and 55 GB of /tmp on 2026-08-27. A pane reading only `ps` would call that idle.

    tmp scopes the sandbox census to one directory, which is how --self-test can plant a
    disagreement in a fixture it owns (t731). Called with no argument it reads the real
    temp dir, and every home it has today, exactly as before.
    """
    def by_proc():
        pids = [(pid, et) for pid, cmd, et in _proc_processes()
                if "coder.py" in cmd and "grep" not in cmd]
        return pids, f"{len(pids)} live coder.py process(es)"

    def by_sandbox():
        boxes = []
        # Both homes: a job the scheduler starts writes under ~/.cache/hivemind-tmp (TMPDIR, 2026-10-13);
        # one run by hand still uses this process's temp dir. A caller-supplied tmp replaces them.
        homes = ({Path(tmp)} if tmp is not None
                 else {Path(tempfile.gettempdir()), Path.home() / ".cache" / "hivemind-tmp"})
        for d in (x for h in homes for x in h.glob("coder-*")):   # where coder.py puts them (t415)
            if d.is_dir():
                m = re.match(r"coder-\d+-(\d+)$", d.name)
                boxes.append((int(m.group(1)) if m else None, d.name))
        return boxes, f"{len(boxes)} sandbox director(ies) under {', '.join(sorted(str(h) for h in homes))}"

    ps = _timed(by_proc, "/proc")
    sb = _timed(by_sandbox, "/tmp/coder-*")
    if not (ps.known and sb.known):
        return {"ps": ps, "sandbox": sb, "verdict": Cell(UNKNOWN, "both", 0,
                "a source was unreachable, so no count is claimed")}

    live = {p for p, _ in ps.value}
    orphaned = [name for pid, name in sb.value if pid is not None and pid not in live]
    if orphaned:
        v = (f"DISAGREEMENT: {len(ps.value)} process(es) running but "
             f"{len(orphaned)} sandbox(es) belong to no live process")
        note = ("a run died and left its sandbox. Reported, not resolved: "
                + ", ".join(sorted(orphaned)[:4]))
    else:
        v = f"{len(ps.value)} run(s) in flight"
        note = "ps and the sandbox directories agree"
    return {"ps": ps, "sandbox": sb,
            "verdict": Cell(v, "ps + /tmp/coder-*", ps.seconds + sb.seconds, note)}


def awaiting_review():
    """The ledger says a branch was delivered; git says whether it is there. Both.

    Two disagreements are real and both were seen today. A branch named in the ledger that
    does not exist means work was lost. A `coder/*` branch that NO ledger row names is a run
    that committed and had not yet recorded -- the window that made finding 41 destructive.
    """
    def by_ledger():
        sys.path.insert(0, str(REPO / "scripts"))
        import reviewq
        rows = reviewq.pending()
        return [r.get("branch") for r in rows if r.get("branch")], f"{len(rows)} pending row(s)"

    def by_git():
        rc, out = _sh("git", "-C", str(REPO), "branch", "--list", "coder/*",
                      "--format=%(refname:short)")
        if rc is None:
            raise RuntimeError(out)
        return [b for b in out.split() if b], "git branch --list coder/*"

    led = _timed(by_ledger, "reviewq/agent-runs.jsonl")
    gitb = _timed(by_git, "git")
    if not (led.known and gitb.known):
        return {"ledger": led, "git": gitb,
                "verdict": Cell(UNKNOWN, "both", 0, "a source was unreachable")}

    missing = [b for b in led.value if b not in gitb.value]
    unrecorded = [b for b in gitb.value if b not in led.value]
    if missing or unrecorded:
        parts = []
        if missing:
            parts.append(f"{len(missing)} named in the ledger but NOT in git ({', '.join(missing[:3])})")
        if unrecorded:
            parts.append(f"{len(unrecorded)} branch(es) no pending row names ({', '.join(unrecorded[:3])})")
        v = "DISAGREEMENT: " + "; ".join(parts)
        note = ("a ledger branch that is gone means work was lost; a branch nothing names is "
                "either merged already or a run that has not finished recording")
    else:
        v = f"{len(led.value)} branch(es) waiting"
        note = "the ledger and git agree"
    return {"ledger": led, "git": gitb,
            "verdict": Cell(v, "reviewq + git", led.seconds + gitb.seconds, note)}


def services_current():
    """systemd knows about units; ps knows about everything else. L-143 is the gap."""
    def by_check():
        rc, out = _sh(sys.executable, str(REPO / "scripts" / "check_stale_services.py"),
                      timeout=120)
        if rc is None:
            raise RuntimeError(out)
        stale = [l.strip() for l in out.splitlines() if "[STALE]" in l]
        return (f"{len(stale)} stale" if stale else "all current"), \
               ("; ".join(stale[:3]) if stale else "check_stale_services reports none")

    def by_proc():
        n = sum(1 for _pid, cmd, _et in _proc_processes()
                if str(REPO) in cmd and "grep" not in cmd and "agent_status" not in cmd)
        return n, f"{n} process(es) running this repo's code"

    return {"check": _timed(by_check, "check_stale_services.py"),
            "ps": _timed(by_proc, "/proc")}


def waiting_for_claude():
    """Escalations sitting in the inbox that no Claude session has swept.

    NOTHING NOTIFIES ANYONE, and that is not a bug to fix here -- a cron that woke a Claude
    session to poll would spend the one resource this whole design exists to conserve. What
    it can do is stop the wait being INVISIBLE: Jas asked "will you activate right away?",
    and the honest answer is no, so the least this can do is show him there is a reason to
    start a session.

    Counted from the directory, not from a marker file. A swept message is deleted, so the
    absence of a file is the record and there is no state to go stale.
    """
    def by_dir():
        d = REPO / "workspace" / "inbox"
        if not d.is_dir():
            return 0, "no inbox directory"
        msgs = [f for f in d.rglob("*.md") if f.name != "README.md"]
        if not msgs:
            return 0, "nothing waiting"
        # THE LABEL IS THE ROLE THAT OWNS THE CONSOLE, not the model that happened to serve it (Jas,
        # 2026-10-29: "Q16 is a model we no longer use. That is another hard coded role that needs fixing").
        # This said "Qwen" -- a model retired months ago -- so every console message in the Hub was labelled
        # with a name nothing on the box answers to.
        who = sorted({(_console_role() if "--console--" in f.name else
                       "Chat" if "--chat--" in f.name else "?") for f in msgs})
        return len(msgs), f"from {', '.join(who)} — a session sweeps these at start"
    return _timed(by_dir, "workspace/inbox/")


def last_runs(n=3):
    def recent():
        if not RUNS.is_file():
            return [], "no ledger yet"
        rows = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
        out = [f"{r.get('at','?')[11:19]} {r.get('outcome')}" for r in rows[-n:]]
        age = time.time() - RUNS.stat().st_mtime
        return out, f"{len(rows)} row(s); last written {int(age)}s ago"
    return _timed(recent, "agent-runs.jsonl")


def board():
    return {"in_flight": runs_in_flight(), "review": awaiting_review(),
            "services": services_current(), "recent": last_runs(),
            "inbox": waiting_for_claude()}


def render(b):
    def line(label, cell):
        mark = "??" if not cell.known else ("!!" if "DISAGREEMENT" in str(cell.value) else "  ")
        print(f"  {mark} {label:<14} {cell.value}")
        print(f"       {'':<14} ({cell.source}, {cell.seconds:.2f}s) {cell.note}")

    print("AGENT STATUS — derived on read, nothing stored\n")
    line("in flight", b["in_flight"]["verdict"])
    line("review", b["review"]["verdict"])
    line("services", b["services"]["check"])
    line("for Claude", b["inbox"])
    line("recent", b["recent"])
    print("\n  '??' is cannot tell. '!!' is two sources disagreeing, which is reported\n"
          "  rather than resolved -- picking one silently is how a status surface lies.")


def self_test():
    """Fabricate a disagreement in a fixture the test OWNS and assert it is REPORTED.

    The fixture is its own temp dir, counted only through runs_in_flight(tmp=fixture), so a
    coder-* sandbox left by a live or killed coder.py run can neither fail this self-test nor
    let it pass by its absence (t731, workspace/rca/bef22a9c14fc.md). What it does to the
    shared temp dir is reported as a count, never judged.
    """
    fails = []
    fixture = Path(tempfile.mkdtemp(prefix="agent_status-selftest-", dir=tempfile.gettempdir()))
    box = fixture / f"coder-{int(time.time())}-999999"        # a pid that cannot be running
    box.mkdir(parents=True)
    try:
        v = runs_in_flight(tmp=fixture)["verdict"]
        ok = "DISAGREEMENT" in str(v.value) and box.name in str(v.note)
        print(f"  [{'ok' if ok else 'FAIL'}]   an orphaned sandbox is reported: {v.value} \u2014 {v.note}")
        if not ok:
            fails.append("an orphaned sandbox did not produce a disagreement naming it")
    finally:
        shutil.rmtree(fixture, ignore_errors=True)

    v = runs_in_flight(tmp=fixture)["verdict"]
    listed = f"{v.value} {v.note}"
    ok = box.name not in listed and not ("DISAGREEMENT" in str(v.value) and box.name in str(v.note))
    print(f"  [{'ok' if ok else 'FAIL'}]     and removing it clears the disagreement: {v.value}")
    if not ok:
        fails.append("the disagreement survived the cause being removed")

    c = Cell(UNKNOWN, "x", 0, "")
    print(f"  [{'ok' if not c.known else 'FAIL'}]   'cannot tell' is not treated as known")
    if c.known:
        fails.append("UNKNOWN registered as a known value")

    tmp = Path(tempfile.gettempdir())
    ambient = sorted(d.name for d in tmp.glob("coder-*") if d.is_dir())
    cut = f", +{len(ambient) - 4} more not named" if len(ambient) > 4 else ""
    print(f"\n  ambient coder-* sandbox(es) in {tmp}: {len(ambient)} "
          f"({', '.join(ambient[:4])}{cut})")

    print(f"\nagent_status self-test: {'ok' if not fails else 'FAILED'}")
    for f in fails:
        print(f"  - {f}")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    b = board()
    if a.json:
        out = {k: ({kk: vv.as_dict() for kk, vv in v.items()} if isinstance(v, dict)
                   else v.as_dict()) for k, v in b.items()}
        print(json.dumps(out, indent=1))
        return 0
    render(b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
