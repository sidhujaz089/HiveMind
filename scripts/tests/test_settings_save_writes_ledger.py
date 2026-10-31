#!/usr/bin/env python3
"""t738: every knob move through settings.save() records actor, reason and task.

RED ON MAIN for the missing change and nothing else. projects/local-inference/settings.py
defines no LEDGER, no record_change and no ledger_rows, and save() (settings.py:315-319)
writes workspace/console/settings.json and nothing else -- so a COMMITTED file records no
actor, no reason and no task for any knob moved through it. Its git history is the evidence:
chat.temperature to 1.0 and chat.tool_rounds 8 -> 24 -> 100 landed in nightly "close out
session" commits (575a4289, e5dfb180, ba0216bf) with no attribution, so 0.6's provenance is
unrecoverable and 100's is unrecorded (workspace/rca/e9a2e94ec33f.md, Rule 4 case 2: no
collector exists at this endpoint).

THE CONTRACT THIS SUITE PINS, all in projects/local-inference/settings.py:

    LEDGER        pathlib.Path  workspace/console/settings-changes.jsonl, read at CALL time
                                so a test may point it at a temp file
    record_change(*, section, key, old, new, actor, reason, task, source="") -> dict
                                validate, fill ts, append ONE JSON line, return the row
    ledger_rows()               (rows, torn) -- a torn line is REPORTED, never fatal

A row naming no section, key, actor, reason or task raises ValueError and records nothing.
save() records one row per CHANGED knob, no row when nothing changed, and keeps its existing
one-positional-argument contract (console.py:1889 calls cfg.save(cur) with one argument).

The two values asserted below are the repository's own and they are DISTINCT: 24 is line 49,
the documented chat tool_rounds; 100 is line 166's LIMITS max and the value in the live
settings.json. A recorder that stamps one placeholder row for every call cannot pass.

Run: python3 scripts/tests/test_settings_save_writes_ledger.py   (pytest is not installed)
No live state: every write lands under tempfile.mkdtemp(). No subprocess, no urlopen, no
model call, no fixed /tmp path, nothing left running.
"""
import copy
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))

import settings as cfg  # noqa: E402  -- the path settings.py really lives on

FAILURES = []
FIELDS = ("ts", "actor", "reason", "task", "section", "key", "old", "new")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
DOCUMENTED = 24
CEILING = 100


def check(name, ok, detail=""):
    if not ok:
        FAILURES.append("%s -- %s" % (name, detail))


def baseline():
    """The repository's own documented defaults, computed -- not a hand-written copy."""
    return copy.deepcopy(cfg.DEFAULTS)


def sandbox():
    """Point FILE and LEDGER at a temp dir. Returns a restore callable."""
    tmp = Path(tempfile.mkdtemp(prefix="t738-"))
    saved_file, saved_ledger = cfg.FILE, cfg.LEDGER
    cfg.FILE = tmp / "settings.json"
    cfg.LEDGER = tmp / "settings-changes.jsonl"

    def restore():
        cfg.FILE, cfg.LEDGER = saved_file, saved_ledger
        shutil.rmtree(tmp, ignore_errors=True)
    return restore


def rows_of(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def numeric_knobs():
    out = []
    for section in sorted(cfg.DEFAULTS):
        block = cfg.DEFAULTS[section]
        if not isinstance(block, dict):
            continue
        for key in sorted(block):
            value = block[key]
            if isinstance(value, int) and not isinstance(value, bool):
                out.append((section, key, value))
    return out


def test_the_recorder_names_what_it_records():
    for name in ("LEDGER", "record_change", "ledger_rows"):
        check("settings.py defines %s" % name, hasattr(cfg, name),
              "the endpoint that writes workspace/console/settings.json records no actor, "
              "no reason and no task for any knob move (workspace/rca/e9a2e94ec33f.md)")
    check("the documented tool_rounds and the validator ceiling are DISTINCT real values",
          cfg.DEFAULTS["chat"]["tool_rounds"] == DOCUMENTED
          and cfg.LIMITS["tool_rounds"]["max"] == CEILING,
          "a fixture built on one repeated placeholder cannot tell the two ends of this knob "
          "apart: documented=%r LIMITS max=%r"
          % (cfg.DEFAULTS["chat"].get("tool_rounds"),
             (cfg.LIMITS.get("tool_rounds") or {}).get("max")))


def test_a_row_naming_no_reason_is_refused_and_records_nothing():
    restore = sandbox()
    try:
        for blank in ("", "   ", None):
            try:
                cfg.record_change(section="chat", key="tool_rounds",
                                  old=DOCUMENTED, new=CEILING,
                                  actor="manager", reason=blank, task="t738")
                check("a blank reason (%r) is refused" % (blank,), False,
                      "no exception raised: a row with no reason is the silent knob move "
                      "this ledger exists to make impossible")
            except ValueError:
                check("a blank reason (%r) is refused" % (blank,), True)
        left = rows_of(cfg.LEDGER) if cfg.LEDGER.exists() else []
        check("a refused row leaves no trace on the ledger", left == [], "rows=%s" % (left,))
    finally:
        restore()


def test_a_row_naming_no_owner_is_refused_and_records_nothing():
    restore = sandbox()
    try:
        for field in ("actor", "task"):
            try:
                cfg.record_change(section="chat", key="tool_rounds", old=DOCUMENTED,
                                  new=CEILING, actor="manager", reason="reason text",
                                  task="t738", **{field: "   "})
                check("a blank %s is refused" % field, False, "no exception raised")
            except ValueError:
                check("a blank %s is refused" % field, True)
        left = rows_of(cfg.LEDGER) if cfg.LEDGER.exists() else []
        check("a refused row leaves no trace on the ledger", left == [], "rows=%s" % (left,))
    finally:
        restore()


def test_a_row_naming_no_knob_is_refused_and_records_nothing():
    restore = sandbox()
    try:
        for field in ("section", "key"):
            try:
                cfg.record_change(section="chat", key="tool_rounds", old=DOCUMENTED,
                                  new=CEILING, actor="manager", reason="reason text",
                                  task="t738", **{field: ""})
                check("a blank %s is refused" % field, False, "no exception raised")
            except ValueError:
                check("a blank %s is refused" % field, True)
        left = rows_of(cfg.LEDGER) if cfg.LEDGER.exists() else []
        check("a refused row leaves no trace on the ledger", left == [], "rows=%s" % (left,))
    finally:
        restore()


def test_a_save_that_changes_nothing_writes_no_row():
    restore = sandbox()
    try:
        cfg.save(baseline())
        good, torn = cfg.ledger_rows()
        check("a save that changes nothing writes no row", good == [], "rows=%s" % (good,))
        check("and reports nothing torn", torn == [], repr(torn))
    finally:
        restore()


def test_a_knob_move_through_save_is_recorded_with_actor_reason_and_task():
    restore = sandbox()
    try:
        cfg.save(baseline())
        data = baseline()
        data["chat"]["tool_rounds"] = CEILING
        cfg.save(data, actor="manager",
                 reason="Jas raised the cap: a cap of 8 ended a normal investigation",
                 task="t738")
        good, torn = cfg.ledger_rows()
        check("a clean ledger reports nothing torn", torn == [], repr(torn))
        moves = [r for r in good if r.get("key") == "tool_rounds"]
        check("exactly one tool_rounds row is written", len(moves) == 1, "rows=%s" % (good,))
        if len(moves) == 1:
            row = moves[0]
            check("the row names the section it came from", row.get("section") == "chat", repr(row))
            check("the row names the documented old value",
                  row.get("old") == DOCUMENTED, repr(row))
            check("the row names the value it moved to",
                  row.get("new") == CEILING, repr(row))
            check("the row names the actor", row.get("actor") == "manager", repr(row))
            check("the row names the task", row.get("task") == "t738", repr(row))
            check("the row carries a reason a person wrote",
                  str(row.get("reason", "")).strip() not in ("", "unrecorded"), repr(row))
            check("the row stamps a UTC timestamp",
                  bool(STAMP.match(str(row.get("ts", "")))), repr(row.get("ts")))
        check("every row carries every required field",
              all(set(FIELDS) <= set(r) for r in good), "missing fields in %s" % (good,))
    finally:
        restore()


def test_two_knobs_in_one_save_are_two_rows_with_their_own_values():
    knobs = numeric_knobs()
    check("DEFAULTS carries at least two numeric knobs to move", len(knobs) >= 2, repr(knobs))
    if len(knobs) < 2:
        return
    (s1, k1, v1), (s2, k2, v2) = knobs[0], knobs[1]
    restore = sandbox()
    try:
        cfg.save(baseline())
        data = baseline()
        data[s1][k1] = v1 + 1
        data[s2][k2] = v2 + 100
        cfg.save(data, actor="manager", reason="raising two knobs at once", task="t738")
        good, _torn = cfg.ledger_rows()
        by = {(r.get("section"), r.get("key")): r for r in good}
        for sec, key, old, bump in ((s1, k1, v1, 1), (s2, k2, v2, 100)):
            row = by.get((sec, key))
            check("a row exists for %s.%s" % (sec, key), row is not None, "rows=%s" % (good,))
            if row is not None:
                check("%s.%s records the documented old value" % (sec, key),
                      row.get("old") == old, repr(row))
                check("%s.%s records the value that was set" % (sec, key),
                      row.get("new") == old + bump, repr(row))
        check("one save that moves two knobs writes two rows", len(good) == 2, "rows=%s" % (good,))
    finally:
        restore()


def test_a_one_argument_save_still_records_the_move_as_ownerless():
    # console.py:1889 calls cfg.save(cur) with ONE positional argument. save() must not start
    # raising for it, and the move must still be recorded -- as ownerless, a visible gap
    # rather than a swallowed one.
    restore = sandbox()
    try:
        cfg.save(baseline())
        data = baseline()
        data["chat"]["tool_rounds"] = CEILING
        try:
            cfg.save(data)
        except TypeError as exc:
            check("save(data) keeps its one-argument contract", False, repr(exc))
            return
        check("save(data) keeps its one-argument contract", True)
        good, _ = cfg.ledger_rows()
        moves = [r for r in good if r.get("key") == "tool_rounds"]
        check("a legacy save still records the move", len(moves) == 1, "rows=%s" % (good,))
        if moves:
            check("an unattributed save records an empty task", moves[0].get("task") == "", repr(moves[0]))
            check("an unattributed save records reason 'unrecorded'",
                  moves[0].get("reason") == "unrecorded", repr(moves[0]))
    finally:
        restore()


def test_the_settings_file_still_ends_up_valid_with_the_new_value():
    restore = sandbox()
    try:
        cfg.save(baseline())
        data = baseline()
        data["chat"]["tool_rounds"] = CEILING
        cfg.save(data, actor="manager", reason="raise the cap", task="t738")
        saved = json.loads(cfg.FILE.read_text(encoding="utf-8"))
        check("settings.json is written and parses as one object", isinstance(saved, dict), str(cfg.FILE))
        check("the moved knob is the value that was set",
              saved.get("chat", {}).get("tool_rounds") == CEILING, repr(saved.get("chat")))
        check("the section's untouched keys survive unchanged",
              {k: v for k, v in saved.get("chat", {}).items() if k != "tool_rounds"}
              == {k: v for k, v in data["chat"].items() if k != "tool_rounds"},
              repr(saved.get("chat")))
        check("the file's other sections are untouched",
              {k: v for k, v in saved.items() if k != "chat"}
              == {k: v for k, v in data.items() if k != "chat"}, sorted(saved.keys()))
    finally:
        restore()


def test_a_torn_final_line_is_reported_and_the_good_rows_survive():
    # The recorder's reason for existing: a process cut off mid-write must not make the ledger
    # unreadable. test_runledger.py passed 39/0 while reconcile() raised JSONDecodeError on
    # exactly this input, so a recorder that dies on its own torn line is a known defect.
    restore = sandbox()
    try:
        cfg.record_change(section="chat", key="tool_rounds", old=DOCUMENTED, new=CEILING,
                          actor="manager", reason="first row", task="t738")
        with cfg.LEDGER.open("a", encoding="utf-8") as fh:
            fh.write('{"actor": "manager", "task": "t738", "key": "to')
        good, torn = cfg.ledger_rows()
        check("the torn line is reported, not hidden", len(torn) == 1, repr(torn))
        check("the torn line is named in the report", bool(torn) and "t738" in str(torn[0]), repr(torn))
        check("the complete row survives", len(good) == 1, repr(good))
        check("and keeps the values it was given",
              bool(good) and good[0].get("new") == CEILING, repr(good))
    finally:
        restore()


def test_the_default_ledger_is_the_file_the_finding_named():
    parts = list(Path(str(cfg.LEDGER)).parts[-2:])
    check("the default ledger is workspace/console/settings-changes.jsonl",
          parts == ["console", "settings-changes.jsonl"], str(cfg.LEDGER))


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:  # noqa: BLE001  a raising test is a recorded FAIL
                FAILURES.append("%s raised %r" % (_name, exc))
    print("%d failure(s)" % len(FAILURES))
    for _f in FAILURES:
        print("  FAIL", _f)
    sys.exit(1 if FAILURES else 0)
