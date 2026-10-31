#!/usr/bin/env python3
"""Why is this lane idle? (harness #4): each idle model role gets the first blocker that applies, in
order -- scheduler paused, device paused, server not answering, memory guard, dispatcher not
ticking, its queued work's reason, nothing waiting. Every input is injected; nothing live is read.

Run: python3 scripts/tests/test_lane_idle.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lane_idle as LI  # noqa: E402

D = {"roles": {
    "jas": {"kind": "person", "tasks": [{"task": "assigned_jas"}]},
    "manager": {"name": "Manager", "kind": "model", "model": "swift", "lanes": 2,
                "tasks": [{"task": "scoping"}, {"task": "building"}]},
    "auditor": {"name": "Auditor", "kind": "model", "model": "qwen-tp", "lanes": 1, "tasks": [{"task": "rca"}]},
    "embedder": {"name": "Embedder", "kind": "model", "model": "emb", "lanes": 0, "tasks": [{"task": "corpus_index"}]},
}}
BASE = dict(d=D, running=["auditor"], tick=[], tick_age_s=60, paused=False, devices={}, down={}, guard={})


def why(**over):
    rows = LI.reasons(**{**BASE, **over})
    return {r["role"]: r["reason"] for r in rows}


def test_only_roles_with_a_free_lane_and_lanes_on():
    assert set(why()) == {"manager"}          # auditor busy, embedder 0 lanes = off, jas not a model


def test_scheduler_pause_comes_first():
    assert "scheduler is paused" in why(paused=True, devices={"gx10": {"who": "jas", "why": "x"}})["manager"]


def test_device_pause_names_who():
    assert "gx10 is paused (jas: from the Hub)" in why(devices={"gx10": {"who": "jas", "why": "from the Hub"}})["manager"]


def test_server_not_answering():
    assert "does not answer" in why(down={"swift": "NoServer: none answers"})["manager"]


def test_memory_guard():
    g = {"at": "2026-10-20T00:23:33Z", "stopped": "builds", "available_gb": 12.1}
    assert "memory guard stopped builds" in why(guard=g)["manager"]


def test_a_stale_tick():
    assert "has not ticked for 20 min" in why(tick_age_s=1200)["manager"]


def test_queued_work_gives_its_reason():
    tick = ["    queued    assigned_items t1096: swift is doing priority work"]
    assert why(tick=tick)["manager"] == "its work is queued: swift is doing priority work"


def test_no_build_lane_for_a_building_role():
    tick = ["  no build lane this tick: 9.0 GB free cannot admit a build"]
    assert "no build lane this tick" in why(tick=tick)["manager"]


def test_nothing_waiting_is_said_plainly():
    assert why()["manager"] == "nothing was waiting for it in the last tick"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for n, f in tests:
        try:
            f()
            print(f"PASS {n}")
        except Exception as e:                                      # noqa: BLE001
            bad += 1
            print(f"FAIL {n}: {type(e).__name__}: {e}")
    print(f"{len(tests) - bad} passed, {bad} failed")
    sys.exit(1 if bad else 0)
