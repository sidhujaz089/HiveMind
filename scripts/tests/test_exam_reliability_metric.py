#!/usr/bin/env python3
"""Oracle for t1223 -- the reviewer reliability metric: the DIRECTIONS of a model's errors.

WHY (workspace/rca/cce460d3b012.md). The finding: 'it isn't reliably careful, and isn't reliably
right'. Swift's ENDURANCE errors run in BOTH directions -- 3 invented bugs (answered DEFECT where
the ground truth is CANNOT-DETERMINE), 2 false all-clears (answered NOT-A-DEFECT where the truth
is CANNOT-DETERMINE), 6 over-cautious, 1 false alarm. exam.py scores every item against ground
truth, so rows[].expected and rows[].given already record the direction of every miss; exam.py
sums items into real_score/trap_score and the direction is erased by the roll-up. results/*.json
therefore cannot answer 'is this model safe to let review code' -- MANAGER 5/5 and 'untrustworthy
at judging code' coexist in the same directory, in the same file set, with nothing joining them.

RED ON MAIN: projects/local-inference/exam.py (339 lines) defines no reliability_block and no
pass_bar_conditions, its result dict carries no 'reliability' key, and its result dict sets 'model'
twice. Every test below fails on that, none on the oracle's own wiring.

NO LIVE STATE: no model call, no subprocess, no server, no clock read, no write anywhere. The
fixture is the COMMITTED artefact workspace/exams/results/ENDURANCE-swift.json, read from disk, so
the 3/2/6/1 counts are the artefact's, not invented here.
"""
import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
import exam  # noqa: E402

RESULTS = REPO / "workspace" / "exams" / "results" / "ENDURANCE-swift.json"
DIRECTIONS = ("invented_bugs", "false_all_clears", "over_cautious", "false_alarms")


def _rows():
    """The scored rows of the committed Swift ENDURANCE sitting, read from disk every call."""
    return json.loads(RESULTS.read_text(encoding="utf-8"))["rows"]


def test_the_metric_functions_exist_on_the_module():
    for name in ("reliability_block", "pass_bar_conditions"):
        assert callable(getattr(exam, name, None)), f"exam.py defines no callable {name!r}"


def test_the_fixture_is_the_committed_sitting_and_not_a_synthetic_row_set():
    """A fixture built from real rows only, because a synthetic one encodes what we imagine the
    paper looks like; the gap between that and the file on disk is where a vacuous pass hides."""
    assert RESULTS.is_file(), f"{RESULTS} is not on disk: the metric's only real evidence is missing"
    rows = _rows()
    assert len(rows) >= 12, f"ENDURANCE-swift.json carries {len(rows)} rows; expected a full paper"
    assert all({"id", "expected", "given", "points"} <= set(r) for r in rows), \
        "a row lacks id/expected/given/points -- the directions are not recoverable from the rows"


def test_the_real_swift_sitting_shows_errors_in_both_directions():
    """The exact numbers the finding names, computed from the committed file."""
    b = exam.reliability_block(_rows())
    for k in DIRECTIONS:
        assert k in b, f"reliability_block returns no {k!r} key: keys were {sorted(b)}"
    assert b["invented_bugs"] == 3, f"invented bugs: {b['invented_bugs']} != 3"
    assert b["false_all_clears"] == 2, f"false all-clears: {b['false_all_clears']} != 2"
    assert b["over_cautious"] == 6, f"over-cautious: {b['over_cautious']} != 6"
    assert b["false_alarms"] == 1, f"false alarms: {b['false_alarms']} != 1"
    assert b["verdict"] == "BOTH-DIRECTIONS", f"verdict: {b['verdict']!r}"
    stored = json.loads(RESULTS.read_text(encoding="utf-8"))
    points = sum(r["points"] for r in _rows())
    assert points == stored["real_score"] + stored["trap_score"], (
        f"rows sum to {points}, not real_score+trap_score = "
        f"{stored['real_score']}+{stored['trap_score']}")


def test_each_direction_is_counted_separately():
    """One row per direction, so a build that lumps them together cannot pass."""
    rows = [
        {"id": "a", "expected": "CANNOT-DETERMINE", "given": "DEFECT", "points": -1},
        {"id": "b", "expected": "CANNOT-DETERMINE", "given": "NOT-A-DEFECT", "points": -1},
        {"id": "c", "expected": "NOT-A-DEFECT", "given": "CANNOT-DETERMINE", "points": -1},
        {"id": "d", "expected": "NOT-A-DEFECT", "given": "DEFECT", "points": -1},
    ]
    b = exam.reliability_block(rows)
    assert (b["invented_bugs"], b["false_all_clears"], b["over_cautious"], b["false_alarms"]) == (1, 1, 1, 1), b
    assert b["verdict"] == "BOTH-DIRECTIONS", b["verdict"]


def test_a_model_that_only_asks_questions_is_not_reported_as_safe():
    """Retreat is not care. 26 CANNOT-DETERMINE answers answer nothing."""
    rows = [{"id": f"i{n}", "expected": w, "given": "CANNOT-DETERMINE", "points": p}
            for n, (w, p) in enumerate([("NOT-A-DEFECT", -1), ("DEFECT", -1), ("CANNOT-DETERMINE", 1)])]
    b = exam.reliability_block(rows)
    assert b["over_cautious"] == 2, b
    assert b["invented_bugs"] == 0 and b["false_alarms"] == 0, f"over-caution counted as a confident wrong verdict: {b}"
    assert b["false_all_clears"] == 0, f"over-caution counted as a false all-clear: {b}"
    assert b["verdict"] == "OVER-CAUTIOUS", b["verdict"]


def test_a_model_that_never_flags_is_not_reported_as_safe():
    rows = [{"id": "x", "expected": "CANNOT-DETERMINE", "given": "NOT-A-DEFECT", "points": -1}]
    b = exam.reliability_block(rows)
    assert b["false_all_clears"] == 1 and b["over_cautious"] == 0, b
    assert b["verdict"] == "CONFIDENT-WRONG", b["verdict"]


def test_a_clean_sitting_is_reported_clean():
    """Negative assertion: a model with no directional errors must not be flagged."""
    rows = [{"id": "x", "expected": "DEFECT", "given": "DEFECT", "points": 1},
            {"id": "y", "expected": "CANNOT-DETERMINE", "given": "CANNOT-DETERMINE", "points": 1}]
    b = exam.reliability_block(rows)
    assert all(b[k] == 0 for k in DIRECTIONS), b
    assert b["verdict"] == "CLEAN", b["verdict"]


def test_the_conditions_come_from_the_bar_file_and_not_from_a_hardcoded_list():
    """The bar is prose in one file; exam.py must read it, not restate it (L-046 drift).
    These ids are what the real table says -- ENDURANCE is named by condition 6 too, because
    condition 6 is measured on the ENDURANCE sitting."""
    assert exam.pass_bar_conditions("ENDURANCE") == [1, 2, 6], exam.pass_bar_conditions("ENDURANCE")
    assert exam.pass_bar_conditions("MANAGER") == [3, 5], exam.pass_bar_conditions("MANAGER")
    assert exam.pass_bar_conditions("SENIOR") == [4], exam.pass_bar_conditions("SENIOR")
    assert exam.pass_bar_conditions("NO-SUCH-PAPER") == [], exam.pass_bar_conditions("NO-SUCH-PAPER")


def test_the_block_names_the_bar_it_is_measured_against():
    b = exam.reliability_block(_rows(), "ENDURANCE")
    assert b["conditions"] == [1, 2, 6], f"conditions: {b.get('conditions')!r}"
    assert b["pass_bar"].replace("\\", "/").endswith("SWIFT-PASS-CONDITIONS.md"), b.get("pass_bar")


def test_the_result_row_carries_the_block_and_loses_its_duplicate_model_key():
    """WIRING, read off the source (no model is called). A metric computed to stdout and written
    nowhere is the defect; so the dict exam.py writes must contain the block, the block must come
    from reliability_block(rows), and the dict must stop setting 'model' twice."""
    src = (REPO / "projects" / "local-inference" / "exam.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    main = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    assert main is not None, "exam.py has no main() -- the result row is written somewhere else"
    dicts = [n for n in ast.walk(main) if isinstance(n, ast.Dict)]
    res = next((d for d in dicts
                if any(isinstance(k, ast.Constant) and k.value == "real_score" for k in d.keys)), None)
    assert res is not None, 'no dict with a real_score key in main() -- the result row was not found'
    names = [k.value for k in res.keys if isinstance(k, ast.Constant)]
    assert "reliability" in names, f"the result row has no reliability key; keys are {names}"
    assert names.count("model") == 1, f"the result row sets 'model' {names.count('model')} times: {names}"
    val = res.values[names.index("reliability")]
    assert isinstance(val, ast.Call) and getattr(val.func, "id", None) == "reliability_block", (
        f"reliability must be computed by reliability_block(rows), not stored: {ast.dump(val)[:160]}")
    assert val.args and getattr(val.args[0], "id", None) == "rows", (
        f"reliability_block must be called on the scored rows: {[ast.dump(a)[:60] for a in val.args]}")
    printed = [n for n in ast.walk(main) if isinstance(n, ast.Call)
               and getattr(n.func, "id", None) == "print"]
    assert any("reliability" in ast.unparse(p).lower() for p in printed), (
        "main() prints no reliability line -- the metric is written but never shown")


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as exc:  # noqa: BLE001 -- a failure must say what and why
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
