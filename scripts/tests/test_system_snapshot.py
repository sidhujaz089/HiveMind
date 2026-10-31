#!/usr/bin/env python3
"""t209: a point-in-time system snapshot for the Hub -- CPU, GPU, RAM, disk, temperature, network.

Jas: "I want to see overall CPU, GPU, RAM, SSD, temp, and network data. It can be a point in time
I load the page and not live." RED on main: lane_status has no system_snapshot(). GREEN when
system_snapshot(proc, sys_root, disk_path, run) reads every figure from the roots it is given (so
this fixture stands in for /proc and /sys) and gets the GPU through the `run` callable (so no
nvidia-smi is needed here). No live state is read.
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import lane_status as ls  # noqa: E402

FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILS.append(label)


def fixture(t):
    proc, sysr = t / "proc", t / "sys"
    (proc / "net").mkdir(parents=True)
    (proc / "loadavg").write_text("5.00 4.00 3.00 2/900 12345\n")
    (proc / "cpuinfo").write_text("".join(f"processor\t: {i}\nmodel name\t: x\n\n" for i in range(20)))
    (proc / "meminfo").write_text("MemTotal:       125829120 kB\nMemFree:  1 kB\nMemAvailable:    62914560 kB\n")
    (proc / "net" / "dev").write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo: 999999999 1 0 0 0 0 0 0 999999999 1 0 0 0 0 0 0\n"
        "  eth0: 1073741824 10 0 0 0 0 0 0 536870912 5 0 0 0 0 0 0\n"
        " wlan0: 1073741824 10 0 0 0 0 0 0 536870912 5 0 0 0 0 0 0\n")
    for z, mc in (("thermal_zone0", 45000), ("thermal_zone1", 83500)):
        d = sysr / "class" / "thermal" / z
        d.mkdir(parents=True)
        (d / "temp").write_text(f"{mc}\n")
    return proc, sysr


def main():
    if not hasattr(ls, "system_snapshot"):
        print("  [BAD]  lane_status has no system_snapshot(): the missing change")
        return 1
    t = Path(tempfile.mkdtemp(prefix="t209-"))
    try:
        proc, sysr = fixture(t)
        calls = []

        def run(cmd):
            calls.append(cmd)
            return 0, "95, 71\n"
        s = ls.system_snapshot(proc=proc, sys_root=sysr, disk_path=t, run=run)
        ck("CPU: 1-min load as a share of the cores", (s["cpu"]["load1"], s["cpu"]["cores"], s["cpu"]["load_pct"]),
           (5.0, 20, 25))
        ck("RAM: total and used from MemTotal/MemAvailable", (s["ram"]["total_gib"], s["ram"]["used_gib"], s["ram"]["used_pct"]),
           (120.0, 60.0, 50))
        ck("Disk: the given path's usage", (s["disk"]["total_gib"] > 0, 0 <= s["disk"]["used_pct"] <= 100), (True, True))
        ck("Temperature: the hottest thermal zone, in C", s["temp_c"], 83.5)
        ck("Network: rx/tx since boot, loopback excluded", (s["net"]["rx_gib"], s["net"]["tx_gib"]), (2.0, 1.0))
        ck("GPU: utilisation and temperature from nvidia-smi", (s["gpu"]["util_pct"], s["gpu"]["temp_c"]), (95, 71))
        ck("  asked nvidia-smi for exactly those two fields",
           bool(calls) and any("utilization.gpu,temperature.gpu" in str(a) for a in calls[0]), True)
        s2 = ls.system_snapshot(proc=proc, sys_root=sysr, disk_path=t, run=lambda c: (1, "not found"))
        ck("a GPU that cannot be read says so instead of raising", (s2["gpu"].get("util_pct"), bool(s2["gpu"].get("error"))),
           (None, True))
        s3 = ls.system_snapshot(proc=t / "nowhere", sys_root=t / "nowhere", disk_path=t, run=run)
        ck("a missing /proc file is reported, not a crash", bool(s3["cpu"].get("error")) and bool(s3["ram"].get("error")), True)
    finally:
        shutil.rmtree(t, ignore_errors=True)
    print("test_system_snapshot:", "FAILED" if FAILS else "ok")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
