"""t1358: coder.py must refuse ops that name a file outside the brief's declared scope.

The defect (dispatch p608-t1059-session-review, 2026-10-17, read from its own log): the
dispatch was run --files scripts/session_review.py --target scripts/tests/test_job_ledger.py.
Attempt 1 'asked for 2 file(s), gave 2 new'; attempt 3 'asked for 3 file(s), gave 3 new'.
The suite that went red both times was scripts/tests/test_fast_path.py -- a suite the brief
never named. test_job_ledger.py, the brief's target, is not in the failures at all.

The write door is the damage. coder.py's readable() carries a scope check ('out of target:
dispatch is scoped to ...'); apply_ops() has none. It validates a path against the SANDBOX --
absolute paths, '..', secrets, chat/, and existence -- and never against the brief's declared
file list. So 'ONE file: scripts/session_review.py' in a brief is prose, and prose is what
the model is free to ignore.

Red on main for the stated reason: apply_ops is defined as def apply_ops(root, ops) and has
no declared-scope parameter, so every call below that passes files= raises TypeError. That is
not a bug in this test -- it is the missing change. Tests 4 and 5 pass on main BY DESIGN: they
exist to stop the implementation from being 'refuse everything'.

Run: python3 scripts/tests/test_coder_scope.py
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "coder_under_test", ROOT / "projects" / "local-inference" / "coder.py")
coder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(coder)

IN_SCOPE = "in_scope.py"
OUT_SCOPE = "out_of_scope.py"
ANCHOR = "VALUE = 1  # marker"
REPLACED = "VALUE = 2  # marker"
OTHER = "OTHER = 1  # marker"
OTHER_REPLACED = "OTHER = 2  # marker"


def sandbox():
    """A throwaway tree holding two files, each with a unique anchor. Nothing outside it is touched."""
    d = Path(tempfile.mkdtemp(prefix="t1358-scope-"))
    (d / IN_SCOPE).write_text(f"{ANCHOR}\n", encoding="utf-8")
    (d / OUT_SCOPE).write_text(f"{OTHER}\n", encoding="utf-8")
    return d


def op(path, old, new):
    return {"path": path, "old": old, "new": new}


def test_refuses_a_file_outside_the_declared_scope():
    d = sandbox()
    ok, detail = coder.apply_ops(d, [op(OUT_SCOPE, OTHER, OTHER_REPLACED)], files=[IN_SCOPE])
    assert ok is False, f"an op on an undeclared file was accepted: {detail!r}"
    assert OUT_SCOPE in (detail or ""), f"the refusal does not name the file: {detail!r}"
    assert IN_SCOPE in (detail or ""), f"the refusal does not name the allowed file: {detail!r}"
    assert (d / OUT_SCOPE).read_text(encoding="utf-8") == f"{OTHER}\n", \
        "the undeclared file was written: the harness accepted a file outside the brief's scope"


def test_refuses_the_whole_batch_when_one_op_is_out_of_scope():
    """apply_ops's own docstring promises all-or-nothing. Scope must not be the exception: an
    in-scope edit that lands before the out-of-scope one has to be rolled back with it."""
    d = sandbox()
    ok, detail = coder.apply_ops(
        d, [op(IN_SCOPE, ANCHOR, REPLACED), op(OUT_SCOPE, OTHER, OTHER_REPLACED)],
        files=[IN_SCOPE])
    assert ok is False, f"an op on an undeclared file was accepted: {detail!r}"
    assert (d / IN_SCOPE).read_text(encoding="utf-8") == f"{ANCHOR}\n", \
        f"the in-scope op was left applied -- a half-applied change is a state nobody asked " \
        f"for: {detail!r}"
    assert (d / OUT_SCOPE).read_text(encoding="utf-8") == f"{OTHER}\n", \
        f"the out-of-scope op was applied: {detail!r}"


def test_declared_files_are_still_applied():
    """The fix must not be 'refuse everything': a brief's own file has to stay writable, or
    every dispatch on the box dies on scope refusals."""
    d = sandbox()
    ok, detail = coder.apply_ops(d, [op(IN_SCOPE, ANCHOR, REPLACED)], files=[IN_SCOPE])
    assert ok is True, f"a declared file was refused: {detail!r}"
    assert (d / IN_SCOPE).read_text(encoding="utf-8") == f"{REPLACED}\n", \
        "the declared file was not written"


def test_no_declared_scope_changes_nothing():
    """Callers that pass no list keep today's behaviour. apply_ops(root, ops) is the shape the
    existing coder_* suites drive it with; a default that refuses everything breaks them all."""
    d = sandbox()
    ok, detail = coder.apply_ops(d, [op(OUT_SCOPE, OTHER, OTHER_REPLACED)])
    assert ok is True, f"apply_ops(root, ops) stopped applying: {detail!r}"
    assert (d / OUT_SCOPE).read_text(encoding="utf-8") == f"{OTHER_REPLACED}\n", \
        "apply_ops(root, ops) no longer writes"


def test_an_empty_declared_list_does_not_silence_every_write():
    """files=[] means 'no scope declared', not 'nothing is allowed'. A truthiness test, not an
    `is not None` test, is what keeps a caller holding an empty list from losing every op.
    readable() already shows how this goes wrong: its guard is `is not None`."""
    d = sandbox()
    ok, detail = coder.apply_ops(d, [op(IN_SCOPE, ANCHOR, REPLACED)], files=[])
    assert ok is True, f"files=[] refused an op: {detail!r}"
    assert (d / IN_SCOPE).read_text(encoding="utf-8") == f"{REPLACED}\n", \
        "files=[] blocked the write"


def test_a_dotted_prefix_cannot_dodge_the_scope():
    """root / './x' resolves to the same file, so './x' is the out-of-scope file wearing a
    prefix. It must be refused by its real name."""
    d = sandbox()
    ok, detail = coder.apply_ops(d, [op("./" + OUT_SCOPE, OTHER, OTHER_REPLACED)],
                                 files=[IN_SCOPE])
    assert ok is False, f"'./{OUT_SCOPE}' slipped past the scope: {detail!r}"
    assert OUT_SCOPE in (detail or ""), f"the refusal does not name the file: {detail!r}"
    assert (d / OUT_SCOPE).read_text(encoding="utf-8") == f"{OTHER}\n", "the file was written"


if __name__ == "__main__":
    fails = 0
    for name in sorted(n for n in dir() if n.startswith("test_")):
        try:
            globals()[name]()
            print(f"PASS {name}")
        except Exception as e:                                  # noqa: BLE001
            fails += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    sys.exit(1 if fails else 0)
