#!/usr/bin/env python3
"""t1418: a supervisor REWRITE-BRIEF carrying an unfilled template placeholder is REFUSED.

Filed from p673-t1257-run-tests on swift, 2026-10-18T03:43:19Z. The supervisor model's own reply
carried an instruction to ITS author --
"[REPLACE THIS BLOCK WITH THE ACTUAL SOURCE FROM scripts/run_tests.py, lines ~620-684, ...]" --
and scripts/supervise.py:298 (bp.write_text(new + "\n")) shipped it to the brief file verbatim.
The builder dispatched against that brief was asked to insert a call at a source section it was
never shown: the shape briefing-the-local-model names, "a brief that describes instead of showing
produces invented APIs and invented paths". Its two attempts were 236 and 176 tokens.

This suite points SV.BRIEFS at a temp dir: the live workspace/briefs/ is never written.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import supervise as SV

FAIL = {"at": "2026-10-18T03:43:19Z", "chunk": "p673-t1257-run-tests", "model": "swift",
        "exit": 1, "brief": "brief-p673-t1257-run-tests.txt",
        "brief_sha256": "371c19f7d29a292d3e5c5e679feed25a14df27a08964d48e05082e4aee650f14"}

OLD = "TASK: t1257\nONE file: scripts/run_tests.py. Add a function and call it at sweep start.\n"

# What a good rewrite looks like: a brief with real source pasted in. Distinct from OLD, so a
# rewrite that quietly does nothing is caught by test_a_clean_rewrite_still_lands.
GOOD = ("TASK: t1257\nSYMBOLS: pre_clean_flaky_scratch (new)\n\n"
        "ONE file: scripts/run_tests.py.\n\n=== REAL SOURCE (scripts/run_tests.py) ===\n"
        "def main():\n"
        "    ceiling = json.loads(Path('workspace/test-failures-ceiling.json').read_text())\n"
        "    if fast_path:\n        return run_fast()\n    pool = TestPool(ceiling)\n"
        "    pool.launch()\n")

# The two unfilled-template shapes that shipped in the real brief, quoted as they appeared.
SHAPE_A = ("[REPLACE THIS BLOCK WITH THE ACTUAL SOURCE FROM scripts/run_tests.py, "
           "lines ~620-684, showing the ceiling JSON load, the fast-path guard, and the pool "
           "launch. Show at least 15 lines of context around each landmark so the model can "
           "see the exact insertion point.]")
SHAPE_B = ("# [SHOW THE REAL SOURCE HERE \u2014 the ~60 lines from where the ceiling JSON is "
           "loaded\n#  through the pool launch. The model must see the exact lines around the "
           "insertion\n#  point. Insert the call between the ceiling load and the pool launch, "
           "in a location\n#  that does NOT execute on the fast path.]")


def _run(new_brief, old_brief=OLD):
    """Call SV.execute() with SV.BRIEFS redirected to a fresh temp dir. Returns
    (action string, brief text on disk, [(archive name, archive content)]). SV.BRIEFS is
    restored in a finally block, so nothing outside the temp dir is ever written."""
    orig = SV.BRIEFS
    with tempfile.TemporaryDirectory() as td:
        SV.BRIEFS = Path(td)
        try:
            (SV.BRIEFS / FAIL["brief"]).write_text(old_brief, encoding="utf-8")
            out = SV.execute(dict(FAIL), {"decision": "REWRITE-BRIEF", "new_brief": new_brief})
            written = (SV.BRIEFS / FAIL["brief"]).read_text(encoding="utf-8")
            adir = SV.BRIEFS / "archive"
            archived = ([(p.name, p.read_text(encoding="utf-8"))
                         for p in sorted(adir.iterdir())] if adir.is_dir() else [])
        finally:
            SV.BRIEFS = orig
    return out, written, archived


def test_unfilled_replace_this_block_is_refused():
    out, written, archived = _run("=== REAL SOURCE ===\n" + SHAPE_A)
    assert out.startswith("REFUSED"), "the placeholder brief was written, not refused: " + out
    assert written == OLD, "the brief on disk was overwritten by a placeholder: " + written
    assert archived == [], "an archive entry was written for a refused brief: " + repr(archived)


def test_show_the_real_source_is_refused():
    # The second shape came in the same real reply, wrapped in '#' comments and split over
    # lines: the guard must fire on a brief that is part real source, part author scaffolding.
    out, written, archived = _run(GOOD + "\n" + SHAPE_B)
    assert out.startswith("REFUSED"), "the second template shape was written, not refused: " + out
    assert written == OLD, "the brief on disk was overwritten: " + written
    assert archived == [], "an archive entry was written for a refused brief: " + repr(archived)


def test_refusal_names_the_placeholder_it_found():
    # A bare "no" is not a record: the reason has to travel inside the returned string, because
    # that string is what the supervisor's main() records as the ledger action field.
    out, _w, _a = _run("=== REAL SOURCE ===\n" + SHAPE_A)
    assert out.startswith("REFUSED"), "not refused at all: " + out
    assert "REPLACE THIS BLOCK" in out, "the refusal does not quote what it found: " + out
    assert "SHOW THE REAL SOURCE" in _run(GOOD + "\n" + SHAPE_B)[0], \
        "the refusal for shape B does not quote what it found"


def test_ordinary_bracketed_text_is_not_a_placeholder():
    # The guard fires on an author-instruction, not on brackets, not on the lowercase word
    # replace, not on a slice. A regex broad enough to fire on brackets rejects every real brief.
    text = GOOD + ("Note: the ceiling list may change [see the skill], and the builder "
                   "replaces the placeholder value with the real constant, e.g. row[:2000].\n")
    out, written, archived = _run(text)
    assert not out.startswith("REFUSED"), "an ordinary brief was refused: " + out
    assert written == text + "\n", "a clean brief was not written verbatim: " + written


def test_a_clean_rewrite_still_lands():
    out, written, archived = _run(GOOD)
    assert not out.startswith("REFUSED"), "a brief carrying real source was refused: " + out
    assert written == GOOD + "\n", "the new brief was not written verbatim: " + written
    assert len(archived) == 1, "the old brief was not archived: " + repr(archived)
    assert archived[0][1] == OLD, "the archive does not hold the old brief: " + repr(archived[0])


if __name__ == "__main__":
    fails = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("ok   " + _name)
            except AssertionError as e:
                fails += 1
                print("FAIL " + _name + ": " + str(e))
    print(f"{fails} failure(s)")
    sys.exit(1 if fails else 0)
