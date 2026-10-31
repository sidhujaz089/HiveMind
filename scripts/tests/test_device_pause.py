#!/usr/bin/env python3
"""Independent memory guards (Jas, 2026-10-13: "I don't want the memory guard on the GX10 to also stop
the ThinkPad"). The guard pauses the GX10 alone; the tick holds GX10 jobs and starts ThinkPad ones;
Resume clears every pause. Hermetic: flags live in a temp dir, systemctl and docker are fakes."""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import dispatch_loop as DL  # noqa: E402
import memory_guard as MG  # noqa: E402
import scheduler_commands as SC  # noqa: E402

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


THINKPAD = "qwen3.8:27b-mtp-q4_K_M-56k"
DL.DEVICE_OF[THINKPAD] = "thinkpad"          # as SERVERS.json/REGISTRY declare it
DL.DEVICE_OF.pop("swift", None)              # unlisted -> the GX10, the default

with tempfile.TemporaryDirectory() as td:
    SC.PAUSE_FLAG = Path(td) / "scheduler-paused.enabled"
    # t-3b (2026-10-23): SC.DEVICE_PAUSE_LEDGER is a module constant, read at call time
    # inside _record_device_pause; redirecting PAUSE_FLAG does NOT move it. This suite
    # wrote 648 fake rows into the live workspace/run-logs/device-pause.jsonl before
    # handover 2026-10-23 item 5 named it. Redirect the ledger, then prove the guard:
    SC.DEVICE_PAUSE_LEDGER = Path(td) / "device-pause.jsonl"
    try:
        SC.refuse_live_device_pause_ledger(REPO / "workspace" / "run-logs" / "device-pause.jsonl")
        check("the live-ledger guard refuses the live path in a test process", False, True)
    except RuntimeError:
        check("the live-ledger guard refuses the live path in a test process", True, True)
    calls = []
    SC._systemctl = lambda *a: (calls.append(a), type("R", (), {"returncode": 0, "stderr": ""})())[1]

    # --- the guard's key pauses the GX10 only
    led = Path(td) / "guard.jsonl"
    row = MG.trip(1.5, kill=lambda n: True, ledger=led, names=("swift",))
    check("the guard paused the GX10", sorted(SC.paused_devices()), ["gx10"])
    check("the guard did NOT turn the master key", SC.is_paused(), False)
    check("the guard stopped production.target (Swift, Manager queue)", calls[-1], ("stop",))
    check("the trip row says the key went off", row["key_off"], True)

    # --- the tick: ThinkPad work starts, GX10 work waits, each said
    p = [{"kind": "rca", "key": "a.json", "model": THINKPAD},
         {"kind": "script-audit", "key": "s.json", "model": "swift"}]
    c = [{"kind": "console", "key": "m1", "model": "swift"}]
    b = [{"chunk": "p1-t1-x", "model": "swift"}]
    queued, skipped = [], []
    p2, c2, b2 = DL.device_hold(p, c, b, queued, skipped)
    check("the ThinkPad's RCA still starts", [j["key"] for j in p2], ["a.json"])
    check("Swift's audit waits", [j["key"] for j, _ in queued], ["s.json", "m1"])
    check("the console message waits", c2, [])
    check("the build waits", (b2, [n for n, _ in skipped]), ([], ["p1-t1-x"]))
    check("the reason names the device and the guard", "gx10 is paused (memory-guard" in queued[0][1], True)

    # --- nothing paused: nothing held
    q3, s3 = [], []
    out = DL.device_hold(p, c, b, q3, s3, paused={})
    check("no pause holds nothing", (len(out[0]), len(out[1]), len(out[2]), q3, s3), (2, 1, 1, [], []))

    # --- Resume is one button
    SC.breaker_on()
    check("Resume clears the device pause", SC.paused_devices(), {})
    check("Resume starts production.target", calls[-1], ("start",))

    # --- the ThinkPad's own button (Jas, 2026-10-13): the GX10 keeps going
    SC.pause_device("thinkpad", "jas", "paused from the Hub")
    q4, s4 = [], []
    p4, _c4, b4 = DL.device_hold(p, c, b, q4, s4)
    check("ThinkPad paused: Swift's audit still starts", [j["key"] for j in p4], ["s.json"])
    check("ThinkPad paused: its RCA waits", [j["key"] for j, _ in q4], ["a.json"])
    check("ThinkPad paused: GX10 builds still start", [j["chunk"] for j in b4], ["p1-t1-x"])
    SC.resume_device("thinkpad")
    check("Resume ThinkPad clears only it", SC.paused_devices(), {})

    # --- Pause GX10 / Resume GX10, the Hub's first button (Jas, 2026-10-13: "manage them separately")
    # A person's gx10_off stops the units through subprocess.run, not _systemctl (b9cccaaca). Unfaked,
    # this line stopped the LIVE Manager and Auditor on every run (t1557); fake it here.
    import subprocess
    units_stopped = []
    _real_run = subprocess.run
    subprocess.run = lambda cmd, *a, **k: (units_stopped.append(list(cmd)), subprocess.CompletedProcess(
        cmd, 0, "production.target\nmanager-queue.service\nswift.service\n" if "list-dependencies" in cmd else "",
        ""))[1]
    SC.pause_device("thinkpad", "jas", "eGPU unplugged")
    try:
        ok, _ = SC.gx10_off("jas", "paused from the Hub")
    finally:
        subprocess.run = _real_run
    check("Pause GX10 stops the units except Swift, through the fake",
          [c for c in units_stopped if "stop" in c], [["systemctl", "--user", "--no-block", "stop", "manager-queue.service"]])
    check("Pause GX10 pauses only the GX10 (and the ThinkPad keeps its own)", sorted(SC.paused_devices()), ["gx10", "thinkpad"])
    SC.gx10_on()
    check("Resume GX10 leaves the ThinkPad paused", sorted(SC.paused_devices()), ["thinkpad"])
    check("Resume GX10 starts production.target", calls[-1], ("start",))
    SC.resume_device("thinkpad")

    # --- a reboot leaves both off, each resumed on its own
    SC.boot()
    check("boot pauses both devices", sorted(SC.paused_devices()), ["gx10", "thinkpad"])
    SC.gx10_on()
    check("after a reboot, Resume GX10 does not start the ThinkPad", sorted(SC.paused_devices()), ["thinkpad"])
    check("and clears the reboot's global flag", SC.is_paused(), False)
    SC.resume_device("thinkpad")

    # --- the master key still stops everything
    SC.breaker_off("jas", "test")
    check("the master key still pauses everything", SC.is_paused(), True)
    SC.resume()

# --- the embedder follows the pause (memory.embed_url): a paused ThinkPad is not called
import memory as M  # noqa: E402
_real = M.REPO
with tempfile.TemporaryDirectory() as td2:
    t = Path(td2)
    (t / "projects/local-inference/models").mkdir(parents=True)
    (t / "workspace/run-logs").mkdir(parents=True)
    (t / "projects/local-inference/models/SERVERS.json").write_text(json.dumps(
        {"servers": {"ollama-thinkpad": {"url": "http://10.9.9.9:11434", "device": "thinkpad"}}}))
    M.REPO, M.OLLAMA, M._URL = t, None, "http://10.9.9.9:11434/api/embed"
    check("ThinkPad running: the embedder calls it", M.embed_url(), "http://10.9.9.9:11434/api/embed")
    (t / "workspace/run-logs/device-paused-thinkpad.enabled").write_text("{}")
    check("ThinkPad paused: the embedder calls the GX10", M.embed_url(), M.OLLAMA_DEFAULT)
    M.REPO = _real

print(f"test_device_pause: {'FAILED' if fails else 'ok'} ({fails} failure(s))")
sys.exit(1 if fails else 0)
