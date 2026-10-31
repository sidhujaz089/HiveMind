#!/usr/bin/env python3
"""The scoping loop gets enough tool rounds to read what it scopes (t9549, 2026-10-29).

manager_queue.sh passed --max-rounds 6. Over 7 days, 88 of 283 scoping answers (31%) said the budget
ran out before the target file was read; each was escalated as BLOCKED and came back unchanged. The
floor is ask_q16's own default, read from its source, so the two cannot drift apart again.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sh = (ROOT / "scripts" / "manager_queue.sh").read_text(encoding="utf-8")
q16 = (ROOT / "scripts" / "ask_q16.py").read_text(encoding="utf-8")
got = [int(n) for n in re.findall(r"--max-rounds (\d+)", sh)]
floor = int(re.search(r'--max-rounds", type=int, default=(\d+)', q16).group(1))
ok = bool(got) and min(got) >= floor
print(f"  {'ok ' if ok else 'BAD'}  manager_queue.sh --max-rounds {got} >= ask_q16 default {floor}")
print("test_scoping_tool_rounds:", "ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
