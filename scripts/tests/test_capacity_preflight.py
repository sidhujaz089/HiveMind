#!/usr/bin/env python3
"""t1494: the preflight's OWN measurement must be able to stop a dispatch it has already
proved cannot finish. Nothing is wrong with such a brief -- the box is the problem.

THE DEFECT. coder.py probes the inference path (inference_health.classify, coder.py:2916) and
the probe's dict carries `seconds` and `eval_count` (scripts/inference_health.py:190-193).
coder.py reads them for a NOTE only (2928-2933), then builds the run's output budget (3065-3067)
and its socket timeout (ask(), 1099-1111) from sources that have never seen that number. On
2026-10-19 the probe measured 31.36 s for five tokens -- 6.272 s/token -- and the dispatch ran
9363 s at that rate, ~1,492 tokens of a 16000-token budget, and died on its own timeout. At
6.272 s/token that budget needs 100,352 s; settings.HARD_MAX_TIMEOUT_S is 21600. There is NO
sanctioned timeout at which that dispatch could have answered. 1800 s buys ~287 tokens:
done_reason=length, the empty answer the briefing skill documents. Both outcomes are useless, so
the decision is start / do-not-start, and the number that decides it is already measured.

WHY A SEAM. capacity_verdict(seconds, eval_count, num_predict, hard_max=None, slack=None)
turns the probe's two numbers plus the run's budget into a verdict, and capacity_guard(probe,
num_predict, say_fn) applies it: False, and says why, when the budget cannot be produced inside
the cap. A pure seam is how this repo tests a decision it cannot drive end to end -- the
precedent is dispatch_loop.inference_gate in scripts/tests/test_inference_busy.py. The guard
takes say_fn and WRITES NOTHING, so asserting it touches no live state.

Asserted: the verdict comes from the probe's OWN seconds/eval_count (not a stored rate); the cap
is settings.HARD_MAX_TIMEOUT_S, not a literal; a warm probe is NOT refused (a guard that refuses
everything is not a guard); a probe with no usable numbers does not block the run and says so
(unknown is not evidence); a probe dict with missing keys does not raise; and the guard USES the
verdict, because computed-and-discarded IS the defect.

Decay: thresholds are computed at test time from settings.HARD_MAX_TIMEOUT_S and
settings.PROMPT_EVAL_SLACK_S, with an exact-crossover case, so moving the cap moves the test.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "local-inference"))

PASSED = FAILED = 0


def check(label, cond, got=None):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [ok]   {label}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: got {got!r}")


def main():
    import settings  # projects/local-inference/settings.py -- the one source of these numbers
    CAP = int(settings.HARD_MAX_TIMEOUT_S)
    SLACK = float(settings.PROMPT_EVAL_SLACK_S)

    try:
        import coder
    except Exception as e:
        print(f"  [BAD]  coder.py cannot be imported: {type(e).__name__}: {e}")
        return 1

    have_v = callable(getattr(coder, "capacity_verdict", None))
    have_g = callable(getattr(coder, "capacity_guard", None))
    check("coder.capacity_verdict is a callable seam", have_v, getattr(coder, "capacity_verdict", None))
    check("coder.capacity_guard is a callable seam", have_g, getattr(coder, "capacity_guard", None))
    if not (have_v and have_g):
        print("test_capacity_preflight: FAILED -- the seam does not exist yet; the other "
              "cases cannot run")
        return 1

    print("1. the 2026-10-19 measurement, passed the way the probe returns it:")
    v = coder.capacity_verdict(31.36, 5, 16000)
    check("31.36s for 5 tokens against a 16000-token budget is NOT ok",
          v.get("ok") is False, v)
    check("  seconds/token came from seconds/eval_count",
          abs(float(v.get("spt", 0)) - 31.36 / 5.0) < 1e-9, v)
    check("  it reports the seconds the budget needs, incl. prefill slack",
          abs(float(v.get("needed_s", 0)) - (31.36 / 5.0 * 16000 + SLACK)) < 1.0, v)
    check("  the cap is settings.HARD_MAX_TIMEOUT_S, not a literal in coder.py",
          int(v.get("cap_s", -1)) == CAP, v)
    s = str(v.get("say", ""))
    check("  say names the measured seconds (31.36)", "31.36" in s, s)
    check("  say names the run's budget (16000)", "16000" in s, s)
    check("  say names the cap's own value", str(CAP) in s, s)
    check("  say names the knob a human would raise", "HARD_MAX_TIMEOUT_S" in s, s)
    check("  say says the brief is not at fault", "brief" in s.lower(), s)

    print("2. a warm box is not refused (a guard that refuses everything is not a guard):")
    v = coder.capacity_verdict(2.2, 5, 16000)
    check("2.2s for 5 tokens is ok", v.get("ok") is True, v)
    check("  needed = 2.2/5*16000 + slack",
          abs(float(v.get("needed_s", 0)) - (2.2 / 5.0 * 16000 + SLACK)) < 1.0, v)
    check("  an ok verdict says nothing", v.get("say") == "", v)

    print("3. the crossover, computed from settings (the decay test):")
    at_cap = ((CAP - SLACK) / 16000.0) * 5
    v = coder.capacity_verdict(at_cap, 5, 16000)
    check("exactly at the cap is ok, not over", v.get("ok") is True, v)
    v = coder.capacity_verdict(at_cap * 1.01, 5, 16000)
    check("one percent slower than the cap is NOT ok", v.get("ok") is False, v)
    v = coder.capacity_verdict(31.36, 5, 16000, hard_max=200000)
    check("a bigger cap flips the same numbers to ok", v.get("ok") is True, v)
    check("  and cap_s reports the cap it was given", int(v.get("cap_s", -1)) == 200000, v)
    v = coder.capacity_verdict(31.36, 5, 16000, slack=0)
    check("a caller-supplied slack is honoured",
          abs(float(v.get("needed_s", 0)) - 31.36 / 5.0 * 16000) < 1e-6, v)

    print("4. unknown is not evidence: never block, never crash:")
    v = coder.capacity_verdict(None, 5, 16000)
    check("seconds=None is ok (the probe's no-model shape)", v.get("ok") is True, v)
    check("  and the reason is said, not silent", "not" in str(v.get("say", "")).lower(), v)
    v = coder.capacity_verdict(31.36, 0, 16000)
    check("eval_count=0 is ok (no rate can be derived)", v.get("ok") is True, v)
    v = coder.capacity_verdict(31.36, 5, 0)
    check("a zero budget is ok (nothing to fit)", v.get("ok") is True, v)
    v = coder.capacity_verdict("slow", 5, 16000)
    check("a non-numeric seconds does not raise", v.get("ok") is True, v)

    print("5. the guard USES the verdict, on the probe dict's own fields:")
    notes = []
    g = coder.capacity_guard({"seconds": 31.36, "eval_count": 5, "slow": True,
                              "state": "busy-but-answering"}, 16000, notes.append)
    check("the incident's probe is refused", g is False, g)
    check("  exactly one thing is said", len(notes) == 1, notes)
    check("  it names the measured seconds", bool(notes) and "31.36" in notes[0], notes)
    check("  it names the budget", bool(notes) and "16000" in notes[0], notes)

    notes2 = []
    g = coder.capacity_guard({"seconds": 2.2, "eval_count": 5, "slow": False, "state": "up"},
                             16000, notes2.append)
    check("a warm probe proceeds", g is True, g)
    check("  and says nothing", notes2 == [], notes2)

    notes3 = []
    g = coder.capacity_guard({"seconds": None, "eval_count": None}, 16000, notes3.append)
    check("an unmeasured probe proceeds", g is True, g)
    check("  and the reason is said, not swallowed", len(notes3) == 1, notes3)

    notes4 = []
    g = coder.capacity_guard({}, 16000, notes4.append)
    check("a probe dict with no keys at all does not raise", g is True, g)

    print("6. the measurement is read from the dict, not from a rate stored elsewhere:")
    notes5 = []
    g = coder.capacity_guard({"seconds": 31.36, "eval_count": 5, "tok_s": 40.0}, 16000, notes5.append)
    check("a stale tok_s field cannot override the measured seconds", g is False, g)
    check("  and what is said quotes the measurement, not the stored rate",
          bool(notes5) and "31.36" in notes5[0] and "40" not in notes5[0], notes5)

    print("7. settings.py unreachable: proceed, loudly (a lost guard is never silent):")
    saved = sys.modules.get("settings")
    sys.modules["settings"] = None
    try:
        notes6 = []
        g = coder.capacity_guard({"seconds": 31.36, "eval_count": 5}, 16000, notes6.append)
        check("no settings.py does not block the run", g is True, g)
        check("  and the lost guard is announced",
              len(notes6) == 1 and "settings" in str(notes6[0]).lower(), notes6)
    finally:
        if saved is None:
            sys.modules.pop("settings", None)
        else:
            sys.modules["settings"] = saved

    print(f"test_capacity_preflight: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
