#!/usr/bin/env python3
"""t1575: the RCA prompt must not carry a finding's bulk diagnostic list.

Finding 502bf4c839aa's RCA came back EMPTY twice. workspace/session-reviews/failed/502bf4c839aa.json
records both attempts as the empty-reply branch of scripts/session_review.py:1589 ("no answer
(timed out or empty)"), not the "no JSON list in the answer" branch -- so the model never returned a
body to parse. The record on disk is 10,175 bytes and most of it is a `candidates_consulted` list of
~900 task ids, and rca() hands the model the WHOLE record: `json.dumps(f, indent=1)` at
scripts/session_review.py:1578. Unlike extract() (which splits a session into parts, :522/:530), the
RCA is ONE call at a 24000-token budget (:1584) -- the shared-num_predict ceiling of
docs/skills/briefing-the-local-model, `done_reason=length` with the content empty.

So: bound it, LOUDLY. never-truncate-a-record -- a value handed to a model may be bounded, and the
bound must say it happened, say how many entries are missing, and name where the whole list lives.
An unannounced cut is worse than the giant prompt: the model concludes the missing ids do not exist.
The RCA reasons over the quote, the kind, the where and the open tasks -- not over the 900 ids a
cover search happened to look at.
"""
import json
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import session_review  # noqa: E402

KEY = "502bf4c839aa"


def record(n_ids=40):
    """A finding shaped like workspace/session-reviews/failed/502bf4c839aa.json."""
    return {
        "key": KEY,
        "kind": "deferral",
        "quote": "If you want, I'll run a longer generation through it to measure real tok/s "
                 "at full context -- say the word.",
        "where": "assistant turn 259 of 487",
        "request": "dsh-session-65ba3e9b-3a87-4198-bb76-da895a4be50c.json",
        "session": "65ba3e9b-3a87-4198-bb76-da895a4be50c",
        "harness": "dsh",
        "found": "2026-10-24T16:30:16Z",
        "covered_by": "",
        "overruled": "unproven cover t1006: shares 0.12 of the item's words (< 0.2)",
        "candidates_consulted": [f"t{1000 + i}" for i in range(n_ids)],
    }


def test_a_bulk_list_is_bounded_and_says_so():
    rec = record(40)
    text = session_review.rca_finding_payload(
        rec, path="workspace/session-reviews/failed/%s.json" % KEY)
    data = json.loads(text)
    kept = data["candidates_consulted"]
    assert isinstance(kept, list), f"the field must stay a list, got {type(kept).__name__}"
    assert 0 < len(kept) < 40, f"a bulk list must be bounded, not dropped: {len(kept)} entries"
    assert kept[0] == "t1000" and kept[1] == "t1001", f"keep from the front, in order: {kept[:3]!r}"
    tail = kept[-1]
    assert isinstance(tail, str) and "TRUNCATED" in tail, f"the cut must announce itself: {tail!r}"
    assert "40" in tail, f"it must say how many entries the model is not seeing: {tail!r}"
    assert ("workspace/session-reviews/failed/%s.json" % KEY) in tail, \
        f"it must say where the whole list lives: {tail!r}"
    for dropped in rec["candidates_consulted"][len(kept) - 1:]:
        assert dropped not in text, f"{dropped} is past the bound and must not ride in the prompt"


def test_the_fields_the_rca_reasons_over_survive_whole():
    rec = record(40)
    text = session_review.rca_finding_payload(rec)
    data = json.loads(text)
    assert data["quote"] == rec["quote"], "the quote IS the finding: it cannot be clipped"
    for field in ("key", "kind", "where", "request", "session", "harness", "found", "overruled"):
        assert data.get(field) == rec[field], f"{field} must reach the RCA unchanged: {data.get(field)!r}"
    assert data.get("covered_by") == "", "an empty cover pointer is evidence; keep it"
    assert len(text) < len(json.dumps(rec, indent=1)), "the point of the bound is a smaller prompt"


def test_a_small_list_is_passed_through_whole():
    rec = record(3)
    data = json.loads(session_review.rca_finding_payload(rec))
    assert data["candidates_consulted"] == ["t1000", "t1001", "t1002"], \
        f"a list at the bound is not a truncation: {data['candidates_consulted']!r}"
    assert "TRUNCATED" not in json.dumps(data), "no bound fired, so no bound may be announced"


def test_the_caller_s_record_is_not_mutated():
    rec = record(40)
    before = json.dumps(rec, sort_keys=True)
    session_review.rca_finding_payload(rec)
    assert json.dumps(rec, sort_keys=True) == before, \
        "render a copy: the record on disk is the RCA's evidence"


def test_rca_hands_the_model_the_bounded_payload():
    """rca() end-to-end in a temp dir: the prompt the model is given is bounded, and the
    report + give-up behaviour are unchanged. Nothing outside the temp dir is written."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "findings").mkdir()
        (root / "rca").mkdir()
        finding = root / "findings" / (KEY + ".json")
        finding.write_text(json.dumps(record(40), indent=1) + "\n", encoding="utf-8")
        skill = root / "SKILL.md"
        skill.write_text("# root-cause-analysis\n\nAsk why five times.\n", encoding="utf-8")
        seen = {}

        def fake_asker(prompt_text, purpose, budget, **kw):
            seen["prompt"] = prompt_text
            seen["purpose"] = purpose
            seen["budget"] = budget
            return "[]"

        saved = (session_review.FINDINGS, session_review.RCA_DIR, session_review.LEDGER,
                 session_review.SKILL)
        sys.modules["skills"] = types.SimpleNamespace(
            with_skills=lambda text, kind, can_open=False, already=None: (text, []))
        session_review.FINDINGS = root / "findings"
        session_review.RCA_DIR = root / "rca"
        session_review.LEDGER = root / "ledger.jsonl"
        session_review.SKILL = skill
        try:
            ids = session_review.rca(finding, asker=fake_asker)
        finally:
            (session_review.FINDINGS, session_review.RCA_DIR, session_review.LEDGER,
             session_review.SKILL) = saved
            sys.modules.pop("skills", None)
        prompt = seen.get("prompt", "")
        assert "TRUNCATED" in prompt, \
            "rca() must build its prompt from the bounded payload; on main it dumps the whole record"
        assert "measure real tok/s" in prompt, "the finding's quote must still reach the model"
        assert "t1000" in prompt, "the kept ids must still be there for a cover search to cite"
        assert "t1039" not in prompt, "an id past the bound must not be in the prompt at all"
        assert (root / "rca" / (KEY + ".md")).is_file(), "the RCA report is still written"
        assert ids == [], "an empty JSON list files no task: unchanged behaviour"
        assert seen.get("budget") == 24000, "this fix bounds the PROMPT, not the RCA's output budget"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
