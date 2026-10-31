#!/usr/bin/env python3
"""The third retrieval layer: edges this repository already declares, parsed rather than inferred.

    python3 scripts/graph.py build                  parse every declared edge
    python3 scripts/graph.py neighbours L-114       what touches this
    python3 scripts/graph.py why L-116              the chain: lesson -> guard -> test -> mutations
    python3 scripts/graph.py history t159           every run, verdict and reason for a task
    python3 scripts/graph.py impact apply_ops       what breaks if this changes
    python3 scripts/graph.py stale                  edges pointing at things that no longer exist

WHY THIS IS CHEAP, AND WHY THAT MATTERS (`D-32`, amending `D-25`)
----------------------------------------------------------------
`D-25` deferred a graph because Chat's plan needed a LightRAG-style **extractor** -- an LLM
reading prose and inferring entities and relations. That objection was to extraction and it
was correct: an extractor can invent a relation that was never there, and you cannot tell from
the output which edges are real.

We do not need one. This repository has been hand-authoring its edges for two weeks in a fixed
syntax: `` `L-114` `` inside a lesson, `ENFORCEMENT: scripts/…`, `[SUPERSEDED … by L-114]`, the
suite named in every mutation, the `see:` field in every memory record. **265 of them.**

So this file is a parser, not a model. It can miss an edge that was written in an unexpected
shape -- and `stale` exists to surface that -- but it cannot fabricate one. That asymmetry is
the entire argument for building it now.

WHAT IT ANSWERS THAT THE OTHER LAYERS CANNOT
--------------------------------------------
Keyword finds exact tokens. Vectors find paraphrase. Neither can traverse:

    what guards this lesson?          L-116 -> selfmatch.py -> test_selfmatch.py -> 2 mutations
    has this been overturned?         L-112 -> superseded by L-114
    what did we try on t159?          5 runs, 3 verdicts, and why each was sent back
    what breaks if apply_ops changes? the suites and mutations that name it

The last is the cross-component class -- the shape behind `L-114`, `L-115` and the
branch-writer bug -- which a prober exercising one function at a time structurally cannot see.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "workspace" / "graph.json"


def _add(edges, src, rel, dst):
    if src and dst and src != dst:
        edges.append({"from": src, "rel": rel, "to": dst})


def build():
    """Every edge, from the syntax that already carries it. Nothing inferred."""
    edges = []

    # --- lessons -------------------------------------------------------------------
    lessons = REPO / "docs" / "LESSONS.md"
    if lessons.is_file():
        text = lessons.read_text(encoding="utf-8")
        blocks = re.split(r"\n(?=\*\*L-\d+ \[)", text)
        for b in blocks:
            m = re.match(r"\*\*(L-\d+) \[([A-Z]+)", b)
            if not m:
                continue
            lid, status = m.group(1), m.group(2)
            if status == "SUPERSEDED":
                for other in re.findall(r"by\s+`?(L-\d+)`?", b[:400]):
                    _add(edges, lid, "superseded_by", other)
            # every other lesson it cites
            for other in set(re.findall(r"`(L-\d+)`", b)) - {lid}:
                _add(edges, lid, "cites", other)
            # the guard that enforces it
            enf = re.search(r"ENFORCEMENT:(.{0,400})", b, re.S)
            if enf:
                for path in re.findall(r"`?((?:scripts|projects)/[\w/.\-]+\.(?:py|sh))`?",
                                       enf.group(1)):
                    _add(edges, lid, "enforced_by", path)

    # --- decisions -----------------------------------------------------------------
    dec = REPO / "docs" / "DECISIONS.md"
    if dec.is_file():
        for b in re.split(r"\n(?=### D-\d+)", dec.read_text(encoding="utf-8")):
            m = re.match(r"### (D-\d+)", b)
            if not m:
                continue
            did = m.group(1)
            for lid in set(re.findall(r"`(L-\d+)`", b)):
                _add(edges, did, "rests_on", lid)
            for other in set(re.findall(r"`(D-\d+)`", b)) - {did}:
                _add(edges, did, "amends", other)

    # --- mutations: registry entry -> the suite that must catch it -----------------
    mut = REPO / "scripts" / "mutate.py"
    if mut.is_file():
        for name, target, suite in re.findall(
                r'\("([\w\-]+)",\s*"([\w/.\-]+)",\s*\n?.*?\n?.*?\n?\s*"(scripts/tests/[\w.]+)"',
                mut.read_text(encoding="utf-8"), re.S):
            _add(edges, f"mutation:{name}", "mutates", target)
            _add(edges, f"mutation:{name}", "caught_by", suite)

    # --- memory records -> the document they point at ------------------------------
    memf = REPO / "workspace" / "memory.jsonl"
    if memf.is_file():
        for line in memf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                print(f"  NOTE: unreadable line in {memf} -- not graphed", file=sys.stderr)
                continue
            if r.get("see"):
                _add(edges, f"memory:{r['id']}", "see", r["see"])

    # --- runs and verdicts: the task's actual history -------------------------------
    for fname, rel in (("agent-runs.jsonl", "run_of"), ("review-verdicts.jsonl", "verdict_on")):
        f = REPO / "workspace" / fname
        if not f.is_file():
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                print(f"  NOTE: unreadable line {i + 1} in {f} -- not graphed", file=sys.stderr)
                continue
            task = r.get("task")
            if not task:
                continue
            node = f"{'run' if rel == 'run_of' else 'verdict'}:{task}:{r.get('at', i)}"
            _add(edges, node, rel, task)
            if r.get("served"):
                _add(edges, node, "by_model", r["served"])
            if r.get("branch"):
                _add(edges, node, "branch", r["branch"])
            if r.get("verdict"):
                _add(edges, node, "said", r["verdict"])
            if r.get("outcome"):
                _add(edges, node, "said", r["outcome"])

    return edges


def load():
    if OUT.is_file():
        return json.loads(OUT.read_text())
    return build()


def index(edges):
    fwd, rev = defaultdict(list), defaultdict(list)
    for e in edges:
        fwd[e["from"]].append(e)
        rev[e["to"]].append(e)
    return fwd, rev


def neighbours(node, edges=None, depth=1):
    edges = edges if edges is not None else load()
    fwd, rev = index(edges)
    seen, frontier, out = {node}, [node], []
    for _ in range(depth):
        nxt = []
        for n in frontier:
            for e in fwd.get(n, []) + rev.get(n, []):
                other = e["to"] if e["from"] == n else e["from"]
                out.append(e)
                if other not in seen:
                    seen.add(other)
                    nxt.append(other)
        frontier = nxt
    # de-duplicate while keeping order
    uniq, seen_e = [], set()
    for e in out:
        k = (e["from"], e["rel"], e["to"])
        if k not in seen_e:
            seen_e.add(k)
            uniq.append(e)
    return uniq


def backlinks(node, edges=None):
    """What points AT this node, grouped by relation. The reverse of chain's forward view."""
    edges = edges if edges is not None else load()
    _, rev = index(edges)
    out = {}
    for e in rev.get(node, []):
        out.setdefault(e["rel"], []).append(e["from"])
    for k in out:
        out[k] = sorted(set(out[k]))
    return out


def chain(node, edges=None):
    """lesson -> what enforces it -> the suite -> the mutations that prove the suite works."""
    edges = edges if edges is not None else load()
    fwd, rev = index(edges)
    out = {"node": node, "superseded_by": [], "cites": [], "enforced_by": [],
           "suites": [], "mutations": [], "rested_on_by": []}
    for e in fwd.get(node, []):
        if e["rel"] in out:
            out[e["rel"]].append(e["to"])
    for e in rev.get(node, []):
        if e["rel"] == "rests_on":
            out["rested_on_by"].append(e["from"])
    # De-duplicate: a lesson naming its guard twice in one ENFORCEMENT block is one edge,
    # not two, and a repeated answer reads as two findings.
    for k in out:
        if isinstance(out[k], list):
            out[k] = list(dict.fromkeys(out[k]))
    for guard in out["enforced_by"]:
        for e in rev.get(guard, []):
            if e["rel"] == "mutates":
                out["mutations"].append(e["from"])
                for e2 in fwd.get(e["from"], []):
                    if e2["rel"] == "caught_by" and e2["to"] not in out["suites"]:
                        out["suites"].append(e2["to"])
    out["mutations"] = list(dict.fromkeys(out["mutations"]))
    return out


def impact(target, edges=None):
    """What names this file: the mutations that break it and the suites that must notice."""
    edges = edges if edges is not None else load()
    fwd, rev = index(edges)
    muts = [e["from"] for e in rev.get(target, []) if e["rel"] == "mutates"]
    suites, lessons = [], [e["from"] for e in rev.get(target, []) if e["rel"] == "enforced_by"]
    for m in muts:
        for e in fwd.get(m, []):
            if e["rel"] == "caught_by" and e["to"] not in suites:
                suites.append(e["to"])
    return {"target": target, "mutations": muts, "suites": suites, "lessons": lessons}


def history(task, edges=None):
    edges = edges if edges is not None else load()
    _, rev = index(edges)
    fwd, _ = index(edges)
    rows = []
    for e in rev.get(task, []):
        if e["rel"] in ("run_of", "verdict_on"):
            node = e["from"]
            said = [x["to"] for x in fwd.get(node, []) if x["rel"] == "said"]
            model = [x["to"] for x in fwd.get(node, []) if x["rel"] == "by_model"]
            branch = [x["to"] for x in fwd.get(node, []) if x["rel"] == "branch"]
            rows.append({"kind": e["rel"], "node": node, "said": said,
                         "model": model, "branch": branch})
    rows.sort(key=lambda r: r["node"].split(":")[-1])
    return rows


def stale(edges=None):
    """Edges pointing at a file that no longer exists.

    A parser cannot invent an edge, but it can keep one after the thing moved. This is the
    honest failure mode of the design and it is checkable, which is why it has a command.
    """
    edges = edges if edges is not None else load()
    bad = []
    for e in edges:
        t = e["to"]
        if t.startswith(("scripts/", "projects/")) and "#" not in t:
            if not (REPO / t).exists():
                bad.append(e)
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    for name in ("neighbours", "why", "impact", "history", "backlinks"):
        s = sub.add_parser(name)
        s.add_argument("node")
    sub.add_parser("stale")
    sub.add_parser("stats")
    a = ap.parse_args()

    if a.cmd == "build":
        edges = build()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(edges, indent=0))
        rels = defaultdict(int)
        for e in edges:
            rels[e["rel"]] += 1
        print(f"  {len(edges)} edges")
        for r, n in sorted(rels.items(), key=lambda x: -x[1]):
            print(f"    {r:16} {n}")
        return 0
    if a.cmd == "stats":
        edges = load()
        nodes = {e["from"] for e in edges} | {e["to"] for e in edges}
        print(f"  {len(edges)} edges over {len(nodes)} nodes")
        return 0
    if a.cmd == "neighbours":
        for e in neighbours(a.node):
            print(f"  {e['from']}  --{e['rel']}->  {e['to']}")
        return 0
    if a.cmd == "why":
        c = chain(a.node)
        for k in ("superseded_by", "enforced_by", "suites", "mutations", "cites",
                  "rested_on_by"):
            if c[k]:
                print(f"  {k:15} {', '.join(c[k][:6])}")
        return 0
    if a.cmd == "impact":
        i = impact(a.node)
        print(f"  lessons that name it : {', '.join(i['lessons']) or '-'}")
        print(f"  mutations            : {len(i['mutations'])}")
        print(f"  suites that must fail: {', '.join(i['suites']) or '-'}")
        return 0
    if a.cmd == "history":
        for r in history(a.node):
            print(f"  {r['kind']:11} {' '.join(r['said']) or '-':18} "
                  f"{' '.join(r['model']) or '':26} {' '.join(r['branch'])}")
        return 0
    if a.cmd == "backlinks":
        b = backlinks(a.node)
        for rel in sorted(b):
            print(f"  {rel:15} {', '.join(b[rel])}")
        if not b:
            print("  (nothing points at this node)")
        return 0
    if a.cmd == "stale":
        bad = stale()
        for e in bad:
            print(f"  {e['from']}  --{e['rel']}->  {e['to']}  (missing)")
        print(f"  {len(bad)} stale edge(s)")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
