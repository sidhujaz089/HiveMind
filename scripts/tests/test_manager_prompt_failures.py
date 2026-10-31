#!/usr/bin/env python3
"""t366 step d: the Manager's scoping prompt shows the failure classes, most-seen first."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import manager_prompt as M  # noqa: E402

root = Path(tempfile.mkdtemp())
(root / "workspace").mkdir()
(root / "workspace" / "failure-schema.json").write_text(json.dumps({"failures": [
    {"class": "rare", "cause_category": "content", "instances": [{}]},
    {"class": "common", "cause_category": "harness-capability", "instances": [{}, {}, {}], "defense": "do X"},
    {"class": "unclassified", "cause_category": "unclassified", "instances": [{}] * 9}]}))
t = M.failure_classes(root)
ok = (t.index("- common") < t.index("- rare") and "DEFENSE -- do X" in t
      and "no defense written yet" in t and "unclassified" not in t.split("\n", 2)[-1])
print("test_manager_prompt_failures:", "ok" if ok else "FAILED\n" + t)
sys.exit(0 if ok else 1)
