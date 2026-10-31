#!/usr/bin/env python3
"""t1226: every computer statistic on the Lanes tab comes from the hardware, never from Ollama.

Evidence 2026-10-15: Ollama's /api/ps on the ThinkPad reported gemma4:26b size_vram 1.05 GB while
nvidia-smi showed 20,871 MiB used on the 5090 eGPU, and the tab showed "<2 GB". The tab was reading
Ollama's per-model accounting as the device's memory. The device's memory is what nvidia-smi knows.

RED ON MAIN for the missing change and nothing else: scripts/lane_status.py has no hardware_vram(),
and remote_devices() sums Ollama's size_vram into vram_used_gb (lane_status.py:1255, 1260).

Hermetic: no live server, no model, no subprocess. urllib.request.urlopen is replaced before any
call; dispatch_loop.rows / ledger_running are stubbed so the scheduler ledger is not walked. nvidia-
smi is never executed -- every case injects a `run=` stub, and remote_devices() with run=None must
NOT run nvidia-smi for a REMOTE device (it cannot reach one; that is the honest "no transport" path).

Run: python3 scripts/tests/test_lanes_hardware_stats.py  (no pytest; exits non-zero on a failure)
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import lane_status  # noqa: E402

# The real figures from the evidence, in the units each source reports: nvidia-smi gives MiB,
# Ollama's size_vram is bytes. 1.05 GB is the number the tab showed.
SMI_5090 = "NVIDIA GeForce RTX 5090, 20871 MiB, 24564 MiB\n"
SMI_OTHER = "NVIDIA GeForce RTX 5090, 8219 MiB, 24564 MiB\n"
OLLAMA_SIZE_VRAM = 1_127_428_000      # bytes: 1.05 GB decimal -- the number the tab showed
GB = 1e9
fails = []


def ck(label, cond):
    if not cond:
        fails.append(label)


def _query(cmd):
    """The --query-gpu=... field list, so the test does not pin field ORDER, only the fields."""
    for tok in cmd:
        if tok.startswith("--query-gpu="):
            return tok.split("=", 1)[1]
    return ""


class Resp:
    """A urllib response good enough for `with urllib.request.urlopen(u) as f: json.load(f)`."""

    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def smi_stub(asked, out=SMI_5090):
    """A run= stub that REFUSES traffic it was not written to answer.

    No `timeout=` assertion here: _run() bakes timeout=15 in (lane_status.py:59) and takes
    positional args only, so a correct implementation passing no timeout kwarg would go red for
    a reason this brief does not change.
    """
    def run(*a, **kw):
        cmd = [str(x) for x in a]
        if "nvidia-smi" not in cmd:
            raise AssertionError(f"asked the hardware reader to run something that is not nvidia-smi: {cmd}")
        q = _query(cmd)
        if "memory.used" not in q or "memory.total" not in q:
            raise AssertionError(f'nvidia-smi was not asked for memory.used and memory.total: got "{q}"')
        asked.append(cmd)
        return out
    return run


def main():
    # --- 1. hardware_vram parses nvidia-smi's REAL csv shape -------------------------------
    if not hasattr(lane_status, "hardware_vram"):
        ck("lane_status.hardware_vram() exists (the hardware reader the tab must use)", False)
        return _report()

    asked = []
    got = lane_status.hardware_vram("thinkpad", run=smi_stub(asked))
    ck("hardware_vram: used in MiB is nvidia-smi's number", got.get("used_mib") == 20871)
    ck("hardware_vram: total in MiB is nvidia-smi's number", got.get("total_mib") == 24564)
    ck("hardware_vram: it says which hardware source answered", got.get("source") == "nvidia-smi")
    ck("hardware_vram: nvidia-smi was invoked exactly once per call", len(asked) == 1)

    # A stub returning a DIFFERENT number must change the answer -- kills a hard-coded return.
    asked2 = []
    again = lane_status.hardware_vram("thinkpad", run=smi_stub(asked2, SMI_OTHER))
    ck("hardware_vram: the answer is READ from nvidia-smi, not fixed", again.get("used_mib") == 8219)

    # --- 2. a device with no discrete VRAM says so; it never reads as 0 ---------------------
    na = lane_status.hardware_vram("gx10", run=lambda *a, **k: "NVIDIA GB10, [N/A], [N/A]\n")
    ck('hardware_vram: "[N/A]" is not reported as 0', na.get("used_mib") is None)
    ck("hardware_vram: an unanswerable device RECORDS why (no silent None)",
       bool(str(na.get("reason") or "").strip()))

    empty = lane_status.hardware_vram("thinkpad", run=lambda *a, **k: "")
    ck("hardware_vram: nvidia-smi failing gives None, not 0", empty.get("used_mib") is None)
    ck("hardware_vram: nvidia-smi failing says why",
       bool(str(empty.get("reason") or "").strip()))

    # --- 3. remote_devices() reports the device's hardware number, not Ollama's -------------
    srv = json.loads((ROOT / "projects" / "local-inference" / "models" / "SERVERS.json")
                     .read_text(encoding="utf-8"))
    devs = [d for d in (srv.get("devices") or {}) if d != "gx10"]
    if not devs:
        ck("SERVERS.json lists at least one non-gx10 device to exercise remote_devices()", False)
        return _report()
    dev = devs[0]

    def fake_urlopen(url, timeout=None, **kw):
        assert str(url).endswith("/api/ps"), f"unexpected URL fetched: {url}"
        assert timeout, "urlopen called without a timeout"
        return Resp({"models": [{"name": "gemma4:26b", "size": 17_700_000_000,
                                 "size_vram": OLLAMA_SIZE_VRAM, "context_length": 8192,
                                 "expires_at": "2026-10-15T23:59:59Z"}]})

    urllib.request.urlopen = fake_urlopen
    import dispatch_loop
    dispatch_loop.rows = lambda *a, **k: []
    dispatch_loop.ledger_running = lambda h, *a, **k: []
    now = lane_status._ts("2026-10-15T22:29:18Z")
    expected = round(20871 / 1024, 2)           # MiB -> GiB, comparable with SERVERS.json memory_gb

    rows = {d["device"]: d for d in lane_status.remote_devices(now=now, timeout=1,
                                                              run=smi_stub([]))}
    ck(f"the {dev} row is present with its usual keys",
       dev in rows and all(k in rows[dev] for k in
                           ("device", "what", "memory_gb", "servers", "loaded", "running",
                            "done", "reachable")))
    v = rows[dev].get("vram_used_gb")
    ck(f"{dev} vram_used_gb is nvidia-smi's 20871 MiB ({expected} GiB), got {v!r}",
       isinstance(v, (int, float)) and abs(v - expected) < 0.01)
    ck("the Ollama figure (1.13 GB) is NOT what the tab shows",
       not (isinstance(v, (int, float)) and abs(v - 1.13) < 0.01))
    ck("the row says the number came from hardware",
       rows[dev].get("vram_source") == "nvidia-smi")
    # The per-model table keeps rendering (device_tiles.py:139-140 reads m["vram_gb"]).
    ck("per-model loaded rows keep the keys device_tiles.py reads",
       bool(rows[dev]["loaded"]) and all(
           k in rows[dev]["loaded"][0] for k in ("server", "name", "vram_gb", "context", "until")))

    # No transport decided yet for a remote box => unknown + a stated reason, never Ollama's number.
    rows2 = {d["device"]: d for d in lane_status.remote_devices(now=now, timeout=1)}
    v2 = rows2[dev].get("vram_used_gb")
    ck(f"{dev} vram_used_gb with no transport is None, not the Ollama number", v2 is None)
    ck("no hardware reading says why, on the page (not a blank)",
       bool(str(rows2[dev].get("vram_note") or "").strip()))

    # --- 4. the shape of the fix: the device total is not a sum of Ollama's per-model numbers
    src = (ROOT / "scripts" / "lane_status.py").read_text(encoding="utf-8")
    s = src.find("def remote_devices(")
    e = src.find("\n\ndef ", s + 1)
    body = src[s:e] if (s >= 0 and e > s) else ""
    ck("remote_devices() is locatable in scripts/lane_status.py", s >= 0 and e > s)
    ck("vram_used_gb is no longer the sum of the per-model Ollama figures",
       s >= 0 and e > s and not any(
           line.strip().startswith('d["vram_used_gb"]') and "sum(" in line
           for line in body.splitlines()))

    return _report()


def _report():
    for f in fails:
        print("FAIL:", f)
    if not fails:
        print("ok: the Lanes tab's VRAM figure comes from nvidia-smi, and an unreadable device says so")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
