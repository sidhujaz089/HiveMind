#!/usr/bin/env python3
"""An ACCEPTED verdict must not survive a branch that was never written.

WHY (2026-08-27, env3 probe round 26, confirmed on the real code path)
---------------------------------------------------------------------
`main()` called `ok, branch, detail = write_branch(...)` and **threw `ok` away**. When the
branch could not be created the run still recorded ACCEPTED, still stored the branch NAME,
printed the failure into the middle of its output, and **returned 0**.

Worse, `git worktree add -b` CREATES THE BRANCH REF BEFORE it fails on the path, and that
early-return path could not reach `abandon()` because abandon is defined after it. So the
observed state was: ledger says ACCEPTED on `coder/brief`, the branch EXISTS, and
`git diff main..coder/brief` is EMPTY -- precisely the "looks like a change waiting for
review and contains nothing" that abandon()'s own comment exists to prevent.

The verdict on the change was true; the DELIVERY failed. So the fix is a distinct outcome
(ACCEPTED-NOT-WRITTEN) and a non-zero exit, because every caller that trusts `returncode == 0`
inherits the false claim otherwise.

THIS DRIVES THE REAL ENTRY POINT (L-117). Four missed mutations in one session came from
testing the helper while the call site stayed broken, and the call site IS the bug here:
write_branch reported the failure correctly and honestly every time.

DO NOT RUN TWO OF THESE AT ONCE. The label is derived from a fixed brief, so every instance
computes the SAME branch name and operates on the LIVE repository. Two concurrent runs fight
over `coder/<label>`: one writes it, the other finds it taken and lands on `-2`, and the first
then fails "a written branch was not recorded as ACCEPTED with its name". Observed 2026-08-31
by running this by hand while the full suite was running it too -- twenty minutes spent looking
for a defect in the code under test, which was fine. The name is deliberately derived rather
than unique (finding 44), and making it unique per process would leave orphan branches behind
whenever a run is killed, so the constraint is documented rather than engineered away.

It leaves the ledger exactly as it found it -- a test that appends to workspace/agent-runs.jsonl
would feed the Pareto rows that never happened.
"""
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CODER = REPO / "projects" / "local-inference" / "coder.py"
# The suite's OWN ledger, handed to coder.py as CODER_RUNS: it never touches the live one.
RUNS = Path(tempfile.mkdtemp(prefix="coder-runs-", dir=str(Path.home() / ".cache" / "hivemind-tmp"))) / "agent-runs.jsonl"
# DERIVED, NOT HARDCODED. This said LABEL = "brief", which is what coder.py used until the
# label became a hash of the brief text. The test then looked for a branch that could never
# appear and failed while the code was CORRECT -- a false negative, red on working code,
# which is the failure mode that teaches people to ignore a suite. Found by the env3 probe
# (finding 44) after my own change broke my own test.
#
# Computing it the same way coder.py does means the test follows the code instead of
# asserting a copy of it.
TARGET_FILE = "workspace/inbox/README.md"
BRIEF = "add (probe) to the inbox README heading"
LABEL = "brief-" + hashlib.sha256(BRIEF.encode()).hexdigest()[:8]
BRANCH = f"coder/{LABEL}"
# WHERE coder.py PUTS IT, not a hard-coded /tmp (t286, 2026-10-01). coder.py follows TMPDIR (t415)
# and run_tests gives every suite its own, so under a sweep the blocker below sat in /tmp while the
# worktree went to $TMPDIR: nothing was blocked, the run exited 0, and the suite failed in every
# sweep while passing alone -- read for weeks as "fails under load".
WT = Path(tempfile.gettempdir()) / f"review-{LABEL}"

FAKE = '''
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
OPS = {"ops": [{"path": "workspace/inbox/README.md",
                "old": "# inbox \\u2014 Claude Chat \\u2192 Claude Code",
                "new": "# inbox \\u2014 Claude Chat \\u2192 Claude Code (probe)"}],
       "why": "a real one-line change to a tracked file"}
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        b = json.dumps({"choices": [{"message": {"content": json.dumps(OPS)}}],
                        "usage": {"completion_tokens": 42}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def git(*a):
    return subprocess.run(("git", "-C", str(REPO)) + a, capture_output=True, text=True)


def branch_exists():
    return git("rev-parse", "--verify", "--quiet", BRANCH).returncode == 0


def cleanup():
    # THE NUMBERED VARIANTS TOO. write_branch writes BESIDE a branch it must not overwrite,
    # so a run that finds coder/<label> already there lands on coder/<label>-2 -- and this
    # removed only the base name. One `Agent: ollama/faketest` branch was left sitting in
    # the review branches, indistinguishable at a glance from work waiting for a human.
    # A test that leaves a fake review branch behind is a test that manufactures the exact
    # state abandon() exists to prevent.
    for name in [BRANCH] + [f"{BRANCH}-{n}" for n in range(2, 12)]:
        git("worktree", "remove", "--force", str(WT))
        git("branch", "-D", name)
    for d in [WT] + [Path(f"{WT}-{n}") for n in range(2, 12)]:
        git("worktree", "remove", "--force", str(d))
        shutil.rmtree(d, ignore_errors=True)


def run_coder(port):
    return subprocess.run(
        [sys.executable, str(CODER), "--brief", BRIEF,
         "--files", "workspace/inbox/README.md", "--apply", "--model", "faketest",
         "--attempts", "1", "--reads", "0"],
        capture_output=True, text=True, cwd=str(REPO), timeout=900,
        env={**__import__("os").environ,
             "CODER_ENDPOINT": f"http://127.0.0.1:{port}/v1/chat/completions",
             "CODER_RUNS": str(RUNS)})


def last_run():
    rows = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
    return rows[-1] if rows else {}


def main():
    fails = []
    # ITS OWN DEBRIS IS CLEARED; ANYTHING ELSE STILL REFUSES (t286). A run killed mid-way left
    # /tmp/review-<label> holding only the `blocker` fixture this suite plants, and every run
    # after refused -- one interrupted sweep poisoned the suite until a person cleaned up. The
    # suite's own leftovers are recognisable: that blocker-only directory, and a branch whose tip
    # is a faketest commit. Those are removed; a branch or worktree that is anything else is
    # still refused, because it could be real work.
    # NOT INSIDE A SANDBOX, AND ONLY STALE DEBRIS. coder.py's baseline runs every suite in a
    # .git-less copy; a nested copy that took the OUTER run's live blocker for debris deleted it
    # mid-test (2026-10-01, found the same hour). No .git = nothing this suite can do: stop.
    # And a leftover younger than an hour may belong to a run still going: leave it.
    if not (REPO / ".git").exists():
        print("  no .git here (a coder sandbox) -- this suite needs the real repository; not run")
        return 1

    def own_debris():
        if WT.exists() and time.time() - WT.stat().st_mtime < 3600:
            return False
        wt_ok = (not WT.exists()) or (not (WT / ".git").exists()
                                      and sorted(p.name for p in WT.iterdir()) == ["blocker"])
        tip = git("log", "-1", "--format=%B", BRANCH).stdout if branch_exists() else ""
        br_ok = (not branch_exists()) or "Agent: ollama/faketest" in tip
        return wt_ok and br_ok
    if branch_exists() or WT.exists():
        if not own_debris():
            print(f"refusing to run: {BRANCH} or {WT} already exists and is not this suite's own")
            return 1
        print(f"  clearing this suite's own leftovers from an interrupted run: {WT}, {BRANCH}")
        cleanup()

    port = free_port()
    tmp = Path(tempfile.mkdtemp())
    (tmp / "fake.py").write_text(FAKE)
    srv = subprocess.Popen([sys.executable, str(tmp / "fake.py"), str(port)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           preexec_fn=lambda: __import__("ctypes").CDLL("libc.so.6").prctl(1, 15))  # dies with the test (PR_SET_PDEATHSIG)
    time.sleep(2)
    try:
        # ---- the failure path: the worktree path is occupied, so the branch cannot be made
        print("A branch that could not be written:")
        shutil.rmtree(WT, ignore_errors=True)
        (WT / "blocker").mkdir(parents=True)
        (WT / "blocker" / "f").write_text("in the way\n")

        r = run_coder(port)
        ok = r.returncode != 0
        print(f"  [{'ok' if ok else 'FAIL'}]   the run does NOT exit 0: {r.returncode}")
        if not ok:
            fails.append(f"a run whose branch was never written exited {r.returncode}")

        said = "NOT WRITTEN" in r.stdout
        print(f"  [{'ok' if said else 'FAIL'}]     and says so in plain words: {said}")
        if not said:
            fails.append("the run did not say the change was not written")

        row = last_run()
        outcome_ok = row.get("outcome") == "ACCEPTED-NOT-WRITTEN"
        print(f"  [{'ok' if outcome_ok else 'FAIL'}]     and the ledger does not say ACCEPTED: "
              f"{row.get('outcome')}")
        if not outcome_ok:
            fails.append(f"ledger recorded {row.get('outcome')!r} for an unwritten branch")

        nobranch = row.get("branch") is None
        print(f"  [{'ok' if nobranch else 'FAIL'}]     and names no branch: {row.get('branch')!r}")
        if not nobranch:
            fails.append(f"ledger named branch {row.get('branch')!r} that was never written")

        # The half-made ref is the dangerous residue: it LOOKS like work awaiting review.
        left = branch_exists()
        print(f"  [{'ok' if not left else 'FAIL'}]     and leaves no empty branch behind: "
              f"{not left}")
        if left:
            fails.append(f"{BRANCH} was left behind carrying none of the change")

        shutil.rmtree(WT, ignore_errors=True)
        cleanup()

        # ---- the happy path must still work. A guard that refuses everything is no better.
        print("\nAnd a branch that IS written:")
        r2 = run_coder(port)
        good = r2.returncode == 0
        print(f"  [{'ok' if good else 'FAIL'}]   a real run still exits 0: {r2.returncode}")
        if not good:
            # NAME THE LIKELY CAUSE. Exit 3 here is almost always the delivery guard doing
            # its job, not a defect: this drives a REAL coder run against the LIVE
            # repository, so an uncommitted change to the file it edits makes the sandbox
            # (built from the working tree) differ from the branch (cut from HEAD), and
            # write_branch correctly refuses. Cost twenty minutes once, reading as "the
            # happy path broke" when the working tree was simply dirty on that one file.
            hint = ""
            if r2.returncode == 3:
                d = git("diff", "--name-only", "HEAD", "--", TARGET_FILE).stdout.strip()
                hint = (f" -- {TARGET_FILE} has uncommitted changes, so delivery refused. "
                        f"Commit or stash it and run this again." if d else
                        " -- ACCEPTED-NOT-WRITTEN; read the run's own output for which "
                        "guard refused.")
            fails.append(f"the happy path broke: exit {r2.returncode}{hint}")

        carried = git("show", f"{BRANCH}:workspace/inbox/README.md").stdout.startswith(
            "# inbox — Claude Chat → Claude Code (probe)")
        print(f"  [{'ok' if carried else 'FAIL'}]     and the branch carries the change: {carried}")
        if not carried:
            fails.append("the accepted branch does not carry the accepted change")

        row2 = last_run()
        named = row2.get("outcome") == "ACCEPTED" and row2.get("branch") == BRANCH
        print(f"  [{'ok' if named else 'FAIL'}]     and the ledger names it: "
              f"{row2.get('outcome')} {row2.get('branch')!r}")
        if not named:
            fails.append("a written branch was not recorded as ACCEPTED with its name")

        untouched = (REPO / "workspace" / "inbox" / "README.md").read_text().startswith(
            "# inbox — Claude Chat → Claude Code\n")
        print(f"  [{'ok' if untouched else 'FAIL'}]     and the working tree is untouched: "
              f"{untouched}")
        if not untouched:
            fails.append("the run modified the working tree instead of only the branch")
    finally:
        srv.kill()
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(WT, ignore_errors=True)
        cleanup()
        # The live ledger was never written: coder.py wrote to this suite's own file (CODER_RUNS).
        shutil.rmtree(RUNS.parent, ignore_errors=True)

    # THE TASK NOTE IS WHAT JAS READS TO DECIDE WHETHER TO MERGE.
    #
    # record() inferred "dry run" from the ABSENCE of a branch, which was true while the only
    # branchless ACCEPTED was a dry run -- and false the day ACCEPTED-NOT-WRITTEN was added,
    # since that records branch=None too. The note then said "Dry run" about a run where
    # --apply WAS given and the write failed. Third consumer of one implicit contract to
    # break the same way (L-134 was the second).
    #
    # Captured, not performed: record() shells out to taskstore.py against the LIVE queue.
    print("\nThe note Jas reads says what actually happened:")
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("coder_nb", CODER)
    cm = _ilu.module_from_spec(spec)
    spec.loader.exec_module(cm)
    calls = []

    def fake_sh(*a, **k):
        calls.append(a)
        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()

    real_sh = cm.sh
    cm.sh = fake_sh
    cm.RUNS = Path(tempfile.mkdtemp()) / "runs.jsonl"
    tr = [{"n": 1, "tokens": 1, "seconds": 1, "outcome": "ACCEPTED", "ops": []}]
    said = {}
    for oc, br in [("ACCEPTED-NOT-WRITTEN", None), ("ACCEPTED-DRYRUN", None),
                   ("ACCEPTED", "coder/t1")]:
        calls.clear()
        cm.record("t1", "faketest", oc, tr, "p", "why", br, target="t.py")
        said[oc] = calls[-1][4]

    nw = "NOT WRITTEN" in said["ACCEPTED-NOT-WRITTEN"]
    print(f"  [{'ok' if nw else 'FAIL'}]   a failed write is NOT called a dry run: {nw}")
    if not nw:
        fails.append("the note calls a failed branch write a dry run")
    notdry = "Dry run" not in said["ACCEPTED-NOT-WRITTEN"]
    print(f"  [{'ok' if notdry else 'FAIL'}]     and the words 'Dry run' do not appear: {notdry}")
    if not notdry:
        fails.append("'Dry run' still appears in the ACCEPTED-NOT-WRITTEN note")
    dry = "Dry run" in said["ACCEPTED-DRYRUN"]
    print(f"  [{'ok' if dry else 'FAIL'}]   a real dry run still says dry run: {dry}")
    if not dry:
        fails.append("a genuine dry run stopped saying so")
    nm = "coder/t1" in said["ACCEPTED"]
    print(f"  [{'ok' if nm else 'FAIL'}]   a written branch is still named: {nm}")
    if not nm:
        fails.append("a written branch is no longer named in the note")

    # THE STUB ENDS HERE. `fake_sh` answers returncode 0 to everything, which is what record()
    # needs and is actively wrong for anything that ASKS git a question. branch_is_pending now
    # runs `rev-parse --verify` to tell "someone else's branch, mid-flight" from "no branch",
    # and under the stub every branch name looks like it exists -- so the assertion below,
    # which is about a branch that does NOT exist, was answering a question about a branch the
    # stub said was there. The stub's lifetime was wider than its purpose: the same shape as
    # L-137 and L-142, in a test.
    cm.sh = real_sh

    # A BRANCH AWAITING REVIEW MUST NOT BE OVERWRITTEN.
    #
    # `branch -D` was unconditional, so a second run on the same label destroyed the first
    # run's branch. For --brief the label is always the literal "brief", so two unrelated
    # runs collide every time. Noticed 2026-08-27, left unfixed, then confirmed live by the
    # env3 probe on 2026-08-28 -- which found it listed as FIXED when it was not.
    print("\nWork awaiting review is not overwritten:")
    import inspect as _i
    wsrc = _i.getsource(cm.write_branch)
    guarded = "branch_is_pending" in wsrc
    print(f"  [{'ok' if guarded else 'FAIL'}]   write_branch asks whether it is pending: {guarded}")
    if not guarded:
        fails.append("write_branch deletes a branch without asking if a human is waiting on it")

    psrc = _i.getsource(cm.branch_is_pending)
    reuses = "reviewq" in psrc
    print(f"  [{'ok' if reuses else 'FAIL'}]     by reading reviewq, not a second copy: {reuses}")
    if not reuses:
        fails.append("branch_is_pending reimplements the queue rule instead of reading it")

    # Fails OPEN: an unreadable queue must keep the branch. A spare branch is cheap; deleting
    # work a human was about to review is not.
    opens = cm.branch_is_pending("coder/definitely-not-a-real-branch-xyz") is False
    print(f"  [{'ok' if opens else 'FAIL'}]     and an unknown branch is not pending: {opens}")
    if not opens:
        fails.append("branch_is_pending said an unknown branch was pending")

    for x in fails:
        print("FAIL:", x)
    print("test_coder_branch_delivery:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
