#!/usr/bin/env python3
"""t753 -- the session-close recorder for the unmerged-branch population.

RED ON MAIN, FOR THE STATED REASON: scripts/session_end.py exists and imports cleanly, but defines
no unmerged_branches / rows_from_git / append_rows / read_rows / reconcile. Verified 2026-10-19:
`ls scripts/branch_ledger.py` -> "No such file or directory", and `search_repo "branch_ledger"`
finds only the RCA (workspace/rca/0f167ec334ff.md), the manager-queue rows, this task's file, the
refused scope (workspace/scope-defects/done/a59b4d7773d9.json) and the task-session logs -- no code.
load_writer() below raises naming exactly those missing names, so every test here fails for one
reason: the recorder is not written.

WHY THESE ASSERTIONS. The finding is a prose count nobody can reproduce -- "14 branches unmerged."
(workspace/rca/0f167ec334ff.md:1). The surface that reports it names 3 of the 4 branches it counts
(same file, section 1), and the merge work it named has had no owner for nine days. So the recorder
is tested on the two properties the count stands on: it enumerates EVERY unmerged coder/* branch,
named exactly as git names it (no '  ' indent, no '*' marker, no truncation, no merged branch), and
git and the ledger reconcile in BOTH directions. A recorder that is defined but never CALLED is the
same defect wearing a green suite -- the last test asserts the call site exists.

LIVE STATE IS NOT TOUCHED: every repository is a fresh `git init` under tempfile.TemporaryDirectory,
every git call carries timeout=60, the ledger is written inside the temp dir (never under
workspace/run-logs/), nothing outside the temp dir is written, and no model is called.
"""

import json
import re
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "session_end.py"
REQUIRED = ("unmerged_branches", "rows_from_git", "append_rows", "read_rows", "reconcile")
FIELDS = {"ts", "branch", "head", "task_id", "review_state", "merged", "actor"}
ALPHA, BETA, GAMMA = "coder/alpha-1111", "coder/beta-2222", "coder/gamma-3333"
DELTA = "coder/delta-4444"
GIT_CFG = ["-c", "user.email=t753@fixture.invalid", "-c", "user.name=t753 fixture"]


def load_writer():
    """Import scripts/session_end.py from the path it really lives on (no sys.path edit)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("session_end_t753", MODULE_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {MODULE_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    missing = [n for n in REQUIRED if not hasattr(mod, n)]
    if missing:
        raise AssertionError(
            "scripts/session_end.py defines none of: " + ", ".join(missing)
            + " -- the session-close recorder for the unmerged-branch population does not exist (t753)")
    return mod


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *GIT_CFG, *args],
                          capture_output=True, text=True, errors="replace", timeout=60)


def _commit(repo, filename, body):
    (repo / (filename + ".txt")).write_text(body + "\n", encoding="utf-8")
    r = git(repo, "add", "-A")
    assert r.returncode == 0, f"git add failed: {r.stderr}"
    r = git(repo, "commit", "-q", "-m", body)
    assert r.returncode == 0, f"git commit failed: {r.stderr}"
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def build_repo(tmp):
    """A real repo: main + three coder/* branches, GAMMA merged into main, ALPHA/BETA not."""
    repo = Path(tmp) / "repo"
    repo.mkdir()
    r = git(repo, "init", "-q")
    assert r.returncode == 0, f"git init failed: {r.stderr}"
    _commit(repo, "base", "base commit")
    assert git(repo, "branch", "-m", "main").returncode == 0
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    heads = {}
    for name in (ALPHA, BETA, GAMMA):
        r = git(repo, "checkout", "-q", "-b", name)
        assert r.returncode == 0, f"checkout -b {name} failed: {r.stderr}"
        heads[name] = _commit(repo, name.replace("/", "_"), "work on " + name)
        assert git(repo, "checkout", "-q", "main").returncode == 0
    r = git(repo, "merge", "-q", "--no-ff", "-m", "merge " + GAMMA, GAMMA)
    assert r.returncode == 0, f"merge failed: {r.stderr}"
    return repo, heads, base


def test_unmerged_branches_names_the_whole_population_exactly():
    mod = load_writer()
    with tempfile.TemporaryDirectory() as tmp:
        repo, _heads, _base = build_repo(tmp)
        got = sorted(mod.unmerged_branches(repo))
        assert got == [ALPHA, BETA], (
            f"expected exactly {[ALPHA, BETA]} named as git names them, got {got!r} -- a leading "
            f"'  ' indent (git's own format), a '*' current-branch marker, a truncated name, or a "
            f"branch merged into HEAD all fail here")
        assert GAMMA not in got, f"{GAMMA} is merged into main and must not be reported: {got!r}"


def test_a_repo_with_no_unmerged_coder_branches_yields_nothing():
    mod = load_writer()
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        assert git(repo, "init", "-q").returncode == 0
        _commit(repo, "only", "only commit")
        assert git(repo, "branch", "-m", "main").returncode == 0
        assert git(repo, "checkout", "-q", "-b", ALPHA).returncode == 0
        _commit(repo, "work", "work")
        assert git(repo, "checkout", "-q", "main").returncode == 0
        r = git(repo, "merge", "-q", "--no-ff", "-m", "merge", ALPHA)
        assert r.returncode == 0, f"merge failed: {r.stderr}"
        got = mod.unmerged_branches(repo)
        assert got == [], (
            "with every coder/* branch merged the population is empty; an implementation that "
            f"lists every branch, or the current branch, passes the population test by accident: {got!r}")


def test_rows_carry_the_seven_named_fields_with_git_real_values():
    mod = load_writer()
    with tempfile.TemporaryDirectory() as tmp:
        repo, heads, _base = build_repo(tmp)
        rows = sorted(mod.rows_from_git(repo, actor="claude-code/opus-5"), key=lambda r: r["branch"])
        assert [r["branch"] for r in rows] == [ALPHA, BETA], f"got {rows!r}"
        assert set(rows[0]) == FIELDS, f"row keys must be exactly {sorted(FIELDS)}, got {sorted(rows[0])}"
        for r in rows:
            assert r["head"] == heads[r["branch"]], (
                f"{r['branch']}: head {r['head']!r} is not the commit git reports "
                f"({heads[r['branch']]!r}) -- a placeholder, a short hash or HEAD fails here")
            assert re.fullmatch(r"[0-9a-f]{40}", r["head"]), f"head {r['head']!r} is not a 40-hex sha"
            assert r["merged"] is False, f"merged must be False for an unmerged branch, got {r['merged']!r}"
            assert r["actor"] == "claude-code/opus-5", f"actor must be the argument verbatim, got {r['actor']!r}"
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", r["ts"]), \
                f"ts {r['ts']!r} is not UTC as YYYY-MM-DDTHH:MM:SSZ"
            assert isinstance(r["task_id"], str), f"task_id must be a str (the join is a later scope): {r['task_id']!r}"
            assert isinstance(r["review_state"], str), f"review_state must be a str: {r['review_state']!r}"
        assert all(r["branch"] != GAMMA for r in rows), "the merged branch must not be recorded"


def test_append_rows_is_additive_one_json_object_per_line_creating_parent_dirs():
    mod = load_writer()
    with tempfile.TemporaryDirectory() as tmp:
        repo, heads, _base = build_repo(tmp)
        ledger = Path(tmp) / "nested" / "run-logs" / "branch-ledger.jsonl"
        rows = sorted(mod.rows_from_git(repo, actor="t753-scope"), key=lambda r: r["branch"])
        assert mod.append_rows(ledger, rows) == 2, "append_rows must return the number of rows appended"
        assert mod.append_rows(ledger, [{**rows[0], "branch": DELTA, "head": heads[ALPHA]}]) == 1, \
            "a later append reports its own count, not the running total"
        text = ledger.read_text(encoding="utf-8")
        lines = [l for l in text.splitlines() if l.strip()]
        assert len(lines) == 3, f"append must ADD lines, never rewrite: expected 3, got {len(lines)}"
        parsed = [json.loads(l) for l in lines]
        assert [p["branch"] for p in parsed] == [ALPHA, BETA, DELTA], f"order/content: {parsed!r}"
        assert parsed[0] == rows[0], f"a row must round-trip whole, got {parsed[0]!r}"
        assert GAMMA not in text, "the merged branch must never reach the ledger"


def test_read_rows_survives_a_torn_line_and_a_missing_file():
    mod = load_writer()
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / "branch-ledger.jsonl"
        a = {"ts": "2026-10-07T21:09:08Z", "branch": ALPHA, "head": "a" * 40, "task_id": "t753",
             "review_state": "unmerged", "merged": False, "actor": "t753-scope"}
        b = dict(a, branch=BETA)
        ledger.write_text(json.dumps(a) + "\n" + '{"ts": "2026-10-07T21:09:09Z", "branch": "co'
                          + "\n" + json.dumps(b) + "\n", encoding="utf-8")
        rows = mod.read_rows(ledger)
        assert [r["branch"] for r in rows] == [ALPHA, BETA], (
            f"a torn line (a process killed mid-append) must be skipped, not fatal: {rows!r}")
        assert rows[0]["task_id"] == "t753", f"fields must survive the round trip: {rows[0]!r}"
        assert mod.read_rows(Path(tmp) / "absent.jsonl") == [], \
            "a missing ledger is an empty population, not a crash -- session close runs every session"


def test_reconcile_fails_in_both_directions():
    mod = load_writer()

    def row(name):
        return {"branch": name, "head": "b" * 40}

    rep = mod.reconcile([ALPHA, BETA], [row(ALPHA)])
    assert rep["unrecorded"] == [BETA], f"a branch in git with no row must be reported: {rep!r}"
    assert rep["ok"] is False, f"an unrecorded branch cannot reconcile ok: {rep!r}"
    rep2 = mod.reconcile([ALPHA], [row("coder/ghost-9999")])
    assert rep2["ghosts"] == ["coder/ghost-9999"], f"a row naming a branch absent from git must be reported: {rep2!r}"
    assert rep2["ok"] is False, f"a ghost row cannot reconcile ok: {rep2!r}"
    rep3 = mod.reconcile([ALPHA, BETA], [row(ALPHA), row(BETA)])
    assert rep3["ok"] is True and rep3["unrecorded"] == [] and rep3["ghosts"] == [], (
        f"a complete ledger must be ok with both lists empty (a branch covered by a row must not "
        f"be reported): {rep3!r}")


def test_the_writer_calls_the_collector_it_defines():
    """A recorder that is defined and never called is the defect t753 is about (dead-path class)."""
    src = MODULE_PATH.read_text(encoding="utf-8")
    calls = [l.strip() for l in src.splitlines()
             if "rows_from_git(" in l and not l.strip().startswith(("def ", "#", '"""', "'''"))]
    assert calls, (
        f"{MODULE_PATH} must CALL rows_from_git at session close, not only define it -- no call "
        f"site found, so no session would ever write a row")
    assert "branch-ledger.jsonl" in src, (
        "the ledger path workspace/run-logs/branch-ledger.jsonl is not in scripts/session_end.py; "
        "the population must be appended there, not returned to a caller that drops it")


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
