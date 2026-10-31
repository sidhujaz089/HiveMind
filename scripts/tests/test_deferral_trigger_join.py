#!/usr/bin/env python3
"""t1390 item 2: census deferrals whose trigger is satisfied in a session other than the
promising one (workspace/rca/a5d9ea5606c5.md, dead end D3).

The deferral that minted this task said "If you check the connector's issue-creation ability,
I'll wire the inbox convention to match." That check happened 5h22m later in a DIFFERENT session,
and nothing on this box joins a deferral to the turn that satisfied it -- so the population of
that shape is unmeasured.

Pins scripts/session_review.py, next to the deferral ledger it joins (deferral_rows,
scripts/session_review.py:1205):

  TRIGGER_JOIN_LEDGER           default output: workspace/session-reviews/findings/trigger-join.jsonl
  _transcript_turns(path)         -> [{"ts","role","text"}] for a .jsonl transcript's user turns
  join_deferral_triggers(findings_dir, transcripts_root, out_path) -> int  rows appended

A row is: key, quote, trigger_phrase, satisfying_session, ts.

Red on main: scripts/session_review.py defines none of those three names.
Live state is never touched: findings dir, transcripts root and output path are all injected into
a temp dir; no subprocess, no network, no model call.
"""
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "session_review.py"
NEW_NAMES = ("TRIGGER_JOIN_LEDGER", "_transcript_turns", "join_deferral_triggers")
ROW_FIELDS = ("key", "quote", "trigger_phrase", "satisfying_session", "ts")
PHRASE = "check the connector's issue-creation ability"
QUOTE_A5D9 = "If you check the connector's issue-creation ability, I'll wire the inbox convention to match."
PROMISE_A5D9 = "2026-08-18T21:26:18Z"
FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def load():
    if not MODULE_PATH.is_file():
        raise AssertionError("no module at " + str(MODULE_PATH) + " -- the file the join belongs in is missing")
    spec = importlib.util.spec_from_file_location("session_review_t1390", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot import " + str(MODULE_PATH) + " as a module")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def finding(key, quote, disposition, ts=None, phrase=None):
    rec = {"key": key, "quote": quote,
           "why_missed": "never mentioned again; no covering task found",
           "disposition": disposition, "session": "session-that-promised"}
    if phrase is not None:
        rec["trigger_phrase"] = phrase
    if ts is not None:
        rec["ts"] = ts
    else:
        rec["found"] = "2026-09-01T10:00:00Z"
    return rec


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def read_rows(out):
    if not out.is_file():
        return []
    return [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_the_join_names_exist():
    mod = load()
    for name in NEW_NAMES:
        check(hasattr(mod, name),
              "scripts/session_review.py defines no " + name + ": the trigger-join census has no "
              "endpoint (t1390 item 2, workspace/rca/a5d9ea5606c5.md dead end D3)")


def test_ledger_constant_names_the_artifact_the_rca_names():
    mod = load()
    if not hasattr(mod, "TRIGGER_JOIN_LEDGER"):
        return
    got = str(mod.TRIGGER_JOIN_LEDGER).replace("\\", "/")
    check(got.endswith("workspace/session-reviews/findings/trigger-join.jsonl"),
          "TRIGGER_JOIN_LEDGER is " + got + "; item 2 names the artifact "
          "workspace/session-reviews/findings/trigger-join.jsonl")


def test_transcript_reader_returns_user_turns_only():
    mod = load()
    if not hasattr(mod, "_transcript_turns"):
        return
    root = Path(tempfile.mkdtemp())
    try:
        p = root / "s1.jsonl"
        lines = [
            json.dumps({"type": "user", "timestamp": "2026-10-02T18:37:54Z", "sessionId": "s1",
                        "message": {"role": "user",
                                    "content": "something is wrong with my computer"}}),
            json.dumps({"role": "user", "ts": "2026-10-02T19:00:00Z", "content": "second shape"}),
            json.dumps({"type": "assistant", "timestamp": "2026-10-02T19:01:00Z",
                        "message": {"role": "assistant", "content": "an assistant turn is not a trigger"}}),
            json.dumps({"type": "user", "timestamp": "2026-10-02T19:02:00Z",
                        "message": {"role": "user",
                                    "content": [{"type": "text", "text": "list content"}]}}),
            '{"type": "user", "tim',
        ]
        p.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
        turns = mod._transcript_turns(p)
        check(isinstance(turns, list),
              "_transcript_turns returned " + type(turns).__name__ + ", expected a list")
        texts = sorted(str(t.get("text", "")) for t in turns)
        want = sorted(["something is wrong with my computer", "second shape", "list content"])
        check(texts == want,
              "_transcript_turns returned texts " + repr(texts)[:300] + "; expected the 3 user turns "
              "(both role shapes, list content included) and no assistant turn")
        check(all(t.get("role") == "user" for t in turns),
              "role is not normalised to 'user': " + repr([t.get("role") for t in turns])[:200])
        got = sorted(str(t.get("ts", "")) for t in turns)
        want_ts = sorted(["2026-10-02T18:37:54Z", "2026-10-02T19:00:00Z", "2026-10-02T19:02:00Z"])
        check(got == want_ts, "ts not read from timestamp/ts: " + repr(got)[:200])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_join_records_the_satisfying_session_after_the_promise():
    mod = load()
    if not hasattr(mod, "join_deferral_triggers"):
        return
    root = Path(tempfile.mkdtemp())
    try:
        findings = root / "findings"
        nested = findings / "done"
        nested.mkdir(parents=True)
        transcripts = root / "transcripts"
        transcripts.mkdir()
        (findings / "a5d9ea5606c5.json").write_text(
            json.dumps(finding("a5d9ea5606c5", QUOTE_A5D9, "deferred", PROMISE_A5D9, PHRASE)) + "\n",
            encoding="utf-8")
        (nested / "b1nested.json").write_text(
            json.dumps(finding("b1nested", "same trigger, recorded under findings/done/",
                               "deferred", None, PHRASE)) + "\n", encoding="utf-8")
        (nested / "c1.json").write_text(
            json.dumps(finding("c1", "a covered finding is not the population", "covered",
                               PROMISE_A5D9, PHRASE)) + "\n", encoding="utf-8")
        (nested / "d1.json").write_text(
            json.dumps(finding("d1", "no left key: this record has no trigger phrase",
                               "deferred")) + "\n", encoding="utf-8")
        (nested / "e1.json").write_text(
            json.dumps(finding("e1", "a one-word trigger matches any sentence", "deferred",
                               PROMISE_A5D9, "ok")) + "\n", encoding="utf-8")
        write_jsonl(transcripts / "ef02843a.jsonl", [
            {"type": "user", "timestamp": "2026-08-18T21:00:00Z", "sessionId": "ef02843a",
             "message": {"role": "user",
                         "content": "I will check the connector's issue-creation ability later."}},
            {"type": "user", "timestamp": "2026-08-19T02:48:33Z", "sessionId": "ef02843a",
             "message": {"role": "user",
                         "content": "I checked the connector's issue-creation ability and it works."}},
            {"type": "assistant", "timestamp": "2026-08-19T03:00:00Z", "sessionId": "ef02843a",
             "message": {"role": "assistant",
                         "content": "I checked the connector's issue-creation ability."}},
        ])
        write_jsonl(transcripts / "77ab12.jsonl", [
            {"role": "user", "ts": "2026-09-03T05:15:00Z",
             "content": "checked the connector's issue-creation ability -- works"},
        ])
        write_jsonl(transcripts / "equal999.jsonl", [
            {"type": "user", "timestamp": PROMISE_A5D9, "content": PHRASE},
        ])
        write_jsonl(transcripts / "nrmss01.jsonl", [
            {"type": "user", "timestamp": "2026-09-04T09:00:00Z",
             "content": "issue creation is not wired"},
        ])
        out = root / "trigger-join.jsonl"
        n = mod.join_deferral_triggers(findings, transcripts, out)
        rows = read_rows(out)
        check(n == 3, "join_deferral_triggers returned " + repr(n) + ", expected 3 rows "
              "(a5d9ea5606c5 x ef02843a, a5d9ea5606c5 x 77ab12, b1nested x 77ab12)")
        check(len(rows) == 3,
              "the ledger holds " + str(len(rows)) + " rows, expected 3: " + repr(rows)[:500])
        pairs = sorted((str(r.get("key")), str(r.get("satisfying_session"))) for r in rows)
        want = sorted([("a5d9ea5606c5", "ef02843a"), ("a5d9ea5606c5", "77ab12"),
                       ("b1nested", "77ab12")])
        check(pairs == want, "joined pairs are " + repr(pairs) + ", expected " + repr(want)
              + ": a satisfying turn must be LATER than the promise's turn, and the population is "
                "every finding with disposition=='deferred' anywhere under the findings dir "
                "(today every real finding lives in findings/done/)")
        for r in rows:
            check(tuple(sorted(r)) == tuple(sorted(ROW_FIELDS)),
                  "row fields " + repr(sorted(r)) + ", expected exactly " + repr(sorted(ROW_FIELDS)))
            check(r.get("trigger_phrase") == PHRASE,
                  "trigger_phrase is " + repr(r.get("trigger_phrase"))
                  + ", expected the promise's trigger phrase verbatim")
            check(r.get("key") not in ("c1", "d1", "e1"),
                  "joined a finding outside the population: " + repr(r)[:250]
                  + " -- 'covered' is not 'deferred'; a finding with no trigger phrase has no left "
                    "key (rca D3) and matching its why_missed prose invents one; a one-word "
                    "trigger matches any sentence")
            check(r.get("satisfying_session") not in ("asst9999", "equal999", "nrmss01", "s1"),
                  "joined a non-user turn, a turn at the promise's own timestamp, a near-miss "
                  "phrase, or the reader's own fixture: " + repr(r)[:250])
            if r.get("key") == "a5d9ea5606c5" and r.get("satisfying_session") == "ef02843a":
                check(r.get("ts") == "2026-08-19T02:48:33Z",
                      "ts is " + repr(r.get("ts")) + "; expected the satisfying turn's own ts "
                      "(2026-08-19T02:48:33Z), not the promise's turn and not the earlier "
                      "matching turn at 2026-08-18T21:00:00Z")
            if r.get("key") == "a5d9ea5606c5":
                check(r.get("quote") == QUOTE_A5D9,
                      "quote is " + repr(r.get("quote"))[:160] + ", expected the deferral's own quote")
            if r.get("key") == "b1nested":
                check(r.get("ts") == "2026-09-03T05:15:00Z",
                      "b1nested carries no ts field, so its promise time is its found "
                      "(2026-09-01T10:00:00Z); it joined at " + repr(r.get("ts"))
                      + " -- 2026-08-19T02:48:33Z is earlier than that and must not count")
        n2 = mod.join_deferral_triggers(findings, transcripts, out)
        check(n2 == 3, "second call returned " + repr(n2) + ", expected 3")
        check(len(read_rows(out)) == 6,
              "a re-run left " + str(len(read_rows(out))) + " rows; a census is durable evidence, "
              "so a pass appends to the ledger rather than rewriting it")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_deferral_with_no_trigger_phrase_yields_no_rows():
    mod = load()
    if not hasattr(mod, "join_deferral_triggers"):
        return
    root = Path(tempfile.mkdtemp())
    try:
        findings = root / "findings"
        findings.mkdir(parents=True)
        transcripts = root / "transcripts"
        transcripts.mkdir()
        (findings / "d1.json").write_text(
            json.dumps(finding("d1", "no left key", "deferred")) + "\n", encoding="utf-8")
        write_jsonl(transcripts / "x1.jsonl", [
            {"type": "user", "timestamp": "2026-09-04T09:00:00Z",
             "content": "never mentioned again; no covering task found"},
        ])
        out = root / "trigger-join.jsonl"
        n = mod.join_deferral_triggers(findings, transcripts, out)
        check(n == 0, "join returned " + repr(n) + " for a deferral with no trigger phrase; "
              "expected 0 -- matching the why_missed prose invents a left key (rca D3)")
        check(read_rows(out) == [],
              "rows were written for a trigger-less deferral: " + repr(read_rows(out))[:250])
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                FAILS.append(name + " raised " + type(e).__name__ + ": " + str(e))
    for f in FAILS:
        print("FAIL: " + f)
    print(str(len(FAILS)) + " failure(s)")
    sys.exit(1 if FAILS else 0)
