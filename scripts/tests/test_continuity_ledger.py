#!/usr/bin/env python3
"""t1043 -- Record loop arm/fire/stop as a ledger row at the continuation endpoint.

Red on main for ONE reason: scripts/check_declared_intent.py defines no ledger writer, so no
endpoint records /loop and ScheduleWakeup arm/fire/stop state (RCA 5e6c59cc67cf, dead end D1)
and the promise "I'll work straight through each turn and restart myself between turns."
(session 745a17ba-a559-4e99-81f6-3f8ec745ad46, 2026-10-01T22:24:32Z; armed 2 h 44 m later at
01:09:01; zero fired ticks) leaves no artefact in the repository. Every test below fails on the
first missing name in the module, which IS the defect: nothing collects the state at all.

Run: python3 scripts/tests/test_continuity_ledger.py   (pytest is not installed)
"""
import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MOD_PATH = REPO / "scripts" / "check_declared_intent.py"
MODULE_NAME = "check_declared_intent"

WAKEUP = "ScheduleWakeup"
LOOP_PROMPT = ("/loop Work the backlog: highest infrastructure impact first, close with "
               "evidence, commit as you go")
INTENT_PROMPT = ("Work the backlog: highest infrastructure impact first, close with evidence, "
                 "commit as you go and I'll work straight through each turn and restart "
                 "myself between turns.")
REASON_1 = ("Next backlog pass in 20 min; also lets the next ask_q16 500 land in its new "
            "shape log.")
REASON_2 = ("Checking the t469 build after its preflight, any new t616 server-error shape, "
            "and new backlog arrivals.")
PROMISE = "I'll work straight through each turn and restart myself between turns."
LONG_PROMISE = ("I'll work straight through each turn and restart myself between turns, closing "
                "every ticket in the backlog with evidence, committing as I go, and reporting "
                "the shape of every new server error that arrives.")
LONG_INTENT = ("To keep me working through the backlog without prompting, send:\n/loop "
               + INTENT_PROMPT + "\n" + PROMISE)
NOT_CONTINUATION = "I'll check the tests."
KINDS = {"arm", "stop", "fire", "unarmed-intent", "row-unreadable"}
FIELDS = {"kind", "session", "ts", "delay_seconds", "reason", "prompt_hash", "noop"}
SESSION = "745a17ba-a559-4e99-81f6-3f8ec745ad46"
RCA_TRANSCRIPT = (Path.home() / ".claude" / "projects" / "-home-user-ai-workspace" /
                  (SESSION + ".jsonl"))
ARM_TS_1 = "2026-10-01T21:23:45.469Z"
ARM_TS_2 = "2026-10-02T01:09:01.824Z"
STOP_TS_1 = "2026-10-01T21:32:42.044Z"
STOP_TS_2 = "2026-10-02T01:17:07.750Z"
INTENT_TS = "2026-10-01T22:24:32.569Z"
ARMED_IN_WINDOW_TS = "2026-10-01T22:25:11.569Z"

FAILS = []


def _mod():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    for name in ("WAKEUP", "CONTINUATION", "prompt_hash", "read_rows", "scan", "record",
                 "ledger_main", "LEDGER", "TRANSCRIPTS"):
        assert hasattr(mod, name), (
            "scripts/check_declared_intent.py defines no %s: no collector records loop arm/"
            "fire/stop at the continuation endpoint (RCA 5e6c59cc67cf D1)" % name)
    return mod


def check(label, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "BAD", label, ("  " + detail) if detail else ""))
    if not cond:
        FAILS.append(label + (" " + detail if detail else ""))


def _assistant(ts, blocks):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "content": blocks}}


def _text_row(ts, text, role="assistant"):
    return {"type": role, "timestamp": ts,
            "message": {"role": role, "content": [{"type": "text", "text": text}]}}


def _arm(ts, prompt=LOOP_PROMPT, reason=REASON_1):
    return _assistant(ts, [{"type": "tool_use", "name": WAKEUP,
                            "input": {"delaySeconds": 1200, "reason": reason, "prompt": prompt,
                                      "noop": False}}])


def _stop(ts):
    return _assistant(ts, [{"type": "tool_use", "name": WAKEUP, "input": {"stop": True}}])


def _kind(events, kind):
    return [e for e in events if e.get("kind") == kind]


def test_the_wakeup_tool_name_is_the_real_tool_name():
    m = _mod()
    check("WAKEUP is the harness tool name, not a guess", m.WAKEUP == "ScheduleWakeup",
          repr(m.WAKEUP))


def test_prompt_hash_strips_the_loop_prefix_so_arm_fire_and_intent_are_comparable():
    m = _mod()
    h = m.prompt_hash(INTENT_PROMPT)
    check("prompt_hash is 12 hex chars", bool(re.fullmatch(r"[0-9a-f]{12}", h)), repr(h))
    check("a leading /loop is stripped before hashing",
          m.prompt_hash("/loop " + INTENT_PROMPT) == h, repr(m.prompt_hash("/loop " + INTENT_PROMPT)))
    check("None hashes as the empty prompt", m.prompt_hash(None) == m.prompt_hash(""),
          repr(m.prompt_hash(None)))
    check("a different prompt hashes differently", m.prompt_hash("/loop Close t318") != h,
          repr(m.prompt_hash("/loop Close t318")))


def test_an_arm_row_carries_the_delay_reason_prompt_hash_and_noop_read_from_the_payload():
    m = _mod()
    events = m.scan([_arm(ARM_TS_1)], SESSION)
    check("one row for one arm", len(events) == 1, str(events))
    e = events[0]
    check("every field is present", FIELDS <= set(e), str(sorted(set(e))))
    check("kind is arm", e.get("kind") == "arm", str(e.get("kind")))
    check("session is recorded", e.get("session") == SESSION, str(e.get("session")))
    check("ts is the transcript timestamp", e.get("ts") == ARM_TS_1, str(e.get("ts")))
    check("delay_seconds is the payload's 1200", e.get("delay_seconds") == 1200,
          str(e.get("delay_seconds")))
    check("reason is the payload's, verbatim", e.get("reason") == REASON_1, repr(e.get("reason")))
    check("prompt_hash is the prompt's hash", e.get("prompt_hash") == m.prompt_hash(LOOP_PROMPT),
          str(e.get("prompt_hash")))
    check("noop is read from the payload, not assumed", e.get("noop") is False, str(e.get("noop")))


def test_a_stop_is_a_stop_and_is_not_recorded_as_an_arm():
    m = _mod()
    events = m.scan([_arm(ARM_TS_1), _stop(STOP_TS_1)], SESSION)
    check("arm then stop, in row order", [e.get("kind") for e in events] == ["arm", "stop"],
          str(events))
    s = events[1]
    check("stop carries every field", FIELDS <= set(s), str(sorted(set(s))))
    check("stop ts", s.get("ts") == STOP_TS_1, str(s.get("ts")))
    check("a stop has no delay", s.get("delay_seconds") is None, str(s.get("delay_seconds")))
    check("a stop has no reason", s.get("reason") is None, str(s.get("reason")))
    check("a stop hashes the empty prompt", s.get("prompt_hash") == m.prompt_hash(None),
          str(s.get("prompt_hash")))
    check("a stop that followed an arm is not a noop", s.get("noop") is False, str(s.get("noop")))


def test_a_stop_with_no_arm_before_it_is_recorded_as_a_noop():
    m = _mod()
    events = m.scan([_stop(STOP_TS_1)], SESSION)
    check("one row", len(events) == 1, str(events))
    check("kind is stop", events[0].get("kind") == "stop", str(events[0]))
    check("a stop with nothing armed is a noop", events[0].get("noop") is True,
          str(events[0].get("noop")))


def test_a_fired_tick_is_a_user_loop_line_matched_to_the_arm_that_armed_it():
    m = _mod()
    events = m.scan([_arm(ARM_TS_1), _text_row("2026-10-01T21:43:46.000Z", LOOP_PROMPT, "user")],
                    SESSION)
    check("arm then fire, and nothing invented", [e.get("kind") for e in events] == ["arm", "fire"],
          str(events))
    f = events[1]
    check("fire carries every field", FIELDS <= set(f), str(sorted(set(f))))
    check("a fire whose prompt was armed is matched", f.get("matched") is True, str(f.get("matched")))
    check("fire and arm hash the same prompt",
          f.get("prompt_hash") == events[0].get("prompt_hash"), str(f.get("prompt_hash")))
    other = m.scan([_arm(ARM_TS_1),
                    _text_row("2026-10-01T21:43:46.000Z", "/loop Close t318 and report the shape",
                              "user")], SESSION)
    check("a fire whose prompt was never armed is not matched",
          bool(other) and other[-1].get("matched") is False, str(other))


def test_a_continuation_promise_with_no_timely_arm_is_recorded_whole():
    m = _mod()
    events = m.scan([_text_row(INTENT_TS, LONG_INTENT), _arm(ARM_TS_2, reason=REASON_2)], SESSION)
    u = _kind(events, "unarmed-intent")
    check("the 2 h 44 m gap produces at least one row", len(u) >= 1, str(events))
    check("the promise is recorded as its own row",
          len([e for e in u if e.get("sentence") == PROMISE]) == 1,
          str([e.get("sentence") for e in u]))
    check("no row is matched to an arm 2 h 44 m away",
          bool(u) and all(e.get("matched") is False for e in u),
          str([e.get("matched") for e in u]))
    p = [e for e in u if e.get("sentence") == PROMISE]
    check("the promise row carries every field", bool(p) and FIELDS <= set(p[0]),
          str(sorted(set(p[0]))) if p else "no row")
    check("the promise row carries the row's own timestamp",
          bool(p) and p[0].get("ts") == INTENT_TS, str(p[0].get("ts")) if p else "no row")
    check("the arm's prompt and the intent's prompt hash alike",
          bool(p) and p[0].get("prompt_hash") == m.prompt_hash(INTENT_PROMPT),
          str(p[0].get("prompt_hash")) if p else "no row")
    check("the sentence is recorded whole",
          bool(p) and len(p[0].get("sentence")) == len(PROMISE) and not
          str(p[0].get("sentence")).endswith("..."), repr(p[0].get("sentence")) if p else "no row")


def test_a_promise_armed_within_the_window_writes_no_unarmed_row():
    m = _mod()
    events = m.scan([_text_row(INTENT_TS, LONG_INTENT),
                     _arm(ARMED_IN_WINDOW_TS, prompt="/loop " + INTENT_PROMPT)], SESSION)
    check("an armed promise is not reported as a gap",
          [e.get("kind") for e in events] == ["arm"], str(events))


def test_an_intention_that_is_not_about_continuation_writes_no_row():
    m = _mod()
    events = m.scan([_text_row(INTENT_TS, NOT_CONTINUATION)], SESSION)
    check("a promise with no continuation vocabulary is not a row", events == [], str(events))


def test_the_long_promise_sentence_is_recorded_whole_not_cut_to_a_fixed_width():
    m = _mod()
    check("the fixture sentence is longer than the slice findings() applies",
          len(LONG_PROMISE) > 200, "len=%d" % len(LONG_PROMISE))
    events = m.scan([_text_row(INTENT_TS, LONG_PROMISE)], SESSION)
    u = [e for e in _kind(events, "unarmed-intent") if e.get("sentence") == LONG_PROMISE]
    check("the sentence is stored verbatim, not sliced", bool(u),
          repr(str(u[0].get("sentence"))[-40:]) if u else "no row")
    check("no ellipsis in a recorded sentence",
          bool(u) and not str(u[0].get("sentence")).endswith("..."),
          repr(str(u[0].get("sentence"))[-12:]) if u else "no row")


def test_a_malformed_line_becomes_a_row_carrying_the_parse_error_and_its_line_number():
    m = _mod()
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "s.jsonl"
        src.write_text(json.dumps(_stop(STOP_TS_1)) + "\n{not json\n", encoding="utf-8")
        rows = m.read_rows(src)
        check("read_rows yields the good row and the broken one", len(rows) == 2, str(len(rows)))
        check("the broken line is reported, not swallowed",
              bool(rows) and rows[-1].get("type") == "unreadable" and rows[-1].get("line") == 2,
              str(rows[-1:]))
        events = m.scan(rows, SESSION)
        check("the break becomes a row-unreadable row",
              [e.get("kind") for e in events] == ["stop", "row-unreadable"],
              str([e.get("kind") for e in events]))
        r = events[-1]
        check("the unreadable row carries every field", FIELDS <= set(r), str(sorted(set(r))))
        check("the row says which line broke", r.get("ts") == "line:2", str(r.get("ts")))
        check("the row carries the parse error", "not json" in str(r.get("reason")),
              str(r.get("reason")))
        check("a broken line invents no delay", r.get("delay_seconds") is None,
              str(r.get("delay_seconds")))


def test_record_appends_one_machine_readable_row_per_event_and_the_entry_point_writes():
    m = _mod()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        f1 = d / "a.jsonl"
        f1.write_text(json.dumps(_arm(ARM_TS_1)) + "\n", encoding="utf-8")
        f2 = d / "b.jsonl"
        f2.write_text(json.dumps(_stop(STOP_TS_1)) + "\n{not json\n", encoding="utf-8")
        out = d / "ledger.jsonl"
        rows = m.record([f1, f2], out)
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        check("one row per event, appended to the file", len(lines) == 3, str(len(lines)))
        check("record returns the rows it wrote", len(rows) == 3, str(len(rows)))
        parsed = [json.loads(l) for l in lines]
        check("every line is a machine-readable row",
              all(FIELDS <= set(p) and p.get("kind") in KINDS for p in parsed), str(parsed[:1]))
        check("each row carries the session of its own transcript",
              [p.get("session") for p in parsed] == [f1.stem, f1.stem, f2.stem],
              str([p.get("session") for p in parsed]))
        m.record([f1], out)
        grew = len(out.read_text(encoding="utf-8").strip().splitlines())
        check("the ledger is appended to, not overwritten", grew == 4, str(grew))
        out2 = d / "ledger2.jsonl"
        rc = m.ledger_main([f1, f2], out2)
        wrote = len(out2.read_text(encoding="utf-8").strip().splitlines()) if out2.is_file() else 0
        check("the entry point returns 0", rc == 0, str(rc))
        check("the entry point WRITES, it does not only print", wrote == 3, str(wrote))
        check("LEDGER is the repo ledger the RCA names",
              str(m.LEDGER).endswith("workspace/continuity-ledger.jsonl"), str(m.LEDGER))
        check("TRANSCRIPTS is where the transcripts live",
              str(m.TRANSCRIPTS).endswith(".claude/projects"), str(m.TRANSCRIPTS))


def test_the_finding_reproduces_from_the_real_transcript_that_produced_it():
    m = _mod()
    check("the RCA transcript is present", RCA_TRANSCRIPT.is_file(), str(RCA_TRANSCRIPT))
    if not RCA_TRANSCRIPT.is_file():
        return
    rows = m.read_rows(RCA_TRANSCRIPT)
    broken = len([r for r in rows if r.get("type") == "unreadable"])
    check("the transcript parses in full", broken == 0, "%d unreadable line(s)" % broken)
    events = m.scan(rows, SESSION)
    arms = _kind(events, "arm")
    check("the two real ScheduleWakeup calls are the only arms", len(arms) == 2, str(len(arms)))
    check("both arms carry delay_seconds 1200 read from the payload",
          [a.get("delay_seconds") for a in arms] == [1200, 1200],
          str([a.get("delay_seconds") for a in arms]))
    check("the two DISTINCT real reason strings are recorded verbatim",
          {a.get("reason") for a in arms} == {REASON_1, REASON_2},
          str(sorted({str(a.get("reason"))[:38] for a in arms})))
    check("noop comes from the payload: both real arms carry false",
          [a.get("noop") for a in arms] == [False, False], str([a.get("noop") for a in arms]))
    stops = sorted(str(e.get("ts")) for e in _kind(events, "stop"))
    check("the two real stops are recorded at their own timestamps",
          stops == sorted([STOP_TS_1, STOP_TS_2]), str(stops))
    check("no fired tick is invented: this transcript has zero", _kind(events, "fire") == [],
          str(len(_kind(events, "fire"))))
    check("nothing is invented: exactly 4 arm/stop/fire rows",
          len([e for e in events if e.get("kind") in ("arm", "stop", "fire")]) == 4,
          str([e.get("kind") for e in events if e.get("kind") in ("arm", "stop", "fire")]))
    p = [e for e in _kind(events, "unarmed-intent") if e.get("sentence") == PROMISE]
    check("the finding's promise is recorded, unmatched, at its own timestamp",
          len(p) == 1 and p[0].get("matched") is False and p[0].get("ts") == INTENT_TS, str(p))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except AssertionError as e:
            FAILS.append("%s: AssertionError: %s" % (t.__name__, e))
            print("  [BAD] %s raised AssertionError: %s" % (t.__name__, e))
        except Exception as e:
            FAILS.append("%s: %s: %s" % (t.__name__, type(e).__name__, e))
            print("  [BAD] %s raised %s: %s" % (t.__name__, type(e).__name__, e))
    print("FAILS: %d of %d test(s)" % (len(FAILS), len(tests)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
