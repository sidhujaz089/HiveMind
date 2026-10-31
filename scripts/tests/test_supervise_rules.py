#!/usr/bin/env python3
"""Rules-first supervision (Jas, 2026-10-19): a failed build whose log ENDS in a machine state is
decided without a model.

  SERVER-DOWN  the model server did not answer -> no task, no park, not counted against the brief
  HARNESS-BUG  the harness crashed -> ONE task per cause (exception + file:line), noted on repeat
  anything else -> None, and the model decides as before

Run: python3 scripts/tests/test_supervise_rules.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import supervise as S  # noqa: E402

CRASH = """  attempt 1 … building
Traceback (most recent call last):
  File "/home/x/ai-workspace/projects/local-inference/coder.py", line 2551, in main
    base = run_suites(root)
  File "/home/x/ai-workspace/projects/local-inference/coder.py", line 801, in run_suites
    return _declared
UnboundLocalError: cannot access local variable '_declared' where it is not associated with a value
"""


def test_server_down_at_the_end_is_ruled():
    d = S.rule_decision("  attempt 1 … asking\n  NoServer: 'swift' is offered by vllm-swift, and none answers\n")
    assert d and d["decision"] == "SERVER-DOWN", d


def test_a_server_blip_long_before_the_end_is_not():
    log = "NoServer: none answers\n" + "\n".join(f"  attempt {i} … ok" for i in range(40)) + \
          "\n  FAIL test_x: assert 1 == 2\n"
    assert S.rule_decision(log) is None


def test_a_crash_is_one_harness_bug_named_by_its_cause():
    d = S.rule_decision(CRASH)
    assert d["decision"] == "HARNESS-BUG", d
    assert d["bug_title"] == "coder.py crashed: UnboundLocalError at coder.py:801", d["bug_title"]
    assert "UnboundLocalError" in d["bug_evidence"]


def test_a_duplicate_run_is_recorded_only():
    d = S.rule_decision("REFUSED: pid 646839 is already running this job (brief-ab12, model swift).\n")
    assert d["decision"] == "DUPLICATE-RUN", d
    assert S.harness_bug_keys([{"failure": "k", "decision": "DUPLICATE-RUN"}]) == {"k"}


def test_a_timeout_is_the_server_not_the_brief():
    assert S.rule_decision("  attempt 2 … ERROR TimeoutError: timed out\n")["decision"] == "SERVER-DOWN"


def test_an_http_400_is_one_task_per_status():
    d = S.rule_decision("  attempt 1 … ERROR HTTPError: HTTP Error 400: Bad Request\n")
    assert d["decision"] == "HARNESS-BUG" and d["bug_title"] == "model call rejected: HTTP 400 Bad Request", d


def test_a_bare_exception_is_one_task_per_type():
    d = S.rule_decision("shutil.Error: [('a', 'b', 'gone')]\n")
    assert d["bug_title"] == "harness raised shutil.Error", d


RED_LOG = """  attempt 1 … tests fail (1: test_widget.py, 40s)
--- test_widget.py output (whole) ---
AssertionError: spin() returned 2, want 3
--- end test_widget.py ---
  attempt 2 … tests fail (1: test_widget.py, 38s)
  stopping: two identical TESTS-FAILED failures
  last: TESTS-FAILED — test_widget.py
"""


def test_a_still_red_target_goes_back_to_scoping():
    d = S.rule_decision(RED_LOG, "scripts/tests/test_widget.py")
    assert d and d["decision"] == "STILL-RED", d
    assert S.still_red("p1-t1-w", "sha", [{"decision": "STILL-RED", "chunk": "p1-t1-w", "brief_sha256": "sha"}])


def test_red_collateral_with_a_green_target_is_not_still_red():
    log = RED_LOG.replace("tests fail (1: test_widget.py", "tests fail (1: test_other.py")
    d = S.rule_decision(log, "scripts/tests/test_widget.py")
    assert not d or d["decision"] != "STILL-RED", d


def test_the_dispatcher_sends_still_red_to_the_scoper():
    import dispatch_loop as DL
    assert DL.brief_defect("PARKED: STILL RED: the target suite stayed red after every attempt -- x")


def test_an_ordinary_red_suite_goes_to_the_model():
    assert S.rule_decision("  attempt 3 … NOT ACCEPTED: test_x still red\n  FAIL test_x\n") is None


def test_server_down_does_not_count_against_the_brief():
    rows = [{"failure": "k1", "decision": "SERVER-DOWN"}, {"failure": "k2", "decision": "PARK"}]
    assert S.harness_bug_keys(rows) == {"k1"}


def test_server_down_files_nothing():
    class NoStore:
        def __getattr__(self, n):
            raise AssertionError(f"SERVER-DOWN must not touch the task store ({n})")
    out = S.execute({"chunk": "p1-t1-x", "model": "swift", "at": "now"},
                    {"decision": "SERVER-DOWN", "why": "x"}, store=NoStore())
    assert "runs again" in out, out


def test_a_second_crash_of_the_same_cause_is_noted_not_filed():
    class Store:
        def __init__(self):
            self.tasks = {"t9": ({"title": "HARNESS: coder.py crashed: UnboundLocalError at coder.py:801",
                                  "status": "queued"}, "from a failed dispatch: p0-t0-other on gemma")}
            self.added, self.notes = [], []

        def load_all(self):
            return self.tasks

        def add(self, title, **k):
            self.added.append(title)
            return "t10"

        def add_note(self, tid, text, who=None):
            self.notes.append(tid)
    st = Store()
    d = S.rule_decision(CRASH)
    out = S.execute({"chunk": "p5-t5-coder", "model": "swift", "at": "now"}, d, store=st)
    assert st.added == [] and st.notes == ["t9"], (out, st.added, st.notes)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for n, f in tests:
        try:
            f()
            print(f"PASS {n}")
        except Exception as e:                                      # noqa: BLE001
            bad += 1
            print(f"FAIL {n}: {type(e).__name__}: {e}")
    print(f"{len(tests) - bad} passed, {bad} failed")
    sys.exit(1 if bad else 0)
