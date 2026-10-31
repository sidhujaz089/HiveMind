#!/usr/bin/env python3
"""Is this delivered branch safe to merge? Two mechanical conditions, both checked TODAY.

WHY (t272). The review queue holds branches cut from an older main. A branch can be green
against the contract it was GIVEN and fail the contract as it STANDS: coder/brief-c80a5fec
passed 15/15 when it was cut and failed 6/30 against the same suite on 2026-09-11, because
HEALTHY_STATUS was added to main after the branch existed. Merging it would also have
silently deleted that constant on a careless conflict resolution. Nothing in reviewq.py
checks this, so every review until now compared a branch against a contract that had moved.

THE TWO CONDITIONS, and neither is a matter of judgement:

  1. THE ORACLE IS UNTOUCHED. No file under scripts/tests/ differs from the merge base. This
     is the one thing D-35's freeze is load-bearing for: the model must not edit the test it
     is judged by. Checked against the MERGE BASE, never against main -- `git diff main..br`
     renders every newer commit on main as a deletion by the model (coder.py says so in its
     own delivery note).

  2. IT IS GREEN AGAINST CURRENT MAIN. main is merged INTO a throwaway worktree of the
     branch, and the suites are run there. Failures are then judged against the same two
     declarations coder.py's baseline guard uses -- test-failures-ceiling.json and
     sandbox-baseline.json -- through the same baseline_findings(). One definition of
     "acceptable failure", used by both, so they cannot drift.

THIS SCRIPT NEVER MERGES. It reports. Claude is the gate (D-34) and a script that merged on
its own verdict would be the thing being judged deciding its own case.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "projects" / "local-inference"))


def git(*args, cwd=None):
    r = subprocess.run(["git", *args], cwd=str(cwd or REPO),
                       capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def declared(path, key):
    """A MISSING DECLARATION IS NOT PERMISSION -- absence must not read as 'all fine' (L-146)."""
    f = REPO / path
    if not f.is_file():
        return set(), f"MISSING DECLARATION: {path}"
    try:
        return set(json.loads(f.read_text(encoding="utf-8")).get(key, []) or []), None
    except (json.JSONDecodeError, ValueError) as e:
        return set(), f"UNREADABLE DECLARATION: {path} ({e})"


def oracle_touched(branch):
    """Files under scripts/tests/ that differ from the MERGE BASE."""
    rc, base, _ = git("merge-base", "main", branch)
    if rc != 0:
        return None, ["no merge base with main"]
    rc, out, _ = git("diff", "--name-only", f"{base}..{branch}")
    changed = [p for p in out.splitlines() if p.strip()]
    return base, [p for p in changed if p.startswith("scripts/tests/")]


def suites_after_merge(branch, verbose=True):
    """Run the suites with CURRENT main merged in, in a throwaway worktree."""
    import coder
    # ON DISK (2026-10-19): a 2.2 GB copy in /tmp (RAM) helped hold Swift 5 GB short of starting.
    _home = Path.home() / ".cache" / "hivemind-tmp"; _home.mkdir(parents=True, exist_ok=True)
    wt = Path(tempfile.mkdtemp(prefix="mergegate-", dir=_home))
    try:
        rc, _, err = git("worktree", "add", "-q", "--detach", str(wt), branch)
        if rc != 0:
            return None, [f"could not create worktree: {err}"]
        rc, _, err = git("merge", "--no-commit", "--no-ff", "main", cwd=wt)
        conflicts = []
        if rc != 0:
            _, out, _ = git("diff", "--name-only", "--diff-filter=U", cwd=wt)
            conflicts = [p for p in out.splitlines() if p.strip()]
            if conflicts:
                return None, [f"CONFLICTS with current main: {', '.join(conflicts)}"]
        failed, _ = coder.run_suites(wt)
        return {p.split("/")[-1] for p in failed}, []
    finally:
        git("worktree", "remove", "--force", str(wt))
        shutil.rmtree(wt, ignore_errors=True)


def review(branch, verbose=True):
    import coder
    out = {"branch": branch, "verdict": "REFUSE", "problems": []}
    base, touched = oracle_touched(branch)
    if base is None:
        out["problems"] += touched
        return out
    if touched:
        out["problems"].append(
            "THE MODEL EDITED ITS OWN TEST: " + ", ".join(touched)
            + " -- this is the one thing the freeze exists to prevent (D-35)")
        return out

    ceiling, e1 = declared("workspace/test-failures-ceiling.json", "allowed_to_fail")
    cannot, e2 = declared("workspace/sandbox-baseline.json", "cannot_run_in_sandbox")
    for e in (e1, e2):
        if e:
            out["problems"].append(e + " -- every failure counts as undeclared")

    failed, errs = suites_after_merge(branch, verbose)
    if failed is None:
        out["problems"] += errs
        return out
    surprises = coder.baseline_findings(failed, ceiling, cannot)
    if surprises:
        out["problems"] += surprises
        return out
    out["verdict"] = "MERGEABLE"
    out["failed_but_declared"] = sorted(failed)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("branch", nargs="*", help="branches; default every refs/heads/coder/*")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    branches = a.branch
    if not branches:
        _, out, _ = git("for-each-ref", "--format=%(refname:short)",
                        "--sort=-committerdate", "refs/heads/coder/")
        branches = [b for b in out.splitlines() if b.strip()]
    results = []
    for b in branches:
        print(f"\n  {b}")
        r = review(b)
        results.append(r)
        if r["verdict"] == "MERGEABLE":
            print("    MERGEABLE -- oracle untouched, green against current main")
            if r.get("failed_but_declared"):
                print(f"    (declared failures present: {len(r['failed_but_declared'])})")
        else:
            print("    REFUSE:")
            for p in r["problems"]:
                print(f"      {p}")
    if a.json:
        print(json.dumps(results, indent=1))
    ok = sum(1 for r in results if r["verdict"] == "MERGEABLE")
    print(f"\n  {ok}/{len(results)} mergeable. This script does not merge -- Claude is the "
          f"gate (D-34).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
