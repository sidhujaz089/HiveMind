#!/usr/bin/env python3
"""The script reviewer cites a line NUMBER; the code is read off disk, never taken from the model.

On 2026-09-15 the reviewer was asked for "the EXACT code on that line" as its own proof against
hallucination. Escaping a line of Python that already held quotes and backslashes broke its JSON
40 times on one file, identically, for 7.7 hours. The proof cost more than it proved.

So the guarantee changed shape, and this pins the new one: whatever the model says the code is,
the report shows the line that is actually in the file.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

REL = "scripts/truncation_review.py"          # a real tracked file; this test writes nothing


def reply(findings):
    """An ask_fn that answers with exactly these findings and a normal stop."""
    return lambda _prompt: (json.dumps({"findings": findings}), "stop")


def main():
    import truncation_review as tr
    fails = []
    lines = (REPO / REL).read_text(encoding="utf-8").splitlines()

    # 1. THE CODE COMES FROM THE FILE. The model sends a line number and no code at all.
    r = tr.review(REL, ask_fn=reply([{"check": "truncation", "line": 1, "silent": True,
                                      "severity": "low", "why": "fixture"}]))
    f = (r["findings"] or [{}])[0]
    ok = r["status"] == "ok" and f.get("code") == lines[0].strip() and f.get("verified") is True
    print(f"  [{'ok' if ok else 'BAD'}]    a line number alone yields the real source line "
          f"({f.get('code')!r})")
    if not ok:
        fails.append(f"a finding citing line 1 did not come back with line 1 of the file: {f}")

    # 2. A MODEL'S OWN `code` IS NOT TRUSTED, EVEN WHEN IT SENDS ONE. This is the case that
    #    fails the moment anyone restores the old "believe the model's quote" behaviour.
    lie = "THIS STRING IS DEFINITELY NOT ANYWHERE IN THE FILE"
    r = tr.review(REL, ask_fn=reply([{"check": "truncation", "line": 1, "code": lie,
                                      "silent": True, "severity": "high", "why": "fixture"}]))
    f = (r["findings"] or [{}])[0]
    ok = f.get("code") == lines[0].strip() and lie not in str(f.get("code"))
    print(f"  [{'ok' if ok else 'BAD'}]    a hallucinated `code` is overwritten from disk")
    if not ok:
        fails.append("the model's own `code` survived into the finding -- a hallucinated site "
                     f"can reach the report again: {f}")

    # 3. A LINE OUT OF RANGE IS KEPT BUT UNVERIFIED. Dropping it would hide that the model is
    #    citing lines that do not exist; trusting it would print an empty quote as evidence.
    r = tr.review(REL, ask_fn=reply([{"check": "truncation", "line": len(lines) + 5000,
                                      "silent": True, "severity": "low", "why": "fixture"}]))
    f = (r["findings"] or [{}])[0]
    ok = len(r["findings"]) == 1 and f.get("verified") is False and f.get("code") == ""
    print(f"  [{'ok' if ok else 'BAD'}]    a line past the end of the file is kept, unverified")
    if not ok:
        fails.append(f"an out-of-range line was not marked unverified: {f}")

    # 4. A NON-NUMERIC LINE DOES NOT RAISE. The reviewer is a model; it will send "123", or None.
    r = tr.review(REL, ask_fn=reply([{"check": "truncation", "line": "not a number",
                                      "silent": True, "severity": "low", "why": "fixture"}]))
    f = (r["findings"] or [{}])[0]
    ok = r["status"] == "ok" and f.get("verified") is False
    print(f"  [{'ok' if ok else 'BAD'}]    a line that is not a number is unverified, not a crash")
    if not ok:
        fails.append(f"a non-numeric line was not handled: {r}")

    # 5. THE PROMPT MUST NOT ASK FOR THE CODE. The guarantee above is only worth anything while
    #    the model is not being invited to produce the thing that broke it.
    p = tr.prompt_for("x.py", "1  pass")
    asks_for_code = '"code"' in p
    forbids = "do NOT quote the code" in p.lower() or "do not quote the code" in p.lower()
    ok = not asks_for_code and forbids
    print(f"  [{'ok' if ok else 'BAD'}]    the prompt asks for a number and forbids quoting code "
          f"(schema mentions code={asks_for_code}, forbids={forbids})")
    if not ok:
        fails.append("the prompt still asks the model to quote code, which is what broke the "
                     "reply 40 times on 2026-09-15")

    # 6. PROOF THAT THE OLD SHAPE REALLY DOES FAIL, from the reply that actually cost the hours.
    #    Not hypothetical: this is the escaping Gemma produced on scripts/check_workspace.py.
    real = ('{"findings": [{"check": "truncation", "line": 1164, "code": '
            '"\\"\\\\n\\".join(f\\"{c} :: {f} (owner {o})" for c, f, o in drifted[:6])", '
            '"silent": true, "severity": "medium", "why": "x"}]}')
    try:
        json.loads(real)
        broke = False
    except ValueError:
        broke = True
    print(f"  [{'ok' if broke else 'BAD'}]    the 2026-09-15 reply is still unparseable, which is "
          f"why the field is gone")
    if not broke:
        fails.append("the historical reply now parses, so this test no longer demonstrates the "
                     "reason the code field was removed -- check the fixture was not edited")

    for x in fails:
        print("FAIL:", x)
    print("test_review_line_only:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
