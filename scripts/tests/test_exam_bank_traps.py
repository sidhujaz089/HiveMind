#!/usr/bin/env python3
"""A CANNOT-DETERMINE item must not show the reader the line that decides it.

WHY THIS EXISTS
---------------
The 8 traps exist to catch a candidate that bluffs: the excerpt is supposed to contain too
little to judge, so the honest answer is determinable=false. That only holds if the DECISIVE
LINE -- the line the mutation actually changed -- is outside the excerpt. If it is inside,
the item is answerable, and a candidate marked down for answering it is being penalised for
our authoring mistake.

Proposed by Q16 on 2026-09-03 when asked why two judges claimed determinable on three traps:

    "Add a meta-check to the harness: for every CANNOT-DETERMINE item, verify that the stated
     defect's locus is NOT in the excerpt. If it is, the item is mislabelled by construction."

Every item already carries `decisive_line` and `decisive_index`, so the check is exact rather
than a judgement call. Run against the current bank all 8 pass -- the excerpts begin exactly
two lines AFTER the decisive line in every case. That makes this a regression guard for the
next bank build, and it also settles the question it was written for: the three contested
items were judge scope errors, not bank defects.

The mirror check matters as much. A DEFECT or NOT-A-DEFECT item whose decisive line is NOT
shown is unanswerable, and marking a candidate wrong for abstaining on it is the same
unfairness pointing the other way.
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BANK = REPO / "workspace" / "exams" / "bank-senior-endurance.json"


def unnumbered(body):
    """The excerpt with its `  90| ` gutter stripped, which is what the model actually reads."""
    return "\n".join(re.sub(r"^\s*\d+\|\s?", "", l) for l in body.splitlines())


def main():
    if not BANK.is_file():
        print(f"FAIL: {BANK} is missing")
        return 1
    bank = json.loads(BANK.read_text())
    fails, counted = [], {"CANNOT-DETERMINE": 0, "answerable": 0}

    print("Traps -- the decisive line must be OUTSIDE the excerpt:")
    for it in bank["items"]:
        if it["answer"] != "CANNOT-DETERMINE":
            continue
        counted["CANNOT-DETERMINE"] += 1
        dl = (it.get("decisive_line") or "").strip()
        if not dl:
            fails.append(f"{it['id']} has no decisive_line, so nothing can be checked")
            print(f"  [BAD]   {it['id']}: no decisive_line")
            continue
        shown = dl in unnumbered(it["body"])
        print(f"  [{'BAD' if shown else 'ok'}]    {it['id']}  line {it.get('decisive_index')}"
              f"  {'IS SHOWN' if shown else 'not shown'}  {dl[:52]}")
        if shown:
            fails.append(f"{it['id']} is labelled CANNOT-DETERMINE but its decisive line "
                         f"({it.get('decisive_index')}) is inside the excerpt. It is "
                         f"answerable, and a candidate penalised for answering it is being "
                         f"marked down for our mistake.")

    print("\nAnswerable items -- the decisive line must be INSIDE the excerpt:")
    for it in bank["items"]:
        if it["answer"] == "CANNOT-DETERMINE":
            continue
        counted["answerable"] += 1
        dl = (it.get("decisive_line") or "").strip()
        if dl and dl not in unnumbered(it["body"]):
            print(f"  [BAD]   {it['id']} ({it['answer']}): decisive line "
                  f"{it.get('decisive_index')} is NOT shown -- {dl[:48]}")
            fails.append(f"{it['id']} is labelled {it['answer']} but its decisive line is "
                         f"outside the excerpt, so abstaining is the honest answer and the "
                         f"bank would mark that wrong")
    print(f"  {counted['answerable']} answerable item(s) checked")

    # THE NEGATIVE CONTROL. A checker that has only ever passed cannot be told from one that
    # cannot fail (docs/CHECKS.md, rule 1). Build the mistake and confirm it is caught.
    trap = {"answer": "CANNOT-DETERMINE", "decisive_line": "    if now is None:",
            "body": "# f.py\n\n   88|     if now is None:\n   89|         gone.append(p)\n"}
    caught = trap["decisive_line"].strip() in unnumbered(trap["body"])
    print(f"\n  [{'ok' if caught else 'BAD'}]    control: a trap that leaks its decisive line "
          f"is caught -> {caught}")
    if not caught:
        fails.append("the gutter-stripping detector does not catch a leaked decisive line, "
                     "so every pass above is meaningless")

    if counted["CANNOT-DETERMINE"] == 0:
        fails.append("no CANNOT-DETERMINE items found -- this check just stopped checking")

    for x in fails:
        print("FAIL:", x)
    print("test_exam_bank_traps:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
