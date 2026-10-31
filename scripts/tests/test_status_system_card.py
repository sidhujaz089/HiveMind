#!/usr/bin/env python3
"""t209: the Hub's Status page shows a System card -- CPU, GPU, RAM, Disk, Temp, Network.

RED on main: feedback.py has no system_card() and status_page() never shows a system snapshot.
GREEN when system_card(snapshot) renders a lane_status.system_snapshot() dict -- called here on a
fixture dict, no live state -- and status_page() puts it on the page. The figures' sources are
pinned by test_system_snapshot.py.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
sys.path.insert(0, str(ROOT / "scripts"))
import feedback as f  # noqa: E402

FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILS.append(label)


SNAP = {"cpu": {"load1": 5.0, "cores": 20, "load_pct": 25},
        "ram": {"total_gib": 120.0, "used_gib": 60.0, "used_pct": 50},
        "disk": {"total_gib": 1863.0, "used_pct": 41},
        "temp_c": 83.5,
        "net": {"rx_gib": 2.0, "tx_gib": 1.0},
        "gpu": {"util_pct": 95, "temp_c": 71}}


def main():
    if not hasattr(f, "system_card"):
        print("  [BAD]  feedback has no system_card(): the missing change")
        return 1
    h = f.system_card(SNAP)
    ck("every heading is on the card", all(x in h for x in ("CPU", "GPU", "RAM", "Disk", "Temp", "Network")), True)
    ck("each figure is shown", all(x in h for x in ("25%", "95%", "60.0", "120.0", "41%", "83.5", "2.0", "1.0")), True)
    bad = dict(SNAP, gpu={"error": "nvidia-smi: <not found>"}, ram={"error": "no meminfo"})
    hb = f.system_card(bad)
    ck("an unreadable figure shows its error, HTML-escaped", ("&lt;not found&gt;" in hb, "<not found>" in hb), (True, False))
    ck("  and the readable ones still show", "25%" in hb, True)
    src = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    s0 = src.find("def status_page(")
    body = src[s0:src.find("\ndef ", s0 + 1)] if s0 >= 0 else ""
    ck("status_page renders system_card(lane_status.system_snapshot(...))",
       ("system_card(" in body, "system_snapshot(" in body), (True, True))
    print("test_status_system_card:", "FAILED" if FAILS else "ok")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
