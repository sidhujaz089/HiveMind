#!/usr/bin/env python3
"""A run must never delete a branch that holds another run's committed work.

WHY (env3 probe, finding 41)
----------------------------
`write_branch` decided whether a name was safe to take by asking reviewq, and reviewq derives
"pending" from agent-runs.jsonl. But write_branch COMMITS first and main() calls record()
after, so there is a window where a branch holds finished, committed work that no ledger row
mentions yet. A concurrent run with the same label reads not-pending and deletes it:

    P2  branch_is_pending("coder/x") -> False   (P1 has not recorded yet)
    P2  branch -D coder/x
    P1  ...commits its work to coder/x...
    P2  worktree add -b coder/x            (fails -- taken)
    P2  branch -D coder/x                  (DELETES P1'S COMMITTED WORK)

Both halves are fixed: the name is now claimed atomically with `git branch`, so a delete can
only ever remove a ref this process created; and a branch that EXISTS while no ledger row
names it is treated as someone else's work in flight rather than as an abandoned name.

This reproduces the state the losing interleaving PRODUCES -- a committed branch with no
ledger row -- and then drives a real `coder.py` run at the same label. Reproducing the timing
would be flaky; reproducing the state is exact, and it is the state that does the damage.
"""
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coder_harness as H  # noqa: E402

TARGET = "workspace/inbox/README.md"
BRIEF = "mark the inbox README heading for the clobber check"
LABEL = "brief-" + hashlib.sha256(BRIEF.encode()).hexdigest()[:8]
BRANCH = f"coder/{LABEL}"

OPS = {"ops": [{"path": TARGET,
                "old": "# inbox — Claude Chat → Claude Code",
                "new": "# inbox — Claude Chat → Claude Code (p2)"}],
       "why": "a second run wanting the same label"}


def git(clone, *a):
    return subprocess.run(["git", "-C", str(clone)] + list(a),
                          capture_output=True, text=True)


def main():
    out = []
    tmp = Path(H.sandbox("clobber-"))
    srv = None
    try:
        clone, err = H.build_clone(tmp)
        if clone is None:
            print(f"  [BAD]  {err}")
            return 1

        # P1: committed work on the branch, and NO ledger row naming it -- exactly the state
        # the window leaves behind.
        wt = tmp / "p1"
        git(clone, "worktree", "add", "-q", "-b", BRANCH, str(wt), "HEAD")
        (wt / "P1-WORK.txt").write_text("P1's accepted change.\n", encoding="utf-8")
        git(clone, "-C", str(wt), "add", "-A")
        subprocess.run(["git", "-C", str(wt), "-c", "core.hooksPath=/dev/null",
                        "commit", "-qm", "P1 work"], capture_output=True)
        git(clone, "worktree", "remove", "--force", str(wt))
        p1_tip = git(clone, "rev-parse", BRANCH).stdout.strip()
        ledger = H.ledger_rows(clone)
        names_it = any(r.get("branch") == BRANCH for r in ledger)

        print("P1 has committed work on the branch and has not recorded it yet:")
        out.append(("  the branch exists", bool(p1_tip), True))
        out.append(("  and no ledger row names it", names_it, False))

        srv, port = H.serve_ops(tmp, OPS)
        run = H.run_coder(clone, port, BRIEF, files=[TARGET], extra=["--apply"])
        rows = H.ledger_rows(clone)
        last = rows[-1] if len(rows) > len(ledger) else {}

        after = git(clone, "rev-parse", "--verify", BRANCH)
        print("\nP2 runs at the same label:")
        print(f"         outcome={last.get('outcome')!r} branch={last.get('branch')!r} "
              f"exit={run.returncode}")
        out.append(("  P2 recorded a verdict of its own", bool(last.get("outcome")), True))
        out.append(("  P1's branch still exists",
                    after.returncode == 0, True))
        out.append(("  and still points at P1's commit",
                    after.stdout.strip() == p1_tip, True))
        out.append(("  and P1's file is still on it",
                    git(clone, "show", f"{BRANCH}:P1-WORK.txt").returncode == 0, True))
        out.append(("  P2 wrote somewhere else",
                    bool(last.get("branch")) and last.get("branch") != BRANCH, True))

        # THE REFUSAL ITSELF, AND ITS NEGATIVE CONTROL. Everything above observes that P1's
        # work survived, which is the consequence. This is the decision that produces it:
        # branch_is_pending must REFUSE to free a name holding committed work no ledger row
        # mentions, and must NOT refuse a name that holds nothing -- a guard that refuses
        # everything would also pass every assertion above, while filling the repository
        # with coder/<label>-2, -3, -4 on every run (docs/CHECKS.md rule 1).
        import importlib.util as ilu
        spec = ilu.spec_from_file_location(
            "coder_nc", clone / "projects" / "local-inference" / "coder.py")
        cm = ilu.module_from_spec(spec)
        spec.loader.exec_module(cm)
        cm.REPO = clone
        print("\nThe refusal that produced that, asserted directly:")
        out.append(("  an unrecorded branch with commits is refused",
                    cm.branch_is_pending(BRANCH), True))
        out.append(("  and a name holding nothing is NOT refused",
                    cm.branch_is_pending("coder/no-such-branch-xyz"), False))

        # P2 FAILING IS THE ACTUAL SCENARIO OF FINDING 41. The branch was destroyed by the
        # FAILURE path -- P2's `worktree add` failed and its cleanup deleted a ref another
        # process owned. So a P2 that cannot deliver is not an edge case here, it is the
        # case: it must exit non-zero AND leave P1's commit exactly where it was.
        #
        # Delivery is made to fail the honest way, by making the sandbox disagree with HEAD
        # on the file being edited, which is the guard from finding 32.
        (clone / TARGET).write_text("<!-- uncommitted -->\n"
                                    + (clone / TARGET).read_text(encoding="utf-8"),
                                    encoding="utf-8")
        srv.terminate()
        srv, port2 = H.serve_ops(tmp, OPS)
        run2 = H.run_coder(clone, port2, BRIEF, files=[TARGET], extra=["--apply"])
        after2 = git(clone, "rev-parse", "--verify", BRANCH)
        print("\nAnd when P2 cannot deliver at all:")
        print(f"         exit={run2.returncode}")
        out.append(("  P2 exits non-zero", run2.returncode != 0, True))
        out.append(("  and P1's commit is STILL there",
                    after2.returncode == 0 and after2.stdout.strip() == p1_tip, True))
        if not last:
            print(run.stdout[-2500:]); print("STDERR:", run.stderr[-1200:])
    finally:
        if srv:
            srv.terminate()
        shutil.rmtree(tmp, ignore_errors=True)

    bad = 0
    for label, got, want in out:
        if got == want:
            print(f"  [ok]   {label}: {got}")
        else:
            bad += 1
            print(f"  [BAD]  {label}: expected {want}, got {got}")
    print(f"test_coder_branch_not_clobbered: {'ok' if not bad else f'{bad} FAILED'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
