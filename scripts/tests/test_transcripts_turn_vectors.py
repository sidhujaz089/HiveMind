#!/usr/bin/env python3
"""K2 (t1099): a persistent, append-only vector index of past turns.

Red on main for ONE reason: scripts/transcripts.py has no row_id / parse_at / recent_rows /
append_turns / load_vectors, so every test below fails with AttributeError. The module itself
imports fine -- it is the file this task's index is being added to.

No model, no server, no live file: every vector is hand-written, every path is a temp dir, and
recent_rows is always handed rows explicitly here -- it never reads the 39 MB
cWorkspace/transcript-index.jsonl from this suite.
"""
import json
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import transcripts  # the file the change goes into (exists on main)

# Three DISTINCT turns with real locators: two hivemind turns in one session, one console turn.
# Three DISTINCT vectors -- not one vector reused -- so a build that writes the same vector to
# every row fails (RCA t613: one repeated placeholder cannot tell values apart).
A = {"session": "abcdef01-1111-2222-3333-444455556666", "n": 4, "turn": 2,
     "role": "user", "origin": "hivemind", "at": "2026-10-12T09:15:00.000Z",
     "text": "what did we decide about routing.csv",
     "full_text": "what did we decide about routing.csv"}
B = {"session": "abcdef01-1111-2222-3333-444455556666", "n": 9, "turn": 5,
     "role": "assistant", "origin": "hivemind", "at": "2026-10-12T09:16:00.000Z",
     "text": "we deferred routing.csv; see t1042",
     "full_text": "we deferred routing.csv; see t1042"}
C = {"session": "console-thread-1", "n": 0, "turn": 0,
     "role": "user", "origin": "console", "at": "2026-10-13T01:00:00.000Z",
     "text": "where did L-114 come from",
     "full_text": "where did L-114 come from"}
ROWS = [A, B, C]
# Every value is exactly representable in float32, so a round trip is exact, not approximate.
VEC = {"abcdef01-1111-2222-3333-444455556666:4": [1.0, 0.5, -2.0],
       "abcdef01-1111-2222-3333-444455556666:9": [0.0, 1.0, 0.25],
       "console-thread-1:0": [-0.5, 2.0, 0.0]}


def _vecs():
    return [VEC[transcripts.row_id(r)] for r in ROWS]


def test_row_id_tells_the_two_turns_apart():
    assert transcripts.row_id(A) == "abcdef01-1111-2222-3333-444455556666:4"
    assert transcripts.row_id(B) == "abcdef01-1111-2222-3333-444455556666:9"
    assert transcripts.row_id(C) == "console-thread-1:0"
    ids = [transcripts.row_id(r) for r in ROWS]
    assert len(set(ids)) == 3, f"ids collide: {ids}"
    assert transcripts.row_id(B) != transcripts.row_id(A), (
        "two turns of one session share an id: the key is session and n, not session alone")


def test_parse_at_reads_the_timestamp_form_the_index_stores():
    # transcripts.py stores "at": m.get("timestamp", "") -- the Claude Code form, milliseconds
    # and a trailing Z. 2026-10-13T00:00:00Z is 1791849600 seconds since the epoch.
    assert transcripts.parse_at("2026-10-13T00:00:00.000Z") == 1791849600.0
    assert transcripts.parse_at("2026-10-13T00:00:00Z") == 1791849600.0
    assert transcripts.parse_at("") is None, "a turn with no timestamp must parse to None, not 0"
    assert transcripts.parse_at("not a date") is None, "an unparseable timestamp must not become 0"


def test_recent_rows_keeps_the_thirty_day_window_and_the_undated_turns():
    now = "2026-10-13T00:00:00.000Z"
    fresh = dict(A, at="2026-10-05T00:00:00.000Z")     # 8 days old: in
    console = dict(C, at="2026-09-20T00:00:00.000Z")   # 23 days old: in, and from Console
    old = dict(B, at="2026-08-15T00:00:00.000Z")       # 59 days old: out
    undated = dict(C, session="no-date", at="")        # no timestamp at all
    got = transcripts.recent_rows(days=30, now=now, rows=[fresh, old, console, undated])
    ids = {transcripts.row_id(r) for r in got}
    assert transcripts.row_id(fresh) in ids, "an 8-day-old turn was dropped by a 30-day window"
    assert transcripts.row_id(console) in ids, "a 23-day-old console turn was dropped"
    assert transcripts.row_id(old) not in ids, "a 59-day-old turn survived a 30-day window"
    assert transcripts.row_id(undated) in ids, (
        "a turn with no timestamp was dropped: silent data loss (never-truncate-a-record)")
    kept = {transcripts.row_id(r): r for r in got}
    r = kept[transcripts.row_id(fresh)]
    assert (r["session"], r["n"], r["turn"], r["at"], r["origin"]) == (
        fresh["session"], 4, 2, fresh["at"], "hivemind"), f"locator fields lost: {r}"
    assert r["full_text"] == fresh["full_text"], "full_text lost -- a located answer needs it"
    assert transcripts.recent_rows(days=30, now=now, rows=[]) == []


def test_append_round_trips_each_rows_own_vector():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        n = transcripts.append_turns(ROWS, _vecs(), out)
        assert n == 3, f"appended {n}, expected 3"
        store = transcripts.load_vectors(out)
        assert set(store) == set(VEC), f"keys: {sorted(store)}"
        for rid, v in VEC.items():
            assert [float(x) for x in store[rid]] == v, f"{rid}: stored {store[rid]}, expected {v}"


def test_vectors_are_binary_float32_not_json():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        transcripts.append_turns(ROWS, _vecs(), out)
        raw = (out / "vectors.f32").read_bytes()
        assert len(raw) == 3 * 3 * 4, (
            f"vectors.f32 is {len(raw)} bytes; 3 rows x 3 dims x 4 bytes = 36. Longer means the "
            "vectors are text/JSON, which is what the task rules out")
        assert struct.unpack_from("<3f", raw, 0) == (1.0, 0.5, -2.0), (
            "the first row's vector is not float32 little-endian at offset 0")
        first = json.loads((out / "meta.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert (first["session"], first["n"], first["turn"]) == (A["session"], 4, 2), first
        assert first["at"] == A["at"] and first["origin"] == "hivemind", first
        assert "full_text" not in first and "text" not in first, (
            "meta.jsonl duplicated the turn text; the text lives in transcript-index.jsonl")


def test_append_is_append_only_and_idempotent():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        transcripts.append_turns(ROWS, _vecs(), out)
        v0 = (out / "vectors.f32").read_bytes()
        m0 = (out / "meta.jsonl").read_text(encoding="utf-8")
        added = {"session": "console-thread-2", "n": 7, "turn": 7, "role": "user",
                 "origin": "console", "at": "2026-10-13T02:00:00.000Z",
                 "text": "add the reranker to the k2 measurement",
                 "full_text": "add the reranker to the k2 measurement"}
        n = transcripts.append_turns([added], [[1.0, 1.0, 2.0]], out)
        assert n == 1, f"appending one new turn wrote {n} row(s), expected 1"
        v1 = (out / "vectors.f32").read_bytes()
        m1 = (out / "meta.jsonl").read_text(encoding="utf-8")
        assert v1[:len(v0)] == v0, "existing vectors were rewritten: the store must be append-only"
        assert m1.startswith(m0), "existing meta rows were rewritten"
        assert len(m1.strip().splitlines()) == 4, "a new turn must add exactly one meta row"
        store = transcripts.load_vectors(out)
        assert [float(x) for x in store["console-thread-2:7"]] == [1.0, 1.0, 2.0]
        assert [float(x) for x in store[transcripts.row_id(A)]] == VEC[transcripts.row_id(A)], (
            "the new append changed an existing row's vector")
        n2 = transcripts.append_turns(ROWS, _vecs(), out)
        assert n2 == 0, f"re-appending the same {len(ROWS)} turns added {n2} row(s); idempotent by id"
        assert (out / "vectors.f32").read_bytes() == v1, "the idempotent re-append wrote bytes"
        assert (out / "meta.jsonl").read_text(encoding="utf-8") == m1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except Exception as e:                                        # noqa: BLE001
            failed += 1
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n  {len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
