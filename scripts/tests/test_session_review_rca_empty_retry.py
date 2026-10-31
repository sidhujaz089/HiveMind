"""t1566: an RCA that answers NOTHING must be asked again, small, and that answer used.

Real artefact: workspace/session-reviews/failed/check-56cecdc3893f.json (read in full this session).
It carries two unusable attempts, both "no answer (timed out or empty)", at 2026-10-23T23:20:19Z and
2026-10-23T23:43:34Z -- 23 min 15 s apart, against RCA_LIMIT_S = 9000 (scripts/session_review.py:300).
A call capped at 9000 s cannot return in 23 minutes, so NEITHER attempt timed out: both came back
EMPTY. That is the shape briefing-the-local-model measures as a shared-budget failure -- reasoning and
the answer draw on ONE num_predict, so a long enough reasoning pass returns done_reason=length with
the content empty. The RCA prompt is where that budget gets eaten: rca() (scripts/session_review.py:1517-1518)
hands the model the RCA template, the WHOLE finding record and SKILL.md, and routes it through
skills.with_skills("rca"), which for this job loads THREE whole skill files -- verified in the run output
of scripts/tests/test_session_review_rca_empty_reason.py: "skills for manager/rca: loaded
root-cause-analysis, never-truncate-a-record, planning-for-sustainment".

Nothing on the box today notices an empty answer: rca() parses it (line 1525), fails, and gives up on
the first one (1526-1530). This suite pins the missing step -- re-ask ONCE from a prompt with the skill
block dropped, at the SAME 24000-token budget, and USE the answer that comes back.

Sibling contracts this suite deliberately does NOT touch, so that three scopes can land on rca():
test_session_review_rca_cause.py (t1416) owns the CAUSE string recorded on the finding and asserts every
RCA ask carries budget 24000 -- which is why the retry keeps 24000 and does not halve it;
test_session_review_rca_empty_reason.py (t1290) owns the wording of the "no answer" reason. This suite
asserts no wording beyond the one phrase t1290 itself pins as a substring.

Run: python3 scripts/tests/test_session_review_rca_empty_retry.py
"""
import contextlib
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import session_review as sr
import skills

KEY = "check-56cecdc3893f"
QUOTE = "[FAIL] The live checkout is on main"
RETRY_TITLE = "Keep the live checkout on main in hourly_checks"
FIRST_TITLE = "A title only the FIRST reply could have produced"

FINDING = {  # field names and values copied from the record named in the docstring
    "kind": "check-failure",
    "quote": QUOTE,
    "context": "it is on manager/t999-land-target-context; the services are committing there.",
    "where": "hourly check",
    "why_missed": "Filed as t1562 by hourly_checks.py; name t1562 in covered_by for the fix itself.",
    "covered_by": "t1562",
    "disposition": "deferred",
    "key": KEY,
    "request": "hourly-checks",
    "session": "",
    "harness": "hourly-checks",
    "found": "2026-10-23T21:28:51Z",
}
# covered_by is "" per item so rca() never consults the live tasks dir through _task_open().
REPLY_RETRY = json.dumps([{
    "title": RETRY_TITLE, "kind": "solution",
    "why": "the live checkout sits on a coder branch, so every hourly check fails",
    "change": "in scripts/hourly_checks.py, refuse to run when HEAD is not main",
    "covered_by": ""}], indent=1)
REPLY_FIRST = json.dumps([{
    "title": FIRST_TITLE, "kind": "solution",
    "why": "the live checkout sits on a coder branch",
    "change": "in scripts/hourly_checks.py, check HEAD before running",
    "covered_by": ""}], indent=1)

SKILL_HEAD = sr.SKILL.read_text(encoding="utf-8")[:120]


@contextlib.contextmanager
def _env(replies):
    """Redirect every path rca() can write, fake the model, hand back the fixtures.

    Patched names are module globals rca() resolves at CALL time: FINDINGS (session_review.py:40),
    FAILED (246), RCA_DIR (50), LEDGER (41), _log (65), and the task-minting trio _file_task (1582),
    _note_task (1375), _file_for_claude (282) -- _unusable() falls through to _file_for_claude when
    handed file_task=None (line 1529), so patching _file_task alone lets a give-up path mint a REAL
    task. skills.with_skills becomes an identity pass-through so skills_for() is never reached and
    skills.LOADS is never opened; the LOADS redirect is the belt.
    """
    tmp = Path(tempfile.mkdtemp())
    (tmp / "findings" / "done").mkdir(parents=True)
    (tmp / "failed").mkdir(parents=True)
    minted, rows, calls = [], [], []

    def _mint(title, body, **kw):
        minted.append((title, body))
        return "t9999"

    def asker(prompt, purpose, budget, **kw):
        assert isinstance(purpose, str) and purpose.startswith("RCA: session-review finding "), (
            "asker() was handed a prompt rca() does not build: %r" % (purpose,))
        assert isinstance(budget, int) and 0 < budget <= 24000, (
            "the RCA output budget must be an int in 1..24000, got %r" % (budget,))
        assert isinstance(prompt, str) and prompt.strip(), "rca() sent the model an empty prompt"
        assert len(calls) < len(replies), (
            "rca() made a %d. model call; this brief allows ONE retry per empty answer -- every "
            "extra call is a 9000-second slot (RCA_LIMIT_S, scripts/session_review.py:300)"
            % (len(calls) + 1,))
        calls.append({"prompt": prompt, "purpose": purpose, "budget": budget})
        return replies[len(calls) - 1]

    saved = {"ws": skills.with_skills, "loads": getattr(skills, "LOADS", None)}
    for name, val in (("FINDINGS", tmp / "findings"), ("FAILED", tmp / "failed"),
                      ("RCA_DIR", tmp / "rca"), ("LEDGER", tmp / "ledger.jsonl")):
        saved[name] = getattr(sr, name)
        setattr(sr, name, val)
    if saved["loads"] is not None:
        skills.LOADS = tmp / "skill-loads.jsonl"
    skills.with_skills = lambda text, task, *a, **k: (text, {"loaded": [], "indexed": []})
    saved["_log"] = sr._log
    sr._log = lambda row: rows.append(row)
    for name in ("_file_task", "_note_task", "_file_for_claude"):
        saved[name] = getattr(sr, name)
        setattr(sr, name, _mint)
    saved["_open_group_task"] = sr._open_group_task
    sr._open_group_task = lambda marker, tasks=None: ""

    finding = tmp / "findings" / (KEY + ".json")
    finding.write_text(json.dumps(FINDING, indent=1) + "\n", encoding="utf-8")
    try:
        yield {"tmp": tmp, "calls": calls, "minted": minted, "rows": rows,
               "asker": asker, "file_task": _mint, "finding": finding}
    finally:
        for name, val in saved.items():
            if name == "ws":
                skills.with_skills = val
            elif name == "loads":
                if val is not None:
                    skills.LOADS = val
            elif name == "_log":
                sr._log = val
            elif name == "_open_group_task":
                sr._open_group_task = val
            else:
                setattr(sr, name, val)


def _record(tmp):
    p = tmp / "findings" / (KEY + ".json")
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def test_an_empty_first_answer_is_re_asked_once_and_that_answer_is_used():
    with _env(["", REPLY_RETRY]) as e:
        ids = sr.rca(e["finding"], asker=e["asker"], file_task=e["file_task"])
        assert len(e["calls"]) == 2, (
            "an empty first answer was given up on: rca() made %d model call(s), expected 2. "
            "check-56cecdc3893f is exactly this shape: two attempts 23m15s apart, each recorded "
            "'no answer (timed out or empty)', neither of which filed anything."
            % len(e["calls"]))
        assert e["minted"] and e["minted"][0][0] == RETRY_TITLE, (
            "the retry's answer was not USED: file_task was handed %r. Only the SECOND reply "
            "carries %r, so a build that re-asks and then throws the answer away fails here."
            % (e["minted"], RETRY_TITLE))
        assert ids == ["t9999"], "a recovered run must return the task id it minted: %r" % (ids,)
        report = e["tmp"] / "rca" / (KEY + ".md")
        assert report.is_file() and RETRY_TITLE in report.read_text(encoding="utf-8"), (
            "no RCA report written from the retry's answer: %s" % report)
        rec = _record(e["tmp"])
        assert "unusable" not in rec, (
            "a run that recovered was still marked unusable: %r" % (rec.get("unusable"),))
        assert rec.get("outcome_task") == "t9999" and str(rec.get("rca_report", "")).strip(), (
            "a recovered run wrote no outcome back on its record, so the next hourly pass re-dispatches "
            "the same key: %r" % rec)


def test_the_retry_prompt_is_small_but_still_an_rca_prompt():
    with _env(["", REPLY_RETRY]) as e:
        sr.rca(e["finding"], asker=e["asker"], file_task=e["file_task"])
        assert len(e["calls"]) == 2, "needs the retry: %d call(s)" % len(e["calls"])
        p1, p2 = e["calls"][0]["prompt"], e["calls"][1]["prompt"]
        assert len(p1) - len(p2) >= 1200, (
            "the retry only trimmed %d of %d characters. Reasoning and the answer draw on ONE "
            "num_predict (24000, scripts/session_review.py:1523), so the point of the retry is to "
            "leave the answer room: drop the RCA skill block, not a line of it."
            % (len(p1) - len(p2), len(p1)))
        assert SKILL_HEAD not in p2, (
            "the retry prompt still carries the RCA skill block: %r... -- that block is the part "
            "this finding shows is too long for one call" % SKILL_HEAD[:40])
        assert QUOTE in p2, "the retry prompt dropped the finding it is asked to RCA"
        assert KEY in p2, "the retry prompt dropped the finding key"
        for needle in ('"title"', "data-collection", "covered_by"):
            assert needle in p2, (
                "the retry is not an RCA prompt: %r is missing, so the model is no longer asked "
                "for the JSON list the answer is parsed as (the contract is in the RCA template, "
                "scripts/session_review.py:1382)" % needle)
        assert e["calls"][1]["budget"] == 24000, (
            "the retry asked for %r tokens, not 24000: cutting the OUTPUT budget shrinks the answer "
            "too, and test_session_review_rca_cause.py pins 24000 on every RCA call" % (e["calls"][1]["budget"],))
        assert e["calls"][0]["budget"] == 24000, (
            "the FIRST call's budget moved off 24000: accepted RCAs need it (the comment at "
            "scripts/session_review.py:1520-1522)")


def test_a_recovered_run_is_recorded_as_recovered():
    with _env(["", REPLY_RETRY]) as e:
        sr.rca(e["finding"], asker=e["asker"], file_task=e["file_task"])
        events = [r.get("event") for r in e["rows"]]
        assert events.count("rca-retry") == 1, (
            "the retry was not recorded in the ledger (events: %r) -- an unrecorded retry is invisible "
            "to the next reader who has to cost this path" % events)
        assert "rca-unusable" not in events, (
            "a run whose retry answered was recorded as unusable: %r" % (e["rows"],))


def test_an_empty_retry_gives_up_once_and_records_what_it_knew():
    with _env(["", ""]) as e:
        ids = sr.rca(e["finding"], asker=e["asker"], file_task=e["file_task"])
        assert ids == [], "a run that filed nothing must return no task ids: %r" % (ids,)
        assert len(e["calls"]) == 2, (
            "expected exactly ONE retry; got %d call(s) -- a blind third ask is a 9000-second slot "
            "burned on a shape that has already failed twice" % len(e["calls"]))
        assert e["minted"] == [], "a run that filed nothing minted a task: %r" % (e["minted"],)
        assert not (e["tmp"] / "rca" / (KEY + ".md")).exists(), (
            "an RCA that answered nothing wrote a report")
        un = _record(e["tmp"]).get("unusable")
        assert isinstance(un, list) and len(un) == 1, (
            "a run with one retry must record ONE unusable attempt; got %r -- MAX_UNUSABLE is 2 "
            "(scripts/session_review.py:245), so counting the retry as a second attempt files a "
            "task on every empty reply" % (un,))
        assert str(un[0].get("at", "")).strip() and str(un[0].get("why", "")).strip(), (
            "the attempt record lost its stamp or its reason: %r" % (un[0],))
        rows = [r for r in e["rows"] if r.get("event") == "rca-unusable"]
        assert len(rows) == 1, "expected one rca-unusable row, got %r" % (rows,)
        assert rows[0].get("empty_answer") is True, (
            "the give-up row records a key and a timestamp and nothing about WHY: %r -- the two rows "
            "for check-56cecdc3893f in workspace/session-reviews/ledger.jsonl say only 'no answer "
            "(timed out or empty)', which is why this task had to be re-derived by hand" % (rows[0],))
        assert rows[0].get("num_predict") == 24000, (
            "the give-up row does not record the output budget it starved on: %r" % (rows[0],))
        assert isinstance(rows[0].get("prompt_chars"), int) and rows[0]["prompt_chars"] > 0, (
            "the give-up row does not record the prompt size: %r -- 'too long a part for one call' "
            "must be answerable from the record, not by hand" % (rows[0],))


def test_an_answer_that_parses_is_asked_once():
    with _env([REPLY_FIRST, REPLY_RETRY]) as e:
        ids = sr.rca(e["finding"], asker=e["asker"], file_task=e["file_task"])
        assert len(e["calls"]) == 1, (
            "a usable first answer was re-asked (%d calls): every RCA in the harness pays a second "
            "9000-second slot for nothing, and L-114's warning is that a gate which fires twice gets "
            "disabled" % len(e["calls"]))
        assert e["minted"] and e["minted"][0][0] == FIRST_TITLE, (
            "the FIRST answer's title is the one to file: %r" % (e["minted"],))
        assert ids == ["t9999"], "a normal run must still return its task id: %r" % (ids,)
        assert not [r for r in e["rows"] if r.get("event") == "rca-retry"], (
            "a run that answered on the first call was recorded as retried: %r" % (e["rows"],))


def test_a_reply_with_no_json_list_is_not_re_asked():
    with _env(["I read the session. Here is my prose, no list.", REPLY_RETRY]) as e:
        ids = sr.rca(e["finding"], asker=e["asker"], file_task=e["file_task"])
        assert len(e["calls"]) == 1, (
            "the retry fired on a NON-empty reply (%d calls). The shape this brief fixes is the EMPTY "
            "answer; a reply with content but no JSON list is a different defect (t1416), and "
            "re-asking it buys a second 9000-second slot with the same prompt" % len(e["calls"]))
        assert ids == [], "an unusable reply files nothing: %r" % (ids,)
        un = _record(e["tmp"]).get("unusable")
        assert isinstance(un, list) and len(un) == 1, "expected one unusable attempt: %r" % (un,)
        assert "no JSON list in the answer" in str(un[0].get("why", "")), (
            "the non-empty branch's wording moved: %r -- test_session_review_rca_empty_reason.py "
            "(t1290) pins that phrase as a substring of it; append to it, do not replace it"
            % (un[0].get("why"),))


def test_the_run_wrote_nothing_under_the_real_workspace():
    live = REPO / "workspace" / "session-reviews" / "ledger.jsonl"
    before = live.stat().st_size if live.is_file() else None
    with _env(["", ""]) as e:
        sr.rca(e["finding"], asker=e["asker"], file_task=e["file_task"])
    after = live.stat().st_size if live.is_file() else None
    assert before == after, (
        "the run appended to the LIVE ledger (%s -> %s bytes): a fixture that writes into "
        "workspace/session-reviews/ledger.jsonl makes every later session review see phantom "
        "attempts" % (before, after))


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    bad = []
    for fn in fns:
        try:
            fn()
            print("PASS " + fn.__name__)
        except Exception as ex:
            bad.append(fn.__name__)
            print("FAIL %s: %s: %s" % (fn.__name__, type(ex).__name__, ex))
    print("%d/%d passed" % (len(fns) - len(bad), len(fns)))
    sys.exit(1 if bad else 0)
