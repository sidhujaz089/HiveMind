"""2026-10-14: the post-commit hook ran corpus_scripts.py with the Auditor at 0 lanes and loaded the Q4
onto a ThinkPad already short of RAM -- thinkpad-guard tripped twice. stood_down() is the off switch:
a stopped role, a paused scheduler or a paused device each stop it, and main() then asks no model."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import corpus_scripts as C
import scheduler_commands as S
import task_routing as T

ok, fail = 0, 0
def check(name, cond):
    global ok, fail
    print(f"  [{'ok' if cond else 'FAIL'}]   {name}"); ok += bool(cond); fail += not cond

T.load = lambda *a, **k: {"roles": {}}
lanes = {"n": 1}
T.role_lanes = lambda d, role: lanes["n"]
paused = {"scheduler": False, "devices": {}}
S.is_paused = lambda: paused["scheduler"]
S.paused_devices = lambda: paused["devices"]

check("nothing off -> runs", C.stood_down() is None)
lanes["n"] = 0
check("Auditor at 0 lanes -> stands down", "0 lanes" in (C.stood_down() or ""))
lanes["n"] = None
check("lanes unset (no role limit) -> runs", C.stood_down() is None)
paused["scheduler"] = True
check("scheduler paused -> stands down", "scheduler" in (C.stood_down() or ""))
paused["scheduler"] = False
paused["devices"] = {"thinkpad": {}, "gx10": {}}
check("the model's device paused -> stands down", "paused" in (C.stood_down() or ""))
paused["devices"] = {}

# main() with the switch off writes the facts and asks no model.
lanes["n"] = 0
asked = []
C.ask = lambda *a, **k: asked.append(a) or ("{}", "stop")
C.facts = lambda: {"scripts/corpus_scripts.py": {"sha256": "new", "run": None, "kind": "py", "referenced_count": 0,
                                                  "referenced_by": [], "mentioned_in_docs": 0, "commits": 1,
                                                  "first_seen": "", "last_changed": ""}}
written = []
C.write = lambda rows, prev: written.append(rows)
real_out = C.OUT
C.OUT = Path("/nonexistent/scripts.json")
sys.argv = ["corpus_scripts.py"]
try:
    C.main()
finally:
    C.OUT = real_out
check("stood down -> no model is asked", asked == [])
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
