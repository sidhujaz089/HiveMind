#!/usr/bin/env python3
"""t1317: every device-pause acquire and clear is recorded in an append-only ledger.

RCA 176c87a6bd9a, the unresolved finding: "the guard tripped at 00:27:30Z, and the pause was
gone by 00:38:41Z". Unanswerable by construction: a device pause is a bare flag file
(scripts/scheduler_commands.py:152-153) and every clearer except the guard's own unlinks it
(scripts/scheduler_commands.py:163-164) with no log row, no print and no journal line.

Pinned at the PRIMITIVE -- pause_device() / resume_device() -- so every existing and future
caller inherits the record: Hub Resume (breaker_on, :197-202), gx10_on (:187-194), boot
(:205-221), the GX10 memory guard (gx10_off, :179-184), the ThinkPad guard
(scripts/thinkpad_guard.py:161). A brief cannot pin the callers: they are outside this file.

Runs hermetically: sc.PAUSE_FLAG and sc.DEVICE_PAUSE_LEDGER are redirected into a temp dir,
the same move scripts/tests/test_scheduler_commands.py:51 makes for PAUSE_FLAG. No model, no
server, no subprocess, no live state, no process left running.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import scheduler_commands as sc  # noqa: E402

PASSED = FAILED = 0
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
KEYS = ("at", "device", "action", "who", "why", "source_pid", "source_argv0", "epoch", "flag_id")
# Three real callers with three distinct real reasons: a build that writes one placeholder
# string everywhere cannot pass (failure-schema oracle-weak, case 2 -- distinct values).
GUARD_WHO, GUARD_WHY = "thinkpad-guard", "ThinkPad RAM 0.42 GB free, under the 1.5 GB reserve"
HUB_WHO, HUB_WHY = "jas", "Hub Resume button"
GX10_WHO, GX10_WHY = "memory-guard", "GX10 available memory 0.09 GB"


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def rows(ledger):
    """The ledger as JSONL records. A line that is not a JSON object is a finding, never a skip."""
    global FAILED
    if not ledger.exists():
        return []
    out = []
    for n, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError as e:
            FAILED += 1
            print(f"  [BAD]  ledger line {n} is not a JSON object ({e}): {line[:120]!r}")
            out.append({"__broken__": str(e)})
    return out


def call_resume(device, **kw):
    """resume_device with the clearer's identity. Today's signature has no who/why -- that IS
    the defect, so report it as a failed assertion rather than a traceback (oracle-crash)."""
    global FAILED
    try:
        sc.resume_device(device, **kw)
        return True
    except TypeError as e:
        FAILED += 1
        print(f"  [BAD]  resume_device({device!r}, {kw}) is not accepted: {e}")
        return False


def run():
    tmp = Path(tempfile.mkdtemp())
    orig_flag = sc.PAUSE_FLAG
    orig_ledger = getattr(sc, "DEVICE_PAUSE_LEDGER", None)
    sc.PAUSE_FLAG = tmp / "scheduler-paused.enabled"
    sc.DEVICE_PAUSE_LEDGER = tmp / "device-pause.jsonl"
    led = sc.DEVICE_PAUSE_LEDGER
    try:
        print("\nBefore anything happens the ledger is empty:")
        check("no rows before any pause", rows(led), [])

        print("\npause_device records the ACQUIRE:")
        sc.pause_device("thinkpad", GUARD_WHO, GUARD_WHY)
        check("the pause itself still works -- paused_devices() sees it, with the right who",
              sc.paused_devices().get("thinkpad", {}).get("who"), GUARD_WHO)
        r = rows(led)
        check("pausing appended exactly one row", len(r), 1)
        p = r[0] if r else {}
        check("every field the RCA names is present", sorted(k for k in KEYS if k not in p), [])
        check("device is the device paused", p.get("device"), "thinkpad")
        check("action is the literal 'pause'", p.get("action"), "pause")
        check("who is the caller that took the pause", p.get("who"), GUARD_WHO)
        check("why is the reason, verbatim", p.get("why"), GUARD_WHY)
        check("at is a UTC second in the flags' own format", bool(ISO.match(str(p.get("at")))), True)
        check("source_pid is this process", p.get("source_pid"), os.getpid())
        check("source_argv0 is a non-empty string",
              isinstance(p.get("source_argv0"), str) and bool(str(p.get("source_argv0")).strip()), True)
        check("epoch is a positive number",
              isinstance(p.get("epoch"), (int, float)) and float(p.get("epoch")) > 0, True)
        check("flag_id is a non-empty string",
              isinstance(p.get("flag_id"), str) and bool(str(p.get("flag_id")).strip()), True)

        print("\nresume_device records the CLEAR, naming the clearer AND whose pause it was:")
        before = led.read_text(encoding="utf-8") if led.exists() else ""
        cleared = call_resume("thinkpad", who=HUB_WHO, why=HUB_WHY)
        check("the clear itself still works -- the flag is gone", "thinkpad" in sc.paused_devices(), False)
        r = rows(led)
        if cleared:
            check("clearing appended one more row", len(r), 2)
            c = r[1] if len(r) > 1 else {}
            check("action is the literal 'clear'", c.get("action"), "clear")
            check("device", c.get("device"), "thinkpad")
            check("who is the CLEARER, not the pauser -- the RCA's question", c.get("who"), HUB_WHO)
            check("why is the clearer's reason, verbatim", c.get("why"), HUB_WHY)
            check("pause_who names whose pause was cleared", c.get("pause_who"), GUARD_WHO)
            check("flag_id pairs this clear to that acquire", c.get("flag_id"), p.get("flag_id"))
            check("at is a UTC second", bool(ISO.match(str(c.get("at")))), True)
            check("source_pid is this process", c.get("source_pid"), os.getpid())
            check("the clear row carries all nine fields", sorted(k for k in KEYS if k not in c), [])

        print("\nA clear of a device that was never paused writes NOTHING (no fabricated record):")
        call_resume("gx10")
        check("no row for a clear that cleared nothing", len(rows(led)), 2)
        check("and the flag was already absent, said", sc.paused_devices().get("gx10"), None)

        print("\nA second pause is a NEW pause, not a repeat of the first:")
        sc.pause_device("gx10", GX10_WHO, GX10_WHY)
        r = rows(led)
        check("the second pause appended a third row", len(r), 3)
        p2 = r[2] if len(r) > 2 else {}
        check("its device is the device paused", p2.get("device"), "gx10")
        check("its who/why are the second caller's, verbatim",
              (p2.get("who"), p2.get("why")), (GX10_WHO, GX10_WHY))
        check("its flag_id differs from the first pause's, so pairs stay unambiguous",
              bool(p2.get("flag_id")) and p2.get("flag_id") != p.get("flag_id"), True)

        print("\nAn unreadable flag is still cleared, and the record says so:")
        sc._device_flag("gx10").write_text("{not json", encoding="utf-8")
        check("an unreadable flag still pauses, said",
              str(sc.paused_devices().get("gx10", {}).get("why", "")).startswith("flag unreadable"), True)
        crash = ""
        try:
            call_resume("gx10", who=HUB_WHO, why=HUB_WHY)
        except Exception as e:                                  # noqa: BLE001 -- reported, never swallowed
            crash = f"{type(e).__name__}: {e}"
        check("clearing an unreadable flag does not raise", crash, "")
        r = rows(led)
        check("and it is recorded as a clear", [x.get("action") for x in r[3:]], ["clear"])
        check("with the flag's unreadable state named in why",
              "unreadable" in str(r[-1].get("why", "")), True)

        print("\nThe ledger is append-only end to end:")
        check("the earlier rows are byte-identical (append, never rewrite)",
              led.read_text(encoding="utf-8").startswith(before), True)
        check("every row is a JSON object on its own line",
              all(isinstance(x, dict) and "__broken__" not in x for x in r), True)
        check("four rows total: pause, clear, pause, clear", len(r), 4)
    finally:
        sc.PAUSE_FLAG = orig_flag
        if orig_ledger is None:
            try:
                del sc.DEVICE_PAUSE_LEDGER
            except AttributeError:
                pass
        else:
            sc.DEVICE_PAUSE_LEDGER = orig_ledger
    print(f"\n{PASSED} passed, {FAILED} failed\n")
    return FAILED


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
