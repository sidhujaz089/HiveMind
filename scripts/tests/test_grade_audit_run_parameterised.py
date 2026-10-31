#!/usr/bin/env python3
"""Oracle for t1360: scripts/session_review_grade_audit.py must take
--gold/--run/--baseline/--ledger, append a grading row naming the population, and
REFUSE a comparison whose denominators differ.

Red on main for the stated reason: that file (28 lines) defines no main / population_of /
sessions_in / gold_for / grade_session / row_for and parses no flags -- its body is hardcoded
to trials/audit + trials/audit-q4 -- so every test below fails with AttributeError naming the
missing symbol, not with an import error and not with a placeholder.

No model, no server, no live state: every fixture is built in a tempfile.TemporaryDirectory,
the ledger is always a temp path, the two REAL run directories are only listed and json-loaded.
The population numbers for the real tree are COMPUTED here, never typed in.
"""
import importlib.util
import json
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts" / "session_review_grade_audit.py"
TRIALS = ROOT / "workspace" / "session-reviews" / "trials"
GOLD_REAL = TRIALS / "audit"
RUN_Q4 = TRIALS / "triage-q4"
RUN_SWIFT = TRIALS / "triage-swift"

_mod = None


def mod():
    """Import the grader by path. Once the module body is guarded by __main__ (this task's
    change), this costs nothing; today it also runs the whole audit, which is the red state."""
    global _mod
    if _mod is None:
        spec = importlib.util.spec_from_file_location("srga", SCRIPT)
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
    return _mod


def _gold_item(i, about, missed, cover):
    # keys taken from the real gold file workspace/session-reviews/trials/audit/s01.json
    return {"id": i, "about": about, "anchors": [about], "missed": missed, "cover": cover}


def _mk_gold(d, plan):
    """plan: {"s01": [(id, about, missed, cover, right, found, said), ...], ...}"""
    for sid, items in plan.items():
        (d / f"{sid}.json").write_text(json.dumps({
            "session": sid,
            "items": [_gold_item(i[0], i[1], i[2], i[3]) for i in items],
            "request": "workspace/session-reviews/pending/_absent_in_fixture.json",
            "_what": "t1360 oracle fixture",
        }))


def _mk_run(d, plan):
    """One sNN.log per session naming, repo-relative-style, the graded result json -- the shape
    scripts/session_review_grade_audit.py:14 reads (its regex is trials/\\S+\\.json here)."""
    for sid, items in plan.items():
        rp = d / f"{sid}.result.json"
        rp.write_text(json.dumps({
            "grade": [{"right": i[4], "found": i[5], "said": i[6], "cover": ""} for i in items],
            "rows": [{"verdict": "MISSED"}] * plan[sid + "_noise"]
                    + [{"verdict": "PROMISED"}] * 2,
        }))
        (d / f"{sid}.log").write_text(
            f"invocation\nwrote {rp}\n")
    return plan


PLAN = {
    "s01": [("A1", "about A1", "no", "t461", True, True, "yes"),
            ("A2", "about A2", "yes", "t719", True, True, "yes")],
    "s02": [("B1", "about B1", "yes", "t900", False, True, "MISSED"),
            ("B2", "about B2", "either", "t901", False, False, "MISSED")],
    "s03": [("C1", "about C1", "no", "t999", True, True, "no"),
            ("C2", "about C2", "yes", "t777", False, False, "MISSED")],
}
NOISE = {"s01_noise": 1, "s02_noise": 3, "s03_noise": 1}
PLAN.update(NOISE)


def _cands_none(items, gold_json):
    return []


def test_main_writes_one_row_naming_the_population():
    m = mod()
    with tempfile.TemporaryDirectory() as td:
        g, r = Path(td) / "gold", Path(td) / "run"
        g.mkdir(); r.mkdir()
        _mk_gold(g, PLAN)
        _mk_run(r, PLAN)
        ledger = Path(td) / "GRADE-LEDGER.jsonl"
        buf = StringIO()
        with redirect_stdout(buf):
            rc = m.main(["--gold", str(g), "--run", str(r),
                         "--ledger", str(ledger), "--as-of", "2026-10-04T000000Z",
                         "--script-rev", "abc1234"],
                        turn_fn=None, candidate_fn=_cands_none)
        out = buf.getvalue()
        assert rc == 0, f"main returned {rc} on matching fixtures; output:\n{out[-2000:]}"
        lines = [l for l in ledger.read_text().splitlines() if l.strip()]
        assert len(lines) == 1, f"expected exactly 1 ledger row, got {len(lines)}: {lines}"
        row = json.loads(lines[0])
        for k in ("run", "gold_set", "sessions", "items", "right", "real_misses",
                  "noise", "script_rev", "as_of"):
            assert k in row, f"ledger row missing required key {k!r}: {row}"
        # the population is the GOLD set's, not the run's file count: 3 sessions x 2 items.
        assert row["sessions"] == 3, f"sessions should be 3, got {row['sessions']!r}"
        assert row["items"] == 6, f"items should be 6 (gold items of 3 sessions), got {row['items']!r}"
        # right counts gold items the run graded right: s01 A1,A2 + s03 C1 = 3.
        assert row["right"] == 3, f"right should be 3, got {row['right']!r}"
        # real_misses counts gold items whose missed=="yes" AND were graded right: only A2.
        assert row["real_misses"] == 1, f"real_misses should be 1 (A2), got {row['real_misses']!r}"
        # noise counts result rows judged MISSED: 1+3+1 = 5, not every row (9).
        assert row["noise"] == 5, f"noise should be 5 MISSED rows, got {row['noise']!r}"
        assert row["as_of"] == "2026-10-04T000000Z", f"as_of not recorded: {row['as_of']!r}"
        assert row["script_rev"] == "abc1234", f"script_rev not recorded: {row['script_rev']!r}"
        assert row["run"] == str(r), f"run must name the run graded: {row['run']!r}"
        assert row["gold_set"] == str(g), f"gold_set must name the gold dir: {row['gold_set']!r}"
        # the population must be EMITTED, not only stored: "items=" appears in the report.
        assert "items=" in out, f"report must print the population; tail:\n{out[-1500:]}"
        # per-item detail survives the refactor (AUDIT-Q4-GRADE-2026-10-05.md reads it).
        assert "A1" in out, f"per-item lines expected; tail:\n{out[-1500:]}"
        assert "WRONG-JUDGE" in out, f"B1 (not right, found=True) is WRONG-JUDGE; tail:\n{out[-1500:]}"
        assert "NOT-EXTRACTED" in out, f"C2 (not right, not found) is NOT-EXTRACTED; tail:\n{out[-1500:]}"
        assert "RIGHT" in out, f"per-item RIGHT verdict missing; tail:\n{out[-1500:]}"
        assert "NO-RESULT" not in out, f"every fixture session has a result file; tail:\n{out[-1500:]}"


def test_refuses_mismatched_denominators_and_records_nothing():
    m = mod()
    with tempfile.TemporaryDirectory() as td:
        g, r, b = Path(td) / "gold", Path(td) / "run", Path(td) / "base"
        g.mkdir(); r.mkdir(); b.mkdir()
        _mk_gold(g, PLAN)                      # 3 sessions / 6 items on gold
        _mk_run(r, PLAN)                        # full run: 3 sessions / 6 items
        _mk_run(b, {"s01": PLAN["s01"], "s01_noise": NOISE["s01_noise"]})   # 1 session / 2 items
        ledger = Path(td) / "GRADE-LEDGER.jsonl"
        buf = StringIO()
        with redirect_stdout(buf):
            rc = m.main(["--gold", str(g), "--run", str(r), "--baseline", str(b),
                         "--ledger", str(ledger), "--as-of", "2026-10-04T000000Z",
                         "--script-rev", "abc1234"],
                        turn_fn=None, candidate_fn=_cands_none)
        out = buf.getvalue()
        assert rc != 0, (f"a comparison whose populations differ (3 sessions/6 items vs "
                         f"1 session/2 items) must not exit 0; tail:\n{out[-1500:]}")
        # the refusal is a record: BOTH denominators printed in full, so the reader sees the gap.
        assert "items=" in out, f"refusal must print both populations; tail:\n{out[-1500:]}"
        assert "6" in out and "2" in out, f"refusal must print both item counts; tail:\n{out[-1500:]}"
        assert "sessions=" in out, f"refusal must print the session denominators; tail:\n{out[-1500:]}"
        assert not ledger.exists() or not ledger.read_text().strip(), \
            "a refused comparison must write no grading row (a half-measurement must not land)"


def test_matching_baseline_is_accepted_and_appends_one_row():
    m = mod()
    with tempfile.TemporaryDirectory() as td:
        g, r, b = Path(td) / "gold", Path(td) / "run", Path(td) / "base"
        g.mkdir(); r.mkdir(); b.mkdir()
        _mk_gold(g, PLAN)
        _mk_run(r, PLAN)
        _mk_run(b, PLAN)
        ledger = Path(td) / "GRADE-LEDGER.jsonl"
        buf = StringIO()
        with redirect_stdout(buf):
            rc = m.main(["--gold", str(g), "--run", str(r), "--baseline", str(b),
                         "--ledger", str(ledger), "--as-of", "2026-10-04T000000Z",
                         "--script-rev", "abc1234"],
                        turn_fn=None, candidate_fn=_cands_none)
        out = buf.getvalue()
        assert rc == 0, f"equal populations must be comparable; tail:\n{out[-1500:]}"
        assert out.count("items=") >= 2, f"both sides' populations must be printed; tail:\n{out[-1500:]}"
        assert len([l for l in ledger.read_text().splitlines() if l.strip()]) == 1, \
            "one invocation appends exactly one row, not one per session"


def test_session_with_a_done_and_no_log_is_absent_from_the_population():
    """The 83-vs-86 mechanism, verified on disk: trials/triage-q4 has s17.done and NO s17.log."""
    m = mod()
    with tempfile.TemporaryDirectory() as td:
        r = Path(td) / "run"
        r.mkdir()
        (r / "s01.log").write_text("ok\n")
        (r / "s01.done").write_text("ok\n")
        (r / "s17.done").write_text("ok\n")          # never ran: no s17.log
        (r / "ALL.log").write_text("batch log, not a session\n")
        (r / "run.sh.txt").write_text("invocation\n")
        got = list(m.sessions_in(str(r)))
        assert got == ["s01"], f"a .done with no .log is not a session that ran; got {got}"
        assert "s17" not in got and "ALL" not in got, \
            f"only sNN.log counts as a session; got {got}"


def test_population_of_the_real_runs_differs_and_is_computed():
    """Real artefacts (read-only): triage-q4 ran 18 of the 19 gold sessions -- s17 has a .done
    and no .log -- so the promised comparison has never been like-for-like. The item counts are
    summed from the real gold jsons here, never typed in."""
    m = mod()
    assert GOLD_REAL.is_dir() and RUN_Q4.is_dir() and RUN_SWIFT.is_dir(), \
        "the t663 trial dirs must exist for this comparison to mean anything"
    p_q4 = m.population_of(str(GOLD_REAL), str(RUN_Q4))
    p_sw = m.population_of(str(GOLD_REAL), str(RUN_SWIFT))
    assert list(p_q4) and list(p_sw), f"population must name sessions and items: {p_q4} {p_sw}"
    assert set(p_q4) == set(p_sw) == {"sessions", "items"}, f"keys: {p_q4} {p_sw}"
    assert p_q4["sessions"] == 18, f"triage-q4 has 18 sNN.log files: {p_q4}"
    assert p_sw["sessions"] == 19, f"triage-swift has 19 sNN.log files: {p_sw}"
    assert p_sw["items"] > 0 and p_q4["items"] > 0, f"items must be counted: {p_q4} {p_sw}"
    assert p_sw["items"] > p_q4["items"], (
        f"the 18-session run must cover fewer gold items than the 19-session one, "
        f"got q4={p_q4} swift={p_sw} -- if this fails the gold dir no longer keys sessions "
        f"by sNN, and the grader's session-keying needs revisiting")
    # the grader must refuse to compare these two real runs: 18 vs 19, 83-against-86 in spirit.
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "GRADE-LEDGER.jsonl"
        buf = StringIO()
        with redirect_stdout(buf):
            rc = m.main(["--gold", str(GOLD_REAL), "--run", str(RUN_Q4),
                         "--baseline", str(RUN_SWIFT), "--ledger", str(ledger),
                         "--as-of", "2026-10-04T000000Z", "--script-rev", "abc1234"],
                        turn_fn=None, candidate_fn=_cands_none)
        out = buf.getvalue()
        assert rc != 0, (f"grading triage-q4 against triage-swift on the same gold must be "
                        f"REFUSED as a mismatched population; tail:\n{out[-1500:]}")
        assert not ledger.exists() or not ledger.read_text().strip(), \
            "the refused real comparison must not leave a grading row behind"


def test_grades_every_gold_item_even_when_the_run_has_no_result_for_it():
    """A run that never ran a session must reduce the DENOMINATOR loudly, not silently."""
    m = mod()
    with tempfile.TemporaryDirectory() as td:
        g, r = Path(td) / "gold", Path(td) / "run"
        g.mkdir(); r.mkdir()
        _mk_gold(g, PLAN)
        _mk_run(r, PLAN)
        (r / "s03.log").write_text("keep+judge ran, result file missing\n")  # no json named
        ledger = Path(td) / "GRADE-LEDGER.jsonl"
        buf = StringIO()
        with redirect_stdout(buf):
            rc = m.main(["--gold", str(g), "--run", str(r),
                         "--ledger", str(ledger), "--as-of", "2026-10-04T000000Z",
                         "--script-rev", "abc1234"],
                        turn_fn=None, candidate_fn=_cands_none)
        out = buf.getvalue()
        assert rc == 0, f"an unresolvable result file is a finding, not a crash; tail:\n{out[-1500:]}"
        assert "NO-RESULT" in out, f"the missing result must be named in the record; tail:\n{out[-1500:]}"
        row = json.loads([l for l in ledger.read_text().splitlines() if l.strip()][-1])
        assert row["items"] == 6, f"gold items are still owed even when a result is missing: {row}"
        assert row["right"] == 2, f"s03's two items score 0: expected right=2, got {row['right']!r}"


def test_ledger_row_shape_and_jsonl_append_is_append_only():
    m = mod()
    with tempfile.TemporaryDirectory() as td:
        g, r = Path(td) / "gold", Path(td) / "run"
        g.mkdir(); r.mkdir()
        _mk_gold(g, PLAN)
        _mk_run(r, PLAN)
        ledger = Path(td) / "GRADE-LEDGER.jsonl"
        ledger.write_text('{"run": "prior", "items": 999}\n')
        for _ in range(2):
            buf = StringIO()
            with redirect_stdout(buf):
                rc = m.main(["--gold", str(g), "--run", str(r),
                             "--ledger", str(ledger), "--as-of", "2026-10-04T000000Z",
                             "--script-rev", "abc1234"],
                            turn_fn=None, candidate_fn=_cands_none)
            assert rc == 0, f"second grading run failed: {buf.getvalue()[-1500:]}"
        lines = [l for l in ledger.read_text().splitlines() if l.strip()]
        assert len(lines) == 3, f"grading is append-only (1 prior + 2 new), got {len(lines)}"
        assert json.loads(lines[0]) == {"run": "prior", "items": 999}, "must not rewrite history"
        for l in lines[1:]:
            row = json.loads(l)
            assert row["items"] == 6 and row["sessions"] == 3, f"row: {row}"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {str(e)[:4000]}")
    print(f"{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)
