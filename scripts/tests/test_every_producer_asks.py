#!/usr/bin/env python3
"""A guard that needs remembering is not a guard: every place that starts a model must ask the pause, and a NEW one
cannot be forgotten (Jas, 2026-10-30).

The pause is only as real as its coverage. `must_hold()` answers correctly, but on 2026-10-30 seven producers existed
and exactly ONE consulted a pause -- so pressing pause on both devices still let the scoping loop start work on swift.

WHAT MAKES THIS CHECK TRUSTWORTHY, after a first attempt that was not: it matches a SPAWN, not a mention. The first
version searched for the string anywhere, reported 33 "producers" (mostly /proc scanners and a label map) and was a
check nobody could believe. Two patterns are precise: the Python form that builds a command list containing the model
runner, and the shell form that invokes it. An OBSERVER that merely names the runner is listed in the registry's
_not_producers with its reason.

AND THE LIST CANNOT ROT: an entry that no longer spawns fails too, so a deleted producer cannot keep a check alive.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
REGISTRY = HERE / "workspace" / "model-producers.json"
PY_SPAWN = re.compile(r'REPO\s*/\s*"scripts"\s*/\s*"ask_q16\.py"|\[\s*sys\.executable\s*,\s*str\([^)]*ask_q16\.py')
SH_SPAWN = re.compile(r'python3\s+(?:\S*/)?scripts/ask_q16\.py')

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
declared = {e["path"] for e in reg["producers"]}
excluded = set(reg.get("_not_producers") or {})

found = set()
for p in sorted((HERE / "scripts").glob("*")):
    if not p.is_file() or p.suffix not in (".py", ".sh"):
        continue
    text = p.read_text(encoding="utf-8", errors="replace")
    if PY_SPAWN.search(text) or SH_SPAWN.search(text):
        found.add(f"scripts/{p.name}")

new = found - declared - excluded
check("no producer starts a model without being in the registry", not new, "unregistered: " + ", ".join(sorted(new)))
check("  and every registered producer still spawns a model",
      not (declared - found), "no longer spawns: " + ", ".join(sorted(declared - found)))
check("  an observer is listed with a reason, not silently skipped",
      all((reg["_not_producers"] or {}).get(e) for e in excluded), "an exclusion has no reason")

missing = []
for rel in sorted(declared):
    text = (HERE / rel).read_text(encoding="utf-8", errors="replace")
    if "must_hold" not in text:
        missing.append(rel)
check("every producer ASKS the pause before it starts", not missing, "does not ask: " + ", ".join(missing))

# and the predicate itself, so this suite fails if must_hold is ever weakened to one flag
sys.path.insert(0, str(HERE / "scripts"))
import scheduler_commands as sc          # noqa: E402
both = {"gx10": {"who": "jas"}, "thinkpad": {"who": "jas"}}
check("the predicate holds when BOTH devices are paused, whoever asks",
      sc.must_hold(paused=False, devices=both, known=("gx10", "thinkpad"))[0] is True, "")
check("  and when the production screen is paused",
      sc.must_hold(paused=True, devices={})[0] is True, "")

print("test_every_producer_asks:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
