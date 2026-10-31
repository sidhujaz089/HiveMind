#!/usr/bin/env python3
'''t803 oracle: a session that ran out of room leaves a record.

workspace/rca/5b128316dc5b.md: the last assistant turn of a session ended mid-sentence
("The self-test caught a disagreement between me and the code.") and no collector
recorded it. scripts/transcripts.py drops the rows the evidence lives in by design, and
the session-close writer cannot run in the session that dies at the ceiling. This fixture
is the recorder's proof of life: it is RED while scripts/session_end.py has no writer.

Run: python3 scripts/tests/test_session_end.py   (pytest is not installed)
'''
import ast
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "session_end.py"

# The ten fields t803 names. final_sentence is stored WHOLE, not at 400 chars:
# docs/skills/never-truncate-a-record -- a cut record looks complete, and this row's whole
# job is to say what the session did not get to say.
REQUIRED_KEYS = {"session", "harness", "last_turn", "turns_expected", "truncated",
                 "final_sentence", "open_question_marker", "finding_key",
                 "task_number", "at"}

# The exact shape from the finding, and its closed form. Distinct values throughout: a
# writer that returns one placeholder for every field passes a shape-only oracle, not this one.
OPEN_COLON = ("The self-test caught a disagreement between me and the code, and the two "
              "sides are: ")
CLOSED_PERIOD = "The self-test caught a disagreement between me and the code."
OPEN_COMMA = "Tonight runs by itself, "
CLOSED_EXCLAIM = "Probe is stopped!"
QUESTION = "Which of the nine findings is the self-test disagreeing with?"


def load_session_end():
    spec = importlib.util.spec_from_file_location("session_end_under_test", MODULE)
    if spec is None or spec.loader is None:
        raise AssertionError(
            "scripts/session_end.py is not importable from " + str(MODULE)
            + " -- this fixture cannot reach the close-side recorder")
    tree = ast.parse(MODULE.read_text(encoding="utf-8"), filename=str(MODULE))
    guarded = any(
        isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
        and getattr(n.test.left, "id", "") == "__name__"
        and any(getattr(c, "id", None) == "__main__" for c in n.test.comparators)
        for n in tree.body)
    if not guarded:
        raise AssertionError(
            "scripts/session_end.py has no `if __name__ == '__main__':` guard. This fixture "
            "imports the module, so an unguarded module would run a real session close and "
            "write live state from inside a test -- add the guard, then the recorder.")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rows_of(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_module_defines_the_recorder_and_the_detector_and_one_ledger():
    mod = load_session_end()
    for name in ("record_context_exhaustion", "detect_open_cut"):
        assert callable(getattr(mod, name, None)), (
            "scripts/session_end.py defines no callable " + name +
            "() -- no recorder writes the cut, so a session can exhaust its context and "
            "leave no row (the finding's whole payload)")
    target = str(getattr(mod, "EXHAUSTION_LEDGER", ""))
    assert target.endswith("workspace/session-exhaustion.jsonl"), (
        "EXHAUSTION_LEDGER is " + repr(target) + "; t803 names ONE ledger at "
        "workspace/session-exhaustion.jsonl. A recorder writing a path nobody reads is a "
        "record nobody finds.")


def test_an_open_clause_is_flagged_and_a_finished_sentence_is_not():
    mod = load_session_end()
    for text, want, why in (
            (OPEN_COLON, True, "ends on a colon -- the finding's exact cut"),
            (OPEN_COMMA, True, "ends on a comma"),
            ("The scan covers the open findings;", False, "ends on a semicolon: the clause closed"),
            (CLOSED_PERIOD, False, "ends on a full stop"),
            (CLOSED_EXCLAIM, False, "ends on an exclamation mark"),
            ("Done: see `run_tests.py`", False, "ends inside a code span"),
            ("", False, "no text at the cut is not a cut")
    ):
        got = mod.detect_open_cut(text)
        assert isinstance(got, bool), (
            "detect_open_cut(" + repr(text) + ") returned " + repr(got) +
            " (" + type(got).__name__ + "), expected a bool -- a truthy non-bool cannot be "
            "stored as a row's truncated flag")
        assert got is want, (
            "detect_open_cut(" + repr(text) + ") = " + repr(got) + ", expected " + repr(want)
            + " (" + why + "). The rule is the inverse of a term list: the CLOSED endings are "
            "enumerable (. ! ? ... ) ] } quote backtick), so an ending outside that set is a cut.")


def test_the_recorder_computes_the_flag_from_the_sentence_it_is_given():
    # Behaviour, not shape: the caller passes the final sentence, not a verdict. A writer that
    # only records a truncated flag someone handed it cannot fire at the cut.
    mod = load_session_end()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "exhaustion.jsonl"
        open_row = mod.record_context_exhaustion(
            session="4ad43b91", harness="claude", last_turn=364, turns_expected=401,
            final_sentence=OPEN_COLON, finding_key="5b128316dc5b", task_number="t803",
            out_path=out)
        closed_row = mod.record_context_exhaustion(
            session="4ad43b91", harness="dsh", last_turn=266, turns_expected=266,
            final_sentence=CLOSED_PERIOD, finding_key="0187a2dd15d6", task_number="t803",
            out_path=out)
        assert open_row["truncated"] is True, (
            "a colon-ended sentence recorded truncated=" + repr(open_row["truncated"])
            + "; the flag must be derived from the sentence")
        assert closed_row["truncated"] is False, (
            "a full-stop sentence recorded truncated=" + repr(closed_row["truncated"]) +
            "; flagging every close as a cut makes the ledger unreadable, which is how a "
            "correct gate gets bypassed")
        assert closed_row["harness"] == "dsh" and open_row["harness"] == "claude", (
            "harness recorded as " + repr(open_row["harness"]) + ", " + repr(closed_row["harness"]) +
            " -- the two harnesses named in scripts/session_review.py turns() must be stored "
            "per row, not fixed, or a DSH cut is charged to Claude")


def test_the_row_carries_the_ten_named_fields_with_the_values_passed():
    mod = load_session_end()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "exhaustion.jsonl"
        row = mod.record_context_exhaustion(
            session="4ad43b91", harness="claude", last_turn=337, turns_expected=364,
            final_sentence=OPEN_COMMA, finding_key="2c6800f20445", task_number="t803",
            out_path=out)
        assert isinstance(row, dict), "record_context_exhaustion returned " + type(row).__name__
        assert set(row) == REQUIRED_KEYS, (
            "row keys " + repr(sorted(row)) + " are not the ten t803 names: "
            + repr(sorted(REQUIRED_KEYS)))
        assert row["session"] == "4ad43b91", "session is " + repr(row.get("session"))
        assert row["last_turn"] == 337, "last_turn is " + repr(row.get("last_turn"))
        assert row["turns_expected"] == 364, "turns_expected is " + repr(row.get("turns_expected"))
        assert row["finding_key"] == "2c6800f20445", "finding_key is " + repr(row.get("finding_key"))
        assert row["task_number"] == "t803", "task_number is " + repr(row.get("task_number"))
        assert row["final_sentence"] == OPEN_COMMA, (
            "final_sentence is " + repr(row.get("final_sentence")) + "; the sentence is the "
            "finding -- it is stored verbatim")
        assert row["open_question_marker"] is False, (
            "open_question_marker is " + repr(row.get("open_question_marker")) +
            " for a sentence with no question mark")
        assert isinstance(row["at"], str) and row["at"].endswith("Z"), (
            "at is " + repr(row.get("at")) + "; expected a UTC stamp ending Z")


def test_an_open_question_at_the_cut_is_marked():
    mod = load_session_end()
    with tempfile.TemporaryDirectory() as td:
        row = mod.record_context_exhaustion(
            session="4ad43b91", harness="claude", last_turn=12, turns_expected=40,
            final_sentence=QUESTION, finding_key="5b128316dc5b", task_number="t803",
            out_path=Path(td) / "exhaustion.jsonl")
        assert row["open_question_marker"] is True, (
            "a sentence ending on a question mark recorded open_question_marker="
            + repr(row["open_question_marker"]) + " -- the deferral that needs a number is a "
            "question left open at the cut")


def test_a_recorded_sentence_is_never_cut():
    # never-truncate-a-record: a record is never sliced, and t803's 400-char cap is NOT shipped.
    mod = load_session_end()
    long_sentence = ("The self-test caught a disagreement between me and the code, "
                     "and the two sides are: ") + ("the model's claim vs the code's behaviour " * 200)
    assert len(long_sentence) > 400, "fixture shrank: " + str(len(long_sentence))
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "exhaustion.jsonl"
        row = mod.record_context_exhaustion(
            session="4ad43b91", harness="claude", last_turn=401, turns_expected=401,
            final_sentence=long_sentence, finding_key="5b128316dc5b", task_number="t803",
            out_path=out)
        assert row["final_sentence"] == long_sentence, (
            "final_sentence stored at " + str(len(row["final_sentence"])) + " of "
            + str(len(long_sentence)) + " characters -- a cut record looks complete and the "
            "missing part is the finding (docs/skills/never-truncate-a-record)")


def test_a_truncated_cut_with_no_task_number_is_refused_and_writes_no_row():
    # Anti-deferral: "I'll finish this next session" with no number is the finding's shape.
    mod = load_session_end()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "exhaustion.jsonl"
        try:
            mod.record_context_exhaustion(
                session="4ad43b91", harness="claude", last_turn=147, turns_expected=147,
                final_sentence=OPEN_COLON, finding_key="5b128316dc5b", task_number="",
                out_path=out)
        except ValueError as e:
            assert not out.exists(), (
                "the row for a cut with no task number was written to " + str(out)
                + " before the refusal -- a ledger row charging work to no task is the "
                "deferral this task exists to stop")
            assert "task" in str(e).lower(), (
                "the refusal said " + repr(str(e)) + "; it must name the missing task number, "
                "so the next reader knows what to supply")
        else:
            raise AssertionError(
                "record_context_exhaustion accepted a cut with task_number='' -- a truncation "
                "recorded against no task is the deferral RCA 5b128316dc5b was raised on")


def test_the_ledger_is_appended_in_call_order_and_the_row_returned_is_the_row_written():
    mod = load_session_end()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "exhaustion.jsonl"
        out.write_text(json.dumps({"session": "seed", "note": "a row that predates this run"})
                       + "\n", encoding="utf-8")
        first = mod.record_context_exhaustion(
            session="aaa1", harness="claude", last_turn=10, turns_expected=20,
            final_sentence=OPEN_COLON, finding_key="k1", task_number="t803", out_path=out)
        second = mod.record_context_exhaustion(
            session="bbb2", harness="dsh", last_turn=30, turns_expected=99,
            final_sentence=CLOSED_PERIOD, finding_key="k2", task_number="t803", out_path=out)
        written = rows_of(out)
        assert len(written) == 3, (
            "expected 3 rows (1 seed + 2 new), found " + str(len(written))
            + " -- the ledger is appended, never rewritten: a rewritten file erases every "
            "cut recorded before this call")
        assert written[0]["session"] == "seed", (
            "the pre-existing row is " + repr(written[0]) + "; the seed row was clobbered")
        assert written[1]["session"] == "aaa1" and written[2]["session"] == "bbb2", (
            "rows came back in the order " + repr([r.get("session") for r in written[1:]])
            + " -- call order is the record's timeline")
        assert written[1] == first, (
            "the row written is not the row returned -- a writer that formats on the way to "
            "disk can cut what the caller checked")
        assert written[2] == second, "the row written is not the row returned (second call)"


def test_the_writer_uses_the_module_ledger_by_default_and_reports_a_write_failure():
    # A write that fails silently reads as "no session was truncated" -- the swallowed-exception
    # shape. The writer's default target must be the one constant test 1 pins, and an unwritable
    # path must say so (see record_promise_ledger in scripts/session_review.py).
    mod = load_session_end()
    src = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(MODULE))
    writer = next((n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "record_context_exhaustion"), None)
    assert writer is not None, "record_context_exhaustion is not a module-level def"
    args = [a.arg for a in list(writer.args.args) + list(writer.args.kwonlyargs)]
    assert "out_path" in args, (
        "record_context_exhaustion has no out_path parameter (has " + repr(args) +
        ") -- every test in this suite writes into a temp dir, and an oracle that writes "
        "workspace/session-exhaustion.jsonl for real is an oracle-dirty")
    handler_speaks = any(
        isinstance(n, ast.ExceptHandler) and any(
            isinstance(v, ast.Name) and v.id in ("print", "print_stderr")
            for v in ast.walk(n))
        for n in ast.walk(writer))
    assert handler_speaks, (
        "record_context_exhaustion catches an exception without printing it -- a cut that "
        "could not be recorded must announce itself, the way record_promise_ledger prints "
        "'promise ledger ... not written (OSError: ...)' (swallowed-exception-introduced)")


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failed += 1
            print("FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc))
        else:
            print("ok   " + name)
    print(str(len(tests)) + " test(s), " + str(failed) + " failed")
    sys.exit(1 if failed else 0)
