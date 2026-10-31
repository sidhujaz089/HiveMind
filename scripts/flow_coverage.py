#!/usr/bin/env python3
"""Is every permanent script on the Flow chart? The chart is the system of record.

    python3 scripts/flow_coverage.py             # scripts not on the chart, and the count
    python3 scripts/flow_coverage.py --json
    python3 scripts/flow_coverage.py --check <path>   # exit 0 if that script is on the chart

WHY (Jas, 2026-10-01): "we should establish a more structured control mechanism where the
flowchart serves as the system of record. Under this approach, any new workflow or process must
first be defined in the flowchart before it can be implemented or executed elsewhere ... any
permanent script must be represented in the flowchart, and if it is not documented there, it
should not be eligible for approval or deployment." Measured that day: 57 of 222 registered
non-test scripts were named anywhere in FLOW.json -- dispatch_loop.task_of(), the chunk-to-task
helper, among the missing.

ON THE CHART means one of: named by a task (worker / command / queue / note), a link (mover), a
source (mover) or a service (unit) -- OR listed in FLOW.json's `helpers`, where each supporting
script says which task(s) it serves and what it does. A PERMANENT script is any script in
scripts/library.json whose kind is not `test` (a test is the evidence for a script, not a process).

Who enforces it: library.py refuses to REGISTER an off-chart script (a script that is not
registered cannot run -- the deployment gate); truncation_review.py records an `off-flow` finding
without asking a model; check_workspace.py fails while any registered script is off the chart;
session_review.py asks the reviewer for processes the chart does not show.
"""
import json
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

REPO = Path(__file__).resolve().parent.parent
FLOW = REPO / "projects" / "local-inference" / "models" / "FLOW.json"
LIBRARY = REPO / "scripts" / "library.json"
EXEMPT_KINDS = {"test"}
FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _text_fields(flow):
    """Every string the chart's tasks, links, sources and services say, joined."""
    out = []
    for sec, keys in (("tasks", ("worker", "command", "queue", "note")), ("links", ("mover",)),
                      ("sources", ("mover",)), ("services", ("unit", "name"))):
        for x in flow.get(sec, []):
            out += [str(x.get(k) or "") for k in keys]
    return "\n".join(out)


def named(flow):
    """{script stem} named by the chart's own boxes and arrows -- not counting `helpers`."""
    return set(re.findall(r"([A-Za-z0-9_][A-Za-z0-9_.-]*?)(?:\.py|\.sh)\b", _text_fields(flow))) | \
        set(re.findall(r"\b([a-z][a-z0-9_]+)\.[a-z_]+\(", _text_fields(flow)))


def helper_paths(flow):
    return {h.get("script") for h in flow.get("helpers", []) if h.get("script")}


def on_chart(path, flow=None):
    flow = flow if flow is not None else json.loads(FLOW.read_text(encoding="utf-8"))
    path = str(path)
    if path in helper_paths(flow):
        return True
    stem = Path(path).name.rsplit(".", 1)[0]
    return stem in named(flow)


def permanent(library=None):
    lib = library if library is not None else json.loads(LIBRARY.read_text(encoding="utf-8"))["scripts"]
    return sorted(p for p, v in lib.items() if v.get("kind") not in EXEMPT_KINDS
                  and "/tests/" not in p)


def uncovered(flow=None, library=None):
    flow = flow if flow is not None else json.loads(FLOW.read_text(encoding="utf-8"))
    return [p for p in permanent(library) if not on_chart(p, flow)]


def _record_fields_entries(flow):
    """The ownership entries the chart declares, tolerating charts predating record_fields."""
    if not isinstance(flow, dict):
        return []
    entries = flow.get("record_fields")
    return entries if isinstance(entries, list) else []


def _is_record_path(value):
    """A repo-relative record path: .jsonl/.json, not absolute, not escaping the repo."""
    if not isinstance(value, str) or not value:
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]  # strip the explicit repo-relative marker, not the record
    if PurePosixPath(normalized).is_absolute() or PureWindowsPath(value).is_absolute():
        return False
    if any(part == ".." for part in PurePosixPath(normalized).parts):
        return False
    return normalized.endswith(".jsonl") or normalized.endswith(".json")


def record_fields(flow=None):
    """{(artefact, field): node id} as declared by the chart; never by scanning prose."""
    flow = flow if flow is not None else json.loads(FLOW.read_text(encoding="utf-8"))
    out = {}
    for entry in _record_fields_entries(flow):
        if not isinstance(entry, dict):
            continue
        node = entry.get("node")
        artefact = entry.get("artefact")
        fields = entry.get("fields", [])
        if not isinstance(fields, list):
            continue
        for field in fields:
            if isinstance(artefact, str) and isinstance(field, str):
                out[(artefact, field)] = node
    return out


def field_owner(artefact=None, field=None, flow=None):
    """The node id declared to own `field` on `artefact`, answering from record_fields() only."""
    flow = flow if flow is not None else json.loads(FLOW.read_text(encoding="utf-8"))
    try:
        return record_fields(flow).get((artefact, field))
    except TypeError:
        # A non-hashable artefact/field (e.g. a chart dict passed as the artefact) is not a key.
        return None


def field_problems(flow=None):
    """Findings in the chart's ownership block; [] when the block is well-formed."""
    flow = flow if flow is not None else json.loads(FLOW.read_text(encoding="utf-8"))
    if not isinstance(flow, dict):
        return []
    tasks = flow.get("tasks")
    task_ids = {
        t.get("id") for t in (tasks if isinstance(tasks, list) else [])
        if isinstance(t, dict) and isinstance(t.get("id"), str)
    }
    problems = []
    owners = {}
    for i, entry in enumerate(_record_fields_entries(flow)):
        if not isinstance(entry, dict):
            problems.append(f"record_fields[{i}]: entry is not an object")
            continue
        node = entry.get("node")
        node_ok = isinstance(node, str) and node in task_ids
        if not node_ok:
            problems.append(f"record_fields[{i}]: node {node!r} is not the id of a task")
        artefact = entry.get("artefact")
        artefact_ok = _is_record_path(artefact)
        if artefact in (None, ""):
            problems.append(f"record_fields[{i}]: entry is missing an artefact")
        elif not artefact_ok:
            problems.append(f"record_fields[{i}]: artefact {artefact!r} is not a repo-relative .jsonl/.json path")
        fields = entry.get("fields")
        if not isinstance(fields, list):
            problems.append(f"record_fields[{i}]: fields must be a list")
            fields = []
        if not fields:
            problems.append(f"record_fields[{i}]: artefact {artefact!r} has an empty fields list")
        seen = set()
        for field in fields:
            if not isinstance(field, str):
                problems.append(f"record_fields[{i}]: field {field!r} on {artefact!r} is not a string")
                continue
            field_ok = bool(FIELD_NAME.match(field))
            if not field_ok:
                problems.append(f"record_fields[{i}]: field {field!r} on {artefact!r} is not a lowercase column name")
            if field in seen:
                problems.append(f"record_fields[{i}]: field {field!r} is repeated on {artefact!r}")
            else:
                seen.add(field)
            if node_ok and artefact_ok and field_ok:
                key = (artefact, field)
                previous = owners.get(key)
                if previous is None:
                    owners[key] = node
                elif previous != node:
                    problems.append(f"field {field!r} on {artefact!r} is owned by two nodes: {previous!r} and {node!r}")
    return problems


def main(argv):
    if argv[:1] == ["--check"] and len(argv) > 1:
        ok = on_chart(argv[1])
        print(f"{argv[1]}: {'on the Flow chart' if ok else 'NOT on the Flow chart'}")
        return 0 if ok else 1
    if argv[:1] == ["--field"]:
        args = argv[1:]
        flow_path = FLOW
        pos = []
        i = 0
        while i < len(args):
            if args[i] == "--flow":
                if i + 1 >= len(args):
                    print("flow_coverage.py --field: --flow needs a chart path")
                    return 2
                flow_path = Path(args[i + 1])
                i += 2
                continue
            pos.append(args[i])
            i += 1
        if len(pos) < 2:
            print("usage: flow_coverage.py --field <artefact> <field> [--flow <path>]")
            return 2
        artefact, field = pos[0], pos[1]
        if not flow_path.exists():
            print(f"flow_coverage.py --field: no such chart: {flow_path}")
            return 2
        try:
            flow = json.loads(flow_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"flow_coverage.py --field: cannot read chart {flow_path}: {e}")
            return 2
        owner = field_owner(artefact, field, flow)
        if owner is None:
            print(f"{artefact}: field {field!r}: NOT STATED in {flow_path}")
            return 1
        print(owner)
        return 0
    miss = uncovered()
    total = len(permanent())
    if "--json" in argv:
        print(json.dumps({"permanent": total, "uncovered": miss}, indent=1))
        return 0
    print(f"{total - len(miss)} of {total} permanent script(s) on the Flow chart")
    for p in miss:
        print(f"  NOT ON THE CHART  {p}")
    return 1 if miss else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
