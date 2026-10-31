#!/usr/bin/env python3
"""t820 (second half): the REAL chart must state the record fields its nodes write.

The code chunk is tested against synthetic charts. This one tests the census: the dispatcher's
outbound record shapes, transcribed from scripts/dispatch_loop.py's write points, recorded in
projects/local-inference/models/FLOW.json under "record_fields".

RED TODAY BECAUSE: FLOW.json has no "record_fields" key -- its top-level keys are _what,
_source, tasks, links, sources, services, _services, helpers, procedures.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / "projects" / "local-inference" / "models" / "FLOW.json"
ARTEFACT = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)+\.(jsonl|json)$")
FIELD = re.compile(r"^[a-z][a-z0-9_]*$")
FAILS = []


def ck(label, ok):
    print(f"  [{'ok' if ok else 'BAD'}]  {label}")
    if not ok:
        FAILS.append(label)


def report():
    for f in FAILS:
        print("FAIL:", f)
    print("test_flow_record_fields_populated:", "FAILED" if FAILS else "ok")
    return 1 if FAILS else 0


def main():
    try:
        data = json.loads(FLOW.read_text(encoding="utf-8"))
    except Exception as e:
        ck(f"FLOW.json is valid JSON after the change: {e!r}", False)
        return report()

    ck("FLOW.json is a JSON object", isinstance(data, dict))
    for k in ("tasks", "links", "sources", "services", "helpers"):
        ck(f"the chart keeps its '{k}' section", isinstance(data.get(k), list) and bool(data.get(k)))

    rf = data.get("record_fields")
    ck('FLOW.json has a "record_fields" list -- the fact the chart could not state before',
       isinstance(rf, list) and len(rf) >= 1)
    if not isinstance(rf, list) or not rf:
        return report()

    task_ids = {t.get("id") for t in data.get("tasks", []) if isinstance(t, dict) and t.get("id")}
    dispatcher = {t.get("id") for t in data.get("tasks", [])
                  if isinstance(t, dict)
                  and str(t.get("command", "")).split("/")[-1] == "dispatch_loop.py"}
    ck("a task on the chart runs dispatch_loop.py (the script the quoted change is in)",
       bool(dispatcher))

    ck("every entry is an object naming node, artefact and fields",
       all(isinstance(e, dict) and e.get("node") and e.get("artefact")
           and e.get("fields") is not None for e in rf))

    for e in [x for x in rf if isinstance(x, dict)]:
        node, art, fields = e.get("node"), e.get("artefact"), e.get("fields")
        ck(f"{art}: node {node!r} is a task id on the chart", node in task_ids)
        ck(f"{art}: is a repo-relative record path", isinstance(art, str)
           and bool(ARTEFACT.match(str(art))))
        ck(f"{art}: exists on disk", bool(art) and (ROOT / str(art)).exists())
        ck(f"{art}: fields is a non-empty list of lowercase names",
           isinstance(fields, list) and bool(fields)
           and all(isinstance(f, str) and FIELD.match(f) for f in fields))
        ck(f"{art}: no field is listed twice inside one entry",
           isinstance(fields, list) and len(set(fields)) == len(fields))

    pairs, dupes = {}, []
    for e in rf:
        if isinstance(e, dict):
            for f in (e.get("fields") or []):
                key = (e.get("artefact"), f)
                if key in pairs:
                    dupes.append((key, pairs[key], e.get("node")))
                pairs[key] = e.get("node")
    ck("no (artefact, field) is owned by two nodes", not dupes)

    artefacts = {e.get("artefact") for e in rf if isinstance(e, dict)}
    ck(f"at least two record shapes are declared ({len(artefacts)})", len(artefacts) >= 2)

    ck("the dispatcher's own node owns at least one record shape with >= 2 fields",
       any(isinstance(e, dict) and e.get("node") in dispatcher
           and isinstance(e.get("fields"), list) and len(e["fields"]) >= 2 for e in rf))

    quoted = {"port", "endpoint", "lease"}
    ck("the quoted change's referents are stateable: some entry declares port, endpoint or lease",
       any(isinstance(e, dict) and quoted.intersection(e.get("fields") or []) for e in rf))

    return report()


if __name__ == "__main__":
    sys.exit(main())
