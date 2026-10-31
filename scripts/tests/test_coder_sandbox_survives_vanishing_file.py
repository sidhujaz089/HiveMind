#!/usr/bin/env python3
"""t1137: coder.py's sandbox() must survive a file that vanishes mid-copytree, and must SAY
what it skipped.

WHY
---
Dispatch p505-t1114-roles-pages (2026-10-13T22:09:51Z) never reached the model:
shutil.copytree raised shutil.Error because workspace/scoping-lease.json was LISTED and then
deleted by a lease release before it was COPIED. modellease.py creates and deletes these files
by design, so the sandbox build races a file that is supposed to vanish. The same race reaches
the generic directory branch and the top-level copy2, so all three are pinned here.

The contract is NOT "swallow the error". A sandbox that silently loses files is
indistinguishable from a clean one (never-truncate-a-record, swallowed-exception-introduced):
each skipped file gets its OWN record naming the file and the cause, and a build that lost
nothing records nothing.

WHY THIS SUITE IS SEPARATE FROM scripts/tests/test_coder_sandbox_transient_copy.py (t1121)
------------------------------------------------------------------------------------------
That suite carries its own defect. sandbox() names its directory tempdir + int(time.time()) +
pid and creates it with mkdir(parents=True), so a second sandbox() in the same process inside
the same second hits the name the first build left behind and dies in FileExistsError before
it copies anything. Verified on main 2026-10-14: that suite reports 0/3, and tests 2 and 3 die
on FileExistsError inherited from test 1, no matter what coder.py does. This suite gives every
build its own tempfile base, so what a test asserts is coder.py, not the name collision.

Run: python3 scripts/tests/test_coder_sandbox_survives_vanishing_file.py
"""
import contextlib
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CODER = REPO / "projects" / "local-inference" / "coder.py"

# Three DISTINCT lease names in three DISTINCT locations -- the top-level copy2 branch, the
# generic directory branch, and the workspace branch, which is the one in the crash. A fix
# that patches one branch and not the others, or that records one generic entry, fails here.
# scoping-lease.json is the name the crash named; the slot files are the ones modellease.py
# rotates.
VANISHED = ("scoping-lease.json", "scoping-lease.slot1.json", "scoping-lease.slot2.json")
# Each vanished file's own location, so a fix that drops its whole directory is caught.
VREL = (VANISHED[0], "scripts/" + VANISHED[1], "workspace/" + VANISHED[2])
KEEPERS = ("keep.py", "scripts/keep_script.py", "workspace/keep_ws.py")


def load_coder():
    """Import coder.py from the path it really lives on (the pattern in
    scripts/tests/test_coder_scope_apply.py)."""
    spec = importlib.util.spec_from_file_location("coder_under_test", CODER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_repo():
    """A tiny repository under a temp dir. Nothing in the real repository is created, touched
    or deleted by this suite."""
    root = Path(tempfile.mkdtemp(prefix="sbxsrc-"))
    (root / "keep.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / VANISHED[0]).write_text("{}\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "keep_script.py").write_text("VALUE = 2\n", encoding="utf-8")
    (root / "scripts" / VANISHED[1]).write_text("{}\n", encoding="utf-8")
    (root / "workspace").mkdir()
    (root / "workspace" / "keep_ws.py").write_text("VALUE = 3\n", encoding="utf-8")
    (root / "workspace" / VANISHED[2]).write_text("{}\n", encoding="utf-8")
    return root


@contextlib.contextmanager
def temp_base(mod, base):
    """Point coder's tempfile.gettempdir() at `base` for one build, then restore it.

    Needed because sandbox() builds its root as tempdir + coder-<int(time.time())>-<pid> and
    creates it with mkdir(parents=True): two builds in one process inside one second name the
    SAME directory, and the second dies in FileExistsError before it copies anything. A test
    that checks what a build records has to build twice, so each build gets a base of its
    own. coder.py imports the module as `_tempfile`, so that is the attribute patched -- and
    it is restored in the finally, because that module object is the shared `tempfile`;
    leaving it patched would leak into every later test.
    """
    real = mod._tempfile.gettempdir
    mod._tempfile.gettempdir = lambda: str(base)
    try:
        yield
    finally:
        mod._tempfile.gettempdir = real


@contextlib.contextmanager
def vanishing(names):
    """shutil.copy2 raises FileNotFoundError for `names` -- what a lease release does between
    copytree's directory listing and the per-file copy. copytree reaches copy2 through
    shutil's module globals, so this is what the race does to the copy machinery."""
    real = shutil.copy2

    def fake(src, dst, *a, **kw):
        if Path(str(src)).name in names:
            raise FileNotFoundError(2, "No such file or directory")
        return real(src, dst, *a, **kw)

    shutil.copy2 = fake
    try:
        yield
    finally:
        shutil.copy2 = real


def build(mod):
    """One sandbox() call. If it dies on the race, fail naming the race, so the suite is red
    for the stated reason and not for a bare traceback nobody can act on."""
    try:
        return mod.sandbox()
    except (shutil.Error, FileNotFoundError) as e:
        raise AssertionError("sandbox() died on a file that vanished mid-copy (t1137): " + repr(e))


def _cleanup(entries):
    for root, base, src in entries:
        for d in (root, base, src):
            if d is not None:
                shutil.rmtree(d, ignore_errors=True)


def test_a_vanishing_file_does_not_kill_the_build():
    """The defect: the dispatch aborted before the model ran. The files that did NOT vanish,
    in all three code paths, must still be copied -- skipping a vanished file must not skip
    its whole directory, and must not fabricate the vanished file."""
    c = load_coder()
    src = fake_repo()
    c.REPO = src
    base = Path(tempfile.mkdtemp(prefix="sbx-"))
    entries = []
    try:
        with temp_base(c, base), vanishing(set(VANISHED)):
            root = build(c)
        entries.append((root, base, src))
        assert root is not None and root.is_dir(), "sandbox() must return its copy"
        for rel in KEEPERS:
            assert (root / rel).is_file(), (
                "keeper not copied -- a fix that abandons the directory it found a vanished "
                "file in is not a fix: " + rel)
        for rel in VREL:
            assert not (root / rel).exists(), (
                "a vanished file must not be faked into the sandbox: " + rel)
            assert (src / rel).is_file(), (
                "the fixture's source file must not be deleted by the build: " + rel)
    finally:
        _cleanup(entries)


def test_every_vanished_file_is_recorded_by_name_and_cause():
    """The record, not the silence. A bare `except OSError: pass`, an onerror handler that
    returns None, and one generic 'something vanished' entry all pass the test above and fail
    this one: each vanished file gets its OWN record naming the file and the cause."""
    c = load_coder()
    src = fake_repo()
    c.REPO = src
    base = Path(tempfile.mkdtemp(prefix="sbx-"))
    entries = []
    try:
        with temp_base(c, base), vanishing(set(VANISHED)):
            root = build(c)
        entries.append((root, base, src))
        recs = [str(r) for r in c.SANDBOX_SKIPS]
        assert len(recs) == 3, (
            "exactly the three vanished files must be recorded, got " + repr(recs))
        for name in VANISHED:
            hits = [r for r in recs if name in r]
            assert len(hits) == 1, (
                "each vanished file gets its own record naming it; one generic entry is not "
                "an account of what was lost: " + name + " in " + repr(recs))
        for r in recs:
            assert "No such file" in r, (
                "each record names the cause, not just that something was skipped: " + r)
            assert "keep" not in r, (
                "a file that was copied must never be reported as lost: " + r)
    finally:
        _cleanup(entries)


def test_the_record_describes_this_build_only():
    """A module-level list that accumulates reads as 'the same files vanished again' on every
    later dispatch. A build reports its own skips, not the running total of every build."""
    c = load_coder()
    entries = []
    try:
        for _ in range(2):
            src = fake_repo()
            c.REPO = src
            base = Path(tempfile.mkdtemp(prefix="sbx-"))
            with temp_base(c, base), vanishing(set(VANISHED)):
                entries.append((build(c), base, src))
        recs = [str(r) for r in c.SANDBOX_SKIPS]
        assert len(recs) == 3, (
            "the second build must report its own 3 skips, not two builds' worth: " + repr(recs))
    finally:
        _cleanup(entries)


def test_a_build_that_lost_nothing_records_nothing():
    """The negative control. An implementation that records every file it copies, or that
    swallows every OSError and calls it a skip, passes the three tests above by over-reporting;
    this one is green only when a build that lost nothing says it lost nothing."""
    c = load_coder()
    src = fake_repo()
    c.REPO = src
    base = Path(tempfile.mkdtemp(prefix="sbx-"))
    entries = []
    try:
        with temp_base(c, base):
            root = build(c)
        entries.append((root, base, src))
        assert list(c.SANDBOX_SKIPS) == [], (
            "a build that lost no file must record no skips: " + repr(list(c.SANDBOX_SKIPS)))
        for rel in KEEPERS:
            assert (root / rel).is_file(), "the whole tree must be copied: " + rel
    finally:
        _cleanup(entries)


if __name__ == "__main__":
    _fns = [v for k, v in sorted(globals().items())
            if k.startswith("test_") and callable(v) and k != "test"]
    _fails = []
    for _fn in _fns:
        try:
            _fn()
            print("ok   " + _fn.__name__)
        except Exception as _e:                                   # noqa: BLE001
            _fails.append(_fn.__name__)
            print("FAIL " + _fn.__name__ + ": " + repr(_e))
    print("\n%d/%d passed" % (len(_fns) - len(_fails), len(_fns)))
    sys.exit(1 if _fails else 0)
