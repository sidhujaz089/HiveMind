#!/usr/bin/env python3
"""t1389 -- every scoping proposals row records WHY a model answer failed to parse.

WHY THIS EXISTS. The pre-phase0 collector wrote `finished=false` plus a 200-char snippet for 16
of 70 rows; the raw answer and the model's stop-reason were never written, so those 16 rows
cannot be diagnosed. A model cut mid-JSON by a token cap (`{"id": "t010", "verdict": "SCO`)
and a model that narrated prose instead of answering (t020) leave the same record.
Evidence: workspace/rca/a65f150baac6.md sections 1-2, and workspace/scoping-proposals.jsonl --
70 rows, every one stamped `pre-phase0-broken`, zero `scope_queue-v1` rows (checked this session).
The current collector keeps `raw` (scripts/scope_queue.py:303) and a `status` (ok / no-json /
no-match->no-verdict / invalid-json / ambiguous). `status` records the OUTCOME of the parse;
nothing records its CAUSE. That is the field this file pins.

THE CONTRACT, for run(tasks, ask, out_path, ledger_path, lease_path=None, model=None,
                      run_id=None, store=None) in scripts/scope_queue.py:
  ask(tid, body) may return the answer string (the frozen contract of
  scripts/tests/test_scope_run.py) or a 2-tuple (answer, done_reason).
  Every proposals row written from an answer carries:
    raw          the full answer -- never a slice
    done_reason  the stop-reason the collector was handed, None when it was handed none
    parse_status "ok" | "truncated-json" | "no-json-at-all" | "malformed-json"

WHEN IT CANNOT BE DONE HONESTLY. The deferral named four values, the fourth being
"http-error". scope_queue.py writes NO proposals row when the model call raises
(scripts/scope_queue.py:295-299, read this session: ledger.finish + continue), and the frozen
suite scripts/tests/test_scope_run.py:120 asserts that no row is written (2 rows after a
raise). There is therefore no row to stamp, and adding one here would break a committed oracle.
"http-error" is left out, not faked -- see ## For Claude.

RED ON MAIN: the row dict at scripts/scope_queue.py:301-304 carries neither done_reason nor
parse_status, so every such assertion below fails today. That is the whole point of this file.

THE ORACLE IS WRITTEN HERE; THE IMPLEMENTATION IS DELEGATED. coder.py is given
scripts/scope_queue.py and never this file.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


try:
    import scope_queue
except Exception as e:                                       # noqa: BLE001
    print(f"  [BAD]  cannot import scope_queue: {type(e).__name__}: {e}")
    print("\n0 passed, 1 failed\n")
    sys.exit(1)

run = getattr(scope_queue, "run", None)
if not callable(run):
    print("  [BAD]  scope_queue.run is missing: the contract scripts/tests/test_scope_run.py pins")
    print("\n0 passed, 1 failed\n")
    sys.exit(1)

# Four DISTINCT answers, so a build cannot collapse the four causes into one string.
CLEAN = '{"id": "t01", "verdict": "NOT_DELEGABLE", "why": "needs Jas"}'
CUT = '{"id": "t010", "verdict": "SCO'                # cut mid-value at a token cap
PROSE = ("Let me explore the repository to check for the prerequisite "
         "(cloud pipeline / onboarding) before deciding.")
MALFORMED = '{"id": "t011", "verdict": SCOPED}'       # balanced braces, unquoted value

ONE = [("t01", "one", "body one")]
FOUR = [("t01", "one", "body one"), ("t010", "two", "body two"),
        ("t020", "three", "body three"), ("t011", "four", "body four")]


class NoStore:
    """run() must not touch live task files for a NOT_DELEGABLE verdict. A call is a defect."""

    touched = []

    def add_note(self, *a, **k):
        NoStore.touched.append("add_note")
        raise AssertionError("oracle reached a live task file")

    def set_meta(self, *a, **k):
        NoStore.touched.append("set_meta")
        raise AssertionError("oracle reached a live task file")

    def move(self, *a, **k):
        NoStore.touched.append("move")
        raise AssertionError("oracle reached a live task file")


def paths():
    d = Path(tempfile.mkdtemp())
    return d / "out.jsonl", d / "ledger.jsonl"


def rows(p):
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]


def test_clean_answer_records_the_stop_reason():
    out, led = paths()
    rep = run(ONE, lambda tid, body: (CLEAN, "stop"), out, led, store=NoStore(), run_id="r1")
    rs = rows(out)
    check("one row written", len(rs), 1)
    r = rs[0]
    check("raw is the whole answer", r.get("raw"), CLEAN)
    check("done_reason carries the model's stop-reason", r.get("done_reason", "<absent>"), "stop")
    check("parse_status is ok for a parseable answer", r.get("parse_status", "<absent>"), "ok")
    check("status keeps the verdict outcome", r.get("status"), "ok")
    check("the proposal is still recorded", (r.get("proposal") or {}).get("verdict"), "NOT_DELEGABLE")
    check("the run still reconciles", rep["ok"], True)
    check("and no live task file was touched", NoStore.touched, [])


def test_a_cut_answer_is_reported_as_a_cut():
    out, led = paths()
    run(ONE, lambda tid, body: (CUT, "length"), out, led, store=NoStore(), run_id="r2")
    r = rows(out)[0]
    check("the whole cut answer is kept", r.get("raw"), CUT)
    check("the cut's cause is recorded", r.get("done_reason", "<absent>"), "length")
    check("parse_status says cut, not refused", r.get("parse_status", "<absent>"), "truncated-json")
    check("status keeps what jsonx saw", r.get("status"), "invalid-json")


def test_prose_is_not_reported_as_a_cut():
    out, led = paths()
    run(ONE, lambda tid, body: (PROSE, "stop"), out, led, store=NoStore(), run_id="r3")
    r = rows(out)[0]
    check("prose with no JSON at all", r.get("parse_status", "<absent>"), "no-json-at-all")
    check("and is NOT reported as a cut", r.get("parse_status") == "truncated-json", False)
    check("status keeps what jsonx saw", r.get("status"), "no-json")
    check("raw keeps the whole prose", r.get("raw"), PROSE)


def test_malformed_json_is_not_reported_as_a_cut():
    out, led = paths()
    run(ONE, lambda tid, body: (MALFORMED, "stop"), out, led, store=NoStore(), run_id="r4")
    r = rows(out)[0]
    check("balanced braces, unparseable content", r.get("parse_status", "<absent>"), "malformed-json")
    check("and is NOT reported as a cut", r.get("parse_status") == "truncated-json", False)
    check("status keeps what jsonx saw", r.get("status"), "invalid-json")


def test_a_bare_string_answer_still_works_and_invents_nothing():
    out, led = paths()
    rep = run(ONE, lambda tid, body: CLEAN, out, led, store=NoStore(), run_id="r5")
    r = rows(out)[0]
    check("the frozen contract's string answer is recorded", r.get("raw"), CLEAN)
    check("status is still ok", r.get("status"), "ok")
    check("the done_reason key is present", "done_reason" in r, True)
    check("and no stop-reason is invented for a collector handed none", r.get("done_reason"), None)
    check("the run reconciles", rep["ok"], True)


def test_every_row_of_a_mixed_run_carries_the_two_fields():
    out, led = paths()
    answers = {"t01": (CLEAN, "stop"), "t010": (CUT, "length"),
               "t020": (PROSE, "stop"), "t011": (MALFORMED, "stop")}
    rep = run(FOUR, lambda tid, body: answers[tid], out, led, store=NoStore(), run_id="r6")
    rs = rows(out)
    check("one row per task", len(rs), 4)
    check("every row has done_reason", all("done_reason" in r for r in rs), True)
    check("every row has parse_status", all("parse_status" in r for r in rs), True)
    check("every row keeps its full raw answer",
          sorted(r["raw"] for r in rs), sorted(a for a, _ in answers.values()))
    check("the four causes stay distinguishable", sorted(r["parse_status"] for r in rs),
          ["malformed-json", "no-json-at-all", "ok", "truncated-json"])
    check("provenance is untouched", sorted({r["producer"] for r in rs}), ["scripts/scope_queue.py"])
    check("collector_version is untouched", sorted({r["collector_version"] for r in rs}),
          ["scope_queue-v1"])
    check("the run reconciles", rep["ok"], True)


if __name__ == "__main__":
    for fn in (test_clean_answer_records_the_stop_reason,
               test_a_cut_answer_is_reported_as_a_cut,
               test_prose_is_not_reported_as_a_cut,
               test_malformed_json_is_not_reported_as_a_cut,
               test_a_bare_string_answer_still_works_and_invents_nothing,
               test_every_row_of_a_mixed_run_carries_the_two_fields):
        print(f"\n{fn.__name__}:")
        fn()
    print(f"\n{PASSED} passed, {FAILED} failed\n")
    sys.exit(1 if FAILED else 0)
