"""t1121: coder.py's sandbox() must survive a file that vanishes mid-copy.

Dispatch p495-t1099-memory died in shutil.copytree before the model was ever invoked:
workspace/scoping-lease.slot2.json existed when copytree LISTED the directory and was
deleted by a lease release before copytree COPIED it -- a TOCTOU on a file modellease.py
creates and deletes by design.

The contract pinned here is NOT "ignore the error". A sandbox that silently loses files is
indistinguishable from a clean one. A vanished transient file is SKIPPED AND RECORDED: the
record names the file and the cause, so a build that lost something says so.

Run: python3 scripts/tests/test_coder_sandbox_transient_copy.py
"""
import contextlib
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CODER = REPO / "projects" / "local-inference" / "coder.py"

# Three DISTINCT lease-slot names in three locations: the top-level copy2 path, the generic
# directory branch, and the workspace branch (the one in the crash). A fix that patches one
# branch and not the others, or appends one generic "something vanished" entry, fails here.
VANISHED = ("scoping-lease.slot1.json", "scoping-lease.slot2.json", "scoping-lease.slot3.json")


def load_coder():
    """Import coder.py from the path it really lives on (the pattern proven in
    scripts/tests/test_coder_scope_apply.py)."""
    spec = importlib.util.spec_from_file_location("coder_under_test", CODER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_repo():
    """A tiny repository: three keepers and three lease-slot files. Everything lives under a
    temp dir; nothing in the repository is created, touched or deleted by this suite."""
    root = Path(tempfile.mkdtemp())
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
def vanishing(names):
    """shutil.copy2 raises FileNotFoundError for `names` -- exactly what a lease release does
    between a directory listing and the copy. copytree resolves copy2 through the module
    global, so patching it is what the real race does to the copy machinery."""
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
    """One sandbox build. If the crash is back, fail naming the race, so the suite is red for
    the stated reason and not for a bare traceback nobody can act on."""
    try:
        return mod.sandbox()
    except (shutil.Error, FileNotFoundError) as e:
        raise AssertionError("sandbox() died on a file that vanished mid-copy (t1121): " + repr(e))


def test_a_vanishing_file_does_not_kill_the_sandbox():
    """The defect: the dispatch aborted before the model ran. The keepers must still be
    copied -- a fix that abandons the whole directory is not a fix."""
    c = load_coder()
    c.REPO = fake_repo()
    with vanishing(set(VANISHED)):
        root = build(c)
    try:
        assert (root / "keep.py").is_file(), "the top-level keeper must be copied"
        assert (root / "scripts" / "keep_script.py").is_file(), (
            "the file that shared a directory with a vanished one must still be copied -- "
            "skipping a vanished file must not skip its whole directory")
        assert (root / "workspace" / "keep_ws.py").is_file(), (
            "the workspace branch, the one in the crash, must survive it too")
        for rel in (c.REPO / VANISHED[0], c.REPO / "scripts" / VANISHED[1],
                    c.REPO / "workspace" / VANISHED[2]):
            assert rel.is_file(), "the fixture's own source files must not be deleted"
        for rel in (root / VANISHED[0], root / "scripts" / VANISHED[1],
                    root / "workspace" / VANISHED[2]):
            assert not rel.exists(), "a vanished file must not be faked into the sandbox: " + str(rel)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_every_vanished_file_is_recorded_by_name_and_cause():
    """swallowed-exception-introduced: the handler must RECORD what it caught. A silent
    `except Exception: pass`, an onerror that returns None, records nothing and passes
    nothing here."""
    c = load_coder()
    c.REPO = fake_repo()
    with vanishing(set(VANISHED)):
        root = build(c)
    try:
        recs = [str(r) for r in c.SANDBOX_SKIPS]
        assert len(recs) == 3, "exactly the three vanished files must be recorded, got " + repr(recs)
        for name in VANISHED:
            hits = [r for r in recs if name in r]
            assert len(hits) == 1, (
                "each vanished file gets its OWN record naming it; one generic entry is not "
                "an account of what was lost: " + name + " in " + repr(recs))
        for r in recs:
            assert "No such file" in r, "each record names the cause, not just that something " \
                                        "was skipped: " + r
            assert "keep" not in r, "a file that was copied must never be reported as lost: " + r
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_record_list_describes_this_build_only():
    """A module-level list that accumulates reads as \"the same files vanished again\" on every
    later dispatch. A build must report its own skips, not the running total of all builds."""
    c = load_coder()
    c.REPO = fake_repo()
    with vanishing(set(VANISHED)):
        first = build(c)
        shutil.rmtree(first, ignore_errors=True)
        c.REPO = fake_repo()
        root = build(c)
    try:
        recs = [str(r) for r in c.SANDBOX_SKIPS]
        assert len(recs) == 3, "the second build must report 3 skips, not two builds' worth: " + repr(recs)
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
