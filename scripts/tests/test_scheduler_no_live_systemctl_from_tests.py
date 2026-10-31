#!/usr/bin/env python3
"""A test process must never reach the REAL systemctl through scheduler_commands (t1557, 2026-10-23).

WHY. test_device_pause.py faked SC._systemctl, but since b9cccaaca a person's gx10_off stops the
production units through its own subprocess.run, which nothing faked. Every run of that suite --
directly, or inside a coder.py sandbox baseline (test_coder_bad_shape, _branch_delivery,
_branch_not_clobbered all build one) -- ran `systemctl --user --no-block stop scoping-queue@manager.service
scoping-queue@auditor.service` on the live box. The hourly sweep did that every few minutes on 2026-10-23
(13:24, 14:22, 14:26, 14:29, 14:34 MDT) and the Manager and Auditor never stayed up.

THE RULE: from a process whose argv[0] is a test, scheduler_commands refuses to run the real
systemctl (RuntimeError naming the remedy) unless the suite has faked subprocess.run itself.

Hermetic: PATH puts a fake systemctl first that only logs, so even a red run stops nothing.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    log = td / "systemctl.log"
    fake = td / "bin" / "systemctl"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\necho \"$*\" >> " + str(log) + "\n"
                    "case \"$*\" in *list-dependencies*) printf 'production.target\\n"
                    "scoping-queue@manager.service\\nswift.service\\n';; esac\nexit 0\n")
    fake.chmod(0o755)
    os.environ["PATH"] = f"{fake.parent}{os.pathsep}{os.environ['PATH']}"

    import scheduler_commands as SC  # noqa: E402
    SC.PAUSE_FLAG = td / "scheduler-paused.enabled"
    SC.DEVICE_PAUSE_LEDGER = td / "device-pause.jsonl"      # device flags follow PAUSE_FLAG's dir

    def stops():
        return [l for l in (log.read_text().splitlines() if log.exists() else [])
                if " stop " in f" {l} " or " start " in f" {l} "]

    try:
        SC.gx10_off("jas", "paused from the Hub")
        check("gx10_off from a test refuses the real systemctl", "ran", "refused")
    except RuntimeError as e:
        check("gx10_off from a test refuses the real systemctl", "refused", "refused")
        check("the refusal names the remedy", "fake subprocess.run" in str(e), True)
    check("no stop/start reached systemctl from gx10_off", stops(), [])

    try:
        SC._systemctl("start")
        check("_systemctl from a test refuses the real systemctl", "ran", "refused")
    except RuntimeError:
        check("_systemctl from a test refuses the real systemctl", "refused", "refused")
    check("no stop/start reached systemctl at all", stops(), [])

    # A suite that fakes subprocess.run is hermetic and keeps working (test_gx10_pause_keeps_swift).
    seen = []
    real = subprocess.run

    def fake_run(cmd, *a, **k):
        seen.append(list(cmd))
        out = "production.target\nscoping-queue@manager.service\nswift.service\n" if "list-dependencies" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")
    subprocess.run = fake_run
    try:
        ok, _ = SC.gx10_off("jas", "paused from the Hub")
    finally:
        subprocess.run = real
    check("with subprocess.run faked, gx10_off still runs", ok, True)
    check("and it stopped the units through the fake",
          any(c[:4] == ["systemctl", "--user", "--no-block", "stop"] for c in seen), True)
    check("still nothing reached the real systemctl", stops(), [])

print(f"test_scheduler_no_live_systemctl_from_tests: {'FAILED' if fails else 'ok'} ({fails} failure(s))")
sys.exit(1 if fails else 0)
