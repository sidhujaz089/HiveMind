#!/usr/bin/env python3
"""t1025: a nas-backup ledger row must name the commit it verified.

RCA workspace/rca/2a77b922ac11.md: the row written at 2026-10-02T03:38:02Z says `status:
verified` and nothing about a commit, so the commit authored 66 seconds later (058b6b6d,
03:39:08Z) sat unbacked for 30h24m with no record of the gap. "It is on the NAS" had no
referent, and the deferral "the next session's end will back them up" had nowhere to live. The
missing thing is a RECORD FIELD, so this suite pins the schema: every row the script writes
names the commit it snapshotted, as a full sha, in `head`; and names the refs the bundle
carries, in `refs`.

Runs the REAL scripts/nas_backup.sh in a sandbox (NAS_BACKUP_REPO / NAS_BACKUP_DEST point at
temp dirs; scripts/backup_corpora.py is a stub that records argv) -- the same harness
scripts/tests/test_nas_backup_prune_wiring.py runs green today. No live state, no model, temp
dirs only, every subprocess given a timeout.

RED ON MAIN because scripts/nas_backup.sh's row() helper (lines 21-31) writes exactly
{at, job, status, detail} -- there is no head and no refs on any row. It is NOT red for a
sandbox bug: the success-path assertion runs the script to returncode 0 first, so the script
runs end to end and the failure is the missing key.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "nas_backup.sh"

STUB = '''
import os, sys, pathlib
log = os.environ.get("NAS_BACKUP_TEST_LOG")
if log:
    pathlib.Path(log).open("a").write(" ".join(sys.argv[1:]) + "\\n")
if len(sys.argv) > 1 and sys.argv[1] == "--to":
    d = pathlib.Path(sys.argv[2]) / "ai-workspace-corpora"
    d.mkdir(parents=True, exist_ok=True)
    (d / "corpusfile").write_text("payload\\n")
    (d / "MANIFEST.sha256").write_text("deadbeef  corpusfile\\n")
fv = os.environ.get("NAS_BACKUP_TEST_FAIL_VERIFY")
if fv and pathlib.Path(fv).exists() and len(sys.argv) > 1 and sys.argv[1] == "--verify":
    sys.exit(1)
sys.exit(0)
'''


def git(repo, *args, timeout=60):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)


def sandbox(base):
    """A fake repo with TWO commits and a second branch, so the head, the branch tip and the
    feature-branch tip are three distinct values a stub implementation cannot conflate -- and
    `refs` has to be a map keyed by real ref name, not one sha pasted twice."""
    text = SCRIPT.read_text()
    assert "NAS_BACKUP_DEST" in text, ("scripts/nas_backup.sh does not read NAS_BACKUP_DEST, so "
                                       "a sandboxed run would touch the real /mnt/nas -- live "
                                       "state. Refusing to run it.")
    repo, dest = base / "repo", base / "dest"
    (repo / "scripts").mkdir(parents=True)
    (repo / "workspace").mkdir(parents=True)
    dest.mkdir(parents=True)
    assert git(repo, "init", "-q").returncode == 0
    (repo / "note.txt").write_text("v1\n")
    assert git(repo, "add", "note.txt").returncode == 0
    assert git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c1").returncode == 0
    branch = git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
    assert branch, "sandbox: could not read the default branch name"
    assert git(repo, "branch", "feature/x").returncode == 0
    (repo / "note.txt").write_text("v2\n")
    assert git(repo, "add", "note.txt").returncode == 0
    assert git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c2").returncode == 0
    (repo / "scripts" / "backup_corpora.py").write_text(STUB)
    tip = git(repo, "rev-parse", "HEAD").stdout.strip()
    feat = git(repo, "rev-parse", "refs/heads/feature/x").stdout.strip()
    assert tip and feat and tip != feat, "sandbox did not yield two distinct commits"
    return repo, dest, branch, tip, feat


def env_for(repo, dest, base, fail_verify=None):
    env = dict(os.environ)
    env["NAS_BACKUP_REPO"] = str(repo)
    env["NAS_BACKUP_DEST"] = str(dest)
    env["NAS_BACKUP_TEST_LOG"] = str(base / "argv.log")
    if fail_verify:
        env["NAS_BACKUP_TEST_FAIL_VERIFY"] = str(fail_verify)
    return env


def rows(ledger):
    return [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]


def bundle_refs(repo, bundle):
    """The expected map is COMPUTED from the artefact, not stored: `git bundle list-heads`
    says what refs the bundle really carries, so the row must agree with the bundle."""
    r = git(repo, "bundle", "list-heads", str(bundle))
    assert r.returncode == 0, f"git bundle list-heads failed: {r.stderr}"
    out = {}
    for line in (r.stdout or "").splitlines():
        parts = line.strip().split(" ", 1)
        if len(parts) == 2:
            out[parts[1]] = parts[0]
    return out


def test_verified_row_records_the_head_and_the_refs():
    base = Path(tempfile.mkdtemp(prefix="t1025_"))
    try:
        repo, dest, branch, tip, feat = sandbox(base)
        r = subprocess.run(["bash", str(SCRIPT)], env=env_for(repo, dest, base),
                           capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, f"script failed on the success path:\n{r.stdout}\n{r.stderr}"
        ledger = repo / "workspace" / "backup-runs.jsonl"
        assert ledger.is_file(), "the script wrote no ledger row"
        rs = rows(ledger)
        assert rs, "the ledger is empty"
        verified = [x for x in rs if x.get("status") == "verified"]
        assert verified, f"no verified row written: {rs}"
        row = verified[-1]

        # THE FIELD THE CLASS NEEDS: a row that verifies a backup names the commit.
        assert "head" in row, f"the verified row names no commit: {row}"
        assert re.fullmatch(r"[0-9a-f]{40}", str(row["head"])), \
            f"head is not a full sha: {row['head']!r}"
        assert row["head"] == tip, \
            f"head {row['head']!r} is not the repo tip {tip} -- a row must name the commit it bundled"

        # refs: the whole ref set of the bundle, keyed by real ref name, not a single sha.
        bundles = sorted((repo / "workspace" / "mirror").glob("*.bundle"))
        assert bundles, "no bundle was created in workspace/mirror"
        want = bundle_refs(repo, bundles[-1])
        assert want, "git reported no refs in the bundle -- sandbox setup broken"
        assert set(want) == {f"refs/heads/{branch}", "refs/heads/feature/x"}, want
        assert "refs" in row, f"the verified row records no refs: {row}"
        assert row["refs"] == want, f"refs {row['refs']!r} != the bundle's refs {want}"
        assert row["refs"][f"refs/heads/{branch}"] == tip
        assert row["refs"]["refs/heads/feature/x"] == feat, \
            "refs collapsed to one sha: the second ref is missing"
        assert row["head"] in row["refs"].values()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_a_failing_night_records_the_head_too():
    """`head` is a property of the ROW WRITER, not a bolt-on at the success call site:
    the row that says the copy does NOT read back must also name what it tried to back up."""
    base = Path(tempfile.mkdtemp(prefix="t1025_"))
    try:
        repo, dest, branch, tip, feat = sandbox(base)
        marker = base / "FAILVERIFY"
        marker.write_text("x")
        r = subprocess.run(["bash", str(SCRIPT)],
                           env=env_for(repo, dest, base, fail_verify=marker),
                           capture_output=True, text=True, timeout=180)
        assert r.returncode != 0, "a failed read-back must fail the night"
        ledger = repo / "workspace" / "backup-runs.jsonl"
        rs = rows(ledger)
        assert rs, "a failed night wrote no ledger row"
        assert rs[-1].get("status") == "verify-failed", rs[-1]
        for row in rs:
            assert row.get("head"), f"a row was written without naming a commit: {row}"
            assert re.fullmatch(r"[0-9a-f]{40}", str(row["head"])), row
        assert rs[-1]["head"] == tip, rs[-1]
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    failures = 0
    for fn in (test_verified_row_records_the_head_and_the_refs,
               test_a_failing_night_records_the_head_too):
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception as e:  # noqa: BLE001 -- report the cause, never swallow it
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
    if failures:
        print(f"{failures} test(s) failed")
        sys.exit(1)
    print("all tests passed")
    sys.exit(0)
