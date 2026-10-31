#!/usr/bin/env python3
"""A review verdict expires when the question that produced it changes.

Thirteen compliance chunks sat held for a day on refusals that were CORRECT: the reviewer was
never shown the module its target suite imported, and question 3 called any outside dependency
impossible. Both were fixed on 2026-09-15 -- and the chunks would have stayed held for ever,
because nothing expired a verdict. A refusal is evidence about the brief AND about the question
it was asked.

TWO CLAUSES, deliberately, because one cannot do both jobs:

  * a row DECLARING a prompt_sha other than the current one is stale. Permanent, self-
    maintaining: every future edit to the question expires its own verdicts.
  * a row written before REVIEWER_FIXED is stale. One-time, because rows from before the field
    existed cannot answer the first question.

Treating "no prompt_sha" as stale was tried first and reverted: it silently voids any row
lacking the field, which broke three existing suites and would have made every future fixture
carry it. An unstamped, undated row stays binding -- that case is here so it stays that way.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

SHA = "b" * 64


def main():
    import dispatch_loop as DL
    import preflight_brief as pb
    fails = []

    def ck(label, got, want):
        ok = got == want
        print(f"  [{'ok' if ok else 'BAD'}]    {label} -> {got}")
        if not ok:
            fails.append(f"{label}: expected {want}, got {got}")

    now, old = "2026-09-20T120000Z", "2026-09-15T120000Z"
    cur, other = pb.PROMPT_SHA, "deadbeefdead"

    def row(stopped, at=now, ps=cur):
        o = {"brief_sha256": SHA, "stopped": stopped, "at": at}
        if ps is not None:
            o["prompt_sha"] = ps
        return o

    # --- the permanent mechanism ------------------------------------------------------
    ck("a current objection holds", DL.review_state(SHA, [row(True)]), "objected")
    ck("a current clearance holds", DL.review_state(SHA, [row(False)]), "cleared")
    ck("an objection from a DIFFERENT question expires",
       DL.review_state(SHA, [row(True, ps=other)]), "unreviewed")
    ck("a clearance from a different question expires too -- staleness is not one-directional",
       DL.review_state(SHA, [row(False, ps=other)]), "unreviewed")

    # --- the one-time cutoff ----------------------------------------------------------
    ck("a verdict from before the reviewer was fixed expires",
       DL.review_state(SHA, [row(True, at=old, ps=None)]), "unreviewed")
    ck("a verdict from after it, unstamped, still holds",
       DL.review_state(SHA, [row(True, at=now, ps=None)]), "objected")

    # --- the case that broke three suites ---------------------------------------------
    # A fixture with neither field is what every existing test passes. It MUST stay binding.
    ck("a row with no prompt_sha and no timestamp is binding",
       DL.review_state(SHA, [{"brief_sha256": SHA, "stopped": True}]), "objected")
    ck("and the same when it cleared",
       DL.review_state(SHA, [{"brief_sha256": SHA, "stopped": False}]), "cleared")

    # --- the newest surviving row wins ------------------------------------------------
    ck("a fresh clearance overrides an older objection",
       DL.review_state(SHA, [row(True, at="2026-09-20T100000Z"),
                             row(False, at="2026-09-20T110000Z")]), "cleared")
    ck("an expired clearance does not override a live objection",
       DL.review_state(SHA, [row(True), row(False, ps=other)]), "objected")
    ck("another brief's verdict is not this brief's",
       DL.review_state("c" * 64, [row(True)]), "unreviewed")
    ck("no rows at all is unreviewed", DL.review_state(SHA, []), "unreviewed")

    # --- the live effect, which is the whole point ------------------------------------
    # Every one of the 28 recorded verdicts predates the fix, so nothing is held by them.
    import json
    live = [json.loads(x) for x in
            (REPO / "workspace" / "preflight-objections.jsonl").read_text().splitlines() if x.strip()]
    # This asserted that EVERY recorded verdict predated the cutoff -- true when written and
    # false an hour later once re-reviews began landing. A test that pins today's data fails on
    # tomorrow's; the same mistake as "the repository as it stands passes" in the ratchet suite.
    # What is worth pinning is that the rows can be COMPARED to the cutoff at all.
    ck("the newest rows carry a prompt_sha, so they expire themselves",
       any(o.get("prompt_sha") for o in live), True)
    ck("the cutoff is in preflight_brief's timestamp format (no colons)",
       ":" in DL.REVIEWER_FIXED.split("T")[1], False)

    for x in fails:
        print("FAIL:", x)
    print("test_verdict_expiry:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
