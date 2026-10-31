"""t772: the standing-process census must see the whole box, and chart coverage must be exact.

New suite on purpose: scripts/tests/test_process_registry.py (t643) is a passing, frozen suite and
the bridge refuses to overwrite an existing suite (scripts/bridge.py:128-131). This file pins the
three things this task changes, and re-pins the t643 recorder behaviour so a build that breaks the
recorder is caught here too.

  D1 scope -- snapshot() enumerates `systemctl --user` only (process_registry.py:34), so a unit an
             installer creates as a SYSTEM unit (ollama.service) is invisible at the standing-process
             endpoint: Rule 4 case 2, no collector at the endpoint. (workspace/rca/50c99d891199.md)
  D2 cover -- on_chart() answers with a substring test (process_registry.py:56): the stem 'ollama' is
             'covered' by ollama-station2.service, so any ollama* unit is recorded as on-chart while
             absent from FLOW.json services[]. A census built on that certifies false coverage.
  t723     -- a census that reads an empty population must not be a verdict.

No live systemctl/crontab is read. Every command is answered by the fake `run` below, which RAISES
on any argv it was not written to answer -- the suite cannot pass by reading an empty population.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import process_registry as P

ok = 0
fail = 0


def check(name, cond):
    global ok, fail
    tag = "ok" if cond else "FAIL"
    print(f"  [{tag}]   {name}")
    ok += 1 if cond else 0
    fail += 0 if cond else 1


# NEVER THE LIVE STATE (the lesson recorded at scripts/tests/test_process_registry.py:13-17: coder's
# sandbox copies the working tree, so the live snapshot file exists there and 'the first snapshot'
# is not a first one). Redirect both paths to a temp dir before anything reads them.
_TMP = tempfile.mkdtemp()
P.SNAP = Path(_TMP) / "process-registry.json"
P.LEDGER = Path(_TMP) / "process-changes.jsonl"


def chart(*units):
    """A chart in FLOW.json's real shape -- services[] entries carry id/name/unit, and no entry is
    ollama.service (read 2026-10-08: 12 entries). Built as JSON so an exact-match implementation may
    parse it, and so a quoted whole-name lookup has the same shape the real chart has."""
    return json.dumps({"services": [{"id": "svc", "name": u, "unit": u} for u in units]})


class _Res:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0


def fake_run(user_units, system_units, cron_lines):
    """Answers ONLY the census's own commands. Anything else is a finding, not a reply."""
    def run(cmd, capture_output=True, text=True, timeout=None):
        assert timeout, f"subprocess call with no timeout: {cmd}"
        argv = [str(c) for c in cmd]
        joined = " ".join(argv)
        if argv[:2] == ["crontab", "-l"]:
            return _Res("".join(line + "\n" for line in cron_lines))
        if "list-unit-files" in joined:
            rows = user_units if "--user" in argv else system_units
            return _Res("".join(u + " enabled enabled\n" for u in rows))
        raise AssertionError(f"census asked for a command it is not written to answer: {cmd}")
    return run


def test_snapshot_enumerates_system_scope():
    run = fake_run(["scoping-queue@manager.service"], ["ollama.service", "ssh.service"],
                   ["*/10 * * * * python3 scripts/dispatch_loop.py"])
    s = P.snapshot(run=run)
    check("snapshot() enumerates SYSTEM-scope enabled units -- the ollama.service blind spot",
          {"system:ollama.service", "system:ssh.service"} <= set(s["units"]))
    check("  and tags user-scope units with their scope", "user:scoping-queue@manager.service" in s["units"])
    check("  and every item carries a scope tag (no un-tagged key leaks into the census)",
          all(u.split(":", 1)[0] in ("user", "system") for u in s["units"]))
    check("  and keeps the cron lines it has always kept",
          s["cron"] == ["*/10 * * * * python3 scripts/dispatch_loop.py"])


def test_record_tags_scope_and_names_it_off_chart():
    prev = {"cron": [], "units": {"user:scoping-queue@manager.service": True}}
    new = {"cron": [], "units": {"user:scoping-queue@manager.service": True, "system:ollama.service": False}}
    row = P.record(new, prev=prev, flow_text=chart("scoping-queue@manager.service"), write=False)
    check("an added system unit is recorded with its scope",
          bool(row) and "system:ollama.service" in row["added"])
    check("  and is named off_chart while services[] has no ollama.service",
          bool(row) and row["off_chart"] == ["system:ollama.service"])


def test_coverage_is_the_whole_name_not_a_substring():
    c = chart("ollama-station2.service")
    check("a unit that is a PREFIX of a charted unit is NOT covered (the ollama / ollama-station2 lie)",
          P.on_chart("ollama.service", c) is False)
    check("  the charted unit itself IS covered", P.on_chart("ollama-station2.service", c) is True)
    check("  a scoped name is covered by the same unit (scope tag stripped, not part of the name)",
          P.on_chart("system:ollama-station2.service", c) is True)
    check("  a scoped name of an uncharted unit is NOT covered",
          P.on_chart("system:ollama.service", c) is False)
    check("  a chart naming the bare stem, unquoted, does NOT cover the unit",
          P.on_chart("ollama-station2.service", "ollama-station2") is False)


def test_cron_coverage_is_the_whole_path_not_a_stem():
    path_chart = json.dumps({"scripts": ["scripts/dispatch_loop.py"]})
    check("a cron line running a charted path is covered",
          P.on_chart("cron: */10 * * * * python3 scripts/dispatch_loop.py", path_chart) is True)
    stem_chart = json.dumps({"notes": "timer-gemma"})
    check("  a chart naming only the stem (no extension, no path) does NOT cover the cron line",
          P.on_chart("cron: */15 * * * * bash scripts/timer-gemma.sh", stem_chart) is False)


def test_t643_recorder_behaviour_is_kept():
    # Verbatim shapes from scripts/tests/test_process_registry.py:18-29, so a build that breaks the
    # recorder fails here. flow_text is t643's non-JSON pseudo-chart -- coverage must not require JSON.
    flow = '"dispatch_loop" "manager-queue" "nas-backup"'
    base = {"cron": ["*/10 * * * * python3 scripts/dispatch_loop.py"],
            "units": {"scoping-queue@manager.service": True}}
    check("the first snapshot is a baseline, not a change",
          P.record(base, prev=None, flow_text=flow, write=False) is None)
    check("no change, no row",
          P.record(base, prev=base, flow_text=flow, write=False) is None)
    new = {"cron": base["cron"] + ["*/15 * * * * bash scripts/timer-gemma.sh"],
           "units": {"scoping-queue@manager.service": True, "nas-backup.timer": True}}
    row = P.record(new, prev=base, flow_text=flow, write=False)
    check("an added cron line and unit are recorded",
          bool(row) and row["added"] == ["cron: */15 * * * * bash scripts/timer-gemma.sh",
                                         "nas-backup.timer"])
    check("  and only the cron line is named off_chart (nas-backup IS on the chart)",
          bool(row) and row["off_chart"] == ["cron: */15 * * * * bash scripts/timer-gemma.sh"])
    back = P.record(base, prev=new, flow_text=flow, write=False)
    check("a removal is recorded too (the timers that vanished with no trail)",
          bool(back) and set(back["removed"]) == {"cron: */15 * * * * bash scripts/timer-gemma.sh",
                                                  "nas-backup.timer"})


def test_an_empty_census_is_not_a_verdict():
    s = P.snapshot(run=fake_run([], [], []))
    check("a box with nothing standing gives an empty unit population", s["units"] == {})
    row = P.record(s, prev={"cron": [], "units": {"user:scoping-queue@manager.service": True}},
                   flow_text=chart("scoping-queue@manager.service"), write=False)
    check("record() refuses to certify a census over an empty unit population", row is None)


def test_the_suite_writes_nothing_outside_its_temp_dir():
    check("no snapshot file was written", not P.SNAP.exists())
    check("no ledger row was written", not P.LEDGER.exists())


if __name__ == "__main__":
    for _name in sorted(globals()):
        if _name.startswith("test_") and callable(globals()[_name]):
            print(_name)
            globals()[_name]()
    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)
