#!/usr/bin/env python3
"""timeout_for() must tune itself from telemetry -- and must not tune itself into a death spiral.

Run:  python3 scripts/tests/test_timeout_tuning.py

WHY EACH CASE. Jas asked for the cap to be computed from the last N runs of a harness+model
pair rather than revisited by hand. The idea is right and every one of its obvious
implementations is wrong in a specific way, so each way gets a case:

  1. COLD START. No rows must fall back to the static estimate, not to zero and not to
     infinity. A new model has no history and must still be dispatchable.
  2. PER TOKEN. A history of 2000-token runs must not cap a 16000-token run at the 2000-token
     duration. Raw seconds are not comparable across budgets; the rate is.
  3. THE DEATH SPIRAL, and this is the one that would quietly destroy the harness. A run KILLED
     by its timeout records seconds == the timeout. Feed that back and the estimator learns the
     cap is the normal duration, tightens, kills more, learns a shorter duration -- until
     everything dies. Killed rows must be excluded.
  4. THE FLOOR. A lucky fast streak must not starve a legitimate long run.
  5. THE CEILING. A pathological history must not produce an unbounded cap: a wedged server has
     to die eventually or it holds the single llama-server slot forever (2026-09-11/12).
  6. OPT-IN. A caller that passes no harness/model gets the old static number unchanged, so
     this cannot silently alter callers that have not asked for it.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "local-inference"))

import run_telemetry as rt                                          # noqa: E402
import settings                                                     # noqa: E402

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: got {got!r}, want {want!r}")


def seed(rows):
    """Point the ledger at a temp file holding exactly these rows."""
    td = tempfile.mkdtemp()
    p = Path(td) / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""),
                 encoding="utf-8")
    rt.LEDGER = p
    return p


def row(seconds, eval_count, ok=True, harness="H", model="M", load=0.0, timeout_s=None):
    return {"at": "2026-09-14T00:00:00Z", "harness": harness, "model": model,
            "seconds": seconds, "eval_count": eval_count, "load_seconds": load,
            "ok": ok, "timeout_s": timeout_s}


def main():
    old_ledger = rt.LEDGER
    try:
        static_16k = settings.timeout_for(16000)

        # 1. COLD START
        seed([])
        check("1. no history falls back to the static estimate",
              settings.timeout_for(16000, harness="H", model="M"), static_16k)

        # not ENOUGH history is also not evidence
        seed([row(100.0, 1000)] * 2)
        check("1b. fewer rows than MIN_ROWS_TO_TUNE still uses the static estimate",
              settings.timeout_for(16000, harness="H", model="M"), static_16k)

        # 2. PER TOKEN. Ten runs at 0.1 s/token (2000 tokens in 200s).
        seed([row(200.0, 2000) for _ in range(10)])
        tuned = settings.timeout_for(16000, harness="H", model="M")
        expected = int(0.1 * 16000 * settings.TIMEOUT_SAFETY_FACTOR + settings.PROMPT_EVAL_SLACK_S)
        check("2. the cap scales with THIS run's token budget", tuned, expected)
        check("2b. and a 2000-token history does not cap a 16000-token run at 200s",
              tuned > 2000, True)

        # 3. THE DEATH SPIRAL. Ten honest slow runs, plus ten that were KILLED at a short cap.
        #    If the killed rows counted, the estimate would collapse toward the short cap.
        honest = [row(200.0, 2000) for _ in range(10)]
        killed = [row(10.0, 50, ok=False, timeout_s=10) for _ in range(10)]
        seed(honest + killed)
        after = settings.timeout_for(16000, harness="H", model="M")
        check("3. runs KILLED by their timeout are excluded from the estimate", after, expected)
        rate, used = rt.observed_seconds_per_token("H", "M")
        check("3b. and they are not counted as rows either", used, 10)

        # 4. THE FLOOR. A very fast history must not shrink the cap below half the static one.
        seed([row(1.0, 2000) for _ in range(20)])
        fast = settings.timeout_for(16000, harness="H", model="M")
        # AMENDED while writing it: the floor was half the static estimate, which meant a
        # genuinely fast model could never earn a shorter cap -- three cases collapsed onto the
        # same clamped number and case 7 could not tell two models apart. The floor is absolute
        # now; what protects a long run is that the tuned value scales with num_predict.
        check("4. a fast streak cannot take the cap below the absolute floor",
              fast >= settings.MIN_TUNED_TIMEOUT_S, True)
        # 4b WAS WRONG AND THE CODE WAS RIGHT. It asserted the floor is what bounds a fast
        # history; it is not, because PROMPT_EVAL_SLACK_S (600) is added to every tuned value
        # and already equals MIN_TUNED_TIMEOUT_S, so the floor can never bind while those two
        # numbers match. The floor stays as a backstop for anyone who lowers the slack. What is
        # worth pinning is the property the tuning exists for: a genuinely fast model EARNS a
        # smaller cap than the 4-tok/s static assumption, which is the whole point of measuring.
        check("4b. a fast model earns a SMALLER cap than the static assumption",
              fast < static_16k, True)
        check("4c. and never a nonsensical one", fast >= settings.PROMPT_EVAL_SLACK_S, True)

        # 5. THE CEILING. A pathological history must stay finite and bounded.
        seed([row(100000.0, 10) for _ in range(20)])
        huge = settings.timeout_for(16000, harness="H", model="M")
        check("5. a pathological history is clamped to HARD_MAX_TIMEOUT_S",
              huge, settings.HARD_MAX_TIMEOUT_S)
        check("5b. and that is finite, so a wedged server still dies",
              settings.HARD_MAX_TIMEOUT_S < 10**9, True)

        # 6. OPT-IN
        seed([row(200.0, 2000) for _ in range(10)])
        check("6. no harness/model given -> unchanged static behaviour",
              settings.timeout_for(16000), static_16k)

        # 7. A DIFFERENT PAIR HAS ITS OWN HISTORY -- one slow model must not cap a fast one.
        seed([row(200.0, 2000, harness="H", model="SLOW") for _ in range(10)]
             + [row(2.0, 2000, harness="H", model="FAST") for _ in range(10)])
        slow = settings.timeout_for(16000, harness="H", model="SLOW")
        fastm = settings.timeout_for(16000, harness="H", model="FAST")
        check("7. each harness+model pair tunes on its own rows", slow > fastm, True)

        print(f"test_timeout_tuning: {PASSED} passed, {FAILED} failed")
        return 1 if FAILED else 0
    finally:
        rt.LEDGER = old_ledger


if __name__ == "__main__":
    raise SystemExit(main())
