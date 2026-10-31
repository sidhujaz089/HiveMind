#!/usr/bin/env python3
"""thinkpad_guard.decide: unload a model that spilled out of the eGPU into RAM, and everything when RAM is
under the reserve (Jas, 2026-10-13: the ThinkPad froze when a model loaded into its 15.7 GB of RAM)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import thinkpad_guard as G  # noqa: E402

FAILED = 0


def report(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


GB = 1e9
on_gpu = {"name": "q4", "size": 17.7 * GB, "size_vram": 17.7 * GB}
spilled = {"name": "gemma", "size": 10.1 * GB, "size_vram": 0}
partial = {"name": "half", "size": 10 * GB, "size_vram": 6 * GB}
report("all on the GPU, RAM fine: nothing", G.decide([on_gpu], 6.9), ([], None))
report("a model entirely in RAM is unloaded", G.decide([on_gpu, spilled], 6.9)[0], ["gemma"])
report("a model partly in RAM is unloaded", G.decide([partial], 6.9)[0], ["half"])
report("RAM under the reserve unloads everything", G.decide([on_gpu, spilled], 1.2)[0], ["q4", "gemma"])
report("the reason names the RAM", "1.20 GB free" in G.decide([on_gpu], 1.2)[1], True)
report("an unreadable RAM figure still catches a spill", G.decide([spilled], None)[0], ["gemma"])

# A LOAD IS NOT A SHORTAGE (2026-10-14): a dip must last SUSTAIN_S, unless under the hard floor or a spill.
import tempfile, time  # noqa: E402
import scheduler_commands  # noqa: E402
acted = []
G.device = lambda: ("u", ["ssh"])
G.unload = lambda s, n: acted.append(n) or True
scheduler_commands.pause_device = lambda *a: acted.append("pause")
G.LEDGER = Path(tempfile.mkdtemp()) / "guard.jsonl"
G.loaded = lambda u: [on_gpu]
G.free_ram_gb = lambda s: 0.6
G._low_since = None
report("a dip while a model loads waits", (G.check()["action"], acted), (None, []))
G._low_since = time.time() - G.SUSTAIN_S - 1
report("the same dip lasting SUSTAIN_S unloads and pauses", (bool(G.check()["action"]), acted), (True, ["q4", "pause"]))
acted.clear(); G._low_since = None; G.free_ram_gb = lambda s: G.HARD_FLOOR_GB / 2
report("under the hard floor it acts at once", (bool(G.check()["action"]), acted), (True, ["q4", "pause"]))
acted.clear(); G._low_since = None; G.free_ram_gb = lambda s: 0.6; G.loaded = lambda u: [spilled]
report("a spill with low RAM acts at once", bool(G.check()["action"]), True)

# TEN MINUTES BETWEEN LOADS: the guard resumes its OWN pause after RESUME_AFTER_S, never a person's.
resumed = []
scheduler_commands.resume_device = lambda d: resumed.append(d)
stamp = lambda ago: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago))  # noqa: E731
scheduler_commands.paused_devices = lambda: {"thinkpad": {"who": "thinkpad-guard", "at": stamp(60)}}
report("its own pause, 1 minute old: still paused", (G._resume_own_pause(6.7), resumed), (False, []))
scheduler_commands.paused_devices = lambda: {"thinkpad": {"who": "thinkpad-guard", "at": stamp(G.RESUME_AFTER_S + 5)}}
report("its own pause, past RESUME_AFTER_S: resumed", (G._resume_own_pause(6.7), resumed), (True, ["thinkpad"]))
resumed.clear()
scheduler_commands.paused_devices = lambda: {"thinkpad": {"who": "jas", "at": stamp(G.RESUME_AFTER_S * 3)}}
report("a person's pause is never resumed by the guard", (G._resume_own_pause(6.7), resumed), (False, []))
print(f"\n{'FAILED' if FAILED else 'ok'}: {FAILED} failure(s)")
sys.exit(1 if FAILED else 0)
