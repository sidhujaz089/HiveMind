#!/usr/bin/env python3
"""t1290: rca() must record WHY an RCA answer was empty, with the evidence a reader can act on.

scripts/session_review.py:1528 files the reason for EVERY empty answer as the one string
"no answer (timed out or empty)" -- two mutually exclusive causes in one string, the evidence for
neither. The two attempts on key 09c7f7cc27b0 are recorded 76 seconds apart
(workspace/session-reviews/failed/09c7f7cc27b0.json), so neither can have used the 9000-second
allowance rca() passes at scripts/session_review.py:1524, yet the record says "timed out".

Red on main: assertion 4 (the reason is the catch-all), 5 (no elapsed seconds), 6 (it does not say
"did not time out"), and the second suite's seconds assertion. Assertion 7 is NOT red -- today's
string already contains the word "empty" -- and is kept as a wording guard.

Everything it touches is inside a tempfile: the ledger (_log), the skill-load log (skills.LOADS, the
same redirection scripts/tests/test_session_review.py:20 uses), the failed/ and rca/ directories and
task minting are all redirected or stubbed, and the live files are asserted untouched after.

Run: python3 scripts/tests/test_session_review_rca_empty_reason.py   (pytest is not installed)
"""
import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
SR = ROOT / "scripts" / "session_review.py"
KEY = "09c7f7cc27b0"
QUOTE = "I'll make it stick past this session by recording it in my memory"
CATCH_ALL = "no answer (timed out or empty)"
SECONDS = re.compile(r"\d+(?:\.\d+)?\s*(?:s\b|sec\b|seconds\b)")
FINDING = {"kind": "deferral", "key": KEY, "quote": QUOTE, "request": "claude-84c3196c.json",
           "session": "84c3196c", "harness": "claude", "disposition": "deferred"}
LIVE = [ROOT / "workspace" / "skill-loads.jsonl", ROOT / "workspace" / "ledger.jsonl"]


def _load():
    if not SR.is_file():
        raise AssertionError(f"{SR} is missing; the oracle has no module to exercise")
    # rca() runs `import skills` at scripts/session_review.py:1516. The module's own CLI runs with
    # scripts/ as sys.path[0]; without it the oracle is red for a path bug, not for the missing change.
    d = str(SR.parent)
    if d not in sys.path:
        sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location("sr_under_test", SR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _isolate(mod, tmp, minted):
    """Off live state: no ledger append, no skill-load append, no task minted, no repo directory.

    _file_for_claude is the one _unusable falls back to: scripts/session_review.py:1529 passes
    file_task=None, and _unusable (line 270) reads (file_task or _file_for_claude)(title, body).
    Stubbing _file_task alone would let a give-up path mint a real task (taskstore.add, line 289).
    skills.LOADS must be redirected: rca() -> skills.with_skills -> skills_for appends to it at
    scripts/skills.py:404-405; the sibling session-review suites do the same redirection.
    """
    for name in ("_log", "_file_for_claude", "_file_task", "_note_task"):
        if not callable(getattr(mod, name, None)):
            raise AssertionError(f"session_review.{name} is not a callable module-level name; the "
                                 f"oracle's live-state guard cannot be installed")
    import skills
    if not isinstance(getattr(skills, "LOADS", None), Path):
        raise AssertionError("skills.LOADS is not a Path; the skill-load log cannot be redirected")
    mod._log = lambda *a, **k: None
    mod._file_for_claude = lambda title, body: (minted.append(title), "t999")[1]
    mod._file_task = lambda title, body: (minted.append(title), "t999")[1]
    mod._note_task = lambda *a, **k: "t999"
    skills.LOADS = tmp / "skill-loads.jsonl"
    # Every repo path a single unusable attempt can reach, redirected into the temp dir.
    mod.FAILED = tmp / "failed"
    mod.RCA_DIR = tmp / "rca"
    mod.LEDGER = tmp / "ledger.jsonl"
    fp = tmp / f"{KEY}.json"
    fp.write_text(json.dumps(FINDING, indent=1) + "\n", encoding="utf-8")
    return fp


def _touchable():
    return {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in LIVE if p.exists()}


def _untouched(before):
    for p, was in before.items():
        now = (p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else (None, None)
        assert now == was, f"the oracle would write the live file {p}: {was} -> {now}"


def _run(asker):
    """rca() on a temp finding, with live state guarded. Returns (ids, minted, record, tmp)."""
    mod = _load()
    minted = []
    before = _touchable()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fp = _isolate(mod, tmp, minted)
        ids = mod.rca(fp, asker=asker)
        rec = json.loads(fp.read_text(encoding="utf-8"))
        _untouched(before)
        assert not (tmp / "failed").exists(), "a single unusable attempt was given up and moved to failed/"
        assert not (tmp / "rca" / f"{KEY}.md").exists(), "an unusable RCA wrote a report"
        return ids, minted, rec, str(tmp)


def test_a_fast_empty_answer_is_recorded_as_empty_and_fast():
    ids, minted, rec, tmp = _run(lambda *a, **k: "")
    # 1. an unusable reply files nothing and mints nothing.
    assert ids == [], f"an unusable RCA filed tasks: {ids!r}"
    assert minted == [], f"an unusable RCA minted task(s): {minted!r}"
    # 2. the finding keeps the fields the dispatcher reads.
    assert rec["key"] == KEY, f"the record lost its key: {rec.get('key')!r}"
    assert rec["kind"] == "deferral", f"the record lost its kind: {rec.get('kind')!r}"
    assert rec["disposition"] == "deferred", f"the record lost its disposition: {rec.get('disposition')!r}"
    assert rec["quote"] == QUOTE, f"the record lost its quote: {rec.get('quote')!r}"
    # 3. the attempt is recorded, as one entry with a reason and a stamp.
    un = rec.get("unusable")
    assert isinstance(un, list) and len(un) == 1, f"expected one unusable entry, got: {un!r}"
    why = str(un[0].get("why", ""))
    assert why.strip(), f"the unusable entry carries no reason: {un[0]!r}"
    # 4. the catch-all is gone: one string for every cause is why this task exists.
    assert why.strip() != CATCH_ALL, f"the reason is still the catch-all: {why!r}"
    # 5. how long the attempt took, in seconds: the fact that separates 76 s from 9000 s.
    assert SECONDS.search(why), f"the reason gives no elapsed time in seconds: {why!r}"
    # 6. NEGATIVE: an answer that came back in seconds must not be called a timeout.
    low = why.lower()
    assert "did not time out" in low, f"the reason does not say the attempt stopped early: {why!r}"
    assert "timed out" not in low, f"an instant empty answer is recorded as timed out: {why!r}"
    # 7. the answer is named as empty, not merely "no answer".
    assert "empty" in low, f"the reason does not say the answer was empty: {why!r}"
    # 8. the stamp the dispatcher sorts on is still on the entry.
    assert str(un[0].get("at", "")).strip(), f"the unusable entry lost its stamp: {un[0]!r}"
    # 9. one attempt under MAX_UNUSABLE stays queued (this task's own text: 2 attempts in a row).
    assert (Path(tmp) / f"{KEY}.json").is_file(), "the finding left the queue: it must stay queued"


def test_the_no_json_list_reason_keeps_its_words_and_gains_the_elapsed_figure():
    ids, minted, rec, tmp = _run(lambda *a, **k: "I read the session. Here is my prose, no list.")
    assert ids == [], f"an unusable RCA filed tasks: {ids!r}"
    assert minted == [], f"an unusable RCA minted task(s): {minted!r}"
    un = rec.get("unusable")
    assert isinstance(un, list) and len(un) == 1, f"expected one unusable entry, got: {un!r}"
    why = str(un[0]["why"])
    # the other cause's wording is UNCHANGED (red if it moves) and gains the same evidence.
    assert "no JSON list in the answer" in why, f"the other cause's wording changed: {why!r}"
    assert SECONDS.search(why), f"the no-JSON-list reason gives no elapsed time: {why!r}"
    assert "timed out" not in why.lower(), f"an instant answer is recorded as timed out: {why!r}"


if __name__ == "__main__":
    _tests = [(k, v) for k, v in sorted(globals().items())
              if k.startswith("test_") and callable(v)]
    _failed = []
    for _name, _fn in _tests:
        try:
            _fn()
            print(f"PASS {_name}")
        except AssertionError as _e:
            _failed.append(_name)
            print(f"FAIL {_name}: {_e}")
    print(f"{len(_tests) - len(_failed)}/{len(_tests)} passed")
    sys.exit(1 if _failed else 0)
