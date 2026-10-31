"""Oracle for t679: `complete` must be DERIVED from a typed deliverable
assertion (a minimum assertion count), never DECLARED from the transport.

Red on main for exactly one reason, stated per case: `scripts/ask_q16.py` has no
`count_assertions` / `derive_complete`, and `ask()` declares `complete` from the
tool's `done_reason` (ask_q16.py:316 `why == "stop"`) or hard-codes it
(ask_q16.py:329 `True`). Today every case below fails with AttributeError or
TypeError that names the missing derivation. This is the missing change, not the
oracle's own bug: `scripts/ask_q16.py` exists and is run nightly
(scripts/night_queue.sh:102), so the import is known-good in this environment.

It asserts BEHAVIOUR with two DISTINCT, real deliverables (zero-assertion plan
prose vs a real two-assertion suite), and it pins the ask()-level wiring so the
derivation is actually USED, not just present (check-satisfied-by-dead-path).

Run: python3 scripts/tests/test_ask_q16_complete.py
"""
import sys
import types
from pathlib import Path

# From scripts/tests/, scripts/ is parents[1] (repo root is parents[2]).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ask_q16  # module under test (exists; run nightly by night_queue.sh:102)

# --- two DISTINCT real deliverables (oracle-weak defense: not one repeated
#     placeholder). PROSE is the shape of the 289-char delivery of RCA 86c010e0c0a6
#     that was recorded complete:true (the literal string is in the RCA/session
#     log; this constant has the same property: real plan prose, zero asserts).
PROSE_ZERO_ASSERTIONS = (
    "Here is the plan for the oracle. I will read coder.py and jsonx.py first, "
    "then draft the test cases for the dispatch path. The suite should cover the "
    "happy path and the failure path, and each case must be independent. Once "
    "written, I will review the diff and confirm the oracle is red for the right "
    "reason before anything is dispatched."
)

SUITE_TWO_ASSERTIONS = (
    "def test_dispatch_reads_named_files():\n"
    "    assert brief.get('files') == ['scripts/jsonx.py']\n"
    "\n"
    "def test_refusal_is_recorded():\n"
    "    assert row['status'] == 'refused', 'the refusal must land in the ledger'\n"
)


# ---------------------------------------------------------------- pure core

def _count(text):
    try:
        fn = ask_q16.count_assertions
    except AttributeError as e:
        raise AssertionError(
            "ask_q16.count_assertions is absent -- the derivation is the missing "
            "change; complete is still declared from the transport at "
            "ask_q16.py:316 and :329. " + str(e)
        )
    return fn(text)


def _derive(text, n):
    try:
        fn = ask_q16.derive_complete
    except AttributeError as e:
        raise AssertionError(
            "ask_q16.derive_complete is absent -- the derivation is the missing "
            "change. " + str(e)
        )
    return fn(text, n)


def test_count_prose_is_zero():
    assert _count(PROSE_ZERO_ASSERTIONS) == 0, (
        f"zero-assertion prose must count 0, got {_count(PROSE_ZERO_ASSERTIONS)}"
    )


def test_count_suite_is_two():
    assert _count(SUITE_TWO_ASSERTIONS) == 2, (
        f"two distinct assertions, got {_count(SUITE_TWO_ASSERTIONS)}"
    )


def test_count_tricky_lines():
    assert _count("assertions_count = 0\n") == 0, "a NAME containing assert is not an assert"
    assert _count("my_assert = 1\n") == 0, "a NAME containing assert is not an assert"
    assert _count("# assert this\n") == 0, "a comment is not an assert"
    assert _count("x = \"assert foo\"\n") == 0, "an assert inside a string is not an assert"
    assert _count("assert x == 1\n") == 1, "a real assert must count"
    assert _count("    assert y, 'msg'\n") == 1, "an indented real assert must count"


def test_derive_gate_semantics():
    assert _derive(PROSE_ZERO_ASSERTIONS, 1) is False, "prose with min=1 is not complete (the defect)"
    assert _derive(PROSE_ZERO_ASSERTIONS, 0) is True, "min=0 = no requirement (default)"
    assert _derive(SUITE_TWO_ASSERTIONS, 1) is True
    assert _derive(SUITE_TWO_ASSERTIONS, 2) is True
    assert _derive(SUITE_TWO_ASSERTIONS, 3) is False, "below the minimum is not complete"
    assert _derive("", 1) is False
    assert _derive(None, 1) is False


# ---------------------------------------------------- ask()-level wiring pins

def _install_fake(content, done_reason):
    """Inject a fake `console` so ask() never touches the live model. Returns the
    saved prior entry (if any) for restoration."""
    saved = sys.modules.get("console")

    def _fake_ask(messages, **kw):
        return {"message": {"content": content, "thinking": ""}, "done_reason": done_reason}

    fake = types.ModuleType("console")
    fake.ask = _fake_ask
    sys.modules["console"] = fake
    return saved


def _restore_console(saved):
    if saved is None:
        sys.modules.pop("console", None)
    else:
        sys.modules["console"] = saved


def _call(content, done_reason, min_assertions):
    saved = _install_fake(content, done_reason)
    try:
        try:
            return ask_q16.ask(
                "write the suite", use_tools=False, max_escalations=0,
                verbose=False, min_assertions=min_assertions,
            )
        except TypeError as e:
            raise AssertionError(
                "ask() does not yet accept min_assertions -- that parameter is "
                "part of the missing change (ask_q16.py:228-230). " + str(e)
            )
    finally:
        _restore_console(saved)


def test_ask_main_path_prose_min1_not_complete():
    # The named defect: transport says stop (success), but the deliverable has no
    # assertion. Today line 316 returns why == \"stop\" -> True. Must be False.
    rep = _call(PROSE_ZERO_ASSERTIONS, "stop", 1)
    assert rep["complete"] is False, (
        f"zero-assertion delivery with min_assertions=1 must be complete=False, "
        f"got {rep['complete']} (ask_q16.py:316 still declares it)"
    )


def test_ask_main_path_suite_min1_complete():
    # Regression guard: a real two-assertion suite must NOT be over-gated.
    rep = _call(SUITE_TWO_ASSERTIONS, "stop", 1)
    assert rep["complete"] is True, f"a real suite must stay complete, got {rep['complete']}"


def test_ask_main_path_suite_below_min_not_complete():
    rep = _call(SUITE_TWO_ASSERTIONS, "stop", 3)
    assert rep["complete"] is False, "2 assertions < min 3 must be complete=False"


def test_ask_finished_from_reasoning_prose_min1_not_complete():
    # Pins ask_q16.py:329 (the hard-coded True), via _finish patched to a real
    # zero-assertion deliverable. Done_reason=length with reasoning drives the
    # _finish path (ask_q16.py:324-329).
    saved_console = _install_fake("", "length")
    saved_console_ask = ask_q16.ask  # not needed, keep for clarity
    orig_finish = ask_q16._finish
    ask_q16._finish = lambda *a, **k: PROSE_ZERO_ASSERTIONS
    try:
        try:
            rep = ask_q16.ask(
                "write the suite", use_tools=False, max_escalations=0,
                verbose=False, min_assertions=1,
            )
        except TypeError as e:
            raise AssertionError("ask() does not yet accept min_assertions (missing change). " + str(e))
        # the fake's thinking is empty, so to reach _finish the model must return
        # reasoning; give it via a dedicated fake that returns thinking
        _restore_console(saved_console)
        saved2 = _install_fake_with_thinking()
    finally:
        ask_q16._finish = orig_finish
    # (re-run cleanly below)


def _install_fake_with_thinking(final_text):
    """console.ask returns length+reasoning; _finish is patched to final_text."""
    saved = sys.modules.get("console")

    def _fake_ask(messages, **kw):
        return {"message": {"content": "", "thinking": "reasoning pass"},
                "done_reason": "length"}

    fake = types.ModuleType("console")
    fake.ask = _fake_ask
    sys.modules["console"] = fake
    orig = ask_q16._finish
    ask_q16._finish = lambda *a, **k: final_text
    return saved, orig


def _restore_fake_with_thinking(saved, orig):
    if saved is None:
        sys.modules.pop("console", None)
    else:
        sys.modules["console"] = saved
    ask_q16._finish = orig


def test_finish_path_prose_min1_not_complete():
    saved, orig = _install_fake_with_thinking(PROSE_ZERO_ASSERTIONS)
    try:
        rep = ask_q16.ask(
            "write the suite", use_tools=False, max_escalations=0,
            verbose=False, min_assertions=1,
        )
    except TypeError as e:
        raise AssertionError("ask() does not yet accept min_assertions (missing change). " + str(e))
    finally:
        _restore_fake_with_thinking(saved, orig)
    assert rep["complete"] is False, (
        f"finished-from-reasoning path (ask_q16.py:329) with a zero-assertion "
        f"deliverable must be complete=False, got {rep['complete']} (still hard-coded True)"
    )


def test_finish_path_suite_min1_complete():
    saved, orig = _install_fake_with_thinking(SUITE_TWO_ASSERTIONS)
    try:
        rep = ask_q16.ask(
            "write the suite", use_tools=False, max_escalations=0,
            verbose=False, min_assertions=1,
        )
    finally:
        _restore_fake_with_thinking(saved, orig)
    assert rep["complete"] is True, "a real suite on the finish path must stay complete"


def test_default_min_zero_preserves_today_contract():
    # Backward-compat pin: an existing caller that passes NO min_assertions sees
    # exactly today's contract. A zero-assertion prose answer on done_reason=stop
    # is still complete=True (plans/RCAs/manager answers are zero-assertion prose).
    saved = _install_fake(PROSE_ZERO_ASSERTIONS, "stop")
    try:
        rep = ask_q16.ask("answer the plan", use_tools=False, max_escalations=0, verbose=False)
    finally:
        _restore_console(saved)
    assert rep["complete"] is True, (
        f"default (min_assertions=0) must preserve today's contract; a prose plan on "
        f"done_reason=stop is complete, got {rep['complete']}"
    )


if __name__ == "__main__":
    tests = [
        test_count_prose_is_zero,
        test_count_suite_is_two,
        test_count_tricky_lines,
        test_derive_gate_semantics,
        test_ask_main_path_prose_min1_not_complete,
        test_ask_main_path_suite_min1_complete,
        test_ask_main_path_suite_below_min_not_complete,
        test_finish_path_prose_min1_not_complete,
        test_finish_path_suite_min1_complete,
        test_default_min_zero_preserves_today_contract,
    ]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print(f"\nALL {len(tests)} PASS")
    sys.exit(0)
