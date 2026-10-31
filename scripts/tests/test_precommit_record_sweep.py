#!/usr/bin/env python3
"""test_precommit_record_sweep.py -- oracle for t452.

`git commit -a` stages every tracked change, so a one-file code fix can ride
out with 100+ workspace run records (ledgers, manager answers, task files):
ee88933, a952cb7 and e306fe3 each did it (45, 100 and 129 records into a
one-file fix). By the time pre-commit runs the -a flag is gone and only the
staged set is visible, so the gate must be result-based: a code commit
(scripts/ or projects/) must not carry more than RECORD_CAP workspace record
files, and the two legitimate carriers -- a `session:` closeout and a
`chore(workspace)` batch -- keep passing.

The oracle runs the real hook (scripts/hooks/pre-commit) inside throwaway git
repos laid out exactly as production (core.hooksPath=scripts/hooks), so it is
red on main (the hook has no such section yet) and green once section 7
exists. The two sibling scripts the hook invokes (signature_gate.py,
library.py) are stubbed to a clean pass so only the record-sweep section is
under test. Every repo is a tempfile.mkdtemp dir; no live state is touched.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "hooks" / "pre-commit"

CODE = {"scripts/fix.py": "# t452 one-file fix\nprint('ok')\n"}
RECORDS = {
    "workspace/agent-runs.jsonl": '{"event": "fixture", "n": 1}\n',
    "workspace/backup-runs.jsonl": '{"event": "fixture", "n": 2}\n',
    "workspace/dispatch-loop.jsonl": '{"event": "fixture", "n": 3}\n',
    "workspace/run-logs/manager-queue/out/t001.md": "# t452 fixture run log\n",
    "workspace/manager-answers/2026-09-28/answer-1.md": "# t452 fixture answer\n",
    "workspace/tasks/t001.md": "# t452 fixture task\n",
}


def _git(repo, *args, timeout=120):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)


def _write(repo, rel, body):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _make_repo():
    repo = Path(tempfile.mkdtemp(prefix="t452-"))
    g = _git(repo, "init", "-q")
    if g.returncode != 0:
        raise RuntimeError("git init failed: " + g.stderr)
    _git(repo, "config", "user.email", "t452@test.local")
    _git(repo, "config", "user.name", "t452 oracle")
    hooks = repo / "scripts" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text(HOOK.read_text(encoding="utf-8"))
    (hooks / "pre-commit").chmod(0o755)
    (hooks.parent / "signature_gate.py").write_text("import sys; sys.exit(0)\n")
    (hooks.parent / "library.py").write_text("import sys; sys.exit(0)\n")
    # The hook also runs this suite on any staged .py (t216, 2026-09-30); a fixture repo has no
    # copy, so stub it to a pass like the two scripts above -- only section 7 is under test.
    (hooks.parent / "tests").mkdir(exist_ok=True)
    (hooks.parent / "tests" / "test_sampling_single_source.py").write_text("import sys; sys.exit(0)\n")
    # THE CHECK LIVES IN commit-msg (2026-10-01): it tells the carriers apart by subject, which
    # pre-commit never sees. The real commit-msg and its helper are installed; role_gate.py is
    # absent from the fixture, so commit-msg skips it, and every subject carries a trailer.
    for name in ("commit-msg", "record_sweep.py"):
        (hooks / name).write_text((HOOK.parent / name).read_text(encoding="utf-8"))
        (hooks / name).chmod(0o755)
    _git(repo, "config", "core.hooksPath", "scripts/hooks")
    return repo


def _scenario(name, code_files, record_files, subject, expect_refused):
    repo = None
    try:
        repo = _make_repo()
        for rel, body in code_files.items():
            _write(repo, rel, body)
        for rel, body in record_files.items():
            _write(repo, rel, body)
        paths = list(code_files) + list(record_files)
        _git(repo, "add", "--", *paths)
        g = _git(repo, "commit", "-m", subject + "\n\nAgent: human/jas")
        out = (g.stdout or "") + (g.stderr or "")
        refused = g.returncode != 0
        ok = (refused == expect_refused)
        if expect_refused:
            ok = ok and "sweep" in out.lower()
        print(("[PASS] " if ok else "[FAIL] ") + name
              + ": rc=%d refused=%s want_refused=%s" % (g.returncode, refused, expect_refused))
        if not ok:
            for line in out.splitlines():
                print("    | " + line)
        return ok
    finally:
        if repo is not None:
            shutil.rmtree(repo, ignore_errors=True)


def main():
    results = [
        _scenario("code fix swept with 6 records is refused",
                  CODE, RECORDS, "fix(scripts): a one-file fix", True),
        _scenario("code fix with 2 records (at the cap) still commits",
                  CODE, {k: v for k, v in list(RECORDS.items())[:2]},
                  "fix(scripts): a one-file fix", False),
        _scenario("records-only commit still commits",
                  {}, RECORDS, "chore(workspace): ledger batch", False),
        _scenario("session closeout carrying records still commits",
                  CODE, RECORDS, "session: 2026-09-28 close out session", False),
        _scenario("chore(workspace) carrying records still commits",
                  CODE, RECORDS, "chore(workspace): fix and its records", False),
    ]
    if all(results):
        print("t452 oracle: all scenarios pass")
        return 0
    print("t452 oracle: FAILURES in the scenarios above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
