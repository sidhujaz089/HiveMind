#!/usr/bin/env python3
"""The workflow chart, laid out HERE rather than in a browser -- so whoever reads it can check it.

Jas, 2026-09-29, on the Mermaid preview: "it looks like you aren't really able to render it and
see it directly ... Don't feel constrained ... Make sure it's workable for the local models as
well ... the manager will definitely need to be able to see what they're scoping."

Mermaid lays the chart out inside the browser, so the only way to know where anything landed was a
headless screenshot, and labels were clipped where it decided. This computes the whole layout in
Python from models/FLOW.json -- stage columns, crossing-reduced order, boxes sized to their full
text, labels nudged until nothing overlaps -- and emits it three ways from ONE computation:

    python3 scripts/flow_layout.py --text     # the flow as readable text (for the Manager, for Claude)
    python3 scripts/flow_layout.py --check    # overlaps and unlinked outcomes; exit 1 if any
    python3 scripts/flow_layout.py --json     # every box, edge and label with its coordinates
    python3 scripts/flow_layout.py --svg      # the drawing the Hub's Flow tab shows

Text is never clipped: a box is sized from its wrapped lines, and --check proves no box, label or
edge label overlaps another.
"""
import json
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FLOW = REPO / "projects" / "local-inference" / "models" / "FLOW.json"
# WHO WORKS EACH STAGE (t1594). role_store writes this projection from ROLES.json's task lists on
# every role save, so assigning or unassigning a task moves the chart's answer with it. The chart
# itself is Jas's and is never edited to carry this: the projection is generated, FLOW.json is not.
WORKERS = REPO / "projects" / "local-inference" / "models" / "FLOW-WORKERS.json"


def role_names():
    """{role key: the name Jas gave it} from the store, so the chart shows ROLES.json's words.

    Missing or unreadable is {}: the chart must still draw, and the numbers fall back to the keys.
    """
    try:
        import role_store as rs
        return {k: (r.get("name") or k) for k, r in (rs.load().get("roles") or {}).items()}
    except Exception:                                           # noqa: BLE001 -- drawing, not judging
        return {}


def who_works(node_id, names=None, wmap=None):
    """The lane for one node, DERIVED: the roles ROLES.json says hold its task (t1594).

    Never a copy stored in the chart. An empty answer is said out loud rather than left blank,
    because a stage nobody works is a finding about the roles, not a rendering gap.
    """
    held = (wmap if wmap is not None else workers()).get(node_id) or []
    if not held:
        return "no role holds this stage"
    names = role_names() if names is None else names
    return ", ".join(names.get(k, k) for k in held)


def workers(path=None):
    """{task_id: [role keys]} -- the Flow tab's overlay of who works each stage (t1594).

    Missing, unreadable or the wrong shape is {} and NOT an error: the chart must still draw. The
    check that a stale or unreadable projection is a failure belongs to check_workspace, not here --
    a reader that refuses to draw would take the page down for a file it can do without.
    """
    try:
        return json.loads(Path(path or WORKERS).read_text(encoding="utf-8")).get("workers") or {}
    except (OSError, ValueError, AttributeError):
        return {}

CHAR_W = 7.0          # px per character at the 12px label font, rounded UP so text always fits
LINE_H = 16
PAD_X, PAD_Y = 12, 9
WRAP = 28             # characters per line inside a box
COL_GAP = 360         # room between columns for edge labels (Jas: "spread out a lot more")
ROW_GAP = 64
EDGE_FONT_W = 6.2     # 11px edge labels
EDGE_LINE_H = 14
PORT_GAP = 30          # px of box edge per arrow
BOW = 36              # px a backward neighbour arrow may sag below its lower end
CLEAR = 8             # px an arrow must keep from every box it does not join

STATUS = ("active", "inactive", "dry run", "obsolete?", "hypothetical")


def load(path=FLOW):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _lines(title, sub, extra=()):
    out = textwrap.wrap(title, WRAP) or [""]
    for s in (sub, *extra):
        if s:
            out += textwrap.wrap(s, WRAP)
    return out


def nodes_of(d):
    """Every box: tasks and sources, with its wrapped label lines and size."""
    _W, _N = workers(), role_names()
    nodes = {}
    for t in d["tasks"]:
        st = t.get("status", "active")
        extra = [f"[{st}]"] if st != "active" else []
        # for a role node the lane is derived, never the chart's stored copy (t1594)
        worker = who_works(t["id"], names=_N, wmap=_W) if t.get("worker_kind") == "role" else t["worker"]
        lines = _lines(t["name"], worker, extra)
        nodes[t["id"]] = {"id": t["id"], "kind": t["worker_kind"], "worker": t["worker"],
                          "status": st, "title_lines": len(textwrap.wrap(t["name"], WRAP) or [""]),
                          "lines": lines}
    for s in d.get("sources", []):
        lines = _lines(s["name"], "", [f"[{s['status']}]"] if s.get("status", "active") != "active" else [])
        nodes[s["id"]] = {"id": s["id"], "kind": "source", "worker": "", "status": s.get("status", "active"),
                          "title_lines": len(lines), "lines": lines}
    for n in nodes.values():
        n["w"] = round(max(len(l) for l in n["lines"]) * CHAR_W + 2 * PAD_X)
        n["h"] = len(n["lines"]) * LINE_H + 2 * PAD_Y
    return nodes


def edges_of(d):
    """Every arrow: links (forward/back) and trigger arrows, plus the source arrows."""
    out = []
    for l in d["links"]:
        kind = l.get("kind") or ("back" if l.get("back") else "forward")
        out.append({"from": l["from"], "to": l["to"], "kind": kind,
                    "label": [l["outcome"]] + textwrap.wrap(l.get("mover", ""), 24)})
    for s in d.get("sources", []):
        out.append({"from": s["id"], "to": s["to"], "kind": "forward",
                    "label": textwrap.wrap(s.get("mover", ""), 26)})
    return out


def layer(nodes, edges):
    """Column per node: the longest forward path from the sources. Back and trigger arrows do not
    decide columns. A LOOP OF FORWARD ARROWS IS REAL here (preflight -> building -> merge ->
    script review -> compliance -> preflight), so a depth-first walk from the sources marks the
    arrow that closes each loop and leaves it out of the column count; it is drawn right-to-left."""
    fwd = {}
    for e in edges:
        if e["kind"] == "forward":
            fwd.setdefault(e["from"], []).append(e["to"])
    indeg = {n: 0 for n in nodes}
    for a_, bs in fwd.items():
        for b_ in bs:
            indeg[b_] += 1
    closing, state = set(), {}
    def walk(n):
        state[n] = 1
        for m in fwd.get(n, []):
            if state.get(m) == 1:
                closing.add((n, m))
            elif m not in state:
                walk(m)
        state[n] = 2
    for n in sorted(nodes, key=lambda n: indeg[n]):
        if n not in state:
            walk(n)
    dag = [(a_, b_) for a_, bs in fwd.items() for b_ in bs if (a_, b_) not in closing]
    col = {n: 0 for n in nodes}
    for _ in range(len(nodes)):
        changed = False
        for a_, b_ in dag:
            if col[b_] < col[a_] + 1:
                col[b_] = col[a_] + 1
                changed = True
        if not changed:
            break
    return col


def order(nodes, edges, col):
    """Rows inside each column: barycentre sweeps to cut crossings."""
    cols = {}
    for n, c in col.items():
        cols.setdefault(c, []).append(n)
    pos = {n: i for c in cols.values() for i, n in enumerate(sorted(c))}
    nb_in, nb_out = {n: [] for n in nodes}, {n: [] for n in nodes}
    for e in edges:
        nb_in[e["to"]].append(e["from"])
        nb_out[e["from"]].append(e["to"])
    for sweep in range(8):
        rng = sorted(cols) if sweep % 2 == 0 else sorted(cols, reverse=True)
        for c in rng:
            nbrs = nb_in if sweep % 2 == 0 else nb_out
            def bary(n):
                ps = [pos[m] for m in nbrs[n]]
                return sum(ps) / len(ps) if ps else pos[n]
            cols[c].sort(key=lambda n: (bary(n), pos[n]))
            for i, n in enumerate(cols[c]):
                pos[n] = i
    return cols


def components(nodes, edges):
    """Connected groups of boxes, biggest first: the main flow, then each side flow on its own."""
    adj = {n: set() for n in nodes}
    for e in edges:
        adj[e["from"]].add(e["to"])
        adj[e["to"]].add(e["from"])
    seen, out = set(), []
    for n in nodes:
        if n in seen:
            continue
        stack, comp = [n], set()
        while stack:
            m = stack.pop()
            if m not in comp:
                comp.add(m)
                stack.extend(adj[m] - comp)
        seen |= comp
        out.append(comp)
    order_ = {n: i for i, n in enumerate(nodes)}
    return sorted(out, key=lambda c: (-len(c), min(order_[n] for n in c)))


def _bez(p, t):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p
    u = 1 - t
    return (u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
            u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3)


def points(e, per_seg=12):
    """Sample points along an arrow (all its cubic segments)."""
    out = []
    for seg in e["segs"]:
        out += [_bez(seg, i / per_seg) for i in range(per_seg + 1)]
    return out


def _segs(pts):
    """A smooth arrow through the given points: one cubic per hop, horizontal tangents."""
    segs = []
    for (x0, y0), (x3, y3) in zip(pts, pts[1:]):
        dx = (x3 - x0) / 2
        segs.append([(x0, y0), (x0 + dx, y0), (x3 - dx, y3), (x3, y3)])
    return segs


def layout(d=None):
    """Main flow on top; each side flow in its own band below. An arrow that spans columns gets an
    invisible WAYPOINT in every column it crosses, and the waypoints take part in the row ordering
    like small boxes -- so arrows run through the gaps between boxes, never over them. Back arrows
    get the same treatment, running right-to-left. Labels ride on their own arrow."""
    d = d or load()
    nodes, edges = nodes_of(d), edges_of(d)
    top, width = 40, 0
    for band, comp in enumerate(components(nodes, edges)):
        # IN FILE ORDER, NOT SET ORDER: a set of strings iterates in a different order in every
        # process (hash randomisation), and the chart reshuffled itself between runs.
        sub = {n: nodes[n] for n in nodes if n in comp}
        sedges = [e for e in edges if e["from"] in comp]
        col = layer(sub, sedges)
        allnodes, allcol, segments = dict(sub), dict(col), []
        for i, e in enumerate(sedges):
            ca, cb = col[e["from"]], col[e["to"]]
            chain = [e["from"]]
            if ca != cb:
                step = 1 if cb > ca else -1
                for c in range(ca + step, cb, step):
                    did = f"~{band}.{i}.{c}"
                    allnodes[did] = {"id": did, "w": 14, "h": 10, "dummy": True}
                    allcol[did] = c
                    chain.append(did)
            chain.append(e["to"])
            e["chain"] = chain
            for u, v in zip(chain, chain[1:]):
                segments.append({"from": u, "to": v} if allcol[u] <= allcol[v] else {"from": v, "to": u})
        # A BUSY BOX GROWS: at least PORT_GAP px of edge per arrow on its busiest side, so arrows
        # arriving at one box stay visibly apart (Backlog takes seven).
        port_load = {}
        for e in sedges:
            ch = e["chain"]
            if allcol[ch[-1]] > allcol[ch[0]]:
                sides = ("R", "L")
            elif allcol[ch[-1]] < allcol[ch[0]]:
                sides = ("L", "R")
            else:
                sides = ("R", "R")
            port_load[(ch[0], sides[0])] = port_load.get((ch[0], sides[0]), 0) + 1
            port_load[(ch[-1], sides[1])] = port_load.get((ch[-1], sides[1]), 0) + 1
        for (n, _side), k in port_load.items():
            if not allnodes[n].get("dummy"):
                allnodes[n]["h"] = max(allnodes[n]["h"], (k + 1) * PORT_GAP)
        cols = order(allnodes, segments, allcol)
        real_h = lambda n: allnodes[n]["h"] + (ROW_GAP if not allnodes[n].get("dummy") else 40)
        heights = {c: sum(real_h(n) for n in ns) for c, ns in cols.items()}
        tall = max(heights.values())
        x = 20
        for c in sorted(cols):
            wmax = max((allnodes[n]["w"] for n in cols[c] if not allnodes[n].get("dummy")), default=14)
            y = top + (tall - heights[c]) / 2
            for n in cols[c]:
                allnodes[n]["x"], allnodes[n]["y"] = x + (wmax - allnodes[n]["w"]) / 2, y
                allnodes[n]["band"] = band
                y += real_h(n)
            x += wmax + COL_GAP
        width = max(width, x)
        # PORTS (Jas: "hard to tell if something connects to a point or if it's just going underneath
        # it"): every arrow gets its OWN attachment point on the side of the box, spread over the side
        # and ordered by where the arrow comes from, so arrows never pile into one spot.
        plan, ports = [], {}
        for e in sedges:
            ch = e["chain"]
            if len(ch) == 2 and allcol[ch[0]] == allcol[ch[1]]:
                sides = ("R", "R")
            else:
                sides = ("R", "L") if allcol[ch[-1]] > allcol[ch[0]] else ("L", "R")
            def centre(m):
                return allnodes[m]["y"] + allnodes[m]["h"] / 2
            plan.append((e, sides))
            ports.setdefault((ch[0], sides[0]), []).append((centre(ch[1]), e, 0))
            ports.setdefault((ch[-1], sides[1]), []).append((centre(ch[-2]), e, 1))
        at = {}
        for (n, side), lst in ports.items():
            box = allnodes[n]
            lst.sort(key=lambda t: t[0])
            for i, (_, e, end) in enumerate(lst):
                y = box["y"] + box["h"] * (i + 1) / (len(lst) + 1)
                x = box["x"] + (box["w"] if side == "R" else 0)
                at[(id(e), end)] = (x, y)
        for e, sides in plan:
            ch = e["chain"]
            a, b = allnodes[ch[0]], allnodes[ch[-1]]
            p0, p3 = at[(id(e), 0)], at[(id(e), 1)]
            if sides == ("R", "R"):                                     # same column: loop out right
                bulge = max(a["w"], b["w"]) / 2 + 60
                e["segs"] = [[p0, (p0[0] + bulge, p0[1]), (p3[0] + bulge, p3[1]), p3]]
                continue
            right = sides == ("R", "L")
            mids = [(allnodes[m]["x"] + allnodes[m]["w"] / 2, allnodes[m]["y"] + allnodes[m]["h"] / 2)
                    for m in ch[1:-1]]
            # NO DIP for a backward arrow between neighbours (Jas, 2026-09-29: "lines that are dropping
            # down very low and then coming back up"). The dip existed so a forward and a backward
            # arrow between the same pair did not share a line; since every arrow has its own port
            # (0fae684) they cannot, and the dip only drew a U. What remains is a GENTLE BOW, at most
            # BOW px below the lower END of the arrow (not below the boxes): enough to part the two
            # parallel lines of a two-way pair so each can carry its label, never a U.
            if not mids and not right:
                mids = [((p0[0] + p3[0]) / 2, max(p0[1], p3[1]) + BOW)]
            # A short straight stub out of and into the box, so every arrow visibly starts and ends
            # AT the box's edge rather than merging into a curve near it.
            stub = 18 if right else -18
            e["segs"] = _segs([p0, (p0[0] + stub, p0[1]), *mids, (p3[0] - stub, p3[1]), p3])
        top = top + tall + 110
    placed = []
    boxes = [(n["x"], n["y"], n["w"], n["h"]) for n in nodes.values()]

    def length(e):
        ps = points(e, 6)
        return sum(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** .5 for (x0, y0), (x1, y1) in zip(ps, ps[1:]))
    all_pts = {id(f): points(f, 40) for f in edges}
    pts_by_edge = all_pts
    # SHORT ARROWS FIRST: they have the fewest places a label can go; a long arrow can always move.
    for e in sorted(edges, key=length):
        lw = max(len(s) for s in e["label"]) * EDGE_FONT_W + 8 if e["label"] else 0
        lh = len(e["label"]) * EDGE_LINE_H + 4
        best = None
        # 60, not 20 (2026-10-25, t1594): a label has to find a spot where NO other arrow runs
        # under it, and with the role lanes derived the boxes are wider, so the clear stretches
        # between arrows are shorter. Twenty samples stepped over them and the placement fell
        # back to the fewest-crossings pass, which is what --check reports.
        pts = points(e, 60)
        n = len(pts)
        # Arrows from a SOURCE converge on one box (four into Intake), so their labels go near the
        # source, where the arrows are still apart; every other label starts at the middle.
        src = nodes[e["from"]]["kind"] == "source"
        ks = sorted(range(n), key=(lambda k: k) if src else (lambda k: abs(k - n // 2)))
        others = [pt for f in edges if f is not e for pt in all_pts[id(f)]]

        def free(c):
            if any(_hit(c, bx) for bx in boxes + placed):
                return False
            x0, y0, w0, h0 = c[0] - 3, c[1] - 3, c[2] + 6, c[3] + 6
            return not any(x0 < px < x0 + w0 and y0 < py < y0 + h0 for px, py in others)
        # ALWAYS ON ITS OWN LINE (Jas, 2026-09-29: the connector drawn to a label set beside its line
        # "seems to jut out at 90 degrees from a flow" -- it read as a branch). Pass 1: a spot on the
        # line with no box, label or other arrow under it. Pass 2: a spot on the line with no box or
        # label under it and the FEWEST other arrows crossing it (--check names each crossing).
        e["on_line"] = True
        for k in ks:
            mx, my = pts[k]
            cand = (mx - lw / 2, my - lh / 2, lw, lh)
            if free(cand):
                best = cand
                break
        if best is None:
            scored = []
            for k in ks:
                mx, my = pts[k]
                c = (mx - lw / 2, my - lh / 2, lw, lh)
                if not any(_hit(c, bx) for bx in boxes + placed):
                    x0, y0 = c[0] - 3, c[1] - 3
                    crossing = {fid for fid, fp in pts_by_edge.items() if fid != id(e) and
                                any(x0 < px < x0 + lw + 6 and y0 < py < y0 + lh + 6 for px, py in fp)}
                    scored.append((len(crossing), abs(k - n // 2), c))
            if scored:
                best = min(scored, key=lambda t: (t[0], t[1]))[2]
        if best is None:
            mx, my = pts[n // 2]
            best = (mx - lw / 2, my - lh / 2, lw, lh)       # --check reports the collision
        e["label_box"] = best
        placed.append(best)
    height = max([n["y"] + n["h"] for n in nodes.values()]
                 + [y for e in edges for _, y in points(e, 4)]
                 + [e["label_box"][1] + e["label_box"][3] for e in edges]) + 30
    services = service_row(d, height + 20, width)
    if services:
        height = max(s["y"] + s["h"] for s in services) + 30
        width = max(width, max(s["x"] + s["w"] for s in services) + 20)
    return {"width": round(width), "height": round(height), "nodes": nodes, "edges": edges,
            "services": services}


SVC_H = 30


def service_row(d, top, width):
    """THE SERVICES ROW (t475, Jas: "a small services row ... up/down, not work steps"). Long-running
    services are what the work runs ON, so they get no arrows: a row of pills under the flow, wrapped
    to the chart's width, each coloured by whether its unit is up (svg() asks systemd)."""
    out, x, y = [], 20, top + 24                  # 24 px for the row's heading
    for s in d.get("services", []):
        w = round(len(s["name"]) * CHAR_W + 2 * PAD_X + 16)
        if out and x + w > max(width, 900):
            x, y = 20, y + SVC_H + 12
        out.append({"id": s["id"], "name": s["name"], "unit": s.get("unit", ""),
                    "x": x, "y": y, "w": w, "h": SVC_H, "heading_y": top + 12})
        x += w + 12
    return out


def service_states(services, timeout=5):
    """{unit: 'active' | 'inactive' | 'failed' | ...} from `systemctl --user is-active`, one call.
    Anything that goes wrong reads 'unknown' -- a pill that cannot be checked must not show green."""
    import subprocess
    units = [s["unit"] for s in services if s.get("unit")]
    if not units:
        return {}
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", *units], capture_output=True,
                           text=True, timeout=timeout)
        states = r.stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        states = []
    return {u: (states[i] if i < len(states) else "unknown") for i, u in enumerate(units)}


def _hit(a, b, pad=4):
    return not (a[0] + a[2] + pad <= b[0] or b[0] + b[2] + pad <= a[0]
                or a[1] + a[3] + pad <= b[1] or b[1] + b[3] + pad <= a[1])


def off_line_spot(x, y, w, h, boxes, labels, other_pts, step=12, radius=6):
    """Find the nearest free spot off the line, joined to the arrow by a connector. t1322."""
    pairs = sorted(
        ((i, j) for i in range(-radius, radius + 1) for j in range(-radius, radius + 1) if (i, j) != (0, 0)),
        key=lambda ij: (ij[0] ** 2 + ij[1] ** 2, ij[0], ij[1]),
    )
    for i, j in pairs:
        cand = (x + i * step - w / 2.0, y + j * step - h / 2.0, w, h)
        if any(_hit(cand, b, 4) for b in list(boxes) + list(labels)):
            continue
        x0, y0, w0, h0 = cand[0] - 3, cand[1] - 3, cand[2] + 6, cand[3] + 6
        if any(x0 < px < x0 + w0 and y0 < py < y0 + h0 for px, py in other_pts):
            continue
        return cand
    return None


def check(lay=None, d=None):
    """[problem] -- overlapping boxes/labels, and outcomes with no link, end or trigger."""
    d = d or load()
    lay = lay or layout(d)
    probs = []
    ns = list(lay["nodes"].values())
    for i, a in enumerate(ns):
        for b in ns[i + 1:]:
            if _hit((a["x"], a["y"], a["w"], a["h"]), (b["x"], b["y"], b["w"], b["h"]), 0):
                probs.append(f"boxes overlap: {a['id']} / {b['id']}")
    labels = [(e, e["label_box"]) for e in lay["edges"] if e["label"]]
    for e, lb in labels:
        for n in ns:
            if _hit(lb, (n["x"], n["y"], n["w"], n["h"]), 0):
                probs.append(f"label '{e['label'][0]}' ({e['from']}->{e['to']}) covers box {n['id']}")
    for i, (e, a) in enumerate(labels):
        for f, b in labels[i + 1:]:
            if _hit(a, b, 0):
                probs.append(f"labels overlap: {e['from']}->{e['to']} / {f['from']}->{f['to']}")
    for e in lay["edges"]:                     # an arrow must not run through a box it does not join
        pp = points(e, 20)
        pts = pp[3:-3]
        for n in ns:
            if n["id"] in (e["from"], e["to"]):
                continue
            m = CLEAR                          # a margin, so an arrow cannot graze a box either
            if any(n["x"] - m < x < n["x"] + n["w"] + m and n["y"] - m < y < n["y"] + n["h"] + m for x, y in pts):
                probs.append(f"arrow {e['from']}->{e['to']} passes within {m}px of box {n['id']}")
                break
        if e["label"]:                         # no OTHER arrow may run under this label
            lx, ly, lw, lh = e["label_box"]
            for f in lay["edges"]:
                if f is not e and any(lx < x < lx + lw and ly < y < ly + lh for x, y in points(f, 40)):
                    probs.append(f"arrow {f['from']}->{f['to']} runs under the label of {e['from']}->{e['to']}")
                    break
        if e["label"]:                         # and a label must sit on its own arrow
            lx, ly, lw, lh = e["label_box"]          # the label's box must touch its own arrow
            near = min(max(lx - x, 0, x - lx - lw) + max(ly - y, 0, y - ly - lh) for x, y in pp)
            if near > 6:                       # every label sits ON its own arrow
                if e.get("connector"):
                    pass                       # joined to its arrow by a leader line: allowed (t1322)
                elif not e.get("on_line", True):
                    probs.append(f"label for {e['from']}->{e['to']} is off its arrow with no connector")
                else:
                    probs.append(f"label for {e['from']}->{e['to']} is {near:.0f}px from its arrow")
    for e in lay["edges"]:                     # no U-turns: a direct arrow stays between its ends
        if len(e.get("chain", [])) <= 2:
            ys = [y for _, y in points(e, 30)]
            y0, y1 = e["segs"][0][0][1], e["segs"][-1][-1][1]
            if max(ys) > max(y0, y1) + 40 or min(ys) < min(y0, y1) - 40:
                probs.append(f"arrow {e['from']}->{e['to']} dips or rises more than 40px beyond its ends")
    svcs = lay.get("services", [])
    for i, s in enumerate(svcs):                 # the services row keeps clear of the flow
        sb = (s["x"], s["y"], s["w"], s["h"])
        for n in ns:
            if _hit(sb, (n["x"], n["y"], n["w"], n["h"]), 0):
                probs.append(f"service {s['id']} overlaps box {n['id']}")
        for t in svcs[i + 1:]:
            if _hit(sb, (t["x"], t["y"], t["w"], t["h"]), 0):
                probs.append(f"services overlap: {s['id']} / {t['id']}")
        if not s["unit"]:
            probs.append(f"service {s['id']} names no unit, so it cannot show up or down")
    linked = {(l["from"], l["outcome"]) for l in d["links"]}
    for t in d["tasks"]:
        for o in t["outcomes"]:
            if (t["id"], o) not in linked and o not in t.get("ends", []):
                probs.append(f"outcome not linked: {t['name']} / {o}")
    return probs


def text(d=None):
    """The flow as text a model can read: each task, what it takes and gives, and where each
    outcome goes, by which script, carrying what."""
    d = d or load()
    name = {t["id"]: t["name"] for t in d["tasks"]} | {s["id"]: s["name"] for s in d.get("sources", [])}
    out = [f"FLOW -- {len(d['tasks'])} tasks, {len(d['links'])} links (models/FLOW.json)", ""]
    for s in d.get("sources", []):
        out.append(f"SOURCE {s['name']} [{s.get('status', 'active')}] -> {name[s['to']]} (by {s['mover']})")
    out.append("")
    for t in d["tasks"]:
        lane = who_works(t["id"]) if t.get("worker_kind") == "role" else t["worker"]
        out.append(f"[{t['name']}] {t['worker_kind']}: {lane}  status: {t.get('status', 'active')}")
        out.append(f"   runs:    {t['command']}")
        out.append(f"   queue:   {t['queue']}")
        out.append(f"   inputs:  {', '.join(t['inputs']) or '-'}")
        out.append(f"   outputs: {', '.join(t['outputs']) or '-'}")
        for o in t["outcomes"]:
            ls = [l for l in d["links"] if l["from"] == t["id"] and l["outcome"] == o]
            if not ls:
                out.append(f"   {o}: {'ends here' if o in t.get('ends', []) else 'NOT LINKED'}")
            for l in ls:
                how = {"back": "sends back to", "trigger": "TRIGGER creates in"}.get(
                    l.get("kind") or ("back" if l.get("back") else ""), "->")
                out.append(f"   {o}: {how} {name[l['to']]}  (by {l['mover']}; carries {', '.join(l['carry'])})")
        out.append("")
    if d.get("services"):
        out.append("SERVICES (what the work runs on; up/down, not steps)")
        for s in d["services"]:
            out.append(f"   {s['name']}  ({s.get('unit', '')})")
    return "\n".join(out)


def label_arrow(e):
    """(tip, angle) of the arrowhead on the line going INTO this arrow's label: the tip is where the
    line first enters the label box (found by bisection), so it touches the box edge."""
    import math
    if not e.get("label_box") or not e["label"]:
        return None
    lx, ly, lw, lh = e["label_box"]
    inside = lambda p: lx <= p[0] <= lx + lw and ly <= p[1] <= ly + lh
    pts = points(e, 60)
    for k in range(1, len(pts)):
        if inside(pts[k]) and not inside(pts[k - 1]):
            a, b = pts[k - 1], pts[k]
            for _ in range(20):
                m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                a, b = (a, m) if inside(m) else (m, b)
            return b, math.degrees(math.atan2(pts[k][1] - pts[k - 1][1], pts[k][0] - pts[k - 1][0]))
    return None


def mid_arrows(e, every=0):
    """Kept for callers: the one arrowhead into the label, as [(x, y, angle)]."""
    la = label_arrow(e)
    return [(la[0][0], la[0][1], la[1])] if la else []


def svg(lay=None, states=None):
    """The drawing: boxes coloured by who works them, status shown by border, three arrow styles.
    Every element carries data-id so the page can make it clickable. `states` ({unit: state}) is
    asked of systemd when not given; pass {} to draw the services row with no live state."""
    from html import escape as e_
    lay = lay or layout()
    if states is None:
        states = service_states(lay.get("services", []))
    fill = {"Manager": "#e3f2fd", "Senior": "#f3e5f5", "Frontline": "#e8f5e9", "Claude": "#ede7f6",
            "Jas": "#ffebee", "Embedder": "#e0f7fa", "any role": "#f5f5f5"}
    stroke = {"Manager": "#1565c0", "Senior": "#6a1b9a", "Frontline": "#2e7d32", "Claude": "#4527a0",
              "Jas": "#c62828", "Embedder": "#00838f", "any role": "#757575"}
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" class="flowsvg" width="{lay["width"]}" height="{lay["height"]}" '
         f'viewBox="0 0 {lay["width"]} {lay["height"]}" font-family="system-ui,sans-serif">',
         '<defs>'
         '<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="10" markerHeight="10" orient="auto-start-reverse">'
         '<path d="M0,0L10,5L0,10z" fill="#555"/></marker>'
         '<marker id="ahb" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="10" markerHeight="10" orient="auto-start-reverse">'
         '<path d="M0,0L10,5L0,10z" fill="#b26a00"/></marker></defs>']
    for i, e in enumerate(lay["edges"]):
        dpath = "M{:.0f},{:.0f} ".format(*e["segs"][0][0]) + " ".join(
            "C{:.0f},{:.0f} {:.0f},{:.0f} {:.0f},{:.0f}".format(*sg[1], *sg[2], *sg[3]) for sg in e["segs"])
        style = {"forward": 'stroke="#555"', "back": 'stroke="#888" stroke-dasharray="6 4"',
                 "trigger": 'stroke="#b26a00" stroke-dasharray="2 4" stroke-width="2"'}[e["kind"]]
        mk = "ahb" if e["kind"] == "trigger" else "ah"
        o.append(f'<path class="edge" data-id="e{i}" data-from="{e_(e["from"])}" data-to="{e_(e["to"])}" '
                 f'd="{dpath}" fill="none" '
                 f'{style} stroke-width="1.8" marker-end="url(#{mk})"/>')
        # AN ARROWHEAD INTO THE LABEL (Jas, 2026-09-29), in the arrow's own class so it fades with it.
        colour = "#b26a00" if e["kind"] == "trigger" else ("#888" if e["kind"] == "back" else "#555")
    for n in lay["nodes"].values():
        x, y, w, h = n["x"], n["y"], n["w"], n["h"]
        who = n["worker"]
        f_, s_ = fill.get(who, "#cfd8dc"), stroke.get(who, "#455a64")
        if n["kind"] == "store":
            f_, s_ = "#fff9c4", "#c49000"
        if n["kind"] == "source":
            f_, s_ = "#fafafa", "#9e9e9e"
        dash = {"inactive": ' stroke-dasharray="5 4" opacity=".55"', "dry run": ' stroke-dasharray="2 3"',
                "obsolete?": ' stroke-dasharray="1 3" opacity=".6"', "hypothetical": ' stroke-dasharray="8 3"'
                }.get(n["status"], "")
        rx = 3 if n["kind"] == "script" else (14 if n["kind"] == "store" else 8)
        o.append(f'<g class="node" data-id="{e_(n["id"])}" tabindex="0">'
                 f'<rect x="{x:.0f}" y="{y:.0f}" width="{w}" height="{h}" rx="{rx}" fill="{f_}" stroke="{s_}" '
                 f'stroke-width="{1 if n["kind"] == "source" else 2}"{dash}/>')
        if n["kind"] == "script":
            o.append(f'<text x="{x + w - 6:.0f}" y="{y + 11:.0f}" font-size="9" text-anchor="end" fill="#455a64">script</text>')
        for j, line in enumerate(n["lines"]):
            weight = ' font-weight="700"' if j < n["title_lines"] else ' font-style="italic"'
            o.append(f'<text x="{x + PAD_X:.0f}" y="{y + PAD_Y + 12 + j * LINE_H:.0f}" font-size="12" '
                     f'fill="#111"{weight}>{e_(line)}</text>')
        o.append('</g>')
    for i, e in enumerate(lay["edges"]):
        if not e["label"]:
            continue
        x, y, w, h = e["label_box"]
        col = "#b26a00" if e["kind"] == "trigger" else "#333"
        # BOXED LABELS (Jas, 2026-09-29: "I liked the boxes around the labels, as they made things
        # more legible"; the borderless text tried in 12825e1 was worse).
        o.append(f'<g class="elabel" data-id="e{i}"><rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="3" '
                 f'fill="#fffdf8" stroke="#ccc"/>')
        for j, line in enumerate(e["label"]):
            txt = ("⚡ " if e["kind"] == "trigger" and j == 0 else "") + line
            style = ' font-weight="700"' if j == 0 else ' font-style="italic"'
            o.append(f'<text x="{x + 4:.0f}" y="{y + 12 + j * EDGE_LINE_H:.0f}" font-size="11" fill="{col}"{style}>'
                     f'{e_(txt)}</text>')
        o.append('</g>')
    # THE ARROWHEADS INTO LABELS GO LAST, above every label, so no label can cover one (Jas,
    # 2026-09-29: "the arrowhead is covered by the label").
    for i, e in enumerate(lay["edges"]):
        la = label_arrow(e)
        if la:
            (ax, ay), ang = la
            colour = "#b26a00" if e["kind"] == "trigger" else ("#888" if e["kind"] == "back" else "#555")
            o.append(f'<path class="edge" data-id="e{i}" data-from="{e_(e["from"])}" data-to="{e_(e["to"])}" '
                     f'd="M-10,-5L0,0L-10,5z" fill="{colour}" '
                     f'transform="translate({ax:.1f},{ay:.1f}) rotate({ang:.1f})"/>')
    svcs = lay.get("services", [])
    if svcs:
        o.append(f'<text x="20" y="{svcs[0]["heading_y"]:.0f}" font-size="12" font-weight="700" '
                 f'fill="#455a64">Services (up / down -- what the work runs on)</text>')
    for s in svcs:
        st = states.get(s["unit"], "unknown")
        f_, s_, mark = {"active": ("#e8f5e9", "#2e7d32", "●"), "failed": ("#ffebee", "#c62828", "✕"),
                        "inactive": ("#ffebee", "#c62828", "○")}.get(st, ("#f5f5f5", "#9e9e9e", "?"))
        o.append(f'<g class="service" data-id="{e_(s["id"])}" data-unit="{e_(s["unit"])}" '
                 f'data-state="{e_(st)}" tabindex="0"><title>{e_(s["unit"])}: {e_(st)}</title>'
                 f'<rect x="{s["x"]}" y="{s["y"]}" width="{s["w"]}" height="{s["h"]}" rx="15" '
                 f'fill="{f_}" stroke="{s_}" stroke-width="1.5"/>'
                 f'<text x="{s["x"] + 10}" y="{s["y"] + 19}" font-size="12" fill="{s_}">{mark}</text>'
                 f'<text x="{s["x"] + 26}" y="{s["y"] + 19}" font-size="12" fill="#111">{e_(s["name"])}</text></g>')
    o.append('</svg>')
    return "".join(o)


def main(argv):
    d = load()
    if "--text" in argv:
        print(text(d))
        return 0
    lay = layout(d)
    if "--json" in argv:
        print(json.dumps(lay, indent=1, default=lambda v: round(v, 1)))
        return 0
    if "--svg" in argv:
        print(svg(lay))
        return 0
    probs = check(lay, d)
    for p in probs:
        print(f"  PROBLEM  {p}")
    print(f"  {len(lay['nodes'])} boxes, {len(lay['edges'])} arrows, {lay['width']}x{lay['height']} px: "
          f"{len(probs)} problem(s)")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
