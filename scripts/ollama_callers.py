#!/usr/bin/env python3
"""Who made Ollama load that model? One row per load, unload and context change, WITH the callers.

    python3 scripts/ollama_callers.py watch          # the service: poll once a second, forever
    python3 scripts/ollama_callers.py report [N]     # the last N load events, readable

WHY THIS EXISTS. Ollama does not record who sent a request. Its journal says a model loaded and
nothing about the process behind it, and every local caller is 127.0.0.1. On 2026-09-23 the
bf16 (54 GB) was reloaded ten seconds after Claude unloaded it for a benchmark -- with the
scheduler paused and the warmer disabled -- and finding the two callers by hand took an hour:
the hourly corpus job, and a model-driven eval running inside a merge gate's test sandbox.
Neither goes through the scheduler, so no ledger the repository keeps could have named them.

HOW. Once a second: /api/ps (what is loaded, at what context), and /proc/net/tcp{,6} (every
open connection to port 11434). A connection's socket inode maps to the process holding it
through /proc/<pid>/fd, which for this user's processes needs no privilege. A load of a large
model takes tens of seconds, so the request that caused it is open for many polls: a
one-second poll cannot miss it. When the loaded set changes, the row names every caller seen
in the last LOOKBACK_S seconds -- the load's cause is among them. A caller outside this box
(Open WebUI in docker, the phone over the tailnet) has no local pid; its address is recorded.

WHAT IT MUST NOT DO: sit in the inference path. It only reads. If it dies, inference carries
on and the service restarts it; a proxy in front of Ollama would have made every request depend
on this file.

Rows: workspace/run-logs/ollama-loads.jsonl
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "workspace" / "run-logs" / "ollama-loads.jsonl"
OLLAMA = "http://127.0.0.1:11434"
PORT = 11434
POLL_S = 1.0
LOOKBACK_S = 30          # a caller seen this recently is a candidate for the load
ESTABLISHED = "01"


def parse_tcp(text, port=PORT):
    """[(inode, local, remote, role)] for ESTABLISHED sockets touching `port`.

    role is "client" when this socket's REMOTE end is the port (a process calling Ollama), and
    "server" when its LOCAL end is (Ollama's side -- the peer address is the caller, which is
    how a remote caller is seen at all)."""
    out = []
    for line in text.splitlines()[1:]:
        f = line.split()
        if len(f) < 10 or f[3] != ESTABLISHED:
            continue
        lport, rport = int(f[1].split(":")[1], 16), int(f[2].split(":")[1], 16)
        if rport == port:
            out.append((f[9], f[1], f[2], "client"))
        elif lport == port:
            out.append((f[9], f[1], f[2], "server"))
    return out


def addr(hexaddr):
    """'0100007F:2CAA' -> '127.0.0.1:11434' (IPv4; IPv6 is returned as-is, which is readable
    enough for the rare remote caller and not worth a decoder that could be wrong)."""
    host, port = hexaddr.split(":")
    if len(host) == 32 and host[:24] == "0000000000000000FFFF0000":
        host = host[24:]                 # an IPv4 address mapped into tcp6
    if len(host) == 8:
        host = ".".join(str(int(host[i:i + 2], 16)) for i in (6, 4, 2, 0))
    return f"{host}:{int(port, 16)}"


def socket_owners(proc=Path("/proc")):
    """({socket inode: pid}, unreadable) for every process whose fds this user can read.

    `unreadable` counts the processes skipped -- other users' (root's ollama, docker) or ones
    that exited mid-scan. Expected and harmless, but COUNTED rather than silently dropped: a
    caller hidden among them is recorded by address from Ollama's side instead."""
    owners, unreadable = {}, 0
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            for fd in (d / "fd").iterdir():
                target = os.readlink(fd)
                if target.startswith("socket:["):
                    owners[target[8:-1]] = int(d.name)
        except (PermissionError, FileNotFoundError, ProcessLookupError, NotADirectoryError):
            unreadable += 1
    return owners, unreadable


def cmdline(pid, proc=Path("/proc")):
    try:
        raw = (proc / str(pid) / "cmdline").read_bytes()
        return raw.replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError as e:
        return f"(exited: {type(e).__name__})"


def parent(pid, proc=Path("/proc")):
    try:
        return int((proc / str(pid) / "stat").read_text().split(") ", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return None


def callers(proc=Path("/proc")):
    """Every open connection to Ollama, with the process behind it where it is ours."""
    rows = []
    for name in ("tcp", "tcp6"):
        f = proc / "net" / name
        if f.is_file():                  # tcp6 is absent on a kernel without IPv6
            rows += parse_tcp(f.read_text())
    if not rows:
        return []
    owners, _unreadable = socket_owners(proc)
    out = []
    for inode, local, remote, role in rows:
        if role == "server":
            peer = addr(remote)
            if peer.startswith("127.0.0.1:"):
                continue                 # its client side is listed with a pid already
            out.append({"remote": peer})
            continue
        pid = owners.get(inode)
        if pid is None:
            out.append({"remote": addr(local), "pid": None})
            continue
        pp = parent(pid, proc)
        out.append({"pid": pid, "cmd": cmdline(pid, proc),
                    "parent": cmdline(pp, proc) if pp else None})
    return out


def loaded(timeout=3):
    """{model name: context length} from /api/ps. Raises on an unreachable server."""
    with urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=timeout) as f:
        d = json.load(f)
    return {m["name"]: m.get("context_length") for m in d.get("models") or []}


def events(before, after):
    """[(event, model, ctx_before, ctx_after)] between two /api/ps snapshots."""
    out = []
    for m in sorted(set(before) | set(after)):
        if m not in before:
            out.append(("load", m, None, after[m]))
        elif m not in after:
            out.append(("unload", m, before[m], None))
        elif before[m] != after[m]:
            out.append(("reload-ctx", m, before[m], after[m]))
    return out


def append(row):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def watch():
    recent = {}                         # key -> (last seen, caller dict)
    prev = None
    down = False
    while True:
        now = time.time()
        for c in callers():
            recent[(c.get("pid"), c.get("remote"), c.get("cmd"))] = (now, c)
        recent = {k: v for k, v in recent.items() if now - v[0] <= LOOKBACK_S}
        try:
            cur = loaded()
            if down:
                append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": "server-back"})
                down = False
        except (urllib.error.URLError, OSError, ValueError) as e:
            if not down:
                append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": "server-unreachable", "error": f"{type(e).__name__}: {e}"})
                down = True
            time.sleep(POLL_S)
            continue
        if prev is not None:
            for ev, model, c0, c1 in events(prev, cur):
                append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "event": ev, "model": model, "ctx_before": c0, "ctx_after": c1,
                        "callers_last_%ds" % LOOKBACK_S: [v[1] for v in recent.values()]})
        prev = cur
        time.sleep(POLL_S)


def report(n=20):
    if not OUT.is_file():
        print(f"  no load events yet ({OUT.relative_to(REPO)} does not exist)")
        return 0
    rows = OUT.read_text(encoding="utf-8").splitlines()
    for line in rows[-n:]:
        try:
            r = json.loads(line)
        except ValueError as e:
            print(f"  (unreadable row: {e})")
            continue
        print(f"{r['at']}  {r['event']:<18} {r.get('model', '')}  "
              f"ctx {r.get('ctx_before')} -> {r.get('ctx_after')}")
        for c in r.get("callers_last_%ds" % LOOKBACK_S, []):
            who = c.get("cmd") or c.get("remote")
            print(f"      pid {c.get('pid')}: {who}"
                  + (f"\n        parent: {c['parent']}" if c.get("parent") else ""))
    return 0


def main(argv):
    if argv[:1] == ["watch"]:
        return watch()
    if argv[:1] == ["report"]:
        return report(int(argv[1]) if len(argv) > 1 else 20)
    print(__doc__.split("\n\n")[0])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
