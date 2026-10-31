#!/usr/bin/env python3
"""t1322 -- the label placer must satisfy the checker.

The placer searches for a label spot only ON its own arrow, and when no on-line spot is free it
drops the label on the arrow's midpoint -- a placement the checker exists to report. This suite
pins the third pass (the nearest free spot off the line) and the checker rule that accepts a label
JOINED to its arrow by a connector, while still reporting one that is merely displaced.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import flow_layout  # noqa: E402

W, H = 60, 20
STEP, RAD = 12, 6
AX, AY = 400, 70
BOXES = [(360, 40, 200, 60), (580, 40, 200, 60), (20, 40, 200, 60)]


def _blocked_at(dx, dy):
    cand = (AX + dx - W / 2.0, AY + dy - H / 2.0, W, H)
    return any(flow_layout._hit(cand, b, 0) for b in BOXES)


def test_off_line_spot_is_the_nearest_free_spot():
    """A 60x20 label centred on the arrow at (400,70) overlaps the box at (360,40,200,60) for
every offset with |dx| < 70 and |dy| < 40, so on a 12px grid the first free offset is (0,-48).
The loop proves 'nearest': every grid offset nearer than (0,-4) is blocked, so (0,-4) is first."""
    spot = flow_layout.off_line_spot(AX, AY, W, H, BOXES, [], [])
    assert spot is not None, "off_line_spot returned None: (370, 12, 60, 20) is free of every box"
    assert spot == (370, 12, W, H), f"expected the nearest free spot (370, 12, {W}, {H}), got {spot}"
    for b in BOXES:
        assert not flow_layout._hit(spot, b, 0), f"the chosen spot {spot} sits on box {b}"
    cx, cy = spot[0] + W / 2.0, spot[1] + H / 2.0
    assert (cx - AX) ** 2 + (cy - AY) ** 2 <= 90 ** 2, f"spot {spot} is over 90px from the arrow"
    assert _blocked_at(0, 0), "fixture bug: the on-line spot must be blocked, or this proves nothing"
    for i in range(-RAD, RAD + 1):
        for j in range(-RAD, RAD + 1):
            if i * i + j * j < 16:
                assert _blocked_at(i * STEP, j * STEP), (
                    f"offset ({i},{j}) is nearer than (0,-4) and is free, so (0,-4) is not nearest")


def test_off_line_spot_steps_past_a_placed_label():
    """A label already parked at the first free spot is stepped over, not overlapped."""
    spot = flow_layout.off_line_spot(AX, AY, W, H, BOXES, [(370, 12, W, H)], [])
    assert spot == (370, 108, W, H), f"expected (370, 108, {W}, {H}) past the placed label, got {spot}"
    assert not flow_layout._hit(spot, (370, 12, W, H), 0), "the chosen spot overlaps the placed label"


def _mini_layout(label_box, connector=None):
    """Two boxes 360px apart (COL_GAP) with one straight arrow at y=70 between them, built by
hand so the arithmetic is checkable: at t=0.5 the single cubic of segs [[(220,70),(238,70),
(562,70),(580,70)]] passes exactly through (400,70)."""
    e = {"from": "A", "to": "B", "kind": "forward", "label": ["outcome"],
         "chain": ["A", "B"], "segs": [[(220, 70), (238, 70), (562, 70), (580, 70)]],
         "label_box": label_box, "on_line": connector is None}
    if connector is not None:
        e["connector"] = connector
    return {"width": 800, "height": 300, "services": [], "edges": [e],
            "nodes": {"A": {"id": "A", "kind": "script", "worker": "x", "status": "active",
                             "title_lines": 1, "lines": ["A"], "w": 200, "h": 60, "x": 20, "y": 40},
                      "B": {"id": "B", "kind": "script", "worker": "x", "status": "active",
                            "title_lines": 1, "lines": ["B"], "w": 200, "h": 60, "x": 580, "y": 40}}}


D = {"tasks": [{"id": "A", "name": "A", "worker": "x", "worker_kind": "script",
                 "command": "c", "queue": "q", "inputs": [], "outputs": [],
                 "outcomes": ["outcome"], "ends": []},
                {"id": "B", "name": "B", "worker": "x", "worker_kind": "script",
                 "command": "c", "queue": "q", "inputs": [], "outputs": [],
                 "outcomes": [], "ends": []}],
      "links": [{"from": "A", "to": "B", "outcome": "outcome", "mover": "by"}],
      "sources": [], "services": []}


def test_checker_accepts_a_label_joined_to_its_arrow_by_a_connector():
    """The label box (380,200,60,20) is 130px off the arrow at y=70. Joined by a connector from
    (400,70), a real sample point of the arrow, to the label's top-edge midpoint (410,200), the
    layout is clean. Today check() has no connector rule, so it reports the 130px distance."""
    probs = flow_layout.check(_mini_layout((380, 200, W, H), (400, 70, 410, 200)), D)
    assert probs == [], f"a label joined to its arrow by a connector was reported: {probs}"


def test_checker_still_reports_a_label_displaced_from_its_arrow():
    """Negative assertion: the fix must not silence the rule it relaxes. Same layout, label
    flagged on its line, no connector: 130px from its own arrow is a problem, now and after."""
    probs = flow_layout.check(_mini_layout((380, 200, W, H)))
    assert probs, "a label 130px off its own arrow with no connector must still be a problem"
    assert any("130" in p for p in probs), f"expected the 130px label/arrow distance problem, got {probs}"


def test_a_label_off_the_line_must_carry_its_connector():
    """The connector is READ by check(), not only drawn: a label flagged off its arrow with no
    connector is a problem. This is what stops the new pass placing labels off the line and
    recording nothing, which would satisfy every other test in this file."""
    lay = _mini_layout((380, 200, W, H))
    lay["edges"][0]["on_line"] = False
    probs = flow_layout.check(lay, D)
    assert probs, "a label moved off its arrow with no connector was reported as clean"
    assert any("no connector" in p for p in probs), (
        f"expected an off-the-arrow-with-no-connector problem, got {probs}")


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:
            fails.append(name)
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - len(fails)}/{len(tests)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
