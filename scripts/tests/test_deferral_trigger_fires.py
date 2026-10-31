#!/usr/bin/env python3
"""t1220 -- a deferral bound to a machine-observable trigger is resurfaced, as one
row in the promise ledger naming the deferral key, when that event is observed.

Red on main for ONE reason: scripts/session_start.py has no `resurface_deferrals`.
Every case fails at that one entry point, with that one message -- not for this file's
own import path, signature or fixture.

Every fixture lives in a temp dir. Nothing here opens, writes, copies or appends to
workspace/session-reviews/promise-ledger.jsonl, the real deferral ledger, or any other
live state. No model call, no network, no subprocess, no process left running.
"""
import contextlib
import importlib.util
import json
import sys
import tempfile
from io import StringIO
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]

FAILURES = []


def check(label, ok, detail=""):
    if ok:
        print("  PASS " + label)
    else:
        print("  FAIL " + label + ((" -- " + detail) if detail else ""))
        FAILURES.append(label)
    return bool(ok)


def load_session_start():
    """Import scripts/session_start.py from the path it really lives on.

    stdout/stderr are swallowed: this is a reporting script and its printing is not the
    subject of the test. Import failure is reported as a harness finding, not as a
    behaviour assertion, so a red run says WHY it is red.
    """
    src = SCRIPTS / "session_start.py"
    spec = importlib.util.spec_from_file_location("session_start_under_test", src)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot build an import spec for " + str(src))
    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
        spec.loader.exec_module(module)
    return module


MODULE = load_session_start()


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return path


def read_jsonl(path):
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


NOW = "2026-10-19T12:00:00Z"
OBSERVER = "7f3c1a90-4b6e-4d12-9a55-0d21c9e48b71"
TRIGGER_12B = "interviews/preflight-gemma4_12b.json arrives"
TRIGGER_7B = "interviews/preflight-gemma4_7b.json arrives"
LONG_TAIL = (" -- promised on 2026-09-11T04:32Z, due 2026-09-14, measured against the "
             "5xx correlation ledger, discharged only when the row exists")
TRIGGER_LONG = TRIGGER_12B + LONG_TAIL


def has_hook():
    """The single stated reason this file is red on main."""
    return callable(getattr(MODULE, "resurface_deferrals", None))


def test_hook_exists():
    return check("session_start exposes resurface_deferrals",
                  has_hook(),
                  "scripts/session_start.resurface_deferrals does not exist -- "
                  "nothing today binds a deferral to a landed event")


def test_matching_trigger_emits_one_row_naming_the_key():
    if not has_hook():
        return check("match: one row naming the deferral key", False,
                     "skipped for the same reason: no resurface_deferrals")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = write_jsonl(root / "deferrals.jsonl", [
            {"key": "e06b0ede495d", "trigger": TRIGGER_12B,
             "quote": "I'll correlate timestamps properly"},
        ])
        e = write_jsonl(root / "events.jsonl", [
            {"target": "interviews/preflight-gemma4_12b.json", "session": OBSERVER},
        ])
        ledger = root / "ledger.jsonl"
        rows = MODULE.resurface_deferrals(d, e, ledger, now=NOW)
        check("returns a list", isinstance(rows, list), repr(type(rows)))
        check("exactly one row emitted", len(rows) == 1, repr(rows))
        check("the ledger gained exactly that row", read_jsonl(ledger) == rows,
              "the returned value is not what was written to the ledger")
        if rows:
            row = rows[0]
            check("the row names the deferral key, exactly", row.get("deferral") == "e06b0ede495d",
                  repr(row.get("deferral")))
            check("the row records the WHOLE trigger string, not a slice",
                  row.get("trigger") == TRIGGER_12B, repr(row.get("trigger")))
            check("the row names the event that fired it",
                  row.get("event") == "interviews/preflight-gemma4_12b.json",
                  repr(row.get("event")))
            check("the row is attributed to the OBSERVING session, not the minting one",
                  row.get("session") == OBSERVER, repr(row.get("session")))
            check("the observing session id is not the synthetic fixture id",
                  row.get("session") != "s1", repr(row.get("session")))
            check("the row is stamped at the supplied time", row.get("at") == NOW,
                  repr(row.get("at")))
            check("the row declares its kind", row.get("kind") == "deferral-triggered",
                  repr(row.get("kind")))
            check("the row is JSON-serialisable", isinstance(json.dumps(row), str), "")
        return len(FAILURES) == 0


def test_deferral_key_is_not_a_placeholder():
    """Two deferrals, only one triggered: the row must carry the RIGHT key."""
    if not has_hook():
        return check("two deferrals are distinguishable", False,
                     "skipped for the same reason: no resurface_deferrals")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = write_jsonl(root / "deferrals.jsonl", [
            {"key": "e06b0ede495d", "trigger": TRIGGER_12B},
            {"key": "a5d9ea5606c5", "trigger": TRIGGER_7B},
        ])
        e = write_jsonl(root / "events.jsonl", [
            {"target": "interviews/preflight-gemma4_12b.json", "session": OBSERVER},
        ])
        rows = MODULE.resurface_deferrals(d, e, root / "ledger.jsonl", now=NOW)
        keys = [r.get("deferral") for r in rows]
        check("only the deferral whose trigger matches is resurfaced", keys == ["e06b0ede495d"],
              repr(keys))
        return keys == ["e06b0ede495d"]


def test_non_matching_trigger_emits_no_row():
    """The negative case. Without it a hook that resurfaces everything passes vacuously."""
    if not has_hook():
        return check("non-matching trigger emits no row", False,
                     "skipped for the same reason: no resurface_deferrals")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = write_jsonl(root / "deferrals.jsonl", [
            {"key": "e06b0ede495d", "trigger": TRIGGER_12B},
        ])
        e = write_jsonl(root / "events.jsonl", [
            {"target": "interviews/preflight-gemma4_7b.json", "session": OBSERVER},
        ])
        ledger = root / "ledger.jsonl"
        rows = MODULE.resurface_deferrals(d, e, ledger, now=NOW)
        check("a deferral whose trigger does not match emits no row", rows == [], repr(rows))
        check("and no ledger file row is written for it", read_jsonl(ledger) == [],
              repr(read_jsonl(ledger)))
        return rows == [] and read_jsonl(ledger) == []


def test_deferral_without_a_trigger_emits_no_row():
    if not has_hook():
        return check("an untriggered deferral emits no row", False,
                     "skipped for the same reason: no resurface_deferrals")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = write_jsonl(root / "deferrals.jsonl", [
            {"key": "no-trigger-key", "trigger": ""},
            {"key": "missing-trigger-key"},
        ])
        e = write_jsonl(root / "events.jsonl", [
            {"target": "interviews/preflight-gemma4_12b.json", "session": OBSERVER},
        ])
        ledger = root / "ledger.jsonl"
        rows = MODULE.resurface_deferrals(d, e, ledger, now=NOW)
        check("an untriggered deferral is never resurfaced by a landing event", rows == [],
              repr(rows))
        return rows == []


def test_no_events_writes_nothing():
    if not has_hook():
        return check("no events writes nothing", False,
                     "skipped for the same reason: no resurface_deferrals")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = write_jsonl(root / "deferrals.jsonl", [{"key": "k1", "trigger": TRIGGER_12B}])
        e = write_jsonl(root / "events.jsonl", [])
        ledger = root / "ledger.jsonl"
        rows = MODULE.resurface_deferrals(d, e, ledger, now=NOW)
        check("an empty event stream emits no row", rows == [], repr(rows))
        check("an empty event stream creates no ledger rows", read_jsonl(ledger) == [],
              repr(read_jsonl(ledger)))
        return rows == []


def test_long_trigger_string_is_recorded_whole():
    """A ledger row is a record: the trigger text is never cut."""
    if not has_hook():
        return check("long trigger recorded whole", False,
                     "skipped for the same reason: no resurface_deferrals")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = write_jsonl(root / "deferrals.jsonl", [{"key": "e06b0ede495d",
                                                   "trigger": TRIGGER_LONG}])
        e = write_jsonl(root / "events.jsonl", [
            {"target": "interviews/preflight-gemma4_12b.json", "session": OBSERVER},
        ])
        ledger = root / "ledger.jsonl"
        MODULE.resurface_deferrals(d, e, ledger, now=NOW)
        rows = read_jsonl(ledger)
        check("the full trigger survives in the written row",
              bool(rows) and rows[0].get("trigger") == TRIGGER_LONG,
              repr(rows[0].get("trigger")) if rows else "no row written")
        check("the recorded trigger is longer than any slice would leave",
              bool(rows) and len(rows[0].get("trigger", "")) == len(TRIGGER_LONG),
              repr(len(rows[0].get("trigger", ""))) if rows else "no row written")
        return bool(rows) and rows[0].get("trigger") == TRIGGER_LONG


def test_append_preserves_existing_ledger_rows():
    if not has_hook():
        return check("append preserves existing rows", False,
                     "skipped for the same reason: no resurface_deferrals")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ledger = root / "ledger.jsonl"
        keep = {"session": "s1", "turn": 3, "quote": "keep me",
                "verdict": "undischarged", "at": "2026-10-12T08:59:29Z"}
        write_jsonl(ledger, [keep])
        d = write_jsonl(root / "deferrals.jsonl", [{"key": "k1", "trigger": TRIGGER_12B}])
        e = write_jsonl(root / "events.jsonl", [
            {"target": "interviews/preflight-gemma4_12b.json", "session": OBSERVER},
        ])
        MODULE.resurface_deferrals(d, e, ledger, now=NOW)
        rows = read_jsonl(ledger)
        check("the pre-existing ledger row survives", rows[0] == keep, repr(rows[0]))
        check("the resurface row is appended after it", len(rows) == 2, repr(len(rows)))
        check("every ledger line is valid JSON and carries the deferral key",
              len(rows) == 2 and rows[1].get("deferral") == "k1", repr(rows[1:]))
        return len(rows) == 2 and rows[0] == keep


def test_running_twice_emits_no_duplicates():
    """The surface this hooks into runs EVERY session. A second sighting is not a second debt."""
    if not has_hook():
        return check("a repeat observation is not a duplicate row", False,
                     "skipped for the same reason: no resurface_deferrals")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = write_jsonl(root / "deferrals.jsonl", [{"key": "k1", "trigger": TRIGGER_12B}])
        e = write_jsonl(root / "events.jsonl", [
            {"target": "interviews/preflight-gemma4_12b.json", "session": OBSERVER},
        ])
        ledger = root / "ledger.jsonl"
        first = MODULE.resurface_deferrals(d, e, ledger, now=NOW)
        second = MODULE.resurface_deferrals(d, e, ledger, now="2026-10-20T12:00:00Z")
        check("first run emits the row", len(first) == 1, repr(first))
        check("second run of the same event emits nothing", second == [], repr(second))
        check("the ledger holds exactly one row for the key", len(read_jsonl(ledger)) == 1,
              repr(len(read_jsonl(ledger))))
        return second == [] and len(read_jsonl(ledger)) == 1


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


if __name__ == "__main__":
    print(f"test_deferral_trigger_fires: {len(TESTS)} cases")
    for _fn in TESTS:
        _fn()
    if FAILURES:
        print(f"FAIL -- {len(FAILURES)} assertion(s) failed:")
        for _f in FAILURES:
            print("  - " + _f)
        sys.exit(1)
    print("OK -- deferral triggers fire and resurface")
    sys.exit(0)
