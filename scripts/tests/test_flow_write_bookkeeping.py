#!/usr/bin/env python3
"""t760: a chart write stamps a citable flow_version; a revert restores chart CONTENT.

flow_change.py is the only writer of projects/local-inference/models/FLOW.json. It stamps no
version, so no claim about the chart can cite the chart that was in force when the sentence was
written, and record_lane_change reserves a flow_version argument it can only ever take from the
caller. Every dispatch that added the stamp was refused by tests/test_flow_change.py, which
compares the chart after a revert to the snapshot taken before the first write: a stamp that
snapshot never had makes that equality false for ANY implementation. This suite is the contract
that settles it -- the version is bookkeeping the writer ADDS, so a revert restores CONTENT key
for key, while the stamp is stamped on every write, the revert's own write included. Pinned on
the file on disk, with distinct values (two tasks, two names, one link) so an implementation that
copies one placeholder through cannot pass. Reading the chart writes nothing to it.

Run: python3 scripts/tests/test_flow_write_bookkeeping.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]

import flow_change  # noqa: E402
import flow_lint  # noqa: E402

# Keys a chart writer may add for its own bookkeeping. A revert restores CONTENT, so a difference
# in one of these is not a failed restore; any other key is. t760 adds flow_version; t1068 stamps
# added on every node, so this is a SET, stripped RECURSIVELY -- not a pop of one top-level key --
# and t1068's stamp needs no edit here.
BOOKKEEPING = {"flow_version", "added"}

CHART = {"_what": "the chart under test",
         "tasks": [{"id": "intake", "name": "Intake", "queue": "backlog"},
                   {"id": "scoping", "name": "Scope the queue", "queue": "scoping"}],
         "links": [{"from": "intake", "to": "scoping"}]}
ROW_KEYS = {"n", "at", "who", "what", "task", "ops", "before", "after"}


def _content(d):
    if isinstance(d, dict):
        return {k: _content(v) for k, v in d.items() if k not in BOOKKEEPING}
    if isinstance(d, list):
        return [_content(x) for x in d]
    return d


def _sandbox(tmp):
    # Point flow_change AND flow_lint at a throwaway chart: the live chart is never read
    # (revert() reads it through flow_lint.load()) and never written.
    flow = Path(tmp) / "FLOW.json"
    log = Path(tmp) / "FLOW-CHANGELOG.jsonl"
    flow.write_text(json.dumps(CHART, indent=1) + chr(10), encoding="utf-8")
    flow_change.FLOW = flow
    flow_change.LOG = log
    flow_lint.FLOW = flow
    return flow, log


def _rows(log):
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_write_stamps_the_version_the_chart_can_be_cited_by():
    with tempfile.TemporaryDirectory() as tmp:
        flow, _log = _sandbox(tmp)
        n = flow_change._write(CHART, {}, "test", "t760 stamp probe")
        data = json.loads(flow.read_text(encoding="utf-8"))
        assert isinstance(data.get("flow_version"), int), (
            "FLOW.json on disk carries no integer flow_version, so a claim about the chart has "
            "nothing to cite. Keys written: %r" % (sorted(data),))
        assert data["flow_version"] == n, (
            "the chart on disk says %r but _write returned %r: the returned number is the "
            "handle a finding cites" % (data.get("flow_version"), n))
        for k, v in CHART.items():
            assert data.get(k) == v, (
                "stamping the version changed the chart content at %r -- tasks, links and every "
                "other key are written through" % (k,))


def test_a_revert_restores_the_content_and_keeps_the_stamp_current():
    with tempfile.TemporaryDirectory() as tmp:
        flow, _log = _sandbox(tmp)
        original = json.loads(flow.read_text(encoding="utf-8"))
        moved = json.loads(flow.read_text(encoding="utf-8"))
        moved["tasks"][0]["queue"] = "scoping"
        moved["tasks"][1]["queue"] = "backlog"
        flow_change._write(moved, original, "test", "t760 a real edit")
        n = flow_change.revert(1)
        data = json.loads(flow.read_text(encoding="utf-8"))
        assert _content(data) == _content(original), (
            "a revert did not restore the snapshot in content: keys %r vs %r"
            % (sorted(data), sorted(original)))
        assert set(data) - set(original) <= BOOKKEEPING, (
            "the chart after a revert carries keys the snapshot never had, and they are not "
            "bookkeeping: %r" % (sorted(set(data) - set(original)),))
        assert set(original) - set(data) == set(), (
            "a revert dropped chart keys to make the restore compare equal: %r"
            % (sorted(set(original) - set(data)),))
        assert data.get("flow_version") == n, (
            "a revert is a write: it must stamp flow_version with its own number %r, not write "
            "a versionless chart back (disk has %r)" % (n, data.get("flow_version")))


def test_the_changelog_row_mirrors_the_stamped_number():
    with tempfile.TemporaryDirectory() as tmp:
        flow, log = _sandbox(tmp)
        n = flow_change._write(CHART, {}, "test", "t760 stamp probe")
        rows = _rows(log)
        assert len(rows) == 1, "expected one changelog row, got %d" % len(rows)
        row = rows[0]
        assert ROW_KEYS <= set(row), (
            "the changelog row lost keys it carries today: %r" % sorted(ROW_KEYS - set(row)))
        assert row.get("flow_version") == row.get("n") == n, (
            "the row is the version index: flow_version %r, n %r, returned %r -- one number, "
            "three places" % (row.get("flow_version"), row.get("n"), n))
        assert row.get("after") == json.loads(flow.read_text(encoding="utf-8")), (
            "the after field of the row is not the chart on disk: a version in one and not the "
            "other cannot be cited from either")


def _run():
    tests = sorted((name, fn) for name, fn in globals().items()
                   if name.startswith("test_") and callable(fn))
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
        else:
            print("ok %s" % name)
    print("%d/%d" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
