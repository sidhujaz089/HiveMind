#!/usr/bin/env python3
"""The Ollama load tracer names the process behind a connection, from a FAKE /proc.

Never the live box: a test that read live memory vetoed every production dispatch on
2026-09-22 (4ccf683). Everything here runs against a directory laid out like /proc.

  * parse_tcp keeps ESTABLISHED sockets to or from 11434 and nothing else
  * addr decodes IPv4 and IPv4-mapped IPv6 addresses
  * callers maps a client socket to its pid, command line and parent; skips Ollama's own
    loopback side (already listed from the client end); records a remote caller by address
  * events reports load, unload and a context change -- the three things that move 54 GB
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ollama_callers as oc  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


HDR = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
# 127.0.0.1:43762 -> 127.0.0.1:11434 ESTABLISHED, inode 5001 (a local caller)
CLIENT = "   0: 0100007F:AAF2 0100007F:2CAA 01 00000000:00000000 00:00000000 00000000  1000        0 5001\n"
# Ollama's side of the same connection (skipped: the client end names the pid)
SERVER_LO = "   1: 0100007F:2CAA 0100007F:AAF2 01 00000000:00000000 00:00000000 00000000   998        0 5002\n"
# 172.17.0.2 (docker) -> Ollama, a caller with no local pid
SERVER_RE = "   2: 0100007F:2CAA 020011AC:9C40 01 00000000:00000000 00:00000000 00000000   998        0 5003\n"
# a LISTEN socket and an unrelated port: both ignored
NOISE = ("   3: 00000000:2CAA 00000000:0000 0A 00000000:00000000 00:00000000 00000000   998        0 5004\n"
         "   4: 0100007F:1F90 0100007F:D431 01 00000000:00000000 00:00000000 00000000  1000        0 5005\n")


def fake_proc(tmp):
    proc = Path(tmp)
    (proc / "net").mkdir()
    (proc / "net" / "tcp").write_text(HDR + CLIENT + SERVER_LO + SERVER_RE + NOISE)
    for pid, cmd, ppid in ((4242, b"python3\0scripts/corpus_scripts.py\0", 4241),
                           (4241, b"flock\0-n\0/tmp/x.lock\0python3\0scripts/corpus_scripts.py\0", 1)):
        d = proc / str(pid)
        (d / "fd").mkdir(parents=True)
        (d / "cmdline").write_bytes(cmd)
        (d / "stat").write_text(f"{pid} (python3) S {ppid} 0 0\n")
    os.symlink("socket:[5001]", proc / "4242" / "fd" / "3")
    os.symlink("/dev/null", proc / "4242" / "fd" / "0")
    return proc


def main():
    print("parse_tcp and addr:")
    rows = oc.parse_tcp(HDR + CLIENT + SERVER_LO + SERVER_RE + NOISE)
    report("keeps the three ESTABLISHED sockets on 11434, drops LISTEN and other ports",
           [r[0] for r in rows], ["5001", "5002", "5003"])
    report("roles", [r[3] for r in rows], ["client", "server", "server"])
    report("IPv4", oc.addr("0100007F:2CAA"), "127.0.0.1:11434")
    report("IPv4 mapped into tcp6", oc.addr("0000000000000000FFFF00000100007F:2CAA"),
           "127.0.0.1:11434")

    print("\ncallers() from a fake /proc:")
    with tempfile.TemporaryDirectory() as tmp:
        got = oc.callers(fake_proc(tmp))
    local = [c for c in got if c.get("pid")]
    report("the local caller is named by pid", [c["pid"] for c in local], [4242])
    report("  with its command line", local[0]["cmd"] if local else None,
           "python3 scripts/corpus_scripts.py")
    report("  and its parent", (local[0]["parent"] or "").startswith("flock -n") if local else None, True)
    report("Ollama's loopback side is not double-counted",
           sum(1 for c in got if c.get("remote", "").startswith("127.0.0.1")), 0)
    report("a remote caller is recorded by address",
           [c["remote"] for c in got if "remote" in c and c.get("pid") is None and "cmd" not in c],
           ["172.17.0.2:40000"])

    print("\nevents() between /api/ps snapshots:")
    report("load", oc.events({}, {"q4": 65536}), [("load", "q4", None, 65536)])
    report("unload", oc.events({"bf16": 65536}, {}), [("unload", "bf16", 65536, None)])
    report("a context change is a reload", oc.events({"bf16": 65536}, {"bf16": 262144}),
           [("reload-ctx", "bf16", 65536, 262144)])
    report("no change, no event", oc.events({"g": 65536}, {"g": 65536}), [])

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
