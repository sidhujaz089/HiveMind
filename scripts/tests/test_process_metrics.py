#!/usr/bin/env python3
"""t084: name the program behind the load -- per-process CPU% over an interval.

Jas, 2026-08-22: "I want to be able to see which process is different programs that you use and
if I'm seeing latency issues or bottlenecking potentially this would help us identify some of the
causes."

lane_status.system_snapshot() (built under t209) gives the BOX's totals and cannot name a program;
this covers the naming half. CPU%, command line and resident memory per process, from a proc root
that is passed in.

RED on main: scripts/lane_status.py has no process_snapshots().
GREEN when process_snapshots(proc=Path("/proc"), top=5, hz=100, elapsed_s=1.0, sleep=None) computes
CPU%% from the DELTA of utime+stime between two reads of the proc root it is given (this fixture
stands in for /proc; `sleep` is the pause between the two reads, so the test controls the interval
and nothing sleeps), reports the command line whole, never lists the process doing the measuring,
and records a pid it cannot read instead of dropping it. No live state is read.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import lane_status as ls  # noqa: E402

FAILS = []

# utime and stime are the 12th and 13th tokens AFTER the closing ") " of comm; comm may contain
# spaces and parens, so line.split()[13] is the classic off-by-one. Parse with rsplit(") ", 1).
STAT = "{} ({}) S 1 1 1 0 0 0 0 0 0 0 {} {} 0 0 -1000 0 0 0 0 0 0 0 0 0 0\n"


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILS.append(label)


# pid -> (comm, argv, resident pages). The two samples of (utime, stime) are in FIRST/SECOND.
ROWS = {
    100: ("ollama", ["ollama", "serve"], 262144),
    200: ("python3", ["python3", "coder.py", "--files", "x.py"], 131072),
    300: ("Web Content", ["firefox", "-content"], 65536),
    400: ("llama-server", ["llama-server", "--model", "q4"], 32768),
    os.getpid(): ("the-test-itself", ["python3", "test_process_metrics.py"], 99999),
}
FIRST = {100: (1000, 500), 200: (500, 0), 300: (10, 5), 400: (100, 100), os.getpid(): (100, 100)}
SECOND = {100: (3000, 500), 200: (1500, 500), 300: (10, 5), 400: (4100, 100),
          os.getpid(): (99999, 99999)}


def write(t, pid, comm, argv, res, ticks):
    d = t / str(pid)
    d.mkdir(exist_ok=True)
    (d / "stat").write_text(STAT.format(pid, comm, ticks[0], ticks[1]), encoding="utf-8")
    (d / "cmdline").write_text("\0".join(argv) + "\0", encoding="utf-8")
    (d / "statm").write_text(f"{res * 2} {res} 0 0 0 0 0\n", encoding="utf-8")


def main():
    if not hasattr(ls, "process_snapshots"):
        print("  [BAD]  lane_status has no process_snapshots(): the missing change")
        return 1
    t = Path(tempfile.mkdtemp(prefix="t084-"))
    try:
        for pid, (comm, argv, res) in ROWS.items():
            write(t, pid, comm, argv, res, FIRST[pid])
        # A pid whose stat file is missing: unreadable. Must be recorded, not dropped, not a crash.
        (t / "999").mkdir()
        (t / "999" / "cmdline").write_text("broken\0--flag\0", encoding="utf-8")
        (t / "999" / "statm").write_text("8 4 0 0 0 0 0\n", encoding="utf-8")

        slept = []

        def sleep(s):
            slept.append(s)
            for pid, (comm, argv, res) in ROWS.items():
                write(t, pid, comm, argv, res, SECOND[pid])

        rows = ls.process_snapshots(proc=t, top=5, hz=100, elapsed_s=20.0, sleep=sleep)
        ck("it waited the interval it was given, not a hardcoded one", slept, [20.0])
        ck("the measuring process is not reported as its own top consumer",
           os.getpid() in [r["pid"] for r in rows], False)
        ck("the readable processes, most CPU first",
           [r["name"] for r in rows],
           ["llama-server", "ollama", "python3", "Web Content"])
        ck("CPU%% is the jiffie DELTA over the interval, not a cumulative total",
           [r["cpu_pct"] for r in rows], [200.0, 100.0, 75.0, 0.0])
        ck("the command line is recorded whole, not just the program name",
           [r["cmd"] for r in rows if r["pid"] == 200],
           ["python3 coder.py --files x.py"])
        ck("resident memory: 131072 pages x 4 KiB = 524288 KiB",
           [r["rss_kib"] for r in rows if r["pid"] == 200], [524288])
        ck("a comm containing a space is one name, not a split token",
           [r["name"] for r in rows if r["pid"] == 300], ["Web Content"])
        ck("a pid with no stat file is reported with an error, not dropped, not a crash",
           [(r["pid"], bool(r.get("error"))) for r in rows if r["pid"] == 999],
           [(999, True)])

        # top=2: same fixtures rewound to the FIRST sample, so the deltas are the same ones.
        for pid, (comm, argv, res) in ROWS.items():
            write(t, pid, comm, argv, res, FIRST[pid])
        ck("top=2 keeps the two busiest, by CPU",
           [r["name"] for r in ls.process_snapshots(proc=t, top=2, hz=100, elapsed_s=20.0,
                                                    sleep=sleep)],
           ["llama-server", "ollama"])
    finally:
        shutil.rmtree(t, ignore_errors=True)
    print("test_process_metrics:", "FAILED" if FAILS else "ok")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
