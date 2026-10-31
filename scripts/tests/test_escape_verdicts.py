#!/usr/bin/env python3
"""t938: every escape candidate gets a VERDICT ROW, so "0 escapes across 116 kept patches" is evidence and not an unjudged list. Red on main for one reason: scripts/escape_watch.py defines no VERDICTS, candidate_id(), record_verdict(), verdicts() or check_verdicts().

Why this is a NEW file and not scripts/tests/test_escape_watch.py: that suite exists (t208, green on main today) and bridge.py refuses an oracle chunk whose file already exists with different text, so this behaviour is pinned where the bridge can write it. The t208 candidate rules are repeated here because a verdict is keyed on (task, merge) of a candidate: the same fixtures, asserted twice, so the two suites cannot drift apart.

No live state: the writer target and the dismissal ledger are rebound into a tempfile.mkdtemp directory, and the repository's own workspace/session-reviews/escape-verdicts.jsonl and workspace/escape-dismissals.jsonl are checked to be untouched by this run. No subprocess, no model call, no fixed /tmp path, nothing left running.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import escape_watch as E  # noqa: E402

fails = []


def ck(label, ok):
    print(f"  [{'ok' if ok else 'BAD'}]  {label}")
    if not ok:
        fails.append(label)


# --- t208: which open tasks are candidates (the frozen suite's fixtures, unchanged) ------
E.kept_merges = lambda days: [("abc12345", "2026-10-01T12:00:00+00:00", "coder/brief-aa", "t100",
                                ["scripts/widget.py"])]
E.open_tasks = lambda: [
    ({"id": "t201", "title": "widget.py crashes on empty input", "created": "2026-10-01T130000Z"}, ""),
    ({"id": "t202", "title": "widget.py was slow", "created": "2026-09-30T130000Z"}, ""),
    ({"id": "t203", "title": "gadget.py crashes", "created": "2026-10-01T130000Z"}, ""),
    ({"id": "t100", "title": "the build's own task names widget.py", "created": "2026-10-01T130000Z"}, ""),
    ({"id": "t204", "title": "Scope RCA: oracle-weak", "created": "2026-10-01T130000Z"}, "scripts/widget.py"),
]
got = [c["task"] for c in E.candidates()]
ck("a defect filed AFTER the merge naming its file is a candidate", "t201" in got)
ck("one filed BEFORE the merge is not", "t202" not in got)
ck("one about another file is not", "t203" not in got)
ck("the build's own task is not", "t100" not in got)
ck("a Scope RCA collector is not", "t204" not in got)

# --- t938: every candidate gets a verdict -------------------------------------------------
# Distinct tasks, merges and files throughout: the real code keys a candidate on (task, merge),
# so one repeated placeholder value could not tell a verdict about one merge from a verdict about
# another.
C1 = {"task": "t938a", "title": "widget.py crashes on empty input", "merge": "abc12345def",
      "branch": "coder/brief-aa", "built_for": "t100", "files": ["scripts/widget.py"]}
C2 = {"task": "t938a", "title": "widget.py crashes on empty input", "merge": "99ff0012ab",
      "branch": "coder/brief-bb", "built_for": "t200", "files": ["scripts/widget.py"]}
C3 = {"task": "t938b", "title": "socket.py drops a frame", "merge": "abc12345def",
      "branch": "coder/brief-aa", "built_for": "t100", "files": ["scripts/socket.py"]}
C4 = {"task": "t938c", "title": "panel.py shows the wrong count", "merge": "77aa1122cc",
      "branch": "coder/brief-cc", "built_for": "t300", "files": ["scripts/panel.py"]}
C5 = {"task": "t938c", "title": "ledger.py double-counts", "merge": "55cc3344dd",
      "branch": "coder/brief-ee", "built_for": "t500", "files": ["scripts/ledger.py"]}

WORDS = ("confirmed-escaped", "dismissed", "expired")
NEEDED = ("VERDICTS", "candidate_id", "record_verdict", "verdicts", "check_verdicts")
missing = [n for n in NEEDED if not hasattr(E, n)]
if missing:
    print(f"  escape_watch defines none of: {', '.join(missing)} -- every t938 check below is red "
          f"for that reason, which is the change this brief asks for")

# what the production paths are, read BEFORE anything is redirected into the temp dir
DECLARED = getattr(E, "VERDICTS", None)
DECLARED_DISMISSED = E.DISMISSED
live_verdicts_before = Path(DECLARED).exists() if DECLARED is not None else None
live_dismissed_before = Path(DECLARED_DISMISSED).exists()

TMP = Path(tempfile.mkdtemp(prefix="t938-"))
E.VERDICTS = TMP / "escape-verdicts.jsonl"
E.DISMISSED = TMP / "escape-dismissals.jsonl"


def ledger(name):
    """A fresh ledger per check, so each one sees only the rows it wrote itself."""
    E.VERDICTS = TMP / name
    return E.VERDICTS


def write_rows(rows):
    E.VERDICTS.parent.mkdir(parents=True, exist_ok=True)
    E.VERDICTS.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _path_is_the_recorded_one():
    return (DECLARED is not None
            and str(Path(str(DECLARED)).relative_to(E.REPO))
            == "workspace/session-reviews/escape-verdicts.jsonl")


def _id_shape():
    return (E.candidate_id("t938a", "abc12345def") == "t938a@abc12345"
            and E.candidate_id("t938a", "abc12345") == "t938a@abc12345"
            and E.candidate_id("t938a", "99ff0012ab") == "t938a@99ff0012")


def _one_row_with_the_real_values():
    ledger("v01.jsonl")
    E.record_verdict(C1, "dismissed")
    rows = E.verdicts()
    if len(rows) != 1:
        return False
    r = rows[0]
    return (r.get("candidate_id") == "t938a@abc12345"
            and r.get("merge_sha") == "abc12345"
            and r.get("task") == "t938a"
            and list(r.get("files") or []) == ["scripts/widget.py"]
            and r.get("verdict") == "dismissed"
            and r.get("actor") == "claude-code"
            and isinstance(r.get("at"), str) and str(r.get("at"))[:4].isdigit())


def _all_three_verdict_words_are_writable():
    ledger("v02.jsonl")
    E.record_verdict(C1, "confirmed-escaped")
    E.record_verdict(C2, "dismissed")
    E.record_verdict(C4, "expired")
    return sorted({str(r.get("verdict")) for r in E.verdicts()}) == sorted(WORDS)


def _unknown_word_is_refused_and_writes_nothing():
    ledger("v03.jsonl")
    try:
        E.record_verdict(C1, "not-an-escape")
        return False                    # a free-text verdict is how a ledger goes quietly wrong
    except ValueError as e:
        msg = str(e)
        return (not E.verdicts()
                and "not-an-escape" in msg
                and all(w in msg for w in WORDS))


def _a_candidate_with_no_merge_is_refused():
    ledger("v04.jsonl")
    try:
        E.record_verdict({"task": "t938d", "files": ["scripts/x.py"]}, "dismissed")
        return False
    except ValueError as e:
        return "merge" in str(e) and not E.verdicts()


def _dismiss_records_the_verdict():
    ledger("v05.jsonl")
    E.DISMISSED = TMP / "d05.jsonl"
    E.dismiss("t938c", "55cc3344dd", "not an escape: the task predates the merge")
    rows = E.verdicts()
    return (len(rows) == 1
            and rows[0].get("candidate_id") == "t938c@55cc3344"
            and rows[0].get("verdict") == "dismissed"
            and "predates the merge" in str(rows[0]))


def _it_names_the_candidate_that_has_no_verdict():
    ledger("v06.jsonl")
    E.record_verdict(C1, "dismissed")
    E.record_verdict(C2, "confirmed-escaped")
    code, lines = E.check_verdicts(cands=[C1, C2, C3])
    text = "\n".join(str(x) for x in lines)
    return (code == 1 and "t938b@abc12345" in text
            and "t938a@abc12345" not in text and "t938a@99ff0012" not in text)


def _it_is_quiet_when_every_candidate_is_judged():
    ledger("v07.jsonl")
    for c, v in ((C1, "dismissed"), (C2, "dismissed"), (C3, "confirmed-escaped")):
        E.record_verdict(c, v)
    code, _ = E.check_verdicts(cands=[C1, C2, C3])
    return code == 0


def _no_candidates_is_not_a_finding():
    ledger("v08.jsonl")
    code, _ = E.check_verdicts(cands=[])
    return code == 0


def _a_verdict_on_another_merge_is_not_this_candidate_s():
    ledger("v09.jsonl")
    E.record_verdict(C2, "dismissed")            # same task, a DIFFERENT merge
    code, lines = E.check_verdicts(cands=[C1])
    return code == 1 and "t938a@abc12345" in "\n".join(str(x) for x in lines)


def _a_word_that_is_not_a_verdict_is_not_verdicted():
    ledger("v10.jsonl")
    E.record_verdict(C1, "dismissed")
    row = dict(E.verdicts()[0])                   # the writer's own keys: this tests the reader
    row["verdict"] = "seen"
    write_rows([row])
    code, _ = E.check_verdicts(cands=[C1])
    return code == 1


def _a_torn_ledger_row_is_reported_and_fails_closed():
    ledger("v11.jsonl")
    E.record_verdict(C1, "dismissed")
    good = E.verdicts()[0]
    torn = "{'task': 't938a', 'merge_sha': 'abc12345'"
    E.VERDICTS.write_text(json.dumps(good) + "\n" + torn + "\n", encoding="utf-8")
    rows = E.verdicts()
    torn_rows = [r for r in rows if "_unreadable" in r]
    code, lines = E.check_verdicts(cands=[C1])
    return (len(rows) == 2 and len(torn_rows) == 1
            and torn in str(torn_rows[0].get("_unreadable"))
            and code == 1
            and torn in "\n".join(str(x) for x in lines))


def _the_ledger_it_writes_is_the_temp_one():
    return str(E.VERDICTS).startswith(str(TMP))


def _no_live_state_was_touched():
    after_v = Path(DECLARED).exists() if DECLARED is not None else None
    after_d = Path(str(DECLARED_DISMISSED)).exists()
    return after_v == live_verdicts_before and after_d == live_dismissed_before


CHECKS = [
    ("VERDICTS is workspace/session-reviews/escape-verdicts.jsonl -- beside the RCA's own records",
     _path_is_the_recorded_one),
    ("candidate_id keys a candidate on (task, merge) with 8 chars of the sha, the shape dismiss uses",
     _id_shape),
    ("record_verdict appends ONE row carrying every field the RCA asked for, with real values",
     _one_row_with_the_real_values),
    ("all three verdict words are writable and land in the row verbatim",
     _all_three_verdict_words_are_writable),
    ("an unknown verdict word raises ValueError naming the word and all three allowed words, writes nothing",
     _unknown_word_is_refused_and_writes_nothing),
    ("a candidate with no merge is refused, naming the missing key, and writes nothing",
     _a_candidate_with_no_merge_is_refused),
    ("dismiss() records a 'dismissed' verdict row carrying the reason -- the trace DE-1 says is missing",
     _dismiss_records_the_verdict),
    ("check_verdicts exits 1 and names the candidate that has no verdict row, by its id",
     _it_names_the_candidate_that_has_no_verdict),
    ("check_verdicts exits 0 once every candidate has a verdict",
     _it_is_quiet_when_every_candidate_is_judged),
    ("zero candidates exits 0: nothing to judge is not a finding",
     _no_candidates_is_not_a_finding),
    ("a verdict recorded against a DIFFERENT merge of the same task does not clear the candidate",
     _a_verdict_on_another_merge_is_not_this_candidate_s),
    ("a row whose verdict is not one of the three words does not count as a verdict",
     _a_word_that_is_not_a_verdict_is_not_verdicted),
    ("a torn ledger row is reported whole and fails the check closed",
     _a_torn_ledger_row_is_reported_and_fails_closed),
    ("the writer writes inside the temp dir, not the repository",
     _the_ledger_it_writes_is_the_temp_one),
    ("the run leaves the live ledgers exactly as it found them",
     _no_live_state_was_touched),
]

for _label, _fn in CHECKS:
    try:
        ck(_label, bool(_fn()))
    except Exception as _e:                       # noqa: BLE001 -- a crash is a finding, reported whole
        ck(f"{_label} (raised {type(_e).__name__}: {_e})", False)

for f in fails:
    print("FAIL:", f)
print("test_escape_verdicts:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
