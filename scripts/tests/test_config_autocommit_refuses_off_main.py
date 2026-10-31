#!/usr/bin/env python3
"""t1392: config_autocommit must REFUSE to commit the Models-tab files while HEAD is not main.

WHY (workspace/rca/abf0787375b4.md): a Models-tab edit made during a branch episode is
auto-committed to the disposable branch HEAD happens to be on, and that branch is deleted at
the end of the episode -- the write side of the 2026-10-09 hazard, unguarded in the code.

RED ON MAIN: commit_now() has no branch check, so on a branch it commits and returns True.

Every repository here is a tempfile.TemporaryDirectory created by 'git init'; every
subprocess.run carries timeout=; there is no live repo, no network, no model call, and no
process left running.
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import config_autocommit as ca  # noqa: E402

FILES = list(ca.FILES)
BRANCH = "dsh/dsh-mastery-skills"      # the branch the RCA's episode was committed to
GIT = ["git", "-c", "user.email=t@t.invalid", "-c", "user.name=Test"]


def _git(repo, *args):
    """Every call carries timeout= (the oracle-gate rule); 120s is far past what git init needs."""
    return subprocess.run(GIT + ["-C", str(repo), *args], capture_output=True, text=True,
                          timeout=120)


def _repo(tmp):
    """A committed repo on branch main, containing the Models-tab files the module commits."""
    repo = Path(tmp) / "repo"
    repo.mkdir(parents=True)
    for args in (("init", "-q"), ("checkout", "-qb", "main"),
                 ("config", "user.email", "t@t.invalid"), ("config", "user.name", "Test")):
        r = _git(repo, *args)
        assert r.returncode == 0, f"git {' '.join(args)}: {r.stdout}{r.stderr}"
    for rel in FILES:
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}\n", encoding="utf-8")
    (repo / FILES[1]).write_text(json.dumps({"what": "seed", "who": "Jas"}) + "\n", encoding="utf-8")
    for args in (("add", "-A"), ("commit", "-qm", "seed")):
        r = _git(repo, *args)
        assert r.returncode == 0, f"git {' '.join(args)}: {r.stdout}{r.stderr}"
    return repo


def _n(repo):
    r = _git(repo, "rev-list", "--count", "HEAD")
    assert r.returncode == 0, r.stdout + r.stderr
    return int(r.stdout.strip())


def _dirty(repo):
    """The state a Models-tab save leaves: ROLES.json changed on disk, plus a new changelog line."""
    (repo / FILES[0]).write_text(json.dumps({"roles": {"manager": "gemma4:26b"}}) + "\n",
                                 encoding="utf-8")
    with (repo / FILES[1]).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"what": "manager model", "who": "Jas"}) + "\n")


class _Tally:
    """Wraps ca._git: forwards every call unchanged and records the argument tuples, so the
    guard's cost can be measured. Refuses nothing -- it is a tap, not a stub."""

    def __init__(self):
        self.calls = []
        self._real = ca._git

    def __call__(self, *args, **kw):
        self.calls.append(args)
        return self._real(*args, **kw)


def test_refuses_to_commit_on_a_branch_and_says_which():
    prev = (ca.REPO, ca.CHANGELOG, ca._git)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            _dirty(repo)
            r = _git(repo, "checkout", "-qb", BRANCH)
            assert r.returncode == 0, r.stdout + r.stderr
            before = _n(repo)
            ca.REPO, ca.CHANGELOG = repo, repo / FILES[1]
            ca._git = _Tally()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ok, why = ca.commit_now()
            said = out.getvalue()
            assert ok is False, f"a branch episode must be refused, got ok={ok!r} why={why!r}"
            text = str(why) + said
            assert BRANCH in text, f"the refusal must name the branch it refused on: {text!r}"
            assert "main" in text, f"the refusal must name the branch required: {text!r}"
            assert "git commit --only" in said, \
                f"print the command to commit deliberately: {said!r}"
            assert said.strip(), f"the refusal must be printed, not only returned: {why!r}"
            assert _n(repo) == before, "the refusal must be real: no commit may land on the branch"
            syms = [c for c in ca._git.calls if c and c[0] == "symbolic-ref"]
            assert len(syms) == 1, \
                f"the guard must cost one subprocess call, saw {len(syms)}: {ca._git.calls}"
    finally:
        ca.REPO, ca.CHANGELOG, ca._git = prev


def test_a_detached_tree_is_refused_and_named_as_detached():
    """symbolic-ref fails on a detached HEAD. A detached tree is not main; the reason must say
    'detached', not print an empty branch name -- an empty name names nothing."""
    prev = (ca.REPO, ca.CHANGELOG, ca._git)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            _dirty(repo)
            r = _git(repo, "checkout", "-q", "--detach")
            assert r.returncode == 0, r.stdout + r.stderr
            before = _n(repo)
            ca.REPO, ca.CHANGELOG = repo, repo / FILES[1]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ok, why = ca.commit_now()
            assert ok is False, f"a detached tree must be refused, got {ok!r} why={why!r}"
            assert "detached" in (str(why) + out.getvalue()).lower(), \
                f"the reason must say the tree is detached: {why!r} {out.getvalue()!r}"
            assert _n(repo) == before, "no commit may land on a detached tree"
    finally:
        ca.REPO, ca.CHANGELOG, ca._git = prev


def test_on_main_the_models_tab_commit_still_lands():
    """The positive control: a guard that refuses everything passes nothing. On main the
    Models-tab files must still be committed -- exactly one commit, carrying FILES and
    nothing else staged (the module's own guarantee)."""
    prev = (ca.REPO, ca.CHANGELOG, ca._git)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            _dirty(repo)
            before = _n(repo)
            ca.REPO, ca.CHANGELOG = repo, repo / FILES[1]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ok, why = ca.commit_now()
            assert ok is True, f"on main the Models-tab commit must happen: {why!r}"
            assert _n(repo) == before + 1, f"exactly one commit: {why!r}"
            shown = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
            assert FILES[0] in shown, f"the commit must carry the Models-tab file: {shown!r}"
            assert FILES[2] not in shown, f"an untouched file must not ride along: {shown!r}"
    finally:
        ca.REPO, ca.CHANGELOG, ca._git = prev


if __name__ == "__main__":
    fails = 0
    for name in sorted(n for n in list(globals()) if n.startswith("test_")):
        fn = globals()[name]
        if not callable(fn):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            fails += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{fails} failed")
    sys.exit(1 if fails else 0)
