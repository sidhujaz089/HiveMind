#!/usr/bin/env python3
"""t732: scope_lint must refuse a scope whose chunks carry no `flow` field naming a
node that exists in projects/local-inference/models/FLOW.json.

RED ON MAIN today for the stated reason: scripts/scope_lint.py never reads FLOW.json,
so a scope whose chunks have no flow field lints clean. The chart says so itself --
projects/local-inference/models/FLOW.json:2, "DRAFT: nothing routes by it yet" -- and
its tasks array (FLOW.json:4-932) contains no node id "oracle", the procedure this
task puts on the chart.

Run: python3 scripts/tests/test_scope_lint_frozen.py
(no pytest -- running it runs every assertion; exits non-zero on a failure)
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # scripts/tests/ -> repo root
LINT = ROOT / "scripts" / "scope_lint.py"
FLOW = ROOT / "projects" / "local-inference" / "models" / "FLOW.json"
BOGUS = "definitely-not-a-node-zz9"

FAILURES = []


def check(cond, msg):
    print(("ok:   " if cond else "FAIL: ") + msg)
    if not cond:
        FAILURES.append(msg)


def node_ids():
    """The real chart's task ids, read from the file on disk -- not a hard-coded list,
    so the fixture follows the chart as it is edited."""
    data = json.loads(FLOW.read_text())
    return [t.get("id") for t in data.get("tasks", [])
            if isinstance(t, dict) and t.get("id")]


def chunk(flow_value):
    """One builder-facing chunk, shaped the way a real scope shapes one (this task's
    own chunks use exactly this file/target pair)."""
    item = {
        "kind": "implementation",
        "file": "scripts/scope_lint.py",
        "target": "scripts/tests/test_scope_lint_frozen.py",
        "single_change": "Every implementation chunk names the flow-chart node its work lands on.",
        "what_is_wrong": "scope_lint.py does not consult FLOW.json, so a scope can be frozen for a procedure the chart has no node for.",
        "done_when": "python3 scripts/tests/test_scope_lint_frozen.py exits 0",
        "related_entries": ["brief-unsatisfiable"],
        "symbols": ["flow_node_ids (new)"],
    }
    if flow_value is not None:
        item["flow"] = flow_value
    return item


def answer(entries):
    """A Manager answer in the shape bridge.py consumes: prose plus a fenced chunks block."""
    return "## For Claude\n\n```chunks\n" + json.dumps(entries, indent=1) + "\n```\n"


def run_lint(text, tmp, name):
    """Invoke the documented CLI. Findings are read from the printed lines, never the
    exit code, because scope_lint's exit-code convention is not part of its contract
    here."""
    path = Path(tmp) / name
    path.write_text(text)
    proc = subprocess.run([sys.executable, str(LINT), str(path), "--task", "t732"],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    return proc.stdout.splitlines() + proc.stderr.splitlines()


def main():
    ids = node_ids()
    check(bool(ids), "the real FLOW.json parses and lists at least one task node id")
    if not ids:
        print("%d assertion(s) failed" % len(FAILURES))
        return 1
    known = "scope_check" if "scope_check" in ids else ids[0]

    with tempfile.TemporaryDirectory() as tmp:
        without = run_lint(answer([chunk(None)]), tmp, "scope_a.md")
        withname = run_lint(answer([chunk(known)]), tmp, "scope_b.md")
        bogus = run_lint(answer([chunk(BOGUS)]), tmp, "scope_c.md")

        # 1. a scope with no flow field is a finding, not silence
        check("clean" not in [line.strip().lower() for line in without],
              "a scope whose chunk has no flow field is not reported clean")
        # 2. the finding says which field -- so the Manager can act on it
        check(any("flow" in line.lower() for line in without),
              "a finding names the missing flow field")
        # 3. naming a real node id satisfies it: the check is satisfiable, not a
        #    blanket refusal, and a broken invocation cannot fake it (identical
        #    output for both fixtures leaves the difference set empty)
        dropped = sorted(set(without) - set(withname))
        check(any("flow" in line.lower() for line in dropped),
              "naming a real FLOW.json node removes the flow finding (removed: %s)"
              % (dropped[:3],))
        # 4. a string is not enough -- it must be a node id that exists in FLOW.json
        check(any("flow" in line.lower() for line in bogus),
              "a flow name that is not a FLOW.json node id is still refused")

    print("%d assertion(s) failed" % len(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
