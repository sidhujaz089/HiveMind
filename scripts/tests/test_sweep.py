#!/usr/bin/env python3
"""Fixtures for scripts/sweep.py.

The guard that matters is --done refusing to delete a message whose current bytes are not
in HEAD. Every message Chat sent before 2026-08-12 was destroyed by the sweep that handled
it, and the rule that was supposed to prevent that has been prose in a README ever since.

The subtle case is the third one. A naive guard asks "is this file tracked?", and a message
that was committed and then EDITED passes that test while its edit is still the only copy.
That case is asserted here specifically, because it is the one a plausible implementation
gets wrong and no error announces.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP = ROOT / "scripts" / "sweep.py"

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


def make_repo():
    tmp = Path(tempfile.mkdtemp())
    git(tmp, "init", "-q")
    git(tmp, "config", "user.email", "t@t")
    git(tmp, "config", "user.name", "t")
    for d in ("inbox", "outbox", "commands", "app-feedback", "tasks"):
        (tmp / "workspace" / d).mkdir(parents=True)
        (tmp / "workspace" / d / "README.md").write_text("keep\n")
    git(tmp, "add", "-A")
    git(tmp, "commit", "-qm", "init")
    return tmp


def sweep(repo, *args):
    """Run the real script against the fixture repo."""
    src = SWEEP.read_text()
    shim = repo / "sweep_under_test.py"
    shim.write_text(src.replace(
        "REPO = Path(__file__).resolve().parent.parent",
        f"REPO = Path({str(repo)!r})"))
    return subprocess.run([sys.executable, str(shim), *args],
                          capture_output=True, text=True)


def main():
    print("--done must refuse anything whose current bytes are not in HEAD:")

    # 1. untracked message -- the classic case
    r = make_repo()
    m = r / "workspace" / "inbox" / "msg.md"
    m.write_text("never committed\n")
    out = sweep(r, "--done", "msg.md")
    report("untracked message is refused", out.returncode, 1)
    report("  and still exists afterwards", m.is_file(), True)

    # 2. committed message -- must actually go
    git(r, "add", "-A"); git(r, "commit", "-qm", "add msg")
    out = sweep(r, "--done", "msg.md")
    report("committed message is deleted", out.returncode, 0)
    report("  and is gone from the tree", m.is_file(), False)
    print("\n--done on an OUTBOX path names the real reason, not 'No such message':")
    # Chat proposed --done for an outbox file on 2026-08-25 and got "No such message",
    # which was true and useless: the file existed, in the other directory.
    ob = r / "workspace" / "outbox"
    ob.mkdir(parents=True, exist_ok=True)
    (ob / "sent.md").write_text("a message Code sent\n")
    out = sweep(r, "--done", "sent.md")
    report("refuses", out.returncode, 1)
    report("says it is in the outbox", "OUTBOX, not the inbox" in out.stderr, True)
    report("does not claim it is missing", "No such message" in out.stderr, False)

    print("\n--done takes the path the protocol PRINTS, not only a bare name:")
    # session_start.py's banner says "2 message(s) from Claude Chat in workspace/inbox/:"
    # and then lists them, so the copy-pasteable form is a repo-relative path. Until
    # 2026-09-22 that form resolved to workspace/inbox/workspace/inbox/... and came back
    # "No such message" -- about a file the caller could see, named the way the protocol
    # had just named it. Same class as the two other path defects found that day.
    m2 = r / "workspace" / "inbox" / "second.md"
    m2.write_text("committed message\n")
    git(r, "add", "-A"); git(r, "commit", "-qm", "add second")
    out = sweep(r, "--done", "workspace/inbox/second.md")
    report("a repo-relative path is accepted", out.returncode, 0)
    report("  and the message is gone", m2.is_file(), False)

    # A message in a SUBDIRECTORY of the inbox -- inbox/handled/ is a real one. The basename
    # alone cannot find this, so it is the case that proves the path is genuinely resolved
    # rather than merely stripped down to its last component.
    sub = r / "workspace" / "inbox" / "handled"
    sub.mkdir(parents=True, exist_ok=True)
    m3 = sub / "third.md"
    m3.write_text("committed, in a subdirectory\n")
    git(r, "add", "-A"); git(r, "commit", "-qm", "add third")
    out = sweep(r, "--done", "workspace/inbox/handled/third.md")
    report("a path INTO an inbox subdirectory resolves", out.returncode, 0)
    report("  and that message is gone", m3.is_file(), False)

    # The containment guard is what actually keeps this safe, so prove the widening did
    # not weaken it. This is the load-bearing case: accepting more SHAPES of input must
    # not accept more LOCATIONS.
    out = sweep(r, "--done", "../../etc/passwd")
    report("an escaping path is still refused", out.returncode, 1)
    report("  and says so plainly", "escapes the inbox" in out.stderr, True)
    out = sweep(r, "--done", "workspace/outbox/sent.md")
    report("an outbox PATH still names the real reason",
           "OUTBOX, not the inbox" in out.stderr, True)

    shutil.rmtree(r, ignore_errors=True)

    # 3. THE SUBTLE ONE: committed, then edited. "Is it tracked?" says yes and loses the edit.
    r = make_repo()
    m = r / "workspace" / "inbox" / "msg.md"
    m.write_text("version one\n")
    git(r, "add", "-A"); git(r, "commit", "-qm", "v1")
    m.write_text("version one\nplus an edit that exists nowhere else\n")
    out = sweep(r, "--done", "msg.md")
    report("committed-then-EDITED message is refused", out.returncode, 1)
    report("  and the edit survives", "plus an edit" in m.read_text(), True)

    # 4. the README keeps the directory alive in git
    out = sweep(r, "--done", "README.md")
    report("README.md is refused", out.returncode, 1)

    # 5. path traversal via the filename
    out = sweep(r, "--done", "../../STATE.md")
    report("a traversing filename is refused", out.returncode, 1)

    print("\n--commit must actually commit the channels:")
    (r / "workspace" / "app-feedback" / "note.md").write_text("from the phone\n")
    (r / "workspace" / "tasks" / "t001--x.md").write_text("a task\n")
    before = git(r, "rev-parse", "HEAD").stdout.strip()
    out = sweep(r, "--commit")
    after = git(r, "rev-parse", "HEAD").stdout.strip()
    report("a commit was made", before != after, True)
    report("nothing left uncommitted",
           git(r, "status", "--porcelain", "--", "workspace").stdout.strip(), "")
    # and now the previously-refused message can be retired
    out = sweep(r, "--done", "msg.md")
    report("the refused message retires once committed", out.returncode, 0)

    # A SYMLINK IS VALIDATED WHERE IT POINTS AND DELETED WHERE IT SITS.
    #
    # rel() called .resolve(), which follows links, so is_committed() hashed the TARGET while
    # cmd_done() unlinked the LINK. An uncommitted symlink was deleted with the message "its
    # committed copy remains in history" -- about a file git had never held. env3 probe round
    # 54, reproduced in a fixture. This is the guard that exists because every inbox message
    # was destroyed on 2026-08-12, so a route around it matters more than its rarity suggests.
    print("\n--done must not validate one file and delete another:")
    (r / "docs").mkdir(exist_ok=True)
    (r / "docs" / "target.md").write_text("committed target\n")
    git(r, "add", "-A")
    git(r, "commit", "-m", "target")
    link = r / "workspace" / "inbox" / "link.md"
    link.symlink_to(Path("..") / ".." / "docs" / "target.md")

    out = sweep(r, "--done", "link.md")
    report("  --done refuses a symlink", out.returncode, 1)
    report("  and the link still exists", link.is_symlink(), True)
    report("  and its target is untouched",
           (r / "docs" / "target.md").read_text(), "committed target\n")

    # The hash guard must refuse it on its own, not only the explicit symlink check --
    # two independent reasons, so removing either still fails this suite.
    import importlib.util
    shim = r / "sweep_under_test.py"
    spec = importlib.util.spec_from_file_location("swp", shim)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    report("  rel() does not follow the link", mod.rel(link), "workspace/inbox/link.md")
    report("  so is_committed() alone refuses it", mod.is_committed(link), False)

    # A DELETED tracked file is the state this must never be silent about.
    #
    # The first fix for L-131 filtered the swept paths to those that EXIST, so a deleted
    # receipts.jsonl left the pathspec and sweep printed "0 path(s)" while git printed
    # " D workspace/receipts.jsonl". Found by the probe six hours after that fix shipped.
    # Deletion is the most dangerous state an audit trail can be in, and it was the one
    # state the guard could not see.
    print("\nA deleted tracked file is still uncommitted:")
    rec = r / "workspace" / "receipts.jsonl"
    rec.write_text('{"seed": 1}\n')
    git(r, "add", "-A")
    git(r, "commit", "-m", "seed receipts")
    rec.unlink()
    out = sweep(r).stdout
    report("  the deletion is reported", "receipts.jsonl" in out, True)
    report("  and the board is not called clean",
           "Uncommitted in the channels — 0 path(s)" in out, False)
    sweep(r, "--commit", "-m", "chore: record the deletion")
    report("  and --commit stages it",
           git(r, "status", "--porcelain", "--", "workspace/receipts.jsonl").stdout.strip(), "")

    # AND A DELETION THAT IS ALREADY STAGED, e.g. the user ran `git rm` before sweeping.
    #
    # This case is why swept() consults HEAD (--with-tree) rather than the index. A bare
    # `ls-files` reads the index, so a path whose deletion is already staged is not in it,
    # drops out of the swept set, and sweep goes blind to it again. The mutation reverting
    # --with-tree=HEAD came back MISSED until this case existed -- the earlier test could not
    # see the property because computing the path set once already protected the within-
    # command ordering. Two independent reasons, and only one of them was covered.
    print("\nA deletion that was already staged:")
    rec2 = r / "workspace" / "receipts.jsonl"
    rec2.write_text('{"seed": 2}\n')
    git(r, "add", "-A")
    git(r, "commit", "-m", "seed receipts again")
    git(r, "rm", "-q", "--", "workspace/receipts.jsonl")
    out = sweep(r).stdout
    report("  a staged deletion is still reported", "receipts.jsonl" in out, True)
    report("  and the board is not called clean",
           "Uncommitted in the channels — 0 path(s)" in out, False)

    print("\nreceipts:")
    sweep(r, "--receipt", "m.json", "--outcome", "applied", "--ops", "13",
          "--issued-by", "chat/opus-5")
    sweep(r, "--receipt", "n.json", "--outcome", "refused", "--ops", "3",
          "--issued-by", "chat/opus-5", "--reason", "anchor-not-found")
    rows = [json.loads(l) for l in
            (r / "workspace" / "receipts.jsonl").read_text().splitlines()
            if l.strip() and not l.startswith("#")]
    report("two receipts, parseable as JSON", len(rows), 2)
    report("  outcome recorded", rows[0]["outcome"], "applied")
    report("  refusal reason recorded", rows[1]["reason"], "anchor-not-found")

    # THE RECEIPT MUST BE VISIBLE TO THE SWEEP THAT WROTE IT.
    #
    # receipts.jsonl was not in SWEPT, so `--receipt` appended a row and the very next
    # `sweep.py` printed "Uncommitted in the channels -- 0 path(s)" while listing that
    # receipt two lines below, and `--commit` would never pick it up. env3 probe round 30,
    # confirmed on the real path.
    #
    # It matters more than an ordinary missed file: the receipt exists so Chat can tell "my
    # manifest applied" from "it was never moved out of the inbox", and an audit trail that
    # is never committed survives only until someone runs a checkout. Its having been
    # committed at all was a passing `git add -A` from an unrelated session.
    out = sweep(r).stdout
    report("  the sweep SEES the receipt it just wrote", "receipts.jsonl" in out, True)
    report("  and does not call the board clean",
           "Uncommitted in the channels — 0 path(s)" in out, False)

    sweep(r, "--commit", "-m", "chore: receipts")
    left = git(r, "status", "--porcelain", "--", "workspace/receipts.jsonl").stdout.strip()
    report("  and --commit actually commits it", left, "")

    shutil.rmtree(r, ignore_errors=True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
