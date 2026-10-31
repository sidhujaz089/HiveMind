#!/usr/bin/env python3
"""t229: the log baseline reports what is NEW or has CHANGED RATE, and stays quiet about chronic noise.

The case it exists for: 2026-09-05, 293 EINVALs appeared in the desktop app's main.log where the previous day had
none, and nobody saw them for two days. Hermetic: synthetic events, a fake journalctl, a temp log folder."""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import log_baseline as lb  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    print(f"  [{'ok' if cond else 'BAD'}]  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


NOW = time.mktime(time.strptime("2026-09-06 12:00:00", "%Y-%m-%d %H:%M:%S"))
H = 3600
einval = [(NOW - i * 60, "desktop-app", f"[error] EINVAL: invalid argument, open '/home/x/.config/Claude/p{i}'")
          for i in range(293)]
chronic = [(NOW - d * 24 * H - 600, "journal", "systemd[2554]: IBus: Two services allocated, refusing operation.")
           for d in range(8)]
spike_base = [(NOW - (d * 24 + 5) * H, "desktop-app", "[warn] slow poll 1200 ms") for d in range(1, 8)]
spike_now = [(NOW - i * 300, "desktop-app", f"[warn] slow poll {900 + i} ms") for i in range(40)]
found = lb.diff(einval + chronic + spike_base + spike_now, now=NOW)
kinds = {(f["kind"], f["example"][:30]) for f in found}
ck("293 errors that did not exist the week before are NEW (the 2026-09-05 EINVAL case)",
   any(f["kind"] == "new" and f["recent"] == 293 for f in found), found)
ck("  counted once, not 293 times: ids and numbers are normalised", sum(f["kind"] == "new" for f in found) == 1, found)
ck("  and the example is the whole newest line", any(f["example"].endswith("p0'") for f in found), found)
ck("a chronic once-a-day error is NOT reported (the noise that gets an alert muted)",
   not any("IBus" in f["example"] for f in found), found)
ck("a message whose rate jumps 1/day -> 40/day is a SPIKE", any(f["kind"] == "spike" and f["recent"] == 40 for f in found),
   found)

d = Path(tempfile.mkdtemp())
(d / "main.log").write_text("2026-09-06 11:00:00 [error] boom 1\n2026-09-06 11:00:01 [info] fine\n")
ev = lb.desktop_events(d)
ck("desktop log lines at [error]/[warn] are read, [info] is not", [e[2] for e in ev] == ["[error] boom 1"], ev)
ck("a folder with no main*.log is None (not read), not []", lb.desktop_events(Path(tempfile.mkdtemp())) is None)


def fake(*a, **k):
    return subprocess.CompletedProcess(a, 0, "2026-09-06T10:00:00-06:00 HiveMind app[12]: it failed\n", "")


ck("the journal's short-iso lines are parsed", [e[2] for e in lb.journal_events(run=fake)] == ["app[12]: it failed"])
lines, _ = lb.report(desk=[], jour=[], now=NOW)
ck("both sources read and quiet says so", "nothing new" in "\n".join(lines), lines)
_orig = lb.journal_events
lb.journal_events = lambda: None
lines, _ = lb.report(desk=[], now=NOW)
lb.journal_events = _orig
ck("an unreadable source is NAMED, never reported as quiet", any("NOT READ" in l and "journal" in l for l in lines)
   and not any("nothing new" in l for l in lines), lines)

print("test_log_baseline:", "FAILED" if FAILS else "ok")
sys.exit(1 if FAILS else 0)
