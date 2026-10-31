#!/usr/bin/env python3
"""A build is charged for the memory a starting or growing Swift will still take.

2026-10-12: two builds were admitted at ~18:57Z while Swift was loading (MemAvailable looked
ample), then Swift grew into the same memory and free fell to 3.3 GB; at 18:39:56Z the same race
tripped the memory guard. memory_admit now subtracts `pending` before charging builds. Pure: avail
and pending are passed in, nothing live is read.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import dispatch_loop as d  # noqa: E402

fails = []
build = {"chunk": "p1", "kind": "production"}
k, s = d.memory_admit([dict(build)], [], avail=20.0, pending=0.0)
if len(k) != 1:
    fails.append(f"20 GB free and nothing pending must admit a build: {s}")
k, s = d.memory_admit([dict(build)], [], avail=20.0, pending=12.8)
if k or not s or "waiting for memory" not in s[0][1]:
    fails.append(f"20 GB free with 12.8 GB of Swift still to come must NOT admit a build: kept={k}")
k, s = d.memory_admit([{"chunk": "x", "kind": "preflight"}], [], avail=1.0, pending=50.0)
if len(k) != 1:
    fails.append("a non-build start is never held by memory")
for f in fails:
    print("FAIL:", f)
print("test_swift_pending_admission:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
