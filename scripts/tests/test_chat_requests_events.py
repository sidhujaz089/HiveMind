#!/usr/bin/env python3
"""t1298 red fixture: retiring an inbox note appends a row to the inbox event ledger.

RED ON MAIN: scripts/chat_requests.py has no record_event, no ACTIONS, no INBOX_EVENTS, and
handle() takes no session/task. Every test touches one of those first, so on main each one
raises AttributeError -- the missing change, not a bug in the test.

No live state: every test runs inside a temp dir with fakes. pytest is not installed.
"""
import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "scripts" / "chat_requests.py"
NOTE_A = "2026-09-16T195052Z--chat--unstick-the-hivemind.md"
NOTE_B = "2026-09-16T201840Z--chat--unstick-the-hivemind.md"
ACTIONS = ("received", "read", "answered", "retired")
KEYS = ("ts", "note", "action", "actor", "session", "task")
SESSION = "073b80dd616b"
ACTOR = "code"
TASK = "t1298"


def _mod():
    spec = importlib.util.spec_from_file_location("chat_requests_under_test", MODULE)
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
    """A throwaway repo: inbox/, handled/, outbox/ -- nothing under the real workspace/."""
    mod = _mod()
    root = Path(td).resolve()
    inbox = root / "workspace" / "inbox"
    (inbox / "handled").mkdir(parents=True)
    mod.REPO = root
    mod.INBOX = inbox
    mod.HANDLED = inbox / "handled"
    mod.OUTBOX = root / "workspace" / "outbox"
    mod.LEDGER = root / "chat-requests.jsonl"
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


def test_a_long_note_is_recorded_whole():
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "events.jsonl"
        note = NOTE_A.replace(".md", "-" + "x" * 300 + ".md")
        mod.record_event(note, "retired", ACTOR, path=out)
        rows = _rows(out)
        assert len(rows) == 1, f"{rows!r}"
        assert rows[0]["note"] == note, "the note value was truncated"
        assert note in out.read_text(encoding="utf-8"), "the full note name is not in the file"


def test_the_ledger_endpoint_is_the_inbox_events_file():
    mod = _mod()
    p = Path(str(mod.INBOX_EVENTS))
    assert p.name == "events.jsonl", f"the endpoint must be events.jsonl, got {p.name!r}"
    assert p.parent.name == "inbox", f"the endpoint must sit in workspace/inbox/, got {p.parent!r}"
    assert str(p).startswith(str(Path(str(mod.REPO)))), f"the endpoint must live under the repo: {p}"


def test_importing_the_mover_creates_no_ledger():
    mod = _mod()
    live = Path(str(mod.INBOX_EVENTS))
    assert not live.exists(), f"importing the module wrote a ledger at {live}: no import-time writes"


def test_an_inbox_with_nothing_to_do_writes_no_ledger():
    with tempfile.TemporaryDirectory() as td:
        mod, root, inbox = _temp_root(td)
        waiting = mod.requests_waiting()
        assert list(waiting) == [], f"an empty inbox must have nothing waiting: {waiting!r}"
        assert not Path(str(mod.INBOX_EVENTS)).exists(), "an empty inbox must not create a ledger"


def test_retiring_a_note_appends_a_retired_row():
    with tempfile.TemporaryDirectory() as td:
        mod, root, inbox = _temp_root(td)
        f = inbox / NOTE_A
        f.write_text("please unstick the hivemind\n", encoding="utf-8")
        prev = sys.modules.get("unstick")
        stub = types.ModuleType("unstick")
        stub.quick_faults = lambda: []
        sys.modules["unstick"] = stub
        calls = []

        def fake_run(cmd, *a, **kw):
            calls.append([str(c) for c in cmd])
            return types.SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"verdict": "ok", "report": "r",
                                   "did": [], "blocked": [], "started": []}),
                stderr="")

        mod.subprocess.run = fake_run
        try:
            mod.handle(f, force=True, session=SESSION, task=TASK)
        finally:
            if prev is None:
                sys.modules.pop("unstick", None)
            else:
                sys.modules["unstick"] = prev
        assert (mod.HANDLED / NOTE_A).is_file(), "the note was not moved into handled/"
        assert not f.exists(), "the note is still in the inbox after being retired"
        assert calls, "retiring a note must still run the unstick action"
        assert calls[0][1].endswith("unstick.py"), f"the action must be scripts/unstick.py: {calls[0]!r}"
        rows = _rows(Path(str(mod.INBOX_EVENTS)))
        assert len(rows) == 1, f"retiring one note must append exactly 1 row, got {len(rows)}: {rows!r}"
        r = rows[0]
        assert r["action"] == "retired", f"the move must be recorded as 'retired', got {r['action']!r}"
        assert r["note"] == NOTE_A, f"the row must name the note retired: {r!r}"
        assert r["actor"] == ACTOR, f"the mover is Code reporting on itself: {r!r}"
        assert r["session"] == SESSION and r["task"] == TASK, f"provenance must be recorded: {r!r}"


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
