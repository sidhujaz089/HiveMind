"""t1416: an RCA that returns nothing must be recorded WITH A CAUSE.

Finding ccea772bc7b7 (workspace/session-reviews/failed/ccea772bc7b7.json) carries two attempts
recorded "no answer (timed out or empty)", 2026-10-18T04:12:20Z and 04:12:39Z -- 19 seconds
apart, against RCA_LIMIT_S = 9000 (scripts/session_review.py:300). 19 s proves the call did NOT
time out: it came back EMPTY. The record says neither, and the two causes have opposite fixes --
raise RCA_LIMIT_S vs. re-ask with reasoning OFF. This suite pins the cause string that replaces
the symptom, and checks rca() writes it onto the finding.

Run: python3 scripts/tests/test_session_review_rca_cause.py
"""
import json
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import session_review as sr

SYMPTOM = "no answer (timed out or empty)"


def _low(s):
    return str(s).lower().replace("'", "").replace("\u2019", "")


def test_helper_exists():
    assert hasattr(sr, "rca_unusable_reason"), (
        "missing symbol rca_unusable_reason (new) in scripts/session_review.py -- rca() must "
        "record the CAUSE of an unusable answer, not the symptom (t1416)")


def test_empty_and_fast_is_not_a_timeout():
    r = sr.rca_unusable_reason("", 24000, 9000, elapsed_s=19.0)
    assert isinstance(r, str) and r.strip(), f"helper returned {r!r}"
    assert SYMPTOM not in r, f"the symptom survived as the cause: {r!r}"
    assert "not a timeout" in _low(r), f"19 s of a 9000 s limit is proof it did NOT time out: {r!r}"
    assert "19" in r, f"the elapsed time is the evidence; it belongs in the record: {r!r}"
    assert "9000" in r, f"the limit is half that proof; record it too: {r!r}"


def test_empty_and_fast_points_at_the_output_ceiling_and_its_action():
    r = _low(sr.rca_unusable_reason("", 24000, 9000, elapsed_s=19.0))
    assert ("output ceiling" in r) or ("done_reason=length" in r) or ("num_predict" in r), (
        f"an empty answer that did not time out is the shared-budget class; name it: {r!r}")
    assert "reasoning" in r, f"the action is to re-ask ONCE with reasoning OFF: {r!r}"
    assert "24000" in r, f"the token budget is the number to act on: {r!r}"


def test_spending_the_whole_limit_is_the_timeout_and_names_the_flag():
    r = sr.rca_unusable_reason("", 24000, 9000, elapsed_s=9000.0)
    low = _low(r)
    assert "timed out" in low, f"9000 s of 9000 is a timeout and must say so: {r!r}"
    assert "not a timeout" not in low, f"a real timeout must not read as the empty case: {r!r}"
    assert "limit_s" in r, f"the record must name the flag to raise: {r!r}"
    assert SYMPTOM not in r, f"the symptom must stop being a recorded cause: {r!r}"


def test_a_reply_with_no_json_list_is_its_own_cause():
    r = sr.rca_unusable_reason("I will look into the batching question.", 24000, 9000, elapsed_s=60.0)
    low = _low(r)
    assert "json" in low, f"a reply with no JSON list is its own cause, not 'no answer': {r!r}"
    assert "kept" in low, f"the record must say the finding is kept, not lost: {r!r}"
    assert "39" in r, f"the answer's length is the evidence it said something: {r!r}"
    assert SYMPTOM not in r


def test_rca_records_the_cause_on_the_finding():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = tmp / "sr"
        (base / "failed").mkdir(parents=True)
        (base / "findings" / "done").mkdir(parents=True)
        finding = base / "findings" / "abc123def456.json"
        finding.write_text(json.dumps({
            "key": "abc123def456", "kind": "unresolved",
            "quote": "The trigger now fires on the backlog you see.",
            "where": "assistant, turn 24 of 103", "request": "x.json",
            "session": "x", "harness": "claude", "found": "2026-10-18T03:44:45Z",
        }), encoding="utf-8")
        skill = tmp / "SKILL.md"
        skill.write_text("RCA skill stub.\n", encoding="utf-8")
        try:
            sr.RCA.format(request="x", finding="{}", skill="")
        except KeyError as e:
            raise AssertionError(
                "the RCA template (session_review.py:1382) needs a " + str(e) + " value; this "
                "oracle supplies request/finding/skill only -- report the template, do not "
                "edit this test")

        logged = []
        saved = (sr.BASE, sr.FAILED, sr.FINDINGS, sr.LEDGER, sr.RCA_DIR, sr.SKILL, sr._log)
        fake = types.ModuleType("skills")
        fake.with_skills = lambda p, kind, role=None, can_open=False, already=None: (p, [])
        saved_skills = sys.modules.get("skills")
        sys.modules["skills"] = fake
        try:
            sr.BASE, sr.FAILED, sr.FINDINGS = base, base / "failed", base / "findings"
            sr.LEDGER, sr.RCA_DIR, sr.SKILL = tmp / "ledger.jsonl", tmp / "rca", skill
            sr._log = lambda row: logged.append(row)

            def stub_asker(prompt, purpose, budget, **kw):
                assert "abc123def456" in prompt, "the RCA prompt must carry the finding key"
                assert budget == 24000, f"the RCA budget is 24000 (session_review.py:1523): {budget!r}"
                return ""

            ids = sr.rca(finding, asker=stub_asker)
        finally:
            sys.modules["skills"] = saved_skills if saved_skills else None
            (sr.BASE, sr.FAILED, sr.FINDINGS, sr.LEDGER, sr.RCA_DIR, sr.SKILL, sr._log) = saved

        assert ids == [], f"an unusable RCA files no task; got {ids!r}"
        assert finding.is_file(), "one unusable attempt keeps the finding queued; it must not be deleted"
        rec = json.loads(finding.read_text(encoding="utf-8"))
        rows = rec.get("unusable") or []
        assert rows, f"the attempt was not recorded on the finding: {rec!r}"
        why = str(rows[-1].get("why", ""))
        assert SYMPTOM not in why, f"rca() recorded the symptom again, with no cause: {why!r}"
        assert "not a timeout" in _low(why), (
            f"a near-instant empty answer is the output-ceiling class, not a timeout: {why!r}")
        assert any(r.get("event") == "rca-unusable" for r in logged), (
            f"the rca-unusable ledger row must still be written: {logged!r}")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = []
    for fn in fns:
        try:
            fn()
            print("PASS " + fn.__name__)
        except Exception as e:
            failed.append(fn.__name__)
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"{len(fns) - len(failed)}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
