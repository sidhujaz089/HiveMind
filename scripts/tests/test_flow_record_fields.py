#!/usr/bin/env python3
"""t820: the Flow chart must answer the FIELD question, not only the path question.

RCA workspace/rca/7770a2ed495c.md. The quoted change ("the port on ledger rows, and the
endpoint and lease on the launched job") added COLUMNS to records the dispatcher writes.
scripts/flow_coverage.py answers "is this file path on the chart" (on_chart,
flow_coverage.py:57-63), so it says COVERED for anything dispatch_loop.py does -- and the
reviewer's question has no command that can answer it. A fact the chart cannot state can
never be missing from it.

RED TODAY BECAUSE: scripts/flow_coverage.py defines no record_fields(), field_owner() or
field_problems(), and has no --field mode: main() recognises --check and --json only
(flow_coverage.py:77-90).

Seeded with this session's exact phrase, "the port on ledger rows": the pair
("workspace/dispatch-loop.jsonl", "port") must resolve to an owning node -- from DATA in the
chart, never from the word appearing in prose.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

FAILS = []

# A chart of our own making: the oracle must not depend on the real FLOW.json, so that the
# code chunk can turn this suite green without touching that file.
CHART = {
    "tasks": [
        {"id": "dispatch", "name": "Dispatch", "worker": "dispatch_loop.py",
         "command": "dispatch_loop.py", "queue": "", "note": "", "outcomes": [], "ends": [],
         "about": "the port on ledger rows is discussed here in prose, which is NOT ownership"},
        {"id": "preflight", "name": "Preflight", "worker": "Senior",
         "command": "preflight_brief.py", "queue": "senior_slot", "note": "",
         "outcomes": [], "ends": []},
    ],
    "links": [], "sources": [], "services": [], "helpers": [],
    "record_fields": [
        {"node": "dispatch", "artefact": "workspace/dispatch-loop.jsonl",
         "fields": ["task", "brief", "lane", "station", "model", "port"]},
        {"node": "dispatch", "artefact": "workspace/run-logs/who-works.json",
         "fields": ["endpoint", "lease", "task"]},
        {"node": "preflight", "artefact": "workspace/run-logs/ledger.jsonl",
         "fields": ["task", "verdict"]},
    ],
}

# Same chart with "port" dropped from the ownership block -- it survives only in the task's
# `about` text. A grep over the chart's strings reports it owned; the chart must not.
PROSE_ONLY = json.loads(json.dumps(CHART))
PROSE_ONLY["record_fields"][0]["fields"] = ["task", "brief", "lane", "station", "model"]


def ck(label, ok):
    print(f"  [{'ok' if ok else 'BAD'}]  {label}")
    if not ok:
        FAILS.append(label)


def chart_with(entries):
    f = json.loads(json.dumps(CHART))
    f["record_fields"] = entries
    return f


def run_api(FC, record_fields, field_owner, field_problems):
    declared = [(e["artefact"], f) for e in CHART["record_fields"] for f in e["fields"]]
    ck(f"every one of the {len(declared)} declared (artefact, field) pairs has an owner",
       all(field_owner(a, f, CHART) for a, f in declared))

    ck('the seeded pair -- "the port on ledger rows" -- resolves to the dispatcher node',
       field_owner("workspace/dispatch-loop.jsonl", "port", CHART) == "dispatch")
    ck('"lease" on workspace/run-logs/who-works.json resolves to the dispatcher node',
       field_owner("workspace/run-logs/who-works.json", "lease", CHART) == "dispatch")
    ck("a field is answered per ARTEFACT: 'lease' on the ledger row is not owned",
       not field_owner("workspace/dispatch-loop.jsonl", "lease", CHART))
    ck("a field the chart never states is answered, not raised",
       field_owner("workspace/dispatch-loop.jsonl", "definitely_not_a_column", CHART) is None)

    ck("a field named ONLY in a task's prose is NOT ownership (anti-grep assertion)",
       field_owner("workspace/dispatch-loop.jsonl", "port", PROSE_ONLY) is None)
    keys = record_fields(PROSE_ONLY)
    pairs = set(keys) if isinstance(keys, (list, set)) else set(keys.keys()) if isinstance(keys, dict) else set()
    ck("...and is absent from the ownership map",
       ("workspace/dispatch-loop.jsonl", "port") not in pairs)

    ck("a well-formed chart reports no ownership problems", field_problems(CHART) == [])
    cases = [
        ("an ownership claim pointing at a node the chart does not define",
         [{"node": "ghost", "artefact": "workspace/x.jsonl", "fields": ["a"]}]),
        ("an entry that names no field at all",
         [{"node": "dispatch", "artefact": "workspace/x.jsonl", "fields": []}]),
        ("the same field owned by two nodes",
         [{"node": "dispatch", "artefact": "workspace/x.jsonl", "fields": ["a"]},
          {"node": "preflight", "artefact": "workspace/x.jsonl", "fields": ["a"]}]),
        ("an artefact that is prose, not a repo-relative path",
         [{"node": "dispatch", "artefact": "the ledger", "fields": ["a"]}]),
        ("an artefact that is not a record file (.jsonl/.json)",
         [{"node": "dispatch", "artefact": "workspace/notes.md", "fields": ["a"]}]),
        ("a field that is not a lowercase column name",
         [{"node": "dispatch", "artefact": "workspace/x.jsonl", "fields": ["The Port"]}]),
        ("a field that is not a string",
         [{"node": "dispatch", "artefact": "workspace/x.jsonl", "fields": [7]}]),
        ("a field repeated inside one entry",
         [{"node": "dispatch", "artefact": "workspace/x.jsonl", "fields": ["a", "a"]}]),
    ]
    for label, entries in cases:
        try:
            problems = field_problems(chart_with(entries))
        except Exception as e:                                  # a finding, not a crash
            ck(f"{label}: reported as a string, not raised: {e!r}", False)
            continue
        ck(f"field_problems() reports {label}", any(str(p).strip() for p in problems))

    ck("field_problems() reports a finding as readable text",
       all(isinstance(p, str) for p in
           field_problems(chart_with([{"node": "ghost", "artefact": "workspace/x.jsonl",
                                       "fields": ["a"]}]))))


def run_cli():
    """The field question must be a COMMAND, with an answer a person can check by hand."""
    script = str(ROOT / "scripts" / "flow_coverage.py")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "FLOW.json"
        f.write_text(json.dumps(CHART), encoding="utf-8")
        r = subprocess.run([sys.executable, script, "--field",
                            "workspace/dispatch-loop.jsonl", "port", "--flow", str(f)],
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        ck(f'--field answers the seeded question with its owning node (exit {r.returncode}): '
           f'{out[:160]}', r.returncode == 0 and "dispatch" in out)
        r = subprocess.run([sys.executable, script, "--field",
                            "workspace/dispatch-loop.jsonl", "definitely_not_a_column",
                            "--flow", str(f)], capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        ck(f"--field says NOT STATED, and exits non-zero, for a field no node owns "
           f"(exit {r.returncode}): {out[:160]}", r.returncode != 0)
        r = subprocess.run([sys.executable, script, "--field", "workspace/x.jsonl", "a",
                            "--flow", str(Path(td) / "absent-FLOW.json")],
                           capture_output=True, text=True, timeout=120)
        ck("--field on a missing chart file fails loudly, not silently", r.returncode != 0)


def run_regressions(FC):
    ck("the path question still works: on_chart(path, flow)",
       FC.on_chart("projects/local-inference/dispatch_loop.py", CHART) is True)
    ck("on_chart still answers NO for a script the chart never names",
       FC.on_chart("scripts/definitely_not_on_chart.py", CHART) is False)
    ck("uncovered() still computes the gap from a library dict",
       FC.uncovered(CHART, {"scripts/orphan.py": {"kind": "tool"}}) == ["scripts/orphan.py"])
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "flow_coverage.py"), "--json"],
                       capture_output=True, text=True, timeout=120)
    ck(f"--json still emits the census (exit {r.returncode})", r.returncode in (0, 1)
       and "uncovered" in r.stdout)


def report():
    for f in FAILS:
        print("FAIL:", f)
    print("test_flow_record_fields:", "FAILED" if FAILS else "ok")
    return 1 if FAILS else 0


def main():
    try:
        import flow_coverage as FC
    except Exception as e:
        ck(f"scripts/flow_coverage.py imports: {e!r}", False)
        return report()

    api = [(n, getattr(FC, n, None)) for n in ("record_fields", "field_owner", "field_problems")]
    for name, fn in api:
        ck(f"{name}() is defined in scripts/flow_coverage.py", callable(fn))
    if all(callable(fn) for _, fn in api):
        d = dict(api)
        for name, fn in api:
            for args in ((), (CHART,)):
                try:
                    fn(*args)
                except Exception as e:
                    ck(f"{name}(*{args[:1]}) does not raise on a plain chart dict: {e!r}", False)
        run_api(FC, d["record_fields"], d["field_owner"], d["field_problems"])
    run_cli()
    run_regressions(FC)
    return report()


if __name__ == "__main__":
    sys.exit(main())
