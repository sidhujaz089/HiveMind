#!/usr/bin/env python3
"""Let Q16 explore, cap it, then ask whether the exploration ADVANCED THE PLAN -- and go again.

Jas, 2026-09-11: *"It must be allowed to explore if needed for the task. The exploration needs
to be capped and able to recover. Upon recovery it needs to check the plan to see if the
exploration is advancing the plan and do another exploration if it is."*

WHY ask_q16.py IS NOT ALREADY THIS. It caps tool rounds and spends the final round with tools
OFF, telling the model to write from what it has -- which is right, and it is ONE LEG. When
the cap lands the run is over: nothing asks whether the searching was getting somewhere. The
failure that produced the cap was a model that searched forever (220 tool calls, eight
sections, not a word written, 2026-09-10); the failure this file exists for is the opposite
one, a model cut off three rounds from the answer and forced to guess.

Measured on 2026-09-11, the two ends of it:
    tools on, no round cap, big brief   42 min, 0 bytes    -- searched until the clock died
    tools off, tight brief              8 min, complete    -- but it could not look anything up

THE SHAPE. Exploration runs in LEGS. Each leg is a capped ask_q16 run. Between legs the model
is made to file a CHECKPOINT -- what it learned, which plan step it advanced, and what
specifically it would look up next. The harness, not the model, then decides whether a further
leg is justified.

A LEG IS JUSTIFIED ONLY IF IT NAMES SOMETHING NEW. The checkpoint must name a concrete next
lookup that is not already in the ledger of lookups made. "I would keep investigating" is not
a next lookup. That is the no-repeat rule from unattended-probe-loop, applied to exploration
rather than to tests: what was TRIED is the larger and more useful set, and re-trying it is
what burns a day.

THE MODEL DOES NOT GET TO GRANT ITSELF ANOTHER LEG. It reports; the harness rules. Otherwise
"do I need more time?" is a question asked of the party that wants more time -- the same
reason a model never writes its own oracle (D-35) and never merges its own branch (D-34).

WHAT STOPS IT. Three ceilings, all hard, all reported: the leg cap, the wall clock, and a
checkpoint that names nothing new. When any fires, the final leg runs tools-off and must
produce the deliverable from what it has, marking unverified claims [unverified] -- an honest
gap in a finished document beats a perfect one that does not exist.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

CHECKPOINT = """
================================================================================
DO THE WORK FIRST. Use your tools now -- read the files, run the searches. Only when you have
spent your tool rounds, or have what you need, answer the four questions below.

DO NOT ANSWER THESE FOUR UNTIL YOU HAVE ACTUALLY USED A TOOL. A checkpoint saying "nothing, no
tool calls yet" is not a checkpoint -- it ends the run, because it means this brief is wrong.
On 2026-09-11 four legs were spent that way and the deliverable was written from nothing.
================================================================================
STOP EXPLORING FOR A MOMENT AND FILE A CHECKPOINT. Answer these four, briefly, and nothing
else. This is not the deliverable; it decides whether you get another round of tools.

1. FOUND: what did you establish this leg that you did not know before? Facts, with the file
   or command they came from. If you established nothing, say "nothing" -- that is a real and
   useful answer and it is not held against you.
2. ADVANCED: which step of the plan below did that move, and how far? Name the step.
3. NEXT LOOKUP: the ONE specific thing you would read or search next, as an exact path or
   search string. Not a topic, not "keep investigating" -- something a tool call could take
   verbatim. If you do not need one, write "NONE".
4. ENOUGH: can you write the deliverable now from what you have? YES or NO.

THE PLAN:
{plan}

ALREADY LOOKED UP -- do not name any of these again as your next lookup:
{seen}
"""


def _demark(s):
    """Strip markdown emphasis and list furniture. THE FIRST REAL RUN DIED HERE.

    2026-09-11: Q16 wrote its checkpoint as `**FOUND:** nothing`. The old code stripped a
    leading `*` as list furniture, matched the label, then sliced past it -- leaving `**` as
    the VALUE. Every field came back polluted, `ENOUGH` never parsed at all (None on all four
    legs, so it could never stop early), and the repeat gate compared strings that all began
    `**`. A formatting detail, and it defeated the whole loop.
    """
    return s.replace("**", "").replace("__", "").replace("`", "").strip()


def parse_checkpoint(text):
    """Pull the four fields out. Returns (fields, parsed_any).

    THIS IS THE THIRD SHAPE Q16 HAS WRITTEN A CHECKPOINT IN, and the lesson is in the return
    value rather than in the parsing. Run A: `**FOUND:** x`. Run C: `**ADVANCED** \u2014 x`,
    no colon, em-dash separator, and sometimes the label alone on its line with the value
    underneath. Each time the parser silently produced empty fields, and an empty NEXT LOOKUP
    is indistinguishable from the model deliberately answering NONE.

    THAT CONFLATION ENDED TWO RUNS. "The model says it is done" and "I could not read the
    model" are opposite facts and both arrived as "no next lookup named". So parsing failure
    is now reported as its own outcome, and the raw text is kept for the trail.
    """
    labels = (("found", "FOUND"), ("advanced", "ADVANCED"),
              ("next", "NEXT LOOKUP"), ("enough", "ENOUGH"))
    out = {"found": "", "advanced": "", "next": "", "enough": None}
    lines = [_demark(l.strip().lstrip("0123456789.)-  *#")).strip() for l in text.splitlines()]

    def label_at(i):
        up = lines[i].upper()
        for key, lab in labels:
            if up.startswith(lab):
                return key, lab
        return None, None

    parsed_any = False
    for i, line in enumerate(lines):
        key, lab = label_at(i)
        if not key or out[key] not in ("", None):
            continue
        parsed_any = True
        # SEPARATORS, not just a colon. The em-dash cost run C its findings.
        val = _demark(line[len(lab):].lstrip(":：\u2014\u2013-  ")).strip()
        if not val:
            # LABEL ALONE ON ITS LINE: take what follows, up to the next label.
            for j in range(i + 1, len(lines)):
                if not lines[j]:
                    continue
                if label_at(j)[0]:
                    break
                val = lines[j]
                break
        out[key] = val
    e = (out["enough"] or "").upper() if isinstance(out["enough"], str) else ""
    out["enough"] = True if e.startswith("YES") else False if e.startswith("NO") else None
    return out, parsed_any


def lookup_key(s):
    """The part of a next-lookup that identifies WHAT is being read, stripped of its excuse.

    THE GATE FAILED ON ITS FIRST REAL USE because it compared whole sentences. Q16 asked for
    STATE.md four times running, each with different trailing prose -- "I need to read it to
    verify the four items...", "need to read it to see what...", -- and exact-match said NEW
    every time. Four legs, one file, nothing learned.

    So the key is the FILE PATH or QUOTED TOKEN if there is one, and only the prose if there
    is not. `STATE.md, because X` and `STATE.md, because Y` are the same lookup, which is the
    whole point of a no-repeat rule.
    """
    s = _demark(s or "").strip().strip('"\'')
    m = re.search(r"[\w./-]+\.(?:py|md|sh|json|jsonl|txt|toml|yml|yaml)\b", s)
    if m:
        return m.group(0).lower()
    head = re.split(r"[,;:—–-]| because | to see | so that | in order ", s, 1)[0]
    return " ".join(head.lower().split())[:80]


def is_real_lookup(s, seen):
    """A next lookup must be concrete and unseen, or the leg would repeat the last one.

    THE VAGUE ANSWERS ARE THE DEFAULT ONES, which is why they are refused explicitly rather
    than filtered by length. A model that has run out of ideas says "continue investigating"
    and means "I do not know", and granting a leg for that is how 51 of 61 rounds went
    off-brief on the first probe run.
    """
    if not s:
        return False, "no next lookup named"
    t = s.strip().strip('"`').lower()
    if t in {"none", "n/a", "na", "-", "nothing"}:
        return False, "explicitly named NONE"
    vague = ("keep ", "continue", "further", "more of", "investigate", "explore",
             "look into", "review the code", "the codebase", "various", "etc")
    if any(v in t for v in vague) and "/" not in t and "." not in t:
        return False, f"not concrete: {s[:60]!r}"
    key = lookup_key(s)
    for prev in seen:
        if key and key == lookup_key(prev):
            return False, f"already looked up ({key}): {s[:50]!r}"
    return True, ""


def explore(plan, deliverable, max_legs=4, minutes=45, rounds_per_leg=8,
            budget=16000, out=None, verbose=True):
    """Run legs until the plan is served, the ceilings bite, or nothing new is named."""
    import ask_q16

    def log(m):
        if verbose:
            print(m, flush=True)

    seen, trail, findings = [], [], []
    deadline = time.time() + minutes * 60
    reason = "leg cap reached"

    for leg in range(1, max_legs + 1):
        left = (deadline - time.time()) / 60
        if left <= 1:
            reason = f"wall clock spent ({minutes} min)"
            log(f"  -- {reason}")
            break
        log(f"  leg {leg}/{max_legs}  ({left:.0f} min left, {len(seen)} lookup(s) so far)")
        # A BEAT PER LEG. Not because this loop needs it, but because a supervisor must be
        # able to tell a long leg from a dead one WITHOUT asking this process -- t273, and
        # the two waiters that reported work for two hours after their jobs had died.
        try:
            import heartbeat
            heartbeat.beat(f"explore:{Path(out).stem if out else 'adhoc'}",
                           note=f"leg {leg}/{max_legs}, {len(seen)} lookup(s)")
        except Exception as e:                                    # noqa: BLE001
            # a missing beat must never take the run with it
            print(f"  NOTE: heartbeat not written ({e}) -- the run continues", file=sys.stderr)

        brief = (f"{deliverable}\n\nTHE PLAN THIS SERVES:\n{plan}\n")
        if findings:
            brief += ("\nWHAT EARLIER LEGS ESTABLISHED -- build on this, do not re-derive it:\n"
                      + "\n".join(f"- {f}" for f in findings) + "\n")
        if seen:
            brief += ("\nALREADY LOOKED UP, do not repeat:\n"
                      + "\n".join(f"- {s}" for s in seen) + "\n")
        brief += CHECKPOINT.format(plan=plan, seen="\n".join(f"- {s}" for s in seen) or "(none)")

        # THE LEG'S LAST WORD IS THE CHECKPOINT, not ask_q16's default "write the section
        # now". Both were being sent on 2026-09-11 -- this loop asked for a checkpoint and
        # ask_q16's hardcoded final round told the model to write a document instead. The
        # model obeyed the last thing it was told, the leg came back as prose, and every
        # checkpoint field parsed empty. Two harnesses, contradictory instructions, and the
        # one that ran last won.
        r = ask_q16.ask(brief, use_tools=True, budget=budget, max_escalations=1,
                        max_rounds=rounds_per_leg, verbose=verbose,
                        final_instruction=(
                            "STOP SEARCHING AND FILE YOUR CHECKPOINT NOW. You have spent your "
                            f"tool rounds ({rounds_per_leg}) and have no tools on this turn. "
                            "Do NOT write the deliverable yet -- that comes later, in its own "
                            "turn. Answer ONLY the four checkpoint questions, each on its own "
                            "line, starting with the label:\n"
                            "FOUND: ...\nADVANCED: ...\nNEXT LOOKUP: ...\nENOUGH: YES or NO"))
        raw = r.get("text") or ""
        cp, parsed = parse_checkpoint(raw)
        used = max((x.get("tool_rounds") or 0) for x in (r.get("trail") or [{}]))
        trail.append({"leg": leg, "done_reason": r.get("done_reason"),
                      "complete": r.get("complete"), "tool_rounds": used,
                      # THE WHOLE CHECKPOINT, NOT A SLICE OF IT. This was raw[:600] and the
                      # signature gate refused the commit, correctly: a truncated record is
                      # what makes a model failure undiagnosable, and three post-mortems this
                      # morning turned on text I had not kept. The trail is the diagnosis.
                      "parsed": parsed, "raw": raw, **cp})

        # A LEG THAT TOUCHED NO TOOLS IS NOT EXPLORATION, AND MUST NOT BUY ANOTHER ONE.
        #
        # 2026-09-11, the first real run: four legs, zero tool calls, and Q16 reporting
        # "nothing. No tool calls have been made in this leg yet." every time. It answered the
        # CHECKPOINT instead of doing the work, because the checkpoint was in the same prompt
        # as the task -- and the harness granted a fresh leg each time and finally wrote the
        # deliverable from nothing while reporting complete=True.
        #
        # The model said so plainly in the one field built to carry it. Nothing read it. This
        # is that check: the loop stops and says WHY, rather than spending the day proving the
        # brief wrong four more times.
        if used == 0:
            reason = ("leg made ZERO tool calls -- it answered the checkpoint instead of "
                      "exploring. The brief, not the model.")
            log(f"  -- {reason}")
            break
        if cp["found"] and cp["found"].lower() not in ("nothing", "none"):
            findings.append(cp["found"][:400])
        log(f"    advanced: {cp['advanced'][:70] or '(unstated)'}")
        log(f"    enough:   {cp['enough']}   next: {cp['next'][:60] or '(none)'}")

        # AN UNREADABLE CHECKPOINT IS NOT A STOP SIGNAL. Ending here silently, as "no next
        # lookup named", is what closed runs B and C -- the harness could not read the model
        # and reported it as the model having nothing to say.
        if not parsed:
            reason = "checkpoint did not parse -- the full text is in the trail"
            log(f"  -- {reason}. The checkpoint it sent is in the trail, in full.")
            break

        if cp["enough"] is True:
            reason = "the model says it has enough"
            break
        good, why = is_real_lookup(cp["next"], seen)
        if not good:
            reason = f"no new lookup named -- {why}"
            log(f"  -- {reason}")
            break
        seen.append(cp["next"])

    # THE FINAL LEG IS TOOLS-OFF AND MUST PRODUCE THE DELIVERABLE. Whatever ended the loop,
    # something has to exist at the end of it. This is the step whose absence made the
    # 2026-09-10 run produce 220 tool calls and no document.
    log(f"  writing the deliverable (tools off) -- exploration ended: {reason}")
    final = (f"{deliverable}\n\nTHE PLAN THIS SERVES:\n{plan}\n\n"
             "WHAT YOUR EXPLORATION ESTABLISHED:\n"
             + ("\n".join(f"- {f}" for f in findings) or "- (nothing was established)")
             + "\n\nYou have NO TOOLS on this turn. Write the deliverable from the above. "
               "Any claim you could not verify must be written anyway and marked "
               "[unverified], naming what you would have needed to check it. An honest gap "
               "in a finished document is worth far more than a perfect one that does not "
               "exist.")
    w = ask_q16.ask(final, use_tools=False, budget=budget, max_escalations=1, verbose=verbose)

    # COMPLETE MEANS THE DOCUMENT IS WHOLE **AND** WAS WRITTEN FROM SOMETHING. ask_q16's
    # `complete` only means the generation was not truncated -- on 2026-09-11 that returned
    # True for 17,869 characters written with zero verified findings, which is the "looked
    # healthy the whole time" failure landing inside the loop built to prevent it.
    # THE EMPTY STRING WAS IN THIS TUPLE ON THE FIRST WRITE, and `startswith(("", ...))` is
    # True for every string -- so nothing counted as grounded and everything read as blind.
    # Caught by running it against three real findings rather than by reading it.
    def _empty(f):
        s = f.strip().lower().lstrip("*: ").strip()
        return (not s) or s.startswith(("nothing", "none", "n/a"))
    grounded = [f for f in findings if not _empty(f)]
    blind = not grounded
    result = {"text": w.get("text", ""),
              "complete": w.get("complete", False) and not blind,
              "blind": blind,
              "ended_because": reason, "legs": trail, "lookups": seen,
              "findings": findings}
    # THE OUTCOME IS THIS LOOP'S OWN VOCABULARY, NEVER done_reason.
    #
    # Every blind run today reported done_reason="stop", and "stop" is in error_scan.py's
    # HEALTHY_STATUS. So the failures that actually cost the morning -- a 17,869-character
    # document written from zero findings, and a checkpoint the harness could not read --
    # would have scanned as healthy. The 42-minute timeout WOULD have surfaced, because
    # "timeout" is not on the allowlist. That is the difference between a run that is broken
    # and a run that merely finished.
    #
    # These four words are deliberately NOT on the allowlist, so the inversion does the work:
    # anything that is not plainly "ok" surfaces without anyone predicting it.
    outcome = ("blind" if result["blind"] else
               "incomplete" if not result["complete"] else "ok")
    if "did not parse" in reason:
        outcome = "checkpoint-unparsed"
    elif "ZERO tool calls" in reason:
        outcome = "zero-tool-calls"
    try:
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "task": str(out or "explore"), "tool": "explore_loop",
               "outcome": outcome, "ended_because": reason,
               "legs": len(trail), "lookups": len(seen), "findings": len(grounded),
               "chars": len(result["text"])}
        with open(REPO / "workspace" / "agent-runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:                                   # noqa: BLE001
        log(f"  (could not write the run ledger: {type(e).__name__}: {e})")
    result["outcome"] = outcome

    if out:
        Path(out).write_text(result["text"], encoding="utf-8")
        Path(str(out) + ".trail.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
        log(f"  wrote {out} ({len(result['text'])} chars, complete={result['complete']})")
        if result.get("blind"):
            log("  WRITTEN BLIND -- the exploration established nothing. Not a usable answer.")
        elif not result["complete"]:
            log("  INCOMPLETE -- do not read this as an answer.")
    return result


def self_test():
    """Prove the gate can REFUSE a leg, not only grant one. A gate that only ever says yes
    is the 'looked healthy the whole time' failure this repository keeps finding."""
    ok = True
    cases = [
        ("scripts/coder.py:410", [], True, "a concrete path is a real lookup"),
        ("keep investigating the retry loop", [], False, "vague is refused"),
        ("NONE", [], False, "an explicit NONE is refused"),
        ("", [], False, "an empty next lookup is refused"),
        ("scripts/coder.py:410", ["scripts/coder.py:410"], False, "a repeat is refused"),
        ("scripts/coder.py:410", ["SCRIPTS/CODER.PY:410"], False, "repeat check is case-blind"),
    ]
    print("The leg gate must refuse as well as grant:")
    for nxt, seen, want, why in cases:
        got, detail = is_real_lookup(nxt, seen)
        good = got == want
        ok &= good
        print(f"  [{'ok ' if good else 'BAD'}]  {why}: {nxt!r} -> {got}  {detail}")

    print("\nThe checkpoint parser must read all four fields:")
    cp, _ = parse_checkpoint("1. FOUND: coder.py has its own extractor\n"
                             "2. ADVANCED: step 2, halfway\n"
                             "3. NEXT LOOKUP: scripts/jsonx.py\n"
                             "4. ENOUGH: NO\n")
    for k, want in (("found", "coder.py has its own extractor"), ("advanced", "step 2, halfway"),
                    ("next", "scripts/jsonx.py"), ("enough", False)):
        good = cp[k] == want
        ok &= good
        print(f"  [{'ok ' if good else 'BAD'}]  {k}: {cp[k]!r}")

    # THE REGRESSION CASES, all four taken verbatim from the run that defeated the first
    # version on 2026-09-11. A self-test written from imagination passes; these are what
    # actually happened.
    print("\nMarkdown emphasis must not survive into the values (the bug that killed leg 1-4):")
    cp, _ = parse_checkpoint("**FOUND:** nothing\n**ENOUGH:** NO\n"
                             "**NEXT LOOKUP:** `STATE.md` \u2014 need to read it")
    for k, want, why in (("found", "nothing", "value is not '**'"),
                         ("enough", False, "ENOUGH parses instead of returning None"),
                         ("next", "STATE.md \u2014 need to read it", "lookup is not '**'")):
        good = cp[k] == want
        ok &= good
        print(f"  [{'ok ' if good else 'BAD'}]  {why}: {cp[k]!r}")

    print("\nThe same file asked for twice with different excuses is ONE lookup:")
    real = ["** `STATE.md` \u2014 I need to read it to verify the four items under PHASE 1",
            "**: `STATE.md` \u2014 need to read the PHASE 1 IS NOT READY section"]
    got, detail = is_real_lookup(real[1], [real[0]])
    ok &= got is False
    print(f"  [{'ok ' if got is False else 'BAD'}]  refused: {detail}")

    print("\nA finding of 'nothing' must not count as grounding a deliverable:")
    for f, want in (("** nothing. No tool calls have been made in this leg yet.", False),
                    ("Nothing. No tool calls made yet this leg.", False),
                    ("", False),
                    ("coder.py has its own JSON extractor at line 410", True)):
        s = f.strip().lower().lstrip("*: ").strip()
        grounded = bool(s) and not s.startswith(("nothing", "none", "n/a"))
        good = grounded == want
        ok &= good
        print(f"  [{'ok ' if good else 'BAD'}]  {f[:44]!r} -> grounded={grounded}")

    print("\nRUN C's SHAPE: no colon, em-dash separator, label alone on its line:")
    cp, parsed = parse_checkpoint(
        "**FOUND** \u2014 coder.py has no committed suite\n"
        "**ADVANCED**\n"
        "step 2, the verification step\n"
        "**NEXT LOOKUP** \u2014 scripts/tests/test_coder_guards.py\n"
        "**ENOUGH** \u2014 NO\n")
    for k, want in (("found", "coder.py has no committed suite"),
                    ("advanced", "step 2, the verification step"),
                    ("next", "scripts/tests/test_coder_guards.py"),
                    ("enough", False)):
        good = cp[k] == want
        ok &= good
        print(f"  [{'ok ' if good else 'BAD'}]  {k}: {cp[k]!r}")
    ok &= parsed
    print(f"  [{'ok ' if parsed else 'BAD'}]  reported as parsed")

    print("\nAn UNREADABLE checkpoint must report parsed=False, not look like a clean stop:")
    _, parsed2 = parse_checkpoint("I had a good look around and found the code interesting.")
    ok &= parsed2 is False
    print(f"  [{'ok ' if parsed2 is False else 'BAD'}]  prose with no labels -> parsed={parsed2}")
    print("       (this conflation ended runs B and C: 'the model says stop' and 'I could")
    print("        not read the model' both arrived as 'no next lookup named')")

    print("\n...and an unparseable ENOUGH must be None, never a silent False:")
    good = parse_checkpoint("ENOUGH: maybe")[0]["enough"] is None
    ok &= good
    print(f"  [{'ok ' if good else 'BAD'}]  'maybe' -> None")
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", help="file holding the plan the exploration must serve")
    ap.add_argument("--task", help="file holding the deliverable brief")
    ap.add_argument("--out")
    ap.add_argument("--max-legs", type=int, default=4)
    ap.add_argument("--minutes", type=int, default=45)
    ap.add_argument("--rounds-per-leg", type=int, default=8)
    ap.add_argument("--budget", type=int, default=16000)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not (a.plan and a.task and a.out):
        ap.error("--plan, --task and --out are required")
    r = explore(Path(a.plan).read_text(), Path(a.task).read_text(),
                max_legs=a.max_legs, minutes=a.minutes,
                rounds_per_leg=a.rounds_per_leg, budget=a.budget, out=a.out)
    return 0 if r["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
