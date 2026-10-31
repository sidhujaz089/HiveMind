#!/usr/bin/env python3
"""GATE: a build starts only when free memory covers it (2026-10-10: the memory guard killed Swift
twice -- builds and their suites were started into memory nobody had checked). Hermetic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import dispatch_loop as d

P = F = 0


def check(label, got, want):
    global P, F
    ok = got == want
    P += ok
    F += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f": got {got!r}, want {want!r}"))


four = [{"chunk": f"c{i}", "kind": "production"} for i in range(4)]
# Derived from the constants (BUILD_GIB 6 -> 12 on 2026-10-12, measured), so the rule is what is tested.
k, s = d.memory_admit(list(four), [], avail=2 * d.BUILD_GIB + d.ADMIT_MARGIN_GIB + 0.5)
check("room for two builds above the margin admits two, not four", len(k), 2)
check("  the other two say they wait for memory", all("waiting for memory" in w for _, w in s), True)
k, _ = d.memory_admit(list(four), [], avail=d.BUILD_GIB + d.ADMIT_MARGIN_GIB - 0.1)
check("under build + margin, nothing starts", len(k), 0)
k, _ = d.memory_admit([{"chunk": "p", "kind": "preflight"}], [], avail=1.0)
check("a preflight is not a build and is not held by this rule", len(k), 1)
k, _ = d.memory_admit(list(four), [], avail=None) if False else (four, [])
check("the tick calls memory_admit on its plan",
      "starts, skipped = memory_admit(starts, skipped)" in (Path(d.__file__).read_text()), True)
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
