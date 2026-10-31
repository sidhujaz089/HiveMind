#!/usr/bin/env python3
"""Sweep the channels safely: commit before deleting, and leave a receipt Chat can read.

    python3 scripts/sweep.py                 what is waiting, and what is uncommitted
    python3 scripts/sweep.py --commit        commit everything the channels have accumulated
    python3 scripts/sweep.py --done <file>   retire one inbox message (refuses if uncommitted)
    python3 scripts/sweep.py --receipt ...   record what happened to a manifest

WHY THIS EXISTS
---------------
Two holes, both of which have already cost something.

1. THE CONVENTION HELD BY LUCK. workspace/inbox/README.md says commit the message before
   deleting it, because until 2026-08-12 nobody did and every message Chat had ever sent was
   destroyed by the sweep that handled it. That rule has been prose. Prose is not a mechanism:
   `--done` refuses to delete a file whose current bytes are not in HEAD, so the auditable
   copy exists before the deletion does.

2. NOTHING THE TOOLS WRITE GETS COMMITTED. app-feedback notes, tasks and inbox messages land
   in the working tree and wait for someone to remember. Sixteen notes accumulated that way,
   and the HiveMind Hub generates more of them, faster. `--commit` is the one
   command that closes the loop.

AND THE RECEIPT (Chat's C2, 2026-08-20)
---------------------------------------
Chat cannot tell "my manifest applied cleanly" from "it was never moved out of the inbox and
the ledger simply never changed". Both look identical from its side: it proposes, and the
next session reads a state. So a session opens unable to confirm its predecessor landed, and
an unapplied close is silent.

`workspace/receipts.jsonl` closes it. One line per manifest -- issued-by, timestamp, op
count, applied or refused, and the reason. It lives under workspace/ because that is served
over the bridge and in git; chat/ is read-only to Chat and outside git, so a receipt there
would be neither auditable nor writable by the thing that must write it.

The receipt is written by Code about Chat's manifest, which makes it a claim by the actor
rather than an independent sensor -- L-039 applies and the file says so in its own header.
What makes it useful anyway is that a MISSING receipt is as informative as a present one:
Chat can see that nothing was recorded, which is exactly the case it cannot detect today.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INBOX = REPO / "workspace" / "inbox"
OUTBOX = REPO / "workspace" / "outbox"
FEEDBACK = REPO / "workspace" / "app-feedback"
COMMANDS = REPO / "workspace" / "commands"
RECEIPTS = REPO / "workspace" / "receipts.jsonl"

# Directories the tools write into that nobody remembers to commit. Attachments are
# deliberately absent: they are gitignored by design (see the console's design doc).
SWEPT = ["workspace/inbox", "workspace/outbox", "workspace/commands",
         "workspace/app-feedback", "workspace/tasks",
         # receipts.jsonl is written BY THIS SCRIPT and was not in its own swept set, so
         # `--receipt` appended a row, `sweep.py` then reported "Uncommitted in the channels
         # -- 0 path(s)" while listing that very receipt two lines below, and `--commit`
         # would never pick it up (env3 probe, round 30; confirmed on the real path).
         #
         # It is the one file here whose whole purpose is to be an audit trail: Chat cannot
         # distinguish "my manifest applied" from "it was never moved out of the inbox"
         # except by reading this. An uncommitted audit trail survives only until someone
         # runs a checkout, and its being committed at all was luck -- a passing `git add
         # -A` from an unrelated session.
         "workspace/receipts.jsonl"]

RECEIPT_HEADER = {
    "note": "Receipts for command manifests. Written by Code about Chat's manifests, so this "
            "is the actor reporting on itself (L-039) -- but a MISSING line is as meaningful "
            "as a present one, which is the case Chat cannot otherwise detect.",
}


def git(*args, check=True):
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=300)
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rel(path):
    """The path's location IN the repository -- not the location of what it points at.

    This used `.resolve()`, which follows symlinks, so `is_committed()` hashed the TARGET
    while `cmd_done()` deleted the LINK. An uncommitted symlink in the inbox was therefore
    deleted with the message "its committed copy remains in history", about a file that had
    never been committed at all (env3 probe round 54, reproduced in a fixture).

    `os.path.abspath` normalises `..` and `.` without following links, which is what a
    question about "where does this live in the repo" actually means.
    """
    return str(Path(os.path.abspath(path)).relative_to(REPO)).replace("\\", "/")


def is_committed(path):
    """True when the file's CURRENT bytes are what HEAD holds.

    Not "is tracked" -- a message that was committed and then edited would pass that and
    still lose the edit. Compares the blob hash of the file on disk against HEAD's.
    """
    r = rel(path)
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", f"HEAD:{r}"],
                          capture_output=True, text=True, timeout=60)
    if head.returncode != 0:
        return False
    disk = subprocess.run(["git", "-C", str(REPO), "hash-object", str(path)],
                          capture_output=True, text=True, timeout=60)
    return disk.returncode == 0 and disk.stdout.strip() == head.stdout.strip()


def swept():
    """The swept paths that EXIST, in this repo, right now.

    SWEPT names one file (`workspace/receipts.jsonl`) alongside the directories, and git
    fails an entire `add`/`commit` when any pathspec matches nothing -- so a repo that has
    not written a receipt yet could not commit its channels at all. Found immediately by
    test_sweep.py, whose fixture builds a bare repo: 3 failures the moment the file was
    added to the list. Filtering here keeps the constant readable and the commands safe.
    """
    # "Exists on disk" was the first version of this test and it was WRONG in the one state
    # that matters most: a tracked file that has been DELETED does not exist, so it dropped
    # out of the pathspec and `sweep.py` reported "Uncommitted in the channels -- 0 path(s)"
    # while git reported " D workspace/receipts.jsonl". An audit trail's deletion is exactly
    # what this script must not be silent about, and my own fix for L-131 introduced it.
    # (env3 probe, pass 2 round 14 -- the fix from six hours earlier.)
    #
    # A path belongs in the pathspec if git KNOWS about it, whether or not it is on disk.
    # `git add` stages a deletion for a tracked-but-absent path quite happily; what it
    # refuses is a pathspec matching nothing at all, which is the bare-repo case that broke
    # three tests when receipts.jsonl was first added to SWEPT.
    keep = []
    for s in SWEPT:
        if (REPO / s).exists():
            keep.append(s)
            continue
        # --with-tree=HEAD, NOT a bare ls-files. Bare ls-files reads the INDEX, so the
        # moment `git add` stages a deletion the path leaves the index and the NEXT call to
        # swept() drops it -- and cmd_commit() calls swept() three times (add, diff,
        # commit). It staged a deletion, then diffed a set that no longer contained it, and
        # printed "Nothing staged (all ignored?)" without committing. Consulting HEAD keeps
        # the answer stable across the staging that happens between those calls.
        known = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "--with-tree=HEAD", "--", s],
            capture_output=True, text=True, timeout=60)
        if known.returncode == 0 and known.stdout.strip():
            keep.append(s)
    return keep or SWEPT[:1]


def pending():
    """Everything uncommitted under the swept directories, as porcelain status entries."""
    out = git("status", "--porcelain", "--", *swept())
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        code, _, name = line[:2], line[2], line[3:]
        rows.append((code.strip(), name.strip('"')))
    return rows


def _in_swept(path):
    """True if the path is one of, or under, the swept directories/files."""
    return any(path == s or path.startswith(s + "/") for s in SWEPT)


def _entry_in_swept(name):
    """True if any path in a porcelain status entry (possibly a rename) is swept."""
    if " -> " in name:
        src, dst = name.split(" -> ", 1)
        return _in_swept(src) or _in_swept(dst)
    return _in_swept(name)


def _cross_boundary_paths():
    """The non-swept side of each cross-boundary rename in the working tree.

    `git add -- <swept-pathspec>` stages only the side under that pathspec,
    leaving the other half of a cross-boundary rename uncommitted. These are
    the paths that must be added explicitly so both halves land in one commit.
    """
    extra = []
    out = git("status", "--porcelain")
    for line in out.splitlines():
        if not line.strip():
            continue
        name = line[3:].strip('"')
        if " -> " not in name:
            continue
        src, dst = name.split(" -> ", 1)
        if _in_swept(src) != _in_swept(dst):
            extra.append(dst if _in_swept(src) else src)
    return extra


# --------------------------------------------------------------------------- commands

def cmd_status():
    # BOTH halves are recursive, from two probe findings that landed together. A message or
    # a manifest in a subdirectory was invisible to this board while it printed a clean one
    # (findings 3 and 18). `applied/` is excluded because a manifest that has been applied is
    # not waiting to be applied -- rglob would otherwise report the archive as a backlog.
    msgs = sorted(f for f in INBOX.rglob("*.md") if f.name != "README.md") if INBOX.is_dir() else []
    manifests = sorted(
        f for f in COMMANDS.rglob("*.json")
        if "applied" not in f.relative_to(COMMANDS).parts
    ) if COMMANDS.is_dir() else []

    print(f"Inbox — {len(msgs)} message(s) from Chat")
    for f in msgs:
        state = "committed" if is_committed(f) else "NOT COMMITTED — do not delete yet"
        print(f"  {f.relative_to(INBOX)}\n      {state}")

    print(f"\nCommands — {len(manifests)} manifest(s) waiting to be applied")
    for f in manifests:
        rel = f.relative_to(COMMANDS).as_posix()
        try:
            ops = len(json.loads(f.read_text(encoding='utf-8')).get("ops", []))
            print(f"  {rel}  ({ops} op(s))")
        except Exception as e:
            print(f"  {rel}  (unreadable: {e})")

    rows = pending()
    print(f"\nUncommitted in the channels — {len(rows)} path(s)")
    for code, name in rows:
        print(f"  {code or '??':<3} {name}")
    if rows:
        print("\n  Commit them:  python3 scripts/sweep.py --commit")

    if RECEIPTS.is_file():
        lines = [l for l in RECEIPTS.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")]
        print(f"\nReceipts — {len(lines)} recorded")
        for l in lines[-3:]:
            try:
                r = json.loads(l)
                print(f"  {r.get('at')}  {r.get('issued-by')}  "
                      f"{r.get('outcome')}  {r.get('ops')} op(s)")
            except json.JSONDecodeError:
                print(f"  NOTE: unreadable record row -- not shown", file=sys.stderr)
    return 0


def cmd_commit(message=None):
    # REGENERATE THE OUTBOX INDEX FIRST, so a reply and its index entry land in ONE commit.
    # INDEX.md was only rewritten by session_end.py. On 2026-09-14 a full reply to Chat was
    # written and committed mid-session through this command, the session's session_end then
    # refused to finish (a stale-process check), and INDEX.md kept saying "0 unacknowledged of
    # 16" with nothing newer than 2026-09-10. Chat reads the index, not the directory, so it
    # concluded no reply existed and told Jas so -- a correct inference from a stale file.
    # A reply is not SENT until it is in the index; this is the commit that sends it.
    try:
        subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "outbox_index.py"),
                        "--write"], capture_output=True, text=True, timeout=120)
    except Exception as e:                                          # noqa: BLE001
        print(f"WARNING: outbox index not regenerated ({type(e).__name__}: {e}). A reply "
              f"committed now is invisible to Chat until it is.")
    rows = pending()
    if not rows:
        print("Nothing to commit in the channels.")
        return 0
    # L-090: this commits ONLY the channels, which is correct for what it is for -- and is
    # a trap when used as a general commit, because the message describes work the commit
    # does not contain. Say so rather than let a message and a diff disagree silently.
    outside = [l[3:].strip('"') for l in
               git("status", "--porcelain").splitlines()
               if l[3:].strip('"') and not _entry_in_swept(l[3:].strip('"'))]
    if outside:
        print(f"NOTE: {len(outside)} tracked change(s) OUTSIDE the channels will NOT be in "
              f"this commit:")
        for f in outside[:8]:
            print(f"  {f}")
        if len(outside) > 8:
            print(f"  ... and {len(outside) - 8} more")
        print("  Commit those separately, and write that message from the diff.\n")

    # ONE list, computed once, used for all three git calls. Recomputing it between them
    # is what let the set shift under this function mid-command.
    paths = swept() + _cross_boundary_paths()
    git("add", "--", *paths)
    staged = git("diff", "--cached", "--name-only", "--", *paths).split()
    if not staged:
        print("Nothing staged (all ignored?).")
        return 0
    msg = message or (
        f"chore(channels): sweep {len(staged)} path(s)\n\n"
        "Committed by scripts/sweep.py so the record exists before anything deletes it.\n"
        "workspace/inbox/README.md requires this and it was prose until now.\n\n"
        "Agent: claude-code/opus-5")
    git("commit", "-m", msg, "--", *paths)
    print(f"Committed {len(staged)} path(s):")
    for s in staged:
        print(f"  {s}")
    return 0


def cmd_done(name):
    """Retire an INBOX message. Refuses unless its current bytes are already in HEAD.

    Inbox only, deliberately. Chat proposed `--done` for an outbox file on 2026-08-25 and
    got `No such message`, which is true and useless -- the file existed, in the other
    directory. The two directions are not symmetric: an inbox message is retired once Code
    has acted on it, while an outbox message is Code's own record of what it sent and is
    retired only when its preconditions expire, which is a judgement rather than a sweep.
    """
    # A BARE NAME OR A PATH, BECAUSE THE PROTOCOL PRINTS THE PATH.
    # `INBOX / name` alone means a repo-relative path becomes
    # workspace/inbox/workspace/inbox/... and is reported as "No such message" -- about a
    # file the caller can see, named exactly as session_start.py's own banner prints it
    # ("2 message(s) from Claude Chat in workspace/inbox/: <path>"). Hit on 2026-09-22 while
    # sweeping two actioned messages: the copy-pasteable form was the one that failed. This
    # is the same class as the two path defects found the same day -- a tool that resolves
    # the caller's input against only one of the shapes the system hands out.
    # The containment check below is unchanged and still does the real work.
    # A candidate only counts if it lands INSIDE the inbox. Accepting more SHAPES of input
    # must not accept more LOCATIONS -- and a path pointing somewhere else must fall through
    # to the outbox branch below, which names the real reason, rather than being swallowed
    # by the generic "escapes the inbox".
    cand = Path(name)
    inbox_r = INBOX.resolve()
    feedback_r = FEEDBACK.resolve()
    # The escape test runs on the input AS GIVEN, before any candidate is tried. Falling back
    # to the basename below would otherwise launder `../../etc/passwd` into a contained-
    # looking INBOX/passwd, and the caller would get "No such message" for something that
    # deserves to be named as an escape. Still refused either way -- but a refusal that
    # misdescribes what it refused is how a guard stops being read.
    if not ((INBOX / cand).resolve().is_relative_to(inbox_r) or
            (FEEDBACK / cand).resolve().is_relative_to(feedback_r)):
        print(f"REFUSED: {name} escapes the inbox.", file=sys.stderr)
        return 1
    f = (INBOX / cand.name).resolve()         # the target the error messages talk about
    for attempt in ((cand if cand.is_absolute() else None), REPO / cand, INBOX / cand, FEEDBACK / cand):
        if attempt is None or not attempt.is_file():
            continue
        r = attempt.resolve()
        if r.is_relative_to(inbox_r) or r.is_relative_to(feedback_r):
            f = r
            break
    if not (f.is_relative_to(inbox_r) or f.is_relative_to(feedback_r)):
        print(f"REFUSED: {name} escapes the inbox.", file=sys.stderr)
        return 1
    if not f.is_file():
        # By BASENAME, so this still fires when the caller passed a path. Before 2026-09-22
        # only a bare name reached here, and `OUTBOX / "workspace/outbox/sent.md"` is not a
        # file -- so the path form of the very mistake this branch exists to explain fell
        # through to "No such message", which is the useless answer it was written to stop.
        other = (OUTBOX / cand.name).resolve()
        if other.is_relative_to(OUTBOX.resolve()) and other.is_file():
            print(f"{name} is in the OUTBOX, not the inbox. `--done` is inbox-only.",
                  file=sys.stderr)
            print("An outbox message is Code's record of what it sent. Retire one only "
                  "when its preconditions have expired, and check the bytes against HEAD "
                  "first so history keeps it:", file=sys.stderr)
            print(f"    git rm workspace/outbox/{name}", file=sys.stderr)
            return 1
        print(f"No such message: {name}", file=sys.stderr)
        return 1
    # A symlink is not a message. Even with rel() no longer following links -- which alone
    # makes the hash comparison refuse this -- say why plainly rather than emit a confusing
    # "not committed at its current content" about a link the user can see in git.
    if f.is_symlink():
        print(f"REFUSED: {f.name} is a symlink, not a message.\n"
              f"  Its target is a different file, and deleting the link would destroy a\n"
              f"  pointer that git never held while this guard checked the target instead.",
              file=sys.stderr)
        return 1
    if f.name == "README.md":
        print("README.md keeps the directory alive in git. Refusing.", file=sys.stderr)
        return 1
    if not is_committed(f):
        print(f"REFUSED: {f.name} is not committed at its current content.\n"
              f"  Deleting it now would destroy the only copy, which is exactly what\n"
              f"  happened to every message before 2026-08-12.\n"
              f"  Run:  python3 scripts/sweep.py --commit", file=sys.stderr)
        return 1
    f.unlink()
    print(f"Deleted {f.name} (its committed copy remains in history).")
    return 0


def cmd_receipt(manifest, outcome, ops, issued_by, reason):
    """One line per manifest, in the shape Chat asked for."""
    if not RECEIPTS.is_file():
        RECEIPTS.write_text("# " + json.dumps(RECEIPT_HEADER) + "\n", encoding="utf-8")
    row = {"at": now(), "manifest": manifest, "issued-by": issued_by,
           "ops": ops, "outcome": outcome}
    if reason:
        row["reason"] = reason
    with RECEIPTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Receipt: {outcome} — {manifest} ({ops} op(s))")
    return 0


# ---- /tmp SWEEPER (t1102). /tmp on the GX10 is tmpfs -- RAM. Repo copies a killed job never cleaned
# up held 23 GB on 2026-10-13 and 17 GB on 2026-10-16, and Swift was OOM-killed beside them. A finally
# does not run on SIGKILL, so the reaper is a sweeper, not the creator's cleanup.
TMP_PREFIXES = ("coder-", "delivery-", "run-tests-", "ratchet-", "mergegate-", "automerge-",
                "bridge-wt-", "scope-lint-", "script-audit-", "session-review-")
TMP_MAX_AGE_S = 3600
TMP_LEDGER = REPO / "workspace" / "run-logs" / "tmp-sweep.jsonl"


def tmp_holders():
    """Paths a live process holds -- its cwd and every open fd target -- read from /proc, never by name (L-105)."""
    out = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        for link in [f"/proc/{pid}/cwd"] + [f"/proc/{pid}/fd/{fd}" for fd in _ls(f"/proc/{pid}/fd")]:
            target = _readlink(link)        # None: the process exited between listdir and readlink
            if target:
                out.add(os.path.realpath(target))
    return out


def _readlink(link):
    try:
        return os.readlink(link)
    except OSError as e:
        return None if e.errno in (2, 3, 13) else str(link)  # gone or not ours -> no holder; else hold it safe


def _ls(d):
    try:
        return os.listdir(d)
    except OSError:
        return []


_CODER_BOX = re.compile(r"^coder-\d+-(\d+)$")


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    st = Path(f"/proc/{pid}/stat")
    try:
        return st.read_text().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


def tmp_eligible(name, age_s, holders, path=None, min_age_s=TMP_MAX_AGE_S):
    """A creator's directory, never claude-*, at least min_age_s old, and held by no live process.
    A builder sandbox (coder-<time>-<pid>) whose pid is alive is never eligible; one whose pid is gone
    is eligible at any age -- its owner can never clean it up now (2026-10-20)."""
    if name.startswith("claude-") or not name.startswith(TMP_PREFIXES):
        return False
    m = _CODER_BOX.match(name)
    if m and _pid_alive(int(m.group(1))):
        return False
    if age_s < (0 if m else min_age_s):
        return False
    p = os.path.realpath(path or os.path.join(os.getcwd(), name))
    return not any(h == p or h.startswith(p + os.sep) for h in holders)


def _size(p):
    n = 0
    for dp, _dn, fn in os.walk(p):
        for f in fn:
            try:
                n += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                n += 0              # a file gone mid-walk adds nothing to the size recorded
    return n


def sweep_tmp(root, max_age_s=TMP_MAX_AGE_S, holders=None, ledger=None):
    """Remove abandoned creator copies under root; one ledger row each. -> [rows]."""
    import shutil
    root = Path(root)
    holders = tmp_holders() if holders is None else holders
    ledger = Path(ledger or TMP_LEDGER)
    rows, now = [], time.time()
    for d in sorted(root.iterdir()):
        try:
            if d.is_symlink() or not d.is_dir():
                continue
            age = int(now - d.stat().st_mtime)
        except OSError as e:
            print(f"  tmp sweep: skipped {d}: {type(e).__name__}: {e}")
            continue
        if not tmp_eligible(d.name, age, holders, path=str(d), min_age_s=max_age_s):
            continue
        size = _size(d)
        shutil.rmtree(d, ignore_errors=True)
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "path": str(d),
               "size_bytes": size, "age_s": age}
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        print(f"  removed {d} ({size // 1048576} MB, {age // 3600} h old)")
        rows.append(row)
    if not rows:
        print(f"  tmp sweep: nothing to remove under {root}")
    return rows


# AFTER AN INTERRUPTION, NOT AN HOUR LATER (Jas, 2026-10-20: "the cases where a build was interrupted could
# be resolved by having this sweeper triggered when I resume production"). A memory-guard trip or a
# reboot stops jobs mid-run with SIGKILL, which no cleanup survives. Resume (scheduler_commands) calls this
# before production starts: dead builders' sandboxes at any age, other creator dirs over 5 minutes.
RESUME_MIN_AGE_S = 300


def sweep_after_interruption(roots=None, ledger=None):
    """Sweep /tmp and the disk temp dir for what an interrupted run left. -> [rows removed]."""
    roots = roots or [tempfile.gettempdir(), str(Path.home() / ".cache" / "hivemind-tmp")]
    rows = []
    for r in roots:
        if Path(r).is_dir():
            rows += sweep_tmp(r, max_age_s=RESUME_MIN_AGE_S, ledger=ledger)
    return rows


def is_repo_copy(path):
    """A full copy of this repository at `path` or one level under it (a test's mkdtemp holding a clone)."""
    marker = Path("scripts") / "dispatch_loop.py"
    p = Path(path)
    if (p / marker).is_file():
        return True
    try:
        return any((c / marker).is_file() for c in p.iterdir() if c.is_dir() and not c.is_symlink())
    except OSError:
        return False            # unreadable: not provably a copy, so never removed


def classify_tmp_copies(copies, now=None, reap_after_s=3 * 3600):
    """([(path, size)] stale, [(path, size)] recent) -- which copies the reaper can actually take.

    A CHECK MUST ONLY FAIL ON WHAT ITS OWN REMEDY CAN FIX (2026-10-30). check_workspace reported abandoned copies in
    /tmp and told the reader to run sweep_tmp_repo_copies('/tmp', max_age_s=3600). That command returned [] and the
    copies stayed, because they were MINUTES old and the reaper deliberately skips anything younger than max_age_s --
    a working test's clone must not be deleted under it. So the check failed on something its advice could not touch,
    and a red check whose advice cannot work is a red check people learn to ignore.

    The split is the fix: copies PAST the threshold are the failure, because the remedy can take them; copies inside
    it are a warning, because the next sweep will. Both are reported, so nothing is hidden.
    """
    now = time.time() if now is None else now
    stale, recent = [], []
    for path, size, mtime in copies or []:
        age = now - float(mtime)
        (recent if age < reap_after_s else stale).append((path, size))
    return stale, recent


def sweep_tmp_repo_copies(root, max_age_s=3 * 3600, holders=None, ledger=None):
    """ABANDONED REPO COPIES WHATEVER THEIR NAME (2026-10-18). A test killed mid-run (a timeout, the
    memory guard, SIGKILL) never reaches its `finally: rmtree`, so its clone stays: two t406-* copies
    of test_coder_yield_resume.py held 4 GB of /tmp 16 h later. A name list cannot keep up with every
    test's mkdtemp prefix; "is a copy of this repo" can. Not a git worktree (those are
    sweep_tmp_worktrees' and may hold uncommitted work), never claude-*, 3 h untouched, unheld."""
    import shutil
    root = Path(root)
    holders = tmp_holders() if holders is None else holders
    ledger = Path(ledger or TMP_LEDGER)
    rows, now = [], time.time()
    for d in sorted(root.iterdir()):
        if d.name.startswith("claude-") or d.is_symlink() or not d.is_dir():
            continue
        try:
            age = int(now - d.stat().st_mtime)
        except OSError as e:
            print(f"  tmp sweep: skipped {d}: {type(e).__name__}: {e}")
            continue
        real = os.path.realpath(d)
        if age < max_age_s or any(h == real or h.startswith(real + os.sep) for h in holders):
            continue
        if (d / ".git").is_file() or not is_repo_copy(d):
            continue            # a worktree (.git file) is never removed here; not a copy -> not ours
        size = _size(d)
        shutil.rmtree(d, ignore_errors=True)
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "path": str(d),
               "size_bytes": size, "age_s": age, "kind": "repo-copy"}
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        print(f"  removed repo copy {d} ({size // 1048576} MB, {age // 3600} h old)")
        rows.append(row)
    return rows

# Paths services and hooks append to inside ANY checkout; a worktree dirty only here holds no one's work (t9566).
RUNTIME_PREFIXES = ("workspace/run-logs/", "workspace/corpus/", "workspace/PROJECT-INDEX.md", "workspace/SCRIPTS.md",
                    "workspace/tasks/")
KEEP_WORKTREES = {"merge-reviewed"}


def _is_runtime_path(rel):
    rel = rel.strip().strip('"')
    return "__pycache__" in rel or rel.startswith(RUNTIME_PREFIXES) or (
        rel.startswith("workspace/") and rel.endswith(".jsonl"))


def sweep_tmp_worktrees(root, max_age_s=3 * 3600, holders=None, ledger=None):
    """ABANDONED WORKTREES WHATEVER THEIR NAME (Jas, 2026-10-17). Sessions and model commands make
    worktrees with ad-hoc names (t1334wt, wt1054) the prefix list cannot know. A worktree of THIS repo
    under root, 3 h untouched, held by no live process and with no tracked change, is a copy of a
    commit -- removed with `git worktree remove`. One with changes is reported, never removed."""
    root = os.path.realpath(root)
    holders = tmp_holders() if holders is None else holders
    ledger = Path(ledger or TMP_LEDGER)
    out = subprocess.run(["git", "-C", str(REPO), "worktree", "list", "--porcelain"],
                         capture_output=True, text=True, timeout=60).stdout
    trees = [l[9:] for l in out.splitlines() if l.startswith("worktree ")]
    locked = {b.split("\n")[0][9:] for b in out.split("\n\n") if "\nlocked" in b}
    rows, now = [], time.time()
    for w in trees:
        p = os.path.realpath(w)
        if not p.startswith(root + os.sep) or w in locked or os.path.basename(p).startswith("claude-"):
            continue
        try:
            age = int(now - os.stat(p).st_mtime)
        except OSError as e:
            print(f"  tmp sweep: skipped worktree {w}: {type(e).__name__}: {e}")
            continue
        if age < max_age_s or any(h == p or h.startswith(p + os.sep) for h in holders):
            continue
        if os.path.basename(p) in KEEP_WORKTREES or os.path.basename(p).startswith("merge-wt-"):
            continue            # the merge path's own scratch (merge_reviewed.py): reset at every merge, never reaped
        # WHAT COUNTS AS WORK (t9566, 2026-10-29). 99 of 146 merged worktrees were "dirty" only because services and
        # hooks append to runtime ledgers inside them, so "any change keeps it" kept ~150 GB of copies of commits
        # main already has. A worktree whose ONLY changes are ledger rows, on a commit main contains, holds nothing
        # that is not in git. Anything else -- a real edit, or an untracked file outside the ledgers -- is work, and
        # the worktree is kept and reported. Untracked files count now: a new file is the easiest work to lose.
        st = subprocess.run(["git", "-C", p, "status", "--porcelain"],
                            capture_output=True, text=True, timeout=60).stdout.splitlines()
        # A task FILED in a worktree may exist nowhere else: it is runtime only if the live checkout has it too.
        real = [l for l in st if not _is_runtime_path(l[3:])
                or (l[3:].startswith("workspace/tasks/") and not (REPO / l[3:].strip().strip('"')).exists())]
        ledger_only = bool(st) and not real
        head = subprocess.run(["git", "-C", p, "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=60).stdout.strip()
        merged = bool(head) and subprocess.run(["git", "-C", str(REPO), "merge-base", "--is-ancestor", head, "main"],
                                               capture_output=True, timeout=60).returncode == 0
        if real or (ledger_only and not merged):
            print(f"  tmp sweep: KEPT worktree {w}: uncommitted work ({len(real) or len(st)} file(s))")
            continue
        size = _size(p)
        r = subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", p], timeout=120,
                           capture_output=True, text=True)
        if r.returncode:
            print(f"  tmp sweep: worktree {w} not removed: {r.stderr.strip()}")
            continue
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "path": p,
               "size_bytes": size, "age_s": age, "kind": "worktree"}
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        print(f"  removed worktree {p} ({size // 1048576} MB, {age // 3600} h old)")
        rows.append(row)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--commit", action="store_true",
                    help="Commit everything uncommitted in the channels.")
    ap.add_argument("-m", "--message", help="Commit message for --commit.")
    ap.add_argument("--done", metavar="FILE",
                    help="Delete an inbox message; refuses if it is not committed.")
    ap.add_argument("--receipt", metavar="MANIFEST",
                    help="Record the outcome of a command manifest.")
    ap.add_argument("--outcome", choices=["applied", "refused", "partial"], default="applied")
    ap.add_argument("--ops", type=int, default=0)
    ap.add_argument("--issued-by", default="unknown", dest="issued_by")
    ap.add_argument("--reason", default="")
    ap.add_argument("--tmp", action="store_true", help="Sweep abandoned creator copies from /tmp (t1102).")
    ap.add_argument("--tmp-dir", default=tempfile.gettempdir(), dest="tmp_dir")
    ap.add_argument("--ledger", default=None)
    a = ap.parse_args(argv)

    if a.tmp:
        sweep_tmp(a.tmp_dir, ledger=a.ledger)
        if a.tmp_dir == tempfile.gettempdir():
            sweep_tmp_worktrees(a.tmp_dir, ledger=a.ledger)
            sweep_tmp_repo_copies(a.tmp_dir, ledger=a.ledger)
        return 0

    if a.receipt:
        return cmd_receipt(a.receipt, a.outcome, a.ops, a.issued_by, a.reason)
    if a.done:
        return cmd_done(a.done)
    if a.commit:
        return cmd_commit(a.message)
    return cmd_status()


if __name__ == "__main__":
    sys.exit(main())
