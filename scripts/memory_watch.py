#!/usr/bin/env python3
"""What one lane actually uses, every 20 seconds -- observed before any floor is set.

Jas, 2026-10-08: "Now that we have 1 lane, I want to observe what it uses. Track the embedder and
anything else, including your tests. When we know what 1 lane needs, then we can consider a floor.
I don't want to limit Swift."

    python3 scripts/memory_watch.py sample          # one row to stdout (nothing written)
    python3 scripts/memory_watch.py run             # every 20 s, append to workspace/memory-watch.jsonl
    python3 scripts/memory_watch.py report [hours]  # per group: now, median, peak; lowest free memory

EACH PROCESS IS CHARGED TO THE NEAREST ANCESTOR THAT NAMES A GROUP, so a build's sandboxed test
suites count against that build, and the tests a Claude session runs count against Claude Code.
A process's cost is its resident size PLUS what the GPU driver allocated to it: on the GB10 the GPU
takes from the same pool and that allocation is not in VmRSS (Swift: ~3 GiB resident, ~92 GiB GPU).

WHY 20 s AND NOT THE DISPATCHER TICK. memory-samples.jsonl is written once a tick (10 min) and saw
available memory between 8 and 20 GiB all day while the desktop died twice; whatever spikes lasts
less than a tick. The GPU driver's "out of memory" at 08:54 is in the kernel journal, not in any
sample. The kernel's own swap and pressure figures are recorded too.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "workspace" / "memory-watch.jsonl"
EVERY_S = 20

# (group, test on (comm, cmdline)) -- first match on the process or its nearest ancestor wins.
JOB_SCRIPTS = {"coder.py": "job:build", "preflight_brief.py": "job:preflight",
               "truncation_review.py": "job:script-review", "supervise.py": "job:supervision",
               "session_review.py": "job:session-review", "assigned_worker.py": "job:assigned"}


# THE REPO'S OWN coder.py, NOT ANY FILE CALLED coder.py (2026-10-30). The build group matched `/coder.py`, and the
# coder SUITES run coder.py as a fixture -- so build_procs counted test clones (traced: run-tests-test_coder_.../
# coder-1793379) and my own gate runs fed it. Measured: hundreds of pids there joined to no dispatch row. The reader
# dropped them, so no wrong peak was produced, but a field that overstates what is building cannot be quoted.
BUILD_SCRIPT = str(REPO / "projects" / "local-inference" / "coder.py")


def _is_build_proc(cmd):
    """True when this cmdline is a DISPATCHED build: the repo's own coder.py, not a copy of it.

    A test suite's clone is a copy; it has no dispatch row, spends the suite's memory and not a build's, and would
    otherwise be counted as production work.
    """
    cmd = str(cmd or "")
    if "run-tests" in cmd:
        return False
    return BUILD_SCRIPT in cmd


def _group(comm, cmd):
    if comm.startswith("VLLM::") or "vllm serve" in cmd:
        return "swift"
    if "llama-server" in cmd or "ollama runner" in cmd:
        return "ollama-runner"
    if cmd.strip().endswith("ollama serve"):
        return "ollama-server"
    for script, g in JOB_SCRIPTS.items():
        if f"/{script}" in cmd:
            return g
    if "/ask_q16.py" in cmd:
        p = re.search(r"--purpose (\S+)", cmd)
        purpose = (p.group(1) if p else "").lower()
        return ("job:scoping" if purpose.startswith("manager") else
                "job:rca" if purpose.startswith("rca") else
                "job:session-review" if purpose.startswith("session") else "job:ask")
    if "/console.py" in cmd:
        return "job:console" if "--answer" in cmd else "services"
    if "/claude-code/" in cmd or comm == "claude":
        return "claude-code"
    if "claude-desktop" in cmd:
        return "claude-app"
    if any(s in cmd for s in ("feedback.py", "workspace_mcp", "dispatch_loop.py", "scoping_queue.sh",
                              "agent_shell", "model_chat", "dsh", "cloudflared", "docker")):
        return "services"
    if comm.startswith(("gnome", "gdm", "Xwayland", "firefox", "pipewire", "wireplumber",
                        "evolution", "gvfs", "ibus", "xdg-", "tracker", "localsearch")) or "firefox" in cmd:
        return "desktop"
    return None


def _gpu_mib():
    try:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=20).stdout
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  NOTE: nvidia-smi failed ({type(e).__name__})", file=sys.stderr)
        return {}
    got = {}
    for line in out.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) == 2 and parts[0].isdigit() and parts[1].replace(".", "").isdigit():
            got[int(parts[0])] = got.get(int(parts[0]), 0) + float(parts[1])
    return got


def _procs():
    """{pid: (ppid, comm, cmdline, rss_kib)} for every readable process."""
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            stat = Path(f"/proc/{d}/stat").read_text()
            comm = stat[stat.index("(") + 1:stat.rindex(")")]
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            cmd = Path(f"/proc/{d}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            m = re.search(r"^VmRSS:\s+(\d+)", Path(f"/proc/{d}/status").read_text(), re.M)
            out[int(d)] = (ppid, comm, cmd, int(m.group(1)) if m else 0)
        except (OSError, ValueError, IndexError):
            out.setdefault(int(d), None)   # exited mid-read; dropped below
    return {k: v for k, v in out.items() if v is not None}


def _meminfo():
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, v = line.split(":", 1)
        info[k] = int(v.split()[0]) / 1048576
    psi = Path("/proc/pressure/memory")
    some = None
    if psi.exists():
        m = re.search(r"some avg10=([\d.]+)", psi.read_text())
        some = float(m.group(1)) if m else None
    return {"total_gb": round(info["MemTotal"], 2), "available_gb": round(info["MemAvailable"], 2),
            "swap_used_gb": round(info["SwapTotal"] - info["SwapFree"], 2), "pressure_some_avg10": some}


def sample():
    procs, gpu = _procs(), _gpu_mib()
    memo = {}

    def group_of(pid, depth=0):
        if pid in memo:
            return memo[pid]
        p = procs.get(pid)
        if p is None or depth > 40:
            return "other"
        g = _group(p[1], p[2]) or (group_of(p[0], depth + 1) if p[0] > 1 else "other")
        memo[pid] = g
        return g

    groups = {}
    for pid, (_, comm, cmd, rss) in procs.items():
        g = group_of(pid)
        e = groups.setdefault(g, {"rss_gb": 0.0, "gpu_gb": 0.0, "procs": 0})
        e["rss_gb"] += rss / 1048576
        e["gpu_gb"] += gpu.get(pid, 0) / 1024
        e["procs"] += 1
    for e in groups.values():
        e["rss_gb"], e["gpu_gb"] = round(e["rss_gb"], 2), round(e["gpu_gb"], 2)
        e["total_gb"] = round(e["rss_gb"] + e["gpu_gb"], 2)
    # WHICH PROCESS BELONGS TO WHICH BUILD (A5, 2026-10-30). The `groups` map sums every build's processes under
    # one key, so the rows it produced could only ever answer "how much were builds using at this instant" -- and
    # `build_peaks()` read them as though they were per-build peaks. Measured: 56,826 such samples across 1,051
    # production builds, each sample the SUM of the concurrent ones. Recording the pids lets a reader join them to
    # the dispatch ledger (started rows carry pid + chunk) and take one maximum per build.
    build_procs = {str(pid): round(v[3] / 1048576 + gpu.get(pid, 0) / 1024, 2)
                   for pid, v in procs.items()
                   if group_of(pid) == "job:build" and _is_build_proc(v[2])}
    top = sorted(procs.items(), key=lambda kv: kv[1][3] + gpu.get(kv[0], 0) * 1024, reverse=True)[:6]
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **_meminfo(),
            "groups": dict(sorted(groups.items(), key=lambda kv: -kv[1]["total_gb"])),
            "build_procs": build_procs,
            "top": [{"pid": pid, "group": group_of(pid), "cmd": v[2].strip(),
                     "gb": round(v[3] / 1048576 + gpu.get(pid, 0) / 1024, 2)} for pid, v in top]}


def run():
    while True:
        t0 = time.time()
        try:
            row = sample()
            with OUT.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as e:                                      # noqa: BLE001 -- a sampler must not die
            print(f"  NOTE: sample failed ({type(e).__name__}: {e})", file=sys.stderr, flush=True)
        time.sleep(max(1, EVERY_S - (time.time() - t0)))


def rows(hours=24.0, path=None):
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600))
    out, torn = [], 0
    p = Path(path or OUT)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            torn += 1                   # a row cut by a crash mid-write: counted, said below
            continue
        if r.get("at", "") >= since:
            out.append(r)
    if torn:
        print(f"  NOTE: {torn} unreadable row(s) in {p.name}", file=sys.stderr)
    return out


def summary(hours=24.0, path=None):
    """{"samples", "lowest_available": row-ish, "groups": {g: {now, median, peak}}}"""
    rs = rows(hours, path)
    if not rs:
        return {"samples": 0, "groups": {}}
    names = sorted({g for r in rs for g in r.get("groups", {})})
    groups = {}
    for g in names:
        vals = sorted(r["groups"].get(g, {}).get("total_gb", 0.0) for r in rs)
        groups[g] = {"now": rs[-1]["groups"].get(g, {}).get("total_gb", 0.0),
                     "median": vals[len(vals) // 2], "peak": vals[-1]}
    low = min(rs, key=lambda r: r.get("available_gb", 1e9))
    return {"samples": len(rs), "since": rs[0]["at"], "now_available": rs[-1].get("available_gb"),
            "lowest_available": {"at": low["at"], "gb": low.get("available_gb"),
                                 "swap_used_gb": low.get("swap_used_gb"), "top": low.get("top", [])[:4]},
            "groups": dict(sorted(groups.items(), key=lambda kv: -kv[1]["peak"]))}


def main(argv):
    cmd = argv[0] if argv else "report"
    if cmd == "sample":
        print(json.dumps(sample(), indent=1))
    elif cmd == "run":
        run()
    elif cmd == "report":
        s = summary(float(argv[1]) if len(argv) > 1 else 24.0)
        if not s["samples"]:
            print("no samples yet")
            return 0
        print(f"{s['samples']} samples since {s['since']} -- available now {s['now_available']} GiB")
        lo = s["lowest_available"]
        print(f"lowest available: {lo['gb']} GiB at {lo['at']} (swap {lo['swap_used_gb']} GiB); biggest then:")
        for t in lo["top"]:
            print(f"    {t['gb']:7.2f} GiB  {t['group']:<20} {t['cmd'][:70]}")
        print(f"\n  {'group':<22}{'now':>8}{'median':>8}{'peak':>8}  (GiB, resident + GPU)")
        for g, v in s["groups"].items():
            print(f"  {g:<22}{v['now']:>8.2f}{v['median']:>8.2f}{v['peak']:>8.2f}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
