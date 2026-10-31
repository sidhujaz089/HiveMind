#!/usr/bin/env python3
"""Verifies you left things findable, then commits and pushes. Refuses if not.

It checks that you did the things that make the next session cheap -- updated the
project's STATUS.md, updated STATE.md, written a session log -- then rebuilds the
index, runs the full invariant check, commits, and pushes.

It refuses to finish if the check fails. That is the point of it.

    python3 scripts/session_end.py --agent claude-code/opus-5
    python3 scripts/session_end.py --agent human/jas --message "fix(superbutton): ..."
    python3 scripts/session_end.py --agent ... --dry-run

Ported from session-end.ps1.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
TODAY = date.today().isoformat()
VALID_TYPES = "feat|fix|docs|refactor|test|chore|migrate|session"
_TTY = sys.stdout.isatty()

# THE AGENT TRAILER, accepted exactly as scripts/hooks/commit-msg accepts it (t-3a, 2026-10-23,
# handover 2026-10-23 item 5). Three tools read the trailer and they must not disagree about
# what is valid: commit-msg widened the MODEL half to include ':' on purpose (its comment:
# ollama tags are `name:tag`; `qwen3.8:27b-mtp-q4_K_M` is the real identifier of the local
# model that wrote a commit, and without the colon coder.py's review-branch commits were
# refused for every local run), and role_gate resolves by ROLES.json claims, which carry
# `ollama/<model>` model names that contain colons. session_end's own check kept the narrow
# charset, so the trailer the DSH standing prompt mandates -- `Agent: dsh-harness/qwen3.8:27b-mtp-q4_K_M`
# -- passed the two enforcement points and was refused by the tool that records who you are.
# The ':' is added to the model half only; the shape <tool>/<model> is unchanged, and the
# $ anchor means a newline cannot smuggle a second header.
AGENT_TRAILER_RE = r"[A-Za-z0-9._-]+/[A-Za-z0-9._:-]+"


def agent_trailer_ok(value) -> bool:
    """True when the Agent trailer value is well-formed: <tool>/<model>, the model half
    allowed a colon for ollama tags. Same charset as scripts/hooks/commit-msg."""
    return re.fullmatch(AGENT_TRAILER_RE, value or "") is not None


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def push_is_disabled():
    """True when origin's PUSH url has been deliberately pointed at nothing.

    `git remote set-url --push origin DISABLED-...` keeps fetch working and leaves the GitHub
    repository untouched, so this is reversible with one command. Checked by reading the url
    rather than by trying a push and interpreting the failure -- a network error and a
    deliberate decision must never be confused, because one of them means the history is
    unbacked and the other means it is backed up somewhere else.
    """
    rc, out, _err = git("remote", "get-url", "--push", "origin")
    # NO ORIGIN AT ALL is the end state (2026-10-13: GitHub removed, chat/ in git) -- off, not a failure.
    return rc != 0 or any("DISABLED" in line for line in out)


# WHERE THE TIME WENT (t9580, 2026-10-31). Jas: "I want session ends to generate data that we can see on a graph. If we
# see outliers that is the point we target our CI." Every section's duration is recorded, so the next timeout is
# derived from these rows instead of being another round number. Recording is best-effort: telemetry must never fail a
# close-out.
_RUN_T0 = time.time()          # the whole close-out, started when the module loads
_CHECK_S = [None]              # the invariant check's own duration, filled where it runs
_TIMINGS = []
_SECTION_T0 = [None]
_SECTION_NAME = [None]


def _close_section():
    if _SECTION_T0[0] is not None and _SECTION_NAME[0]:
        _TIMINGS.append((_SECTION_NAME[0], round(time.time() - _SECTION_T0[0], 1)))


def section(title):
    _close_section()
    _SECTION_T0[0] = time.time()
    _SECTION_NAME[0] = title
    print()
    print(_c("36", title))


def ok(t):
    print(f"  {_c('32', '[ok]')}   {t}")


def warn(t):
    print(f"  {_c('33', '[warn]')} {t}")


def bad(t):
    print(f"  {_c('31', '[STOP]')} {t}")


def git(*args, root=None):
    """Every git call in this file, with the ceiling on the wait (900s).

    root= lets a collector name the repository it is measuring. A recorder hard-wired to
    REPO_ROOT can only ever describe the workspace it ships in, so the population it exists to
    count in any other repository -- a repo under test, a worktree -- is unobservable through it
    (t753).
    """
    p = subprocess.run(
        ["git", "-C", str(REPO_ROOT if root is None else root), *args],
        capture_output=True, text=True, errors="replace",
        timeout=900,
    )
    out = (p.stdout or "").rstrip("\n")
    err = (p.stderr or "").rstrip("\n")
    return p.returncode, ([l for l in out.split("\n") if l] if out else []), err


BRANCH_LEDGER = REPO_ROOT / "workspace" / "run-logs" / "branch-ledger.jsonl"


def index_free(root=None, seconds=0.0):
    """True when no other git process holds the index, waiting up to `seconds` for one to finish.

    TWO SESSIONS, ONE INDEX (2026-10-30). Jas: "I want multiple session ends to happen at the same time with no
    issues." git's index is a single file per repository, so two session ends cannot both be staging at once --
    what they CAN do is wait for each other instead of failing. The wait is bounded and whatever happens is said
    out loud; a session end that quietly did nothing would be worse than one that refused.

    The lock's path comes from git itself, not from `<root>/.git/index.lock`: in a worktree `.git` is a FILE
    pointing elsewhere, and a hard-wired path would wait for ever on a lock that is not the one in use.
    """
    root = root or REPO_ROOT
    rc, out, _ = git("rev-parse", "--git-path", "index.lock", root=root)
    if rc != 0 or not out:
        return True                                    # cannot resolve it: proceed and let git refuse
    lock = Path(out[0] if isinstance(out, list) else str(out))
    if not lock.is_absolute():
        lock = Path(root) / lock
    deadline = time.monotonic() + max(0.0, seconds)
    while lock.exists():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)
    return True


# --- E: what a close-out is NOT for (2026-10-30) --------------------------------------------------------------
# Jas: "I want to only do what's necessary and move other routines out of session end where possible."
# Two of these sections run work a systemd timer already runs: section 3b indexed the corpus for up to an hour
# (corpus-index.timer does it hourly) and took the daily NAS bundle (nas-backup.timer does that too). Re-doing the
# timer's job at close is not more careful -- it is slower, and a close that takes three hours is the one that gets
# cut off half way, which is how a session ends with no handover.
#
# WHAT IT MUST NEVER DO IS GO QUIET. Every skip names what was skipped, how fresh the timer's own record is, and
# the command to run it now. A routine skipped without a line is the "it existed and never ran" defect this
# repository keeps finding, and it is worse than a slow close.
SLOW_ROUTINES = {
    "corpus": {"cmd": "python3 scripts/corpus_routine.py", "timer": "corpus-index.timer",
               "stale_after_h": 6.0, "what": "indexing this session into the searchable corpus"},
    # BOTH HAVE A HOME SINCE 2026-10-30: deploy/systemd/slow-checks.{service,timer} runs check_apps and digests
    # daily at 05:30 and writes a row per check per run to workspace/slow-checks.jsonl. The bound is 30 h -- a
    # daily schedule plus slack for a delayed run -- so a close-out only does them when that record has gone quiet.
    "apps": {"cmd": "python3 scripts/slow_checks.py --check apps", "timer": "slow-checks.timer",
             "stale_after_h": 30.0, "what": "the app pipeline (apksigner, emulated here)"},
    "digests": {"cmd": "python3 scripts/slow_checks.py --check digests", "timer": "slow-checks.timer",
                "stale_after_h": 30.0, "what": "the watched corpus digests"},
}
_UNSET = object()


def timer_age_hours(unit):
    """Hours since the timer's service last finished, or None when there is no record.

    MONOTONIC, not a locale string: `systemctl list-timers` prints "Thu 2026-10-29 23:06:47 MDT", and a date parsed
    out of that depends on the machine's locale and clock format. ExecMainExitTimestampMonotonic is a number of
    microseconds since boot, and /proc/uptime is the other end of the same ruler.
    """
    if not unit:
        return None
    service = unit[:-6] + ".service" if unit.endswith(".timer") else unit
    try:
        r = subprocess.run(["systemctl", "--user", "show", service, "-p", "ExecMainExitTimestampMonotonic"],
                           capture_output=True, text=True, errors="replace", timeout=30)
        usec = int((r.stdout or "").strip().split("=", 1)[1])
        up_s = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None
    if usec <= 0:
        return None
    return max(0.0, (up_s - usec / 1_000_000.0) / 3600.0)


def routine_decision(name, asked=False, fast=False, age_hours=_UNSET):
    """(run_now, the line to print) for a routine that may belong to a timer rather than to this close.

    The rules, in the order they are asked:
      * asked for on the command line  -> run it; the caller overrides everything;
      * a timer runs it and is FRESH   -> skip, and say which timer and how long ago;
      * a timer runs it and is BEHIND  -> run it: the thing meant to cover it is not covering it;
      * no timer at all                -> run it, unless --fast, which skips it and prints the command.
    A routine with no timer record is NOT treated as fresh: unknown is not a licence to skip.
    """
    row = SLOW_ROUTINES[name]
    age = timer_age_hours(row["timer"]) if age_hours is _UNSET else age_hours
    if asked:
        return True, f"  {name}: run because it was asked for"
    if row["timer"] and age is not None:
        if age <= row["stale_after_h"]:
            # SAY HOW TO RUN IT (2026-10-31). The line named the timer and not the command, so a reader who wanted
            # the thing NOW -- or who doubted the timer -- had to go and find it. test_session_end_concurrent caught
            # exactly that, red on main: the message must name the command as well as where the job went.
            return False, (f"  {name}: {row['what']} left to {row['timer']}, which ran it "
                           f"{age:.1f} h ago (not this close-out's job -- run it now with: {row['cmd']})")
        return True, (f"  {name}: {row['timer']} is BEHIND at {age:.1f} h (bound "
                      f"{row['stale_after_h']:g} h), so this close-out runs it")
    if fast:
        return False, f"  {name}: skipped (--fast) -- run it with: {row['cmd']}"
    if row["timer"]:
        return True, f"  {name}: no record from {row['timer']}, so this close-out runs it (unknown is not fresh)"
    return True, f"  {name}: no timer owns it, so this close-out runs it"


def _verdict_cache():
    """The shared verdict cache module, or None when it cannot be used. Never raises."""
    try:
        sys.path.insert(0, str(SCRIPTS))
        import test_verdict_cache as tvc
        return tvc
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(_c("33", f"  NOTE: the verdict cache is unavailable ({type(e).__name__}: {e})"))
        return None


def services_digest():
    """A hash of what the running services say about themselves.

    IT IS PART OF THE CHECK'S KEY BECAUSE A CHECK CAN FAIL FOR SOMETHING NO FILE RECORDS. On 2026-10-30 the only
    failure from the full workspace check was "No running service is older than the code it runs", and a
    `systemctl --user restart swift-proxy` fixed it without touching a byte of the repository. A key made of files
    alone would have gone on reporting that failure for the rest of the day.
    """
    r = subprocess.run([sys.executable, str(SCRIPTS / "check_stale_services.py")],
                       capture_output=True, text=True, errors="replace", timeout=300)
    return hashlib.sha256(((r.stdout or "") + (r.stderr or "")).encode("utf-8")).hexdigest()


def full_check(force=False, run=None, services=None):
    """The full workspace check, paid at most once per WORLD state. (rc, reused).

    The check is 10-16 minutes on this tree (953 uncommitted paths), and a session end may attempt to close several
    times: once it failed on a stale service, once on a hook, once on a staged index. Paying sixteen minutes again
    when nothing has changed is what made one close-out take three hours on 2026-10-30 -- and the second run cannot
    disagree with the first, because the world it measures is the same one.

    `force=True` (or --force-check) pays it again deliberately, which is what to do when something outside git has
    changed and you do not trust the key.
    """
    run = run or (lambda cmd: subprocess.run(cmd, timeout=1800))
    tvc = _verdict_cache()
    key = None
    if tvc is not None:
        try:
            key = tvc.state_digest(REPO_ROOT, tests=["check_workspace"], scope=(".",))
            key = hashlib.sha256(f"{key}|{(services or services_digest)()}".encode("utf-8")).hexdigest()
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            print(_c("33", f"  NOTE: this check cannot be cached ({type(e).__name__}: {e}); paying for it"))
            key = None
        prior = tvc.get(key) if key else None
        if prior is not None and not force:
            return int(prior.get("check_workspace", 1)), True
    _t0 = time.time()
    rc = run([sys.executable, str(SCRIPTS / "check_workspace.py")]).returncode
    _CHECK_S[0] = max(_CHECK_S[0] or 0.0, time.time() - _t0) if rc is not None else _CHECK_S[0]
    if key:
        tvc.put(key, {"check_workspace": rc}, ["check_workspace"],
                extra={"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return rc, False


def commit_paths(paths, message, agent, root=None, wait=300):
    """Stage EXACTLY `paths` and commit them. Returns (rc, output).

    NEVER `git add -A`. It staged the whole tree, which on this box meant a session end swept another live
    session's work into its own commit -- and, when the commit was then refused or killed, left every path in
    the repository staged. That is what blocked merges for hours on 2026-10-30. The caller passes the paths it
    found; anything that appeared afterwards belongs to whoever created it.

    A REFUSED COMMIT LEAVES THE INDEX AS IT WAS FOUND: the staged paths are reset (content untouched) so the next
    run, another session, or a merge is not fighting a staging nobody asked for.
    """
    root = Path(root or REPO_ROOT)
    if not index_free(root, wait):
        return 1, ("session_end: another git process is holding the index, so nothing was staged and nothing "
                   "was committed -- run this again when it finishes")
    todo = [p for p in dict.fromkeys(paths) if p]
    if todo:
        rc, _out, err = git("add", "--", *todo, root=root)
        if rc != 0:
            return rc, f"session_end: git add refused\n{err}"
    full = f"{message}\n\nAgent: {agent}\n"
    p = subprocess.run(["git", "-C", str(root), "commit", "-F", "-"],
                       input=full, capture_output=True, text=True, errors="replace", timeout=1800)
    text = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0 and todo:
        git("reset", "-q", "--", *todo, root=root)     # content stays; only the staging is put back
        text += ("\n  (the staged paths were reset, so the index is as this run found it: nothing is left "
                 "half-added for the next session or for a merge)")
    return p.returncode, text



def unmerged_branches(repo_root) -> list:
    """Every coder/* branch not merged into HEAD, named exactly as git names it.

    THE POPULATION BEHIND A PROSE COUNT. "14 branches unmerged" sat in an RCA for nine days
    with no artefact behind it, and the surface reporting it named three of the four it counted
    (workspace/rca/0f167ec334ff.md). git's table pads every name with two leading spaces and
    marks the current branch with '* '; both are its formatting, not part of the name, so they
    come off and no name is sliced -- a truncated branch cannot be joined to a row or checked
    out. The repository is the caller's, passed down through git(root=).
    """
    rc, out, err = git("branch", "--list", "coder/*", "--no-merged", root=repo_root)
    if rc != 0:
        raise RuntimeError(f"git branch --list 'coder/*' --no-merged in {repo_root} exited "
                           f"{rc}; git's stderr: {err.strip()}")
    names = []
    for line in out:
        name = line.strip().lstrip("*").strip()
        if name:
            names.append(name)
    return sorted(names)


def rows_from_git(repo_root, actor="") -> list:
    """One row per unmerged coder/* branch, read from git as it is at this moment.

    task_id and review_state are empty strings, not guesses: the join to the review queue is a
    separate scope, and a field that LOOKS populated is a record nobody thinks to fix. head is
    the branch's own 40-hex sha -- a short hash would let two sessions recording the same branch
    at different commits be read as the same state.
    """
    rows = []
    for branch in unmerged_branches(repo_root):
        rc, out, err = git("rev-parse", branch, root=repo_root)
        if rc != 0:
            raise RuntimeError(f"git rev-parse {branch} in {repo_root} exited {rc}; "
                               f"git's stderr: {err.strip()}")
        rows.append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "branch": branch,
            "head": out[0].strip(),
            "task_id": "",
            "review_state": "",
            "merged": False,
            "actor": actor,
        })
    return rows


def append_rows(ledger_path, rows) -> int:
    """Append one JSON object per row, one per line; return the number appended.

    Append mode with the parent created: the ledger is a history, so the session that records
    second must not rewrite what the first recorded, and a close running before the folder
    exists still writes. The path is the caller's, so a measurement run can write inside a temp
    dir instead of into the workspace being measured.
    """
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
            n += 1
    return n


def read_rows(ledger_path) -> list:
    """The rows of a ledger in file order; a file that does not exist is an empty population.

    A line that will not decode, or decodes to something that is not an object, is SKIPPED:
    a process killed mid-append leaves a torn final line, and a close-out that dies there skips
    the commit, the compliance baseline and the backup. Skipping is not silence -- every line
    that is passed over is named with its line number, because a record dropped in silence is
    the defect class this file exists to stop (never-truncate-a-record).
    """
    path = Path(ledger_path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    rows = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"  NOTE: {path.name} line {n} is not valid JSON, skipped "
                  f"({type(exc).__name__}: {exc})")
            continue
        if not isinstance(row, dict):
            print(f"  NOTE: {path.name} line {n} is JSON {type(row).__name__}, not an object, "
                  "skipped")
            continue
        rows.append(row)
    return rows


def resolution_rows(ledger_rows, unmerged, actor="", repo_root=None) -> list:
    """One row per recorded branch that has LEFT the unmerged population (t-3c, 2026-10-23).

    A ledger row says "this coder/* branch was unmerged at ts" -- a fact scoped to its
    timestamp. When Claude merges a branch (and usually deletes it), git stops listing it,
    and from the next close on every historical row for it reads as a GHOST: the 24 ghosts
    in workspace/run-logs/branch-ledger.jsonl (1070 rows, counted 2026-10-23) are exactly
    that -- 13 branches deleted after merging, 11 merged with the branch kept. The rows are
    history and history is kept; what the ledger was missing is the END of the lifecycle:
    nothing recorded the merge/delete event, so the append-only record had no way to say
    "this branch's story is finished" and the two-directional check (t753) cried at every
    legitimate merge forever.

    A branch is retired when git no longer lists it as unmerged. `resolution` is "merged"
    when git can still resolve the name (the head is the branch's current commit) and
    "deleted" when it cannot (the head is the branch's last head recorded in the ledger --
    never blank: a row whose head is a guess poisons the append-only record). A branch with
    no recorded row is not resolved: it has no history to close, and a fabricated head would
    be invented data. Idempotent: a branch with a resolution row is never re-resolved.
    """
    known = set(unmerged)
    last = {}
    for r in ledger_rows:
        if isinstance(r, dict) and r.get("branch"):
            last[r["branch"]] = r              # file order: later rows overwrite earlier ones
    retired = [b for b, r in last.items()
               if b not in known and not r.get("resolution")]
    rows = []
    for branch in sorted(retired):
        prev = last[branch]
        rc, out, _err = git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}",
                            root=repo_root)
        head = out[0].strip() if rc == 0 and out and re.fullmatch(r"[0-9a-f]{40}", out[0].strip()) \
            else prev.get("head", "")
        rows.append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "branch": branch,
            "head": head,
            "task_id": prev.get("task_id", ""),
            "review_state": prev.get("review_state", ""),
            "merged": True,
            "resolution": "merged" if rc == 0 and head else "deleted",
            "actor": actor,
        })
    return rows


def reconcile(branches, rows) -> dict:
    """Where git and the ledger disagree, in BOTH directions.

    "unrecorded" is the population with no row -- the count nobody can reproduce. "ghosts" is
    rows naming branches git no longer reports AND no resolution row retires; a one-directional
    check (rows are a subset of the branches) passes those silently, which is how a ledger ends
    up describing a population that no longer exists. A branch closed by a resolution row
    (merged/deleted -- t-3c) is REPORTED IN "resolved", not as a ghost: it is not a defect,
    it is the ledger working. "seen" (resolved) and "ghost" (never seen) stay distinct --
    resolving is a record, not a silencing: a row for a branch the ledger never recorded and
    never resolves remains a ghost. ok is True only when both defect lists are empty.
    """
    known = set(branches)
    recorded = {r.get("branch") for r in rows if isinstance(r, dict)}
    resolved = {r.get("branch") for r in rows if isinstance(r, dict) and r.get("resolution")}
    unrecorded = sorted(b for b in known if b not in recorded)
    ghosts = sorted(b for b in recorded if b not in known and b not in resolved)
    return {"unrecorded": unrecorded, "ghosts": ghosts,
            "resolved": sorted(b for b in recorded if b not in known and b in resolved),
            "ok": not unrecorded and not ghosts}


def changed_paths():
    """Every path touched this session: uncommitted, plus unpushed commits."""
    _, dirty, _ = git("status", "--porcelain")
    files = [re.sub(r"^.{3}", "", d).strip('"') for d in dirty]
    rc, unpushed_files, _ = git("diff", "--name-only", "@{u}..HEAD")
    if rc == 0:
        files += unpushed_files
    return dirty, files


# The vocabulary is CLOSED on purpose. "seen" -- the disposition a session reaches for when
# it has looked at a population and done nothing -- is the state RCA 31c25f11372a is about, so
# it cannot be an answer to it. A list that grows to admit "noted" or "ack'd" is the gate
# letting the population through.
ALLOWED_DISPOSITIONS = ("triaged", "deferred", "for-jas", "refuted")

# The two dispositions that mean "not now" are only dispositions if there is a task to find
# them in. A deferral with no task id re-opens the same population next session with nothing
# to look up, which is exactly how the population came to be unexamined.
DISPOSITIONS_NEEDING_TASK = ("deferred", "for-jas")


RUNS_LOG = REPO_ROOT / "workspace" / "session-end-runs.jsonl"
sweep_state = {}


def _log_run(started, blockers):
    """One row per session_end run (2026-10-13: the last three each needed reruns and nothing recorded it)."""
    try:
        with RUNS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"started": started, "ended": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "blockers": blockers, "sweep": sweep_state.get("ran", "not reached")}) + "\n")
    except OSError as e:
        print(f"  NOTE: run not logged ({type(e).__name__}: {e})")


def population_blockers(population_rows, disposition_rows):
    """Every announced item carrying no disposition a close-out is allowed to accept.

    Pure: no file is read and nothing is printed -- the records come in, the verdicts come
    out, so the rule can be argued with without a disk. Rows that are not objects are
    skipped: they are written by a recorder outside this file, which this file cannot trust.
    Returns [{"population", "item", "why"}] sorted by (population, item); [] means the
    close-out may proceed.
    """
    by_item = {}
    for row in disposition_rows:
        if not isinstance(row, dict):
            continue
        item = row.get("item")
        if not isinstance(item, str) or not item:
            continue
        by_item.setdefault(item, []).append(row)

    out = []
    for row in population_rows:
        if not isinstance(row, dict):
            continue
        items = row.get("items")
        if not isinstance(items, list):
            continue
        name = row.get("population")
        for item in items:
            key = item if isinstance(item, str) and item.strip() else str(item)
            why = _disposition_gap(by_item.get(key, []))
            if why:
                out.append({"population": name if isinstance(name, str) else "",
                            "item": key, "why": why})
    return sorted(out, key=lambda b: (str(b["population"]), str(b["item"])))


def _disposition_gap(rows):
    """Why this item is still open, or '' when one of these rows closes it.

    A row closes it when the disposition is in the closed vocabulary, and -- for the two that
    mean "not now" -- only when a task id is present to be looked up by. The reason named is
    the first defect found, not the last: telling someone they recorded nothing when a 'seen'
    row is on disk sends them to fix the wrong thing.
    """
    unrecognised = []
    taskless = []
    for row in rows:
        disposition = row.get("disposition")
        if disposition not in ALLOWED_DISPOSITIONS:
            unrecognised.append(disposition)
            continue
        task = row.get("task")
        if disposition in DISPOSITIONS_NEEDING_TASK and not (isinstance(task, str) and task.strip()):
            taskless.append(disposition)
            continue
        return ""
    if taskless:
        return f"{taskless[0]} without a task id"
    if unrecognised:
        return f"unrecognised disposition {unrecognised[0]!r}"
    return "no disposition recorded"


def _read_jsonl(path):
    """The JSON-object rows in a .jsonl file, plus every line that is not one.

    An absent file reads as NO ROWS, not as a failure: a session that announced no population
    has nothing to account for, and a gate that blocks on a file nobody wrote is a gate people
    learn to route around. A line that fails to parse, or parses to something that is not an
    object, comes back with its line number for the caller to count and say out loud -- a
    record dropped in silence is the defect this whole check exists to stop.
    """
    import json
    rows, bad = [], []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return rows, bad
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            bad.append(f"{Path(path).name} line {n}: not valid JSON ({type(exc).__name__})")
            continue
        if not isinstance(row, dict):
            bad.append(f"{Path(path).name} line {n}: JSON {type(row).__name__}, not an object")
            continue
        rows.append(row)
    return rows, bad


PATH_RE = re.compile(r"[A-Za-z0-9._/~-]+\.(?:csv|json|jsonl|md|py|txt)")

# The phrasings are verbatim from the RCA that motivated this (workspace/rca/6bd275fb9ae7.md),
# not a general retirement vocabulary: a phrase nobody has ever written would produce records
# for decisions that were never made.
RETIRE_PHRASES = (
    "we decided to delete",
    "we aren't using",
    "serve no purpose",
    "no longer needed",
    "delete those",
    "not using projects in Chat",
)


def record_retirements(turns, session, out_path=None, task="", deleted="no") -> int:
    """Append this session's file-retirement decisions to a ledger. Returns rows written.

    The close-out is the last moment a session knows which files its own sentences were
    talking about. Nine days after "we decided to delete those CSV files" there was nothing on
    disk saying so, which is the only way a file's live state becomes unprovable (t809).

    A session with no retirement language returns 0 WITHOUT creating, touching or mkdir-ing
    the ledger: a file that exists only because the endpoint ran records nothing and reads,
    to the next reader, as evidence that was never gathered.
    """
    # Imported here, not at the top: this module's top of file imports only `date`, and a
    # ledger written by a session that found nothing must not need the cost of either.
    from datetime import datetime, timezone
    import json

    rows = []
    for turn in turns:
        # A turn that is not a dict, has no role, has no text, has a non-string text, or
        # matches no phrase is SKIPPED, never a raise: transcript shapes come from outside
        # this file and a close-out cannot trust them.
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        text = turn.get("text")
        if role not in ("user", "assistant") or not isinstance(text, str) or not text:
            continue
        low = text.lower()
        reason = [p for p in RETIRE_PHRASES if p in low]
        if not reason:
            continue
        rows.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": session,
            "quote": text,  # the whole turn, never sliced: a cut quote is not evidence
            "actor": role,
            "paths": PATH_RE.findall(text),
            "reason": ", ".join(reason),
            "task": task,
            "deleted": deleted,
        })

    if not rows:
        return 0

    if out_path is None:
        out_path = REPO_ROOT / "workspace" / "retirements.jsonl"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    # The count and the path are said out loud, because a record written in silence is a
    # record nobody acts on.
    print(f"  recorded {len(rows)} retirement decision(s) in {out_path}")
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default=None,
                    help="Agent trailer, e.g. claude-code/opus-5 or human/jas")
    ap.add_argument("--message", default=None)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--fast", action="store_true",
                    help="skip the routines a timer already owns (and print what was skipped and how to run it)")
    ap.add_argument("--run-corpus", dest="run_corpus", action="store_true",
                    help="index this session into the corpus now, even if the timer is fresh")
    ap.add_argument("--run-apps", dest="run_apps", action="store_true", help="run the app pipeline now")
    ap.add_argument("--run-digests", dest="run_digests", action="store_true",
                    help="run the watched-corpus digests now")
    ap.add_argument("--force-check", dest="force_check", action="store_true",
                    help="pay the full workspace check again even though this exact world was already checked")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--audit", action="store_true",
                    help="Jas asked for this session to be audited: queue a script audit for it (t790)")
    args = ap.parse_args()

    print()
    print("=" * 62)
    print(f" SESSION END — {REPO_ROOT}")
    print("=" * 62)

    blockers = 0
    _started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # --- 1. Is there anything to record? ---------------------------------------------
    section("1. What changed this session")

    dirty, changed = changed_paths()
    rc, unpushed, _ = git("log", "@{u}..HEAD", "--oneline")
    unpushed = unpushed if rc == 0 else []

    # A SESSION THAT COMMITS AND PUSHES AS IT GOES HAS NEITHER, AND IS STILL A SESSION.
    #
    # This asked "is there uncommitted or unpushed work?" and treated no to both as no
    # session. On 2026-08-31 that skipped the close-out of the longest session on record --
    # around eighty commits, all pushed as they were made, and therefore invisible here. The
    # log is the handover, so the reward for pushing promptly was no handover at all.
    #
    # The property is not "is work outstanding" but "did this session commit anything". Ask
    # git, against the last session log: commits newer than it are this session's, and the
    # log is written at close, so the boundary is exact and needs nothing remembered.
    logs = sorted((REPO_ROOT / "workspace" / "sessions").glob("*.md"),
                  key=lambda f: f.stat().st_mtime, reverse=True)
    since = []
    if logs:
        rc_l, out_l, _ = git("log", "-1", "--format=%cI", "--", "workspace/sessions/")
        if rc_l == 0 and out_l:
            rc_s, since, _ = git("log", "--oneline", f"--since={out_l[0]}")
            since = since if rc_s == 0 else []

    if not dirty and not unpushed and not since:
        print()
        print(_c("32", "  Nothing changed and nothing unpushed. No session to close."))
        print()
        return 0

    if since and not dirty and not unpushed:
        print(f"  {len(since)} commit(s) since the last session log, all pushed")

    if dirty:
        print(f"  {len(dirty)} uncommitted path(s)")
        for d in dirty[:12]:
            print(_c("90", f"    {d}"))
    if unpushed:
        print(f"  {len(unpushed)} commit(s) not yet pushed")

    touched_projects = sorted({
        f.split("/")[1] for f in changed if re.match(r"^projects/[^/]+/", f)
    })

    # --- 2. Did you leave it findable? -----------------------------------------------
    section("2. Will the next session know what happened?")

    # STATUS.md was a warning until 2026-08-25, and the audit found fourteen commits that
    # walked straight past it. A warning nobody acts on is not a check, so this now stamps
    # a dated pointer line itself and only complains if that fails. The stamp is factual --
    # date, file count, link to the log -- because a generated summary of what a change
    # MEANT would be indistinguishable from one a person wrote.
    for p in touched_projects:
        if f"projects/{p}/STATUS.md" in changed:
            ok(f"{p} — STATUS.md updated")
        else:
            warn(f"{p} changed but projects/{p}/STATUS.md was not updated")
            print(_c("90", "         STATUS.md is the only current document for a project."))
            print(_c("90", "         A dated pointer will be stamped in below."))
    if not touched_projects:
        print(_c("90", "  No project folders touched."))

    if "STATE.md" in changed:
        ok("STATE.md updated")
    else:
        bad("STATE.md was not updated")
        print(_c("90", "         This is the file the next session reads first. If it is stale,"))
        print(_c("90", "         the whole system fails silently. Update it, then re-run."))
        blockers += 1

    # The log is DRAFTED here rather than demanded. Blocking on a missing log has been in
    # this script since it was written, and the audit on 2026-08-25 still found three days
    # with commits and no log -- a blocker does not produce a log, it produces a session
    # that closes some other way. So the skeleton and every fact git can supply are written
    # automatically, and what remains is the part no script can write.
    sessions_dir = REPO_ROOT / "workspace" / "sessions"
    logs = sorted(sessions_dir.glob(f"{TODAY}-*.md")) if sessions_dir.is_dir() else []
    if not logs:
        subprocess.run([sys.executable, str(SCRIPTS / "session_log.py"),
                        "--stamp-status"], capture_output=True, timeout=120)
        logs = sorted(sessions_dir.glob(f"{TODAY}-*.md")) if sessions_dir.is_dir() else []
        if logs:
            warn(f"no session log existed — drafted {logs[0].name}")
    else:
        subprocess.run([sys.executable, str(SCRIPTS / "session_log.py"),
                        "--stamp-status"], capture_output=True, timeout=120)

    r = subprocess.run([sys.executable, str(SCRIPTS / "session_log.py"), "--check"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode == 0:
        ok(f"session log complete — {logs[0].name}" if logs else "session log complete")
    else:
        bad("the session log is not finished:")
        for line in (r.stdout or "").splitlines():
            if line.strip():
                print(_c("90", f"       {line.strip()}"))
        print(_c("90", "         The skeleton and the git-derived facts are already written."))
        print(_c("90", "         Fill the TODO sections -- that is where a future session"))
        print(_c("90", "         looks for WHY, which git log does not carry."))
        blockers += 1
    dirty, changed = changed_paths()

    # --- 2a. THE HANDOVER (t265) ------------------------------------------------------
    #
    # CLAUDE.md has claimed since it was written that "session_end.py writes and pushes the
    # handover while the outgoing session still has full context. The order is what makes it
    # safe." IT NEVER DID. Nothing in this script had ever written a workspace/HANDOVER-*.md;
    # the only occurrences of the word were three comments and check_handover_read.py, which
    # verifies this session READ the previous one -- the opposite direction. So every handover
    # on disk was hand-written by a session that happened to remember, and the safety property
    # the contract describes rested on somebody's attention.
    #
    # THE STALE HALF IS WORSE THAN THE MISSING HALF. session_start.py names "the newest
    # workspace/HANDOVER-*.md" and tells the incoming session to read it IN FULL. When nobody
    # wrote one, the next session is handed an OLD handover with nothing saying so. On
    # 2026-09-11 the newest was 2026-09-09, written 11:27, while the work it described ran on
    # until 16:25 -- so its central claim was superseded by commits made the same afternoon,
    # and that was caught only by comparing the file's commit date against the log.
    #
    # DRAFT THEN BLOCK, exactly as the session log above does, and for the reason recorded
    # there: a blocker does not produce a handover, it produces a session that closes some
    # other way. The skeleton is written automatically; what is demanded is the part no script
    # can write.
    import handover as _handover
    hpath = REPO_ROOT / "workspace" / f"HANDOVER-{TODAY}.md"
    # t325: keyed on THIS session, so an earlier session's handover of the same day cannot stand in
    # for this one. '' (a human session, no id) keeps the day-keyed check.
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("DSH_SESSION_ID") or ""
    if not hpath.exists():
        hpath.write_text(_handover.draft(REPO_ROOT, TODAY, session_id), encoding="utf-8")
        warn(f"no handover existed — drafted {hpath.name}")
    problems = _handover.check(REPO_ROOT, TODAY, session_id)
    if not problems:
        ok(f"handover complete — {hpath.name}")
    else:
        bad("the handover for today is not finished:")
        for line in problems:
            print(_c("90", f"       {line}"))
        print(_c("90", "         This is the file session_start.py hands the NEXT session and"))
        print(_c("90", "         tells it to read in full. Unwritten, it hands over a stale one"))
        print(_c("90", "         instead, with nothing marking it stale (t265)."))
        blockers += 1
    dirty, changed = changed_paths()

    # --- 3. Regenerate the index ------------------------------------------------------
    section("3. Rebuilding PROJECT-INDEX.md")

    # Before the check rather than after, so a stale index is impossible rather than
    # merely reported. It is what Claude Chat reads instead of crawling the tree.
    if subprocess.run([sys.executable, str(SCRIPTS / "build_index.py")], timeout=600).returncode != 0:
        warn("index generation failed — Chat will read a stale view")
    else:
        dirty, changed = changed_paths()

    # --- 3a. Skills ---------------------------------------------------------------------
    section("3a. Did this session teach anything reusable?")

    # THIS IS THE MECHANISM THAT PROMPTS FOR A SKILL, and it lives here on purpose.
    #
    # Jas, 2026-09-03: *"what is the mechanism that will prompt you automatically to make a
    # skills file? I think something when you do hand overs needs that prompt."* He is right
    # that it belongs at the handover: that is the moment a session knows what it learned and
    # the last moment it can write it down. A rule in AGENTS.md is read at the START, before
    # there is anything to record, and by the end it has scrolled out of anyone's attention.
    #
    # It ASKS rather than blocks. Most sessions genuinely teach nothing reusable, and a gate
    # that fires every time is a gate that gets waved through -- which is `G10`, a check
    # getting ignored because it cries wolf.
    subprocess.run([sys.executable, str(SCRIPTS / "skills.py"), "index"], timeout=1800)
    _touched = [c for c in changed if c.startswith("docs/skills/")]
    if _touched:
        ok(f"skills changed this session: {len(_touched)} file(s)")
    else:
        warn("no skill was written or updated this session")
        print("       A lesson records what went WRONG. A skill records how to do it RIGHT,")
        print("       and it is the only artefact that reaches a session BEFORE the mistake.")
        print()
        print("       Ask: would the next session get this wrong without it? If the know-how")
        print("       is in your head at the end of today and nowhere else, that is a yes.")
        print()
        print("       python3 scripts/skills.py list")
        print("       python3 scripts/skills.py new <name> --description \"when to reach for this\"")
        print()
        print("       Not every session teaches something reusable. Say so and move on if not.")

    # --- 3b. This session into the corpus ---------------------------------------------
    section("3b. Indexing this session into the corpus")

    # corpus-index.timer already does this hourly, and that is not enough at session end.
    # The last stretch of a session is the densest -- the decisions, the handover, the
    # reasoning behind them -- and it is exactly the part that would sit unindexed until
    # the next tick, or forever if the machine sleeps. Jas asked for this after watching a
    # compaction: the raw .jsonl is never at risk, the SEARCHABLE copy is.
    #
    # It also takes the daily NAS bundle when one has not been made yet, and skips quietly
    # when the AFP share is not mounted -- a routine that fails the whole session-end
    # because a network share dropped is a routine people learn to ignore.
    _corpus_run, _corpus_line = routine_decision("corpus", asked=getattr(args, "run_corpus", False),
                                                 fast=getattr(args, "fast", False))
    print(_c("90", _corpus_line))
    if _corpus_run:
        r = subprocess.run([sys.executable, str(SCRIPTS / "corpus_routine.py")],
                           capture_output=True, text=True, timeout=3600)
        for line in (r.stdout + r.stderr).strip().splitlines()[-4:]:
            print(f"  {line}")
        if r.returncode != 0:
            warn("the corpus routine failed — this session may not be searchable next time")
        else:
            ok("this session is in the transcript index")

    # --- 3c. Files whose purpose ended ---------------------------------------------------
    #
    # The decision to stop using a file lives in the transcript and nowhere else, and the
    # transcript sits at a per-machine path (~/.claude/projects) this script does not
    # control. So WARN and continue: a close that dies here skips the commit, the compliance
    # baseline and the NAS bundle, trading one missing record for the whole close-out.
    try:
        sys.path.insert(0, str(SCRIPTS))
        import transcripts as _tr
        _dir = _tr.SESSIONS / _tr._workdir_key()
        _files = sorted(_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime,
                        reverse=True) if _dir.is_dir() else []
        if _files:
            _sid, _rows = _tr.read_session(_files[0])
            _turns = [{"role": r["role"], "text": r["text"]} for r in _rows
                      if r.get("role") in ("user", "assistant") and r.get("text")]
            # The env-var pair is the one section 2a's handover already keys the session on,
            # so the ledger and the handover name the same session.
            record_retirements(
                _turns,
                session=os.environ.get("CLAUDE_CODE_SESSION_ID")
                or os.environ.get("DSH_SESSION_ID") or "",
                task="")
    except Exception as e:                                          # noqa: BLE001
        print(_c("33", f"  WARN: retirements not recorded: {type(e).__name__}: {e}"))

    # --- 4. Invariants ----------------------------------------------------------------
    section("4. Full workspace check")

    # --- 4b. The unmerged-branch population (t753) ---------------------------------------
    #
    # The population git can count and no file records: "14 branches unmerged" is a prose figure
    # with no artefact behind it, and the merge work it named has had no owner for nine days.
    # So the close-out asks git, appends one row per unmerged coder/* branch, and says the
    # reconciliation OUT LOUD -- a ledger written in silence is a population nobody acts on.
    # It runs before the commit so the rows land in this session's history.
    section("4b. Which coder/* branches are unmerged?")
    _actor = args.agent or os.environ.get("WORKSPACE_AGENT") or ""
    try:
        branches = unmerged_branches(REPO_ROOT)
        rows = rows_from_git(REPO_ROOT, actor=_actor)
        n_rows = append_rows(BRANCH_LEDGER, rows)
        # t-3c (2026-10-23): record the END of the lifecycle too. A recorded branch that is
        # no longer unmerged was merged (and often deleted); without a resolution row its
        # history reads as a ghost at every future close -- 24 such ghosts were on the live
        # ledger when this shipped. Appended after the unmerged rows, in the same ledger.
        _ledger_rows = read_rows(BRANCH_LEDGER)
        res_rows = resolution_rows(_ledger_rows, branches, actor=_actor, repo_root=REPO_ROOT)
        n_res = append_rows(BRANCH_LEDGER, res_rows)
        report = reconcile(branches, read_rows(BRANCH_LEDGER))
        print(f"  recorded {n_rows} unmerged-branch row(s) in "
              f"{BRANCH_LEDGER.relative_to(REPO_ROOT)}")
        if res_rows:
            print(f"  recorded {n_res} branch RESOLUTION row(s) "
                  f"({', '.join(sorted(r['resolution'] + ':' + r['branch'] for r in res_rows))})")
        print(f"  reconcile: {json.dumps(report, sort_keys=True)}")
        if not report["ok"]:
            warn(f"git and the branch ledger disagree -- {len(report['unrecorded'])} branch(es) "
                 f"with no row, {len(report['ghosts'])} row(s) for branches git no longer lists")
    except Exception as e:                                            # noqa: BLE001
        # RECORDED, not swallowed: the exception text carries git's stderr, because a git failure
        # printed as nothing must never read as "no unmerged branches" -- a population of zero
        # manufactured by the tool that measures it.
        print(_c("31", f"  branch ledger NOT written: {type(e).__name__}: {e}"))
    # PAID ONCE PER WORLD STATE (2026-10-30). Three close-out attempts in one evening paid this 10-16 minute check
    # three times for a tree that had not changed; the world the check measures was identical, so the second answer
    # could only be the first one. --force-check pays it again on purpose.
    rc_check, reused = full_check(force=getattr(args, "force_check", False))
    if reused:
        print(_c("32" if rc_check == 0 else "31",
                 "  this exact world was already checked in this session, and the answer is the same one: "
                 + ("PASS" if rc_check == 0 else "FAIL")
                 + " (pay for it again with --force-check)"))
    if rc_check != 0:
        bad("the workspace check failed — fix the document or the disk, not the check")
        blockers += 1

    # --- 4a. What this session was handed at start (t762) -------------------------------
    #
    # Everything above asks what this session PRODUCED. Nothing asked what it was HANDED.
    # session_start.py announces populations as counts and records them in
    # workspace/session-start-population.jsonl, and no surface read that file -- so a session
    # could close with every announced member unexamined, which is the RCA's class (31c25f11372a,
    # section 5): a population announced with no per-item obligation and no per-item record.
    #
    # So the close-out reads the ledger, keeps the rows announced to THIS session, and refuses
    # to pass while any announced item carries no disposition in the closed vocabulary.
    # "seen, not examined" has to be a state a close-out cannot pass in.
    pop_path = REPO_ROOT / "workspace" / "session-start-population.jsonl"
    disp_path = REPO_ROOT / "workspace" / "session-population-dispositions.jsonl"
    import json as _json
    pop_rows, pop_bad = _read_jsonl(pop_path)
    disp_rows, disp_bad = _read_jsonl(disp_path)
    # Keyed on the session id section 2a's handover already keys on: one session's close-out
    # cannot clear a population a different session was handed. No id, no claim.
    pop_rows = [r for r in pop_rows if session_id and r.get("session") == session_id]
    gates = population_blockers(pop_rows, disp_rows)
    if pop_rows or pop_bad or disp_bad:
        section("4a. Did you account for every item you were handed?")
    for msg in pop_bad + disp_bad:
        # Counted and named, never skipped: a population row that does not parse is a member
        # nobody examined, and reading it as absent is the false pass this gate exists to stop.
        warn(f"population record not parsed: {msg} (counted, not skipped)")
    for g in gates:
        warn(f"population {g['population']!r}: {g['item']} -- {g['why']}")
    if gates:
        # FLAGGED IN THE HANDOVER, NOT ENFORCED (Jas, 2026-10-12: "T762 should do the check but flag
        # it for the handover instead of enforcing a decision"). The check runs and names every
        # announced item with no disposition; the list is appended to today's handover so the next
        # session sees it. The close-out is NOT refused over it.
        try:
            with open(hpath, "a", encoding="utf-8") as _h:
                _h.write("\n## Announced at session start, no disposition recorded (t762)\n\n"
                         + "".join(f"- {g['population']}: {g['item']} -- {g['why']}\n" for g in gates))
        except OSError as e:
            warn(f"could not add the t762 list to the handover: {e}")
        print(_c("90", f"         {len(gates)} item(s) in "
                       f"{len({g['population'] for g in gates})} population(s) were announced to "
                       "this session and carry no disposition a close-out may accept."))
        print(_c("90", "         Allowed dispositions: " + ", ".join(ALLOWED_DISPOSITIONS) + "."))
        print(_c("90", "         'seen' is not one of them: a disposition that says seen is the"))
        print(_c("90", "         defect, not the cure. Record one row per item, then re-run:"))
        _example = {"session": session_id, "population": gates[0]["population"],
                    "item": gates[0]["item"], "disposition": "triaged"}
        _deferred = dict(_example, disposition="deferred", task="tNNN")
        print(_c("90", f"         echo '{_json.dumps(_example)}' >> {disp_path.relative_to(REPO_ROOT)}"))
        print(_c("90", f"         echo '{_json.dumps(_deferred)}' >> {disp_path.relative_to(REPO_ROOT)}"))
        print(_c("90", "         (the second form is the only one that defers anything: 'deferred'"))
        print(_c("90", "         and 'for-jas' with no task id are not dispositions)"))

    # --- 5. Commit and push -----------------------------------------------------------
    # The app pipeline. Run at CLOSE rather than at start: it shells out to apksigner for
    # every published APK, which is emulated on this machine (L-080), and a session start
    # should be fast. Once per session is enough to catch a catalogue that drifted.
    section("5. App pipeline")
    apps_check = REPO_ROOT / "scripts" / "check_apps.py"
    _apps_run, _apps_line = routine_decision("apps", asked=getattr(args, "run_apps", False),
                                             fast=getattr(args, "fast", False))
    print(_c("90", _apps_line))
    if not _apps_run:
        pass
    elif apps_check.exists() and (Path.home() / "app-repo").is_dir():
        r = subprocess.run([sys.executable, str(apps_check)], capture_output=True, text=True, timeout=600)
        for line in (r.stdout or "").splitlines():
            print(line)
        if r.returncode == 2:
            bad("app pipeline check FAILED -- see above")
    else:
        print(_c("90", "  No app repo on this machine; skipped (not a pass)."))

    # The deployment path, which no file-reading check can see. Added 2026-08-25 after a
    # fix sat on disk for an hour while the bridge served the old code and every other
    # check reported PASS.
    # BOTH OF THESE WERE WRITTEN WITH AN "ENFORCEMENT:" LINE IN docs/LESSONS.md SAYING
    # session_start AND session_end RUN THEM, AND NEITHER WAS WIRED TO ANYTHING. L-146 said
    # check_vacuous.py runs here; it did not. That is a false enforcement claim -- the exact
    # defect class both checks exist to catch -- committed by the person who wrote them.
    section("5c. Claims that must still hold")
    # THE TEST SUITES, WHICH NOTHING RAN UNTIL 2026-09-03. Forty-four of them sat on disk,
    # three failing, and check_workspace.py only READ them -- verifying each proves a failure
    # and not merely a pass, which reads exactly like coverage while nothing was executed.
    # Six new guards written that day would have been decorative. The runner honours a
    # ceiling, so a pre-existing failure does not block a session but a NEW one does.
    # FAIL FAST (Jas, 2026-10-13: "do the failure fast and resume updates"): the cheap checks run first
    # and the ~40-minute sweep runs LAST, and only when nothing above has blocked -- every one of the last
    # three session ends found a blocker in seconds and still ran the whole sweep before saying so.
    for name, why in (("check_handover_read.py",
                       "this session read the handover before doing work"),
                      ("check_vacuous.py", "every verdict site is accounted for (L-146)"),
                      ("check_measurements.py", "every figure has its provenance (L-147)"),
                      # ALWAYS EXITS 0 BY DESIGN, so it can never add a blocker. It reports
                      # intentions stated to Jas in prose with no task id -- his fix, after
                      # a status line said "I'll be checking" and nothing had been checked:
                      # "perhaps making a task. Then when you confirm the task number I will
                      # see it won't be missed." It runs HERE because this is the last moment
                      # a session can still file one, and because a rule in AGENTS.md is read
                      # before there is anything to declare. Same argument as the skill prompt
                      # in section 3a.
                      ("check_declared_intent.py",
                       "intentions stated to Jas carry a task id he can look up"),
                      ("run_tests.py", "every test suite in scripts/tests/ still passes")):
        # A CEILING ON THE WAIT (7200 s): a check that hangs past two hours is reported as a failed
        # check, not waited on for ever. (The sweep itself never runs here -- see below.)
        def _check(name=name):
            try:
                return subprocess.run([sys.executable, str(SCRIPTS / name)], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, timeout=7200)
            except subprocess.TimeoutExpired as e:
                return subprocess.CompletedProcess(e.cmd, 124, stdout=f"{name} TIMED OUT after 7200 s\n", stderr="")
        # THE SWEEP RUNS IN THE BACKGROUND, NOT HERE (Jas, 2026-10-19: "My goal is to start a new session
        # and if this can happen independently, that would be great ... schedule this along with general
        # production"). It took 40-120 min per run and a session end ~3 h across reruns. The commit hook
        # already runs the related suites for every staged script; the full sweep is the backstop. So
        # session_end starts the hourly check's run now, without waiting: run_tests --cache, niced,
        # production NOT paused, and a new failure becomes ONE task at the front of the queue
        # (hourly_checks.py). The session commits and pushes in minutes.
        if name == "run_tests.py":
            started = subprocess.run(["systemctl", "--user", "start", "--no-block", "hourly-checks.service"],
                                     capture_output=True, text=True, timeout=30)
            if started.returncode == 0:
                print(_c("32", "  test sweep: started in the background (hourly-checks.service) -- it does not "
                               "block this session; a new failure becomes one task at the front of the queue. "
                               "Progress on the Lanes tab."))
                sweep_state["ran"] = "background"
            else:
                print(_c("33", f"  test sweep: could not start hourly-checks.service ({started.stderr.strip()}); "
                               "the hourly timer runs it within the hour."))
                sweep_state["ran"] = "background: timer"
            continue
        r = _check()
        for line in (r.stdout or "").splitlines():
            if line.strip():
                print(line)
        if r.returncode != 0:
            blockers += 1
            print(_c("31", f"     {name} FAILED -- {why}"))

    section("6. Is the running bridge the code we just committed?")
    r = subprocess.run([sys.executable, str(SCRIPTS / "check_deployed.py")],
                       capture_output=True, text=True, timeout=600)
    for line in (r.stdout or "").splitlines():
        if line.strip():
            print(line)
    # TWO DIFFERENT FAILURES, AND THEY NEED DIFFERENT SENTENCES. check_deployed exits 1
    # when the bridge serves code that does not match source -- genuinely stale, restart it.
    # It exits 2 when it could not REACH the bridge, which it began doing after probe
    # finding 8: that path printed "Nothing was checked -- this is not a pass" and then
    # returned 0, a false pass on the deployment check.
    #
    # Both must block, because neither one lets you claim the deployment was verified. But
    # telling someone to restart a bridge that is not running, or is not on this machine at
    # all, sends them to fix the wrong thing -- and a check that misdiagnoses is the failure
    # mode of finding 17, one file over.
    if r.returncode == 2:
        bad("could not reach the bridge -- the deployment is UNVERIFIED, not confirmed. "
            "Nothing was checked, so this is not a pass")
        blockers += 1
    elif r.returncode:
        bad("the bridge is stale -- restart it before claiming anything is fixed")
        blockers += 1

    # chat/ is gitignored (D-22), so nothing else in this repo can tell you a ledger or
    # PROTOCOL.txt changed. Reported, never auto-recorded: silently re-baselining would
    # turn the witness into a rubber stamp.
    section("7. Corpus digests")
    _dig_run, _dig_line = routine_decision("digests", asked=getattr(args, "run_digests", False),
                                           fast=getattr(args, "fast", False))
    print(_c("90", _dig_line))
    if _dig_run:
        r = subprocess.run([sys.executable, str(SCRIPTS / "digests.py")],
                           capture_output=True, text=True, timeout=600)
        for line in (r.stdout or "").splitlines():
            if line.strip():
                print(line)
        if r.returncode:
            warn("a watched corpus file changed -- confirm it was intended, then --write")

    # The outbox had no delivery signal at all until 2026-08-25. Regenerate the index
    # before committing so a message written this session is listed the moment Chat looks.
    subprocess.run([sys.executable, str(SCRIPTS / "outbox_index.py"), "--write"],
                   capture_output=True, timeout=300)
    dirty, changed = changed_paths()

    section("8. Commit and push")

    _log_run(_started, blockers)
    if blockers:
        print()
        print(_c("31", f"  {blockers} blocker(s). Nothing committed, nothing pushed."))
        print(_c("31", "  Fix the items marked [STOP] above and run this again."))
        print()
        return 1

    agent = args.agent or os.environ.get("WORKSPACE_AGENT")
    if not agent:
        print(_c("33", "  Who is committing? e.g. claude-code/opus-5, codex/gpt-5, human/jas"))
        try:
            agent = input("  Agent: ").strip()
        except EOFError:
            agent = ""
    if not agent_trailer_ok(agent):
        bad(f"'{agent}' is not a valid Agent trailer (<tool>/<model>). Nothing committed.")
        return 1

    message = args.message or f"session: {TODAY} close out session"
    if not re.match(rf"^({VALID_TYPES})(\([a-z0-9._-]+\))?: .+", message):
        bad(f"'{message}' does not match <type>(<project>): <summary>. Nothing committed.")
        return 1

    if args.dry_run:
        print()
        print(_c("90", "  DRY RUN — would commit:"))
        print(_c("90", f"    {message}"))
        print(_c("90", f"    Agent: {agent}"))
        print()
        return 0

    if dirty:
        # EXACTLY WHAT THIS RUN FOUND, NEVER `git add -A` (2026-10-30). Two sessions can be live on this box; `-A`
        # swept whichever one finished second into the first one's commit, and when the commit was then refused
        # or killed it left the whole tree staged -- which blocked every merge that touched those paths for hours.
        _rcn, now, _ = git("status", "--porcelain")
        now_paths = [re.sub(r"^.{3}", "", d).strip('"') for d in now]
        paths = [p for p in dict.fromkeys(list(changed) + now_paths) if p and " -> " not in p]
        rc_c, out_c = commit_paths(paths, message, agent)
        if rc_c != 0:
            bad("commit refused — the hooks rejected it:")
            for line in out_c.split("\n"):
                if line:
                    print(_c("90", f"         {line}"))
            return 1
        ok(f"committed: {message}")

    # Record that everything up to here has been audited, so the next session's
    # compliance run starts from a clean baseline instead of re-reporting old findings.
    subprocess.run(
        [sys.executable, str(SCRIPTS / "audit_compliance.py"), "--update-baseline", "--quiet"],
        capture_output=True,
        timeout=1800,
    )
    baseline = REPO_ROOT / "workspace" / ".compliance-baseline"
    if baseline.is_file():
        git("add", "--", "workspace/.compliance-baseline")
        if git("diff", "--cached", "--quiet")[0] != 0:
            msg = f"chore(workspace): record audited baseline\n\nAgent: {agent}\n"
            subprocess.run(
                ["git", "-C", str(REPO_ROOT), "commit", "-F", "-"],
                input=msg, capture_output=True, text=True,
                timeout=1800,
            )
            ok("compliance baseline recorded")

    # THE BACKUP IS THE NAS, NOT GITHUB (Jas, 2026-09-16: "I don't care about Github. Back up
    # to the NAS."). The push URL on `origin` is set to DISABLED-2026-09-16-nas-is-the-backup;
    # fetch still works and the GitHub repo is untouched, so this is one `git remote set-url
    # --push` away from being undone.
    #
    # WHAT ACTUALLY MADE THE PUSH FAIL, recorded here because it was NOT GitHub's fault and
    # the next person will assume it was: git opens the connection to the remote BEFORE it
    # runs pre-push, so the 16-minute sweep in that hook left the SSH session idle until
    # GitHub closed it -- SIGPIPE, exit 141, 33 commits still local. The identical push with
    # the hook skipped took 2.181 seconds. That is fixed separately (run_tests --assert-fresh),
    # so if you re-enable pushing it will work; it is disabled because Jas chose the NAS, not
    # because it is broken.
    if args.no_push:
        warn("not pushed (--no-push). History exists only on this machine — say so in STATE.md.")
    elif push_is_disabled():
        # The bundle is the backup, and it is VERIFIED -- written, then read back cold and
        # compared byte for byte. A copy nobody reads back is not a backup.
        print(_c("90", "  origin has no push URL; the NAS bundle is the backup. Running it."))
        # A TIMEOUT, because the failure mode of an NFS share is to hang rather than to
        # error. nas_backup.sh guards its own first touch (`timeout 120 ls "$DEST"`) but a
        # write that stalls half way through the copy is not covered by that, and an unbounded
        # wait here would hang the close for ever with no backup and no message. The measured
        # run is ~5 minutes for 529 files / 614 MB; 30 is room, not an estimate.
        try:
            r = subprocess.run(["bash", str(SCRIPTS / "nas_backup.sh")],
                               capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            bad("the NAS backup did not finish within 30 minutes — it is hung, not slow. "
                "This history exists only on this machine.")
            return 1
        for line in (r.stdout or "").splitlines()[-6:]:
            if line.strip():
                print(_c("90", f"         {line}"))
        if r.returncode != 0:
            bad("the NAS backup did NOT verify — this history exists only on this machine. "
                "See workspace/backup-runs.jsonl")
            return 1
        ok("backed up to the NAS and verified byte-identical")
    else:
        rc, out, err = git("push")
        if rc != 0:
            bad("push failed:")
            for line in (out + err.split("\n")):
                if line:
                    print(_c("90", f"         {line}"))
            return 1
        ok("pushed to origin")

    # SESSION REVIEW (Jas, 2026-09-28): queue this session for the manager, which reads it in the
    # priority lane for work put off without a task and problems left unfiled, then runs an in-depth
    # RCA per finding and files tasks at the top of QUEUE.md. Queueing is instant; nothing waits.
    # Works from the DeepSeek harness too ($DSH_SESSION_ID picks that session).
    try:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import session_review
        q = session_review.enqueue_current()
        print(_c("90", f"  session review: {'queued ' + q if q else 'already queued or nothing to queue'}"))
    except Exception as e:                                          # noqa: BLE001
        print(_c("33", f"  WARN: session review not queued: {type(e).__name__}: {e}"))
    # "AUDIT THIS SESSION" (t790 step 4): Jas asks, and the SCRIPT identifies the session -- the env
    # var, never Claude's memory -- and queues a script audit for it, by jas, after its review.
    if getattr(args, "audit", False):
        try:
            import script_audit
            sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("DSH_SESSION_ID") or ""
            if not sid:
                print(_c("33", "  WARN: --audit: no CLAUDE_CODE_SESSION_ID / DSH_SESSION_ID, so no session to name"))
            else:
                script_audit.file_request(session=sid[:8], by="jas")
        except Exception as e:                                      # noqa: BLE001
            print(_c("33", f"  WARN: script audit not queued: {type(e).__name__}: {e}"))
    _close_section()
    try:
        import close_out_timings as _cot
        _cot.record({
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "agent": agent,
            "session": os.environ.get("DSH_SESSION_ID") or "",
            "total_s": round(time.time() - _RUN_T0, 1),
            "check_s": round(_CHECK_S[0], 1) if _CHECK_S[0] else None,
            "blockers": blockers,
            "sections": dict(_TIMINGS),
        })
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(_c("33", f"  NOTE: close-out timings not recorded ({type(e).__name__}: {e})"))

    print()
    print(_c("90", "-" * 62))
    print(_c("32", " Session closed."))
    print()
    print(_c("90", " One thing left, and no script can do it: tell Jas in plain"))
    print(_c("90", " language what changed, what is verified versus assumed, what is"))
    print(_c("90", " still broken, and what the single next step is."))
    print(_c("90", "-" * 62))
    # Do NOT write a handoff telling the next session to run session_start.py. Jas made the
    # point on 2026-08-25 and he is right: CLAUDE.md is loaded automatically and sends every
    # worker to that script, so an instruction to run it is a paragraph that tells the reader
    # nothing. The state is already on disk; the log above carries what the state cannot.
    print()
    print(_c("90", " The handoff is this repository. The next session runs session_start.py"))
    print(_c("90", " because CLAUDE.md says so — it does not need to be told again."))
    print()
    return 0


def load_review_rows(path) -> list:
    """Read a session-review ledger into a list of dicts, torn lines included.

    A line that will not decode is APPENDED as an "unparseable" row carrying the raw line
    WHOLE -- never sliced, never dropped: a writer cut off mid-line is the evidence of the
    incident this module exists to measure, and dropping the torn record is how a truncated
    write stays invisible (never-truncate-a-record).
    """
    rows = []
    with Path(path).open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                # The parse failure is recorded next to the line, not swallowed: a reader has
                # to be able to tell a torn write from a malformed one.
                rows.append({"at": "", "event": "unparseable", "detail": line.rstrip("\n"),
                             "error": f"{type(e).__name__}: {e}"})
    return rows


def join_queue_ages(rows) -> list:
    """One age row per finding: when its request was queued, when the finding landed, and
    what the ledger recorded against its key. Returns the rows in the order findings appear.

    THE JOIN RUNS THROUGH `request`, NEVER THROUGH `key`. On the real ledger none of the 76
    queued rows carries a key -- a queued row is not keyed -- so keying the join there joins
    nothing, every age comes back empty, and the queue reads as instant. That is the report
    this number exists to replace.

    A finding with no queued row is KEPT with queued "" and age_hours None, never dropped and
    never given a 0.0: an unmeasurable age is a visible gap, a zero is a false pass.

    `covered_by` is read from a row's own `covered_by` field and from nothing else. The
    ledger's `covered` field is an integer COUNT on event="reviewed" rows that carry no key,
    and 0 of the 170 event="covered" rows join to a finding; transposing a count into a field
    that must hold a task id is the defect being recorded, not the join to make.
    """
    rows = [r for r in rows if isinstance(r, dict)]

    def _stamp(text):
        # The ledger writes a trailing Z, which fromisoformat rejects on this interpreter; Z
        # is UTC, so the offset form parses to the same instant.
        return datetime.fromisoformat(text.replace("Z", "+00:00"))

    def _last(key, field):
        """The `field` of the LAST row carrying that field under this finding's key.

        Matched on `key` because disposition and cover are recorded against a finding, not a
        request. Latest by the row's own `at` string, so a finding dispositioned and then
        re-dispositioned reads as its last state, not its first.
        """
        if not key:
            return ""
        value = ""
        stamp = None
        for row in rows:
            if field not in row or row.get("key") != key:
                continue
            at = row.get("at", "")
            if stamp is None or at >= stamp:
                stamp = at
                value = row[field]
        return value

    earliest = {}
    for row in rows:
        if row.get("event") != "queued":
            continue
        at = row.get("at")
        if not isinstance(at, str) or not at:
            continue
        seen = earliest.get(row.get("request"))
        # The EARLIEST queue stamp ages the finding: a request re-queued later must not
        # shorten the time it sat.
        if seen is None or at < seen:
            earliest[row.get("request")] = at

    ages = []
    for row in rows:
        if row.get("event") != "finding":
            continue
        key = row.get("key", "")
        found = row.get("at", "")
        queued = earliest.get(row.get("request"), "")
        age_hours = None
        if queued and isinstance(found, str) and found:
            age_hours = round((_stamp(found) - _stamp(queued)).total_seconds() / 3600.0, 2)
        ages.append({
            "key": key,
            "queued": queued,
            "found": found,
            "age_hours": age_hours,
            "disposition": _last(key, "disposition"),
            "covered_by": _last(key, "covered_by"),
            "kind": row.get("kind", ""),
        })
    return ages


def stale_review_deferrals(ages, max_age_hours=24.0) -> list:
    """The deferrals past the bound with nothing recorded against them.

    All four criteria are required, because any one alone reports findings somebody has
    already acted on, findings that are not deferrals, or findings with no measurable age.
    The bound is strict: a finding exactly 24.0h old is at the bound, not past it, and an age
    of None is unmeasurable rather than zero.
    """
    return [r for r in ages
            if r.get("kind") == "deferral"
            and r.get("disposition") in ("", None)
            and r.get("age_hours") is not None
            and r.get("age_hours") > max_age_hours]


def record_queue_age(ages, out_path) -> int:
    """Append one age row per finding to out_path. Returns rows written.

    The path is an argument with no default, on purpose: this module READS
    workspace/session-reviews/ledger.jsonl, and a writer that defaulted to it would turn a
    measurement run into a write against the thing being measured.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_path.open("a", encoding="utf-8") as fh:
        for row in ages:
            fh.write(json.dumps({
                "key": row.get("key", ""),
                "queued": row.get("queued", ""),
                "found": row.get("found", ""),
                "age_hours": row.get("age_hours"),
                "disposition": row.get("disposition", ""),
                "covered_by": row.get("covered_by", ""),
                "kind": row.get("kind", ""),
            }) + "\n")
            count += 1
    return count


# t1094: a sentence that promises work is a COMMITMENT, and a task number in that same
# line is what discharges it (workspace/rca/aeaf578817c2.md). The five idioms are the RCA's
# own list, verbatim -- a vocabulary invented here would report decisions nobody made, and a
# wider one is the gate everyone routes around with --no-verify within a week.
COMMITMENT_IDIOMS = tuple(
    re.compile(p, re.I)
    for p in (
        r"\bi'?ll\s+(queue|file|add|write|open|do|start|check|probe)\b",
        r"\bi will\s+(queue|file|add|write|open|do|start|check|probe)\b",
        r"\bthis is next\b",
        r"\bnext step\b",
        r"\bwe need to\b",
    )
)
# The number has to be USED, not merely present: it must sit in the commitment's own line,
# because a tNNN in a later line is somebody else's record, not the closure of this sentence.
DISCHARGE_NUMBER = re.compile(r"\bt\d{2,}\b", re.I)


def commitment_debts(text) -> list:
    """Every commitment line in text that never acquired a task number.

    Split on newlines, so each debt is one line's claim: session 7a0e9f2d closed green after
    writing "I'll queue now" with no numbered row after it, so prose was the whole artefact.
    Pure -- the text goes in, the open debts come out; no file, no taskstore, no queue. Each
    record quotes the WHOLE line, never sliced and never truncated, so the debt is evidence of
    the exact sentence that created it (never-truncate-a-record). Refusing a green close on a
    non-empty result, and auto-filing each debt as a numbered task, are the NEXT scope on this
    file -- this chunk is the required-number contract alone.
    """
    debts = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        if not any(rx.search(line) for rx in COMMITMENT_IDIOMS):
            continue
        if DISCHARGE_NUMBER.search(line):
            continue
        debts.append({"quote": line, "number": ""})
    return debts


if __name__ == "__main__":
    sys.exit(main())
