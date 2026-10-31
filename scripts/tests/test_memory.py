#!/usr/bin/env python3
"""Memory must answer paraphrase, keep exact identifiers, and degrade rather than fail.

The vector channel was silently broken when written: Qwen3-Embedding needs an instruction
prefix on the QUERY, and without it the query "indentation" ranked a record about prompt
injection above the record about whitespace. Every assertion here is aimed at that class --
a retrieval layer that returns something confident and wrong is worse than one that returns
nothing.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import memory  # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


FIX = [
    {"id": "x1", "tags": ["python", "whitespace"], "fact": "line-range edits replace whole lines so the new text must carry the block's leading whitespace"},
    {"id": "x2", "tags": ["shell"], "fact": "a process-name wait loop matches its own command line and never exits"},
    {"id": "x3", "tags": ["api"], "fact": "apply_ops refuses a patch when the anchor matches more than once"},
]


def main():
    print("Keyword channel — exact identifiers are its job:")
    kw = memory.keyword_rank("apply_ops", FIX)
    report("an identifier finds its record", kw[0]["id"] if kw else None, "x3")
    # The refusal that matters: keyword must NOT invent a match for a word it does not have.
    report("a word present nowhere returns nothing",
           memory.keyword_rank("kubernetes", FIX), [])
    report("stop words alone return nothing", memory.keyword_rank("the a of", FIX), [])

    print("\nFusion — one channel returning nothing must not break the other:")
    fused = memory.rrf(memory.keyword_rank("apply_ops", FIX), [])
    report("keyword alone still ranks", fused[0]["id"] if fused else None, "x3")
    report("both empty is empty, not an error", memory.rrf([], []), [])

    print("\nThe query instruction is applied to queries and NOT to documents:")
    src = (ROOT / "scripts" / "memory.py").read_text()
    report("a query instruction exists", "QUERY_INSTRUCTION" in src, True)
    report("recall passes is_query=True", "embed([query], is_query=True" in src, True)
    # L-117, sixth occurrence: the flag must DO something, not merely be passed. Driven
    # directly rather than grepped, so killing the prefix logic fails a test.
    report("a query really is prefixed",
           memory.prep(["indentation"], True)[0].startswith("Instruct:"), True)
    report("a document really is not",
           memory.prep(["indentation"], False), ["indentation"])
    # The refusal: documents must be embedded raw. Prefixing both sides destroys the
    # asymmetry the model was trained with.
    report("build_index does NOT prefix documents",
           "embed([r[\"fact\"] for r in todo]," in src and "is_query=True" not in src.split("def build_index")[1].split("\ndef ")[0], True)

    print("\nIntegrity — a changed fact invalidates its cached vector:")
    a = dict(FIX[0])
    d1 = memory.digest(a)
    a["fact"] = a["fact"] + " and something else"
    report("editing the fact changes the digest", memory.digest(a) != d1, True)
    b = dict(FIX[0]); b["tags"] = b["tags"] + ["new"]
    report("editing the tags changes it too", memory.digest(b) != d1, True)

    print("\nDegradation — no embedding model must still answer:")
    real = memory.embed
    memory.embed = lambda *a, **k: None
    try:
        hits = memory.recall("apply_ops", records=FIX, limit=3)
        report("recall still works without vectors", hits[0]["id"] if hits else None, "x3")
    finally:
        memory.embed = real

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
