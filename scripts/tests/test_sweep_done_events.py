#!/usr/bin/env python3
"""t1298 red fixture: retiring a note with sweep --done appends a ledger row.

RED ON MAIN: scripts/sweep.py has no record_event, no ACTIONS, no INBOX_EVENTS, and cmd_done
takes no actor/session/task -- every test's first statement raises AttributeError/TypeError,
which is the missing change, not a bug in the test.

No live state: every test runs inside a temp dir with fakes. pytest is not installed.
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "scripts" / "sweep.py"
NOTE_A = "2026-10-17T022625Z--console--model-called-a-tool-that-does-not-exist-run-file.md"
NOTE_B = "2026-09-16T195052Z--chat--unstick-the-hivemind.md"
ACTIONS = ("received", "read", "answered", "retired")
KEYS = ("ts", "note", "action", "actor", "session", "task")
SESSION = "073b80dd616b"
ACTOR = "code"
TASK = "t1298"


def _mod():
    spec = importlib.util.spec_from_file_location("sweep_under_test", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def _temp_root(td):
    """A throwaway repo: inbox/ with a note in it -- nothing under the real workspace/."""
    mod = _mod()
    root = Path(td).resolve()
    inbox = root / "workspace" / "inbox"
    inbox.mkdir(parents=True)
    mod.REPO = root
    mod.INBOX = inbox
    mod.FEEDBACK = root / "workspace" / "feedback"
    mod.OUTBOX = root / "workspace" / "outbox"
    mod.INBOX_EVENTS = inbox / "events.jsonl"
    return mod, root, inbox


def test_record_event_appends_one_row_with_every_required_key():
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "events.jsonl"
        row = mod.record_event(NOTE_A, "retired", ACTOR, session=SESSION, task=TASK, path=out)
        assert out.is_file(), f"no ledger file written at {out}"
        rows = _rows(out)
        assert len(rows) == 1, f"expected exactly 1 row, got {len(rows)}: {rows!r}"
        r = rows[0]
        assert set(r) == set(KEYS), f"row keys {sorted(r)} are not exactly {sorted(KEYS)}"
        assert r["note"] == NOTE_A, f"the row must name the note: {r!r}"
        assert r["action"] == "retired", f"action must be the value passed: {r!r}"
        assert r["actor"] == ACTOR, f"actor must be the value passed: {r!r}"
        assert r["session"] == SESSION, f"session must be the value passed: {r!r}"
        assert r["task"] == TASK, f"task must be the value passed: {r!r}"
        assert isinstance(r["ts"], (int, float)) and not isinstance(r["ts"], bool), f"ts: {r!r}"
        assert isinstance(row, dict) and row.get("note") == NOTE_A, f"returned row: {row!r}"


def test_two_events_keep_their_distinct_notes_and_a_leading_header():
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "events.jsonl"
        out.write_text("# {\"by\": \"code\", \"about\": \"code\"}\n", encoding="utf-8")
        mod.record_event(NOTE_A, "received", ACTOR, path=out)
        mod.record_event(NOTE_B, "read", ACTOR, session=SESSION, task=TASK, path=out)
        rows = _rows(out)
        assert len(rows) == 2, f"expected 2 rows appended, got {len(rows)}: {rows!r}"
        assert [r["note"] for r in rows] == [NOTE_A, NOTE_B], f"order and values: {rows!r}"
        assert len({r["note"] for r in rows}) == 2, f"rows must name distinct notes: {rows!r}"
        first = out.read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("#"), f"a header comment was overwritten: {first!r}"


def test_every_action_in_the_contract_is_accepted():
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        for action in ACTIONS:
            out = Path(td) / f"events-{action}.jsonl"
            mod.record_event(NOTE_A, action, ACTOR, path=out)
            rows = _rows(out)
            assert [r["action"] for r in rows] == [action], f"{action}: {rows!r}"


def test_an_action_outside_the_contract_is_refused_and_writes_nothing():
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        for bad in ("closed", "deferred", "DONE", "", "retire"):
            out = Path(td) / f"bad-{len(bad)}.jsonl"
            try:
                mod.record_event(NOTE_A, bad, ACTOR, path=out)
            except ValueError:
                pass
            else:
                raise AssertionError(f"action {bad!r} is outside {list(ACTIONS)} and must be refused")
            assert not out.exists(), f"an event refused AFTER a write: {bad!r}"


def test_a_blank_note_or_actor_is_refused_and_writes_nothing():
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        for n, a in (("", ACTOR), ("   ", ACTOR), (NOTE_A, ""), (NOTE_A, "  ")):
            out = Path(td) / f"blank-{len(n)}-{len(a)}.jsonl"
            try:
                mod.record_event(n, "retired", a, path=out)
            except ValueError:
                pass
            else:
                raise AssertionError(f"note={n!r} actor={a!r} must be refused")
            assert not out.exists(), f"wrote a row it had just refused: {n!r} {a!r}"


def test_the_ledger_endpoint_is_the_inbox_events_file():
    mod = _mod()
    p = Path(str(mod.INBOX_EVENTS))
    assert p.name == "events.jsonl", f"the endpoint must be events.jsonl, got {p.name!r}"
    assert p.parent.name == "inbox", f"the endpoint must sit in workspace/inbox/, got {p.parent!r}"
    assert str(p).startswith(str(Path(str(mod.REPO)))), f"the endpoint must live under the repo: {p}"


def test_importing_sweep_creates_no_ledger():
    mod = _mod()
    live = Path(str(mod.INBOX_EVENTS))
    assert not live.exists(), f"importing sweep wrote a ledger at {live}: no import-time writes"


def test_cmd_done_records_a_retired_row_when_it_deletes():
    with tempfile.TemporaryDirectory() as td:
        mod, root, inbox = _temp_root(td)
        f = inbox / NOTE_A
        f.write_text("retire me\n", encoding="utf-8")
        mod.is_committed = lambda p: True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mod.cmd_done(NOTE_A, actor=ACTOR, session=SESSION, task=TASK)
        assert rc == 0, f"a committed note must be retired, rc={rc!r}"
        assert not f.exists(), "the note is still in the inbox after --done"
        rows = _rows(Path(str(mod.INBOX_EVENTS)))
        assert len(rows) == 1, f"retiring one note must append exactly 1 row, got {len(rows)}: {rows!r}"
        r = rows[0]
        assert r["action"] == "retired", f"the deletion must be recorded as 'retired', got {r['action']!r}"
        assert r["note"] == NOTE_A, f"the row must name the note deleted: {r!r}"
        assert r["actor"] == ACTOR, f"the mover is Code reporting on itself: {r!r}"
        assert r["session"] == SESSION and r["task"] == TASK, f"provenance must be recorded: {r!r}"
        out = buf.getvalue()
        assert "Deleted" in out and NOTE_A in out, f"the existing confirmation print must survive: {out!r}"


def test_cmd_done_refusing_an_uncommitted_note_records_nothing():
    with tempfile.TemporaryDirectory() as td:
        mod, root, inbox = _temp_root(td)
        f = inbox / NOTE_A
        f.write_text("uncommitted\n", encoding="utf-8")
        mod.is_committed = lambda p: False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mod.cmd_done(NOTE_A, actor=ACTOR, session=SESSION, task=TASK)
        assert rc != 0, f"an uncommitted note must be refused, rc={rc!r}"
        assert f.exists(), "a refused note must not be deleted"
        assert not Path(str(mod.INBOX_EVENTS)).exists(), "a refused retirement must not write a ledger row"


def test_cmd_done_refusing_a_non_note_records_nothing():
    with tempfile.TemporaryDirectory() as td:
        mod, root, inbox = _temp_root(td)
        f = inbox / "README.md"
        f.write_text("inbox README\n", encoding="utf-8")
        mod.is_committed = lambda p: True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mod.cmd_done("README.md", actor=ACTOR, session=SESSION, task=TASK)
        assert rc != 0, f"the README must not be retired, rc={rc!r}"
        assert f.exists(), "the inbox README must not be deleted"
        assert not Path(str(mod.INBOX_EVENTS)).exists(), "a refused retirement must not write a ledger row"


def test_a_repo_relative_path_resolves_like_the_bare_name():
    with tempfile.TemporaryDirectory() as td:
        mod, root, inbox = _temp_root(td)
        f = inbox / NOTE_A
        f.write_text("retire me\n", encoding="utf-8")
        mod.is_committed = lambda p: True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mod.cmd_done(str(Path("workspace") / "inbox" / NOTE_A),
                              actor=ACTOR, session=SESSION, task=TASK)
        assert rc == 0, f"a repo-relative path must resolve, rc={rc!r}"
        assert not f.exists(), "the note addressed by path is still in the inbox"
        rows = _rows(Path(str(mod.INBOX_EVENTS)))
        assert len(rows) == 1, f"expected exactly 1 ledger row, got {len(rows)}: {rows!r}"
        assert rows[0]["note"] == NOTE_A, f"the row must name the note file, not a path: {rows[0]!r}"


def _main():
    tests = sorted((n, f) for n, f in list(globals().items())
                   if n.startswith("test_") and callable(f))
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}", file=sys.stderr)
        else:
            print(f"pass {name}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
