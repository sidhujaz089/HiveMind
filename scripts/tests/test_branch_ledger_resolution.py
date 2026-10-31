#!/usr/bin/env python3
"""t-3c (handover 2026-10-23 item 5): the branch ledger's 24 ghost rows -- cause and cure.

WHY THEY APPEAR (measured on the live ledger, 2026-10-23):
workspace/run-logs/branch-ledger.jsonl holds 1070 rows. At every session close, session_end
asks git which coder/* branches are unmerged and appends one row per name (t753). A row's
truth is time-scoped -- "this branch was unmerged at ts". When Claude merges a branch (and
usually deletes it), git stops listing it as unmerged, so from the next close onward its
historical rows are reported by reconcile() as GHOSTS: rows naming a branch the current
population no longer contains. The live ledger today has exactly 24 (reconcile's own output):
13 branches deleted after merging + 11 merged-but-kept. Every future merge adds more.

So the defect is not the rows -- an append-only ledger MUST keep them -- and not the check.
It is the missing end of the lifecycle: nothing in the ledger records the terminal event,
"this branch was merged / deleted", so reconcile has no way to tell a retired history from a
dangling one, and the two-directional check cries at every legitimate merge forever.

THE CURE this suite pins (the cause, not the rows):
1. session_end.resolution_rows(ledger_rows, unmerged, actor, repo_root) -- one row per
   recorded branch that is no longer unmerged and has no resolution row yet:
   merged=True, resolution="merged" (git still resolves the name) or "deleted" (it does not;
   head carries the last head recorded for that branch). Idempotent: a retired branch is
   never re-resolved.
2. reconcile(branches, rows) -- a branch retired by a resolution row is REPORTED AS RESOLVED,
   not as a ghost; an unresolvable row stays a ghost (the t753 property is preserved).
3. main() records the resolution rows at the same close-out that records the unmerged ones.

Hermetic: every repo is a fresh `git init` under tempfile.TemporaryDirectory; every git call
carries a timeout; the live ledger and the live checkout are never touched; no model is called.
"""
import json
import re
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "session_end.py"
ALPHA, BETA, GAMMA = "coder/alpha-1111", "coder/beta-2222", "coder/gamma-3333"
GIT_CFG = ["-c", "user.email=t-3c@fixture.invalid", "-c", "user.name=t-3c fixture"]


def load_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("session_end_t3c", MODULE_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {MODULE_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    missing = [n for n in ("unmerged_branches", "rows_from_git", "append_rows", "read_rows",
                           "reconcile", "resolution_rows") if not hasattr(mod, n)]
    if missing:
        raise AssertionError(f"scripts/session_end.py defines none of: {', '.join(missing)} "
                             "(t-3c: the merge/delete resolution does not exist)")
    return mod


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *GIT_CFG, *args],
                          capture_output=True, text=True, errors="replace", timeout=60)


def _commit(repo, filename, body):
    (repo / (filename + ".txt")).write_text(body + "\n", encoding="utf-8")
    assert git(repo, "add", "-A").returncode == 0
    r = git(repo, "commit", "-q", "-m", body)
    assert r.returncode == 0, r.stderr
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def build_repo(tmp):
    """main + ALPHA/BETA/GAMMA as unmerged coder/* branches (one commit each)."""
    repo = Path(tmp) / "repo"
    repo.mkdir()
    assert git(repo, "init", "-q").returncode == 0
    _commit(repo, "base", "base commit")
    assert git(repo, "branch", "-m", "main").returncode == 0
    heads = {}
    for name in (ALPHA, BETA, GAMMA):
        assert git(repo, "checkout", "-q", "-b", name).returncode == 0
        heads[name] = _commit(repo, name.replace("/", "_"), "work on " + name)
        assert git(repo, "checkout", "-q", "main").returncode == 0
    return repo, heads


def _row(branch, head, **over):
    r = {"ts": "2026-10-20T02:05:46Z", "branch": branch, "head": head, "task_id": "",
         "review_state": "", "merged": False, "actor": "claude-code/opus-5.5"}
    r.update(over)
    return r


def test_resolution_rows_record_the_terminal_event():
    mod = load_module()
    with tempfile.TemporaryDirectory() as tmp:
        repo, heads = build_repo(tmp)
        # record today's unmerged population (all three)
        recorded = [ _row(ALPHA, heads[ALPHA]), _row(BETA, heads[BETA]), _row(GAMMA, heads[GAMMA]) ]
        # Claude merges ALPHA into main and keeps the branch; merges BETA and deletes it.
        assert git(repo, "merge", "-q", "--no-ff", "-m", "merge " + ALPHA, ALPHA).returncode == 0
        assert git(repo, "merge", "-q", "--no-ff", "-m", "merge " + BETA, BETA).returncode == 0
        assert git(repo, "branch", "-D", BETA).returncode == 0
        unmerged = sorted(mod.unmerged_branches(repo))
        assert unmerged == [GAMMA], f"fixture: only GAMMA is unmerged now, got {unmerged!r}"

        res = mod.resolution_rows(recorded, unmerged, actor="claude-code/opus-5.5", repo_root=repo)
        got = sorted(r["branch"] for r in res)
        assert got == sorted([ALPHA, BETA]), f"one resolution per retired branch: {got!r}"
        by = {r["branch"]: r for r in res}
        assert by[ALPHA]["merged"] is True and by[BETA]["merged"] is True, "resolution rows set merged=True"
        assert by[ALPHA]["resolution"] == "merged", f"ALPHA exists in git: {by[ALPHA]!r}"
        assert by[BETA]["resolution"] == "deleted", f"BETA was deleted: {by[BETA]!r}"
        assert by[ALPHA]["head"] == heads[ALPHA], "merged: git resolves the name -- its real head"
        assert by[BETA]["head"] == heads[BETA], \
            "deleted: git cannot resolve the name -- the head last recorded for it (never blank)"
        for r in res:
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", r["ts"]), f"ts: {r!r}"
            assert r["actor"] == "claude-code/opus-5.5"
        assert all(r["branch"] != GAMMA for r in res), "the still-unmerged branch must not be resolved"


def test_resolution_is_idempotent():
    mod = load_module()
    with tempfile.TemporaryDirectory() as tmp:
        repo, heads = build_repo(tmp)
        recorded = [_row(ALPHA, heads[ALPHA]), _row(BETA, heads[BETA])]
        assert git(repo, "merge", "-q", "--no-ff", "-m", "merge", ALPHA).returncode == 0
        assert git(repo, "branch", "-D", BETA).returncode == 0
        unmerged = sorted(mod.unmerged_branches(repo))
        first = mod.resolution_rows(recorded, unmerged, repo_root=repo)
        assert len(first) == 2, f"two retired branches, two rows: {first!r}"
        second = mod.resolution_rows(recorded + first, unmerged, repo_root=repo)
        assert second == [], f"a retired branch must never be resolved twice: {second!r}"


def test_reconcile_reports_resolved_branches_instead_of_ghosts():
    mod = load_module()
    with tempfile.TemporaryDirectory() as tmp:
        repo, heads = build_repo(tmp)
        recorded = [_row(ALPHA, heads[ALPHA]), _row(GAMMA, heads[GAMMA])]
        assert git(repo, "merge", "-q", "--no-ff", "-m", "merge", ALPHA).returncode == 0
        unmerged = sorted(mod.unmerged_branches(repo))          # [BETA, GAMMA]
        assert unmerged == [BETA, GAMMA], f"fixture: {unmerged!r}"
        res = mod.resolution_rows(recorded, unmerged, repo_root=repo)
        rep = mod.reconcile(unmerged, recorded + res)
        assert "resolved" in rep, f"reconcile must report the resolved population: keys {sorted(rep)}"
        assert rep["resolved"] == [ALPHA], f"ALPHA retired by a merged row: {rep!r}"
        assert rep["ghosts"] == [], f"a retired branch is not a ghost: {rep!r}"
        assert rep["unrecorded"] == [BETA], f"BETA (unmerged, no row) stays unrecorded: {rep!r}"
        assert rep["ok"] is False, f"an unrecorded branch keeps the close-out honest: {rep!r}"


def test_reconcile_still_reports_true_ghosts():
    """The t753 property survives: a row for a branch the population never had, with no
    resolution row, is a ghost -- resolving must not become a silencing."""
    mod = load_module()
    with tempfile.TemporaryDirectory() as tmp:
        repo, heads = build_repo(tmp)
        unmerged = sorted(mod.unmerged_branches(repo))
        rows = [_row("coder/never-existed", "f" * 40)]
        rep = mod.reconcile(unmerged, rows)
        assert rep["ghosts"] == ["coder/never-existed"], f"{rep!r}"
        assert rep["resolved"] == [], f"{rep!r}"
        assert rep["ok"] is False


def test_main_records_resolutions_at_the_same_close_out():
    """A fix defined and never called is the t753 defect wearing a green suite."""
    src = MODULE_PATH.read_text(encoding="utf-8")
    calls = [l.strip() for l in src.splitlines()
             if "resolution_rows(" in l and not l.strip().startswith(("def ", "#", '"""', "'''"))]
    assert calls, f"{MODULE_PATH} must CALL resolution_rows at session close, not only define it"
    appends = [l.strip() for l in src.splitlines()
               if re.search(r"append_rows\(\s*BRANCH_LEDGER\s*,\s*res_rows\s*\)", l)]
    assert appends, f"the resolution rows must be appended to the ledger: {calls!r}"


if __name__ == "__main__":
    import traceback
    _tests = sorted((n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f))
    _failed = 0
    for _name, _fn in _tests:
        try:
            _fn()
            print(f"PASS {_name}")
        except Exception as _exc:
            _failed += 1
            print(f"FAIL {_name}: {_exc}")
            traceback.print_exc()
    print(f"\n{len(_tests) - _failed} passed, {_failed} failed")
    raise SystemExit(1 if _failed else 0)
