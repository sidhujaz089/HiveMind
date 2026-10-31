#!/usr/bin/env python3
"""t8649: a red digest written by an OLDER rule must be re-run, not reused as "known red".

The sweep keeps a ceiling suite's failure so ~200 unbuilt-feature suites are not re-run every time
(t1153). The digest it keeps is what E1 clusters the reds by. On 2026-10-26 the digest changed --
it used to be the last four lines, which cut the cause off for 37 of 318 suites and left the rest a
257-way tail of singletons -- and every entry written before that change is still in the cache,
carrying a digest that cannot be classified. Nothing could tell them apart, which is why the
classification E1 needs stayed blocked by data nobody could read.

The rule: a red entry records the rule that wrote its digest. An entry without that marker is not a
verdict anybody can use, so the suite runs again. A PASS needs no marker -- a pass carries no digest.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import run_tests as RT
    fails = []

    def check(label, ok):
        print(f"  [{'ok' if ok else 'BAD'}]  {label}")
        if not ok:
            fails.append(label)

    d = Path(tempfile.mkdtemp())
    suite = d / "test_declared_red.py"
    suite.write_text("raise SystemExit(1)\n", encoding="utf-8")
    name = suite.name
    cache_file = d / "cache.json"
    # isolate EVERY path and decision this reaches: the cache, the tests dir, the key function
    RT.CACHE = cache_file
    RT.TESTS = d
    RT.suite_key = lambda p: ("k1", "stubbed for the test")
    allowed = {name}

    def reused(entry):
        cache_file.write_text(json.dumps({name: entry}), encoding="utf-8")
        return name in RT.cached_passes([suite], allowed)

    base = {"key": "k1", "at": "2026-10-27T00:00:00Z", "t": time.time(), "secs": 1.0}

    check("a red entry written by the CURRENT rule is reused as known red",
          reused({**base, "red": True, "out": "tail", "digest": RT.DIGEST_VERSION}))
    check("a red entry with NO digest marker is re-run, not reused",
          not reused({**base, "red": True, "out": "last four lines only"}))
    check("a red entry from an OLDER digest version is re-run",
          not reused({**base, "red": True, "out": "tail", "digest": RT.DIGEST_VERSION - 1}))
    check("a PASS needs no marker -- a pass carries no digest", reused(base))

    src = (ROOT / "scripts" / "run_tests.py").read_text(encoding="utf-8")
    check("the writer stamps the digest version it wrote",
          "digest=DIGEST_VERSION" in src or '"digest": DIGEST_VERSION' in src)

    print("test_run_tests_digest_version:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
