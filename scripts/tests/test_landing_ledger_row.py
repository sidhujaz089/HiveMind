#!/usr/bin/env python3
"""test_landing_ledger_row.py -- oracle for t1077: a refused commit records its own refusal.

RED ON MAIN. The hook it drives, scripts/hooks/pre-commit, contains no reference to
workspace/landing-ledger.jsonl at all: the 203-line file was read in full, and its t650b
section refuses the commit via fail() and writes nothing. workspace/landing-ledger.jsonl
does not exist on this machine (verified: ls -> No such file). The three refusal scenarios
below therefore find no ledger and fail; the two negative scenarios pass on main -- they are
the decay guards for the new code, not proof of it.

WHY. t774's coder built the change the reviewer ACCEPTED, twice, and the commit gate refused
both times -- 'a staged script turns a suite that uses it red -- every coder build would
refuse' -- because a suite importing the staged script is red on main. The refusal left no
branch, no artefact and no record, so dispatch filed the failure class 'unclassified' and
every later reader was told the brief was at fault (workspace/rca/937ac16cbb31.md sec. 2).
The endpoint that discards accepted work records nothing, so nobody can see the class grow.

SHAPE. The REAL hook text is copied into throwaway `git init` repos with core.hooksPath
pointed at it, exactly as scripts/tests/test_precommit_record_sweep.py does. The hook's
sibling helpers (signature_gate.py, library.py, tests/test_sampling_single_source.py) are
stubbed to a clean pass, so the only thing that can refuse a commit here is the t650b section.
That section is guarded by a test for scripts/related_tests.py, so each fixture installs a
stub at that path: the RED stub prints two DISTINCT suite names on '  RED: ' lines -- the
exact format scripts/related_tests.py prints for a red suite -- and exits 1.

Reads and writes only inside tempfile.mkdtemp dirs. No live state, no model, no fixed /tmp
path, no process left running, every subprocess carries a timeout.

Run: python3 scripts/tests/test_landing_ledger_row.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "hooks" / "pre-commit"
LEDGER = "workspace/landing-ledger.jsonl"
TIMEOUT = 180

# The ten fields the task names as the row's shape. A row missing one is a row nothing can
# group by, so every scenario asserts this whole set, never a subset.
FIELDS = ("ts", "task", "brief_sha", "ops_applied", "target_suite", "refusing_gate",
          "red_dep_suites", "ceiling_checked", "branch", "bypassed")

# Two DISTINCT dependent suites, on the lines the real related_tests.py prints (two leading
# spaces, 'RED: ', the bare suite name). The parse the brief specifies strips that prefix.
RED_A = "test_alpha_uses_it.py"
RED_B = "test_beta_uses_it.py"
REFUSE_TEXT = "a staged script turns a suite that uses it red"

STUB_RED = ("import sys\n"
            "print('  related suites: 2 run, 2 red')\n"
            "print('  RED: " + RED_A + "')\n"
            "print('  RED: " + RED_B + "')\n"
            "sys.exit(1)\n")
STUB_GREEN = "import sys\nprint('  related suites: 2 run, 0 red')\nsys.exit(0)\n"
STUB_OK = "import sys; sys.exit(0)\n"

# Four DISTINCT coder-side values: a build that writes one repeated placeholder, or a
# hardcoded null, into task/brief_sha/target_suite/ops_applied cannot satisfy these.
CODER_ENV = {"CODER_TASK": "t1077",
             "CODER_BRIEF_SHA": "9f3c1ab07e4d",
             "CODER_TARGET": "scripts/tests/test_alpha_uses_it.py",
             "CODER_OPS_APPLIED": "7"}


def _git(repo, *args, timeout=TIMEOUT):
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, timeout=timeout)


def _write(repo, rel, body, mode=None):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    if mode is not None:
        p.chmod(mode)


def _make_repo(related_stub):
    """A throwaway repo laid out the way production runs the hook. The three sibling scripts
    the hook calls are stubbed to a clean pass, so the ONLY thing that can refuse a commit
    here is the t650b section. That section is guarded by a test for scripts/related_tests.py
    at its own path, so the stub is installed there."""
    repo = Path(tempfile.mkdtemp(prefix="t1077-"))
    g = _git(repo, "init", "-q")
    if g.returncode != 0:
        raise RuntimeError("git init failed: " + g.stderr)
    _git(repo, "config", "user.email", "t1077@test.local")
    _git(repo, "config", "user.name", "t1077 oracle")
    _write(repo, "scripts/hooks/pre-commit", HOOK.read_text(encoding="utf-8"), 0o755)
    _write(repo, "scripts/signature_gate.py", STUB_OK)
    _write(repo, "scripts/library.py", STUB_OK)
    _write(repo, "scripts/tests/test_sampling_single_source.py", STUB_OK)
    _write(repo, "scripts/related_tests.py", related_stub)
    _git(repo, "config", "core.hooksPath", "scripts/hooks")
    return repo


def _commit(repo, files, env):
    for rel, body in files.items():
        _write(repo, rel, body)
    a = _git(repo, "add", "--", *files)
    if a.returncode != 0:
        raise RuntimeError("git add failed: " + a.stderr)
    e = dict(os.environ)
    e.update(env)
    return subprocess.run(["git", "-C", str(repo), "commit", "-m",
                           "fix(scripts): a staged fix for t1077"],
                          capture_output=True, text=True, timeout=TIMEOUT, env=e)


def _rows(repo):
    """The ledger is JSONL: every non-empty line must parse. A line that does not is a
    finding, not a parse to tolerate -- the row exists so a script can group by it."""
    p = repo / LEDGER
    if not p.is_file():
        return []
    rows = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as exc:
            raise AssertionError("landing ledger line %d is not JSON: %r" % (n, line)) from exc
    return rows


def _refused(repo, env):
    """One scenario: stage a .py under scripts/ and commit. Returns (rc, output)."""
    g = _commit(repo, {"scripts/staged_mod.py": "X = 1\n"}, env)
    return g.returncode, (g.stdout or "") + (g.stderr or "")


def test_a_refused_commit_writes_one_landing_row():
    """The one scenario this module exists for: a commit the reviewer ACCEPTED, refused by
    the related-suites gate, writes a row. Red on main -- there is no ledger to read."""
    repo = None
    try:
        repo = _make_repo(STUB_RED)
        rc, out = _refused(repo, CODER_ENV)
        assert rc != 0, ("the commit was NOT refused, so the fixture never reached the gate, "
                         "and a row found here would be evidence about nothing. Hook output:\n"
                         + out)
        assert REFUSE_TEXT in out, (
            "the commit failed for a reason other than the t650b refusal, so any row below is "
            "not about the gate. Hook output:\n" + out)
        rows = _rows(repo)
        assert len(rows) == 1, (
            "expected exactly one landing row for one refused commit, found %d: %r"
            % (len(rows), rows))
        r = rows[0]
        missing = [k for k in FIELDS if k not in r]
        assert not missing, "the row omits fields the cause class names: %s (row: %r)" % (missing, r)
        # The values, not their presence: a placeholder cannot satisfy four distinct equalities.
        assert r["task"] == CODER_ENV["CODER_TASK"], r["task"]
        assert r["brief_sha"] == CODER_ENV["CODER_BRIEF_SHA"], r["brief_sha"]
        assert r["target_suite"] == CODER_ENV["CODER_TARGET"], r["target_suite"]
        assert r["ops_applied"] == 7, ("ops_applied must be the integer from CODER_OPS_APPLIED, "
                                       "not a string and not null: %r" % (r["ops_applied"],))
        assert isinstance(r["ops_applied"], int) and not isinstance(r["ops_applied"], bool), r
        assert r["red_dep_suites"] == [RED_A, RED_B], (
            "red_dep_suites must be the suites the gate named, in order, unprefixed: %r"
            % (r["red_dep_suites"],))
        assert "related_tests" in str(r["refusing_gate"]), (
            "refusing_gate must say which gate refused, naming the script: %r" % (r["refusing_gate"],))
        assert r["ceiling_checked"] is True, (
            "a refusal from the related-suites section is by definition a run that applied the "
            "ceiling (the section's own comment: known debt is reported, not blocking), so it "
            "is true here and not a bare boolean: %r" % (r["ceiling_checked"],))
        assert r["bypassed"] is False, (
            "a refusal reached through pre-commit is not a --no-verify bypass; bypassed true is "
            "the post-commit row, a later scope: %r" % (r["bypassed"],))
        assert isinstance(r["ts"], str) and "T" in r["ts"] and r["ts"].endswith("Z"), (
            "ts must be a UTC ISO-8601 stamp, computed at write time: %r" % (r["ts"],))
        assert isinstance(r["branch"], str) and r["branch"], (
            "branch must be the branch name, non-empty: %r" % (r["branch"],))
    finally:
        if repo is not None:
            shutil.rmtree(repo, ignore_errors=True)


def test_the_row_is_machine_readable_jsonl():
    """The ledger's whole purpose is to be grouped by a later script: one file, one line per
    refusal, every line valid JSON. Red on main for the same missing change."""
    repo = None
    try:
        repo = _make_repo(STUB_RED)
        _refused(repo, CODER_ENV)
        p = repo / LEDGER
        assert p.is_file(), "%s was not created at the refusal point" % LEDGER
        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 1, "expected one line, found %d: %r" % (len(lines), lines)
        json.loads(lines[0])
    finally:
        if repo is not None:
            shutil.rmtree(repo, ignore_errors=True)


def test_a_commit_the_gate_passes_writes_no_row():
    """NEGATIVE: the writer fires at the REFUSAL, not at every commit. A ledger that grows on
    clean commits is how the writer rots into noise nobody reads. Green on main by absence --
    it is the decay test for the new code, not a proof of it."""
    repo = None
    try:
        repo = _make_repo(STUB_GREEN)
        rc, out = _refused(repo, CODER_ENV)
        assert rc == 0, "a passing gate must not refuse the commit:\n" + out
        assert _rows(repo) == [], "a commit the gate accepted wrote a row: %r" % (_rows(repo),)
        assert not (repo / LEDGER).exists(), (
            "the ledger file must not be created for a commit the gate accepted")
    finally:
        if repo is not None:
            shutil.rmtree(repo, ignore_errors=True)


def test_a_commit_that_stages_no_python_writes_no_row():
    """NEGATIVE: with nothing under scripts/ or projects/ staged as .py, the t650b guard is
    false and the section is skipped -- so no row, even though the installed stub exits 1.
    This is what keeps the writer off every ordinary commit."""
    repo = None
    try:
        repo = _make_repo(STUB_RED)
        g = _commit(repo, {"docs/note.md": "t1077 fixture note\n"}, CODER_ENV)
        assert g.returncode == 0, (
            "a commit staging no .py under scripts/ must not be refused:\n" + (g.stdout or g.stderr))
        assert _rows(repo) == [], "a commit needing no ledger wrote a row: %r" % (_rows(repo),)
    finally:
        if repo is not None:
            shutil.rmtree(repo, ignore_errors=True)


def test_a_human_commit_records_the_coder_fields_as_null():
    """The four fields only a dispatch knows are JSON null when nothing supplied them. A null
    is a gap; a guess is a lie. Red on main for the missing change."""
    repo = None
    try:
        repo = _make_repo(STUB_RED)
        rc, out = _refused(repo, {k: "" for k in CODER_ENV})
        assert rc != 0, "the gate must still refuse with no coder env set:\n" + out
        rows = _rows(repo)
        assert len(rows) == 1, "expected one row, found %r" % (rows,)
        r = rows[0]
        for k in FIELDS:
            assert k in r, "the row must name all ten fields even with no coder env: %r" % (r,)
        for k in ("task", "brief_sha", "target_suite", "ops_applied"):
            assert r[k] is None, "%s must be JSON null when its env var is empty: %r" % (k, r[k])
        assert r["red_dep_suites"] == [RED_A, RED_B], r["red_dep_suites"]
    finally:
        if repo is not None:
            shutil.rmtree(repo, ignore_errors=True)


def main():
    tests = [test_a_refused_commit_writes_one_landing_row,
             test_the_row_is_machine_readable_jsonl,
             test_a_commit_the_gate_passes_writes_no_row,
             test_a_commit_that_stages_no_python_writes_no_row,
             test_a_human_commit_records_the_coder_fields_as_null]
    failed = 0
    for t in tests:
        try:
            t()
            print("ok   %s" % t.__name__)
        except Exception:
            failed += 1
            print("FAIL %s" % t.__name__)
            traceback.print_exc()
    print("%d passed, %d failed" % (len(tests) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
