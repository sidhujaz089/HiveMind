#!/usr/bin/env python3
"""The scheduler's target-suite runs: cached per code state, and run on idle cores.

2026-10-01, profiled: 62.6 of a 66.4 s tick was ten green target suites run one after another,
test_scope_lint alone 61 s, repeated every tick. cached_red() remembers each colour per code state
(HEAD + uncommitted diff); prefetch_red() runs the rest CPU_JOBS at a time.
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import dispatch_loop as DL
    fails = []

    def ck(label, ok):
        print(f"  [{'ok' if ok else 'BAD'}]  {label}")
        if not ok:
            fails.append(label)

    real = DL.RED_CACHE
    DL.RED_CACHE = Path(tempfile.mkdtemp()) / "red-cache.json"
    try:
        calls = []

        def run(t):
            calls.append(t)
            return (t == "red.py", "")

        r = DL.cached_red(run, state="A")
        ck("first ask runs the suite", r("green.py") is False and calls == ["green.py"])
        r = DL.cached_red(run, state="A")
        ck("same code state: answered from the cache, no run", r("green.py") is False
           and calls == ["green.py"])
        r = DL.cached_red(run, state="B")
        ck("new code state: the suite runs again", r("green.py") is False and len(calls) == 2)
        DL.cached_red(lambda t: (None, "could not run"), state="C")("x.py")
        n = len(calls)
        DL.cached_red(run, state="C")("x.py")
        ck("a suite that could not run is not cached -- it runs next time", len(calls) == n + 1)
        ck("no code state (git unreadable): never cached",
           DL.cached_red(run, state="")("green.py") is False and len(calls) == n + 2)

        # prefetch runs in parallel and skips closed tasks
        live, peak = [0], [0]
        lock = threading.Lock()

        def slow(t):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.3)
            with lock:
                live[0] -= 1
            return False
        chunks = [{"target": f"t{i}.py", "brief": None} for i in range(8)]
        cache = {}
        t0 = time.time()
        DL.prefetch_red(chunks, cache, slow)
        ck(f"8 suites ran at once (peak {peak[0]}), not one after another",
           peak[0] > 1 and time.time() - t0 < 1.5)
        ck("every result landed in the cache", len(cache) == 8)
        # THE TASK QUEUE ORDERS THE WORK (Jas, 2026-10-01: reorder buttons on every stage).
        import types
        fake = types.SimpleNamespace(ordered=lambda: [({"id": "t2"}, ""), ({"id": "t1"}, "")])
        real_ts, real_task_of = sys.modules.get("taskstore"), DL.task_of
        sys.modules["taskstore"] = fake
        DL.task_of = lambda b: {"a": "t1", "b": "t2", "c": "t9", "d": "t1"}[b]
        try:
            got = [c["brief"] for c in DL.chunks_in_task_order(
                [{"brief": "a"}, {"brief": "c"}, {"brief": "b"}, {"brief": "d"}])]
        finally:
            DL.task_of = real_task_of
            if real_ts is not None:
                sys.modules["taskstore"] = real_ts
            else:
                sys.modules.pop("taskstore", None)
        ck(f"chunks follow the task queue, stable, unqueued last: {got}", got == ["b", "a", "d", "c"])
    finally:
        DL.RED_CACHE = real

    for f in fails:
        print("FAIL:", f)
    print("test_dispatch_red_cache:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
