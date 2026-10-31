#!/usr/bin/env python3
"""semantic.py: an embedder that is down makes the review script fall back to words, never raise.

Hermetic: memory.embed is replaced, nothing is written (the cache is pointed at a temp file).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import memory  # noqa: E402
import semantic  # noqa: E402

FAILED = 0


def check(label, got, want):
    global FAILED
    FAILED += got != want
    print(f"  [{'ok' if got == want else 'BAD'}] {label}: {got!r}")


semantic.CACHE_FILE = Path(tempfile.mkdtemp()) / "vectors.json"
memory.embed = lambda texts, **k: [[1.0, 0.0] if "prune" in t else [0.0, 1.0] for t in texts]
hit = semantic.best("add the NAS prune", ["the prune is in the nightly sweep", "unrelated words"])
check("an up embedder ranks the matching sentence first", hit[1] if hit else None, 0)
check("and scores it as identical", round(hit[0], 3) if hit else None, 1.0)
check("nothing to compare -> None", semantic.best("x", []), None)
semantic._cache, semantic._down = {}, False
memory.embed = lambda texts, **k: None
check("a down embedder -> None (the caller keeps its word test)", semantic.best("new text", ["other"]), None)
check("and stays down for the run, without retrying every call", semantic._down, True)
print(f"{'FAILED' if FAILED else 'ok'}: {FAILED} failure(s)")
sys.exit(1 if FAILED else 0)
