#!/usr/bin/env python3
"""t1568: the stale p751-t1448-run-telemetry row is gone from the work list."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_stale_p751_row_removed():
    """The refused p751-t1448-run-telemetry row must not be in autonomy_loop.sh."""
    loop = REPO / "scripts" / "autonomy_loop.sh"
    lines = loop.read_text(encoding="utf-8").splitlines()
    p751 = [l for l in lines if l.startswith("p751-t1448-run-telemetry|")]
    assert not p751, f"stale work-list row still present: {p751[0]}"


if __name__ == "__main__":
    test_stale_p751_row_removed()
    print("PASS: stale p751 row removed from autonomy_loop.sh")
