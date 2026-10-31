#!/usr/bin/env python3
"""The semantic vector cache is additive: two processes that each add entries both keep them.

2026-10-12: the cache was one JSON every process loaded whole and wrote back whole, so the last
writer dropped what the others added; lost vectors were re-embedded (466 of 487 s of a score) and a
re-embedded text could differ slightly, so identical code scored 481 then 477. Hermetic: a temp
cache via SEMANTIC_CACHE; nothing is embedded (entries are put directly).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
fails = []
with tempfile.TemporaryDirectory() as td:
    cache = Path(td) / "vectors.json"
    cache.write_text('{"base": [1.0]}', encoding="utf-8")
    env = {**os.environ, "SEMANTIC_CACHE": str(cache)}
    prog = ("import sys; sys.path.insert(0, %r); import semantic as s; s._load(); "
            "[s._put(f'{sys.argv[1]}{i}', [float(i)]) for i in range(50)]; s.save()") % str(REPO / "scripts")
    # Both load BEFORE either saves -- the interleaving that lost entries.
    a = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5); " + prog, "xa"], env=env)
    b = subprocess.Popen([sys.executable, "-c", prog, "xb"], env=env)
    a.wait(timeout=60), b.wait(timeout=60)
    out = subprocess.run([sys.executable, "-c",
                          "import sys; sys.path.insert(0, %r); import semantic as s; c = s._load(); "
                          "print(sum(k.startswith('xa') for k in c), sum(k.startswith('xb') for k in c), 'base' in c)"
                          % str(REPO / "scripts")], env=env, capture_output=True, text=True, timeout=60).stdout.split()
    if out != ["50", "50", "True"]:
        fails.append(f"both writers' entries and the base must survive: got {out}")
    subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import semantic as s; print(s.compact())"
                    % str(REPO / "scripts")], env=env, check=True, capture_output=True, timeout=60)
    log = cache.with_name("vectors.log.jsonl")
    if log.read_text() != "":
        fails.append("compact() must empty the log")
    if '"xa49"' not in cache.read_text():
        fails.append("compact() must fold the log into the base")
for f in fails:
    print("FAIL:", f)
print("test_semantic_cache_additive:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
