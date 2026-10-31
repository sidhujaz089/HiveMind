#!/usr/bin/env python3
"""t1206 -- every standing process this repository ships is named on the Flow chart.

The hourly check (scripts/check_workspace.py:1726-1755, `check_flow_record`) failed at
2026-10-14T22:04:44Z on 708ab41f with one finding: `scoping-queue@auditor.service`. That unit is
shipped in this repository (deploy/systemd/scoping-queue@auditor.service, ExecStart=
/home/user/ai-workspace/scripts/scoping_queue.sh auditor -- the Auditor's second scoping
loop, t1085) and nothing in projects/local-inference/models/FLOW.json names it. FLOW.json is
the file scripts/check_workspace.py reads as the system of record (Jas, 2026-10-01: "any
permanent script must be represented in the flowchart").

This suite pins the CLASS, not the instance. It enumerates the *.service files the repository
ships in deploy/systemd/, takes the name the checker compares (the file's stem,
`unit.rsplit(".", 1)[0]` -- scripts/check_workspace.py:1741), and fails on every one the chart
does not name. A baked list of today's units is a ratchet that locks in today's set and says
nothing about the service added next month; this list is computed from the files on disk.

Reads two files in the repository. No live state: no systemctl, no crontab, no model, no
process started, nothing written.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> the repository root
UNITS = ROOT / "deploy" / "systemd"
FLOW = ROOT / "projects" / "local-inference" / "models" / "FLOW.json"
MARKER = "/ai-workspace/"                           # as ExecStart spells this repo's path
AUDITOR = "auditor-scoping"                         # the unit t1206 was filed for


def _flow_text():
    return FLOW.read_text(encoding="utf-8", errors="replace")


def _repo_units():
    """The *.service files this repository ships that run this repository's code."""
    if not UNITS.is_dir():
        return []
    rows = []
    for f in sorted(UNITS.glob("*.service")):
        if MARKER in f.read_text(encoding="utf-8", errors="replace"):
            rows.append(f)
    return rows


def test_the_shipped_units_are_found():
    """Nothing examined is not a pass (L-146): say so out loud if the sweep is empty."""
    rows = _repo_units()
    assert rows, (f"no *.service under {UNITS} contains {MARKER!r} -- the unit files no longer "
                  f"spell the repository path this way, so this suite proves nothing")
    assert FLOW.is_file(), f"{FLOW} is missing; the Flow chart is the system of record"
    json.loads(_flow_text())


def test_the_auditor_scoping_unit_is_named_on_the_chart():
    """The finding t1206 was filed for: red today on 708ab41f, for exactly that reason."""
    text = _flow_text()
    assert AUDITOR in text, (
        f"{AUDITOR}.service is shipped by this repository "
        f"({(UNITS / (AUDITOR + '.service')).relative_to(ROOT)}) and runs "
        f"scripts/scoping_queue.sh auditor, but "
        f"{FLOW.relative_to(ROOT)} does not name it. Put it on the chart -- the entry the "
        f"chart already carries for the sibling loop is the shape to mirror "
        f"(projects/local-inference/models/FLOW.json:1603, \"unit\": \"scoping-queue@manager.service\").")


def test_every_shipped_unit_is_named_on_the_chart():
    """The class, computed from the files on disk -- not from a list baked in here."""
    text = _flow_text()
    misses = [f.name for f in _repo_units() if f.name.rsplit(".", 1)[0] not in text]
    assert not misses, (
        f"{len(misses)} shipped unit(s) the chart does not name: {', '.join(misses)}. "
        "The Flow chart is the system of record, and scripts/check_workspace.py's "
        "standing-process check fails on every one of these. Add each one with "
        "python3 scripts/flow_change.py helper <script> <task-id> \"<what it does>\".")


if __name__ == "__main__":
    _tests = [(n, f) for n, f in sorted(globals().items())
              if n.startswith("test_") and callable(f)]
    _bad = 0
    for _name, _fn in _tests:
        try:
            _fn()
            print(f"  PASS  {_name}")
        except AssertionError as _e:
            _bad += 1
            print(f"  FAIL  {_name}: {_e}")
    print(f"{len(_tests) - _bad}/{len(_tests)} passed")
    sys.exit(1 if _bad else 0)
