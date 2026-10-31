#!/usr/bin/env python3
"""An unknown tool call is judged by what the model does next (Jas, 2026-10-17): corrected by a real
tool taking the same arguments -> recorded corrected; no correction within MISS_WINDOW calls -> recorded
and escalated. No list of known wrong names is involved."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "projects" / "local-inference"))
import json  # noqa: E402
import console as c  # noqa: E402

FAILED = 0


def report(name, ok):
    global FAILED
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


with tempfile.TemporaryDirectory() as t:
    c.UNKNOWN_TOOL_LOG = Path(t) / "o.jsonl"
    filed = []
    c.escalate = lambda subject, body, **k: filed.append(subject)
    c.RUN["write_file"] = lambda a: "wrote"
    c.RUN["read_file"] = lambda a: "text"
    r = c.run_tool("run_file", {"path": "x", "content": "y"})
    report("the refusal lists the real tools", "no tool called run_file" in r and "write_file" in r)
    report("and points at none of them", "use write_file" not in r)
    c.run_tool("write_file", {"path": "x", "content": "y"})
    c.run_tool("zap_thing", {"target": "q"})
    for _ in range(c.MISS_WINDOW):
        c.run_tool("read_file", {"path": "README.md"})
    rows = [json.loads(x) for x in c.UNKNOWN_TOOL_LOG.read_text().splitlines()]
    report("a same-argument real call counts as corrected",
           any(r["unknown"] == "run_file" and r["corrected_by"] == "write_file" for r in rows))
    report("no correction in the window is recorded uncorrected",
           any(r["unknown"] == "zap_thing" and r["corrected_by"] is None for r in rows))
    report("and only the uncorrected one is escalated", len(filed) == 1 and "zap_thing" in filed[0])
print(f"{'FAILED' if FAILED else 'ok'}")
sys.exit(1 if FAILED else 0)
