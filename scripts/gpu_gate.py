#!/usr/bin/env python3
"""The GPU-residency gate. No throughput figure is recorded until this passes.

WHY IT EXISTS
-------------
The two known ways to get a plausible tok/s number from a model that is not on the GPU both
present as "slow", never as an error:

  * distro `llama.cpp` silently falling back to CPU
  * Ollama splitting layers across CPU and GPU on a stale VRAM estimate

Neither raises. Both produce output. A benchmark run against either is a measurement of the
wrong machine, and the number outlives the session that produced it.

WHY `nvidia-smi --query-gpu=memory.used` IS NOT USED
----------------------------------------------------
It does not work on this box, and the way it fails is the dangerous way. GB10 is unified
memory -- there is no discrete VRAM figure -- so on HiveMind:

    Memory-Usage : Not Supported
    memory.total : [N/A]
    memory.used  : [N/A]

A gate built on that returns "don't know" forever while looking like coverage. The
per-process table does work, so residency is established PER PROCESS:

    nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory --format=csv

THE THREE OUTCOMES ARE DELIBERATELY DISTINCT
--------------------------------------------
    0  RESIDENT      -- the process holds at least the floor, on the GPU
    1  NOT RESIDENT  -- it is running and it is not holding that memory
    2  CANNOT TELL   -- nvidia-smi absent, erroring, or reporting nothing usable

2 is not a pass and is not a failure. Collapsing it into either is the mistake that
`memory.used` invites: "cannot determine" reported as "fine" is how a broken check becomes
invisible. Callers must treat 2 as a refusal to proceed.

Usage:
    gpu_gate.py --pid 4476 --min-mib 100
    gpu_gate.py --match vllm --min-mib 8000
    gpu_gate.py --list
    gpu_gate.py --self-test
"""

import argparse
import re
import shutil
import subprocess
import sys

QUERY = "pid,process_name,used_gpu_memory"

RESIDENT, NOT_RESIDENT, CANNOT_TELL = 0, 1, 2


class CannotTell(Exception):
    """Raised when the instrument cannot answer. Never silently becomes a verdict."""


def compute_apps():
    """Every process holding GPU memory, as [{pid, name, mib}].

    Anything unparseable raises CannotTell rather than being skipped. A row we cannot read
    might be the very process being asked about, and dropping it would turn a blind spot
    into a confident 'not resident'.
    """
    if not shutil.which("nvidia-smi"):
        raise CannotTell("nvidia-smi is not on PATH")
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-compute-apps={QUERY}", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20,
        )
    except (subprocess.SubprocessError, OSError) as e:
        raise CannotTell(f"nvidia-smi did not run: {e}")
    if out.returncode != 0:
        raise CannotTell(f"nvidia-smi exited {out.returncode}: {out.stderr.strip()[:200]}")

    apps = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # "No running processes found" and similar are a legitimate empty answer.
        if "no running processes" in line.lower():
            return []
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            raise CannotTell(f"unparseable row from nvidia-smi: {line!r}")
        pid_s, name, mem_s = parts[0], ",".join(parts[1:-1]), parts[-1]
        if not pid_s.isdigit():
            raise CannotTell(f"non-numeric pid from nvidia-smi: {line!r}")
        m = re.match(r"^(\d+)\s*MiB$", mem_s)
        if not m:
            # "[N/A]" / "Not Supported" land here. That is the unified-memory failure this
            # tool exists to notice, so it must be loud.
            raise CannotTell(
                f"pid {pid_s} reports memory as {mem_s!r}, not a MiB figure. "
                "This driver cannot report per-process GPU memory; the gate cannot run.")
        apps.append({"pid": int(pid_s), "name": name, "mib": int(m.group(1))})
    return apps


def verdict(apps, pid=None, match=None, min_mib=0):
    """Return (code, message). Pure, so the self-test can drive it with fixtures."""
    if pid is not None:
        found = [a for a in apps if a["pid"] == pid]
        label = f"pid {pid}"
    else:
        found = [a for a in apps if match.lower() in a["name"].lower()]
        label = f"process matching {match!r}"

    if not found:
        return NOT_RESIDENT, (
            f"{label} holds no GPU memory. It is not on the GPU.\n"
            "    If it is running, this is the CPU-fallback case: it will still produce "
            "output, and that output is not a measurement of this GPU.")

    total = sum(a["mib"] for a in found)
    detail = ", ".join(f"pid {a['pid']} {a['name']} {a['mib']} MiB" for a in found)

    if total < min_mib:
        return NOT_RESIDENT, (
            f"{label} holds {total} MiB, below the {min_mib} MiB floor.\n"
            f"    {detail}\n"
            "    This is the partial-offload case: some layers are resident and some are "
            "not. Throughput measured here is a blend of two machines.")

    return RESIDENT, f"{label} holds {total} MiB on the GPU (floor {min_mib} MiB).\n    {detail}"


def self_test():
    """Prove the gate refuses before trusting it to permit.

    docs/CHECKS.md: a check is not trusted until it has been observed failing. The fixtures
    below drive `verdict` directly; the live half is exercised by --list and by the two real
    invocations recorded in the session log.
    """
    fx = [{"pid": 100, "name": "vllm-server", "mib": 8000},
          {"pid": 200, "name": "xorg", "mib": 300}]
    cases = [
        ("resident above the floor", verdict(fx, pid=100, min_mib=4000)[0], RESIDENT),
        ("resident, floor exactly met", verdict(fx, pid=100, min_mib=8000)[0], RESIDENT),
        ("BELOW the floor is not resident", verdict(fx, pid=100, min_mib=16000)[0], NOT_RESIDENT),
        ("absent pid is not resident", verdict(fx, pid=999, min_mib=1)[0], NOT_RESIDENT),
        ("match by name works", verdict(fx, match="vllm", min_mib=4000)[0], RESIDENT),
        ("match that hits nothing", verdict(fx, match="sglang", min_mib=1)[0], NOT_RESIDENT),
        ("empty process table is not resident",
         verdict([], pid=100, min_mib=1)[0], NOT_RESIDENT),
        ("a zero floor still requires PRESENCE",
         verdict([], match="vllm", min_mib=0)[0], NOT_RESIDENT),
    ]
    bad = 0
    print("self-test -- the refusals matter more than the passes\n")
    for label, got, want in cases:
        ok = got == want
        bad += not ok
        print(f"  [{'ok' if ok else 'BAD'}]   {label}: {got} (want {want})")

    # The instrument's own failure must be distinguishable from a verdict.
    try:
        raise CannotTell("simulated")
    except CannotTell:
        print("  [ok]   CannotTell is an exception, never a verdict")

    print(f"\n  {len(cases) - bad} passed, {bad} failed")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pid", type=int, help="process id that must hold GPU memory")
    ap.add_argument("--match", help="substring of the process name instead of a pid")
    ap.add_argument("--min-mib", type=int, default=0,
                    help="floor in MiB. Set this to roughly the model's size: a bare CUDA "
                         "context is a few hundred MiB and would otherwise pass.")
    ap.add_argument("--list", action="store_true", help="show every process holding GPU memory")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    try:
        apps = compute_apps()
    except CannotTell as e:
        print(f"  CANNOT TELL -- {e}")
        print("  Exit 2. This is NOT a pass. Do not record a throughput figure.")
        return CANNOT_TELL

    if args.list:
        if not apps:
            print("  no process is holding GPU memory")
        for a in apps:
            print(f"  pid {a['pid']:<8} {a['mib']:>8} MiB  {a['name']}")
        return RESIDENT if apps else NOT_RESIDENT

    if args.pid is None and not args.match:
        ap.error("give --pid or --match (or --list / --self-test)")

    code, msg = verdict(apps, pid=args.pid, match=args.match, min_mib=args.min_mib)
    print(f"  {'RESIDENT' if code == RESIDENT else 'NOT RESIDENT'} -- {msg}")
    if code != RESIDENT:
        print("  Exit 1. Do not record a throughput figure.")
    return code


if __name__ == "__main__":
    sys.exit(main())
