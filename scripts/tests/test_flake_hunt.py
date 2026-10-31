#!/usr/bin/env python3
"""Flake hunter (harness #3): a suite with two verdicts on ONE code tree is a flake; one that only
fails, or flips across trees, is not. Fixture ledgers only -- nothing is run.

Run: python3 scripts/tests/test_flake_hunt.py
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flake_hunt as F  # noqa: E402

NOW = time.time()


def journal(d, rows):
    p = Path(d) / "j.jsonl"
    p.write_text("".join(json.dumps({"kind": "suite", "epoch": NOW - 3600, "at": "t", **r}) + "\n" for r in rows))
    return p


def test_two_verdicts_on_one_tree_is_a_flake():
    with tempfile.TemporaryDirectory() as d:
        j = journal(d, [{"suite": "test_lane_manager_view.py", "tree": "A", "rc": 0},
                        {"suite": "test_lane_manager_view.py", "tree": "A", "rc": 1}])
        empty = Path(d) / "none.jsonl"
        assert list(F.flakes(hunt=empty, journal=j)) == [("test_lane_manager_view.py", "A")]


def test_a_constant_failure_is_not():
    with tempfile.TemporaryDirectory() as d:
        j = journal(d, [{"suite": "test_x.py", "tree": "A", "rc": 1}, {"suite": "test_x.py", "tree": "A", "rc": 1}])
        assert F.flakes(hunt=Path(d) / "none", journal=j) == {}


def test_red_then_green_across_trees_is_a_fix_not_a_flake():
    with tempfile.TemporaryDirectory() as d:
        j = journal(d, [{"suite": "test_x.py", "tree": "A", "rc": 1}, {"suite": "test_x.py", "tree": "B", "rc": 0}])
        assert F.flakes(hunt=Path(d) / "none", journal=j) == {}
        assert F.suspects(journal=j, flaky_log=Path(d) / "none") == []


def test_a_hunt_run_against_a_sweep_verdict_counts_and_is_logged_once():
    with tempfile.TemporaryDirectory() as d:
        j = journal(d, [{"suite": "test_hub_layout.py", "tree": "A", "rc": 0}])
        h = Path(d) / "h.jsonl"
        h.write_text(json.dumps({"at": "x", "hour_utc": "00", "tree": "A", "suite": "test_hub_layout.py",
                                 "rc": 1, "log": "workspace/run-logs/flake-hunt/x.log"}) + "\n")
        fl = Path(d) / "flaky.log"
        F.report(hunt=h, journal=j, flaky_log=fl)
        F.report(hunt=h, journal=j, flaky_log=fl)
        text = fl.read_text()
        assert text.count("flake hunt: test_hub_layout.py") == 1, text
        assert "workspace/run-logs/flake-hunt/x.log" in text


def test_a_timed_out_suite_leaves_no_descendant_alive():
    # 2026-10-30: the cap killed only the suite's own python; its `git apply -` lived on under PID 1.
    import os
    with tempfile.TemporaryDirectory() as d:
        pidfile = Path(d) / "grandchild.pid"
        (Path(d) / "test_hangs.py").write_text(
            "import subprocess, sys, time\n"
            f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
            "time.sleep(120)\n")
        rc, out = F.run_one("test_hangs.py", timeout=3, tests=d)
        assert rc == F.TIMED_OUT, (rc, out)
        pid = int(pidfile.read_text())
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
            state = Path(f"/proc/{pid}/stat").read_text().split(")")[1].split()[0]
            alive = state != "Z"
        except ProcessLookupError:
            alive = False
        if alive:
            os.kill(pid, 9)
        assert not alive, f"grandchild {pid} outlived the cap"


def test_a_hunt_timeout_is_not_a_flake():
    with tempfile.TemporaryDirectory() as d:
        j = journal(d, [{"suite": "test_coder_green_count.py", "tree": "A", "rc": 0}])
        h = Path(d) / "h.jsonl"
        h.write_text(json.dumps({"at": "x", "hour_utc": "12", "tree": "A", "suite": "test_coder_green_count.py",
                                 "rc": F.TIMED_OUT, "log": "x.log"}) + "\n")
        fl = Path(d) / "flaky.log"
        assert F.report(hunt=h, journal=j, flaky_log=fl) == {}
        assert not fl.exists() or "test_coder_green_count" not in fl.read_text()


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
