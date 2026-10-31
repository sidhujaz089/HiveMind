#!/usr/bin/env python3
"""t1597: loading the 93 GB model must not start on top of work that is already running.

Measured 2026-10-24 20:35-20:36 (the task's note): the 93 GB model was loading, a coder build
(pid 429854) was in flight and three git worktrees were being created in the same minute. The
guard shed tests, then builds, then the model -- Swift, the one thing that takes ~20 minutes to
reload. The guard behaved exactly as designed; the load raced its own clean-up. The half of the
task that is not built is a QUIET WINDOW at the point of decision: refuse to LOAD a model the
box cannot absorb, and say what is running.

The contract this pins is ``serve.quiet_window(in_flight, footprint_gb, avail_gb)``:
  in_flight    [{"kind": str, "pid": int, "cmd": str}, ...]  (the CALLER enumerates them;
               the gate never runs `ps` -- L-105: the guard's own command line would match)
  footprint_gb the model's expected resident memory, GB
  avail_gb     free memory right now, GB
  returns      {"ok": bool, "reasons": [str, ...]} -- one reason per blocker, each naming the
               kind, the pid and the process, so a person reading the refusal can act on it.

Run: python3 scripts/tests/test_serve_quiet_window.py   (pytest is not installed)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repository root
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import serve
except Exception as e:                              # noqa: BLE001 -- report, never crash
    print(f"FAIL import: importing projects/local-inference/serve.py raised "
          f"{type(e).__name__}: {e}")
    sys.exit(1)
try:
    import memory_guard as mg
except Exception as e:                              # noqa: BLE001
    print(f"FAIL import: importing scripts/memory_guard.py raised {type(e).__name__}: {e}")
    sys.exit(1)

# THE REAL MARKERS, read from the declared shed order (workspace/guard-tiers.json) rather than
# invented: the gate must refuse to race the same processes the guard sheds, and builds and
# tests are DISTINCT strings, so a fixture built from one placeholder cannot pass both.
_TIERS = json.loads((ROOT / "workspace" / "guard-tiers.json").read_text(encoding="utf-8"))
BUILD_MARK = next(t["processes"][0] for t in _TIERS["tiers"] if t["name"] == "builds")
TEST_MARKS = next(t["processes"] for t in _TIERS["tiers"] if t["name"] == "tests")
MODEL_NAME = next(t["processes"][0] for t in _TIERS["tiers"] if t["name"] == "model")
assert TEST_MARKS, "guard-tiers.json: the tests tier declares no processes"
TEST_MARK = TEST_MARKS[0]
assert BUILD_MARK != TEST_MARK, "the builds and tests tiers name the same process"

MODEL_GB = 93.0   # the model in the 2026-10-24 measurement (RESIDENT 83453 MiB, ~93 GB)


def _fn():
    fn = getattr(serve, "quiet_window", None)
    assert callable(fn), ("serve.py defines no quiet_window: the model load has no quiet "
                          "window, which is the mechanism t1597 asks to build")
    return fn


def test_quiet_window_is_callable():
    assert callable(getattr(serve, "quiet_window", None)), \
        "serve.quiet_window does not exist (the missing change: no gate on loading)"


def test_default_margin_is_the_guard_pause_line():
    m = getattr(serve, "MARGIN_GB", None)
    assert m is not None, "serve.py declares no MARGIN_GB: the load budget is not a named number"
    assert float(m) == float(mg.DEFAULT_LEVELS["pause_gb"]), \
        f"MARGIN_GB is {m}, not the guard's pause line {mg.DEFAULT_LEVELS['pause_gb']}"


def test_nothing_in_flight_and_room_allows_the_start():
    r = _fn()(in_flight=[], footprint_gb=20, avail_gb=60)
    assert r["ok"] is True, f"a free box with room must allow the start: {r}"
    assert r["reasons"] == [], f"a permitted start must give no reasons: {r['reasons']}"


def test_a_build_in_flight_is_refused_and_named_by_pid():
    cmd = f"/usr/bin/python3 {BUILD_MARK} --target scripts/tests/test_x.py"
    r = _fn()(in_flight=[{"kind": "build", "pid": 429854, "cmd": cmd}],
              footprint_gb=20, avail_gb=60)
    assert r["ok"] is False, f"a build in flight must refuse the load: {r}"
    hit = [x for x in r["reasons"] if "build" in x and "429854" in x and BUILD_MARK in x]
    assert hit, (f"the refusal must name the kind, the pid and the process on one line, "
                 f"got {r['reasons']!r}")


def test_a_test_run_in_flight_is_refused_named_and_is_not_the_build_message():
    cmd = f"/usr/bin/python3 {TEST_MARK}"
    r = _fn()(in_flight=[{"kind": "tests", "pid": 429855, "cmd": cmd}],
              footprint_gb=20, avail_gb=60)
    assert r["ok"] is False, f"a test run in flight must refuse the load: {r}"
    hit = [x for x in r["reasons"] if "tests" in x and "429855" in x and TEST_MARK in x]
    assert hit, f"the refusal must name kind, pid and process, got {r['reasons']!r}"


def test_the_two_blockers_are_described_differently():
    """One repeated placeholder cannot satisfy both. A generic reason is a refusal nobody can
    act on, and the task asks the gate to \"say what is running\"."""
    a = _fn()(in_flight=[{"kind": "build", "pid": 429854, "cmd": f"python3 {BUILD_MARK} --target t"}],
              footprint_gb=20, avail_gb=60)
    b = _fn()(in_flight=[{"kind": "tests", "pid": 429855, "cmd": f"python3 {TEST_MARK}"}],
              footprint_gb=20, avail_gb=60)
    assert a["reasons"] and b["reasons"], f"both refusals must carry a reason: {a} {b}"
    assert a["reasons"] != b["reasons"], \
        f"a build and a test run must not produce the same reason text: {a['reasons']!r}"


def test_every_in_flight_blocker_gets_its_own_reason():
    items = [{"kind": "build", "pid": 429854, "cmd": f"python3 {BUILD_MARK} --target t"},
             {"kind": "tests", "pid": 429855, "cmd": f"python3 {TEST_MARK}"}]
    r = _fn()(in_flight=items, footprint_gb=20, avail_gb=60)
    assert r["ok"] is False, f"two blockers must refuse: {r}"
    assert len(r["reasons"]) == len(items), \
        f"expected one reason per blocker, got {len(r['reasons'])} for {len(items)}: {r['reasons']!r}"
    for it in items:
        assert any(str(it["pid"]) in x for x in r["reasons"]), \
            f"pid {it['pid']} is not named in {r['reasons']!r}"
    assert all(isinstance(x, str) for x in r["reasons"]), f"reasons must be strings: {r['reasons']!r}"


def test_a_model_too_big_for_the_box_is_refused_with_nothing_in_flight():
    """The 2026-10-24 shape. The arithmetic is the point: a gate that only scans the process
    list passes every other case in this file and fails this one."""
    r = _fn()(in_flight=[], footprint_gb=MODEL_GB, avail_gb=60)
    assert r["ok"] is False, f"a {MODEL_GB:g} GB model does not fit in 60 GB free: {r}"
    assert r["reasons"], "a refusal for size must say so"
    j = " ".join(r["reasons"])
    assert "93" in j and "60" in j, \
        f"the size refusal must name the footprint and the free memory it was checked " \
        f"against, got {j!r}"


def test_a_small_model_with_room_is_allowed():
    r = _fn()(in_flight=[], footprint_gb=2, avail_gb=60)
    assert r["ok"] is True, f"a 2 GB model in 60 GB free must be allowed: {r}"


def test_the_model_container_is_not_its_own_blocker():
    """DSH runs ON Swift (task note). Refusing to load Swift because a swift process is running
    makes the box unstartable, so the model tier is not a blocker for its own start."""
    items = [{"kind": "model", "pid": 7, "cmd": f"docker run --name {MODEL_NAME} vllm serve"}]
    r = _fn()(in_flight=items, footprint_gb=20, avail_gb=60)
    assert r["ok"] is True, f"the model's own container must not block its start: {r}"


def test_an_unrelated_process_is_not_a_blocker():
    items = [{"kind": "unrelated", "pid": 9, "cmd": "python3 scripts/session_end.py"}]
    r = _fn()(in_flight=items, footprint_gb=20, avail_gb=60)
    assert r["ok"] is True, f"an unlisted process must not block the load: {r}"


if __name__ == "__main__":
    _fails = 0
    for _name, _fn_ in sorted(list(globals().items())):
        if _name.startswith("test_") and callable(_fn_):
            try:
                _fn_()
                print("PASS", _name)
            except AssertionError as e:
                _fails += 1
                print("FAIL", _name, e)
            except Exception as e:                    # noqa: BLE001
                _fails += 1
                print(f"FAIL {_name} -- unexpected {type(e).__name__}: {e}")
    print(f"{_fails} failure(s)")
    sys.exit(1 if _fails else 0)
