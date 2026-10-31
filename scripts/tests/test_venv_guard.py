#!/usr/bin/env python3
"""Oracle for the venv guard functions of scripts/check_workspace.py (t655).

2026-10-02: an Ubuntu update moved /usr/bin/python3 from 3.12 to 3.14 and REMOVED
3.12. Both workspace venvs symlink /usr/bin/python3, so every package in them went
invisible and the Chat bridge crash-looped -- found hours later by a symptom, not by
a check. check_runtime_drift (check_workspace.py:2161) can already tell us AFTER the
fact. This suite pins the half that answers BEFORE: given a venv directory and a
requirements lock, say in words a human can read --

  (a) whether the venv's own interpreter runs at all,
  (b) whether it sits on the apt-managed system interpreter that a distro update can
      move out from under the venv, and
  (c) which packages in the lock file that interpreter cannot import.

The functions live in scripts/check_workspace.py, the file the task's approved plan
already named as the consumer, because coder.py edits files and cannot create one.
Imported the way scripts/tests/test_runtime_drift.py imports it -- a proven shape.

Every fixture is built under tempfile.TemporaryDirectory() and thrown away: no live
venv is opened, no network, no model, no service, nothing left running, nothing
written outside the temp dirs.

Run: python3 scripts/tests/test_venv_guard.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace  # noqa: E402

# The shape the guard exists to steer the workspace toward: an interpreter in the
# home folder, which no apt update can move.
UV_HOME = "/home/jas/.local/share/uv/python/cpython-3.12.7-linux-aarch64-gnu/bin"
# The shape that broke on 2026-10-02: a venv built on the apt interpreter.
APT_HOME = "/usr/bin"

# Real lock-file furniture: a comment, an option line, a blank line, an extras
# bracket, an environment marker. The pair list is what a person checks by hand.
LOCK = """# regenerated with uv pip freeze
json==2.0.9
importlib-metadata==8.5.0
definitely-not-installed-pkg-zzz==0.0.1
requests[socks]==2.32.3
--index-url https://pypi.org/simple/

typing-extensions==4.12.2 ; python_version >= \"3.9\"
"""

# Every requirement, in file order, named exactly as the lock wrote it.
LOCK_PAIRS = [
    ("json", "2.0.9"),
    ("importlib-metadata", "8.5.0"),
    ("definitely-not-installed-pkg-zzz", "0.0.1"),
    ("requests[socks]", "2.32.3"),
    ("typing-extensions", "4.12.2"),
]

# A lock whose importability outcome is deterministic in any CPython >= 3.8: json and
# logging are stdlib; the zzz name exists on no index. No third-party package is
# required, so this suite cannot go red because of what happens to be installed.
LOCK_IMPORTABLE = """# regenerated with uv pip freeze
json==2.0.9
logging==0.0.0
definitely-not-installed-pkg-zzz==0.0.1
--index-url https://pypi.org/simple/
"""

GENERIC_REASONS = ("", "broken", "failed", "error", "none", "n/a", "unknown")


def _write_venv(tmp, home, python_to, cfg=True):
    """A venv directory: bin/python3 -> python_to, plus a pyvenv.cfg naming home."""
    venv = Path(tmp) / "venv"
    (venv / "bin").mkdir(parents=True)
    if python_to is not None:
        (venv / "bin" / "python3").symlink_to(python_to)
    if cfg:
        (venv / "pyvenv.cfg").write_text(
            "home = %s\nimplementation = CPython\nversion = 3.12.7\n" % home,
            encoding="utf-8",
        )
    return venv


def _write_lock(tmp, text):
    lock = Path(tmp) / "requirements.lock.txt"
    lock.write_text(text, encoding="utf-8")
    return lock


def test_venv_python_points_at_bin_python3():
    with tempfile.TemporaryDirectory() as td:
        got = str(check_workspace.venv_python(str(Path(td) / "venv")))
        want = str(Path(td) / "venv" / "bin" / "python3")
        assert got == want, "venv_python: expected %s, got %s" % (want, got)


def test_lock_requirements_returns_name_version_pairs_in_order():
    """Real lock furniture: comments, option lines, blanks, extras, a marker."""
    with tempfile.TemporaryDirectory() as td:
        lock = _write_lock(td, LOCK)
        got = list(check_workspace.lock_requirements(str(lock)))
        assert got == LOCK_PAIRS, (
            "lock_requirements must return (name, version) pairs in file order, "
            "skipping comments, blanks and pip option lines, keeping extras and the "
            "version verbatim, and stripping the environment marker\n"
            "  expected: %r\n  got:      %r" % (LOCK_PAIRS, got)
        )


def test_import_name_is_the_module_name_the_interpreter_sees():
    # The dash rule, with the two names a lock really contains.
    assert check_workspace.import_name("importlib-metadata") == "importlib_metadata", (
        "dashes become underscores: the distribution is importlib-metadata, the module "
        "is importlib_metadata -- the lock's spelling cannot be imported"
    )
    assert check_workspace.import_name("Requests[socks]==2.32.3") == "requests", (
        "the import name is stripped of extras, of the version, and lowercased"
    )
    assert check_workspace.import_name("json") == "json", "a plain name passes through"
    # NEGATIVE: the requirement's own spelling must not be handed to the interpreter --
    # that is the defect this mapping exists to prevent.
    assert check_workspace.import_name("typing-extensions") == "typing_extensions", (
        "'typing-extensions' is not importable; the mapped name is 'typing_extensions'"
    )


def test_missing_imports_names_only_the_packages_that_cannot_import():
    with tempfile.TemporaryDirectory() as td:
        lock = _write_lock(td, LOCK_IMPORTABLE)
        got = sorted(check_workspace.missing_imports(str(lock), sys.executable))
        assert got == ["definitely-not-installed-pkg-zzz"], (
            "json and logging are importable by any CPython and must NOT be reported; "
            "the zzz name must be. Reported: %r" % (got,)
        )
        raw = list(check_workspace.missing_imports(str(lock), sys.executable))
        assert "definitely-not-installed-pkg-zzz" in raw, (
            "names are returned as the lock spells them, dashes intact -- the lock is "
            "what a person repairs from. Got: %r" % (raw,)
        )


def test_venv_health_reports_a_healthy_venv_with_no_reason():
    with tempfile.TemporaryDirectory() as td:
        venv = _write_venv(td, UV_HOME, sys.executable)
        h = check_workspace.venv_health(str(venv))
        assert isinstance(h, dict), "venv_health must return a dict, got %r" % (h,)
        assert set(h) == {"ok", "reason", "python", "base", "system"}, (
            "exactly the keys the ledger row and the phone notification read: ok, "
            "reason, python, base, system. Got %r" % (sorted(h),)
        )
        assert h["ok"] is True, "a venv on a working interpreter is ok: %r" % (h,)
        assert h["system"] is False, (
            "a uv-managed base interpreter is NOT the apt one an update can move; "
            "system must be False for base=%r" % (UV_HOME,)
        )
        assert h["base"] == UV_HOME, "base is the home= value verbatim: %r" % (h,)
        assert h["python"].startswith("Python "), (
            "python is the interpreter's own version banner: %r" % (h,)
        )
        assert str(sys.version_info[0]) in h["python"], (
            "the banner must name the major version the venv is on: %r" % (h,)
        )
        # NEGATIVE: a healthy venv must not get a reportable reason.
        assert h["reason"] == "", (
            "ok must be True with an empty reason; a reason on a healthy venv is noise "
            "in the ledger: %r" % (h,)
        )


def test_venv_health_names_the_interpreter_that_died():
    """The 2026-10-02 failure: bin/python3 symlinks an interpreter apt removed."""
    with tempfile.TemporaryDirectory() as td:
        gone = str(Path(td) / "removed-by-apt" / "python3.12")
        venv = _write_venv(td, APT_HOME, gone)
        h = check_workspace.venv_health(str(venv))
        assert h["ok"] is False, "a dangling interpreter is not ok: %r" % (h,)
        assert str(venv / "bin" / "python3") in h["reason"], (
            "reason must name the venv's own interpreter path -- that is the line a "
            "person checks by hand and the line pushed to the phone: %r" % (h,)
        )
        assert h["reason"].strip().lower() not in GENERIC_REASONS, (
            "reason must say WHAT failed, not 'broken'/'failed'/'error': %r" % (h,)
        )
        assert h["base"] == APT_HOME, "base is reported even when it is the cause: %r" % (h,)


def test_venv_on_the_system_python_is_flagged_even_while_it_works():
    """Prevention, not detection: this venv runs today and dies at the next apt."""
    with tempfile.TemporaryDirectory() as td:
        venv = _write_venv(td, APT_HOME, sys.executable)
        h = check_workspace.venv_health(str(venv))
        assert h["ok"] is True, "it runs today, so ok must be True: %r" % (h,)
        assert h["system"] is True, (
            "a venv whose pyvenv.cfg home is /usr/bin IS the 2026-10-02 shape and must "
            "be flagged while it still works: %r" % (h,)
        )
        assert h["base"] == APT_HOME, "base names the apt interpreter dir: %r" % (h,)


def test_missing_pyvenv_cfg_is_reported_not_raised():
    with tempfile.TemporaryDirectory() as td:
        venv = _write_venv(td, UV_HOME, sys.executable, cfg=False)
        h = check_workspace.venv_health(str(venv))
        assert h["ok"] is False, "a venv with no pyvenv.cfg is not ok: %r" % (h,)
        assert "pyvenv.cfg" in h["reason"], (
            "reason must name the file that is missing, not a generic word: %r" % (h,)
        )


def test_missing_venv_directory_is_reported_not_raised():
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "no-such-venv"
        h = check_workspace.venv_health(str(missing))
        assert h["ok"] is False, "a venv directory that is not there is not ok: %r" % (h,)
        assert "no-such-venv" in h["reason"], (
            "the report must name the venv it could not find: %r" % (h,)
        )


def _run():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except AssertionError as exc:
            failures.append(name)
            print("FAIL %s: %s" % (name, exc))
        except Exception as exc:  # a raise is a failing test, never a silent pass
            failures.append(name)
            print("ERROR %s: %s: %s" % (name, type(exc).__name__, exc))
    print("%d/%d passed" % (len(tests) - len(failures), len(tests)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
