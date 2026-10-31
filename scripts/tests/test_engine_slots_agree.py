#!/usr/bin/env python3
"""The engine's ceiling and the file's claim must not disagree (A2, 2026-10-30).

The brief: "the engine ceiling disagrees with itself: SERVERS.json declares vllm-swift.slots = 8,
_engine_slots('swift') returns 4, the engine runs --max-num-seqs 4. Make it read the live flag."

Measured on this box today: capacity_model.engine_slots() reads the live /proc cmdline and answers **4**, while
SERVERS.json declares **8** and modellease takes the FILE's number as the ceiling. So the scheduler could admit
eight sequences into an engine configured to batch four -- the engine is the thing that batches, and no file
overrules it.

RED ON MAIN: capacity_model has no resolved_slots, and modellease still returns the declared 8.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import capacity_model as cm          # noqa: E402
import modellease as ml              # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


# --- the resolver ---------------------------------------------------------------------------------
live = cm.engine_slots()
check("the engine's own flag is readable on this box", live is not None, str(live))
got, why = cm.resolved_slots(8, 4)
check("the engine wins over the file", got == 4, f"{got} ({why})")
check("  and it says so rather than silently correcting", "ENGINE" in why.upper() and "8" in why and "4" in why, why)
got, why = cm.resolved_slots(8, None)
check("an unreadable flag falls back to the file and calls it a claim", got == 8 and "DECLARED" in why.upper(), why)
got, why = cm.resolved_slots(None, 4)
check("a file with no number still gets the measured ceiling", got == 4, f"{got} ({why})")
got, why = cm.resolved_slots(4, 4)
check("agreement is quiet -- no disagreement is claimed when there is none",
      got == 4 and "wins" not in why.lower(), why)
check("nothing at all is None, not a guess", cm.resolved_slots(None, None)[0] is None, str(cm.resolved_slots(None, None)))

# --- the live disagreement ------------------------------------------------------------------------
declared = json.loads((HERE / "projects" / "local-inference" / "models" / "SERVERS.json").read_text(
    encoding="utf-8"))["servers"]["vllm-swift"]["slots"]
got = ml._engine_slots("swift")
check("modellease no longer returns the declared number when the engine says otherwise",
      got is None or got <= (live if live is not None else declared), f"got {got}, engine {live}, file {declared}")
check("  and its ceiling is the engine's, not eight", got == live or got is None, f"got {got}")

print("test_engine_slots_agree:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
