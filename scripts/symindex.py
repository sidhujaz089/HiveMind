#!/usr/bin/env python3
"""Where is this defined, and who calls it — for the whole repository, from the AST.

    python3 scripts/symindex.py build
    python3 scripts/symindex.py find source_defaults
    python3 scripts/symindex.py files "the receipt parser drops malformed lines"

WHY THIS EXISTS
---------------
`coder.py` picked its starting context by counting keyword overlap between the task text and
the first 4,000 characters of each file. On `t143` that never surfaced the file that needed
changing, the model was handed six irrelevant files, and it invented a path — which read as a
hallucination and was a starved context.

Keyword overlap cannot know that `source_defaults` is *defined* in `check_deployed.py` and
*called* from two tests. The AST does, exactly, and Python ships it.

WHAT IT INDEXES
---------------
Every `def`, `async def` and `class` in the repository's Python, with its file, line,
signature and first docstring line — plus every place each name is referenced. Reference
counting is deliberately name-based rather than scope-resolved: `check_deployed.source_defaults`
and a local variable of the same name are indistinguishable here. For *finding the file to
open* that is enough, and full resolution is a different and much larger tool.

It skips what a sandbox skips anyway — vendored trees, caches, and `chat/`, which is the
personal corpus and is absent from the agent's world by design (`D-22`).

WHAT IT IS NOT
--------------
Not a call graph, not a type checker, not a rename tool. It answers two questions — *where is
this defined* and *what mentions it* — and says nothing else. Naming the boundary keeps the
next person from trusting it for refactoring.
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "workspace" / "symbol-index.json"

SKIP = re.compile(
    r"(^|/)(\.venv|venv|site-packages|dist-packages|node_modules|__pycache__|\.git"
    r"|build|\.gradle)/|^chat/")


def source_files(root=REPO):
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if SKIP.search(rel):
            continue
        yield rel, p


def build(root=REPO):
    """{name: {defs: [...], refs: [...]}} plus a per-file summary."""
    defs, refs, files = {}, {}, {}
    for rel, p in source_files(root):
        try:
            src = p.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError) as e:
            print(f"  NOTE: {rel} unreadable or does not parse ({e}) -- not indexed", file=sys.stderr)
            continue
        names = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "def"
                try:
                    sig = (f"{node.name}({ast.unparse(node.args)})"
                           if kind == "def" else node.name)
                except Exception:                       # noqa: BLE001
                    sig = node.name
                doc = (ast.get_docstring(node) or "").split("\n")[0][:100]
                defs.setdefault(node.name, []).append(
                    {"file": rel, "line": node.lineno, "kind": kind,
                     "sig": sig, "doc": doc})
                names.append(node.name)
            elif isinstance(node, ast.Name):
                refs.setdefault(node.id, set()).add(rel)
            elif isinstance(node, ast.Attribute):
                refs.setdefault(node.attr, set()).add(rel)
        files[rel] = {"lines": src.count("\n") + 1, "defines": names}
    return {"defs": defs,
            "refs": {k: sorted(v) for k, v in refs.items()},
            "files": files}


def load():
    if not INDEX.is_file():
        return None
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def find(name, idx=None):
    idx = idx or load() or build()
    return idx["defs"].get(name, []), idx["refs"].get(name, [])


def files_for(text, idx=None, limit=6):
    """The files most likely to matter for a task, ranked by SYMBOLS not keywords.

    A word in the task that names something defined in this repository is worth far more
    than a word that merely appears in a file. That distinction is the whole point: the old
    scorer could not tell `source_defaults` from `the`.
    """
    idx = idx or load() or build()
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text))
    score = {}
    for w in words:
        # Weight by RARITY. `check`, `class` and `messages` are English words that happen to
        # be identifiers here, and they appear everywhere -- unweighted they drowned the
        # signal and every candidate tied. A name defined in one file and mentioned in two
        # is worth far more than one mentioned in fifty.
        ds = idx["defs"].get(w, [])
        rs = idx["refs"].get(w, [])
        if ds:
            for d in ds:
                score[d["file"]] = score.get(d["file"], 0) + 30 / len(ds)
        if rs and len(rs) <= 12:                 # ubiquitous names carry no information
            for f in rs:
                score[f] = score.get(f, 0) + 6 / len(rs)
    # A file the task names outright outranks anything inferred.
    for m in re.findall(r"[\w/]+\.py", text):
        for f in idx["files"]:
            if f.endswith(m) or f == m:
                score[f] = score.get(f, 0) + 50
    return sorted(score.items(), key=lambda kv: -kv[1])[:limit]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("build")
    f = sub.add_parser("find")
    f.add_argument("name")
    q = sub.add_parser("files")
    q.add_argument("text")
    sub.add_parser("stats")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        idx = build()
        INDEX.parent.mkdir(parents=True, exist_ok=True)
        INDEX.write_text(json.dumps(idx, indent=1), encoding="utf-8")
        print(f"  {len(idx['defs']):,} name(s) defined across {len(idx['files'])} file(s)"
              f" -> {INDEX.relative_to(REPO)}")
        return 0

    if a.cmd == "find":
        d, r = find(a.name)
        if not d:
            print(f"  {a.name}: not defined in this repository")
        for x in d:
            print(f"  {x['file']}:{x['line']}  {x['kind']} {x['sig']}")
            if x["doc"]:
                print(f"      {x['doc']}")
        if r:
            print(f"  mentioned in {len(r)} file(s): {', '.join(r[:8])}"
                  + (" …" if len(r) > 8 else ""))
        return 0

    if a.cmd == "files":
        for rel, s in files_for(a.text):
            print(f"  {s:>4}  {rel}")
        return 0

    if a.cmd == "stats":
        idx = load()
        if not idx:
            print("  no index; run build")
            return 1
        big = sorted(idx["files"].items(), key=lambda kv: -kv[1]["lines"])[:5]
        print(f"  {len(idx['defs']):,} names, {len(idx['files'])} files")
        for rel, m in big:
            print(f"    {m['lines']:>5} lines  {rel}")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
