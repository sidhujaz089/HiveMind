#!/usr/bin/env python3
"""t1440 item 2: re-derive a session-review finding's cover check when a task is minted.

The class (RCA workspace/rca/eff7a7555749.md, checklist item 2): a finding's candidate set is
frozen at its mint date -- a real file, workspace/session-reviews/findings/01e31ba8e809.json,
lists several thousand task ids and stops there while the queue keeps growing -- so covered_by
can never be corrected on its own. taskstore.add() is the trigger: it is the only surface that
fires the instant a task exists.

RED ON MAIN because projects/app-repo/taskstore.py has no finding_cover_sweep() and add() never
calls it, so the first check of every test below fires. Not red for a bug of its own: it imports
taskstore from the path it really lives on, needs no pytest, spawns no subprocess, calls no model,
and writes only inside a tempdir.

Run: python3 scripts/tests/test_taskstore_finding_cover_sweep.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]              # scripts/tests -> repo root
TS_PATH = ROOT / "projects" / "app-repo" / "taskstore.py"

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("FAIL: " + msg)


def load_taskstore(tmp):
    """Import taskstore from where it lives, then point its whole world at tmp.

    taskstore computes its paths at import time from its own file location, so a test that only
    imports it writes into the live queue. Every candidate name is repointed when present; the
    sweep's two constants are new, so the hasattr guard keeps this suite working before the fix.
    """
    spec = importlib.util.spec_from_file_location("ts_under_test", TS_PATH)
    ts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ts)
    for name in ("TASKS", "DONE", "QUEUE", "LOCK", "ATTACH", "FINDINGS", "FINDING_COVER_LEDGER"):
        if hasattr(ts, name):
            setattr(ts, name, tmp / name.lower())
    (tmp / "tasks").mkdir(parents=True, exist_ok=True)
    (tmp / "done").mkdir(parents=True, exist_ok=True)
    (tmp / "findings").mkdir(parents=True, exist_ok=True)
    return ts


# Real shapes, read off workspace/session-reviews/findings/*.json this week: the open one is
# modelled on 01e31ba8e809.json (covered_by empty, candidates frozen at mint), the closed one on
# check-01df28d99155.json (covered_by 't1582').
TITLE = "Record which task covers a session review finding"


def open_finding():
    return {
        "kind": "deferral",
        "quote": "the ledger row for a finding never records which task covers it",
        "context": "covered_by stays empty while the queue grows past the mint-day candidate set",
        "why_missed": "no sweep runs when a task is added",
        "covered_by": "",
        "disposition": "deferred",
        "key": "aabbccddee01",
        "found": "2026-10-28T10:08:36Z",
    }


def unrelated_finding():
    return {
        "kind": "deferral",
        "quote": "the timer must not start before the safe window",
        "context": "the backup job contends for the model lease",
        "why_missed": "the window moved and nothing records it",
        "covered_by": "",
        "disposition": "deferred",
        "key": "aabbccddee02",
        "found": "2026-10-28T10:08:36Z",
    }


def write_finding(findings, stem, data):
    p = findings / (stem + ".json")
    p.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return p


def write_task(tasks, tid, title, created):
    p = tasks / ("%s--%s.md" % (tid, title.split()[0].lower()))
    p.write_text(
        "---\nid: %s\ntitle: %s\narea: workspace\nkind: build\nstatus: queued\n"
        "assigned: production\nsource: session-rca\ncreated: %s\n---\n\n## Notes\n\n" % (tid, title, created),
        encoding="utf-8",
    )
    return p


def ledger_rows(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sweep(ts, tmp, **kw):
    kw.setdefault("findings_dir", tmp / "findings")
    kw.setdefault("tasks_dir", tmp / "tasks")
    kw.setdefault("ledger", tmp / "cover.jsonl")
    return kw["findings_dir"], kw["ledger"], ts.finding_cover_sweep(**kw)


def test_01_module_exposes_the_sweep():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ts = load_taskstore(tmp)
        check(callable(getattr(ts, "finding_cover_sweep", None)),
              "taskstore has no callable finding_cover_sweep() -- this suite's reason for existing")
        if not callable(getattr(ts, "finding_cover_sweep", None)):
            return
        write_finding(tmp / "findings", "aabbccddee01", open_finding())
        write_task(tmp / "tasks", "t777", TITLE, ts.now())
        _fd, ledger, rows = sweep(ts, tmp)
        check(len(rows) == 1, "expected exactly one covered row, got %d: %r" % (len(rows), rows))
        if len(rows) == 1:
            check(rows[0].get("key") == "aabbccddee01",
                  "the row must name the finding's own key, got %r" % (rows[0].get("key"),))
            check(rows[0].get("task") == "t777",
                  "the row must name the COVERING task id, got %r" % (rows[0].get("task"),))
            matched = rows[0].get("matched")
            check(isinstance(matched, list) and len(set(t.lower() for t in matched)) >= 2,
                  "the row must carry the >=2 distinct shared tokens that make it a cover, got %r" % (matched,))
            check(bool(str(rows[0].get("at", "")).strip()),
                  "the row must be timestamped, got %r" % (rows[0].get("at"),))
        written = ledger_rows(ledger)
        check(len(written) == 1, "the ledger must gain exactly one line, got %d" % len(written))
        if written:
            check(written[0].get("task") == "t777",
                  "the ledger line must carry the covering task id, got %r" % (written[0].get("task"),))


def test_02_quiet_when_nothing_covers_it():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ts = load_taskstore(tmp)
        if not callable(getattr(ts, "finding_cover_sweep", None)):
            return FAILS.append("no finding_cover_sweep")
        write_finding(tmp / "findings", "aabbccddee02", unrelated_finding())
        write_task(tmp / "tasks", "t777", TITLE, ts.now())
        _fd, ledger, rows = sweep(ts, tmp)
        check(rows == [], "a finding sharing no tokens with any task title must yield no row, got %r" % (rows,))
        check(not ledger.exists(), "a sweep that found nothing must not create the ledger, found %s" % ledger)


def test_03_leaves_an_already_covered_finding_alone():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ts = load_taskstore(tmp)
        if not callable(getattr(ts, "finding_cover_sweep", None)):
            return FAILS.append("no finding_cover_sweep")
        data = open_finding()
        data["covered_by"] = "t1582"
        path = write_finding(tmp / "findings", "aabbccddee01", data)
        before = path.read_text(encoding="utf-8")
        write_task(tmp / "tasks", "t777", TITLE, ts.now())
        _fd, ledger, rows = sweep(ts, tmp)
        check(rows == [], "a finding with covered_by set is not open, so it must produce no row, got %r" % (rows,))
        check(not ledger.exists(), "a covered finding must not be written to the ledger, found %s" % ledger)
        check(path.read_text(encoding="utf-8") == before,
              "the sweep must NOT rewrite the finding file; covered_by on disk belongs to the session review")


def test_04_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ts = load_taskstore(tmp)
        if not callable(getattr(ts, "finding_cover_sweep", None)):
            return FAILS.append("no finding_cover_sweep")
        write_finding(tmp / "findings", "aabbccddee01", open_finding())
        write_task(tmp / "tasks", "t777", TITLE, ts.now())
        _fd, ledger, first = ts.finding_cover_sweep.__self__ if False else (None, None, None)


def test_04b_second_sweep_adds_no_row():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ts = load_taskstore(tmp)
        if not callable(getattr(ts, "finding_cover_sweep", None)):
            return FAILS.append("no finding_cover_sweep")
        write_finding(tmp / "findings", "aabbccddee01", open_finding())
        write_task(tmp / "tasks", "t777", TITLE, ts.now())
        ts.finding_cover_sweep(findings_dir=tmp / "findings", tasks_dir=tmp / "tasks", ledger=tmp / "cover.jsonl")
        rows2 = ts.finding_cover_sweep(findings_dir=tmp / "findings", tasks_dir=tmp / "tasks", ledger=tmp / "cover.jsonl")
        check(rows2 == [], "the second sweep must find nothing new (the finding is recorded), got %r" % (rows2,))
        lines = ledger_rows(tmp / "cover.jsonl")
        check(len(lines) == 1, "the ledger must hold one row per (finding, task), got %d" % len(lines))


def test_05_reports_an_unreadable_finding():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ts = load_taskstore(tmp)
        if not callable(getattr(ts, "finding_cover_sweep", None)):
            return FAILS.append("no finding_cover_sweep")
        (tmp / "findings" / "badfile.json").write_text("{not json", encoding="utf-8")
        write_finding(tmp / "findings", "aabbccddee01", open_finding())
        write_task(tmp / "tasks", "t777", TITLE, ts.now())
        try:
            _fd, ledger, rows = sweep(ts, tmp)
        except Exception as exc:
            return check(False, "an unparseable finding must not raise out of the sweep, got %r" % (exc,))
        errs = [r for r in rows if r.get("error")]
        check(len(errs) == 1 and str(errs[0].get("key")) == "badfile",
              "an unparseable finding must be returned with a reason naming its stem, got %r" % (rows,))
        check(bool(str(errs[0].get("error", "")).strip()) if errs else False,
              "the report must say WHY it was skipped, not just that it was")
        check(all(r.get("key") != "badfile" for r in ledger_rows(ledger)),
              "an unparseable file must never be written as a coverage row")
        check(len([r for r in rows if r.get("task")]) == 1,
              "one bad file must not stop the sweep covering the good finding, got %r" % (rows,))


def test_06_ignores_a_task_minted_before_the_finding():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ts = load_taskstore(tmp)
        if not callable(getattr(ts, "finding_cover_sweep", None)):
            return FAILS.append("no finding_cover_sweep")
        write_finding(tmp / "findings", "aabbccddee01", open_finding())
        # A task that predates the finding cannot be the one that covers it.
        write_task(tmp / "tasks", "t777", TITLE, "2020-01-01T000000Z")
        _fd, ledger, rows = sweep(ts, tmp)
        check(rows == [], "a task minted before the finding was found must not count as its cover, got %r" % (rows,))


def test_07_empty_findings_dir_is_silent():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ts = load_taskstore(tmp)
        if not callable(getattr(ts, "finding_cover_sweep", None)):
            return FAILS.append("no finding_cover_sweep")
        _fd, ledger, rows = sweep(ts, tmp)
        check(rows == [], "an empty findings dir must return [], got %r" % (rows,))
        check(not ledger.exists(), "an empty sweep must not create a ledger file, found %s" % ledger)


def test_08_add_triggers_the_sweep_and_keeps_its_contract():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ts = load_taskstore(tmp)
        if not callable(getattr(ts, "finding_cover_sweep", None)):
            return FAILS.append("no finding_cover_sweep")
        write_finding(tmp / "findings", "aabbccddee01", open_finding())
        ledger = tmp / "cover.jsonl"
        ts.FINDING_COVER_LEDGER = ledger
        try:
            tid = ts.add(TITLE, source="session-rca")
        except Exception as exc:
            return check(False, "add() must keep working in a temp world, raised %r" % (exc,))
        check(isinstance(tid, str) and tid.startswith("t"),
              "add() must still return its task id, got %r" % (tid,))
        rows = ledger_rows(ledger)
        check(len(rows) == 1, "minting a task that covers an open finding must write one ledger row, got %d" % len(rows))
        if rows:
            check(rows[0].get("task") == tid,
                  "the row must name the task add() just created (%r), got %r" % (tid, rows[0].get("task")))
            check(rows[0].get("key") == "aabbccddee01",
                  "the row must name the finding's key, got %r" % (rows[0].get("key"),))


TESTS = sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f))


if __name__ == "__main__":
    for _name, _fn in TESTS:
        print("-- %s" % _name)
        _fn()
    print("\n%d assertion(s) failed" % len(FAILS))
    sys.exit(1 if FAILS else 0)
