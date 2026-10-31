#!/usr/bin/env python3
"""t1054: a deferred finding record is not serialised unless it can be discharged.

Why (workspace/rca/7767927d7a9c.md): the finding carries disposition 'deferred' with covered_by
empty, and nothing on the record says what event discharges it, which command shows the discharge,
or which open task carries it. Its trigger fired on 2026-09-14 (merge aedf9ff5) and 27 days later
the promised command had still not been run, because the only link between the promise and the
event was a sentence in a chat turn.

The schema rungs exist. scripts/session_review.py defines DEFERRAL_FIELDS, deferral_violations,
refuse_if_deficient and DeferralContractError (verified present at lines 1062, 1069, 1102, 1065),
and scripts/tests/test_t1048_deferral_write_gate.py already pins the CALL SITE for t607's five
fields. What t1054 adds is the two fields nothing requires today -- trigger and recorder -- plus
the requirement that the tracker name an OPEN task, checked where the record is serialised.

Asserted, in the order a person would check by hand:
  - the validator says NOTHING about a record that is not deferred (a rule broadened past its own
    subject gets --no-verify'd within a week), and says everything about one that is;
  - trigger must be an event the machine can emit: a name from DEFERRAL_TRIGGERS. The prose in the
    RCA's own quote ('when the suite goes green') is refused; 'merge: aedf9ff5' is not;
  - recorder must point at a file and a line -- the place that will show the action was performed --
    so 'check later' is refused and 'scripts/session_end.py:42' is not;
  - the tracker on a PERSISTED record is tracked_as, not covered_by. On the write path a covered_by
    naming a tNNN is consumed by _keep as 'this finding is already covered by that task' and the
    record is never written, so requiring a tNNN in covered_by AT THE WRITE would make every
    deferred record unwritable. tracked_as must name an OPEN task: t999999 (no such task file) is
    refused, t1054 (present in workspace/tasks) is not;
  - refuse_if_undischarged raises the EXISTING DeferralContractError, naming every missing key in
    one message, and returns None when the record can be discharged;
  - THE WRITE PATH, not only the validator: _keep writes no finding JSON for a deferred record that
    cannot be discharged, prints a line naming the three keys, and counts no new finding; a record
    that can be discharged is written, keeping disposition 'deferred' and its tracked_as; a record
    that is not a deferral is written untouched;
  - the body of _keep() calls refuse_if_undischarged. A guard that exists and is never called is
    the exact defect this task is about, so asserting the validator alone would repeat it.

Red on main for exactly one reason: scripts/session_review.py defines none of DEFERRAL_TRIGGERS,
discharge_violations, refuse_if_undischarged, and _keep() serialises the record with no discharge
check. Every call into the missing API is guarded, so this reports the gap and exits 1 -- it
crashes on nothing. It writes only inside tempfile.TemporaryDirectory (FINDINGS, _log and _now are
redirected, so the run leaves no log row in the tree), starts no subprocess, makes no model call,
and writes nothing under the repository. pytest is not installed: run it as
python3 scripts/tests/test_deferral_discharge_gate.py
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review as SR

OPEN_ID = "t1054"          # present at the top level of workspace/tasks as t1054--*.md
MISSING_ID = "t999999"     # no such task file anywhere
KEYS = ("trigger", "recorder", "tracked_as")
NEEDED = ("DEFERRAL_TRIGGERS", "discharge_violations", "refuse_if_undischarged")

# The quote is the one scripts/tests/test_t1048_deferral_write_gate.py verified as token-safe: no
# word of six-plus characters contains '_', '.', '/' or a digit, so _archive_cover's 'no words'
# guard fires and the run reads no live task file.
QUOTE = ("The Manager gets a task to run a root-cause analysis and decide, and it resets "
         "the switch itself")
REQ = {"id": "t1054", "harness": "claude-code"}
ok = 0
fail = 0


def check(label, cond):
    global ok, fail
    print("  [%s] %s" % ("ok" if cond else "FAIL", label))
    if cond:
        ok += 1
    else:
        fail += 1


HAS = all(hasattr(SR, n) for n in NEEDED)
if not HAS:
    print("  scripts/session_review.py defines none of %s -- every check below is red for that "
          "reason, which is the change this oracle asks for" % ", ".join(NEEDED))


def rec(**over):
    """A deferral shaped like a real finding, carrying the three keys t1054 requires."""
    base = {"kind": "deferral",
            "quote": QUOTE,
            "why_missed": "Require a machine-emitted trigger for the deferred action",
            "where": "assistant, turn 314 of 364",
            "disposition": "deferred",
            "trigger": "merge: aedf9ff5",
            "recorder": "scripts/merge_accounting.py:1",
            "tracked_as": OPEN_ID,
            "covered_by": ""}
    base.update(over)
    return base


def viol(r):
    """The record's discharge violations, or None while the API is absent. None makes every
    assertion about it FAIL, which is what red-for-the-missing-change must look like; a raise is
    reported as a finding, never swallowed."""
    if not HAS:
        return None
    try:
        return [str(v) for v in SR.discharge_violations(r)]
    except Exception as e:
        print("  [FAIL] discharge_violations raised %s: %s" % (type(e).__name__, e))
        return None


def named(v):
    """Which of the three keys the violations name, read out of the violation TEXT -- not a count.
    A refusal that names one key is not the instruction the task asks for."""
    if not v:
        return []
    joined = " | ".join(str(x) for x in v)
    return [k for k in KEYS if k in joined]


def keep(found, tmp):
    """Drive the real writer with FINDINGS, _log and _now redirected into the temp dir, and
    restored in a finally so the run leaves no log row and no finding file in the tree. Never
    passes a tNNN covered_by: that branch notes a LIVE task file. Returns (new, json names, printed)."""
    req = dict(REQ)
    saved = {n: getattr(SR, n, None) for n in ("FINDINGS", "_log", "_now")}
    out = io.StringIO()
    res = None
    try:
        SR.FINDINGS = Path(tmp)
        SR._log = lambda *a, **k: None
        SR._now = lambda: "2026-10-15T00:00:00Z"
        with contextlib.redirect_stdout(out):
            res = SR._keep("req.json", req, found)
    except Exception as e:
        print("  [FAIL] _keep raised %s: %s" % (type(e).__name__, e))
    finally:
        for n, v in saved.items():
            if v is None:
                try:
                    delattr(SR, n)
                except AttributeError:
                    pass
            else:
                setattr(SR, n, v)
    names = sorted(p.name for p in Path(tmp).glob("*.json"))
    new = res[0] if isinstance(res, (tuple, list)) and len(res) >= 1 else None
    return new, names, out.getvalue()


def run():
    global ok, fail

    deficient = rec(trigger="", recorder="", tracked_as="")
    complete = rec()

    check("a deferred record carrying all three keys is not refused", viol(complete) == [])
    check("a deferred record carrying none of the three is refused, naming all three keys",
          named(viol(deficient)) == list(KEYS))
    check("a prose trigger is refused: the field must name a machine-emittable event",
          "trigger" in named(viol(rec(trigger="when the suite goes green"))))
    check("  and a trigger the merge endpoint can emit is accepted",
          viol(rec(trigger="merge: aedf9ff5")) == [])
    check("  and a trigger the suite and oracle endpoints can emit are accepted",
          viol(rec(trigger="suite-green: test_triage.py")) == []
          and viol(rec(trigger="oracle-green: scripts/tests/test_x.py")) == [])
    tg = getattr(SR, "DEFERRAL_TRIGGERS", None)
    tnames = [str(t).lower() for t in (tg or ())]
    check("the enumeration is published on the module, so the endpoints can emit one",
          isinstance(tg, (tuple, list, set, frozenset)) and bool(tg))
    check("  and it carries all three events the RCA names: merge, suite green, oracle green",
          bool(tnames) and all(any(p in n for n in tnames) for p in ("merge", "suite", "oracle")))
    check("a recorder with no file:line is refused",
          "recorder" in named(viol(rec(recorder="check later"))))
    check("  and a recorder pointing at a file and a line is accepted",
          viol(rec(recorder="scripts/session_end.py:42")) == [])
    check("a tracker naming a task that is not open is refused",
          "tracked_as" in named(viol(rec(tracked_as=MISSING_ID))))
    check("  and a tracker that is not a tNNN is refused",
          "tracked_as" in named(viol(rec(tracked_as="UNTRACKED: no owner yet"))))
    check("the gate says nothing about a record that is not deferred",
          viol(rec(kind="unresolved", disposition="unresolved",
                   trigger="", recorder="", tracked_as="")) == [])
    check("  and nothing about a resolved-in-session record",
          viol(rec(disposition="resolved-in-session",
                   trigger="", recorder="", tracked_as="")) == [])

    msg = ""
    if HAS:
        try:
            SR.refuse_if_undischarged(deficient)
        except SR.DeferralContractError as e:
            msg = str(e)
        except Exception as e:
            print("  [FAIL] refuse_if_undischarged raised %s, not DeferralContractError: %s"
                  % (type(e).__name__, e))
    check("refuse_if_undischarged raises the EXISTING DeferralContractError on the deficient record",
          bool(msg))
    check("  and the message names every key that is missing, so the refusal IS the instruction",
          all(k in msg for k in KEYS))
    check("  and returns None when the record can be discharged",
          HAS and SR.refuse_if_undischarged(complete) is None)

    with tempfile.TemporaryDirectory() as tmp:
        new, names, printed = keep([deficient], tmp)
        check("THE WRITER writes no finding JSON for a deferred record that cannot be discharged",
              names == [])
        check("  and the refusal is printed, naming the three keys as the instruction",
              "DEFERRAL-REFUSED" in printed and all(k in printed for k in KEYS))
        check("  and the run counts no new finding", new == 0)

    open_ok = False
    _to = getattr(SR, "_task_open", None)
    if callable(_to):
        try:
            open_ok = bool(_to(OPEN_ID))
        except Exception as e:
            print("  [note] _task_open raised %s: %s" % (type(e).__name__, e))

    with tempfile.TemporaryDirectory() as tmp2:
        if open_ok:
            new2, names2, printed2 = keep([complete], tmp2)
            body = {}
            if len(names2) == 1:
                try:
                    body = json.loads((Path(tmp2) / names2[0]).read_text(encoding="utf-8"))
                except (OSError, ValueError) as e:
                    print("  [FAIL] the finding written for a dischargeable deferral is unreadable: %s" % e)
            check("a deferred record that CAN be discharged is written", len(names2) == 1)
            check("  and it keeps disposition 'deferred' and its open tracker",
                  body.get("disposition") == "deferred" and body.get("tracked_as") == OPEN_ID)
            check("  and it keeps the trigger and recorder it arrived with",
                  body.get("trigger") == "merge: aedf9ff5"
                  and body.get("recorder") == "scripts/merge_accounting.py:1")
            check("  and the writer stays silent when there is nothing to refuse",
                  "DEFERRAL-REFUSED" not in printed2)
            check("  and the run counts the finding", new2 == 1)
        else:
            print("  [SKIP] the write-the-complete-record case: _task_open does not report "
                  "%s as open from this tree, so the case cannot be decided here" % OPEN_ID)

    with tempfile.TemporaryDirectory() as tmp3:
        plain = rec(kind="unresolved", disposition="unresolved",
                    trigger="", recorder="", tracked_as="")
        new3, names3, printed3 = keep([plain], tmp3)
        check("a record that is not a deferral is written untouched by the gate", len(names3) == 1)
        check("  and is not refused by it", "DEFERRAL-REFUSED" not in printed3)
        check("  and is counted", new3 == 1)

    src = ""
    try:
        src = Path(SR.__file__).read_text(encoding="utf-8")
    except OSError as e:
        print("  [FAIL] scripts/session_review.py is unreadable: %s" % e)
        fail += 1
    start = src.find("def _keep(")
    end = src.find("\ndef ", start)
    body_src = src[start:end] if start >= 0 and end > start else ""
    check("the body of _keep() calls refuse_if_undischarged: a contract the writer never calls is "
          "the defect this task exists to close, so the validator alone is not enough",
          "refuse_if_undischarged(" in body_src)

    print("")
    print("%d passed, %d failed" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(run())
