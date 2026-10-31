#!/usr/bin/env python3
"""t1459 -- the finding -> closing-commit join row.  Run standalone:

    python3 scripts/tests/test_finding_closure.py

WHY (workspace/rca/fb7d098621b5.md).  Nothing joins a finding key to the commit that closed its
subject.  Commit 91ad97f58d833bf8d4082707cf2a708107470412 (git show --stat 91ad97f5) names
projects/local-inference/methodology.py in its message; the finding record
workspace/session-reviews/findings/done/fb7d098621b5.json carries the quote that led to the RCA;
workspace/session-reviews/ledger.jsonl:1037 holds the key's one `event: finding` row.  None of the
three points at the others, and no existing recorder can recover the join -- grep -rn
"findings-closure|findings_closure" --include=*.py over the tree returns nothing, and
workspace/findings-closure.jsonl does not exist.

This suite pins the new writer at scripts/finding_closure.py.  It is RED on main for exactly one
reason: that module does not exist yet.  It writes ONLY inside tempfile directories and READS the
real finding record off disk, so it touches no live state.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
RECORD_DONE = ROOT / "workspace" / "session-reviews" / "findings" / "done" / "fb7d098621b5.json"
RECORD_OPEN = ROOT / "workspace" / "session-reviews" / "findings" / "fb7d098621b5.json"

# Real values, all read off disk, so no assertion can be satisfied by a placeholder.
CLOSING_SHA = "91ad97f58d833bf8d4082707cf2a708107470412"      # git show --stat 91ad97f5
SECOND_SHA = "05066947"                                        # a later commit (git log --oneline)
TASK_ID = "t1459"
AT = "2026-10-18T16:04:05Z"                                    # ledger.jsonl:2331 (event=rca)
CLOSING_MESSAGE = ("fix(workspace): close the three defects the Qwen pair left open\n\n"
                   "methodology.py set no num_ctx at all.")    # from git show 91ad97f5


def writer():
    """The module under test.  Imported inside a function and reported as a FAILED ASSERTION,
    not a traceback, so the suite is red for the missing change and not for its own import."""
    sys.path.insert(0, str(SCRIPTS))
    try:
        import finding_closure
    except Exception as exc:
        raise AssertionError(
            "scripts/finding_closure.py does not exist yet -- this is the change t1459 makes "
            "(import of finding_closure failed: %r)" % (exc,))
    return finding_closure


def real_finding():
    """The finding as it sits on disk: the join's input is the record, so the fixture is not
    synthetic.  Returns (record_dict, record_path)."""
    for path in (RECORD_DONE, RECORD_OPEN):
        if path.is_file():
            with open(path, encoding="utf-8") as handle:
                return json.load(handle), path
    raise AssertionError(
        "the finding record for fb7d098621b5 is not on disk at %s (nor %s); the join's input is "
        "the record on disk, so this fixture cannot fall back to a synthetic one"
        % (RECORD_DONE, RECORD_OPEN))


def quoted_path(record, path):
    """The path the finding quotes, read out of the real record rather than assumed.  The matcher
    keys on it, so the test cannot name a path the finding never mentions."""
    text = json.dumps(record, default=str)
    paths = sorted(set(re.findall(r"[A-Za-z0-9_./-]+\.py", text)))
    if not paths:
        raise AssertionError(
            "the finding record %s quotes no .py path, so a path-matching join has nothing to "
            "match -- the record must be read by the writer, not guessed" % path)
    return paths


def rows_of(store):
    with open(store, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_writer_exposes_the_join_api():
    module = writer()
    for name in ("record_closure", "commit_closure"):
        assert callable(getattr(module, name, None)), \
            "scripts/finding_closure.py defines no callable %s()" % name


def test_record_closure_appends_one_exact_row():
    module = writer()
    with tempfile.TemporaryDirectory() as tmp:
        store = str(Path(tmp) / "findings-closure.jsonl")
        returned = module.record_closure(store, AT, CLOSING_SHA, "fb7d098621b5",
                                         "projects/local-inference/methodology.py",
                                         "message-anchor", TASK_ID)
        assert Path(store).is_file(), "record_closure did not create the store at %s" % store
        rows = rows_of(store)
        assert len(rows) == 1, "one call must append exactly one row, found %d" % len(rows)
        row = rows[0]
        assert set(row) == {"at", "sha", "key", "file", "method", "task_id"}, \
            "a join row's fields must be exactly the join's, found %s" % sorted(row)
        assert row["sha"] == CLOSING_SHA, "sha must be the value passed, got %r" % (row["sha"],)
        assert row["key"] == "fb7d098621b5", "key must be the finding's own key, got %r" % (row["key"],)
        assert row["file"] == "projects/local-inference/methodology.py", \
            "file must be the path named by the finding/commit, got %r" % (row["file"],)
        assert row["method"] == "message-anchor", \
            "method must be the exact value passed -- asserting a substring lets 'diff-path' pass"
        assert row["task_id"] == TASK_ID, "task_id must be the value passed, got %r" % (row["task_id"],)
        assert row["at"] == AT, "at must be the value passed, got %r" % (row["at"],)
        if returned is not None:
            assert json.loads(json.dumps(returned, default=str))["key"] == "fb7d098621b5", \
                "the row returned is not the row written: %r" % (returned,)


def test_the_store_is_append_only():
    module = writer()
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "findings-closure.jsonl"
        module.record_closure(str(store), AT, CLOSING_SHA, "fb7d098621b5",
                              "projects/local-inference/methodology.py", "message-anchor", TASK_ID)
        first = store.read_text(encoding="utf-8")
        module.record_closure(str(store), AT, SECOND_SHA, "fb7d098621b5",
                              "projects/local-inference/methodology.py", "diff-path", TASK_ID)
        rows = rows_of(store)
        assert len(rows) == 2, \
            "a finding closed by two commits must keep both join rows, found %d" % len(rows)
        assert store.read_text(encoding="utf-8").startswith(first), \
            "record_closure rewrote the store; the RCA asks for an append-only writer"
        assert rows[1]["sha"] == SECOND_SHA and rows[1]["method"] == "diff-path", \
            "the second row must carry its own commit's sha and method, got %r" % (rows[1],)


def test_commit_closure_joins_the_finding_on_disk_to_a_commit_touching_its_path():
    module = writer()
    record, path = real_finding()
    paths = quoted_path(record, path)
    target = paths[0]
    with tempfile.TemporaryDirectory() as tmp:
        store = str(Path(tmp) / "findings-closure.jsonl")
        appended = module.commit_closure(store, AT, CLOSING_SHA, CLOSING_MESSAGE,
                                         [target], [record])
        assert isinstance(appended, list), \
            "commit_closure must return the rows it appended, got %s" % type(appended).__name__
        assert len(appended) == 1, \
            "the record %s quotes %s and the commit touches %s, so exactly one join row is "\
            "expected, got %d" % (path.name, target, target, len(appended))
        row = appended[0]
        assert row["key"] == path.stem, \
            "the join row's key is the finding's key, i.e. the record's stem %r, got %r" % (path.stem, row["key"])
        assert row["file"] == target, "the join row's file is the quoted path, got %r" % (row["file"],)
        assert row["sha"] == CLOSING_SHA, "the join row's sha is the commit being closed, got %r" % (row["sha"],)
        assert row["method"] == "diff-path", \
            "the path was in the commit's changed paths, so method is 'diff-path', got %r" % (row["method"],)
        assert rows_of(store) == [row], "what commit_closure returns must be what it appended"


def test_commit_closure_uses_the_message_anchor_when_the_path_is_not_in_the_diff():
    module = writer()
    record, path = real_finding()
    target = quoted_path(record, path)[0]
    message = "fix: close finding %s -- %s needed the same treatment" % (path.stem, target)
    with tempfile.TemporaryDirectory() as tmp:
        store = str(Path(tmp) / "findings-closure.jsonl")
        appended = module.commit_closure(store, AT, CLOSING_SHA, message, [], [record])
        assert len(appended) == 1, \
            "the commit message names %s, which the finding quotes, so one row is expected, "\
            "got %d" % (target, len(appended))
        assert appended[0]["method"] == "message-anchor", \
            "a path named only in the message joins as 'message-anchor', got %r" % (appended[0]["method"],)
        assert appended[0]["key"] == path.stem and appended[0]["file"] == target


def test_a_commit_matching_no_open_finding_records_nothing():
    module = writer()
    record, path = real_finding()
    quoted = set(quoted_path(record, path))
    unrelated = "services/unrelated-thing.py"
    assert unrelated not in quoted, "fixture bug: %s is quoted by the record itself" % unrelated
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "findings-closure.jsonl"
        appended = module.commit_closure(str(store), AT, SECOND_SHA,
                                         "chore: unrelated tidy", [unrelated], [record])
        assert list(appended) == [], \
            "a commit touching nothing the finding quotes must record nothing, got %r" % (list(appended),)
        assert not store.exists(), \
            "commit_closure created the store for a commit that matches no finding -- a writer "\
            "that appends to every commit passes the positive cases and is the defect"


if __name__ == "__main__":
    cases = sorted((n, f) for n, f in list(globals().items())
                   if n.startswith("test_") and callable(f))
    failures = 0
    for name, fn in cases:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print("FAIL %s: %s" % (name, exc))
        except Exception as exc:
            failures += 1
            print("ERROR %s: %s: %s" % (name, type(exc).__name__, exc))
    print("%d/%d passed" % (len(cases) - failures, len(cases)))
    sys.exit(1 if failures else 0)
