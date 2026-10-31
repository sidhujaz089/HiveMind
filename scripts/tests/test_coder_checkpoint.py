#!/usr/bin/env python3
"""p20: coder.py can be interrupted and RESUME from where it stopped, instead of starting over.

Jas, 2026-09-14: "If coders pre start needs Q16 we need a way to interrupt Q16 ... if it can
resume instead of starting over that would be amazing." Q16 is the slowest lane and also the
brief reviewer, so reviews must be able to take it back from a production job without throwing
that job's finished attempts away."""
import re
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {str(got)[:60]}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {str(got)[:60]}")


def code_of(rel):
    return "\n".join(l for l in (ROOT / rel).read_text().splitlines() if not l.strip().startswith("#"))


import json, tempfile
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder as C  # noqa: E402

report("YIELDED exit code is 4", getattr(C, "YIELDED", None), 4)
for name in ("write_checkpoint", "load_checkpoint", "yield_requested"):
    report(f"{name} exists", callable(getattr(C, name, None)), True)
tmp = Path(tempfile.mkdtemp())
if callable(getattr(C, "write_checkpoint", None)) and callable(getattr(C, "load_checkpoint", None)):
    state = {"n": 3, "tries": [{"n": 1, "outcome": "REFUSED"}], "convo": "PROMPT " * 5000,
             "opened": {"a.py": "--- a.py ---"}, "shown_to": {"a.py": 40}, "budget": 16000,
             "label": "brief-x", "model": "m"}
    p = tmp / "cp.json"
    C.write_checkpoint(p, state)
    got = C.load_checkpoint(p)
    report("a checkpoint round-trips every field", got, state)
    report("the conversation is stored whole", len((got or {}).get("convo", "")), len(state["convo"]))
    report("no temp file is left beside it", sorted(x.name for x in tmp.iterdir()), ["cp.json"])
    report("a missing checkpoint loads as None", C.load_checkpoint(tmp / "nope.json"), None)
    (tmp / "torn.json").write_text('{"n": 3, "tri')
    report("a torn checkpoint loads as None, not a crash", C.load_checkpoint(tmp / "torn.json"), None)
if callable(getattr(C, "yield_requested", None)) and hasattr(C, "YIELD_DIR"):
    C.YIELD_DIR = tmp / "yield"
    report("no flag, no yield", C.yield_requested("qwen3.8:27b-mtp-bf16"), False)
    C.YIELD_DIR.mkdir()
    (C.YIELD_DIR / "qwen3.8_27b-mtp-bf16.flag").write_text("reviews waiting")
    report("a flag for this model requests a yield", C.yield_requested("qwen3.8:27b-mtp-bf16"), True)
    report("a flag for another model does not", C.yield_requested("gemma4:26b"), False)
else:
    report("YIELD_DIR exists", hasattr(C, "YIELD_DIR"), True)
src = code_of("projects/local-inference/coder.py")
report("main accepts --resume-checkpoint", '"--resume-checkpoint"' in src, True)
report("the attempt loop checks for a yield", "yield_requested(" in src.split("def main", 1)[-1], True)
report("SIGTERM is handled, so an interrupt checkpoints instead of dying", "signal.SIGTERM" in src, True)
report("a yield exits with YIELDED", "return YIELDED" in src or "exit(YIELDED)" in src, True)

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
