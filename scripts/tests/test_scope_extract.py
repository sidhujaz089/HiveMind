#!/usr/bin/env python3
"""The contract for reading a model's answer out of scope_queue.py.

WHY THIS SUITE EXISTS, AND WHY IT WAS WRITTEN BEFORE THE FIX

On 2026-09-06 the scoping pass reported 8 unreadable answers out of 35 and the obvious
reading -- the model produced malformed output -- was wrong twice over:

  * `row["parse_error"] = m.group(0)[:400]` kept the first 400 characters and discarded the
    rest, so five of the eight bad rows are EXACTLY 400 bytes. The evidence needed to
    diagnose the failure was destroyed by the thing reporting the failure.
  * `re.search(r"\\{.*\\}", answer, re.S)` is greedy. Measured against the shapes a model
    actually produces, it fails on three of four: prose-then-JSON, two objects, and
    JSON-then-prose. Only a bare object survives.

So the run was not measuring the model. This suite pins the contract so that cannot recur.

THE ORACLE IS WRITTEN HERE AND THE FIX IS DELEGATED, DELIBERATELY. Nine outside reviewers
converged on the same warning: an agent judged by a suite it can edit will edit the suite,
and a verifier that is the same entity as the thing verified proves nothing. This file is the
verifier. `coder.py` gets the file under test and not this one.

AMBIGUITY IS A SIGNAL, NOT A NUISANCE. An earlier draft of the fix said "if several objects,
take the last". That is dangerous -- the last may be a summary, a repetition, or a different
schema. Two schema-valid answers means the model gave two verdicts, and guessing which one it
meant is exactly the class of silent wrongness this project keeps finding.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


OK   = '{"id": "t1", "verdict": "SCOPED", "file": "a.py"}'
OK2  = '{"id": "t1", "verdict": "NEEDS_JAS", "question": "which?"}'
NOTA = '{"note": "not a verdict at all"}'

CASES = [
    # (label, raw answer, expected status, expected verdict or None)
    ("a bare object parses",              OK,                                  "ok", "SCOPED"),
    ("prose before the object",           f"Let me think {{about it}}.\n{OK}", "ok", "SCOPED"),
    ("prose after the object",            f"{OK}\nNote: {{check}} later.",     "ok", "SCOPED"),
    ("a fenced json block",               f"Here:\n```json\n{OK}\n```\ndone",  "ok", "SCOPED"),
    ("an object that is not a verdict",   NOTA,                            "no-verdict", None),
    ("two CONFLICTING verdicts is AMBIGUOUS, never a guess", f"{OK}\n{OK2}", "ambiguous", None),
    ("one verdict beside one non-verdict resolves", f"{NOTA}\n{OK}",          "ok", "SCOPED"),

    # THE 2026-09-07 SPEC DEFECT, pinned so it cannot come back. "Two matches means never
    # guess" was wrong, and the running measurement proved it: 5 of 27 rows came back
    # `ambiguous` and ALL FIVE were the same verdict repeated -- t084 fifteen times. There
    # was nothing to guess between. Ambiguity is about CONFLICT, not COUNT.
    ("two AGREEING verdicts resolve -- there is nothing to guess between",
     f"{OK}\n{OK}",                                                            "ok", "SCOPED"),
    ("the t084 shape: the same verdict fifteen times is one answer, not fifteen",
     "\n".join([OK] * 15),                                                     "ok", "SCOPED"),
    ("agreement is on the VERDICT, not on byte-identical text",
     '{"id": "t1", "verdict": "SCOPED", "file": "a.py"}\n'
     '{"verdict": "SCOPED", "id": "t1", "file": "a.py"}',                      "ok", "SCOPED"),

    # A brace inside a string is not a brace. `{"text": "hello } world"}` is one object, and
    # a scanner that counts braces without tracking strings cuts it in half.
    ("a closing brace inside a string does not end the object",
     '{"id": "t1", "verdict": "SCOPED", "note": "use } carefully"}',           "ok", "SCOPED"),
    ("truncated json is invalid, not ok", '{"id": "t1", "verdict": "SCO',  "invalid-json", None),
    ("no json at all",                    "I need to explore the repository first.", "no-json", None),
    ("empty output",                      "",                                "no-json", None),
]


def main():
    print("The extractor's contract:")
    try:
        import scope_queue
    except Exception as e:                                   # noqa: BLE001
        print(f"  [BAD]  cannot import scope_queue: {type(e).__name__}: {e}")
        print("\n0 passed, 1 failed\n")
        return 1

    fn = getattr(scope_queue, "extract_proposal", None)
    if not callable(fn):
        global FAILED
        FAILED += 1
        print("  [BAD]  scope_queue.extract_proposal(answer) does not exist.")
        print("         Required: a PURE function taking the raw answer text and returning")
        print("           {'status': one of ok|ambiguous|no-verdict|invalid-json|no-json,")
        print("            'proposal': the object when status is ok,")
        print("            'candidates': how many schema-valid objects were seen}")
        print("         A verdict object is one carrying both 'id' and 'verdict'.")
    else:
        for label, raw, want_status, want_verdict in CASES:
            try:
                got = fn(raw)
            except Exception as e:                           # noqa: BLE001
                check(label, f"RAISED {type(e).__name__}", want_status)
                continue
            check(label, (got or {}).get("status"), want_status)
            if want_verdict is not None:
                check(f"  and it carries the verdict ({label})",
                      ((got or {}).get("proposal") or {}).get("verdict"), want_verdict)

    # THE TRUNCATION GUARD. The extractor being right is only half of it: the recorder must
    # stop destroying the evidence. A fixed-width slice of the model's answer anywhere in
    # this file is the bug that made the other eight rows undiagnosable.
    print("\nThe recorder must not destroy its own evidence:")
    src = (ROOT / "scripts" / "scope_queue.py").read_text(encoding="utf-8")
    slices = re.findall(r"(?:answer|group\(0\))\s*\[\s*:\s*\d+\s*\]", src)
    check("no fixed-width slice of the model's answer is stored", slices, [])
    check("the full raw answer is kept somewhere",
          bool(re.search(r"\braw\b", src)), True)

    # THE FORK GUARD, added 2026-09-08. scope_queue.py was repaired on 2026-09-07 by giving
    # it its OWN corrected scanner, and scripts/jsonx.py was built the same day to be the one
    # shared implementation -- so the repository ended the day with TWO, already drifting:
    # jsonx had the corrected agreement rule and zero importers, while the copy that actually
    # ran still said "more than one match means ambiguous". That gap is the whole difference
    # between the 23 the pipeline produced and the 28 the module could have.
    #
    # Z AI predicted this exact fork from the prose alone: "Does scope_queue.py import the
    # shared module, or carry its own repaired copy? If the latter, you have two
    # implementations already drifting." It did. So: import it, do not re-implement it.
    print("\nThere must be ONE extractor, and this file must not be the second:")
    check("scope_queue.py imports jsonx", bool(re.search(r"^\s*import jsonx\b|^\s*from jsonx\b",
                                                         src, re.M)), True)
    check("and does not carry its own brace scanner",
          bool(re.search(r"depth\s*[-+]?=\s*1|in_string", src)), False)
    check("and no greedy extractor survives anywhere in it",
          re.findall(r"re\.search\(\s*r?[\"']\\\{\.\*\\\}", src), [])

    print(f"\n{PASSED} passed, {FAILED} failed\n")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
