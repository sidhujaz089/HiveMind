"""t1009: the builder's output budget must leave room for the prompt it is sending.

Dispatch p299-t658-failure-schema (2026-10-10T04:26:37Z, model swift) printed its own
arithmetic and shipped it: `output budget 239062 tokens (window 262144)`
(workspace/run-logs/dispatch/p299-t658-failure-schema--production--swift--1791606397.log:11),
then `attempt 1 ... ERROR HTTPError: HTTP Error 400: Bad Request` (same log, line 12) --
zero tokens generated. 262144 - 239062 = 23082 tokens were left for a prompt made of the
419-line workspace/failure-schema.json, a 4971-char brief and the system prompt + edit
protocol, so prompt + num_predict exceeded num_ctx and Ollama rejected the request before
generation. The budget was computed as `window - prompt_estimate` with ZERO headroom, so any
under-estimate of the prompt becomes an HTTP 400, recorded as an ordinary dispatch failure.

This pins ONE new pure function in projects/local-inference/coder.py:

    fit_output_budget(prompt_tokens, window, requested) -> int | None

Returns the largest output budget that fits, leaving OUTPUT_RESERVE_TOKENS of headroom; returns
None when the prompt has grown so large that no answer worth having fits.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]  # scripts/tests -> repo root
CODER = ROOT / "projects" / "local-inference" / "coder.py"
SCHEMA = ROOT / "workspace" / "failure-schema.json"
BRIEF = ROOT / "workspace" / "briefs" / "brief-p299-t658-failure-schema.txt"

# The numbers the failed dispatch used, taken from its own log (lines 11-12).
WINDOW = 262144
REQUESTED = 239062
LEFT_OVER = WINDOW - REQUESTED  # 23082 -- what today's code hands the prompt

MIN_ANSWER = 256          # a budget this small buys no usable reply: refuse instead
RESERVE_FLOOR = 1024      # the headroom whose absence produced the 400

sys.path.insert(0, str(CODER.parent))
import coder  # noqa: E402  -- imported by real path, the pattern scripts/tests/test_coder_first_budget.py uses


def _toks(text):
    """Conservative upper bound on tokens for JSON/English text: chars/3.

    Deliberately an OVER-estimate, so a budget that clears it clears the real prompt.
    No tokeniser is asserted here -- the function under test takes an int.
    """
    return (len(text) + 2) // 3


def test_function_exists():
    assert hasattr(coder, "fit_output_budget"), \
        "coder.py has no fit_output_budget: the output budget is still window-minus-prompt"
    assert callable(coder.fit_output_budget)


def test_budget_leaves_room_for_the_prompt():
    """The invariant the 400 violated: prompt + budget + reserve fits the window."""
    for prompt in (0, 1000, 40000, 120000):
        b = coder.fit_output_budget(prompt, WINDOW, REQUESTED)
        assert b is not None, f"a {prompt}-token prompt leaves plenty of room; refusing is wrong"
        assert b > 0, f"prompt={prompt}: budget must be positive, got {b}"
        assert prompt + b <= WINDOW - RESERVE_FLOOR, (
            f"prompt={prompt} budget={b}: prompt+num_predict must leave at least "
            f"{RESERVE_FLOOR} tokens of the {WINDOW} window, "
            f"used {prompt + b} of {WINDOW}")


def test_fitting_request_is_returned_unchanged():
    """Nothing that fits today gets smaller -- small briefs must be untouched."""
    assert coder.fit_output_budget(1000, WINDOW, 4000) == 4000
    assert coder.fit_output_budget(1000, WINDOW, 32000) == 32000
    assert coder.fit_output_budget(0, 8192, 2000) == 2000


def test_clamps_when_the_request_is_too_big():
    """A requested budget that does not fit is clamped down, not sent as-is."""
    b = coder.fit_output_budget(250000, WINDOW, REQUESTED)
    assert b is not None, "250000-token prompt leaves 12144 - reserve tokens; a clamp is right"
    assert b < REQUESTED, f"{REQUESTED} cannot fit behind a 250000-token prompt"
    assert 250000 + b <= WINDOW - RESERVE_FLOOR, f"clamp left {WINDOW - 250000 - b} tokens headroom"


def test_observed_pairing_is_no_longer_accepted():
    """The exact numbers that produced today's 400: prompt 23082, requested 239062.

    Today's code returns 239062 -- a budget that consumes every token of the window not
    counted into the prompt, so a prompt one estimate-token larger is a 400. A build that
    returns 239062 has not fixed the defect.
    """
    b = coder.fit_output_budget(LEFT_OVER, WINDOW, REQUESTED)
    assert b != REQUESTED, (
        f"fit_output_budget({LEFT_OVER}, {WINDOW}, {REQUESTED}) returned the requested "
        f"{REQUESTED}: that is today's zero-headroom budget, the one Ollama 400'd")


def test_refuses_when_nothing_worth_reading_fits():
    """A prompt that fills the window must be REFUSED, not given a token-sized budget."""
    b = coder.fit_output_budget(WINDOW - 100, WINDOW, 4000)
    assert b is None, f"only 100 tokens remain for the answer; expected None, got {b!r}"
    assert coder.fit_output_budget(WINDOW, WINDOW, 4000) is None
    assert coder.fit_output_budget(WINDOW + 5000, WINDOW, 4000) is None


def test_real_failure_schema_prompt_fits_the_window():
    """Build the real prompt the builder sends and assert the budget clears it.

    Real artefacts, per writing-oracles-for-delegation: the 419-line JSON the dispatch
    carried, and its real brief (4971 chars per the log line 2).
    """
    json_text = SCHEMA.read_text(encoding="utf-8")
    if BRIEF.exists():
        brief_chars = len(BRIEF.read_text(encoding="utf-8"))
    else:
        brief_chars = 4971  # measured: the log's "4971 chars, whole" line
    system_and_protocol = 4000  # system prompt + edit protocol, upper bound
    prompt = _toks(json_text) + _toks("x" * brief_chars) + _toks("x" * system_and_protocol)
    b = coder.fit_output_budget(prompt, WINDOW, REQUESTED)
    assert b is not None, (
        f"the real prompt estimates at {prompt} tokens of a {WINDOW} window; if that "
        f"leaves no answer, the dispatch is over-sized and must be refused, not 400'd")
    assert prompt + b <= WINDOW - RESERVE_FLOOR, (
        f"real failure-schema.json prompt ({prompt} tok) + budget {b} exceeds the "
        f"window; the dispatch would be rejected with HTTP 400 again")


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as exc:  # noqa: BLE001 -- report every failure, never swallow
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
