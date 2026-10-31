#!/usr/bin/env python3
"""Everyone asked to judge an excerpt is asked the SAME thing, and it must not contradict the labels.

TWO FAILURES THIS PREVENTS, BOTH REAL.

1. TWO COPIES. `verify_control.py` carried its own hardcoded prompt while the bank shipped an
   `instructions` field nothing read. The bank's copy was edited to fix a framing problem and
   the re-run came back identical to the decisecond -- the wrong file had been fixed, and
   nobody could tell, because the run still produced numbers.

2. THE COPY THAT DISAGREED WITH THE ANSWER KEY. The instructions told a candidate:
   "If a function's job, as stated in the excerpt, is done correctly by the excerpt, that is
   NOT-A-DEFECT even if you cannot see every caller." The 8 CANNOT-DETERMINE traps are built
   by omitting the decisive line, so for those items that sentence mandates the WRONG answer.
   A candidate following the instructions was marked down for it, and two judges did exactly
   that on i24 and i26 before anyone noticed the instructions were the cause.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BANK = REPO / "workspace" / "exams" / "bank-senior-endurance.json"
VC = REPO / "scripts" / "verify_control.py"


def main():
    fails = []
    bank = json.loads(BANK.read_text())
    ins = bank["instructions"]

    # 1. NO SECOND COPY. verify_control must derive its prompt from the bank, not restate it.
    src = VC.read_text()
    derives = 'BANK["instructions"]' in src
    print(f"  [{'ok' if derives else 'BAD'}]    verify_control.py builds its prompt FROM the "
          f"bank's instructions")
    if not derives:
        fails.append("verify_control.py does not derive its prompt from the bank -- that is "
                     "the second copy that already cost a run")

    # 2. THE INSTRUCTIONS MUST NOT MANDATE THE WRONG ANSWER ON A TRAP. The banned sentence is
    #    checked by its distinguishing clause rather than verbatim, so a reworded revival is
    #    caught too.
    banned = ("even if you cannot see every caller",
              "not for ordinary correct code that merely lacks full external context")
    for phrase in banned:
        present = phrase.lower() in " ".join(ins.lower().split())
        print(f"  [{'BAD' if present else 'ok'}]    instructions do not say "
              f"{phrase!r}")
        if present:
            fails.append(f"the instructions still contain {phrase!r}, which tells a candidate "
                         f"to answer NOT-A-DEFECT on exactly the excerpts the traps label "
                         f"CANNOT-DETERMINE")

    # 3. AND THEY MUST STATE THE RULE THE TRAPS ACTUALLY TEST.
    needed = ["point to the line", "cannot-determine"]
    for phrase in needed:
        present = phrase in " ".join(ins.lower().split())
        print(f"  [{'ok' if present else 'BAD'}]    instructions state the scope rule "
              f"({phrase!r})")
        if not present:
            fails.append(f"the instructions never say {phrase!r} -- a candidate is not told "
                         f"the standard the traps hold it to")

    # 4. THE LOCK STILL MATCHES. Editing instructions without re-locking leaves a bank whose
    #    own hash says it was tampered with.
    sys.path.insert(0, str(REPO / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("eb", REPO / "scripts" / "exam_bank.py")
    eb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eb)
    problems, _h = eb.verify()
    print(f"  [{'ok' if not problems else 'BAD'}]    the bank's hash matches its contents")
    fails.extend(problems)

    # NEGATIVE CONTROL: the banned-phrase detector must actually fire.
    probe = "blah blah NOT-A-DEFECT even if you cannot see every caller. blah"
    caught = banned[0] in probe
    print(f"  [{'ok' if caught else 'BAD'}]    control: the banned sentence is detected")
    if not caught:
        fails.append("the banned-phrase detector does not detect the banned phrase")

    for x in fails:
        print("FAIL:", x)
    print("test_exam_one_question:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
