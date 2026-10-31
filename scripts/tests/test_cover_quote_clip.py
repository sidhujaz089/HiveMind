#!/usr/bin/env python3
'''t1214 oracle: the cover-judge prompt says when its quote was cut.

workspace/rca/82d340c68a6b.md thread T10: scripts/session_review.py:714 sends
`said[:900]` to the cover judge with no notice, so a long quote is judged on a fragment
presented as the whole. docs/skills/never-truncate-a-record: a value handed to a MODEL may
be bounded, because context is finite -- but the bound must announce itself in the payload.
RED while session_review.py carries the bare slice.

Run: python3 scripts/tests/test_cover_quote_clip.py   (pytest is not installed)
'''
import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "session_review.py"

# Two DISTINCT lengths. One placeholder string cannot satisfy both, so a writer that returns
# the same fixed text for every quote passes a shape-only oracle and fails this one.
SHORT = "Then I'll route them so they reach the root-cause tasks"          # the finding's own shape
LONG = ("The pipeline has a review stage, so I am recording my two findings there, "
        "and then routing them to the root-cause tasks. ") * 40


def load_session_review():
    spec = importlib.util.spec_from_file_location("session_review_under_test", MODULE)
    if spec is None or spec.loader is None:
        raise AssertionError("scripts/session_review.py is not importable from " + str(MODULE))
    tree = ast.parse(MODULE.read_text(encoding="utf-8"), filename=str(MODULE))
    guarded = any(
        isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
        and getattr(n.test.left, "id", "") == "__name__"
        and any(getattr(c, "id", None) == "__main__" for c in n.test.comparators)
        for n in tree.body)
    if not guarded:
        raise AssertionError(
            "scripts/session_review.py has no `if __name__ == '__main__':` guard. This fixture "
            "imports the module, so an unguarded module would run a real review pass, and "
            "write live state, from inside a test -- add the guard first, then the clip.")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_bound_is_a_named_positive_constant():
    mod = load_session_review()
    limit = getattr(mod, "COVER_QUOTE_LIMIT", None)
    assert isinstance(limit, int) and not isinstance(limit, bool), (
        "COVER_QUOTE_LIMIT is " + repr(limit) + "; t1214 names the constant so the bound is "
        "findable and changeable (a literal inlined in an f-string is neither)")
    assert limit >= 900, (
        "COVER_QUOTE_LIMIT = " + str(limit) + "; today's cap is 900 (scripts/session_review.py"
        ":714 slices said[:900]). Raising it is fine; shrinking the bound the judge sees is a "
        "regression, not a fix")


def test_a_quote_that_fits_is_returned_byte_identical():
    mod = load_session_review()
    out = mod.clip_quote(SHORT)
    assert out == SHORT, (
        "clip_quote of a short quote returned " + repr(out) + "; the finding's own quote is "
        "~80 chars, so a clip that annotates every quote buries the item being judged -- the "
        "gate that fires on everything gets bypassed")
    limit = int(mod.COVER_QUOTE_LIMIT)
    exact = "x" * limit
    got = mod.clip_quote(exact)
    assert got == exact, (
        "a quote of exactly the limit came back at " + str(len(got)) + " chars; the boundary "
        "is <=, so a quote that exactly fits is whole text with no notice")
    assert "TRUNCATED" not in got.upper(), (
        "a quote at the limit was annotated: " + repr(got[-80:]))


def test_a_cut_says_it_happened_how_much_and_what_to_do():
    mod = load_session_review()
    assert len(LONG) > int(mod.COVER_QUOTE_LIMIT), (
        "fixture shrank: LONG is " + str(len(LONG)) + " chars, at or below the limit "
        + str(mod.COVER_QUOTE_LIMIT) + " -- this test needs a quote that really is cut")
    out = mod.clip_quote(LONG)
    assert isinstance(out, str), "clip_quote returned " + type(out).__name__
    assert out.startswith(LONG[:int(mod.COVER_QUOTE_LIMIT)]), (
        "the visible part is not the first " + str(mod.COVER_QUOTE_LIMIT) + " characters of "
        "the quote, in order -- a clip that reorders or paraphrases is a new record, not a bound")
    assert str(int(mod.COVER_QUOTE_LIMIT)) in out, (
        "the notice does not state the limit; 'truncated' alone is the shape the skill names")
    assert str(len(LONG)) in out, (
        "the notice does not state the total length. A reader must learn whether the missing "
        "part is a line or a megabyte -- clip() gives {limit} of {len(text)}")
    assert "TRUNCATED" in out.upper(), (
        "the notice does not say it happened: " + repr(out[-120:]))
    assert "infer" in out.lower(), (
        "the notice does not tell the judge what to DO. Without it the model notes the cut and "
        "answers from the fragment, which is the failure this fixture exists to stop")


def test_the_cover_call_site_no_longer_carries_the_bare_slice():
    # Compliance, not decoration: clip_quote must be CALLED at the model boundary. A build that
    # defines the helper and leaves said[:900] in place passes every test above and judges the
    # fragment exactly as silently as today (check-satisfied-by-dead-path).
    src = MODULE.read_text(encoding="utf-8")
    assert "said[:900]" not in src, (
        "scripts/session_review.py still contains the silent slice said[:900] (read at line "
        "714 as: ans = judge(COVER.format(since=since, quote=said[:900], options=...)); "
        "replace the bare slice with the named clip")
    assert src.count("clip_quote(") >= 2, (
        "clip_quote( appears " + str(src.count("clip_quote(")) + " time(s) in the source: one "
        "def plus one call site is the minimum. A helper nothing calls is a rule in prose")


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failed += 1
            print("FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc))
        else:
            print("ok   " + name)
    print(str(len(tests)) + " test(s), " + str(failed) + " failed")
    import sys
    sys.exit(1 if failed else 0)
