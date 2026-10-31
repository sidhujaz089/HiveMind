#!/usr/bin/env python3
"""Run every suite in scripts/tests/, and refuse to let the failing count grow.

    python3 scripts/run_tests.py            run them all, honour the ceiling
    python3 scripts/run_tests.py --ratchet  lower the ceiling to what passes today
    python3 scripts/run_tests.py -k sampling   run only suites matching a substring

    exit 3 means another sweep holds workspace/run-logs/sweep.lock (t1888): nothing ran.

WHY THIS EXISTS, AND IT IS THE WORST GAP FOUND ALL DAY
------------------------------------------------------
Until 2026-09-03 NOTHING RAN THESE. Not the pre-push hook, not session_end.py, not
check_workspace.py. Forty-four suites sat on disk, three of them failing, and the failures had
gone unnoticed for long enough that nobody knew which. `check_workspace.py` READS them -- it
checks that each proves a failure and not only a pass -- which reads exactly like coverage
while nothing was ever executed.

That is this repository's own recurring defect, applied to its own tests: a mechanism that
exists and is never invoked. Six new guards were written that same day into the same directory,
and every one of them would have been decorative.

THEY ARE NOT pytest SUITES. Each is a standalone script with a main() returning non-zero on
failure. `python3 -m pytest scripts/tests` fails with "No module named pytest", which is not a
broken environment -- pytest has never been installed here -- and a session read that as a
missing dependency rather than the wrong runner.

THE RATCHET, FOR THE SAME REASON AS check_enforcement.py
--------------------------------------------------------
Three suites fail today for reasons that predate this runner. Making the whole thing red on
day one would produce a check people learn to skip (AGENTS.md rule 9), and a skipped check is
worth less than none. So `workspace/test-failures-ceiling.json` names exactly which suites are
allowed to fail. A NEW failure fails the run immediately. Fixing one and running --ratchet
removes it from the list, and it can never be re-added by accident.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "scripts" / "tests"
CEILING = REPO / "workspace" / "test-failures-ceiling.json"
KNOWN_LOGS = REPO / "workspace" / "run-logs" / "known-failures"
# The suite-state ledger: one row per suite per run, bound to a commit and an owner. The
# sweep journal says a sweep happened; this says WHICH suite is red on WHICH tree and who has
# to fix it -- the record whose absence made "fails on main too" unaddressable in two RCAs.
SUITE_STATE = REPO / "workspace" / "suite-state.jsonl"
SUITE_OWNERS = REPO / "workspace" / "suite-owners.json"
TASK_DIR = REPO / "workspace" / "tasks"   # OPEN tasks: done/ lives under it and does not count
# ONE SWEEP AT A TIME, ACROSS PROCESSES (t1888, 2026-10-25). Measured: two `--cache --jobs 2` runs
# alive at once, one at 3493 s, because _PLOCK below only orders THIS process's worker threads and
# nothing ordered two processes. Two sweeps plus a build is the 2026-10-24 20:36 configuration that
# took the box to 1.42 GB free and made the memory guard stop Swift mid-conversation.
# An flock, not a pid file: the kernel drops it when the process dies, so a sweep killed with -9
# leaves nothing stale to clear, and there is no window between reading a pid and trusting it.
SWEEP_LOCK = REPO / "workspace" / "run-logs" / "sweep.lock"
SWEEP_LOCK_BUSY = 3                     # exit code of a caller that refused to start; nothing ran
_LOCK_HANDLE = None                     # kept for the life of the run: closing it releases
TIMEOUT = 300
# ONE SUITE GETS MORE, BY MEASUREMENT (t286, 2026-09-25). test_coder_branch_not_clobbered
# drives the real coder.py TWICE, and each run baselines its sandbox: 392.6 s alone, every
# check ok, after the probe fix that removed the wait behind Q4. That is the suite's real
# work, not a hang -- so it gets its own cap rather than a place in the failure ceiling.
# Every other suite keeps 300 s. Raise a number here only with a timing behind it.
# 2026-10-01, measured solo on the day all three went green: branch_delivery 966 s,
# delivers_what_it_verified 448 s, green_count 443 s. Each drives the real coder.py with a full
# sandbox baseline; at 300 s they were "failing" by being cut off. Caps are ~2-3x that: under a full sweep the models are running too, and delivers_what_it_verified was cut at 600 s there.
TIMEOUT_FOR = {"test_coder_branch_not_clobbered.py": 900,
               "test_coder_branch_delivery.py": 1800,
               "test_coder_delivers_what_it_verified.py": 1200,
               "test_coder_green_count.py": 1800,
               # 2026-10-03: a second full coder run (the dead-endpoint case) took this to ~280 s
               # alone; under model load it hit 600 s in two session-end sweeps. green_count takes
               # ~540 s alone and >1200 s under load. Both pass alone.
               "test_coder_endpoint_drop.py": 1200}

# PROGRESS IS PRINTED AS EACH SUITE LANDS, AND THAT IS NOT COSMETIC (2026-09-16).
# Until today this runner printed its header and then NOTHING until every suite had
# finished, because the report loop runs after the executor drains. Four sweeps were
# killed mid-run on 2026-09-15 and each left a one-line log -- the header -- from which
# the session concluded "it dies at the same point every time, the first sandbox suite".
# That conclusion was unsupported: a one-line log is what a kill at suite 3 and a kill at
# suite 130 both look like. A runner that cannot say how far it got turns every kill into
# the same non-evidence, so the progress line goes to stderr, unbuffered, one per suite.
_DONE = 0
_TOTAL = 0
_TREE = None
_PLOCK = Lock()


class SweepBusy(RuntimeError):
    """Another process holds the sweep lock. `str()` is the holder's own recorded words."""


def sweep_lock_path():
    """Where the lock lives. SWEEP_LOCK_FILE overrides it for a fixture or a second checkout, in
    the same shape as SUITE_OWNERS: a path read at CALL time, so a test can point it at a temp dir
    and never touch the live lock."""
    return Path(os.environ.get("SWEEP_LOCK_FILE") or SWEEP_LOCK)


def acquire_sweep_lock(label=None, path=None):
    """Hold the one cross-process sweep lock and return its handle.

    THE CALLER MUST KEEP THE HANDLE. Closing it releases the lock, so a caller that drops it has
    no lock at all; the runner keeps it in _LOCK_HANDLE for the life of the process.

    Raises SweepBusy, whose message is what the holder wrote, rather than blocking: a sweep that
    waited an hour for another sweep would be the same pileup one layer down."""
    import fcntl
    path = Path(path or sweep_lock_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.seek(0)
        holder = fh.read().strip() or "(the holder recorded nothing)"
        fh.close()
        raise SweepBusy(holder)
    fh.seek(0)
    fh.truncate()
    fh.write(json.dumps({"pid": os.getpid(),
                         "by": label or os.environ.get("SWEEP_BY") or "by hand",
                         "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "argv": " ".join(sys.argv)}) + "\n")
    fh.flush()
    return fh


def release_sweep_lock(handle):
    """Release it. The handle must not be used afterwards -- closing IS the release."""
    if handle is not None:
        handle.close()


def _head_commit():
    """Which tree this verdict belongs to: git's SHA when git answers, the sweep's tree id when
    it does not. Never raises and never returns an empty label -- _tick calls this on every
    verdict path, and a ledger helper that can throw is a ledger that can break a run.
    """
    try:
        r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=30)
        sha = (r.stdout or "").strip()
        if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha, "git"
    except (OSError, subprocess.SubprocessError):
        # git absent, not a checkout, HEAD mid-write, git itself timed out: the sweep's tree id
        # is the only label a reader can still address, so the row keeps that, and the
        # commit_source says why the SHA is missing.
        return f"tree:{_TREE}", "git-fallback"
    # git answered and the stdout was not a sha -- same fallback, and the source says so.
    return f"tree:{_TREE}", "git-fallback"


def open_task_ids(tasks=None):
    """The ids of tasks that are still OPEN. A closed task is not an owner: it cannot be asked."""
    tasks = Path(tasks or TASK_DIR)
    return {f.name.split("--")[0] for f in tasks.glob("t*.md")}


def ceiling_reasons():
    """Every reason map in the ceiling, merged in file order -- later maps win, which is the shape
    test_ceiling_owned.py reads. One reader for the reason text, so the two cannot drift apart."""
    try:
        c = json.loads(Path(CEILING).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for v in c.values():
        if isinstance(v, dict):
            out.update(v)
    return out


def suite_owner(name, open_ids=None, owners=None, reasons=None):
    """Who answers for a suite: ONE rule for the sweep and the ceiling check (t1886).

    SUITE_OWNERS is read through the module global at CALL time, so a test can repoint it; a path
    bound at import time would make the ledger untestable.

    MEASURED, 2026-10-25: 457 suites are allowed to fail and test_ceiling_owned.py passes for every
    one, but suite-owners.json held 182 entries -- so the sweep printed UNOWNED for 100 red suites
    the check called owned, and each mechanism was describing its own file. Both now answer from
    here: a suite is owned when it names a TASK ID, in suite-owners.json OR in the ceiling's reason,
    and that task is OPEN. An owner that is closed is not an owner -- see open_task_ids().
    """
    open_ids = open_task_ids() if open_ids is None else set(open_ids)
    if owners is None:
        try:
            owners = json.loads(Path(SUITE_OWNERS).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            owners = {}
    if not isinstance(owners, dict):
        owners = {}
    declared = owners.get(name)
    if isinstance(declared, str):
        # The registry writes either a bare id ("t1065") or an id with a label
        # ("t799-console-compliance"); both name a task, and the ledger keeps the string as written.
        m = re.match(r"\s*(t\d+)\b", declared)
        if m and m.group(1) in open_ids:
            return declared.strip()
    if reasons is None:
        reasons = ceiling_reasons()
    m = re.match(r"\s*(t\d+)\b", str(reasons.get(name, "")))
    return m.group(1) if m and m.group(1) in open_ids else ''


def record_suite_state(suite, exit_code, seconds, owner=None, command=None):
    """Append one ledger row for a suite's verdict, said loudly when it has no owner.

    Written at the point the verdict is known, not at the end of the sweep, for the same reason
    _journal writes as it goes: a sweep that is killed still has to leave a record of what it
    found red.
    """
    owner = owner if owner not in (None, '') else suite_owner(suite)
    unowned = bool(exit_code) and not owner
    owner = "UNOWNED" if unowned else (owner or '')
    command = command or ("python3 scripts/tests/" + suite)
    commit, commit_source = _head_commit()
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = {"ts": ts, "suite": suite, "commit": commit, "command": command,
           "exit_code": exit_code, "seconds": seconds, "owner": owner,
           "task_ref": owner, "unowned": unowned, "commit_source": commit_source}
    if unowned:
        print(f"  UNOWNED: {suite} is RED and names no OPEN task -- add {{\"{suite}\": \"tNNNN\"}} to "
              f"{SUITE_OWNERS.name}, or give it a ceiling reason that starts with the task id. "
              f"A red suite with no address is how 'fails on main too' dies unowned.",
              file=sys.stderr, flush=True)
    written = True
    try:
        Path(SUITE_STATE).parent.mkdir(parents=True, exist_ok=True)
        with Path(SUITE_STATE).open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as e:                            # a ledger we cannot write must not fail a run...
        written = False
        print(f"  NOTE: suite-state row NOT written for {suite} to {SUITE_STATE} ({e})",
              file=sys.stderr, flush=True)        # ...but the failure is said, not swallowed
    out = dict(row)
    out["written"] = written
    return out


def _tick(name, rc, secs):
    global _DONE
    with _PLOCK:
        _DONE += 1
        n = _DONE
    mark = "pass" if rc == 0 else "FAIL"
    print(f"  [{n:3}/{_TOTAL}] {mark} {name:44} {secs:6.1f}s", file=sys.stderr, flush=True)
    # WRITTEN AS IT LANDS, not at the end -- the whole point is to survive a kill.
    if _TREE:
        _journal({"kind": "suite", "tree": _TREE, "suite": name, "rc": rc,
                  "secs": secs, "epoch": time.time(),
                  "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    # The ledger records the SUITE, not the sweep, so it is written whether or not a tree id
    # exists: the _TREE guard above is the journal's, not the ledger's, and a verdict recorded
    # only when a tree id happens to exist is a verdict with no address at all.
    record_suite_state(name, rc, secs)


JOURNAL = REPO / "workspace" / "sweep-journal.jsonl"


# ==================================================== reusing a sweep that already ran
# WHY THIS EXISTS (2026-09-16). This runner was STATELESS: every invocation ran all 149
# suites from zero, with no record that any of them had just passed. Three things followed
# from that one fact, and all three were being paid daily.
#
#   1. session_end swept, and then the pre-push hook swept AGAIN, minutes later, over a
#      tree that had not changed. Two full sweeps per close, ~32 minutes.
#   2. THE PUSH COULD NOT SUCCEED. git opens the SSH connection to the remote BEFORE it
#      runs pre-push, so the connection sat idle for the 16 minutes the hook took and
#      GitHub closed it -- "Connection to github.com closed by remote host", then SIGPIPE.
#      Measured: the identical push with the hook skipped completes in 2.2 seconds.
#   3. EVERY KILL LOST EVERYTHING. Four killed sweeps on 2026-09-15 and three on 09-16 each
#      threw away every suite that had already passed.
#
# The identity below is CONTENT, not a timestamp: HEAD, plus the porcelain status, plus the
# sha256 of every changed or untracked file. Same id means the same code, so the same result.
# Anything at all different and the id changes and everything runs again -- the safe
# direction. Only PASSES are ever reused; a failure always runs again. And a reuse says so
# out loud, with the time it was taken, because a check that silently reports someone else's
# result is exactly the vacuous pass this repository keeps finding in itself.
# A RUN'S OWN OUTPUT CANNOT BE PART OF ITS INPUT IDENTITY. Both of these are written BY
# run_tests.py while it runs, so including them meant the tree id changed the moment the
# first suite landed and a resume could never match its own journal. Deliberately only these
# two -- the ones this file itself writes. Every other churning path stays in the identity,
# so ordinary workspace churn simply means no reuse and a full sweep. That is the fail-safe
# direction: the cost of a miss is time, and the cost of a wrong hit is a test result that
# was never taken.
_SELF_WRITTEN = {"workspace/sweep-journal.jsonl", "workspace/flaky-tests.log"}


def tree_id():
    """Identity of the working tree's CONTENT. Same id => same code => same result.

    CONTENT ONLY -- no HEAD, no index, no status. The first version of this hashed HEAD and
    the porcelain status, and it was wrong in the one place the reuse had to work: session_end
    sweeps, then COMMITS, then pushes. The commit moves HEAD and cleans the status while
    changing not a single byte of any file, so the id changed and pre-push swept the identical
    tree all over again -- the exact 16 minutes this was built to remove. What decides whether
    a suite passes is the bytes on disk, so the bytes on disk are the whole identity.

    1517 tracked files / 62 MB here, hashed in well under a second against a 16-minute sweep.
    """
    def g(*a):
        # A TIMEOUT, because a hung git would hang the whole sweep before a suite ran.
        # An index.lock held by another process is the realistic way that happens here.
        return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True,
                              text=True, timeout=60).stdout

    paths = set(g("ls-files").splitlines())
    paths |= {l for l in g("ls-files", "--others", "--exclude-standard").splitlines()}
    h = hashlib.sha256()
    for rel in sorted(paths):
        rel = rel.strip()
        if not rel or rel in _SELF_WRITTEN:
            continue
        h.update(rel.encode())
        try:
            h.update(hashlib.sha256((REPO / rel).read_bytes()).digest())
        except OSError:
            h.update(b"<absent>")               # a path we cannot read is not a match
    return h.hexdigest()[:16]


def _journal(row):
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:                        # a journal we cannot write must not fail a run...
        print(f"  NOTE: run journal not written ({e})", file=sys.stderr)   # ...but it is said


def _rows():
    try:
        return [json.loads(l) for l in JOURNAL.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def passed_for(tree):
    """{suite: row} for every suite recorded PASSING on this exact tree."""
    out = {}
    for r in _rows():
        if r.get("kind") == "suite" and r.get("tree") == tree:
            if r.get("rc") == 0:
                out[r["suite"]] = r
            else:
                out.pop(r["suite"], None)       # a later failure retires an earlier pass
    return out


def fresh_sweep(tree, max_age_min):
    """The newest COMPLETE, clean sweep of this exact tree inside the window, or None."""
    now = time.time()
    best = None
    for r in _rows():
        if (r.get("kind") == "sweep" and r.get("tree") == tree and r.get("ok")
                and now - r.get("epoch", 0) <= max_age_min * 60):
            best = r
    return best


# ---------------------------------------------------------------- t461: per-suite result cache
# Jas, 2026-09-29: session_end re-ran all ~250 suites (~40 min) on every attempt, because the
# whole-tree identity above changes whenever the dispatcher appends to a ledger -- which on this
# box is every few minutes. A SUITE'S OWN INPUTS are what decide its result, so each suite gets
# its own key: the sha256 of the suite file, every repository module it imports (followed
# transitively, importgraph.reaches), and every repository script path it NAMES in its text
# (a suite that runs a script as a subprocess imports nothing from it). Same key -> same result.
# RULES: only PASSES are reused; a failure always runs again. A pass older than CACHE_MAX_H runs
# again. The sandbox suites (coder_*: real sandboxes, load- and state-sensitive) never come from
# the cache. Every reuse is printed with the time it was taken.
# KNOWN RED IS CACHED TOO (t1153, Jas 2026-10-13: "Can we streamline session end by not running tests
# if the features aren't built?"). A suite declared in the failure ceiling that failed with these exact
# inputs fails again with them, so it is reported "known red, inputs unchanged" rather than re-run --
# ~200 of the sweep's suites are tests for unbuilt features. Only a CEILING suite's failure is kept: an
# undeclared failure always runs again. Touch any input and it runs, so a suite that turns green is
# still seen and still comes off the ceiling.
# EVERY SUITE'S TEMP DIR IS ON DISK (2026-10-18). /tmp on the GX10 is RAM; a suite killed mid-run (a
# timeout, the memory guard, SIGKILL) never reaches its cleanup, and its repo copy -- 1-2 GB -- sat in
# RAM until the sweeper found it (two t406-* copies, 4 GB, 16 h). Same place dispatch_loop gives jobs.
DISK_TMP = Path.home() / ".cache" / "hivemind-tmp"


def disk_tmp():
    DISK_TMP.mkdir(parents=True, exist_ok=True)
    return str(DISK_TMP)


CACHE = REPO / "workspace" / "suite-cache.json"
# WHAT THIS CACHE MEANS, because on 2026-10-26 a reader guessed and got it backwards:
#   an entry EXISTS          -> the suite PASSED (record_passes writes one only on rc == 0)
#   entry.red AND entry.out  -> the suite FAILED, and `out` holds a bounded digest of its output
#                              (failure_tail: the tail, plus the last error-looking line)
#   no entry                 -> no verdict here; the suite has not run since, or was not allowed
# There is no PASS/FAIL word in this file. A reader looking for one reads "no out" -- which means
# PASSED -- as unreadable, and that invented 114 green suites in one session. Use
# `scripts/suite_verdicts.py` for a verdict: it reads workspace/suite-state.jsonl, which carries
# exit_code, and it says so in every report.
CACHE_MAX_H = 24
# WHICH RULE WROTE A RED ENTRY'S DIGEST (t8649, 2026-10-27). The digest is what E1 clusters the reds
# by, and the rule for building it changed on 2026-10-26: it used to keep the last four lines, which
# cut the cause off, and now keeps the tail plus the last error-looking line. Entries written before
# that change still say nothing about their cause, and nothing could tell them apart -- so ~200
# known-red suites sat unclassifiable and the burn-down had no target it could trust. An entry stamped
# with an older version (or none) is re-run instead of reused: a verdict nobody can read is not a
# verdict. Bump this whenever failure_tail's shape changes.
DIGEST_VERSION = 2
_PATH_RE = re.compile(r"\b((?:scripts|projects)/[\w./-]+\.(?:py|sh|json))\b")


def suite_key(path):
    import hashlib
    rel = str(Path(path).resolve().relative_to(REPO))
    files = {rel}
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import importgraph
        files |= set(importgraph.reaches(rel))
    except Exception as e:                                          # noqa: BLE001
        return None, f"imports unreadable: {e}"     # no key -> never reused
    try:
        files |= {m for m in _PATH_RE.findall(Path(path).read_text(encoding="utf-8", errors="replace"))
                  if (REPO / m).is_file()}
    except OSError as e:
        return None, f"suite unreadable: {e}"
    h = hashlib.sha256()
    for f in sorted(files):
        h.update(f.encode() + b"\0" + (REPO / f).read_bytes() + b"\0")
    return h.hexdigest()[:16], f"{len(files)} input file(s)"


def cached_passes(files, allowed=None):
    """{name: {"at", "key"}} for suites whose recorded pass still has the same key -- and, given the
    ceiling as `allowed`, {name: {..., "red": True, "out"}} for a declared suite's recorded failure."""
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        print(f"  NOTE: {CACHE.name} unreadable ({e}); nothing reused", flush=True)
        return {}
    now, out = time.time(), {}
    for f in files:
        if "coder" in f.name:
            continue
        c = cache.get(f.name)
        if not c or now - c.get("t", 0) > CACHE_MAX_H * 3600:
            continue
        if c.get("red") and (allowed is None or f.name not in allowed):
            continue                                           # off the ceiling now: it must run
        key, _why = suite_key(f)
        if not key or key != c.get("key"):
            continue
        if c.get("red") and c.get("digest") != DIGEST_VERSION:
            continue                    # an old-rule digest: re-run it, because the cause is not in it
        out[f.name] = {"at": c["at"], "secs": c.get("secs", 0.0), "key": key}
        if c.get("red"):
            out[f.name].update(red=True, out=c.get("out", ""))
    return out


# THE LINES THAT TELL YOU WHY. Used only to decide what to KEEP of a failing suite's output; it never
# decides anything else about a suite.
FAILURE_LINE = re.compile(
    r"(Error|Exception|Traceback|assert|FAIL|Refused|refused|No such file|timed out|No module)", re.I)


def failure_tail(out, max_lines=40, max_chars=3000):
    """What to store of a failing suite's output: enough to classify it, bounded.

    KEEP THE TAIL (it carries the tally) AND GUARANTEE THE ERROR LINE. Until 2026-10-26 this stored the
    last four lines only, and most of these suites print their tally last -- so 37 of 318 red suites had
    no error line on record at all and the rest were a 257-way tail of singletons. The classification
    was not impossible because the failures were unrelated; the stored window cut the cause off.

    Bounded in characters as well as lines: suite-cache.json is rewritten by every sweep.
    """
    text = (out or "").strip()
    if not text:
        return "(no output recorded)"
    lines = text.splitlines()
    tail = lines[-max_lines:]
    blob = "\n".join(tail)
    if not FAILURE_LINE.search(blob):
        # The cause scrolled past the window: keep the LAST line that looks like one, above the tail.
        for line in reversed(lines[:-max_lines] if len(lines) > max_lines else lines):
            if FAILURE_LINE.search(line) and line not in tail:
                blob = line.strip() + "\n...\n" + blob
                break
    return blob[:max_chars]


def record_passes(results, allowed=None):
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    except ValueError:
        cache = {}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for name, rc, secs, _out, flaky in results:
        if name in cache and rc != 0:
            del cache[name]                                    # a failure forgets the old pass
        red = rc != 0 and allowed is not None and name in allowed
        if (rc == 0 or red) and not flaky and "coder" not in name:
            key, _why = suite_key(TESTS / name)
            if key:
                cache[name] = {"key": key, "at": stamp, "t": time.time(), "secs": round(secs, 1)}
                if red:
                    cache[name].update(red=True, out=failure_tail(_out),
                                       digest=DIGEST_VERSION)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=1) + "\n", encoding="utf-8")
    tmp.replace(CACHE)


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def failing_first(files, journal=None, now=None, window_s=7 * 86400):
    """`files` with every suite that failed in the journal within the window moved to the front,
    the rest in their given order. A journal that cannot be read changes nothing."""
    now = time.time() if now is None else now
    failed = set()
    try:
        for ln in Path(journal or JOURNAL).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(ln)
            except ValueError as e:                    # a torn line: said, and left out of the order
                print(f"  NOTE: unreadable journal line skipped for ordering ({e})", file=sys.stderr)
                continue
            if (r.get("kind") == "suite" and r.get("rc") not in (0, None)
                    and now - float(r.get("epoch") or 0) <= window_s):
                failed.add(r.get("suite"))
    except OSError as e:
        print(f"  NOTE: journal unreadable ({e}); suites keep their order", file=sys.stderr)
        return list(files)
    return [f for f in files if Path(f).name in failed] + [f for f in files if Path(f).name not in failed]


def _once(path):
    """Run one suite in its OWN process group and its OWN temp dir, and leave neither behind (t415).

    A suite killed at its cap used to leave everything it had started: subprocess.run kills the one
    process it spawned, so a fake model server or the coder.py a suite drove was orphaned, and the
    suite's finally never ran -- 15 /tmp/delivery-check-*/fake.py servers and 10 /tmp/coder-*
    sandboxes on 2026-09-25. Now the whole group is killed, and the suite's TMPDIR -- where
    tempfile, and coder.py's sandboxes, put everything -- is removed. Per suite, never a sweep of
    /tmp: four suites run at once and each owns only its own."""
    import signal
    t = time.time()
    cap = TIMEOUT_FOR.get(Path(path).name, TIMEOUT)
    tmp = tempfile.mkdtemp(prefix=f"run-tests-{Path(path).stem}-", dir=disk_tmp())
    env = dict(os.environ, TMPDIR=tmp)
    p = subprocess.Popen([sys.executable, str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, cwd=str(REPO), env=env, start_new_session=True)
    try:
        out, err = p.communicate(timeout=cap)
        # WHOLE. Callers print a few tail lines; the known-failure log needs all of it (t286:
        # a 600-char tail held only the FAIL lines, never what led to them).
        return p.returncode, round(time.time() - t, 1), out + err
    except subprocess.TimeoutExpired:
        return -9, cap, f"TIMED OUT after {cap}s"
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)       # the suite and everything it started
        except ProcessLookupError:
            p.poll()                                # the group already exited: nothing to kill
        p.communicate()
        shutil.rmtree(tmp, ignore_errors=True)


def retry_alone(results, allowed=None, once=None, log=None):
    """Re-run every SURPRISING failure ONCE, ALONE, and say which it was (t1885).

    The retry already existed in run_one, but it ran INSIDE the worker pool: a suite that failed
    because seven others were hammering the box was re-run under the same load and failed again.
    test_coder_yield_resume.py is the whole history of t1167 for exactly that reason -- 11/0 when
    run alone, red under a sweep. Here the pool has drained before `once` is called for a retry,
    which is what makes "alone" true rather than a word.

    A DECLARED failure is not retried at all. The ceiling already says it fails, so re-running all
    173 of them bought nothing and added minutes to a sweep that was already an hour long."""
    once = once or _once
    log = log or (REPO / "workspace" / "flaky-tests.log")
    out = []
    for name, rc, secs, text, flaky in results:
        if rc == 0 or flaky or (allowed is not None and name in allowed):
            out.append((name, rc, secs, text, flaky))
            continue
        rc2, secs2, text2 = once(TESTS / name)
        if rc2 == 0:
            try:
                with Path(log).open("a") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M')} {name} failed under load then "
                            f"passed alone ({secs}s then {secs2}s, t1885)\n")
            except OSError as e:
                print(f"  NOTE: {log} not written ({e}); {name} was flaky", file=sys.stderr)
            print(f"  [{name}] failed under load, passed ALONE -- flaky, not a failure, recorded",
                  file=sys.stderr, flush=True)
            out.append((name, 0, secs + secs2, text2, True))
        else:
            out.append((name, rc2, secs + secs2, text2, False))
    return out


def run_light(files, jobs=8, once=None, allowed=None, log=None):
    """The light phase: a pool, then the surprising failures retried one at a time AFTER it drains.

    Split out of main so the ordering is testable without running a real suite: a fake `once` can
    record when each call ran, and the test asserts no retry overlapped another call."""
    once = once or _once

    def one(path):
        rc, secs, out = once(path)
        _tick(path.name, rc, secs)
        return path.name, rc, secs, out, False

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        results = list(ex.map(one, files))
    return retry_alone(results, allowed, once=once, log=log)


def run_one(path):
    """Run a suite; on failure run it ONCE more, alone, and say which it was.

    A SUITE THAT FAILS UNDER LOAD AND PASSES ALONE IS FLAKY, AND FLAKY IS ITS OWN VERDICT.
    test_coder_guards.py did exactly that on 2026-09-04: 136 assertions pass in isolation, one
    failure under four-way parallelism. Reported as a plain failure it blocks a push at random,
    which teaches people to use --no-verify, and a gate people bypass is worse than no gate
    (AGENTS.md rule 9). Reported as a pass it hides a real timing bug.

    So it is neither. A flake does not block, and it is RECORDED to workspace/flaky-tests.log
    so the count accumulates where somebody will see it rather than in one session's memory.
    """
    rc, secs, out = _once(path)
    if rc == 0:
        _tick(path.name, 0, secs)
        return path.name, rc, secs, out, False
    rc2, secs2, out2 = _once(path)
    if rc2 == 0:
        try:
            with (REPO / "workspace" / "flaky-tests.log").open("a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M')} {path.name} failed then passed "
                        f"({secs}s then {secs2}s)\n")
        except OSError as e:
            print(f"  NOTE: flaky-tests.log not written ({e}); {path.name} was flaky", file=sys.stderr)
        _tick(path.name, 0, secs + secs2)
        return path.name, 0, secs + secs2, out, True
    _tick(path.name, rc2, secs + secs2)
    return path.name, rc2, secs + secs2, out2, False


SANDBOX_BASELINE = REPO / "workspace" / "sandbox-baseline.json"


def sandbox_failures(names):
    """[(suite, tail of its output)] for the suites among `names` that FAIL inside coder.py's sandbox.

    WHY (2026-09-30). coder.py refuses to start a build when a suite fails in its sandbox and is
    declared nowhere (the ceiling, or sandbox-baseline.json). test_notify.py passes on main and
    fails in the sandbox (py_vapid lives in .venv, which the sandbox leaves out); it had been hidden
    in the ceiling, so --ratchet, which only sees main, removed it -- and every build refused to
    start until it was declared properly. This is that check, run on exactly the suites a ratchet
    is about to drop. Pinned by scripts/tests/test_run_tests_ratchet_sandbox.py (L-153)."""
    import shutil
    sys.path.insert(0, str(REPO / "projects" / "local-inference"))
    import coder
    root = coder.sandbox()
    bad = []
    try:
        for n in sorted(names):
            try:
                # Its own TMPDIR, removed after (t415's rule; /tmp ran out of inodes 2026-10-06).
                tmp = tempfile.mkdtemp(prefix=f"ratchet-{Path(n).stem}-", dir=disk_tmp())
                try:
                    r = subprocess.run([sys.executable, str(root / "scripts" / "tests" / n)], cwd=str(root),
                                       capture_output=True, text=True, timeout=TIMEOUT_FOR.get(n, TIMEOUT),
                                       env=dict(os.environ, TMPDIR=tmp))
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                if r.returncode:
                    bad.append((n, (r.stdout + r.stderr)[-300:]))
            except subprocess.TimeoutExpired:
                bad.append((n, "timed out in the sandbox"))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return bad


def ratchet_keep(dropping, failing_in_sandbox=sandbox_failures, baseline=None):
    """(suites to keep in the ceiling anyway, their reasons): of the suites a ratchet would drop, those
    that still fail in the sandbox and are not in sandbox-baseline.json. Dropping one of those stops
    every build, so it stays until someone declares it where it belongs."""
    try:
        cannot = set(json.loads(Path(baseline or SANDBOX_BASELINE).read_text())["cannot_run_in_sandbox"])
    except (OSError, ValueError, KeyError) as e:
        print(f"  NOTE: sandbox-baseline.json unreadable ({e}); every dropped suite is checked")
        cannot = set()
    bad = failing_in_sandbox(sorted(set(dropping) - cannot))
    return {n for n, _ in bad}, bad


def known():
    try:
        return set(json.loads(CEILING.read_text())["allowed_to_fail"])
    except Exception:                                        # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ratchet", action="store_true")
    ap.add_argument("-k", default="", help="only suites whose name contains this")
    ap.add_argument("--jobs", type=int, default=8)   # 20 cores, mostly idle (Jas, 2026-10-01)
    ap.add_argument("--resume", action="store_true",
                    help="skip suites already recorded passing on this exact tree")
    ap.add_argument("--cache", action="store_true",
                    help="reuse a suite's pass when its own inputs are unchanged (t461)")
    ap.add_argument("--assert-fresh", type=int, metavar="MINUTES", default=None,
                    help="exit 0 if a complete clean sweep of this exact tree ran within "
                         "MINUTES, else exit 1. Runs nothing. For the pre-push hook.")
    a = ap.parse_args()

    global _TREE, _LOCK_HANDLE
    _TREE = tree_id()

    # A QUESTION, NOT A RUN. The hook asks this before deciding whether to sweep.
    if a.assert_fresh is not None:
        got = fresh_sweep(_TREE, a.assert_fresh)
        if got:
            print(f"  the full sweep passed at {got['at']} on this exact tree "
                  f"({got['passing']}/{got['total']}, tree {_TREE}). Nothing has changed "
                  f"since, so it is not being run again.")
            return 0
        print(f"  no clean sweep of this tree ({_TREE}) in the last {a.assert_fresh} "
              f"minute(s) -- it must run.")
        return 1

    # ONE SWEEP AT A TIME (t1888), taken BEFORE anything is read, run or written, so a refused
    # caller leaves no journal row, no temp dir and no half-swept state behind.
    try:
        _LOCK_HANDLE = acquire_sweep_lock()
    except SweepBusy as busy:
        print(f"  ANOTHER SWEEP IS ALREADY RUNNING -- nothing was started.\n"
              f"  holder: {busy}\n"
              f"  One sweep at a time is a rule, not a preference: two sweeps plus a build is the\n"
              f"  2026-10-24 20:36 configuration that took the box to 1.42 GB free and stopped\n"
              f"  Swift mid-conversation. Wait for it, or stop it, and run again.",
              file=sys.stderr, flush=True)
        return SWEEP_LOCK_BUSY

    files = sorted(f for f in TESTS.glob("test_*.py") if a.k in f.name)
    if not files:
        print(f"  NO SUITES MATCHED {a.k!r}. Nothing ran, which is not a pass.")
        return 1

    # THE coder_* SUITES RUN ALONE, AND IT IS NOT A WORKAROUND (2026-09-04). Each builds a
    # real sandbox and runs the whole suite inside it; four at once contend, and the assertion
    # that fails is literally "concurrent sandboxes do not collide". Run singly they are
    # 136/136. Retrying hid it as a flake twice before the cause was looked at.
    #
    # Adding it to the failure ceiling would have been the easy move and the wrong one: the
    # suite is not broken, the SCHEDULE was. A ceiling entry would have recorded a defect that
    # does not exist and stopped anyone looking again.
    heavy = [f for f in files if "coder" in f.name]
    light = [f for f in files if "coder" not in f.name]
    global _TOTAL
    _TOTAL = len(files)
    _journal({"kind": "start", "tree": _TREE, "total": _TOTAL, "epoch": time.time(),
              # WHO STARTED IT (2026-10-19): session_end and the hourly check share this journal; the
              # Lanes tab says which one is running. Set by the caller in SWEEP_BY; unset = by hand.
              "by": os.environ.get("SWEEP_BY") or "by hand",
              "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})   # the progress bar's denominator

    # RESUME. Only passes are reused, only for this exact tree, and every one of them is
    # named below with the time it was actually taken -- so the report still accounts for
    # all 149 suites and nobody can mistake a reused pass for one taken just now.
    reused = {}
    if a.resume:
        reused = passed_for(_TREE)
        light = [f for f in light if f.name not in reused]
        heavy = [f for f in heavy if f.name not in reused]
        if reused:
            print(f"  RESUMING: {len(reused)} suite(s) already passed on this exact tree "
                  f"({_TREE}) and are not being run again.", flush=True)
            _DONE_INIT = len(reused)
            globals()["_DONE"] = _DONE_INIT

    if a.cache:
        hits = cached_passes(light + heavy, known())
        if hits:
            reused.update(hits)
            light = [f for f in light if f.name not in hits]
            heavy = [f for f in heavy if f.name not in hits]
            reds = sum(1 for h in hits.values() if h.get("red"))
            print(f"  CACHED: {len(hits) - reds} suite(s) passed and {reds} declared suite(s) failed with "
                  f"these exact inputs within {CACHE_MAX_H} h; none is run again (t461, t1153).", flush=True)
            globals()["_DONE"] = len(reused)

    # SKIPPED SUITES COUNT AS DONE ON THE LANES TAB (Jas, 2026-10-19: it showed "14/791 suites ...
    # running 36m" for a sweep at suite 737 -- cached and resumed suites wrote no journal row, so the
    # count was suites RUN, not suites reached). One row says how many are already accounted for.
    if reused:
        _journal({"kind": "cached", "tree": _TREE, "n": len(reused),
                  "red": sum(1 for r in reused.values() if r.get("red"))})
    # RECENT FAILURES FIRST (2026-10-19 plan, session_end speed-up 5): a regression shows in the first
    # minutes, not at minute 70. Order only -- the same suites run, under the same limits.
    light, heavy = failing_first(light), failing_first(heavy)
    print(f"  running {len(light)} suite(s) {a.jobs} at a time, then {len(heavy)} "
          f"sandbox suite(s) one at a time, {TIMEOUT}s each\n", flush=True)
    # THE POOL DRAINS BEFORE ANY RETRY (t1885). A retry inside the pool runs under the same load
    # that broke the suite; retry_alone only sees `results` once every light suite has finished.
    allowed = known()
    results = run_light(light, a.jobs, allowed=allowed)
    results += [run_one(f) for f in heavy]
    results += [(n, 1 if r.get("red") else 0, r.get("secs", 0.0), r.get("out", ""), False)
                for n, r in reused.items()]

    try:
        record_passes([r for r in results if r[0] not in reused], known())
    except OSError as e:
        print(f"  NOTE: suite cache not written: {e}", flush=True)
    failed = {n for n, rc, _s, _o, _f in results if rc != 0}
    flaky = {n for n, _rc, _s, _o, f in results if f}
    for name, rc, secs, out, is_flaky in sorted(results):
        if is_flaky:
            print(f"  {_c('33', 'FLAKY')} {name:44} {secs:6.1f}s  failed under load, passed "
                  f"alone -- recorded, not blocking")
        elif name in reused and reused[name].get("red"):
            print(f"  {_c('33', 'KNOWN')} {name:44} {secs:6.1f}s  "
                  f"{_c('90', 'known red, inputs unchanged since ' + reused[name]['at'] + ' -- not re-run')}")
        elif name in reused:
            print(f"  {_c('32', 'pass')}  {name:44} {secs:6.1f}s  "
                  f"{_c('90', 'reused from ' + reused[name]['at'] + (' -- same inputs' if 'key' in reused[name] else ' -- same tree'))}")
        elif rc == 0:
            print(f"  {_c('32', 'pass')}  {name:44} {secs:6.1f}s")
        elif allowed is not None and name in allowed:
            # A KNOWN FAILURE KEEPS ITS OUTPUT (t286, 2026-10-01). It was thrown away, so the last
            # suite in the ceiling failed under sweep load and nobody could see why.
            kept = KNOWN_LOGS / f"{name}.log"
            try:
                KNOWN_LOGS.mkdir(parents=True, exist_ok=True)
                kept.write_text(out, encoding="utf-8")
                where = str(kept.relative_to(REPO))
            except OSError as e:
                where = f"output not kept: {e}"
            print(f"  {_c('33', 'KNOWN')} {name:44} {secs:6.1f}s  (in the ceiling; {where})")
            for line in out.strip().splitlines()[-4:]:
                print(f"          {line[:110]}")
        else:
            print(f"  {_c('31', 'FAIL')}  {name:44} {secs:6.1f}s")
            for line in out.strip().splitlines()[-4:]:
                print(f"          {line[:110]}")

    if allowed is None:
        print(f"\n  {CEILING.name} is missing, so nothing bounds the failures. Create it:")
        print("    python3 scripts/run_tests.py --ratchet")
        return 1

    # A FILTERED RUN MAY NOT TOUCH THE CEILING, AND MAY NOT CLAIM ANYTHING WAS FIXED.
    # Found 2026-09-04 by running `-k exam`: the three known-failing suites were excluded by
    # the filter, so `failed` came back empty and all three were reported as "now PASS".
    # With --ratchet that would have silently emptied the ceiling on the strength of suites
    # THAT NEVER RAN -- the vacuous-pass shape (`check_vacuous.py`) inside the runner meant
    # to enforce it: a verdict computed from a collection nothing put anything into.
    if a.k:
        print(f"\n  (filtered run: {len(files)} of "
              f"{len(list(TESTS.glob('test_*.py')))} suites. The ceiling is NOT evaluated -- "
              f"a suite that did not run has not passed.)")
        return 1 if failed else 0

    if a.ratchet:
        if failed - allowed:
            print(f"\n  {_c('31', 'REFUSING')}: these are NEW failures, not ones to record: "
                  f"{sorted(failed - allowed)}")
            print("  --ratchet only removes suites that now pass. Fix them first.")
            return 1
        keep, why = ratchet_keep(allowed - failed)
        for n, tail in why:
            print(f"  {_c('33', 'KEPT')} {n}: passes here but FAILS in coder.py's sandbox -- dropping it would stop "
                  f"every build. Declare it in workspace/sandbox-baseline.json with the missing resource, then "
                  f"ratchet again.\n      {tail.strip().splitlines()[-1] if tail.strip() else ''}")
        CEILING.write_text(json.dumps(
            {**json.loads(CEILING.read_text()), "allowed_to_fail": sorted(failed | keep),
             "lowered_on": time.strftime("%Y-%m-%d")}, indent=1) + "\n")
        print(f"\n  ceiling lowered {len(allowed)} -> {len(failed | keep)}")
        return 0

    new = failed - allowed
    fixed = allowed - failed
    print(f"\n  {len(files) - len(failed)}/{len(files)} passing; "
          f"{len(failed)} failing, {len(allowed)} allowed"
          + (f", {len(flaky)} FLAKY" if flaky else ""))
    if flaky:
        print(_c("33", f"  Flaky: {sorted(flaky)} -- see workspace/flaky-tests.log. A suite "
                       f"that only fails under load is a real bug in the suite or the code, "
                       f"just not a reproducible one."))
    if fixed:
        print(_c("32", f"  {len(fixed)} suite(s) in the ceiling now PASS: {sorted(fixed)}"))
        print(_c("32", "  Lower it: python3 scripts/run_tests.py --ratchet"))
    if new:
        print(_c("31", f"\n  NEW FAILURES -- these are not in the ceiling: {sorted(new)}"))
        print(_c("31", "  A suite that passed before and fails now is a regression. Fix it, "
                       "or say why it belongs in the ceiling."))
        # ENDED, NOT OK: "ok": False never satisfies --assert-fresh, and it tells the progress
        # line (lane_status) that this sweep is over.
        _journal({"kind": "sweep", "tree": _TREE, "ok": False, "total": len(files),
                  "epoch": time.time(), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return 1
    print(_c("32", "  No new failures."))
    # THE COMPLETION ROW, and only on a full unfiltered sweep. A filtered run (-k) has not
    # examined everything, so it must never be able to satisfy --assert-fresh; that would be
    # a pass standing in for a question it never asked.
    if not a.k:
        _journal({"kind": "sweep", "tree": _TREE, "ok": True,
                  "passing": len(files) - len(failed), "total": len(files),
                  "reused": len(reused), "epoch": time.time(),
                  "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return 0


if __name__ == "__main__":
    sys.exit(main())
