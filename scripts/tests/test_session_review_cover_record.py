#!/usr/bin/env python3
"""t742 -- the cover search must be recorded ON the finding, not asserted.

Red on main for the missing change and for it alone:
  * verify_cover() (scripts/session_review.py:607-649) returns (covered_by, cover_check) and writes
    the search NOWHERE: no terms, no ids offered, no commits examined, no judge's answer, no verdict,
    no time. The record of the search is the sentence "judged none of: t032, t036, ..." (:638), which
    no command can re-run.
  * a git pickaxe timeout is swallowed at :761-762 into commits = [], so "we searched and found
    nothing" and "the search timed out" are byte-identical on disk. On the model path (:627) the same
    timeout escapes verify_cover entirely and aborts extract().
  * a judge answer of '51fcc428' -- the 8-hex shape in this finding's own cover_check -- matches none
    of the grammar's three alternatives (:635) and is read as 'none'.

No model, no live queue, no server, no process left running, no fixed /tmp path, no git against the
live tree. Every module global that touches the tree is redirected into a temp dir and the embedding
search is stubbed.
"""
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review as sr  # noqa: E402

try:                    # verify_cover imports it lazily (:618); the oracle must not need it
    import session_review_trial as _st
except Exception:       # noqa: BLE001 -- its absence is a finding, not a skip
    _st = None

FAILS = []

# The real sentence this task's finding was minted from (workspace/rca/f08e3e409144.md). Both of its
# searchable identifiers are real artefacts: check_secrets is a method of scripts/check_workspace.py.
QUOTE = "Start with the safety-critical one -- check_secrets in scripts/check_workspace.py"
CONTEXT = ("Start with the safety-critical one -- check_secrets in scripts/check_workspace.py, "
           "before the rest of the queue.")
HEX8 = "51fcc428"          # the 8-hex id shape present in this finding's cover_check
COMMIT_ROW = HEX8 + " 2026-10-07 run check_secrets on staged files"
T900 = """---
id: t900
title: Run the check_secrets scan on every staged file in check_workspace.py
status: queued
created: 2026-10-01T00:00:00Z
---

Body: the safety-critical gate for check_secrets in scripts/check_workspace.py.
"""
# t777 shares NO term with the quote, so it can only arrive by meaning -- the only case in which a
# score has to justify the offer. A word-matched task needs no score to be offered.
T777 = """---
id: t777
title: Make the staged-file scan run on every push
status: queued
created: 2026-10-01T00:00:00Z
---

Body: the token scan for files the user is about to push, so nothing ships a leaked token.
"""


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def fake_repo(root):
    """A two-task corpus, so the term search resolves without reading workspace/tasks/."""
    tasks = root / "workspace" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "t900--run-the-check-secrets-scan.md").write_text(T900, encoding="utf-8")
    (tasks / "t777--make-the-staged-file-scan-run.md").write_text(T777, encoding="utf-8")
    (root / "workspace" / "session-reviews").mkdir(parents=True)


class Patched:
    """Redirect every session_review global that touches the live tree into the temp dir."""

    _MISSING = object()

    def __init__(self, tmp, **kw):
        self.tmp, self.kw, self.saved = Path(tmp), kw, {}

    def __enter__(self):
        d = {"REPO": self.tmp,
             "FINDINGS": self.tmp / "workspace" / "session-reviews" / "findings",
             "LEDGER": self.tmp / "workspace" / "session-reviews" / "ledger.jsonl",
             "FOR_JAS": self.tmp / "workspace" / "session-reviews" / "for-jas",
             "DONE": self.tmp / "workspace" / "session-reviews" / "done",
             "open_tasks": lambda: "t900 run the check_secrets scan\nt901 x\nt902 y",
             "done_tasks": lambda days=14: "t777 make the staged-file scan run",
             "_archive_cover": lambda quote: "",
             "commit_candidates": lambda q, s, limit=3: []}
        d.update(self.kw)
        for k, v in d.items():
            self.saved[k] = getattr(sr, k, self._MISSING)
            setattr(sr, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            delattr(sr, k) if v is self._MISSING else setattr(sr, k, v)
        return False


def test_the_cover_search_is_recorded_on_the_row():
    """The search's terms, the ids it offered, the judge's whole answer: on the row."""
    r = {"kind": "deferral", "quote": QUOTE, "context": CONTEXT,
         "what": "run check_secrets on staged files"}
    covered_by, _how = sr.verify_cover(r, "2026-10-07", lambda p, why, **kw: "t900")
    check(covered_by == "t900", "the judge's pick is the answer (got %r)" % (covered_by,))
    ev = r.get("cover_evidence")
    check(isinstance(ev, dict),
          "verify_cover leaves its search on the row as a dict 'cover_evidence' (got %r)" % (ev,))
    if not isinstance(ev, dict):
        return
    terms = ev.get("terms")
    check(isinstance(terms, list) and "check_secrets" in terms,
          "the terms searched include the quote's own identifier (got %r)" % (terms,))
    check(isinstance(terms, list) and "scripts/check_workspace.py" in terms,
          "the quote's file path is a search term and is recorded (got %r)" % (terms,))
    check(bool(terms) and len(terms) <= 5,
          "the terms are the search's own bounded set, at most 5 (got %d)" % len(terms or []))
    check("t900" in (ev.get("candidates_examined") or []),
          "the ids put in front of the judge are recorded (got %r)" % (ev.get("candidates_examined"),))
    check(ev.get("judge_answer") == "t900",
          "the judge's whole answer is recorded, un-sliced (got %r)" % (ev.get("judge_answer"),))
    check(isinstance(ev.get("commits_examined"), list),
          "commits_examined is a list, so a search that examined no commit says so (got %r)"
          % (ev.get("commits_examined"),))
    check(ev.get("timeout") is False,
          "a search that did not time out records timeout: False, not an absent key (got %r)"
          % (ev.get("timeout"),))
    check(bool(str(ev.get("ts") or "").strip()), "the search is stamped with a time (got %r)" % (ev.get("ts"),))
    check("judged none of" not in json.dumps(ev), "the evidence is data, not the prose sentence it replaces")


def test_a_bare_hash_answer_is_a_commit_cover():
    """The grammar must read '51fcc428' as that commit. Today it reads it as 'none' (:635-638)."""
    real = sr.commit_candidates
    sr.commit_candidates = lambda q, s, limit=3: [COMMIT_ROW]
    try:
        r = {"kind": "unresolved", "quote": QUOTE, "context": "", "what": "run check_secrets"}
        covered_by, how = sr.verify_cover(r, "2026-10-07", lambda p, why, **kw: HEX8)
    finally:
        sr.commit_candidates = real
    check(covered_by == "fixed:" + HEX8,
          "an 8-hex answer with no 'fixed:' prefix is read as that commit, not as 'none' (got %r)"
          % (covered_by,))
    check("not among the options" not in str(how),
          "an id that WAS offered is never reported as 'not offered': %r" % (how,))


def test_a_semantic_offer_is_recorded_with_its_score():
    """The id and the score that justified it: the evidence for a cover no word explains."""
    if _st is None:
        check(False, "session_review_trial is importable -- verify_cover's meaning search imports it (:618)")
        return
    real_sem, real_cc = _st._semantic_cover, sr.commit_candidates
    _st._semantic_cover = lambda probe, as_of: (0.31, "t777")
    sr.commit_candidates = lambda q, s, limit=3: []
    try:
        r = {"kind": "deferral", "quote": QUOTE, "context": "",
             "what": "run the token scan on pushed files"}
        sr.verify_cover(r, "2026-10-07", lambda p, why, **kw: "t777")
    finally:
        _st._semantic_cover, sr.commit_candidates = real_sem, real_cc
    ev = r.get("cover_evidence")
    check(isinstance(ev, dict) and "t777" in json.dumps(ev),
          "the id the meaning search put in front of the judge is on the record (got %r)" % (ev,))
    check(isinstance(ev, dict) and "0.31" in json.dumps(ev),
          "the score that justified the offer is recorded with the id (a bare id says only 'offered')")
    check(isinstance(ev, dict) and "t900" in json.dumps(ev),
          "the word-matched candidates are recorded beside the semantic one (got %r)" % (ev,))


def test_a_pickaxe_timeout_is_a_timeout_not_an_empty_list():
    """The scripted path: a slow git must not read as 'we looked and found nothing' (:761-762)."""
    real, seen = subprocess.run, []

    class Row:
        returncode, stdout, stderr = 0, "", ""

    def boom(cmd, *a, **kw):
        seen.append(cmd)
        check(bool(kw.get("timeout")), "every git call in the cover search carries a timeout")
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 120))

    class SC:
        @staticmethod
        def dropped(ts):
            return [{"quote": QUOTE, "before": "Two things are queued.",
                     "after": "The scan is the safety-critical one.", "turn": 88, "type": "TODO"}]

        @staticmethod
        def group(rows):
            return [{"rows": rows}]

    real_sc, real_sem = sr._sc, (_st._semantic_cover if _st else None)
    subprocess.run, sr._sc = boom, (lambda: SC)
    if _st:
        _st._semantic_cover = lambda probe, as_of: None
    try:
        items = sr.scripted_items(["x"], "2026-10-07")
    finally:
        subprocess.run, sr._sc = real, real_sc
        if _st:
            _st._semantic_cover = real_sem
    check(len(items) == 1, "the dropped sentence came out as one item (got %d)" % len(items))
    check(items and items[0].get("commits") == [],
          "a search whose pickaxe timed out offers no commit: a false cover is worse than none")
    check(items and any("check_secrets" in str(t) for t in items[0].get("timeouts") or []),
          "the term that timed out is named on the item (got %r)" % (items and items[0].get("timeouts"),))
    check(items and any(str(t).startswith("TIMEOUT:") for t in items[0].get("timeouts") or []),
          "the timeout is a sentence, not an empty list (got %r)" % (items and items[0].get("timeouts"),))
    check(len(seen) >= 1, "the search ran at least one git command (%d seen)" % len(seen))


def test_the_scripted_pipeline_lands_its_search_on_the_finding():
    """scripted_items -> triage -> the finding file on disk: the answers survive the row build."""
    sess = sr.FINDINGS.parent
    rf = sess / "claude-sess-1.json"
    rf.write_text(json.dumps({"id": "sess-1", "harness": "claude", "path": "x.jsonl",
                               "ended_epoch": 1791400000}), encoding="utf-8")

    def fake_triage(items, asker, harness, sid, since, purpose=""):
        return [dict(it, verdict="missed", kind="deferral",
                     what="run check_secrets on staged files", cover="") for it in items]

    real = (getattr(sr, "turns", None), sr.scripted_items, sr.triage)
    sr.turns = lambda harness, path: []
    sr.scripted_items = lambda ts, since, as_of="": [
        {"n": 1, "quote": QUOTE, "context": CONTEXT, "type": "TODO", "turn": 88, "repeats": [],
         "near": ["t900 [queued] Run the check_secrets scan on every staged file"],
         "commits": [COMMIT_ROW], "offered": ["t900", HEX8],
         "timeouts": ["TIMEOUT: git pickaxe on scripts/check_workspace.py"]}]
    sr.triage = fake_triage
    try:
        new, covered = sr.extract_scripted(rf, asker=lambda p, why: "{}")
    finally:
        sr.turns, sr.scripted_items, sr.triage = real
    check(new == 1 and covered == 0,
          "an uncovered deferral is filed as a new finding (new=%d covered=%d)" % (new, covered))
    files = list(sr.FINDINGS.glob("*.json"))
    check(len(files) == 1, "exactly one finding file was written (found %d)" % len(files))
    if not files:
        return
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    ev = rec.get("cover_evidence")
    check(isinstance(ev, dict),
          "the finding record carries cover_evidence: terms, candidates, commits, verdict (got %r)" % (ev,))
    if not isinstance(ev, dict):
        return
    check("check_secrets" in (ev.get("terms") or []),
          "the record's terms include the quote's own identifier (got %r)" % (ev.get("terms"),))
    check(set(ev.get("candidates_examined") or []) == {"t900", HEX8},
          "the exact ids the reviewer judged are on the record (got %r)" % (ev.get("candidates_examined"),))
    check(COMMIT_ROW in (ev.get("commits_examined") or []),
          "the commit row examined is recorded whole, subject included (got %r)"
          % (ev.get("commits_examined"),))
    check(ev.get("verdict") == "none",
          "a search that judged the candidates and took none records verdict 'none' (got %r)"
          % (ev.get("verdict"),))
    check(ev.get("source") == "scripted-triage",
          "the record says which path ran the search (got %r)" % (ev.get("source"),))
    check(ev.get("population") == 2,
          "the ids searched are counted, distinct from the raw census (got %r)" % (ev.get("population"),))
    check(rec.get("candidates_consulted") == ["t900", "t901", "t902", "t777"],
          "the raw census the review consulted stays on the record (got %r)" % (rec.get("candidates_consulted"),))
    check(ev.get("timeout") is True,
          "a search whose pickaxe timed out says so on the record (got %r)" % (ev.get("timeout"),))
    check("TIMEOUT: git pickaxe on scripts/check_workspace.py" in json.dumps(ev),
          "the timeout names the term that timed out, in words (got %r)" % (ev,))
    check("judged none of" not in json.dumps(ev), "the record is data, not the prose it replaces")
    check(json.dumps(ev, sort_keys=True) == json.dumps(json.loads(json.dumps(ev)), sort_keys=True),
          "the evidence is JSON-serialisable, whole -- nothing is cut to fit")


def test_a_row_that_was_never_searched_invents_no_evidence():
    """A deferral the judge never saw gets an honest empty record, not a manufactured population."""
    req = {"id": "sess-9", "harness": "claude", "path": "z.jsonl"}
    sr._keep(Path("claude-sess-9.json"), req,
             [{"kind": "deferral", "quote": QUOTE, "context": CONTEXT,
               "where": "assistant, turn 4 of 9",
               "why_missed": "wire check_secrets into the commit gate", "covered_by": ""}],
             note_task=lambda tid, text: None, file_task=lambda t, b: "t900")
    files = list(sr.FINDINGS.glob("*.json"))
    check(len(files) == 1, "the unsearched deferral is filed (found %d)" % len(files))
    if not files:
        return
    ev = json.loads(files[0].read_text(encoding="utf-8")).get("cover_evidence")
    check(isinstance(ev, dict), "an unsearched deferral carries an honest record (got %r)" % (ev,))
    if not isinstance(ev, dict):
        return
    check(ev.get("candidates_examined") == [],
          "no candidates are invented for a search that never ran (got %r)" % (ev.get("candidates_examined"),))
    check(ev.get("population") == 0, "an unsearched row records population 0 (got %r)" % (ev.get("population"),))
    check(ev.get("verdict") == "not-judged",
          "a row no judge answered records 'not-judged', not 'none' (got %r)" % (ev.get("verdict"),))


if __name__ == "__main__":
    _fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for _f in _fns:
        print("== " + _f.__name__)
        with tempfile.TemporaryDirectory(prefix="t742-") as tmp:
            fake_repo(Path(tmp))
            try:
                with Patched(tmp):
                    _f()
            except Exception:
                FAILS.append("%s raised:\n%s" % (_f.__name__, traceback.format_exc()))
                print("  FAIL " + _f.__name__ + " raised")
    print("\n%d checks failed" % len(FAILS))
    for f in FAILS:
        print(" - " + f)
    sys.exit(1 if FAILS else 0)
