#!/usr/bin/env python3
"""A memory safety stop for the ThinkPad eGPU host, run from the GX10 over the direct cable.

    python3 scripts/thinkpad_guard.py run       # the service (thinkpad-guard.service), every 15 s
    python3 scripts/thinkpad_guard.py once      # one check, printed

WHY (Jas, 2026-10-13): "Even though it didn't have a job it was loaded and used my RAM up and that's why
the PC stopped responding ... Can we create a memory safety stop on the ThinkPad as well in case this
happens again?" That night the ThinkPad (15.7 GB of RAM) froze at full CPU and Jas had to hold the power
button. Two shapes put a model in its system RAM instead of the eGPU's 24 GB: a model that does not fit
beside what the GPU already holds (Frontline's gemma4:e4b, 9.6 GB, beside the Q4's 17.7 GB), and an
Ollama that started before the eGPU was ready and so sees no GPU at all (after the forced restart even a
1 GB model loaded with 0 on the GPU). Either way the RAM fills and Windows stops answering.

WHAT IT DOES, every 15 s, against SERVERS.json's thinkpad device (one source of truth, D-39):
  1. A loaded model with less than SPILL_FRACTION of itself on the GPU -> unload it, pause the ThinkPad.
  2. Free RAM under RESERVE_GB (read over SSH with the device's key) -> unload every model, pause.
  One row per action in workspace/thinkpad-guard.jsonl. It never resumes anything: Jas presses Resume
  ThinkPad when he has looked, as with the GX10's memory guard.
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
SERVERS = REPO / "projects" / "local-inference" / "models" / "SERVERS.json"
LEDGER = REPO / "workspace" / "thinkpad-guard.jsonl"
DEVICE = "thinkpad"
# 2.0 -> 1.5 (Jas, 2026-10-14): the Q4 on the eGPU still takes ~5 GB of Windows RAM, and at 2.0 the guard
# unloaded it six times in two minutes at 1.68-1.88 GB free (thinkpad-guard.jsonl 22:28-22:30Z).
RESERVE_GB = 1.5
SPILL_FRACTION = 0.95
# A LOAD IS NOT A SHORTAGE (2026-10-14). Loading the 17.5 GB Q4 passes the file through Windows RAM: free
# RAM dipped to 0.37-0.65 GB for about a minute, then settled at 6.7 GB with the Q4 fully on the eGPU (Jas
# saw 9 GB free and 21 GB of eGPU in use). The guard unloaded it mid-load, the waiting call loaded it
# again, round and round. RAM under the reserve must now LAST this long before the guard acts -- unless it
# is under the hard floor, which still acts at once.
SUSTAIN_S = 150
HARD_FLOOR_GB = 0.2
_low_since = None
# TEN MINUTES BETWEEN LOADS (Jas, 2026-10-14: "let's adjust the safety shut off so it wait 10 min between
# model loads"). A trip pauses the ThinkPad; the guard itself resumes it RESUME_AFTER_S later, if RAM is
# back over the reserve and the pause is still its own (a person's or another guard's pause is left alone).
RESUME_AFTER_S = 600
EVERY_S = 15


def device():
    """(ollama url, ssh argv prefix or None) for the ThinkPad, from SERVERS.json."""
    d = json.loads(SERVERS.read_text(encoding="utf-8"))
    url = next(s["url"] for s in d["servers"].values() if s.get("device") == DEVICE)
    ssh = (d["devices"][DEVICE] or {}).get("ssh")
    argv = None
    if ssh:
        argv = ["ssh", "-i", str(Path(ssh["key"]).expanduser()), "-o", "BatchMode=yes", "-o", "LogLevel=ERROR",
                "-o", "ConnectTimeout=5", f"{ssh['user']}@{d['devices'][DEVICE]['host']}"]
    return url.rstrip("/"), argv


def loaded(url):
    with urllib.request.urlopen(url + "/api/ps", timeout=8) as f:
        return json.load(f).get("models") or []


def free_ram_gb(ssh):
    if not ssh:
        return None
    cmd = ('powershell -NoLogo -Command "'
           '(Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory).AvailableMBytes"')
    r = subprocess.run(ssh + [cmd], capture_output=True, text=True, timeout=20)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return float(r.stdout.strip()) / 1024.0
    except ValueError:
        return None


def cpu_models():
    """Models SERVERS.json places on the ThinkPad's CPU on purpose."""
    try:
        d = json.loads(SERVERS.read_text(encoding="utf-8"))
        return {name for s in d["servers"].values() if s.get("device") == DEVICE
                for name, m in (s.get("models") or {}).items() if (m or {}).get("placement") == "cpu"}
    except (OSError, ValueError, KeyError) as e:
        print(f"thinkpad-guard: SERVERS.json unreadable for cpu placements ({e}); none exempt", flush=True)
        return set()


def decide(models, free_gb, reserve=RESERVE_GB, spill=SPILL_FRACTION):
    """Pure. -> (models to unload, reason) or ([], None)."""
    if free_gb is not None and free_gb < reserve:
        return [m["name"] for m in models], f"ThinkPad RAM {free_gb:.2f} GB free, under the {reserve} GB reserve"
    # A MODEL DECLARED FOR THE CPU IS MEANT TO BE THERE (2026-10-14): the embedder lives on the
    # ThinkPad CPU (SERVERS.json placement "cpu", ~1.4 GB) so the eGPU can cycle Auditor <-> Frontline.
    # Its RAM still counts toward the reserve check above.
    spilled = [m for m in models if m.get("size") and (m.get("size_vram") or 0) < spill * m["size"]
               and m.get("name") not in cpu_models()]
    if spilled:
        what = ", ".join(f"{m['name']} ({(m.get('size_vram') or 0) / 1e9:.1f} of {m['size'] / 1e9:.1f} GB on the GPU)"
                         for m in spilled)
        return [m["name"] for m in spilled], f"loaded outside the eGPU, into system RAM: {what}"
    return [], None


def unload(ssh, name):
    """`ollama stop <name>` on the ThinkPad -- Ollama's own unload; not a model run."""
    if not ssh:
        print(f"thinkpad-guard: no SSH for the ThinkPad in SERVERS.json; cannot unload {name}", flush=True)
        return False
    r = subprocess.run(ssh + [f"ollama stop {name}"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print(f"thinkpad-guard: could not unload {name}: {(r.stderr or r.stdout).strip()}", flush=True)
    return r.returncode == 0


def check(act=True):
    url, ssh = device()
    models = loaded(url)
    free = free_ram_gb(ssh)
    names, why = decide(models, free)
    global _low_since
    low = free is not None and free < RESERVE_GB
    if not low:
        _low_since = None
    elif free >= HARD_FLOOR_GB and not decide(models, None)[0]:      # a spill never waits
        now = time.time()
        _low_since = _low_since or now
        if now - _low_since < SUSTAIN_S:
            print(f"thinkpad-guard: RAM {free:.2f} GB free for {now - _low_since:.0f}s -- a load passes "
                  f"through RAM; acting only after {SUSTAIN_S}s", flush=True)
            return {"free_ram_gb": free, "models": [(m["name"], m.get("size"), m.get("size_vram")) for m in models],
                    "action": None, "waiting_s": round(now - _low_since)}
    if act and not why and not low:
        _resume_own_pause(free)
    if why and act:
        done = [n for n in names if unload(ssh, n)]
        import scheduler_commands
        scheduler_commands.pause_device(DEVICE, "thinkpad-guard", why)
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "free_ram_gb": free, "why": why,
               "unloaded": done, "tried": names}
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"thinkpad-guard: TRIPPED -- {why}; unloaded {done or 'nothing'}; ThinkPad paused", flush=True)
    return {"free_ram_gb": free, "models": [(m["name"], m.get("size"), m.get("size_vram")) for m in models],
            "action": why}


def _resume_own_pause(free, now=None):
    """Clear the guard's own ThinkPad pause once RESUME_AFTER_S has passed and RAM is fine."""
    import calendar
    import scheduler_commands
    note = scheduler_commands.paused_devices().get(DEVICE)
    if not note or note.get("who") != "thinkpad-guard" or free is None:
        return False
    try:
        at = calendar.timegm(time.strptime(note.get("at", ""), "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return False
    if (now or time.time()) - at < RESUME_AFTER_S:
        return False
    scheduler_commands.resume_device(DEVICE)
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "free_ram_gb": free,
           "resumed": f"its own pause of {note.get('at')}, after {RESUME_AFTER_S}s"}
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(f"thinkpad-guard: RESUMED the ThinkPad -- {row['resumed']}; {free:.2f} GB free", flush=True)
    return True


def main(argv):
    if argv[:1] == ["once"]:
        print(json.dumps(check(act=False), indent=1))
        return 0
    if argv[:1] == ["run"]:
        while True:
            try:
                check()
            except Exception as e:                                  # noqa: BLE001 -- said; a dropped check is retried
                print(f"thinkpad-guard: check failed ({type(e).__name__}: {e})", flush=True)
            time.sleep(EVERY_S)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
