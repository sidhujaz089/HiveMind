#!/usr/bin/env python3
"""What is this thing? One lookup, assembled from the stores that already own the answer (I3a).

    python3 scripts/describe.py <name>          # script, skill, task, page, role, or network entity
    python3 scripts/describe.py --kinds         # what it can look up

A session can search -- `symindex`, `recall`, `transcripts`, `grep` -- but nothing answered *"there is a
thing called X: what is it, who owns it, what keeps it honest, and what do I run to act on it?"* without
opening a file. That is the gap behind Jas's "progressive disclosure": not a lack of search, a lack of a
cheap lookup.

**IT COPIES NOTHING.** Every answer is read at call time from the store that already owns it, and the
answer names that store. A second copy of "what X is" would be a second truth, and this repository has
paid for those all day: the identity file told one instance another's name, the chart said three deleted
scripts existed, and a cache answered a question it never held.

    script   scripts/library.json        + the chart (which task it serves, from FLOW.json helpers)
    skill    docs/skills/<name>/SKILL.md front matter
    task     workspace/tasks/<id>--*.md  (title, status, assignment)
    page     projects/app-repo/hub_common.py PAGES
    role     projects/local-inference/models/ROLES.json
    network  workspace/network.json

An unknown name is NOT FOUND. It is not "did you mean", because a lookup that guesses is worse than one
that says no -- and the near-miss is exactly how a session ends up confidently describing the wrong thing.
"""
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "app-repo"))

LIBRARY = REPO / "scripts" / "library.json"
FLOW = REPO / "projects" / "local-inference" / "models" / "FLOW.json"
ROLES = REPO / "projects" / "local-inference" / "models" / "ROLES.json"
NETWORK = REPO / "workspace" / "network.json"
TASKS = Path(os.environ.get("DESCRIBE_TASKS") or (REPO / "workspace" / "tasks"))
SKILLS = REPO / "docs" / "skills"


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _script(name):
    lib = _json(LIBRARY)
    scripts = (lib or {}).get("scripts") or {}
    key = name if name in scripts else next((k for k in scripts if Path(k).name == name), None)
    if not key:
        return None
    what = str((scripts.get(key) or {}).get("what") or "").strip()
    serves = []
    flow = _json(FLOW) or {}
    for h in flow.get("helpers") or []:
        if h.get("script") == key:
            serves = h.get("serves") or []
            what = what or str(h.get("what") or "")
    if not what:
        # NO `what` IN THE LIBRARY IS NOT "NOTHING TO SAY". Read the script's own docstring -- it is the
        # file the library entry is about, so this is still pointing at a source rather than inventing one.
        try:
            text = (REPO / key).read_text(encoding="utf-8", errors="replace")
            m = re.search(r'^\s*(?:#!.*\n)?\s*(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', text, re.S)
            if m:
                what = " ".join(m.group(1).strip().splitlines()[0:2]).strip()[:300]
        except OSError:
            what = ""
    src = f"{key} (scripts/library.json)"
    if serves:
        src += f"; serves {', '.join(serves)} (FLOW.json helpers)"
    return {"kind": "script", "what": what, "source": src,
            "run": f"python3 {key}", "owner": "the tasks it serves" if serves else "no task declared"}


def _skill(name):
    f = SKILLS / name / "SKILL.md"
    if not f.is_file():
        return None
    text = f.read_text(encoding="utf-8", errors="replace")
    fm = {}
    for k in ("name", "description", "kind", "applies_when"):
        m = re.search(rf"^{k}:\s*(.+)$", text, re.M)
        if m:
            fm[k] = m.group(1).strip()
    return {"kind": "skill", "what": fm.get("description", ""), "source": f"docs/skills/{name}/SKILL.md",
            "run": f"python3 scripts/skills.py show {name}",
            "owner": "the tasks and roles it is attached to", "applies_when": fm.get("applies_when", "")}


def _task(name):
    if not re.match(r"^t\d+$", name):
        return None
    hits = sorted(TASKS.glob(f"{name}--*.md"))
    if not hits:
        return None
    text = hits[0].read_text(encoding="utf-8", errors="replace")
    def field(k):
        m = re.search(rf"^{k}:\s*(.+)$", text, re.M)
        return m.group(1).strip() if m else ""
    try:
        where = str(hits[0].relative_to(REPO))
    except ValueError:                     # a store pointed outside the repository (a test's temp dir)
        where = str(hits[0])
    return {"kind": "task", "what": field("title"), "source": where,
            "status": field("status"), "assigned": field("assigned"),
            "run": f"python3 projects/app-repo/taskstore.py show {name}",
            "owner": field("assigned") or "production"}


def _page(name):
    import hub_common
    e = hub_common.PAGES.get(name)
    if not e:
        return None
    return {"kind": "page", "what": e.get("what", ""),
            "source": "projects/app-repo/hub_common.py PAGES[" + name + "]",
            "run": f"the Hub tab; its own help: /help?p={name}",
            "owner": "Jas",
            "views": "; ".join(e.get("source") or []),
            "honest": "; ".join(e.get("enforce") or [])}


def _role(name):
    d = _json(ROLES) or {}
    r = (d.get("roles") or {}).get(name)
    if not r:
        return None
    return {"kind": "role", "what": r.get("what", ""), "source": "ROLES.json roles." + name,
            "run": "the Hub's Models/Roles tab",
            "owner": "Jas", "model": r.get("model"),
            "tasks": ", ".join(t.get("task", "") for t in (r.get("tasks") or [])) or "(none)"}


def _network(name):
    d = _json(NETWORK) or {}
    e = next((x for x in (d.get("entities") or []) if isinstance(x, dict) and x.get("id") == name), None)
    if not e:
        return None
    src = e.get("source") or {}
    where = (src.get("file") or src.get("command") or "this file")
    return {"kind": "network", "what": e.get("what", ""), "source": f"workspace/network.json -> {where}",
            "run": "the Hub's Network tab (/network)",
            "owner": e.get("owner"), "state": e.get("state"), "reached_by": e.get("reached_by")}


KINDS = (("script", _script), ("skill", _skill), ("task", _task), ("page", _page),
         ("role", _role), ("network", _network))


def describe(name):
    """{kind, what, source, ...} for the first kind that knows the name, or a NOT FOUND answer."""
    name = (name or "").strip()
    if not name:
        return {"kind": None, "what": "nothing to look up"}
    for kind, fn in KINDS:
        try:
            got = fn(name)
        except Exception as e:                                  # noqa: BLE001 -- reported, not swallowed
            return {"kind": None, "what": f"the {kind} store could not be read ({type(e).__name__}: {e})"}
        if got:
            return got
    return {"kind": None,
            "what": f"not found: no script, skill, task, page, role or network entity is called {name!r}. "
                    f"This is an answer, not a near-miss -- try `--kinds` to see what is looked up."}


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    if argv[0] == "--kinds":
        print("looks up: " + ", ".join(k for k, _ in KINDS))
        return 0
    d = describe(" ".join(argv))
    print(f"  {d.get('kind') or 'UNKNOWN'}")
    for k in ("what", "status", "assigned", "owner", "model", "tasks", "state", "reached_by",
              "applies_when", "views", "honest", "run", "source"):
        if d.get(k):
            print(f"  {k.replace('_', ' '):12} {d[k]}")
    return 0 if d.get("kind") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
