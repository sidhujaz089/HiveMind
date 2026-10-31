#!/usr/bin/env python3
"""The memory ledger (t456): what each model really costs on this box, measured, not estimated.

Jas, 2026-09-28: he wants to know whether a model like Qwen3.8-Flash-Next can run here at all, and
what real work costs in memory. Nothing recorded that: run-telemetry has tokens and tok/s but no
memory, and the one KV-per-slot figure was read off the journal by hand (t361's note, 2026-09-22).

    python3 scripts/memory_ledger.py sample        # one sample row (the dispatcher tick calls this)
    python3 scripts/memory_ledger.py loads         # parse model loads from the Ollama journal
    python3 scripts/memory_ledger.py report        # per model: weights, KV per slot, compute, peak RSS
    python3 scripts/memory_ledger.py fits <weights_gb> <kv_mib_per_slot> <slots>   # would it fit now?

SOURCES, and what each cannot see (said, not estimated):
  * /proc/meminfo                       -- GB10 is unified memory: nvidia-smi reports N/A, so system
                                           MemAvailable IS the GPU headroom here.
  * each llama-server process           -- its --model blob, -c (ctx), -np (slots), and RSS. RSS on
                                           unified memory is [Unverified.] as a measure of
                                           GPU-resident bytes; the journal's buffers below are what
                                           the runner itself allocated.
  * the Ollama journal at each load     -- "model buffer size", "llama_kv_cache: size = ... (cells,
                                           layers, seqs)", "compute buffer size": the allocation
                                           the runner reports, per device.
  * Ollama's manifests                  -- blob sha -> model tag.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SAMPLES = REPO / "workspace" / "memory-samples.jsonl"
LOADS = REPO / "workspace" / "run-logs" / "model-loads-memory.jsonl"
CURSOR = REPO / "workspace" / "run-logs" / ".memory-journal.cursor"
MANIFESTS = Path("/usr/share/ollama/.ollama/models/manifests")


def meminfo(path="/proc/meminfo"):
    """{"total_gb", "available_gb"} from /proc/meminfo."""
    kv = {}
    for line in Path(path).read_text().splitlines():
        k, v = line.split(":", 1)
        kv[k] = int(v.split()[0])                  # kB
    return {"total_gb": round(kv["MemTotal"] / 1048576, 2),
            "available_gb": round(kv["MemAvailable"] / 1048576, 2)}


def blob_tags(root=MANIFESTS):
    """{blob sha256 hex: tag} from Ollama's manifests (the model layer of each)."""
    out = {}
    if not root.is_dir():
        return out
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError) as e:
            print(f"  NOTE: manifest {f} unreadable ({e}); its tag is unknown", file=sys.stderr)
            continue
        parts = f.relative_to(root).parts            # registry/namespace/name/tag
        tag = f"{parts[-2]}:{parts[-1]}" if parts[-3] == "library" else "/".join(parts[1:-1]) + f":{parts[-1]}"
        for layer in d.get("layers", []):
            if layer.get("mediaType", "").endswith(".model"):
                out[layer["digest"].split(":", 1)[1]] = tag
    return out


def runners(tags=None):
    """[{pid, tag, rss_gb, ctx, slots}] for every llama-server process."""
    tags = blob_tags() if tags is None else tags
    out = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            argv = (p / "cmdline").read_bytes().split(b"\0")
            if not argv or not argv[0].endswith(b"llama-server"):
                continue
            args = [a.decode("utf-8", "replace") for a in argv]
            rss_kb = int(re.search(r"VmRSS:\s+(\d+)", (p / "status").read_text()).group(1))
        except (OSError, AttributeError, ValueError) as e:
            print(f"  NOTE: runner {p.name} not read ({type(e).__name__}); it ended while being read",
                  file=sys.stderr)
            continue
        def arg(name):
            return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None
        model = arg("--model") or ""
        sha = model.rsplit("sha256-", 1)[-1] if "sha256-" in model else ""
        out.append({"pid": int(p.name), "tag": tags.get(sha, f"blob {sha}"),
                    "rss_gb": round(rss_kb / 1048576, 2),
                    "ctx": int(arg("--ctx-size") or arg("-c") or 0) or None,
                    "slots": int(arg("--parallel") or arg("-np") or 0) or None})
    return sorted(out, key=lambda r: r["tag"])


def sample(write=True):
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **meminfo(), "runners": runners()}
    if write:
        with SAMPLES.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    return row


LOAD_START = re.compile(r'msg="starting llama-server" cmd=".*?--model \S*sha256-([0-9a-f]+)')
MIB = r"([\d.]+) MiB"


def parse_loads(lines, tags=None):
    """[{at, tag, weights_mib, kv_mib, kv_cells, kv_seqs, kv_per_slot_mib, compute_mib}] -- one per
    model load in journal `lines`. Buffers are summed across devices (CUDA0 + CUDA_Host)."""
    tags = blob_tags() if tags is None else tags
    out, cur, pred = [], None, None
    for line in lines:
        # OLLAMA'S OWN PLAN FOR THE LOAD, logged just before it starts: the total it predicted and
        # the context it planned. Some architectures (Gemma 4's sliding-window cache) never print a
        # full-context KV size, so this is the one footprint figure every load carries.
        p = re.search(r'predicted="([\d.]+) GiB" predicted_num_ctx=(\d+)', line)
        if p:
            pred = (float(p.group(1)), int(p.group(2)))
            continue
        m = LOAD_START.search(line)
        if m:
            if cur:
                out.append(cur)
            cur = {"at": line.split(" ", 1)[0], "tag": tags.get(m.group(1), f"blob {m.group(1)}"),
                   "weights_mib": 0.0, "kv_mib": None, "kv_cells": None, "kv_seqs": None, "compute_mib": 0.0,
                   "predicted_gb": pred[0] if pred else None, "predicted_ctx": pred[1] if pred else None}
            pred = None
            continue
        if not cur:
            continue
        if "model buffer size" in line and (m := re.search(MIB, line)):
            cur["weights_mib"] = round(cur["weights_mib"] + float(m.group(1)), 2)
        elif "llama_kv_cache: size" in line and (m := re.search(r"size =\s+" + MIB + r" \(\s*(\d+) cells,\s*(\d+) layers,\s*(\d+)/(\d+) seqs", line)):
            cur["kv_mib"], cur["kv_cells"], cur["kv_seqs"] = float(m.group(1)), int(m.group(2)), int(m.group(5))
            cur["kv_per_slot_mib"] = round(cur["kv_mib"] / max(1, cur["kv_seqs"]), 2)
        elif "compute buffer size" in line and (m := re.search(MIB, line)):
            cur["compute_mib"] = round(cur["compute_mib"] + float(m.group(1)), 2)
    if cur:
        out.append(cur)
    return [r for r in out if r["weights_mib"]]


def loads(since=None):
    """Parse the journal since the cursor (or `since`), append new load rows, move the cursor."""
    try:
        since = since or CURSOR.read_text().strip()
    except FileNotFoundError:
        since = "7 days ago"
    out = subprocess.run(["journalctl", "-u", "ollama", "--since", since, "--no-pager", "-o", "short-iso"],
                         capture_output=True, text=True, timeout=120).stdout
    rows = parse_loads(out.splitlines())
    LOADS.parent.mkdir(parents=True, exist_ok=True)
    with LOADS.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    CURSOR.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
    return rows


def _jsonl(p):
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError as e:
            print(f"  NOTE: unreadable row in {p.name} ({e}); skipped", file=sys.stderr)
    return out


def report():
    """Per model: latest load's weights / KV per slot / compute, and peak RSS seen in samples."""
    # THE LOAD AT THE LARGEST CONTEXT, not the latest: tests and memory-fitting passes load a model
    # at 2-3k cells, and "latest" then reported a 25 MiB KV for a model that runs at 131072.
    latest = {}
    for r in _jsonl(LOADS):
        key = (r.get("predicted_ctx") or 0, r.get("kv_cells") or 0)
        if r["tag"] not in latest or key >= ((latest[r["tag"]].get("predicted_ctx") or 0),
                                             (latest[r["tag"]].get("kv_cells") or 0)):
            latest[r["tag"]] = r
    peak = {}
    samples = _jsonl(SAMPLES)
    for s in samples:
        for rn in s.get("runners", []):
            peak[rn["tag"]] = max(peak.get(rn["tag"], 0), rn["rss_gb"])
    mem = meminfo()
    lines = [f"box: {mem['total_gb']} GB total, {mem['available_gb']} GB available now "
             f"({len(samples)} samples, {len(latest)} models with a recorded load)", "",
             f"{'model':32} {'weights GB':>10} {'KV/slot GB':>10} {'slots':>5} {'compute GB':>10} "
             f"{'Ollama plan GB @ctx':>20} {'peak RSS GB':>11}"]
    for tag in sorted(set(latest) | set(peak)):
        r = latest.get(tag, {})
        g = lambda mib: f"{mib / 1024:.2f}" if mib else "?"
        lines.append(f"{tag[:32]:32} {g(r.get('weights_mib')):>10} {g(r.get('kv_per_slot_mib')):>10} "
                     f"{str(r.get('kv_seqs') or '?'):>5} {g(r.get('compute_mib')):>10} "
                     f"{(str(r.get('predicted_gb')) + ' @' + str(r.get('predicted_ctx'))) if r.get('predicted_gb') else '?':>20} "
                     f"{peak.get(tag, '?')!s:>11}")
    lines += ["", "KV/slot is the journal's own KV line, from the load at the largest context; Gemma 4 prints only",
              "its sliding-window cache there, so use Ollama's plan for its total. RSS on unified memory is",
              "[Unverified.] as GPU-resident bytes."]
    return "\n".join(lines)


def fits(weights_gb, kv_mib_per_slot, slots, available_gb=None, reserve_gb=12.0):
    """(fits, need_gb, available_gb): weights + KV for every slot + the dispatcher's reserve."""
    available_gb = meminfo()["available_gb"] if available_gb is None else available_gb
    need = weights_gb + kv_mib_per_slot * slots / 1024 + reserve_gb
    return need <= available_gb, round(need, 1), available_gb


def main(argv):
    cmd = argv[0] if argv else "report"
    if cmd == "sample":
        print(json.dumps(sample()))
    elif cmd == "loads":
        rows = loads(argv[1] if len(argv) > 1 else None)
        print(f"{len(rows)} model load(s) recorded")
    elif cmd == "fits" and len(argv) == 4:
        ok, need, avail = fits(float(argv[1]), float(argv[2]), int(argv[3]))
        print(f"{'FITS' if ok else 'DOES NOT FIT'}: needs ~{need} GB (incl. 12 GB reserve), {avail} GB available now")
    else:
        print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
