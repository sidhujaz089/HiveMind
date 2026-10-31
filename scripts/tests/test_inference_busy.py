#!/usr/bin/env python3
"""t359 (p45): a live NON-LANE RESIDENT holding the single slot is WAIT, not REFUSED,
and the telemetry names the holder.

WHY. 2026-09-20, four consecutive ticks refused ALL production with "inference path
down" at 08:51-09:21Z. The path was fine: a healthy probe was queued behind an active
interactive dsh-harness step (minutes, on the resident 262k-ctx model) and the 90s
default timed out on the queue. F4 — the fallback probe is blind to non-lane residents.
The fix under test: dispatch_loop.inference_gate(lanes, running, resident) ->
{"verdict": up|wait|refused, "holder": str|None, "say": str}, where probe-down +
resident-non-empty is WAIT with the holder named (the shape a tick record carries).

WRITTEN TO FAIL FIRST against the current code: inference_gate does not exist, and the
inline path classifies the busy-resident state as a naked refusal. Decay: remove the
"wait" branch, or make it stop naming the holder, and this file goes red again.

Everything is faked in-process (a stub inference_health in sys.modules, no record()
calls, no /api/ps, no GPU). The test must be able to run on a dead box.
"""
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import dispatch_loop                                            # noqa: E402

PASSED = FAILED = 0


def check(label, cond, got=None):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [ok]   {label}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: got {got!r}")


DOWN = {"up": False,
        "say": "DOWN: /api/tags answered with 17 model(s) but /api/chat FAILED after "
               "600s (TimeoutError)."}
UP = {"up": True, "say": "UP: qwen3.8:27b-mtp-bf16 answered in 2.2s (5 tokens)"}
RESIDENT = {"qwen3.8:27b-mtp-bf16"}


def run_gate(lanes, running, resident, probe_result):
    """Call the gate with a faked inference_health module; restore on exit."""
    fake = types.ModuleType("inference_health")
    fake.probe = lambda *a, **k: probe_result
    saved = sys.modules.get("inference_health")
    sys.modules["inference_health"] = fake
    try:
        return dispatch_loop.inference_gate(lanes, running, resident), \
            getattr(fake, "called", False)
    finally:
        if saved is None:
            del sys.modules["inference_health"]
        else:
            sys.modules["inference_health"] = saved


# ---- THE SEAM EXISTS ------------------------------------------------------
check("dispatch_loop.inference_gate is a callable seam",
      callable(getattr(dispatch_loop, "inference_gate", None)),
      getattr(dispatch_loop, "inference_gate", None))
if not callable(getattr(dispatch_loop, "inference_gate", None)):
    print(f"test_inference_busy: {FAILED} failed -- the seam does not exist yet; "
          "the other cases cannot run")
    sys.exit(1)

# ---- 1. A RUNNING JOB VOUCHES: verdict up, and the probe is NEVER called ---
poisoned = types.ModuleType("inference_health")


def _boom(*a, **k):
    raise AssertionError("probe must not be called while a job is running")


poisoned.probe = _boom
_saved = sys.modules.get("inference_health")
sys.modules["inference_health"] = poisoned
try:
    g = dispatch_loop.inference_gate([{"model": "qwen3.8:27b-mtp-bf16"}], [], set())
finally:
    if _saved is None:
        del sys.modules["inference_health"]
    else:
        sys.modules["inference_health"] = _saved
check("running lane -> verdict 'up'", g.get("verdict") == "up", g)
check("running lane -> holder None", g.get("holder") is None, g)

# ---- 2. PROBE UP: verdict up, say passed through ---------------------------
g, _ = run_gate([], [], RESIDENT, UP)
check("probe up -> verdict 'up'", g.get("verdict") == "up", g)
check("probe up -> say passed through", g.get("say") == UP["say"], g)

# ---- 3. PROBE DOWN, NOTHING RESIDENT: the genuine dead path, unchanged -----
g, _ = run_gate([], [], set(), DOWN)
check("probe down + no resident -> verdict 'refused'", g.get("verdict") == "refused", g)
check("refused -> holder None", g.get("holder") is None, g)
check("refused -> say passed through", g.get("say") == DOWN["say"], g)

# ---- 4. THE t359 CASE: probe down, slot held by a non-lane resident --------
g, _ = run_gate([], [], RESIDENT, DOWN)
check("probe down + resident -> verdict 'wait' (NOT 'refused')",
      g.get("verdict") == "wait", g)
check("wait -> holder is a non-empty string naming the resident",
      isinstance(g.get("holder"), str) and g["holder"] != ""
      and "qwen3.8:27b-mtp-bf16" in g["holder"], g.get("holder"))
check("wait -> say passed through", g.get("say") == DOWN["say"], g)

# ---- 5. THE SAME STATE WITH A JOB ALSO RUNNING: the vouch still wins -------
g, _ = run_gate([], [{"model": "gemma4:26b", "kind": "production"}], RESIDENT, DOWN)
check("probe down + resident + running job -> verdict 'up' (job vouches)",
      g.get("verdict") == "up", g)

print(f"test_inference_busy: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
