#!/usr/bin/env python3
"""t778 -- every session-review finding carries the part, the turn range and the enclosing
sentence that minted it.

Red on main: session_review has no part_ranges(); extract() and extract_scripted() write finding
rows with no part / from_turn / to_turn / turn keys (the legacy path records the anchor's sentence
only as 'context', and only when it differs from the quote), and the ledger 'finding' event names
only request, key, kind and quote -- so a reader cannot tell which part of a multi-part review
produced a quote, and a fragment carries no antecedent.

Decay fixture, the pair the task names: workspace/rca/31503fe168b0.md and its sibling
b0784ca794ef. A user turn read:
  "we can make something akin to the corpus, for our local models so they can look up lessons and
   learn from past sessions. they can append things the way chat does as well. Then it won't be
   as naive."
and the finding kept only 'they can append things the way chat does as well.' -- a clause whose
referent ('they' = our local models) lives in the preceding clause. The stamps are what let a
reader recover it: part 2 of the split, turns 1-3, and the sentence the anchor computed.

Stdlib only, no model calls, no live state: every output path is redirected into a temp dir.
Run: python3 scripts/tests/test_finding_anchors.py
"""
import json
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review  # noqa: E402

CLAUSE = "they can append things the way chat does as well."
FULL = ("we can make something akin to the corpus, for our local models so they can look up "
        "lessons and learn from past sessions. " + CLAUSE + " Then it won't be as naive.")
WHOLE = "Re-clone env3 before launching the probe."
LONG = "opening turn " + ("x" * 70000)   # one turn longer than CHUNK_CHARS, so chunks() splits here

SAVED = {}


class sandbox:
    """Redirect every path the module writes at a temp dir, and replace every live-state read
    (transcripts, task queue, flow chart, archive cover, skills) with a stub. No real file under
    the repository is opened, written or moved."""

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t778-"))
        for k in ("FINDINGS", "LEDGER", "DONE", "turns", "open_tasks", "done_tasks",
                  "flow_summary", "_archive_cover"):
            SAVED[k] = getattr(session_review, k)
        session_review.FINDINGS = self.tmp / "findings"
        session_review.LEDGER = self.tmp / "ledger.jsonl"
        session_review.DONE = self.tmp / "done"
        session_review.open_tasks = lambda: "t900 fake open task (oracle fixture)"
        session_review.done_tasks = lambda days=14: "(none)"
        session_review.flow_summary = lambda: "TASKS: none\nSUPPORTING SCRIPTS: none"
        session_review._archive_cover = lambda quote: ""
        self.saved_skills = sys.modules.get("skills")
        sys.modules["skills"] = types.SimpleNamespace(
            with_skills=lambda prompt, tag, role=None, can_open=False: (prompt, []))
        return self

    def __exit__(self, *exc):
        for k, v in SAVED.items():
            setattr(session_review, k, v)
        if self.saved_skills is None:
            sys.modules.pop("skills", None)
        else:
            sys.modules["skills"] = self.saved_skills
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False


def ledger_events():
    if not session_review.LEDGER.is_file():
        return []
    return [json.loads(line) for line in
            session_review.LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_part_ranges_gives_every_part_its_turn_range():
    ts = [("user", LONG), ("user", "short one"), ("assistant", "short two")]
    parts = session_review.chunks(ts)
    assert len(parts) == 2, f"fixture: chunks() must split this into 2 parts, got {len(parts)}"
    try:
        ranges = session_review.part_ranges(ts, 0)
    except AttributeError as e:
        raise AssertionError(f"session_review has no part_ranges(): {e}")
    assert len(ranges) == len(parts), f"one range per part, got {ranges} for {len(parts)} parts"
    assert all(b > a for a, b in ranges), f"a part with no turns: {ranges}"
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:])), f"gap or overlap: {ranges}"
    assert ranges[0][0] == 0, f"the first part starts at turn 0, got {ranges[0][0]}"
    assert ranges[-1][1] == len(ts), f"turns left uncovered: {ranges} over {len(ts)} turns"
    off = session_review.part_ranges(ts, 1)
    assert off[0][0] == 1 and off[-1][1] == len(ts), f"from_turn ignored: {off}"


def test_extract_stamps_the_part_that_minted_the_fragment():
    ts = [("user", LONG), ("user", FULL), ("assistant", "noted.")]
    assert len(session_review.chunks(ts)) == 2, "fixture: 2 parts, the clause is in the second"
    answer = json.dumps([{"kind": "deferral", "quote": CLAUSE, "where": "User, late",
                          "why_missed": "no task number", "covered_by": ""}])

    def asker(prompt, purpose, **kw):
        return answer if CLAUSE in prompt else "[]"

    with sandbox() as sb:
        session_review.turns = lambda harness, path: list(ts)
        reqf = sb.tmp / "req.json"
        reqf.write_text(json.dumps({"harness": "claude", "id": "sid-a", "path": "unused.jsonl",
                                    "from_turn": 0, "to_turn": len(ts), "ended_epoch": 1790000000}))
        new, covered = session_review.extract(reqf, asker=asker, note_task=lambda tid, text: None,
                                             file_task=lambda title, body: "t999")
        assert (new, covered) == (1, 0), f"one finding, nothing covered: got {(new, covered)}"
        f = session_review.FINDINGS / (session_review._fkey("req.json", CLAUSE) + ".json")
        assert f.is_file(), "no finding file was written"
        row = json.loads(f.read_text(encoding="utf-8"))
        assert row["quote"] == CLAUSE, f"the model's words must stay the quote: {row['quote']!r}"
        assert row.get("part") == 2, f"the clause is in part 2: got part {row.get('part')!r}"
        assert row.get("from_turn") == 1, f"part 2 starts at turn 1: got {row.get('from_turn')!r}"
        assert row.get("to_turn") == 3, f"part 2 ends at turn 3: got {row.get('to_turn')!r}"
        assert row.get("turn") == 1, f"the quote sits in turn 1: got {row.get('turn')!r}"
        want, found = session_review._anchor(CLAUSE, "### USER\n" + FULL + "\n")
        assert found, "fixture: the clause must anchor in the user turn"
        assert row.get("sentence") == want, (
            f"the record must carry the enclosing sentence: got {row.get('sentence')!r}, want {want!r}")
        assert row.get("sentence") != row["quote"], (
            "a fragment must be recorded WITH its sentence, not alone")
        finding = [e for e in ledger_events() if e.get("event") == "finding"]
        assert len(finding) == 1, f"one finding event: got {len(finding)}"
        assert finding[0].get("part") == 2, f"ledger event has no part: {finding[0]}"
        assert finding[0].get("from_turn") == 1 and finding[0].get("to_turn") == 3, (
            f"ledger event has no turn range: {finding[0]}")
        done = sb.tmp / "done" / "req.json"
        assert done.is_file(), "the request was not moved to done/"
        assert json.loads(done.read_text(encoding="utf-8")).get("parts_done") == 2, (
            "the resume counter broke")


def test_extract_records_the_sentence_even_for_a_whole_sentence_quote():
    ts = [("user", WHOLE), ("assistant", "ok, re-cloning now.")]
    answer = json.dumps([{"kind": "deferral", "quote": WHOLE, "where": "Assistant, early",
                          "why_missed": "no task number", "covered_by": ""}])

    def asker(prompt, purpose, **kw):
        return answer if WHOLE in prompt else "[]"

    with sandbox() as sb:
        session_review.turns = lambda harness, path: list(ts)
        reqf = sb.tmp / "req.json"
        reqf.write_text(json.dumps({"harness": "claude", "id": "sid-b", "path": "unused.jsonl",
                                    "from_turn": 0, "to_turn": len(ts), "ended_epoch": 1790000000}))
        new, covered = session_review.extract(reqf, asker=asker, note_task=lambda tid, text: None,
                                             file_task=lambda title, body: "t999")
        assert (new, covered) == (1, 0), f"one finding: got {(new, covered)}"
        f = session_review.FINDINGS / (session_review._fkey("req.json", WHOLE) + ".json")
        assert f.is_file(), "no finding file was written"
        row = json.loads(f.read_text(encoding="utf-8"))
        assert row.get("sentence") == WHOLE, (
            f"the sentence is recorded unconditionally, even when it is the quote: {row.get('sentence')!r}")
        assert row.get("part") == 1, f"single-part review is part 1: got {row.get('part')!r}"
        assert row.get("from_turn") == 0 and row.get("to_turn") == 2, (
            f"turn range: got {row.get('from_turn')!r}-{row.get('to_turn')!r}")
        assert row.get("turn") == 0, f"the quote is in turn 0: got {row.get('turn')!r}"


def test_extract_scripted_stamps_the_part_and_the_turn_range():
    ts = [("user", f"turn {i}") for i in range(8)]
    item = {"n": 1, "quote": "I'll re-clone env3 before launching the probe.",
            "context": "before text after text", "type": "PROMISED", "turn": 1,
            "repeats": [], "near": [], "commits": [], "offered": []}

    def items(turns_, since, as_of=""):
        return [dict(item)]

    def triage(items_, asker, harness, sid, since, purpose="session review: triage"):
        return [dict(items_[0], verdict="missed", kind="deferral", what="re-clone env3", cover="")]

    with sandbox() as sb:
        session_review.turns = lambda harness, path: list(ts)
        session_review.scripted_items = items
        session_review.triage = triage
        reqf = sb.tmp / "req.json"
        reqf.write_text(json.dumps({"harness": "claude", "id": "sid-c", "path": "unused.jsonl",
                                    "from_turn": 5, "to_turn": 8, "ended_epoch": 1790000000}))
        new, covered = session_review.extract_scripted(reqf, asker=lambda prompt, why: "",
                                                      note_task=lambda tid, text: None,
                                                      file_task=lambda title, body: "t999")
        assert (new, covered) == (1, 0), f"one finding: got {(new, covered)}"
        f = session_review.FINDINGS / (session_review._fkey("req.json", item["quote"]) + ".json")
        assert f.is_file(), "no finding file was written by extract_scripted"
        row = json.loads(f.read_text(encoding="utf-8"))
        assert row.get("part") == 1, f"the scripted pass is one pass, part 1: got {row.get('part')!r}"
        assert row.get("from_turn") == 5, f"absolute start turn: got {row.get('from_turn')!r}"
        assert row.get("to_turn") == 8, f"absolute end turn: got {row.get('to_turn')!r}"
        assert row.get("turn") == 1, f"the item's own turn number is on the row: got {row.get('turn')!r}"
        assert "turn 1 of 3" in str(row.get("where", "")), (
            f"the locator prose broke: {row.get('where')!r}")
        finding = [e for e in ledger_events() if e.get("event") == "finding"]
        assert finding and finding[0].get("part") == 1 and finding[0].get("to_turn") == 8, (
            f"ledger event carries no part/turn range: {finding}")


FAILS = []


def run(name, fn):
    try:
        fn()
        print(f"  ok    {name}")
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        FAILS.append(name)


if __name__ == "__main__":
    for name in sorted([k for k, v in globals().items() if k.startswith("test_") and callable(v)]):
        run(name, globals()[name])
    print(f"test_finding_anchors: {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
