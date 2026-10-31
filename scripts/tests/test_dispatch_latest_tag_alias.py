#!/usr/bin/env python3
"""A model named without ':latest' is the same model to the dispatcher (2026-10-24).

WHY. The Models tab saved the Auditor as 'swift-junior'; REGISTRY.json declares 'swift-junior:latest'.
Ollama treats the two as one model, but DEVICE_OF and FOOTPRINT_GIB were keyed by the exact tag, so the
bare name fell to the GX10 default with the unknown footprint. fits() then said 'swift-junior does not
fit beside [swift]' and no Auditor build or preflight started overnight 2026-10-23/24 (30 refusals).

Reads the live REGISTRY/SERVERS through the module, which is what the tick does; the expected device is
read from the same map under the full tag, so this does not pin today's hardware.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "projects" / "local-inference")]
import dispatch_loop as D  # noqa: E402

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


full = [t for t in D.DEVICE_OF if t.endswith(":latest")]
if not full:
    print("  [skip]  no ':latest' model declared on a non-GX10 server here")
else:
    for tag in full:
        bare = tag[: -len(":latest")]
        check(f"{bare} lives where {tag} lives", D.DEVICE_OF.get(bare), D.DEVICE_OF[tag])
        if tag in D.FOOTPRINT_GIB:
            check(f"{bare} has {tag}'s footprint", D.FOOTPRINT_GIB.get(bare), D.FOOTPRINT_GIB[tag])
        if D.DEVICE_OF[tag] != "gx10":
            check(f"{bare} fits beside swift (different device)", D.fits(bare, {"swift"}), D.fits(tag, {"swift"}))
    check("an exact tag is unchanged", all(D.DEVICE_OF[t] for t in full), True)

print(f"test_dispatch_latest_tag_alias: {'FAILED' if fails else 'ok'} ({fails} failure(s))")
sys.exit(1 if fails else 0)
