#!/usr/bin/env python3
"""The Flow chart is laid out in Python so it can be CHECKED (Jas, 2026-09-29: "you aren't really
able to render it and see it directly"). This pins what --check promises on the real FLOW.json and
on small fixtures: no overlaps, arrows never through boxes, labels on their arrows, loops handled."""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "scripts")]
import flow_layout as fl  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def T(id, name, worker="Manager", kind="role", outcomes=("done",), ends=()):
    return {"id": id, "name": name, "worker": worker, "worker_kind": kind, "command": "x", "queue": "q",
            "inputs": [], "outputs": [], "outcomes": list(outcomes), "triggers": {}, "ends": list(ends)}


def L(a, o, b, **k):
    return {"from": a, "outcome": o, "to": b, "mover": "m.py", "carry": [], **k}


real = fl.load()
report("the real FLOW.json lays out with 0 problems", fl.check(fl.layout(real), real), [])

loop = {"tasks": [T("a", "A"), T("b", "B"), T("c", "C")],
        "links": [L("a", "done", "b"), L("b", "done", "c"), L("c", "done", "a")], "sources": []}
lay = fl.layout(loop)
cols = sorted({round(n["x"]) for n in lay["nodes"].values()})
report("a loop of forward arrows spreads over 3 columns, not to the relaxation bound", len(cols), 3)
report("  and the chart stays narrow (3 columns, not the relaxation bound)",
       lay["width"] <= 3 * (max(n["w"] for n in lay["nodes"].values()) + fl.COL_GAP) + 40, True)

gap = {"tasks": [T("a", "A", outcomes=("done", "failed"))], "links": [], "sources": []}
report("an outcome with no link and no end is reported",
       [p for p in fl.check(fl.layout(gap), gap) if "not linked" in p],
       ["outcome not linked: A / done", "outcome not linked: A / failed"])

side = {"tasks": [T("a", "A"), T("b", "B", ends=("done",)), T("x", "X"), T("y", "Y", ends=("done",))],
        "links": [L("a", "done", "b"), L("x", "done", "y")], "sources": []}
lay = fl.layout(side)
report("a separate flow gets its own band below", lay["nodes"]["x"]["y"] > lay["nodes"]["a"]["y"] + lay["nodes"]["a"]["h"], True)

long_name = T("a", "A task whose name is far longer than one line of the box can hold")
lay = fl.layout({"tasks": [long_name], "links": [], "sources": []})
n = lay["nodes"]["a"]
report("a box is sized to its wrapped text (nothing clipped)",
       n["w"] >= max(len(l) for l in n["lines"]) * fl.CHAR_W and len(n["lines"]) > 1, True)

import os, subprocess, hashlib
digests = set()
for seed in ("1", "2", "3"):
    out = subprocess.run([sys.executable, str(Path(fl.__file__)), "--json"], capture_output=True,
                         env={**os.environ, "PYTHONHASHSEED": seed}, timeout=120).stdout
    digests.add(hashlib.sha256(out).hexdigest())
report("the layout is identical in every process (no set-order reshuffle)", len(digests), 1)
report("every labelled arrow has one arrowhead into its label, and no more",
       all(len(fl.mid_arrows(e)) <= 1 for e in fl.layout(real)["edges"]), True)
two_way = {"tasks": [T("a", "A"), T("b", "B", outcomes=("done", "back"), ends=("done",))],
           "links": [L("a", "done", "b"), L("b", "back", "a", back=True)], "sources": []}
lay = fl.layout(two_way)
report("a backward arrow between neighbours bows gently, never a U (Jas, 2026-09-29)",
       [p for p in fl.check(lay, two_way) if "dips or rises" in p], [])
back = [e for e in lay["edges"] if e["kind"] == "back"][0]
lowest = max(y for _, y in fl.points(back, 30))
ends = max(back["segs"][0][0][1], back["segs"][-1][-1][1])
report("  its sag is at most BOW px below its lower end", lowest <= ends + fl.BOW + 1, True)
txt = fl.text(real)
report("--text names every task", all(f"[{t['name']}]" in txt for t in real["tasks"]), True)
report("--text shows triggers as creating work", "TRIGGER creates in Backlog" in txt, True)

# THE SERVICES ROW (t475): up/down pills under the flow, no arrows, never over a box.
svc = {"tasks": [T("a", "A", ends=("done",))], "links": [], "sources": [],
       "services": [{"id": "s1", "name": "Hub", "unit": "app-feedback.service"},
                    {"id": "s2", "name": "Tunnel", "unit": "cloudflared-hivemind.service"}]}
lay = fl.layout(svc)
report("services are laid out in a row below every box",
       all(s["y"] > lay["nodes"]["a"]["y"] + lay["nodes"]["a"]["h"] for s in lay["services"]), True)
report("  and the chart is tall enough to hold them",
       lay["height"] >= max(s["y"] + s["h"] for s in lay["services"]), True)
drawn = fl.svg(lay, states={"app-feedback.service": "active", "cloudflared-hivemind.service": "failed"})
report("  each pill carries its unit's live state",
       ('data-state="active"' in drawn, 'data-state="failed"' in drawn), (True, True))
report("  a unit that cannot be asked is 'unknown', never green",
       'data-state="unknown"' in fl.svg(lay, states={}), True)
report("the real FLOW.json names a unit for every service",
       all(s.get("unit") for s in real.get("services", [])) and bool(real.get("services")), True)
report("--text lists the services", "SERVICES" in txt, True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
