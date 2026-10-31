#!/usr/bin/env python3
"""t812: a lane-to-station switch is recorded as a ledger row.

Red on main because scripts/flow_change.py has no lane ledger: it defines no LANE_LOG
constant and no record_lane_change (search the file for 'def record_lane_change' -- not
there). Every case below therefore dies with AttributeError on the missing writer, for
the missing change, and for no other reason.

WHY (workspace/rca/6a87ffecd80c.md, D3). The 2026-09-28 hand-over of station 2 from the
manager queue to the Senior builder was executed by hand -- "restart the manager queue so
it leaves station 2, and run one real tick" -- and left no record at the endpoint where it
happened. The phrase survives repo-wide in exactly two files: that RCA and
session-reviews/ledger.jsonl. No ledger on the box answers "when did lane X stop running
on station Y, on whose instruction", and the lane then drifted three more times by hand
with the same silence.

WHAT THIS SUITE PINS: the row -- {ts, actor, task, from:{lane,station}, to:{lane,station},
command, flow_version, repo_head} -- appended whole, one per switch, at the path the task
names (workspace/flow-lane-changes.jsonl, NOT the tick rows t675 is slimming), with the
cause recorded when the repo head cannot be read. It pins the writer only. The endpoints
that supply the arguments -- scripts/manager_queue.sh, where STATION is set (75 -> 92),
and scripts/role_store.py, where a role's endpoint is saved -- are recorded in
workspace/run-logs/manager-queue/out/t812.md, because an oracle that reached them would
either call a model (scripts/ask_q16.py, manager_queue.sh:28) or write live state, and both
are refused for a test.

NOTE ON THE FIXTURE SHAPE: FLOW.json is deliberately not modelled here. Verified 2026-10-09
-- its top-level keys are _what _source tasks links sources services _services helpers, its
36 tasks carry no 'station' key, and it has no 'version' key. A fixture that moved a
node's 'station' field (an earlier scope's) asserted a field that does not exist, so the
values below are passed to the writer, not read out of a chart.
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import flow_change  # noqa: E402

LIVE_LEDGER = REPO / "workspace" / "flow-lane-changes.jsonl"

# The command from the switch being reproduced, quoted from the finding at
# workspace/rca/6a87ffecd80c.md. A fixture built from the real artefact, so the row is
# checked against the phrase that has to be recoverable, not against a synthetic one.
SWITCH_COMMAND = "restart the manager queue so it leaves station 2"

TS_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _rows(path):
    """Every row in the ledger, parsed. Raises if a line is not JSON -- a torn row is a finding."""
    p = Path(path)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _head(timeout=30):
    """The short HEAD, computed at test time (never stored: a stored hash rots on the next commit)."""
    r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


class LaneChangeRecord(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = {}
        for name in ("LANE_LOG", "REPO"):
            self._saved[name] = getattr(flow_change, name, None)
        self.ledger = Path(self._tmp.name) / "flow-lane-changes.jsonl"
        flow_change.LANE_LOG = self.ledger

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(flow_change, name, value)
        self._tmp.cleanup()

    # ---- the destination the task names -------------------------------------------------

    def test_the_ledger_lives_at_the_path_the_task_names(self):
        """Red today: flow_change has no LANE_LOG. Pins the filename AND the directory, so a
        build that files the rows in the tick rows t675 is slimming fails here."""
        original = self._saved["LANE_LOG"]
        self.assertIsNotNone(
            original,
            "flow_change has no LANE_LOG constant: nothing on the box records a lane move")
        self.assertEqual(Path(original).name, "flow-lane-changes.jsonl")
        self.assertEqual(Path(original).parent, REPO / "workspace")

    # ---- the row, field by field --------------------------------------------------------

    def test_record_lane_change_writes_one_row_with_the_named_fields(self):
        row = flow_change.record_lane_change(
            actor="jas", task="t760",
            from_lane="manager_queue", from_station=2,
            to_lane="manager_queue", to_station=1,
            command=SWITCH_COMMAND, flow_version="7", repo_head="dee6eaf5")
        rows = _rows(self.ledger)
        self.assertEqual(len(rows), 1, "record_lane_change wrote no ledger row")
        got = rows[0]
        self.assertEqual(got["actor"], "jas")
        self.assertEqual(got["task"], "t760")
        self.assertEqual(got["from"], {"lane": "manager_queue", "station": 2})
        self.assertEqual(got["to"], {"lane": "manager_queue", "station": 1})
        self.assertEqual(got["command"], SWITCH_COMMAND)
        self.assertEqual(got["flow_version"], "7")
        self.assertEqual(got["repo_head"], "dee6eaf5")
        self.assertRegex(got["ts"], TS_Z)
        # the values a reader discriminates on must be DISTINCT, not one placeholder repeated
        self.assertNotEqual(got["from"]["station"], got["to"]["station"])
        self.assertEqual(row.get("command"), SWITCH_COMMAND, "the writer must return the row it wrote")

    def test_the_ledger_is_appended_and_a_command_is_never_cut(self):
        """never-truncate-a-record: a row that reads complete with the command missing is
        worse than no row, because nothing flags the loss."""
        long_cmd = SWITCH_COMMAND + " " + ("run one real tick " * 150)   # ~2.4 KB, past any cap
        self.assertGreater(len(long_cmd), 1000)
        flow_change.record_lane_change("jas", "t760", "manager_queue", 2, "manager_queue", 1,
                                       command=SWITCH_COMMAND)
        flow_change.record_lane_change("manager", "t812", "scoping", 1, "scoping", 2,
                                       command=long_cmd)
        rows = _rows(self.ledger)
        self.assertEqual(len(rows), 2, "a second switch must append, not rewrite")
        self.assertEqual(rows[0]["command"], SWITCH_COMMAND, "an append must not disturb earlier rows")
        self.assertEqual(rows[1]["command"], long_cmd, "the command was truncated")
        self.assertEqual(rows[1]["from"], {"lane": "scoping", "station": 1})
        self.assertEqual(rows[1]["to"], {"lane": "scoping", "station": 2})

    def test_a_row_is_one_json_object_per_line(self):
        flow_change.record_lane_change("jas", "t812", "manager_queue", 2, "manager_queue", 1)
        text = self.ledger.read_text(encoding="utf-8")
        self.assertEqual(text.count("}\n"), 1, "the ledger is JSONL: one object per line, terminated")
        json.loads(text.strip())

    # ---- a recorder that hides its own failure is the defect this task is about ---------

    def test_a_git_failure_is_recorded_in_the_row_and_the_row_is_still_written(self):
        """swallowed-exception-introduced: repo_head must never be null, empty, absent, and
        never a bare exit code -- the cause goes in the row, and the row goes in the file."""
        flow_change.REPO = Path(self._tmp.name) / "not-a-repo"
        flow_change.record_lane_change("manager", "t812", "manager_queue", 2, "manager_queue", 1)
        rows = _rows(self.ledger)
        self.assertEqual(len(rows), 1,
                         "a repo-head failure swallowed the row: a lane move was lost silently")
        head = rows[0]["repo_head"]
        self.assertIsInstance(head, str)
        self.assertTrue(head, "repo_head is empty: the failure left no trace in the row")
        self.assertTrue(str(head).startswith("unavailable"),
                        "repo_head must say the switch was recorded with an unreadable head")
        self.assertNotIn(head, ("1", "128", "None", "Exception"),
                         "repo_head names a code, not a cause")

    def test_a_git_failure_never_raises_out_of_the_recorder(self):
        """Losing the row is not a permissible way of reporting a missing head."""
        flow_change.REPO = Path(self._tmp.name) / "not-a-repo"
        try:
            flow_change.record_lane_change("manager", "t812", "manager_queue", 2,
                                           "manager_queue", 1, command=SWITCH_COMMAND)
        except Exception as e:                                    # noqa: BLE001
            self.fail(f"record_lane_change raised {type(e).__name__}: {e} when the repo head "
                      f"could not be read -- a lane switch must not be lost to it")

    # ---- the defaults a caller must not have to supply ----------------------------------

    def test_repo_head_defaults_to_the_head_the_row_is_written_at(self):
        flow_change.record_lane_change("manager", "t812", "manager_queue", 2, "manager_queue", 1)
        rows = _rows(self.ledger)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["repo_head"], _head(),
                         "the row must name the commit it was written at, not a placeholder")
        self.assertEqual(rows[0]["flow_version"], "", "flow_version must be a string, defaulted")

    # ---- the oracle itself must stay out of the repository -------------------------------

    def test_the_suite_leaves_the_live_ledger_alone(self):
        """A test that writes workspace/flow-lane-changes.jsonl is a test that dirties the
        worktree and pollutes the very ledger it is meant to guard."""
        flow_change.record_lane_change("jas", "t812", "manager_queue", 2, "manager_queue", 1)
        self.assertFalse(LIVE_LEDGER.exists(),
                         "the suite wrote the live workspace/flow-lane-changes.jsonl: "
                         "LANE_LOG must be read at call time so a suite can redirect it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
