"""Resume sweeps leftover temp dirs only after an interruption -- a memory guard or a reboot -- never
after an ordinary pause (Jas, 2026-10-20). And the sweep after one never touches a live builder's
sandbox: a coder-<time>-<pid> dir is swept only when that pid is gone."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import scheduler_commands as sc  # noqa: E402
import sweep  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


dead = subprocess.Popen([sys.executable, "-c", "pass"])
dead.wait()
live = os.getpid()
E = sweep.tmp_eligible
report("a live builder's sandbox is never eligible, however old", E(f"coder-1-{live}", 10 ** 6, set()), False)
report("a dead builder's sandbox is eligible at any age", E(f"coder-1-{dead.pid}", 0, set()), True)
report("another creator dir under the resume floor is not", E("mergegate-abc", 100, set(), min_age_s=300), False)
report("  and is once past it", E("mergegate-abc", 400, set(), min_age_s=300), True)
report("the 15-minute sweeper's hour still holds by default", E("mergegate-abc", 400, set()), False)
report("a dir a live process holds is never eligible", E("mergegate-abc", 10 ** 6, {"/x/mergegate-abc"},
                                                        path="/x/mergegate-abc"), False)

tmp = Path(tempfile.mkdtemp())
saved = (sc.PAUSE_FLAG, sc.DEVICE_PAUSE_LEDGER, sc._systemctl, sweep.sweep_after_interruption)
calls = []
sc.PAUSE_FLAG = tmp / "scheduler-paused.enabled"
sc.DEVICE_PAUSE_LEDGER = tmp / "device-pause.jsonl"
sc._systemctl = lambda *a: subprocess.CompletedProcess(a, 0, "", "")
sweep.sweep_after_interruption = lambda *a, **k: calls.append(1) or []
try:
    for who, want in (("memory-guard", 1), ("jas", 0), ("boot", 1)):
        calls.clear()
        sc.pause_device("gx10", who, "test")
        sc.gx10_on()
        report(f"GX10 Resume after a {who!r} pause sweeps {want} time(s)", len(calls), want)
    calls.clear()
    sc.pause("boot", "test")
    for dev in ("gx10", "thinkpad"):
        sc.pause_device(dev, "boot", "test")
    sc.breaker_on()
    report("the one-button Resume after a reboot sweeps once, not per device", len(calls), 1)
    for who, want in (("thinkpad-guard", 1), ("jas", 0)):
        calls.clear()
        sc.pause_device("thinkpad", who, "test")
        sc.resume_device("thinkpad")
        report(f"ThinkPad Resume after a {who!r} pause sweeps {want} time(s)", len(calls), want)
    calls.clear()
    sc.gx10_on()
    report("resuming what was not paused sweeps nothing", len(calls), 0)
finally:
    sc.PAUSE_FLAG, sc.DEVICE_PAUSE_LEDGER, sc._systemctl, sweep.sweep_after_interruption = saved
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
