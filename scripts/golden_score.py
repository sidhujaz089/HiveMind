#!/usr/bin/env python3
"""Score the qualifier against Jas's labels. Gate 6, and the number nobody had.

Six of the nine reviewers of draft four said the golden set could not be deferred, because
parse rate is a proxy for nothing if the verdicts are wrong. Kimi: "you are tuning a guitar by
making sure the strings are taut, not by checking the pitch." DeepSeek: "if the parser works
but the verdicts are wrong, you have built a polished garbage generator."

They were right. Parse rate reached 80% while the verdicts were useless for their purpose, and
no further parser work would ever have shown it.

THE TWO RATERS DO NOT MEAN THE SAME THING BY "SCOPED", AND THIS SCRIPT MUST NOT PRETEND THEY
DO. Jas, 2026-09-08: "If I read it and it has a functional outcome that seems like you and Q16
can figure something out, I consider it scoped... my task may be vague because I rely on
getting my hands on the first iteration to really define the final version." So his SCOPED
means A DIRECTION EXISTS. The prompt's SCOPED means SPECIFIED ENOUGH TO HAND OVER TODAY, with
an oracle that already exists on disk.

Those are different questions and a single agreement figure over them is meaningless. The
first version of this script reported "false-rejected 14/14" as though the model had failed;
it had not, it was answering the other question. Reported here as three separate things:

  * AGREEMENT ON REJECTION -- both answering "is this not worth handing over", genuinely
    comparable, and the number that actually says whether the qualifier works.
  * DEFINITIONAL DIVERGENCE -- Jas says a direction exists, the model says it is not yet
    specified. Both can be right. This is a missing PIPELINE STAGE, not an error rate.
  * FALSE-SCOPED -- the only figure that holds under either definition, because over-delegating
    is wrong on both readings. ChatGPT said this must dominate the risk metric.

    python3 scripts/golden_score.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LABELS = REPO / "workspace" / "golden-set-labels.jsonl"
VERDICTS = REPO / "workspace" / "scoping-rerun.jsonl"
REAL = ("SCOPED", "NEEDS_JAS", "NOT_DELEGABLE")


def rows(p):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def main():
    lab = {r["id"]: r for r in rows(LABELS)}
    mdl = {r["id"]: (r.get("proposal") or {}).get("verdict")
           for r in rows(VERDICTS) if r["status"] == "ok"}
    pairs = [(t, lab[t]["label"], mdl[t], lab[t]["trusted"])
             for t in lab if t in mdl and lab[t]["label"] in REAL]
    if not pairs:
        print("  no comparable pairs -- nothing to report.")
        return 1

    print(f"\nGolden set, recomputed from the record. {len(lab)} labelled, "
          f"{len(pairs)} comparable.\n")
    rej = [p for p in pairs if p[1] == "NOT_DELEGABLE"]
    both = [p for p in rej if p[2] == "NOT_DELEGABLE"]
    if rej:
        print(f"  AGREEMENT ON REJECTION   {len(both)}/{len(rej)} = "
              f"{100 * len(both) / len(rej):.0f}%   <- the comparable question")
    div = [p for p in pairs if p[1] == "SCOPED" and p[2] != "SCOPED"]
    print(f"  DEFINITIONAL DIVERGENCE  {len(div)}   Jas: a direction exists. "
          "Model: not specified yet.")
    print("                           Both can be right. This is a missing pipeline stage,")
    print("                           not an error rate.")
    allsaid = [p for p in pairs if p[2] == "SCOPED"]
    print(f"  FALSE-SCOPED             {sum(1 for p in allsaid if p[1] != 'SCOPED')}"
          "   <- holds under either definition\n")
    print("  --- the raw confusion, for completeness; do not quote the agreement line alone")
    for name, sel in (("all comparable", pairs), ("trusted labels only",
                                                  [p for p in pairs if p[3]])):
        if not sel:
            continue
        n = len(sel)
        agree = sum(1 for _, j, m, _ in sel if j == m)
        said = [p for p in sel if p[2] == "SCOPED"]
        good = [p for p in said if p[1] == "SCOPED"]
        wanted = [p for p in sel if p[1] == "SCOPED"]
        missed = [p for p in wanted if p[2] != "SCOPED"]
        print(f"  {name} (n={n})")
        print(f"    agreement          {agree}/{n} = {100 * agree / n:.0f}%")
        print(f"    model said SCOPED  {len(said)}"
              + (f"   precision {100 * len(good) / len(said):.0f}%" if said else
                 "   <- it never delegates anything"))
        print(f"    FALSE-SCOPED       {len(said) - len(good)}   (the dangerous error)")
        print(f"    false-rejected     {len(missed)}/{len(wanted)}"
              "   (Jas would delegate, model refused)")
        print()

    print(f"  model: {dict(Counter(m for _, _, m, _ in pairs))}")
    print(f"  jas:   {dict(Counter(j for _, j, _, _ in pairs))}")
    untrusted = sum(1 for p in pairs if not p[3])
    if untrusted:
        print(f"\n  {untrusted} of these labels were given on TRUNCATED text (my bug) and are "
              "flagged\n  untrusted in the record. Prefer the trusted-only figures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
