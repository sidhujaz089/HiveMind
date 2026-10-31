#!/usr/bin/env python3
"""t066: the Manager's prompt carries the classes its SCOPES were sent back for, most-seen first, each with the
lint's own rule. Rejected scopes were recorded and routed to RCA tasks but never reached the prompt; build failures
already came back through failure_classes(). Hermetic: a temp repo root with a fixture ledger."""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import manager_prompt as mp  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    print(f"  [{'ok' if cond else 'BAD'}]  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


now = time.time()
at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600))
old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 60 * 86400))
rows = ([{"at": at, "scope": f"s{i}", "class": "brief-anchor", "scope_fault": True, "evidence": f"e{i}"} for i in range(3)]
        + [{"at": at, "scope": "s9", "class": "oracle-green", "scope_fault": True, "evidence": "x"}] * 4   # one, recorded 4x
        + [{"at": at, "scope": "s8", "class": "harness", "scope_fault": False, "evidence": "not the scope"}]
        + [{"at": old, "scope": "s7", "class": "oracle-crash", "scope_fault": True, "evidence": "old"}])
d = Path(tempfile.mkdtemp())
(d / "workspace").mkdir()
(d / "workspace" / "scope-quality.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
txt = mp.scope_defect_classes(d, now=now)
lines = [l for l in txt.splitlines() if l.startswith("- ")]
ck("the most-seen class comes first, counted once per defect", lines[0].startswith("- brief-anchor (3 in 30 days, 3 in all)"), lines)
ck("a defect re-recorded on every dispatch counts once", any(l.startswith("- oracle-green (1 in 30 days, 1 in all)") for l in lines), lines)
ck("each line carries the lint's own rule", "is not defined in the file" in lines[0], lines[0])
ck("a defect that is not the scope's fault is left out", not any("harness" in l for l in lines), lines)
ck("a defect older than the window is KEPT, ranked last (nothing ages out)",
   lines[-1].startswith("- oracle-crash (0 in 30 days, 1 in all)"), lines)
ck("no ledger, no section", mp.scope_defect_classes(Path(tempfile.mkdtemp())) == "")
ck("the section is in the scoping prompt", "YOUR SCOPES HAVE BEEN SENT BACK" in mp.build_prompt("t999", root=d)
   if (d / "workspace" / "tasks").mkdir() is None else False)

print("test_manager_prompt_scope_defects:", "FAILED" if FAILS else "ok")
sys.exit(1 if FAILS else 0)
