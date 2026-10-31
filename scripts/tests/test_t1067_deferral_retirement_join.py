#!/usr/bin/env python3
"""t1067 -- an OPEN deferral whose record names a retired file must be reported.

The class (workspace/rca/8dd23c39ab72.md): a session deferred a fix to
projects/local-inference/duo.py; that file was deleted in commit aab4e92d; nothing joined
'file retired' to 'open deferral naming that file', so the item sat 37 days as neither
open, closed, nor executable. scripts/check_workspace.py gains the join.

RED ON MAIN FOR ONE REASON: scripts/check_workspace.py defines no
DEFERRAL_CLOSED_STATUSES, deferral_target_paths, format_deferral_orphan or
deferrals_orphaned_by_retirement today, so every test below raises AttributeError naming one
of those four names. Not an import path, not a signature, not a placeholder.

Rows are built in the shape the real deferral ledger uses: scripts/deferral_ledger.py
publishes LEDGER = workspace/session-reviews/deferral-ledger.jsonl with row fields
ts, session, key, quote, due, tracked_as (scripts/tests/test_deferral_ledger.py asserts
both). Those rows carry no status field, so openness is read from status/disposition
compared against DEFERRAL_CLOSED_STATUSES, and from a non-empty covered_by.

No live state: every input is an in-memory dict. No model call, no subprocess, no writes.

Run: python3 scripts/tests/test_t1067_deferral_retirement_join.py  (no pytest on this box)
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGET = ROOT / "scripts" / "check_workspace.py"

# The real retired path from the RCA, and the real surviving host the re-host must name.
DUO = "projects/local-inference/duo.py"
LIVE = "scripts/session_end.py"
MSG = "deferral orphaned by retirement - re-host: " + DUO

_MOD = None


def _mod():
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("check_workspace_t1067", TARGET)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def test_open_deferral_naming_a_retired_file_is_reported_in_the_rca_words():
    # A real-shaped ledger row: no status field, the path lives in key as file:<path>.
    rows = [{"ts": "2026-09-01T04:00:00Z", "session": "aeef1f57",
             "key": "file:" + DUO, "quote": "I'll leave that as a design note",
             "due": "2026-09-08", "tracked_as": "t1067"}]
    out = _mod().deferrals_orphaned_by_retirement(rows, {DUO})
    assert out == [MSG + " (deferral t1067)"], out


def test_an_explicit_id_wins_over_the_key_in_the_rehost_word():
    rows = [{"id": "a1", "key": "file:" + DUO, "status": "open"}]
    out = _mod().deferrals_orphaned_by_retirement(rows, {DUO})
    assert out == [MSG + " (deferral a1)"], out


def test_deferred_is_open_and_a_live_path_is_not_reported():
    # Two distinct ids and two distinct paths; only the retired one may be reported.
    rows = [
        {"id": "b2", "disposition": "deferred", "file": DUO},
        {"id": "c3", "disposition": "deferred", "file": LIVE},
    ]
    out = _mod().deferrals_orphaned_by_retirement(rows, [DUO])
    assert out == [MSG + " (deferral b2)"], out


def test_closed_statuses_are_not_reported():
    m = _mod()
    rows = [{"id": "d4", "status": "closed", "file": DUO},
            {"id": "e5", "disposition": "done", "file": DUO},
            {"id": "f6", "status": "resolved", "file": DUO},
            {"id": "g7", "status": "landed", "file": DUO}]
    assert m.deferrals_orphaned_by_retirement(rows, {DUO}) == [], \
        "closed work must not be re-hosted"


def test_a_deferral_covered_by_a_task_is_not_reported():
    rows = [{"id": "h8", "file": DUO, "covered_by": "t1042"}]
    assert _mod().deferrals_orphaned_by_retirement(rows, {DUO}) == [], \
        "a deferral with a tracked_as owner is already re-hosted"


def test_an_unrecognised_status_is_open_not_silently_skipped():
    # Inversion: the closed set is the small enumerated one; anything outside it is OPEN, so
    # a new status spelling surfaces as a finding instead of as silence -- the silence is
    # what left t1067 in the tree for 37 days.
    rows = [{"id": "i9", "status": "parked-in-a-note", "file": DUO}]
    out = _mod().deferrals_orphaned_by_retirement(rows, {DUO})
    assert out == [MSG + " (deferral i9)"], out


def test_target_paths_reads_the_ledger_fields_in_field_order():
    m = _mod()
    assert m.deferral_target_paths({"key": "file:" + DUO}) == (DUO,)
    assert m.deferral_target_paths({"group": "file:" + DUO}) == (DUO,)
    assert m.deferral_target_paths({"file": "./" + DUO}) == (DUO,)
    assert m.deferral_target_paths({"path": "  " + DUO + "  "}) == (DUO,)
    assert m.deferral_target_paths({"tracked_as": DUO}) == (DUO,)
    assert m.deferral_target_paths({"quote": "the note names " + LIVE + " then " + DUO}) == (LIVE, DUO)
    assert m.deferral_target_paths({}) == ()
    assert m.deferral_target_paths({"key": "t785-item-a"}) == (), "a task key is not a repo path"
    assert m.deferral_target_paths({"key": None}) == (), "a non-string field is not a path"


def test_a_dot_slash_prefixed_retirement_entry_still_matches():
    rows = [{"id": "j10", "status": "open", "file": DUO}]
    out = _mod().deferrals_orphaned_by_retirement(rows, ["./" + DUO])
    assert out == [MSG + " (deferral j10)"], out


def test_a_retirement_ledger_with_nothing_retired_reports_nothing():
    # A stub that reports every open deferral fails here; so does one that ignores the
    # retired set entirely.
    rows = [{"id": "k11", "status": "open", "file": DUO}]
    assert _mod().deferrals_orphaned_by_retirement(rows, []) == []


def test_non_dict_rows_are_skipped_not_fatal():
    # A jsonl ledger read yields a str row when the line is torn; the join must not raise.
    out = _mod().deferrals_orphaned_by_retirement(
        ["torn line", 7, {"id": "m13", "status": "open", "file": DUO}], {DUO})
    assert out == [MSG + " (deferral m13)"], out


def test_format_deferral_orphan_states_the_join_the_path_and_the_id():
    m = _mod()
    assert m.format_deferral_orphan(DUO, "t1067") == (MSG + " (deferral t1067)")
    assert m.format_deferral_orphan(DUO) == MSG


def test_closed_statuses_constant_is_only_the_closed_vocabulary():
    m = _mod()
    for word in ("closed", "done", "resolved", "landed"):
        assert word in m.DEFERRAL_CLOSED_STATUSES, word
    for word in ("open", "deferred", "queued", "unresolved", "missed"):
        assert word not in m.DEFERRAL_CLOSED_STATUSES, word


def _main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("ok   " + name)
        except Exception as e:                      # noqa: BLE001
            failed += 1
            print("FAIL " + name + ": " + type(e).__name__ + ": " + str(e))
    print("%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
