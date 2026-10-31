#!/usr/bin/env python3
"""The network map has ONE write path: this one.

    python3 scripts/network_change.py add <entity.json>          # a whole entity, validated
    python3 scripts/network_change.py set <id> <field> <value>   # one field of one entity
    python3 scripts/network_change.py retire <id> [why]          # keep it, marked retired
    python3 scripts/network_change.py log                        # the changelog
    python3 scripts/network_change.py revert <n>                 # restore row n's BEFORE
    python3 scripts/network_change.py guard                      # for the pre-commit hook

WHY THIS EXISTS (Jas, 2026-10-26: the network map must be "kept up to date ... just like the flowchart
tab"). The chart's own history is the argument: `FLOW-CHANGELOG.jsonl` has 82 consecutive rows and SIX
of them do not chain -- row N's `after` is not row N+1's `before` -- so the chart was changed outside
`flow_change.py` at least six times, and the changelog cannot be replayed to the present state. A
changelog nobody can replay is a log, not a ledger. The map gets its write path BEFORE it gets a page,
so the page is never the easiest way to change it.

EVERY WRITE IS VALIDATED, NOT JUST THE FIRST. The result is checked with
`check_workspace.network_map_problems` -- the same rule the bar runs -- so this tool cannot create an
entry the checker would refuse: no dangling `source.file`, no word outside the declared vocabulary, no
link naming an entity that is not there. A write that would break the map is REFUSED and the file is
left exactly as it was.

THE CHANGELOG CARRIES `before` AND `after`, whole, so a row can be replayed and reverted. `revert` is
itself a row, so a revert can be undone the same way.

No schedule, no service, no model: this is a tool a person or an agent runs.
"""
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NETWORK = REPO / "workspace" / "network.json"
LOG = REPO / "workspace" / "NETWORK-CHANGELOG.jsonl"

sys.path.insert(0, str(REPO / "scripts"))
import check_workspace  # noqa: E402  -- the ONE definition of a valid map


def _who(explicit=None):
    if explicit:
        return explicit
    if os.environ.get("DSH_SESSION_ID"):
        return "dsh-harness"
    return os.environ.get("NETWORK_BY") or "network_change"


def load(path=None):
    p = Path(path) if path else NETWORK
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"network.json could not be read ({type(e).__name__}: {e}) -- nothing written")


def _rows(path=None):
    p = Path(path) if path else LOG
    if not p.is_file():
        return []
    out, torn = [], 0
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError as e:                                  # noqa: BLE001 -- counted, not swallowed
            torn += 1
            first = str(e)[:120]
            continue
    if torn:
        # SAID, NOT SWALLOWED: a torn row and an absent row look identical to every caller, so the
        # count goes to stderr rather than into a silent skip.
        print(f"  NOTE: {p.name}: {torn} unreadable row(s) skipped -- first: {first}", file=sys.stderr)
    return out


def problems(doc, repo=None):
    """What the checker would say about this document."""
    return check_workspace.network_map_problems(doc, Path(repo) if repo else REPO)


def _write(after, before, who, what, ops=None, network=None, log=None):
    """Validate, then rewrite atomically and append the row. Refuses a map the checker would reject."""
    net = Path(network) if network else NETWORK
    lp = Path(log) if log else LOG
    bad = problems(after, net.parent.parent)
    if bad:
        raise SystemExit("REFUSED -- this change would make the map invalid:\n  "
                         + "\n  ".join(bad[:12]) + "\n  Nothing was written.")
    n = (max([r.get("n", 0) for r in _rows(lp)] or [0])) + 1
    row = {"n": n, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "who": who, "what": what, "ops": ops or [], "before": before, "after": after}
    tmp = net.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(after, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(net)
    with lp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return n


def _find(doc, eid):
    for i, e in enumerate(doc.get("entities") or []):
        if isinstance(e, dict) and e.get("id") == eid:
            return i
    return -1


def add(entity, who=None, network=None, log=None):
    doc = load(network)
    before = json.loads(json.dumps(doc))
    if not isinstance(entity, dict) or not entity.get("id"):
        raise SystemExit("REFUSED -- an entity needs at least an id; nothing written")
    if _find(doc, entity["id"]) >= 0:
        raise SystemExit(f"REFUSED -- {entity['id']} is already in the map; use `set` to change it")
    doc.setdefault("entities", []).append(entity)
    return _write(doc, before, _who(who), f"add {entity['id']} ({entity.get('kind', '?')})",
                  [{"op": "add", "id": entity["id"]}], network, log)


def set_field(eid, field, value, who=None, network=None, log=None):
    doc = load(network)
    before = json.loads(json.dumps(doc))
    i = _find(doc, eid)
    if i < 0:
        raise SystemExit(f"REFUSED -- no entity {eid} in the map")
    # A VALUE THAT LOOKS LIKE JSON IS JSON: state/kind are strings, joins is a list, verified is an
    # object. Anything else is taken literally, so `_note "a sentence"` works.
    try:
        parsed = json.loads(value)
    except ValueError:
        parsed = value
    old = doc["entities"][i].get(field)
    doc["entities"][i][field] = parsed
    return _write(doc, before, _who(who), f"{eid}: {field} -> {parsed!r}"[:180],
                  [{"op": "set", "id": eid, "field": field, "from": old, "to": parsed}], network, log)


def retire(eid, why="", who=None, network=None, log=None):
    doc = load(network)
    before = json.loads(json.dumps(doc))
    i = _find(doc, eid)
    if i < 0:
        raise SystemExit(f"REFUSED -- no entity {eid} in the map")
    doc["entities"][i]["state"] = "retired"
    if why:
        # SAID, NOT DELETED: how a thing left the network is the fact a future reader needs.
        doc["entities"][i]["_retired"] = why
    return _write(doc, before, _who(who), f"retire {eid}" + (f": {why}" if why else ""),
                  [{"op": "retire", "id": eid}], network, log)


def revert(n, who=None, network=None, log=None):
    rows = _rows(log)
    row = next((r for r in rows if r.get("n") == n), None)
    if not row:
        raise SystemExit(f"REFUSED -- no changelog row {n}")
    before = load(network)
    return _write(row["before"], before, _who(who),
                  f"revert of change {n} ({str(row.get('what', ''))[:120]})",
                  [{"op": "revert", "n": n}], network, log)


def seed(why="adopted: written before this tool existed", who=None, network=None, log=None):
    """Adopt a map that predates the tool, as row 1, so the changelog has a starting point.

    Row 1's `before` is null ON PURPOSE: there was no map before it, and pretending there was would
    make a replay look like it started from something. This is the only write that does not validate,
    because the alternative is a file nobody can ever commit again.
    """
    doc = load(network)
    lp = Path(log) if log else LOG
    if _rows(lp):
        raise SystemExit("REFUSED -- the changelog already has rows; seed is for the first one only")
    bad = problems(doc, (Path(network).parent.parent) if network else REPO)
    if bad:
        raise SystemExit("REFUSED -- the map is already invalid, so there is nothing to adopt:\n  "
                         + "\n  ".join(bad[:12]))
    row = {"n": 1, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "who": _who(who), "what": why, "ops": [{"op": "seed"}], "before": None, "after": doc}
    with lp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 1


def ignore(key, why, who=None, network=None, log=None):
    """Declare something real that the map deliberately does not model, WITH a reason.

    The reason is required: an ignore with no explanation is a silence, and the review that reads this
    file later cannot tell it from an oversight. `network_undeclared` treats a reasonless ignore as
    unaccounted, so this check has teeth in both places.
    """
    if not str(why or "").strip():
        raise SystemExit("REFUSED -- an ignore needs a reason; an ignore with no reason is a silence")
    doc = load(network)
    before = json.loads(json.dumps(doc))
    for i in doc.get("ignored") or []:
        if isinstance(i, dict) and i.get("key") == key:
            raise SystemExit(f"REFUSED -- {key} is already ignored")
    doc.setdefault("ignored", []).append({"key": key, "why": why.strip()})
    return _write(doc, before, _who(who), f"ignore {key}: {why.strip()}"[:180],
                  [{"op": "ignore", "key": key}], network, log)


def guard(network=None, log=None):
    """The pre-commit gate: the staged map must equal the newest changelog row's `after`.

    Returns a list of problems; empty means the file on disk was produced by this tool, which is the
    only thing a file can tell you about its own provenance.
    """
    doc = load(network)
    rows = _rows(log)
    if not rows:
        return ["workspace/network.json exists but NETWORK-CHANGELOG.jsonl has no rows -- it was not "
                "written by scripts/network_change.py. Use the tool: `network_change.py add` or `set`."]
    last = rows[-1]
    if last.get("after") != doc:
        return [f"workspace/network.json does not match the newest changelog row (n={last.get('n')}): "
                f"the file has {len(doc.get('entities') or [])} entities, the row's `after` has "
                f"{len(((last.get('after') or {}).get('entities')) or [])}. It was edited outside "
                f"scripts/network_change.py. Fix it with the tool, or revert row {last.get('n')}."]
    return []


USAGE = """network_change.py -- the one write path for workspace/network.json

  add <entity.json>            a whole entity, validated before it is written
  set <id> <field> <value>     one field (JSON values parsed: lists, objects, true/false)
  retire <id> [why]            mark it retired and keep the reason
  log                          the changelog, newest last
  revert <n>                   restore row n's BEFORE, as a new row
  guard                        exit 1 if the file was not produced by this tool (pre-commit)
  ignore <key> "<why>"         declare something real the map deliberately does not model
  seed ["why"]                 adopt a map that predates this tool, as changelog row 1
"""


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "add" and rest:
            print(f"  added; changelog row {add(json.loads(Path(rest[0]).read_text(encoding='utf-8')))}")
        elif cmd == "set" and len(rest) >= 3:
            print(f"  set; changelog row {set_field(rest[0], rest[1], ' '.join(rest[2:]))}")
        elif cmd == "retire" and rest:
            print(f"  retired; changelog row {retire(rest[0], ' '.join(rest[1:]))}")
        elif cmd == "log":
            for r in _rows():
                print(f"  {r.get('n'):3d} {r.get('at')} {r.get('who')}: {r.get('what')}")
        elif cmd == "revert" and rest:
            print(f"  reverted; changelog row {revert(int(rest[0]))}")
        elif cmd == "seed":
            print(f"  seeded; changelog row {seed(' '.join(rest) or '')}")
        elif cmd == "ignore" and len(rest) >= 2:
            print(f"  ignored; changelog row {ignore(rest[0], ' '.join(rest[1:]))}")
        elif cmd == "guard":
            probs = guard()
            for p in probs:
                print(f"  {p}")
            return 1 if probs else 0
        else:
            print(USAGE)
            return 2
    except SystemExit:
        raise
    except (OSError, ValueError) as e:
        print(f"REFUSED -- {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
