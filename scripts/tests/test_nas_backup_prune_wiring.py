#!/usr/bin/env python3
"""t690: the nightly script must run the 3-version prune (both pile-ups) after a
green cold verify, and fail the night if a prune refuses.

Runs the REAL scripts/nas_backup.sh end-to-end in a fake tree (fake git repo, fake DEST, a
stub scripts/backup_corpora.py that records its argv) and asserts behaviour: call order (prune
calls only after --verify), exact args (--prune-bundles $DEST/ai-workspace-corpora --apply
--keep 3 and --prune $DEST --apply --keep 3), the ledger row (verified on success, prune-failed
+ exit 1 when a prune refuses, verify-failed + no prune calls when verify fails).

SAFETY GUARD: while the script does not read NAS_BACKUP_DEST, a sandboxed run would hit the
hardcoded /mnt/nas paths -- live state. The guard reads the script text and refuses to run it in
that case. Red on main for the missing change (no env seam, no prune calls), never for a
live-state accident.

The stub fails only the invocation named by an env marker, so a test can make --verify fail
(NAS_BACKUP_TEST_FAIL_VERIFY) without stopping the copy, and a prune fail
(NAS_BACKUP_TEST_FAIL_PRUNE) without stopping the verify.
"""
import json
import os
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
fp = os.environ.get("NAS_BACKUP_TEST_FAIL_PRUNE")
if fp and pathlib.Path(fp).exists() and len(sys.argv) > 1 and sys.argv[1].startswith("--prune"):
    sys.exit(1)
sys.exit(0)
'''


def run_script(env, timeout=180):
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=timeout)


def guard_and_base(base):
    text = SCRIPT.read_text()
    if "NAS_BACKUP_DEST" not in text:
        print("RED: scripts/nas_backup.sh hardcodes REPO/DEST -- a sandboxed run "
              "would hit the real /mnt/nas; the script must read NAS_BACKUP_REPO/"
              "NAS_BACKUP_DEST before this oracle will run it. It also does not "
              "call any prune step.")
        sys.exit(1)
    repo = base / "repo"
    dest = base / "dest"
    (repo / "scripts").mkdir(parents=True)
    (repo / "workspace").mkdir(parents=True)
    dest.mkdir(parents=True)
    r = subprocess.run(["git", "-C", str(repo), "init", "-q"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    (repo / "note.txt").write_text("v1\n")
    for cmd in (["add", "note.txt"],
                ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c1"]):
        r = subprocess.run(["git", "-C", str(repo), *cmd],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
    (repo / "scripts" / "backup_corpora.py").write_text(STUB)
    return repo, dest


def env_for(base, repo, dest, fail_verify=None, fail_prune=None):
    env = dict(os.environ)
    env["NAS_BACKUP_REPO"] = str(repo)
    env["NAS_BACKUP_DEST"] = str(dest)
    env["NAS_BACKUP_TEST_LOG"] = str(base / "argv.log")
    if fail_verify:
        env["NAS_BACKUP_TEST_FAIL_VERIFY"] = str(fail_verify)
    if fail_prune:
        env["NAS_BACKUP_TEST_FAIL_PRUNE"] = str(fail_prune)
    return env


def last_row(ledger):
    lines = [l for l in ledger.read_text().splitlines() if l.strip()]
    return json.loads(lines[-1])


def test_success_path_prunes_then_verified():
    base = Path(tempfile.mkdtemp(prefix="t690w_"))
    try:
        repo, dest = guard_and_base(base)
        r = run_script(env_for(base, repo, dest))
        assert r.returncode == 0, f"script failed: {r.stdout}\n{r.stderr}"
        calls = [l for l in (base / "argv.log").read_text().splitlines() if l.strip()]
        assert calls, "stub never invoked -- script did not run the python steps"
        i_verify = next(i for i, c in enumerate(calls) if c.startswith("--verify "))
        i_pb = next(i for i, c in enumerate(calls) if c.startswith("--prune-bundles "))
        i_p = next(i for i, c in enumerate(calls)
                   if c.split()[0] == "--prune" and "--prune-bundles" not in c)
        assert i_verify < i_pb < i_p, f"wrong order: {calls}"
        assert calls[i_pb] == f"--prune-bundles {dest}/ai-workspace-corpora --apply --keep 3", calls[i_pb]
        assert calls[i_p] == f"--prune {dest} --apply --keep 3", calls[i_p]
        row = last_row(repo / "workspace" / "backup-runs.jsonl")
        assert row["status"] == "verified", row
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_prune_refusal_fails_the_night():
    base = Path(tempfile.mkdtemp(prefix="t690w_"))
    try:
        repo, dest = guard_and_base(base)
        fp = base / "FAILPRUNE"
        fp.write_text("x")
        r = run_script(env_for(base, repo, dest, fail_prune=fp))
        assert r.returncode != 0, "a refusing prune must fail the night"
        row = last_row(repo / "workspace" / "backup-runs.jsonl")
        assert row["status"] == "prune-failed", row
        statuses = [json.loads(l)["status"] for l in
                    (repo / "workspace" / "backup-runs.jsonl").read_text().splitlines() if l.strip()]
        assert "verified" not in statuses, "a prune refusal must not record verified"
        calls = [l for l in (base / "argv.log").read_text().splitlines() if l.strip()]
        assert any(c.startswith("--prune") for c in calls), \
            f"a prune was never attempted on the success path: {calls}"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_verify_failure_never_reaches_prune():
    base = Path(tempfile.mkdtemp(prefix="t690w_"))
    try:
        repo, dest = guard_and_base(base)
        fv = base / "FAILVERIFY"
        fv.write_text("x")
        r = run_script(env_for(base, repo, dest, fail_verify=fv))
        assert r.returncode != 0
        row = last_row(repo / "workspace" / "backup-runs.jsonl")
        assert row["status"] == "verify-failed", row
        calls = [l for l in (base / "argv.log").read_text().splitlines() if l.strip()]
        assert not any(c.startswith("--prune") for c in calls), \
            f"prune ran after a failed verify: {calls}"
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    failures = 0
    for fn in (test_success_path_prunes_then_verified,
               test_prune_refusal_fails_the_night,
               test_verify_failure_never_reaches_prune):
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception as e:  # noqa: BLE001 -- report, do not swallow silently
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
    if failures:
        print(f"{failures} test(s) failed")
        sys.exit(1)
    print("all tests passed")
    sys.exit(0)
