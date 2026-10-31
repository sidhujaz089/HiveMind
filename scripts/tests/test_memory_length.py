#!/usr/bin/env python3
"""Keyword ranking must not confuse a record's LENGTH with its relevance (t725).

scripts/memory.py keyword_rank scores term overlap as

    s += (1 + math.log(c)) * math.log(1 + n / (1 + df.get(w, 0)))

at memory.py:122, where c is body.count(w) (memory.py:120) and body is the record's content
words plus its tags doubled (memory.py:117). Nothing divides by the length of that record. So a
long record that repeats a query term outscores a short record that is the precise answer: a
term matched twice scores (1 + log 2) = 1.693 against 1.0, three times (1 + log 3) = 2.099.

scripts/skills.py prints that same diagnosis in its own length tripwire (scripts/skills.py:186)
and writes the long records itself (f"SKILL {name}: {description}", scripts/skills.py:146), and
it names the fix as normalisation in memory.py because a cap "doesn't rank better, it just
deletes the evidence". Nothing in this repository failed while that fix was absent, so the
finding was closeable in name only. This file is that command: red on main for the missing
divisor, green once keyword_rank divides by a function of record length.

Red on main for one reason: the four SHORT-FIRST assertions below. The three CONTROLS are green
on main and must stay green, so a build that promotes short records by fiat, drops long records,
truncates facts, or throws away term frequency fails at least one of them.

Hermetic: every record is a literal in this file, passed explicitly to keyword_rank and to
memory.recall(..., use_vectors=False), so nothing is read from workspace/memory.jsonl (the
default at memory.py:230), nothing is written, nothing is embedded -- no urlopen, no model
call, no subprocess, and the worktree stays clean.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import memory  # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print("  [ok]   " + label)
    else:
        FAILED += 1
        print("  [BAD]  " + label + ": expected " + repr(want) + ", got " + repr(got))


def rec(rid, tags, fact):
    """One dict literal in this file, so no fixture can be mis-bracketed."""
    return {"id": rid, "tags": list(tags), "fact": fact}


# ---------------------------------------------------------------------- the fixtures
# Each pair is sized so that a LINEAR divisor (score / content words), a SQUARE-ROOT divisor
# and BM25's k1/b saturation all rank the intended record first. idf is a per-term constant
# shared by both records of a pair, so it cancels: only the term-frequency factor (1 + log c)
# and the length divisor decide the order. Do NOT tidy these records -- the length ratios and
# the exact repeat counts are what make the fix testable.

# 6 content words, each query term appearing ONCE.
SHORT_QUERY_FACT = "a query gets its own instruction prefix"
# ~37 content words, and EXACTLY two of each of: query, instruction, prefix.
LONG_QUERY_FACT = (
    "the query channel embeds a query with a prefix that documents never get, and a "
    "record about prompt injection is ranked above the record about whitespace when the "
    "instruction is missing, so every retrieval path that claims to answer about "
    "indentation must attach the prefix and the instruction, and a path that omits them "
    "is not measuring relevance at all")
LENGTH_PAIR = [
    rec("lb_short", ["memory"], SHORT_QUERY_FACT),
    rec("lb_long", ["skills"], LONG_QUERY_FACT),
]
LENGTH_QUERY = "query instruction prefix"

# The crowding the fix exists for, in its real shape: skills.py:146 writes
# "SKILL name: description" into the same store as short memory facts. Third vocabulary on
# purpose, so a build that hard-codes the ids above passes nothing. Note tokens() (memory.py:94)
# keeps hyphens, so the word 'writing-briefs' is one token and is NOT an occurrence of 'brief'.
SKILL_FACT = (
    "SKILL writing-briefs: how to write a brief for a local model so it cannot invent, "
    "covering the exact file path, the real source, the target suite, and the reason a big "
    "brief burns fifty minutes and fails, with the oracle attached and the symbols list "
    "naming every function that exists in the file, because a brief that describes instead "
    "of showing produces invented paths and the reviewer learns the truth only when the "
    "suite is green and the diff is read")
SKILL_PAIR = [
    rec("sk_short", ["memory"], "a brief must carry the exact file path"),
    rec("sk_long", ["skills"], SKILL_FACT),
]
SKILL_QUERY = "brief"

# CONTROL, green today and green after: relevance must beat length, so normalisation must not
# become length supremacy. rel_long carries BOTH query terms, rel_short one, and the length gap
# is small (6 content words against 4) -- every sane divisor keeps the more relevant record top.
RELEVANCE_PAIR = [
    rec("rel_short", [], "wibble appears once in this note"),
    rec("rel_long", [], "wibble and zorch describe the same retrieval defect"),
]
RELEVANCE_QUERY = "wibble zorch"

# CONTROL, green today and green after: equal length, so any divisor divides both sides by the
# same number and term frequency must still decide the order. Kills a normalisation that throws
# away the (1 + log c) factor and scores presence only.
TF_PAIR = [
    rec("tf_once", [], "wobble appears once"),
    rec("tf_thrice", [], "wobble wobble wobble"),
]
TF_QUERY = "wobble"


def words(r):
    """The divisor's input, measured by the module's own tokenizer rather than by eye."""
    return len(memory.tokens(r["fact"]))


def length_is_not_relevance():
    print("Length is not relevance -- keyword_rank (t725):")
    print("  content words: short " + str(words(LENGTH_PAIR[0]))
          + " long " + str(words(LENGTH_PAIR[1]))
          + " | fact " + str(words(SKILL_PAIR[0]))
          + " skill " + str(words(SKILL_PAIR[1])))

    # RED TODAY. Short matches all three query terms once (raw 3 * 1 = 3.0); long matches all
    # three twice (raw 3 * (1 + log 2) = 5.079), so long is first until length is divided out.
    ranked = memory.keyword_rank(LENGTH_QUERY, LENGTH_PAIR)
    report("short record matching every query term outranks a longer one repeating them",
           [r["id"] for r in ranked], ["lb_short", "lb_long"])

    # NEGATIVE, and the one that matters most: the fix must RANK, not delete evidence. A cap, a
    # slice, a filter that drops long records, or a short-circuit that returns the short record
    # alone satisfies "short first" and fails here.
    long_out = [r for r in ranked if r["id"] == "lb_long"]
    report("the long record is still returned, not dropped", len(long_out), 1)
    report("the long record's fact comes back byte-identical, not truncated",
           (long_out[0]["fact"] == LONG_QUERY_FACT) if long_out else False, True)

    # RED TODAY. The crowding case, third vocabulary: 1.0 against (1 + log 3) = 2.099.
    report("a short fact outranks a long SKILL description that says the word more often",
           [r["id"] for r in memory.keyword_rank(SKILL_QUERY, SKILL_PAIR)],
           ["sk_short", "sk_long"])

    # CONTROL. Green today, must stay green: two matched terms beat one at a 6:4 length ratio.
    report("a record matching more query terms still beats a shorter one",
           [r["id"] for r in memory.keyword_rank(RELEVANCE_QUERY, RELEVANCE_PAIR)],
           ["rel_long", "rel_short"])

    # CONTROL. Green today, must stay green: term frequency survives the divisor.
    report("equal-length records are still ordered by how often they repeat the term",
           [r["id"] for r in memory.keyword_rank(TF_QUERY, TF_PAIR)],
           ["tf_thrice", "tf_once"])

    # The fix must land where the consumer reads it. recall() calls keyword_rank for the keyword
    # channel (memory.py:232), and use_vectors=False keeps that path exact -- no embed, no model
    # call, no store touched. Normalisation added in a helper keyword_rank never calls, or only
    # inside recall(), fails one of these two.
    hits = memory.recall(LENGTH_QUERY, records=LENGTH_PAIR, limit=2, use_vectors=False)
    report("recall's keyword channel agrees: the short record is first",
           [r["id"] for r in hits], ["lb_short", "lb_long"])
    report("recall returns both records at limit=2", len(hits), 2)


def main():
    print("memory.py " + str(ROOT / "scripts" / "memory.py"))
    length_is_not_relevance()
    print("")
    print(str(PASSED) + " passed, " + str(FAILED) + " failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
