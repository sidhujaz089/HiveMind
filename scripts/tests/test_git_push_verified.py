#!/usr/bin/env python3
"""t1324: push success is the remote ref advancing -- never a piped exit code.

Source of the rule: workspace/rca/2cbd3724265f.md (transcript 3182/3187).
`git push 2>&1 | tail -3` returned tail's exit 0 while five commits stayed local.
A push is therefore reported successful ONLY when
`git rev-list --count <remote>/<branch>..HEAD` is 0 after the attempt.

The fixture uses a REAL repo and a REAL bare remote inside a tempfile.TemporaryDirectory,
and injects a `run` that models the defect faithfully: it answers the push with exit 0
and moves NO ref -- exactly what `tail` did. A success decision taken from a process exit
code says ok=True here; a decision taken from the remote ref says ok=False.

No model, no network, no live server, no fixed /tmp path, nothing left running.
"""
import subprocess
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # scripts/tests/ -> repo root
MODULE = ROOT / "scripts" / "git_push_verified.py"

GIT = ["-c", "user.email=oracle@local", "-c", "user.name=oracle",
       "-c", "commit.gpgsign=false", "-c", "advice.detachedHead=false"]
SUB = 15   # every git leg in the fixture gets a timeout
TIMEOUT = 60


def _load():
    """Import the module under test from the path it really lives on."""
    import importlib.util
    if not MODULE.is_file():
        raise AssertionError(f"missing module: {MODULE} (root={ROOT})")
    spec = importlib.util.spec_from_file_location("git_push_verified", str(MODULE))
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load spec for {MODULE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo, *args, timeout=SUB):
    return subprocess.run(["git", *GIT, "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)


def _make_repo():
    """A repo with one commit the remote HAS and one commit it does NOT."""
    tmp = tempfile.TemporaryDirectory(prefix="pushver-")
    work = Path(tmp.name) / "work"
    remote = Path(tmp.name) / "remote.git"
    assert _git(tmp.name, "init", "--bare", "-q", str(remote)).returncode == 0
    assert _git(tmp.name, "clone", "-q", str(remote), str(work)).returncode == 0
    (work / "f.txt").write_text("base\n")
    assert _git(work, "checkout", "-q", "-B", "main").returncode == 0
    assert _git(work, "add", "f.txt").returncode == 0
    assert _git(work, "commit", "-q", "-m", "base").returncode == 0
    assert _git(work, "push", "-q", "origin", "main").returncode == 0
    (work / "f.txt").write_text("local-only\n")
    assert _git(work, "commit", "-q", "-am", "unpushed").returncode == 0
    return tmp, work, remote


def _real(cmd, **kw):
    """The module's real leg: git commands only, always with a timeout."""
    if isinstance(cmd, str):
        raise AssertionError(f"push_verified must pass a list argv, got str: {cmd[:80]!r}")
    if "git" not in cmd:
        raise AssertionError(f"push_verified ran a non-git command: {cmd}")
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=kw.get("timeout", TIMEOUT))


def _masking(calls):
    """The 03:03 defect modelled: the push answers 0 and moves nothing.

    Refuses any command it was not written to answer -- a fixture that serves
    everything measures the machinery around it, not the module.
    """
    def run(cmd, **kw):
        calls.append(" ".join(str(c) for c in cmd))
        if "push" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if "rev-list" in cmd:
            return _real(cmd, **kw)
        raise AssertionError(f"fixture got a command it was not written to answer: {cmd}")
    return run


def test_module_exposes_the_contract():
    assert MODULE.is_file(), f"file does not exist: {MODULE}"
    mod = _load()
    assert callable(getattr(mod, "push_verified", None)), "no callable push_verified"


def test_masked_success_is_refused():
    """The case the module exists for: push exits 0, remote ref does not move."""
    mod = _load()
    tmp, work, remote = _make_repo()
    calls = []
    with tmp:
        rep = mod.push_verified(str(work), run=_masking(calls))
        assert isinstance(rep, dict), f"expected a dict report, got {type(rep).__name__}"
        missing = [k for k in ("ok", "count", "reason") if k not in rep]
        assert not missing, f"report missing keys {missing}; has {sorted(rep)}"
        assert rep["ok"] is False, (
            f"reported ok={rep['ok']!r} for a push that moved no ref -- this is the "
            f"03:03 false success: {rep}")
        assert rep["count"] == 1, f"count must be the real unpushed count, got {rep['count']!r}"
        assert "1" in str(rep["reason"]), f"reason must name the unpushed count: {rep['reason']!r}"
        assert "origin/main" in str(rep["reason"]), f"reason must name the remote ref: {rep['reason']!r}"
        assert any("push" in c for c in calls), f"no push was ever attempted; calls={calls}"


def test_real_push_is_success():
    """The mirror image: a push that lands must report success, with the ref proven moved."""
    mod = _load()
    tmp, work, remote = _make_repo()
    with tmp:
        rep = mod.push_verified(str(work), run=_real)
        assert rep["ok"] is True, f"ok={rep['ok']!r} reason={rep['reason']!r}"
        assert rep["count"] == 0, f"count={rep['count']!r} after a landing push"
        assert _git(work, "rev-parse", "origin/main").stdout.strip() == \
               _git(work, "rev-parse", "HEAD").stdout.strip(), "remote ref did not advance to HEAD"


def test_guard_failure_blocks_the_push():
    """A pre-push guard that fails must stop the push, and say why in its own words."""
    mod = _load()
    tmp, work, remote = _make_repo()
    calls = []

    def guard():
        return subprocess.CompletedProcess(["pre-push"], 1, "", "sweep failed: suite X")

    with tmp:
        rep = mod.push_verified(str(work), run=_masking(calls), guard=guard)
        assert rep["ok"] is False, f"a failed guard must not yield success: {rep}"
        assert not any("push" in c for c in calls), f"pushed despite a failed guard: {calls}"
        assert "guard" in str(rep["reason"]).lower(), f"reason must name the guard: {rep['reason']!r}"
        assert "sweep failed" in str(rep["reason"]), (
            f"reason must carry the guard's own output, not just an exit code: {rep['reason']!r}")


def test_success_is_computed_from_ref_state_not_an_exit_code():
    """The decision is the count in the ref query's stdout -- not any process's exit code."""
    mod = _load()
    tmp, work, remote = _make_repo()

    def fake(stdout, rc=0):
        def run(cmd, **kw):
            if "push" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if "rev-list" in cmd:
                return subprocess.CompletedProcess(cmd, rc, stdout, "")
            raise AssertionError(f"fixture got a command it was not written to answer: {cmd}")
        return run

    with tmp:
        ok = mod.push_verified(str(work), run=fake("0\n"))
        assert ok["ok"] is True, f"count 0 must report success: {ok}"
        empty = mod.push_verified(str(work), run=fake(""))
        assert empty["ok"] is False, f"an unreadable count must not default to success: {empty}"
        broken = mod.push_verified(str(work), run=fake("0", rc=128))
        assert broken["ok"] is False, f"a failing ref query must not default to success: {broken}"


if __name__ == "__main__":
    _fns = [(n, f) for n, f in sorted(globals().items())
            if n.startswith("test_") and callable(f)]
    _failed = 0
    for _name, _fn in _fns:
        try:
            _fn()
            print(f"PASS {_name}")
        except Exception as _exc:
            _failed += 1
            print(f"FAIL {_name}: {type(_exc).__name__}: {_exc}")
            traceback.print_exc()
    print(f"\n{len(_fns) - _failed}/{len(_fns)} passed")
    raise SystemExit(1 if _failed else 0)
