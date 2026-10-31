#!/usr/bin/env python3
"""t1069 -- the Manager's write_file / edit_file refuse scripts/tests/ of the live checkout.

Why a gate and not a convention: workspace/analysis/t1056-why-work-lands-on-claude.md row 1 --
106 of the 510 tasks that reached Claude came from "six oracles a scoping model wrote untracked
into the live checkout". A scoper that writes its own oracle with write_file lands a suite the
freeze never recorded, so main goes red and every build against it is refused. The write tool is
where that happened, so the write tool is where it stops: an oracle reaches scripts/tests/ only
through a chunks block (scripts/bridge.py), which freezes it in a commit a separate build
implements against.

Run: python3 scripts/tests/test_console_refuse_tests_writes.py   (pytest is not installed)
"""
import contextlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import console  # noqa: E402  verified: importing it takes 0.04s and leaves git status unchanged

LIVE_TESTS = Path(console.REPO) / "scripts" / "tests"   # the module's own REPO -- no path arithmetic
PROBE = "test_zz_t1069_probe.py"


@contextlib.contextmanager
def quiet_actions():
    """Route the tool audit log into a temp dir for the duration of the calls.

    console._action() appends to ACTIONS = <repo>/workspace/manager-actions.jsonl on every
    write_file / edit_file, and that file is tracked -- so calling the handlers in-process, as this
    suite must to test them by name, dirties the worktree the builder runs it in. t1069's first
    scoping was refused oracle-dirty for exactly that, and unlinking the written test files does
    not help: the append is in the handler, and it fires even when the write is refused.
    Redirecting the module's own constant keeps the audit away from the live checkout and keeps
    every assertion about what a write records.
    """
    saved = console.ACTIONS
    with tempfile.TemporaryDirectory(prefix="t1069-actions-") as d:
        console.ACTIONS = Path(d) / "manager-actions.jsonl"
        try:
            yield Path(d)
        finally:
            console.ACTIONS = saved


def test_write_file_refused_into_the_live_scripts_tests():
    target = LIVE_TESTS / PROBE
    assert not target.exists(), "precondition: %s must not exist before the run" % target
    with quiet_actions():
        try:
            out = console.tool_write_file({"path": str(target), "content": "# probe\n"})
        finally:
            if target.exists():
                target.unlink()
    assert out.startswith("REFUSED"), (
        "write_file into the live scripts/tests/ must be refused: a scoping model that writes its "
        "own oracle there lands a suite red on main that no builder's sandbox can see. Got: %r" % out)
    assert "scripts/tests" in out, "the refusal must name the directory it refuses: %r" % out
    assert "chunks" in out, (
        "the refusal must name the sanctioned route (a chunks block), not just say no: %r" % out)
    assert not target.exists(), "a REFUSED write_file must create no file; found %s" % target


def test_edit_file_refused_into_the_live_scripts_tests():
    # A target that does not exist: today the handler refuses it for being missing, and that is the
    # right shape of red -- a refusal that does not name this policy is not this policy.
    target = LIVE_TESTS / "test_zz_t1069_probe_edit.py"
    assert not target.exists(), "precondition: %s must not exist before the run" % target
    with quiet_actions():
        try:
            out = console.tool_edit_file({"path": str(target), "old": "a", "new": "b"})
        finally:
            if target.exists():
                target.unlink()
    assert out.startswith("REFUSED"), "edit_file into scripts/tests/ must be refused: %r" % out
    assert "chunks" in out, "the refusal must name the sanctioned route (a chunks block): %r" % out
    assert not target.exists(), "a REFUSED edit_file must create no file; found %s" % target


def test_writes_everywhere_else_are_unchanged():
    """The guard is a scope, not a ban. Four DISTINCT writes that must all still happen, each with
    the bytes handed to it: a scripts/tests mirror under a temp dir, a path that merely starts with
    the same words, a plain file, and a real edit applied. A blanket write_file ban, a guard that
    searches the path for the substring 'scripts/tests', and a guard that answers without acting
    all fail here."""
    with quiet_actions() as d:
        root = Path(d)
        for rel, body in (("scripts/tests/probe.py", "mirror\n"),
                          ("scripts/tests_extra/probe.py", "neighbour\n"),
                          ("probe.py", "plain\n")):
            target = root / rel
            out = console.tool_write_file({"path": str(target), "content": body})
            assert not out.startswith("REFUSED"), (
                "%s is a scripts/tests directory under a TEMP DIR, not the live checkout's; "
                "write_file must still write there: %r" % (rel, out))
            assert target.is_file(), "write_file must create the file; %s -> %r" % (rel, out)
            assert target.read_text(encoding="utf-8") == body, (
                "write_file must write the content it was given, not a placeholder: %s -> %r"
                % (rel, out))
            assert "created" in out, "the success string for a new file must be unchanged: %r" % out
        ed = root / "scripts" / "tests" / "existing.py"
        ed.parent.mkdir(parents=True, exist_ok=True)
        ed.write_text("alpha\n", encoding="utf-8")
        out = console.tool_edit_file({"path": str(ed), "old": "alpha", "new": "beta"})
        assert not out.startswith("REFUSED"), (
            "edit_file under a temporary scripts/tests/ must still edit: %r" % out)
        assert ed.read_text(encoding="utf-8") == "beta\n", (
            "edit_file must apply the edit, not just report success: %r" % out)
        assert "edited" in out, "the success string must be unchanged: %r" % out


def test_a_refusal_records_nothing_and_an_allowed_write_still_records():
    """The refusal returns BEFORE the audit row; the allowed path still writes one, with its
    required fields. Pins that the guard is a return path and not a log-then-refuse, and that it
    did not delete the recording the handlers exist to do."""
    with quiet_actions() as d:
        log = d / "manager-actions.jsonl"
        blocked = LIVE_TESTS / PROBE
        assert not blocked.exists(), "precondition: %s must not exist before the run" % blocked
        try:
            out_blocked = console.tool_write_file({"path": str(blocked), "content": "x\n"})
        finally:
            if blocked.exists():
                blocked.unlink()
        assert out_blocked.startswith("REFUSED"), (
            "the live scripts/tests/ path must be refused before it records a write: %r" % out_blocked)
        assert not log.exists(), (
            "a REFUSED write_file must not append an audit row for a write that did not happen; "
            "found %s" % log)
        ok = d / "scripts" / "tests" / "probe.py"
        out = console.tool_write_file({"path": str(ok), "content": "y\n"})
        assert not out.startswith("REFUSED"), "the temp-dir mirror must stay writable: %r" % out
        rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(rows) == 1, "the allowed write must record exactly one audit row, got %r" % rows
        assert rows[0].get("tool") == "write_file", "the audit row must name the tool: %r" % rows
        assert rows[0].get("chars") == 2, "the audit row must record the bytes written: %r" % rows


def test_the_existing_empty_path_refusal_is_unchanged():
    with quiet_actions():
        out = console.tool_write_file({"path": "", "content": "x"})
    assert out == "REFUSED: write_file needs a path.", (
        "an empty path is a bad call, not a policy refusal: tool_write_file returns this exact "
        "string today and it must survive byte-identical. Got: %r" % out)


def main():
    tests = [test_write_file_refused_into_the_live_scripts_tests,
             test_edit_file_refused_into_the_live_scripts_tests,
             test_writes_everywhere_else_are_unchanged,
             test_a_refusal_records_nothing_and_an_allowed_write_still_records,
             test_the_existing_empty_path_refusal_is_unchanged]
    bad = 0
    for t in tests:
        try:
            t()
            print("ok    %s" % t.__name__)
        except AssertionError as e:
            bad += 1
            print("FAIL  %s: %s" % (t.__name__, e))
        except Exception as e:
            bad += 1
            print("ERROR %s: %s: %s" % (t.__name__, type(e).__name__, e))
    print("%d/%d passed" % (len(tests) - bad, len(tests)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
