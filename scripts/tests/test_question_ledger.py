#!/usr/bin/env python3
"""t736 -- a task close must record the Jas-addressed questions it carries.

The contract (task t736, checklist item 2) adds to projects/app-repo/taskstore.py:

    QUESTION_LEDGER = "workspace/tasks/question-ledger.jsonl"      # repo-relative
    find_open_questions(text) -> list[{"question": str,
                                       "options": [{"letter": str, "text": str}, ...]}]
    write_question_ledger(task_id, text, *, path=None, asked_at="") -> list[dict]
    unanswered_rows(path) -> list[dict]

One row per open question, appended as one JSON object per line, with exactly the keys
{ts, task, question, options, asked_at, due, answered_by, answer}; `ts` is UTC
%Y-%m-%dT%H:%M:%SZ.  A question is ANSWERED when some row for the same
(task, question) carries a non-empty `answer`.

RED ON MAIN because none of those four names exists:
`grep -rln "question-ledger" --include=*.py .` returns nothing (checked 2026-10-11).
"""
import importlib.util
import json
import re
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "projects" / "app-repo" / "taskstore.py"
NEW_NAMES = ("QUESTION_LEDGER", "find_open_questions", "write_question_ledger",
             "unanswered_rows")
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

Q_A = "which web-search provider should the box use?"
OPT_A = ["self-hosted searxng on the box", "DuckDuckGo html endpoint", "a paid API key in env"]
BODY_ASKED = (
    "Refuse to close a task carrying an unanswered question.\n"
    "\n"
    f"Decide: {Q_A}\n"
    f"  A) {OPT_A[0]}\n"
    f"  B) {OPT_A[1]}\n"
    f"  C) {OPT_A[2]}\n"
    "\n"
    "The close gate itself is the next chunk.\n"
)
BODY_PLAIN = (
    "Wire the online path to the large model.\n"
    "\n"
    "We decided to ship the 4-bit build and left the rename for the next session.\n"
    "- the launcher takes --model and --keep-alive\n"
)
BODY_OPTIONS_ONLY = (
    "Notes from the model cleanup.\n"
    "\n"
    "A) keep Qwen 3.8 Flash 79GB 2-bit\n"
    "B) keep Nemotron\n"
    "C) delete both\n"
)
Q_B = "should I mount the network drive for a full home-directory backup?"
BODY_B = (
    "Backup coverage before the launch.\n"
    "\n"
    f"Decide: {Q_B}\n"
    "  A) yes, mount it and run nas-backup against the whole home directory\n"
    "  B) no, the current snapshot set is enough\n"
)


def _taskstore():
    spec = importlib.util.spec_from_file_location("taskstore_under_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {MODULE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    missing = [n for n in NEW_NAMES if not hasattr(mod, n)]
    assert not missing, f"taskstore.py has no {missing}: the question ledger writer is absent"
    return mod


def test_new_names_exist():
    _taskstore()


def test_row_written_for_a_decide_question():
    """The body's own Decide: question, its real A/B/C options, and every required field."""
    mod = _taskstore()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "question-ledger.jsonl"
        rows = mod.write_question_ledger("t488", BODY_ASKED, path=ledger,
                                         asked_at="2026-09-30T12:14Z")
        assert len(rows) == 1, f"one Decide: line -> one row, got {rows!r}"
        row = rows[0]
        assert set(row) == {"ts", "task", "question", "options", "asked_at", "due",
                            "answered_by", "answer"}, f"row keys {sorted(row)}"
        assert row["task"] == "t488", row
        assert row["question"] == Q_A, row
        assert [o["letter"] for o in row["options"]] == ["A", "B", "C"], row
        assert [o["text"] for o in row["options"]] == OPT_A, row
        assert row["asked_at"] == "2026-09-30T12:14Z", row
        assert row["due"] == "", row
        assert row["answered_by"] == "", row
        assert row["answer"] == "", row
        assert TS_RE.fullmatch(str(row["ts"])), f"ts {row['ts']!r} is not UTC %Y-%m-%dT%H:%M:%SZ"
        on_disk = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
        assert on_disk == rows, f"file holds {on_disk!r}, call returned {rows!r}"


def test_extract_finds_only_a_real_question():
    """Negative assertions: prose is not a question, and A)/B)/C) with no question line is not one."""
    mod = _taskstore()
    assert mod.find_open_questions(BODY_ASKED) == [
        {"question": Q_A,
         "options": [{"letter": "A", "text": OPT_A[0]},
                     {"letter": "B", "text": OPT_A[1]},
                     {"letter": "C", "text": OPT_A[2]}]}
    ], mod.find_open_questions(BODY_ASKED)
    assert mod.find_open_questions(BODY_PLAIN) == [], mod.find_open_questions(BODY_PLAIN)
    assert mod.find_open_questions(BODY_OPTIONS_ONLY) == [], mod.find_open_questions(BODY_OPTIONS_ONLY)


def test_close_with_no_question_writes_nothing():
    mod = _taskstore()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "question-ledger.jsonl"
        assert mod.write_question_ledger("t107", BODY_PLAIN, path=ledger) == []
        assert not ledger.exists() or ledger.read_text() == "", \
            "a task carrying no question must append no row"


def test_ledger_accumulates_across_two_closes():
    """Append, not overwrite, and the two closes carry DISTINCT payloads."""
    mod = _taskstore()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "question-ledger.jsonl"
        rows1 = mod.write_question_ledger("t293", BODY_ASKED, path=ledger,
                                          asked_at="2026-10-04T09:00Z")
        rows2 = mod.write_question_ledger("t640", BODY_B, path=ledger,
                                          asked_at="2026-10-04T09:30Z")
        lines = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
        assert len(lines) == 2, f"expected 2 appended rows, got {lines!r}"
        assert lines[0]["question"] == Q_A, lines
        assert lines[1]["question"] == Q_B, lines
        assert lines[0]["asked_at"] == "2026-10-04T09:00Z", lines
        assert lines[1]["asked_at"] == "2026-10-04T09:30Z", lines
        assert rows2 == [lines[1]], f"return value is the rows appended: {rows2!r}"
        assert [r["task"] for r in rows1 + rows2] == ["t293", "t640"], rows1 + rows2


def test_open_questions_close_when_the_ledger_records_an_answer():
    """The rule the close gate will lean on: open until a row for the same (task, question) answers."""
    mod = _taskstore()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "question-ledger.jsonl"
        rows = mod.write_question_ledger("t055", BODY_ASKED, path=ledger)
        assert [r["question"] for r in mod.unanswered_rows(ledger)] == [Q_A], rows
        answered = dict(rows[0])
        answered["answer"] = "A"
        answered["answered_by"] = "Jas"
        with ledger.open("a") as fh:
            fh.write(json.dumps(answered) + "\n")
        assert mod.unanswered_rows(ledger) == [], mod.unanswered_rows(ledger)


def test_ledger_home_is_the_named_file():
    """Writer and (next chunk's) gate must agree on one path, not two."""
    mod = _taskstore()
    want = str(ROOT / "workspace" / "tasks" / "question-ledger.jsonl")
    got = str(Path(str(mod.QUESTION_LEDGER)))
    if not got.startswith("/"):
        got = str(ROOT / str(mod.QUESTION_LEDGER))
    assert got == want, f"QUESTION_LEDGER resolves to {got!r}, expected {want!r}"


TESTS = [
    test_new_names_exist,
    test_row_written_for_a_decide_question,
    test_extract_finds_only_a_real_question,
    test_close_with_no_question_writes_nothing,
    test_ledger_accumulates_across_two_closes,
    test_open_questions_close_when_the_ledger_records_an_answer,
    test_ledger_home_is_the_named_file,
]

if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"{len(TESTS) - failed}/{len(TESTS)} passed")
    raise SystemExit(1 if failed else 0)
