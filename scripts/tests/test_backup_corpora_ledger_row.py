#!/usr/bin/env python3
"""t1328: `backup_corpora.py --verify` must RECORD the verified offline corpus backup.

RCA workspace/rca/f5e27b8c3555.md sections 2, 4 and 5: the precondition that gates retiring
Google Drive (D-15 ISO PDFs and D-22 chat/ each become one copy on one machine) lives only as
prose (workspace/ROADMAP.md:191, docs/DECISIONS.md:424/429), so it re-appears every session as
the bare deferral "that's a backup call you haven't been asked to make yet, and it should
happen before the switch, not after". The RCA's close-condition is
`grep '"usb-corpus"' workspace/backup-runs.jsonl` -- and that command can never return
anything, because no such row type exists (0 hits in the 247 rows of the ledger; its only
jobs are nas-backup, night-queue, collect-logs). This suite builds the row type, so the
precondition becomes provable by a command.

RED ON MAIN: scripts/backup_corpora.py has no ledger code at all. Verified by reading all 610
lines -- --verify (do_verify, line 334) prints and returns 0 at line 424 writing only the
bundle's MANIFEST.sha256 and BACKUP-INFO.txt (lines 258-275); the file has no 'json' import,
no os.environ read, and no mention of backup-runs.jsonl. The first test fails on the missing
environment variable; the rest fail with 'no ledger row written'. Every test asserts the
fixture bundle verifies with returncode 0 BEFORE asserting on the ledger, so the script runs
end to end and the missing record is the only cause of red.

No live state: bundle, manifest and ledger all live under tempfile.mkdtemp, and the ledger path
is redirected by the BACKUP_RUNS_LEDGER environment variable, which the first test asserts the
script honours before anything is run -- the guard shape of
scripts/tests/test_backup_records_head.py:60. No model, no server, no fixed /tmp paths, every
subprocess given timeout=180.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents(2)
SCRIPT = REPO / "scripts" / "backup_corpora.py"

# Three DISTINCT names, deliberately not two: an implementation that writes one repeated
# placeholder, or a hardcoded ['iso-pdf', 'chat'] list, cannot satisfy the corpora assertion.
FIXTURE = [
    ("chat/notes.md", "chat\n"),
    ("library/essay.txt", "essay\n"),
    ("claude-code-sessions/1.jsonl", "session line\n"),
]


def make_bundle(base):
    """A real bundle directory: payload files plus a MANIFEST.sha256 in the exact
    '<sha256>  <relpath>' shape do_verify parses (scripts/backup_corpora.py:344-347)."""
    bundle = base / "usb" / "ai-workspace-corpora"
    lines = []
    for rel, text in FIXTURE:
        p = bundle / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        lines.append(hashlib.sha256(text.encode("utf-8")).hexdigest() + "  " + rel)
    (bundle / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return bundle


def run_verify(bundle, ledger):
    env = dict(os.environ, BACKUP_RUNS_LEDGER=str(ledger))
    return subprocess.run([sys.executable, str(SCRIPT), "--verify", str(bundle)],
                          env=env, capture_output=True, text=True, timeout=180)


def rows_of(ledger):
    return [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_the_script_honours_BACKUP_RUNS_LEDGER():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "BACKUP_RUNS_LEDGER" in src, (
        "scripts/backup_corpora.py does not read BACKUP_RUNS_LEDGER, so a sandboxed --verify "
        "would append to the LIVE workspace/backup-runs.jsonl. Refusing to run it: the oracle "
        "must not touch live state (a ledger is a record, and this suite must not write one).")


def test_verify_appends_one_usb_corpus_row():
    base = Path(tempfile.mkdtemp(prefix="t1328_"))
    try:
        bundle = make_bundle(base)
        ledger = base / "backup-runs.jsonl"
        r = run_verify(bundle, ledger)
        assert r.returncode == 0, ("the fixture bundle did not verify -- the fixture is wrong, "
                                   "not the code:\n" + r.stdout + r.stderr)
        assert ledger.is_file(), ("--verify on a verified bundle recorded nothing at "
                                  + str(ledger) + "; stdout:\n" + r.stdout)
        rows = rows_of(ledger)
        assert len(rows) == 1, "expected exactly one ledger row, got " + repr(rows)
        row = rows[0]
        assert row.get("job") == "usb-corpus", "job must be exactly 'usb-corpus': " + repr(row)
        assert row.get("status") == "verified", "status must be exactly 'verified': " + repr(row)
        corpora = sorted({rel.split("/")[0] for rel, _ in FIXTURE})
        assert row.get("corpora") == corpora, ("corpora " + repr(row.get("corpora"))
                                               + " != the corpora in the manifest " + repr(corpora))
        assert row.get("files") == len(FIXTURE), "files " + repr(row.get("files")) + " != 3"
        total = sum(len(t.encode("utf-8")) for _, t in FIXTURE)
        assert row.get("bytes") == total, "bytes " + repr(row.get("bytes")) + " != " + str(total)
        want = hashlib.sha256((bundle / "MANIFEST.sha256").read_bytes()).hexdigest()
        got = str(row.get("sha256", ""))
        assert len(got) == 64 and all(c in "0123456789abcdef" for c in got), \
            "sha256 is not a hex digest: " + repr(got)
        assert got == want, ("sha256 " + repr(got) + " is not the digest of THIS bundle's "
                             "manifest (" + want + "); a fixed digest makes every row lie")
        at = str(row.get("at", ""))
        assert at.endswith("Z"), "at is not a UTC Z timestamp: " + repr(at)
        datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_a_failed_verify_records_no_verified_row():
    """The row's whole value is that 'verified' means verified. A bundle whose bytes do not
    match its manifest must fail AND leave no record -- the same shape as the repository's
    swallowed-exception rule: a failure must never be written as a success."""
    base = Path(tempfile.mkdtemp(prefix="t1328_"))
    try:
        bundle = make_bundle(base)
        ledger = base / "backup-runs.jsonl"
        prior = {"at": "2026-10-01T00:00:00Z", "job": "nas-backup",
                 "status": "verified", "detail": "pre-existing row, must survive"}
        ledger.write_text(json.dumps(prior) + "\n", encoding="utf-8")
        (bundle / "library" / "essay.txt").write_text("tampered\n", encoding="utf-8")
        r = run_verify(bundle, ledger)
        assert r.returncode != 0, ("the corrupted bundle verified, so this fixture proves "
                                   "nothing:\n" + r.stdout + r.stderr)
        rows = rows_of(ledger)
        assert len(rows) == 1, "a failed verify recorded a row: " + repr(rows)
        assert rows[0] == prior, ("the ledger was rewritten, not appended to: "
                                  + repr(rows[0]))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_two_bundles_record_two_rows_with_different_digests():
    """Append, once per verified bundle, each bound to its own content. A writer that emits a
    constant digest -- or writes at import time, once -- fails the inequality."""
    base = Path(tempfile.mkdtemp(prefix="t1328_"))
    try:
        b1 = make_bundle(base)
        b2 = base / "usb2"
        shutil.copytree(b1, b2)
        rel = "chat/notes.md"
        text = "chat v2\n"
        (b2 / rel).write_text(text, encoding="utf-8")
        mp = b2 / "MANIFEST.sha256"
        keep = [l for l in mp.read_text(encoding="utf-8").splitlines()
                if l.strip() and l.split("  ", 1)[-1] != rel]
        keep.append(hashlib.sha256(text.encode("utf-8")).hexdigest() + "  " + rel)
        mp.write_text("\n".join(keep) + "\n", encoding="utf-8")
        ledger = base / "backup-runs.jsonl"
        for b in (b1, b2):
            r = run_verify(b, ledger)
            assert r.returncode == 0, (b.name + " failed to verify: " + r.stdout + r.stderr)
        rows = rows_of(ledger)
        assert len(rows) == 2, "two verified bundles must leave two rows, got " + repr(rows)
        assert all(x.get("job") == "usb-corpus" and x.get("status") == "verified" for x in rows), rows
        assert rows[0].get("sha256") != rows[1].get("sha256"), (
            "both rows carry the same sha256: the digest is not computed from the bundle")
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    _tests = (test_the_script_honours_BACKUP_RUNS_LEDGER,
              test_verify_appends_one_usb_corpus_row,
              test_a_failed_verify_records_no_verified_row,
              test_two_bundles_record_two_rows_with_different_digests)
    _fail = 0
    for _fn in _tests:
        try:
            _fn()
            print("ok   " + _fn.__name__)
        except Exception as _e:  # noqa: BLE001 -- report the cause, never swallow it
            _fail += 1
            print("FAIL " + _fn.__name__ + ": " + repr(_e))
    if _fail:
        print(str(_fail) + " test(s) failed")
        sys.exit(1)
    print("all tests passed")
    sys.exit(0)
