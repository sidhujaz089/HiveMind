#!/usr/bin/env python3
"""t1331 oracle: an announced N-piece plan that shipped every piece in-session
is INCREMENTAL DELIVERY, not a deferral.

Red on main for exactly one reason: scripts/session_review.py defines neither
classify_multi_piece() nor deferral_completion_gaps(). Both are pure functions over
caller-supplied data -- no git, no network, no model, no /tmp, no subprocess.

Evidence for the fixture values: workspace/rca/38afe1ad7457.md section 1 -- the three
announced pieces (Models tab / Roles grid, Chat moved into the Hub, 'Assign to:') and the
commits that closed them in-session (744f734, c915bde, piece 3). Third hash is test data.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import session_review  # noqa: E402

ROLES = "Models tab / Roles grid"
CHAT = "Chat moved into the Hub"
ASSIGN = "Assign to: replacing needs:"
H_ROLES = "744f734"
H_CHAT = "c915bde"
H_ASSIGN = "e41f902"


def test_all_pieces_committed_in_session_is_incremental_delivery():
    pieces = [{"label": ROLES, "commit": H_ROLES},
              {"label": CHAT, "commit": H_CHAT},
              {"label": ASSIGN, "commit": H_ASSIGN}]
    # extra unrelated commit in the window: completion must not be len(pieces)==len(commits)
    commits = [H_ROLES, H_CHAT, H_ASSIGN, "991ab04"]
    got = session_review.classify_multi_piece(pieces, commits)
    assert got["kind"] == "incremental-delivery", got
    assert got["completed_in_session"] is True, got
    assert got["unshipped"] == [], got


def test_one_unshipped_piece_stays_a_deferral_and_names_it():
    pieces = [{"label": ROLES, "commit": H_ROLES},
              {"label": CHAT, "commit": H_CHAT},
              {"label": ASSIGN, "commit": None}]
    got = session_review.classify_multi_piece(pieces, [H_ROLES, H_CHAT])
    assert got["kind"] == "deferral", got
    assert got["completed_in_session"] is False, got
    assert got["unshipped"] == [ASSIGN], got


def test_a_commit_not_in_the_session_window_counts_as_unshipped():
    pieces = [{"label": ROLES, "commit": "dead000"}]
    got = session_review.classify_multi_piece(pieces, [H_ROLES, H_CHAT])
    assert got["kind"] == "deferral", got
    assert got["unshipped"] == [ROLES], got


def test_no_pieces_announced_is_not_evidence_of_delivery():
    got = session_review.classify_multi_piece([], [H_ROLES, H_CHAT])
    assert got["kind"] == "deferral", got
    assert got["completed_in_session"] is False, got


def test_a_short_hash_ships_a_piece_recorded_against_a_full_hash():
    pieces = [{"label": ROLES, "commit": H_ROLES},
              {"label": CHAT, "commit": H_CHAT},
              {"label": ASSIGN, "commit": H_ASSIGN}]
    commits = [H_ROLES + "ab991", H_CHAT + "0002", H_ASSIGN + "cc11"]
    got = session_review.classify_multi_piece(pieces, commits)
    assert got["kind"] == "incremental-delivery", got
    assert got["completed_in_session"] is True, got


def test_a_hash_shorter_than_seven_chars_does_not_count_as_shipped():
    pieces = [{"label": ROLES, "commit": "744"}]
    got = session_review.classify_multi_piece(pieces, [H_ROLES + "ab991"])
    assert got["kind"] == "deferral", got
    assert got["unshipped"] == [ROLES], got


def test_deferral_record_without_completed_in_session_is_a_violation():
    gaps = session_review.deferral_completion_gaps(
        {"kind": "deferral", "quote": "I'll ship it in three pieces"}, [H_ROLES, H_CHAT])
    assert len(gaps) == 1, gaps
    assert "completed_in_session" in gaps[0], gaps


def test_deferral_record_that_shipped_every_piece_is_complete():
    record = {"kind": "deferral",
              "pieces": [{"label": ROLES, "commit": H_ROLES}],
              "completed_in_session": True}
    assert session_review.deferral_completion_gaps(record, [H_ROLES]) == []


def test_pieces_recorded_with_no_commit_log_is_a_violation():
    record = {"kind": "deferral",
              "pieces": [{"label": ROLES, "commit": H_ROLES}],
              "completed_in_session": True}
    gaps = session_review.deferral_completion_gaps(record, [])
    assert len(gaps) == 1, gaps
    assert "commit" in gaps[0], gaps


def test_a_non_deferral_finding_reports_no_deferral_gap():
    assert session_review.deferral_completion_gaps({"kind": "for-jas", "quote": "x"}, []) == []
    assert session_review.deferral_completion_gaps({"kind": "unresolved", "quote": "y"},
                                                  [H_ROLES]) == []


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
        except Exception as exc:  # a missing function is a FAIL, not a silent skip
            failed += 1
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
    print("%d/%d passed" % (len(tests) - failed, len(tests)))
    sys.exit(1 if failed else 0)
