#!/usr/bin/env python3
"""Build a LOCKED item bank for the SENIOR endurance test. Ground truth is mechanical.

    python3 scripts/exam_bank.py build      write the bank and print its sha256
    python3 scripts/exam_bank.py show N     print one item exactly as a model sees it
    python3 scripts/exam_bank.py verify     re-check the bank against its recorded hash

WHY THE ITEMS COME FROM mutate.py
---------------------------------
`scripts/mutate.py` holds 102 mutations, each carrying the file, the CORRECT line, the
DEFECTIVE line, the test that catches it and why it matters. 101 still match the live file.
So three item types fall out without anyone keying anything:

    the defective line in context  -> DEFECT            (a test catches it; that is what a
                                                         mutation IS)
    the correct line in context    -> NOT-A-DEFECT      (the suite is green on it)
    a window EXCLUDING the line    -> CANNOT-DETERMINE  (the evidence is provably absent)

THIS IS THE FIX FOR THE DEFECT THAT WRECKED THE LAST PAPER. Item D2 was keyed DEFECT while
its excerpt contained no collection, no loop and no guard -- and the only model that reasoned
correctly answered CANNOT-DETERMINE and was scored -1. A bad key announces itself by inverting
the ranking. Here the window is DERIVED FROM the line the key depends on, so an unanswerable
item cannot be keyed answerable by accident, and `verify` asserts it mechanically.

SELECTION IS BY HASH, NOT BY ME
-------------------------------
Q16's design point: `sha256(id) % pool` so the person building the bank cannot pick the easy
ones. Its version hashed a lesson date; mutations have no dates, so this hashes the mutation
NAME instead. The property that matters is preserved -- deterministic, and not influenced by
my knowledge of which items are hard. Recorded here because substituting the key is a change
to the method, not a detail.

THE BANK IS LOCKED
------------------
`build` writes a sha256 into the bank itself. `endurance.py` refuses to start if the file no
longer hashes to it, and refuses to RESUME across a change. A run that silently mixes two
versions of the test is worse than one that died, because the first looks complete.
"""
import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import mutate                                                # noqa: E402

OUT = REPO / "workspace" / "exams" / "bank-senior-endurance.json"

CONTEXT_LINES = 12          # lines of real code shown either side of the decisive line
SEED = 20260903

INSTRUCTIONS = """You are reviewing Python from a real repository, one excerpt at a time.

For each excerpt answer with EXACTLY ONE of:
  DEFECT           -- the code shown does NOT do what it appears to be trying to do
  NOT-A-DEFECT     -- the code shown DOES do what it appears to be trying to do
  CANNOT-DETERMINE -- the excerpt does not contain enough to decide

Judge the code against its OWN apparent purpose and its own comments/docstring, not against a
hidden external contract you were not shown.

DEFECT or NOT-A-DEFECT means you can POINT TO THE LINE, in the excerpt, that settles it. If
what you are shown is a fragment whose correctness rests on something you are not shown -- the
branch that populates the list, the caller that invokes the guard, the predicate it hands the
work to -- then the excerpt does not settle it, and CANNOT-DETERMINE is the honest answer EVEN
WHEN EVERY VISIBLE LINE LOOKS RIGHT. "The part I can see is correct" is not the same claim as
"this code is correct", and only the second one is a verdict.

CANNOT-DETERMINE is a real answer and often the right one -- reserve it for excerpts where the
decisive line is genuinely absent, not for ordinary correct code you simply feel unsure about.
Some excerpts deliberately omit the line that would settle the question; most do not. Guessing
is worse than admitting it: a wrong verdict costs more than an abstention, because a false
all-clear closes a file nobody reopens.

Answer with a JSON object keyed by the item id exactly as shown, like:
  {"i07": {"verdict": "DEFECT", "why": "one sentence", "line": "the exact line you judged on"}}

`line` must be quoted VERBATIM from the excerpt you were shown. If you cannot quote a line from
the excerpt that supports your verdict, the honest answer is CANNOT-DETERMINE."""


def _lines(path):
    p = REPO / path
    return p.read_text(encoding="utf-8", errors="replace").splitlines() if p.is_file() else None


def _comment_ratio(block):
    """How much of this window is prose rather than code.

    THIS REPOSITORY NARRATES ITS OWN PAST DEFECTS IN COMMENTS. The first bank built put a
    mutated `sandbox()` call directly under twelve lines explaining that checking `REPO` is
    the correct thing to do -- so the item tested READING, not reasoning. A window that is
    mostly commentary is not a code-review question.
    """
    real = [ln for ln in block if ln.strip()]
    if not real:
        return 1.0
    prose = [ln for ln in real if ln.strip().startswith("#")]
    return len(prose) / len(real)


def _window(lines, idx, keep_decisive=True):
    """A CONTIGUOUS window. Never a window with a hole cut in it.

    The first version cut the decisive line out of the middle and showed the two halves with
    an "(omitted)" marker. On a multi-line expression that produced syntactically broken code,
    and Q16's spec was explicit that a trap must be "syntactically complete (parses) but
    semantically insufficient" -- otherwise a CANNOT-DETERMINE means "this is gibberish"
    rather than "the evidence is absent", and the item measures nothing.

    So a trap is a contiguous block that simply STOPS before the decisive line (or starts
    after it). Real code, real boundary, nothing spliced.
    """
    if keep_decisive:
        lo = max(0, idx - CONTEXT_LINES)
        hi = min(len(lines), idx + CONTEXT_LINES + 1)
        return [(lo, lines[lo:hi])]
    # Take whichever contiguous side carries more actual code, so the excerpt still looks
    # like something worth reviewing.
    before = (max(0, idx - 2 * CONTEXT_LINES), lines[max(0, idx - 2 * CONTEXT_LINES):idx])
    after = (idx + 1, lines[idx + 1:idx + 1 + 2 * CONTEXT_LINES])
    pick = min(before, after, key=lambda b: _comment_ratio(b[1]))
    return [pick]


def _render(path, blocks):
    out = []
    for start, block in blocks:
        if not block:
            continue
        out.append("\n".join(f"{start + n + 1:5d}| {ln}" for n, ln in enumerate(block)))
    return f"# {path}\n\n" + "\n\n        ... (lines omitted) ...\n\n".join(out)


def build():
    usable = []
    for name, path, orig, mut, test, why in mutate.MUTATIONS:
        lines = _lines(path)
        if lines is None:
            continue
        try:
            idx = lines.index(orig)
        except ValueError:
            # line no longer in the file
            print(f"  NOTE: mutation {name}: its line is no longer in {path} -- template stale (t525)", file=sys.stderr)
            continue
        lo = max(0, idx - CONTEXT_LINES)
        window = lines[lo:idx + CONTEXT_LINES + 1]
        if _comment_ratio(window) > 0.25:
            continue          # mostly prose: not a code-review question

        # AND a targeted check on the comments NEAREST the decisive line. A 43%-comment window
        # slipped the ratio test while carrying "This computed `ok`, printed it, logged it --
        # and then returned 0 unconditionally, so a FAILED gate failed nothing" directly above
        # a mutated `return 0`. The ratio measures how much prose there is; this measures
        # whether the prose IS the answer.
        near = " ".join(lines[max(0, idx - 6):idx + 7]).lower()
        near_prose = " ".join(ln for ln in lines[max(0, idx - 6):idx + 7]
                              if ln.strip().startswith("#")).lower()
        vocab = [w for w in re.split(r"[-_]", name) if len(w) > 4]
        if any(w in near_prose for w in vocab):
            continue          # the surrounding commentary names the defect
        usable.append({"name": name, "path": path, "orig": orig, "mut": mut,
                       "test": test, "why": why, "idx": idx, "lines": lines})

    # Deterministic order by sha256 of the NAME. Not my choice, not file order.
    usable.sort(key=lambda m: hashlib.sha256(m["name"].encode()).hexdigest())

    items, used = [], set()

    def take(n, kind):
        got = []
        for m in usable:
            if len(got) == n:
                break
            if m["name"] in used:
                continue
            used.add(m["name"])
            got.append(m)
        for m in got:
            i = len(items) + 1
            iid = f"i{i:02d}"
            if kind == "DEFECT":
                shown = list(m["lines"])
                shown[m["idx"]] = m["mut"]
                body = _render(m["path"], _window(shown, m["idx"]))
                decisive = m["mut"]
            elif kind == "NOT-A-DEFECT":
                body = _render(m["path"], _window(m["lines"], m["idx"]))
                decisive = m["orig"]
            else:
                body = _render(m["path"], _window(m["lines"], m["idx"], keep_decisive=False))
                decisive = m["orig"]
            items.append({"id": iid, "answer": kind, "mutation": m["name"],
                          "file": m["path"], "decisive_line": decisive,
                          "decisive_index": m["idx"], "why": m["why"],
                          "body": body})

    # 10 DEFECT, 8 NOT-A-DEFECT, 8 traps -- Q16's stratification. The 2 "subtly ambiguous"
    # items it also wanted are NOT generated: I have no mechanical way to produce a genuinely
    # ambiguous item, and hand-writing two would put exactly the kind of keyed-by-judgement
    # item back into a bank whose whole point is that nothing is keyed by judgement.
    take(10, "DEFECT")
    take(8, "NOT-A-DEFECT")
    take(8, "CANNOT-DETERMINE")

    # Q16's anchoring and complacency blocks. Ordering is part of the instrument: a run of one
    # verdict followed by its opposite is what detects drift, so it is constructed, not shuffled.
    rng = random.Random(SEED)
    defects = [i for i in items if i["answer"] == "DEFECT"]
    clean = [i for i in items if i["answer"] == "NOT-A-DEFECT"]
    traps = [i for i in items if i["answer"] == "CANNOT-DETERMINE"]
    for pool in (defects, clean, traps):
        rng.shuffle(pool)
    order = (
        [defects[0], clean[0], traps[0], defects[1], clean[1]] +          # 1-5 mixed
        defects[2:6] +                                                    # 6-9 anchoring block
        [traps[1]] +                                                      # 10 anchoring probe
        [clean[2], traps[2], defects[6], clean[3]] +                      # 11-14 mixed
        clean[4:7] +                                                      # 15-17 complacency
        [defects[7]] +                                                    # 18 complacency probe
        [traps[3], defects[8], traps[4], clean[7], traps[5],              # 19-23
         defects[9], traps[6], traps[7]]                                  # 24-26
    )
    seen, ordered = set(), []
    for it in order:
        if it["id"] not in seen:
            seen.add(it["id"])
            ordered.append(it)
    for it in items:                                          # anything the layout missed
        if it["id"] not in seen:
            ordered.append(it)
    for n, it in enumerate(ordered, 1):
        it["position"] = n

    bank = {
        "built": "2026-09-03",
        "purpose": "SENIOR endurance test: laguna-xs.2 vs gpt-oss:20b",
        "seed": SEED,
        "context_lines": CONTEXT_LINES,
        "instructions": INSTRUCTIONS,
        "selection": ("sha256(mutation name) ordering, then stratified take. Not file order "
                      "and not chosen by hand."),
        "counts": {k: sum(1 for i in ordered if i["answer"] == k)
                   for k in ("DEFECT", "NOT-A-DEFECT", "CANNOT-DETERMINE")},
        "blocks": {"anchoring": "positions 6-9 all DEFECT, position 10 is a trap",
                   "complacency": "positions 15-17 all NOT-A-DEFECT, position 18 is a defect"},
        "items": ordered,
    }
    payload = json.dumps(bank, indent=1, sort_keys=True)
    bank["sha256"] = hashlib.sha256(payload.encode()).hexdigest()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(bank, indent=1) + "\n")
    return bank


def verify(bank=None):
    """Two things: the hash still matches, and every trap is MECHANICALLY unanswerable."""
    bank = bank or json.loads(OUT.read_text())
    recorded = bank.pop("sha256", None)
    payload = json.dumps(bank, indent=1, sort_keys=True)
    actual = hashlib.sha256(payload.encode()).hexdigest()
    bank["sha256"] = recorded
    problems = []
    if recorded != actual:
        problems.append(f"HASH MISMATCH: recorded {recorded[:12]}, actual {actual[:12]}. "
                        f"The bank changed after it was locked.")
    for it in bank["items"]:
        present = it["decisive_line"] in it["body"]
        if it["answer"] == "CANNOT-DETERMINE" and present:
            problems.append(f"{it['id']}: trap SHOWS its decisive line -- it is answerable, "
                            f"so the key is wrong")
        if it["answer"] != "CANNOT-DETERMINE" and not present:
            problems.append(f"{it['id']}: keyed {it['answer']} but the decisive line is NOT "
                            f"in the excerpt -- unanswerable, exactly the D2 defect")
    return problems, actual


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    s = sub.add_parser("show"); s.add_argument("n", type=int)
    sub.add_parser("verify")
    a = ap.parse_args()

    if a.cmd == "build":
        bank = build()
        print(f"  {len(bank['items'])} items: {bank['counts']}")
        print(f"  sha256 {bank['sha256']}")
        problems, _ = verify(json.loads(OUT.read_text()))
        for p in problems:
            print(f"  PROBLEM: {p}")
        print("  " + ("mechanical verification PASSED" if not problems else "FAILED"))
        return 1 if problems else 0
    if a.cmd == "show":
        bank = json.loads(OUT.read_text())
        it = next(i for i in bank["items"] if i["position"] == a.n)
        print(f"--- {it['id']} (position {it['position']}) key={it['answer']} ---")
        print(it["body"])
        return 0
    if a.cmd == "verify":
        problems, actual = verify()
        for p in problems:
            print(f"  PROBLEM: {p}")
        print(f"  sha256 {actual}")
        print("  " + ("PASSED" if not problems else "FAILED"))
        return 1 if problems else 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
