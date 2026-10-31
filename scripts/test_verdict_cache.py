#!/usr/bin/env python3
"""A verdict cache for related_tests -- the same tree state buys the same answer once (2026-10-30).

WHY THIS EXISTS. `pre-commit` runs `related_tests.py`, which runs every suite that imports a staged script, under
a 900 s timeout. That gate is right: a commit should not carry a break. What is not right is paying it again for a
tree that has not changed. On 2026-10-30 a session end paid it repeatedly inside one hour -- 04:35 to 04:50, then
again from 05:08 -- because the commit was being retried, and every retry re-ran suites whose inputs were
byte-identical. The measured cost of one session end reached three hours, and the gate was not the fault; running
it twice for the same bytes was.

WHAT MAKES A REUSE SAFE. A verdict is reused only for the exact tree state it was measured on:
  * HEAD, because a suite's behaviour depends on the code the commit sits on;
  * the bytes of every tracked Python file under `scripts/` and `projects/` that differs from HEAD, staged or
    not, because that is what the suites actually import;
  * every UNTRACKED Python file, listed by name and hash, because a new file can shadow a module;
  * the list of suites the caller decided to run.
Change any of those and the key changes, so a stale verdict cannot be returned. Nothing here ever turns a red
into a green: it stores the exit codes it measured and hands them back unchanged.

WHY NOT JUST A TIMEOUT. A time-based cache says "we ran something recently"; this says "we ran THIS". The
difference is the whole point -- the tree moves constantly on this box, and a cache keyed on time would answer
for code nobody ran.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# NOT under ~/.cache/hivemind-tmp: both the tmp reaper and the worktree reaper sweep that directory, and a cache
# that vanishes is only a miss -- but a cache that vanishes MID-COMMIT while a hook is reading it is a crash.
DEFAULT_DIR = Path.home() / ".cache" / "hivemind-test-verdicts"
TTL_HOURS = 6.0                     # a verdict older than this is not reused even if the key matches
SCOPE = ("scripts", "projects")     # the only trees whose Python the suites import


def cache_dir():
    return Path(os.environ.get("TEST_VERDICT_CACHE") or DEFAULT_DIR)


def _git(repo, *args):
    r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=120)
    return r.stdout if r.returncode == 0 else ("" if args[0] == "rev-parse" else None)


def _sha(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "absent"


class CannotDetermine(Exception):
    """The state a suite would see cannot be read, so nothing may be reused.

    Raised rather than returning a key, because a key built from an unreadable repo is a key shared by every
    unreadable repo: the first fixture's RED would be handed to the next fixture's GREEN. That is the confident
    wrong answer this whole file exists to prevent, and it was found by `test_related_tests_declared_debt` --
    whose throwaway repos are not git repos, so both of its fixtures produced the same key and the green one
    inherited the red one's verdict.
    """


def state_digest(repo, tests=(), ceiling=None, scope=None):
    """A hash of the state a suite run would see. Same tree, same answer; any change, a new key.

    Deliberately includes files OUTSIDE the staged set: a suite imports what is on DISK, so an unstaged edit to
    a module it imports changes its verdict. Keying on the staged set alone would reuse a green measured before
    that edit. The CEILING file is in the key too, because it decides whether a red is blocking debt or known
    debt -- the same red is a different outcome after a ceiling edit.
    """
    repo = Path(repo)
    head = _git(repo, "rev-parse", "HEAD").strip()
    if not head:
        raise CannotDetermine(f"{repo} has no readable HEAD")
    parts = [f"head:{head}"]
    out = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", *(scope or SCOPE))
    if out is None:
        raise CannotDetermine(f"{repo}: git status failed")
    changed = []
    for line in out.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if path.endswith(".py"):
            changed.append(path)
    for path in sorted(changed):
        parts.append(f"{path}:{_sha(repo / path)}")
    if ceiling is not None:
        parts.append(f"ceiling:{_sha(Path(ceiling))}")
    parts.append("tests:" + ",".join(sorted(tests)))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _path(key):
    return cache_dir() / f"{key}.json"


def get(key, ttl_hours=None):
    """{suite: exit_code} measured for this exact tree state, or None.

    A missing, unreadable or expired entry is None -- never {}. An empty mapping would read as "nothing failed".
    """
    p = _path(key)
    try:
        row = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ttl = TTL_HOURS if ttl_hours is None else ttl_hours
    try:
        age_h = (time.time() - float(row["at"])) / 3600.0
    except (KeyError, TypeError, ValueError):
        return None
    if age_h > ttl:
        return None
    verdicts = row.get("verdicts")
    return verdicts if isinstance(verdicts, dict) and verdicts else None


def get_extra(key):
    """Whatever the writer attached to this entry (a summary, a reason), or {}."""
    try:
        return json.loads(_path(key).read_text(encoding="utf-8")).get("extra") or {}
    except (OSError, ValueError):
        return {}


def put(key, verdicts, tests=(), extra=None):
    """Store one measurement. Best-effort: an unwritable cache is a slower commit, not a failed one."""
    if not verdicts:
        return False
    try:
        d = cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = _path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": time.time(), "verdicts": verdicts,
                                   "tests": sorted(tests), "extra": extra or {}}, indent=0),
                       encoding="utf-8")
        tmp.replace(_path(key))
        return True
    except OSError as e:                                            # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: verdict cache not written ({type(e).__name__}: {e}); this commit is not slower",
              file=sys.stderr)
        return False


def prune(keep=200):
    """Keep the newest `keep` entries. A cache that grows without bound is the ledger problem again.

    A failure to delete is REPORTED, not swallowed: the whole point of a sweep is that somebody can tell it ran.
    """
    d = cache_dir()
    try:
        rows = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError as e:                                            # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: verdict cache not pruned ({type(e).__name__}: {e})", file=sys.stderr)
        return 0
    dropped = 0
    for p in rows[keep:]:
        try:
            p.unlink()
            dropped += 1
        except OSError as e:                                        # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: verdict cache entry not removed ({p.name}: {type(e).__name__}: {e})",
                  file=sys.stderr)
    return dropped
