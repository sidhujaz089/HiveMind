#!/usr/bin/env python3
"""Models-tab edits commit themselves (Jas, 2026-10-08).

"I didn't know you needed to commit changes to the model settings. Can that be automated? I am not
sure why you need to be in the loop on config changes to the role." Nobody needed to be: role_store
wrote the files and nothing committed them, so they waited for a session to `git add` them.

    request()                 # the Hub calls this after every Models-tab save
    python3 scripts/config_autocommit.py now    # commit what is waiting, at once

ONE COMMIT PER QUIET SPELL. Jas's six lane edits on 2026-10-08 spanned 26 seconds; that is one change
of mind, not six commits. request() (re)starts a QUIET_S timer; when it fires, the role store's files
are committed with `git commit --only`, so nothing else staged rides along. The message is the
changelog entries written since the last commit; the trailer is the person who made them, and
role_gate lets a person sign a commit of exactly these files and nothing more.

A busy index (a session mid-commit) or a refused hook is retried, then said in the Hub log -- the edit
is already saved on disk either way; only its commit waits.
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FILES = ("projects/local-inference/models/ROLES.json",
         "projects/local-inference/models/ROLES-CHANGELOG.jsonl",
         "projects/local-inference/models/SERVING.json")
CHANGELOG = REPO / FILES[1]
QUIET_S = 60
RETRIES = 5

_timer = None
_lock = threading.Lock()


def _git(*args, timeout=600):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=timeout)


def _new_entries():
    """Changelog entries on disk that HEAD does not have yet."""
    head = _git("show", f"HEAD:{FILES[1]}").stdout
    n_head = len([l for l in head.splitlines() if l.strip()])
    out = []
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines()[n_head:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({"what": "(an unreadable changelog line)", "who": ""})
    return out


def message(entries):
    whats = [e.get("what", "") for e in entries]
    subject = whats[0] if len(whats) == 1 else f"{len(whats)} Models-tab edits"
    body = "\n".join(f"- {w}" for w in whats)
    return (f"chore(local-inference): Models tab -- {subject}\n\nCommitted by config_autocommit.py after a quiet spell; "
            f"the edits, from ROLES-CHANGELOG.jsonl:\n{body}\n\nAgent: human/jas\n")


def commit_now():
    """(committed, why). Commits only FILES, only when one of them differs from HEAD."""
    changed = _git("status", "--porcelain", "--", *FILES).stdout.strip()
    if not changed:
        return False, "nothing to commit"
    entries = _new_entries()
    msg = message(entries or [{"what": "role store files changed", "who": "Jas"}])
    for attempt in range(RETRIES):
        r = _git("commit", "--only", "-m", msg, "--", *FILES)
        if r.returncode == 0:
            return True, _git("log", "--oneline", "-1").stdout.strip()
        if "index.lock" in (r.stderr + r.stdout):
            time.sleep(10 * (attempt + 1))      # a session is mid-commit; wait our turn
            continue
        return False, (r.stdout + r.stderr).strip()[-2000:]
    return False, "the index stayed locked; will try on the next edit"


def _fire():
    global _timer
    with _lock:
        _timer = None
    ok, why = commit_now()
    print(f"config_autocommit: {'committed ' if ok else 'not committed -- '}{why}", flush=True)


def request(quiet_s=None):
    """(Re)start the quiet timer. Safe to call from any thread; the timer is a daemon."""
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(QUIET_S if quiet_s is None else quiet_s, _fire)
        _timer.daemon = True
        _timer.start()


def main(argv):
    if argv[:1] == ["now"]:
        ok, why = commit_now()
        print(("committed " if ok else "not committed -- ") + why)
        return 0 if ok or why == "nothing to commit" else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
