#!/usr/bin/env python3
"""Flake hunter (harness #3, plan 2026-10-19): find the suites whose verdict depends on WHEN or
UNDER WHAT they run, not on the code.

    python3 scripts/flake_hunt.py run      # re-run the suspects now, no cache; keep every output
    python3 scripts/flake_hunt.py report   # suites with two verdicts on one code tree

Cron runs `run` twice a day, at 00:20 and 12:20 UTC: the weekend's two flakes were a suite red only
from 00:00 to 01:00 UTC (test_lane_manager_view) and one red only while the corpus index ran
(test_hub_layout). Each held commits and builds as "red on main" until someone noticed by hand.
The hourly sweep cannot see this: run_tests --cache skips a suite whose inputs have not changed, so
an unchanged suite is never run at a second time of day.

SUSPECTS: every suite with BOTH a pass and a failure on ONE code tree in workspace/sweep-journal.jsonl
in the last 14 days, plus every suite named in workspace/flaky-tests.log. Each run is one row in workspace/flake-hunt.jsonl
{at, hour_utc, tree, suite, rc, log}; its whole output is kept in workspace/run-logs/flake-hunt/.
A FLAKE is a (suite, tree) with both a pass and a failure across the hunt and the sweep journal. It
is reported with the hour of each verdict and the logs that hold both outputs, and appended once to
workspace/flaky-tests.log, which run_tests already treats as the flake record.
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "scripts" / "tests"
JOURNAL = REPO / "workspace" / "sweep-journal.jsonl"
FLAKY_LOG = REPO / "workspace" / "flaky-tests.log"
LEDGER = REPO / "workspace" / "flake-hunt.jsonl"
LOGS = REPO / "workspace" / "run-logs" / "flake-hunt"
WINDOW_S = 14 * 86400
SUITE_TIMEOUT_S = 600


def _rows(path):
    out = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return out
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            print(f"  NOTE: unreadable line in {Path(path).name}: {ln}", file=sys.stderr)
    return out


def tree():
    """run_tests' own tree id, so a hunt verdict and a sweep verdict on one tree are compared."""
    sys.path.insert(0, str(REPO / "scripts"))
    import run_tests
    return run_tests.tree_id()


def suspects(journal=None, flaky_log=None, now=None):
    """Suites that failed in the sweep journal inside the window, or are named in flaky-tests.log."""
    now = time.time() if now is None else now
    seen = {}
    for r in _rows(journal or JOURNAL):
        if r.get("kind") == "suite" and r.get("suite") and r.get("rc") is not None \
                and now - float(r.get("epoch") or 0) <= WINDOW_S:
            seen.setdefault((r["suite"], r.get("tree")), set()).add(r["rc"] == 0)
    # BOTH VERDICTS ON ONE TREE IN THE WINDOW. A suite that only ever fails is a constant, not a flake
    # (a declared red, or a real bug), and red-then-green across trees is usually a fix -- re-running
    # either twice a day would measure nothing.
    out = {s for (s, _t), v in seen.items() if v == {True, False}}
    try:
        text = Path(flaky_log or FLAKY_LOG).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        text = ""
    out |= set(re.findall(r"\b(test_\w+\.py)\b", text))
    return sorted(s for s in out if (TESTS / s).is_file())


TIMED_OUT = 124


def run_one(suite, timeout=SUITE_TIMEOUT_S, tests=TESTS):
    """(rc, output) for one suite, and on a timeout NOTHING it started is left alive.

    THE WHOLE GROUP, NOT THE CHILD (2026-10-30). This used subprocess.run(timeout=), which kills only
    the suite's own python: the coder suites' `git apply -` carried on, re-parented to PID 1, inside a
    1.5 GB repo copy that the dead suite's `finally` never removed. Four were found in RAM-backed /tmp
    (6.2 GB, 37-77 min old) and the workspace check went red. run_tests.py:570 already does this right.
    """
    p = subprocess.Popen(["nice", "-n", "15", sys.executable, str(Path(tests) / suite)], cwd=str(REPO),
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)               # the suite and everything it started
        except ProcessLookupError:
            p.poll()                                        # the group already exited: nothing to kill
        out, _ = p.communicate()
        return TIMED_OUT, f"TIMED OUT after {timeout} s\n{out or ''}"


def run(suites=None):
    LOGS.mkdir(parents=True, exist_ok=True)
    t, hour = tree(), time.strftime("%H", time.gmtime())
    rows = []
    for s in suites if suites is not None else suspects():
        log = LOGS / f"{s[:-3]}--{time.strftime('%Y%m%dT%H%MZ', time.gmtime())}.log"
        rc, out = run_one(s)
        log.write_text(out, encoding="utf-8")
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "hour_utc": hour, "tree": t,
               "suite": s, "rc": rc, "log": str(log.relative_to(REPO))}
        rows.append(row)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"  {'pass' if rc == 0 else 'FAIL'}  {s}")
    return rows


def flakes(hunt=None, journal=None):
    """{(suite, tree): [verdict rows]} for every suite with both a pass and a failure on one tree."""
    seen = {}
    for r in _rows(journal or JOURNAL):
        if r.get("kind") == "suite" and r.get("suite") and r.get("tree"):
            seen.setdefault((r["suite"], r["tree"]), []).append(
                {"at": r.get("at"), "rc": r.get("rc"), "log": None, "via": "sweep"})
    for r in _rows(hunt or LEDGER):
        seen.setdefault((r["suite"], r["tree"]), []).append(
            {"at": r.get("at"), "rc": r.get("rc"), "log": r.get("log"), "via": "hunt"})
    return {k: v for k, v in seen.items() if any(x["rc"] == 0 for x in v) and any(map(_failed, v))}


def _failed(x):
    """A red verdict. A TIMEOUT OF THE HUNT'S OWN IS NOT ONE: its cap firing says the suite did not finish,
    not that the code is red. 40 hunt rows to 2026-10-30 were rc 124, nearly all coder suites stuck carrying
    a 109 MB working-tree patch; counted as failures they would log a flake the code never had."""
    return x["rc"] not in (0, None) and not (x["via"] == "hunt" and x["rc"] == TIMED_OUT)


def report(hunt=None, journal=None, flaky_log=None):
    found = flakes(hunt, journal)
    path = Path(flaky_log or FLAKY_LOG)
    try:
        already = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        already = ""
    for (suite, t), v in sorted(found.items()):
        fails = [x for x in v if _failed(x)]
        passes = [x for x in v if x["rc"] == 0]
        line = (f"flake hunt: {suite} on tree {t} passed at {passes[-1]['at']} and failed at "
                f"{fails[-1]['at']} (rc {fails[-1]['rc']}); outputs: "
                f"{fails[-1]['log'] or 'sweep, not kept'} / {passes[-1]['log'] or 'sweep, not kept'}")
        print("  " + line)
        if f"flake hunt: {suite} on tree {t} " not in already:
            with path.open("a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M ", time.gmtime()) + line + "\n")
    print(f"  {len(found)} flaky suite(s)")
    return found


def main(argv):
    if argv[:1] == ["run"]:
        run()
        report()
        return 0
    if argv[:1] == ["report"]:
        report()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
