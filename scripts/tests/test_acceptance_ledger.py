#!/usr/bin/env python3
"""Oracle for t1065 -- a capability-acceptance row recorded AT THE SHIP ENDPOINT.

Why this is the contract (workspace/rca/8972f552b6d7.md:36,81-85): a capability was
shipped to the Frontline at 2026-10-02 06:34:55Z against a condition the agent stated at
06:25 ("ship to Frontline only if Gemma catches the embedder item") and measured at
07:38:05Z as NOT met (transcript line 2353, v2 and v3 both missed). No endpoint could
hold condition + measurement, so the gate ran after the ship and nothing recorded the
violation. scripts/role_store.py is that endpoint: update_role() (line 356) is what
routes a task to a model. Today it writes ROLES.json, SERVING.json and its changelog, and
says nothing about the condition the ship was taken on.

Red on main: role_store has no LEDGER, no record_acceptance, no not_met_rows, and
update_role accepts no 'acceptance' / 'ledger' keyword.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import role_store  # noqa: E402

FIELDS = {"lane", "task", "session_set", "condition", "measured", "verdict", "actor", "at"}
SHIP = {
    "lane": "frontline",
    "task": "t623",
    "session_set": "a15c2172-0900-44fc-b8eb-71e98a90aa6f",
    "condition": "ship to Frontline only if Gemma catches the embedder item",
    "measured": "rerun of the 3 Q4 baseline sessions, v2 and v3: embedder item found 0 of 3",
    "verdict": "not-met",
}


def test_ledger_path_is_the_documented_file():
    assert str(role_store.LEDGER).replace("\\", "/").endswith("workspace/acceptance-ledger.jsonl")


def test_record_acceptance_appends_one_complete_row():
    ledger = Path(tempfile.mkdtemp()) / "acceptance-ledger.jsonl"
    row = role_store.record_acceptance(**SHIP, actor="claude", at="2026-10-02T07:38:05Z",
                                       path=ledger)
    raw = ledger.read_text(encoding="utf-8")
    assert raw.endswith("\n"), "a ledger line that is not newline-terminated corrupts the next append"
    assert len(raw.splitlines()) == 1
    assert json.loads(raw.splitlines()[0]) == {**SHIP, "actor": "claude", "at": "2026-10-02T07:38:05Z"}
    assert set(row) == FIELDS, f"the row must carry exactly {sorted(FIELDS)}, got {sorted(row)}"


def test_record_acceptance_appends_a_second_row():
    ledger = Path(tempfile.mkdtemp()) / "acceptance-ledger.jsonl"
    role_store.record_acceptance(**{**SHIP, "verdict": "pending", "measured": ""},
                                 actor="jas", at="2026-10-02T06:34:55Z", path=ledger)
    role_store.record_acceptance(**SHIP, actor="claude", at="2026-10-02T07:38:05Z", path=ledger)
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {**SHIP, "verdict": "pending", "measured": "", "actor": "jas", "at": "2026-10-02T06:34:55Z"},
        {**SHIP, "actor": "claude", "at": "2026-10-02T07:38:05Z"},
    ], "the ship row and the measurement row are two rows that cannot cancel each other"


def test_verdict_vocabulary_is_an_allowlist():
    ledger = Path(tempfile.mkdtemp()) / "acceptance-ledger.jsonl"
    for ok in ("pending", "met", "not-met"):
        role_store.record_acceptance(**{**SHIP, "verdict": ok, "measured": ""},
                                     actor="claude", at="2026-10-02T06:34:55Z", path=ledger)
    for bad in ("ok", "close-enough", "met-ish", "", "MET"):
        try:
            role_store.record_acceptance(**{**SHIP, "verdict": bad}, actor="claude",
                                        at="2026-10-02T06:34:55Z", path=ledger)
        except ValueError as e:
            assert "close-enough" not in str(e)  # the message names the verdict it was given
            continue
        raise AssertionError(f"verdict {bad!r} was accepted; VERDICTS is an allowlist")


def test_an_empty_condition_is_refused():
    ledger = Path(tempfile.mkdtemp()) / "acceptance-ledger.jsonl"
    try:
        role_store.record_acceptance(**{**SHIP, "condition": "  "}, actor="claude",
                                    at="2026-10-02T06:34:55Z", path=ledger)
    except ValueError:
        pass
    else:
        raise AssertionError("a ship recorded with NO condition is the defect this ledger exists for")


def test_not_met_rows_returns_the_rows_and_only_them():
    ledger = Path(tempfile.mkdtemp()) / "acceptance-ledger.jsonl"
    role_store.record_acceptance(**{**SHIP, "verdict": "pending", "measured": ""},
                                 actor="jas", at="2026-10-02T06:34:55Z", path=ledger)
    role_store.record_acceptance(**SHIP, actor="claude", at="2026-10-02T07:38:05Z", path=ledger)
    role_store.record_acceptance(**{**SHIP, "lane": "senior", "verdict": "met"},
                                 actor="claude", at="2026-10-03T06:00:00Z", path=ledger)
    assert role_store.not_met_rows(path=ledger) == [{**SHIP, "actor": "claude", "at": "2026-10-02T07:38:05Z"}]


def test_a_missing_ledger_is_no_rows():
    assert role_store.not_met_rows(path=Path(tempfile.mkdtemp()) / "absent.jsonl") == []


def test_a_torn_line_is_announced_not_silently_dropped():
    import contextlib
    import io
    d = Path(tempfile.mkdtemp())
    ledger = d / "acceptance-ledger.jsonl"
    role_store.record_acceptance(**SHIP, actor="claude", at="2026-10-02T07:38:05Z", path=ledger)
    ledger.write_text(ledger.read_text(encoding="utf-8") + '{"lane": "frontline", "task"\n',
                      encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rows = role_store.not_met_rows(path=ledger)
    assert rows == [{**SHIP, "actor": "claude", "at": "2026-10-02T07:38:05Z"}], \
        "a corrupt line must not hide the good rows"
    err = buf.getvalue()
    assert "ledger" in err.lower() and "2" in err, \
        f"the unreadable line must be printed with its file and line number, got {err!r}"


def _temp_store(d):
    store = d / "ROLES.json"
    store.write_text(json.dumps({"roles": {"frontline": {
        "name": "Frontline", "kind": "model", "model": "gemma4:26b", "what": "", "tasks": []}}}),
        encoding="utf-8")
    serving = d / "SERVING.json"
    serving.write_text(json.dumps({"roles": {}}), encoding="utf-8")
    return store, serving


def test_the_ship_endpoint_records_the_condition():
    d = Path(tempfile.mkdtemp())
    store, serving = _temp_store(d)
    ledger = d / "acceptance-ledger.jsonl"
    role_store.update_role("frontline", "jas", "session review to the Frontline",
                           tasks=[{"task": "session_review"}],
                           acceptance={**SHIP, "verdict": "pending", "measured": ""},
                           path=store, serving=serving, ledger=ledger)
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["lane"] == "frontline" and row["task"] == "t623"
    assert row["condition"] == SHIP["condition"]
    assert row["verdict"] == "pending"
    assert row["actor"] == "jas", "actor defaults to the who argument"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", str(row["at"])), row["at"]
    saved = json.loads(store.read_text(encoding="utf-8"))
    assert saved["roles"]["frontline"]["tasks"] == [{"task": "session_review"}], \
        "the ship itself must still be applied"
    assert "acceptance" not in saved["roles"]["frontline"], \
        "the ledger is the record; ROLES.json keeps its schema"
    assert json.loads(serving.read_text(encoding="utf-8"))["roles"]["frontline"]["tag"] == "gemma4:26b"


def test_a_ship_with_no_acceptance_writes_no_row():
    d = Path(tempfile.mkdtemp())
    store, serving = _temp_store(d)
    ledger = d / "acceptance-ledger.jsonl"
    role_store.update_role("frontline", "jas", "reorder my tasks", how="top",
                           path=store, serving=serving, ledger=ledger)
    assert not ledger.exists(), "not every role edit is a capability ship; no row, no file"


if __name__ == "__main__":
    import traceback
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failures += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"{failures} failed")
    sys.exit(1 if failures else 0)
