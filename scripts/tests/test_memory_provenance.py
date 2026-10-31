#!/usr/bin/env python3
"""t792 oracle: scripts/memory.py's evaluate() must REFUSE an eval case the implementer can
approve, and the refusal must COST THE RUN.

Red on main for the missing change, verified 2026-10-10 by reading evaluate() in full: it reads
each case's query, want and channel only, never the provenance fields the case shape carries
(authored_by, approved_by, authored_from_sha), and the strings SELF_APPROVED and NO_PROVENANCE do
not appear in the file. A case authored by the implementer of the ranking function, authored from
a tree that already contains that code, scores exactly like a blind one and the process exits 0.
That is the session RCA verbatim: 'My eval in memory.py has three cases I invented after building
it.'

Hermetic by rule: the store, the index and the case file all live inside a
tempfile.TemporaryDirectory; memory.embed is replaced per test by a fake that returns empty
vectors, so no model, no network, no process, and nothing under workspace/ is touched. evaluate()
is called with no arguments, exactly as main() calls it.

The control test (a file with full provenance, authored_by != approved_by) passes on main and
must pass after the change: it proves the suite is not self-breaking, and it is the positive
control an implementation that always refuses fails.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import memory  # scripts/memory.py

PROVENANCE = ("authored_by", "approved_by", "authored_from_sha")

# Records chosen so keyword_rank's top-1 is deterministic without a model: each query shares
# content words with exactly one fact.
R1 = {"id": "m9101", "fact": "the ledger writes a row on entry so a dead process leaves evidence",
      "tags": ["ledger"]}
R2 = {"id": "m9102", "fact": "signature_gate blocks fixed-width slices stored into a record",
      "tags": ["signature_gate"]}

# Full, independent provenance: author is not the approver, and the case is anchored to a sha.
BLIND = {"query": "ledger row written on entry", "want": "m9101", "channel": "keyword",
         "why": "fixture: shares three content words with m9101 and none with m9102",
         "authored_by": "t792-blind-author", "approved_by": "claude-review",
         "authored_from_sha": "0" * 40}
BLIND2 = {"query": "signature_gate slices refused", "want": "m9102", "channel": "keyword",
          "why": "fixture: signature_gate and slices appear only in m9102",
          "authored_by": "t792-blind-author", "approved_by": "claude-review",
          "authored_from_sha": "0" * 40}


def _dead_embed(texts, timeout=120, is_query=False):
    """No model, no network. Empty vectors make vector_rank return [], and evaluate then scores
    the hybrid from the keyword list -- which is all these keyword-channel cases need."""
    return [[] for _ in texts]


def run(cases):
    """Point memory at a temp store/index/case-file, swap embed, call evaluate() with no
    arguments, capture stdout, restore every global. Returns (rc, printed output)."""
    saved = (memory.STORE, memory.INDEX, memory.EVAL_FILE, memory.embed)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = root / "memory.jsonl"
        store.write_text("".join(json.dumps(r) + "\n" for r in (R1, R2)), encoding="utf-8")
        casesf = root / "eval-cases.jsonl"
        casesf.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
        memory.STORE = store
        memory.INDEX = root / "index.json"
        memory.EVAL_FILE = casesf
        memory.embed = _dead_embed
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = memory.evaluate()
        finally:
            (memory.STORE, memory.INDEX, memory.EVAL_FILE, memory.embed) = saved
    return rc, buf.getvalue()


def test_a_clean_file_still_scores_every_case():
    """GREEN ON MAIN -- the control. Full provenance, author != approver: both cases are accepted,
    both are scored, rc 0, and no refusal text appears. The gate must not shrink a clean file's
    denominator: an implementation that refuses every case fails here."""
    rc, out = run([BLIND, BLIND2])
    assert rc == 0, f"a file with independent provenance must pass\n{out}"
    assert "keyword=2/2" in out, f"both accepted cases must be scored\n{out}"
    assert "SELF_APPROVED" not in out, out
    assert "NO_PROVENANCE" not in out, out


def test_a_self_approved_case_is_refused_and_names_the_name():
    """SELF_APPROVED: authored_by == approved_by is not independence -- the t792 finding, 'cases I
    invented after building it'. Refused with rc 1 even though m9101's keyword top-1 IS correct,
    and the line names the name doing both jobs plus the case's query, so a reader can act."""
    rc, out = run([dict(BLIND, authored_by="q16", approved_by="q16"), BLIND2])
    assert rc == 1, f"a case the implementer can approve must cost the run\n{out}"
    assert "SELF_APPROVED" in out, f"the report must say SELF_APPROVED\n{out}"
    assert "q16" in out, f"the refusal must name the author==approver value\n{out}"
    assert BLIND["query"] in out, f"the refusal must name the case\n{out}"


def test_a_blank_approver_is_self_approved():
    """authored_by='q16', approved_by='   ' is the same defect in a new coat: strip() before
    comparing, so a blank approver cannot pass as an independent one. The brief's author-only
    carve-out is a loophole a build can walk through, so the oracle refuses it as SELF_APPROVED."""
    rc, out = run([dict(BLIND, authored_by="q16", approved_by="   "), BLIND2])
    assert rc == 1, f"a blank approved_by is not a vouch\n{out}"
    assert "SELF_APPROVED" in out, f"blank==blank is self-approval, reported as such\n{out}"


def test_the_refused_case_is_not_counted_as_a_scored_case():
    """The number is the finding. The rejected case must leave the denominator: the one accepted
    case scores 1/1. An implementation that scores it and merely logs a note prints 1/2 and
    fails -- the score line is a record, and a record that counts an unvouched case is the defect.
    The accepted case is still ranked and printed, so the measurement is not lost."""
    rc, out = run([dict(BLIND, authored_by="q16", approved_by="q16"), BLIND2])
    assert rc == 1, out
    assert "keyword=1/1" in out, \
        f"the one accepted case must be the only case scored (keyword=1/1)\n{out}"
    assert "keyword=1/2" not in out, \
        f"the self-approved case must not be counted in the total\n{out}"
    assert "hybrid=1/1" in out, f"hybrid must drop the rejected case too\n{out}"
    assert BLIND2["query"] in out, f"the accepted case must still be ranked and printed\n{out}"


def test_a_self_approved_case_is_refused_even_though_its_channel_wins():
    """The heart of the finding: m9101's keyword top-1 IS correct, so a build that scores the
    case and logs a note leaves rc == 0. Self-approval must cost the run even when the case
    scores perfectly -- rc 1 is the whole content of this task."""
    rc, out = run([dict(BLIND, authored_by="q16", approved_by="q16"), BLIND2])
    assert rc == 1, \
        f"a case the implementer can approve must cost the run even when it scores\n{out}"


def test_missing_provenance_is_refused_and_names_the_absent_field():
    """Each of the three provenance fields, dropped in turn: the report must name THAT field, so
    a reader can fix the row. One NO_PROVENANCE per case, not one per field -- a case is refused
    once."""
    for missing in PROVENANCE:
        c = {k: v for k, v in BLIND.items() if k != missing}
        rc, out = run([c, BLIND2])
        assert rc == 1, f"a case with no {missing} has no provenance to vouch for it\n{out}"
        assert "NO_PROVENANCE" in out, \
            f"a missing {missing} must be reported as NO_PROVENANCE\n{out}"
        assert missing in out, f"the report must name the absent field ({missing})\n{out}"
        assert out.count("NO_PROVENANCE") == 1, \
            f"one NO_PROVENANCE for the one case missing {missing}\n{out}"


def test_a_blank_provenance_field_is_a_missing_field():
    """authored_by='   ' is an anonymous case. Pins that the check is .strip(), not presence of
    the key -- a mint that writes empty strings would otherwise pass."""
    for field in PROVENANCE:
        rc, out = run([dict(BLIND, **{field: "   "}), BLIND2])
        assert rc == 1, f"{field}='   ' is an anonymous case\n{out}"
        assert "NO_PROVENANCE" in out, f"blank {field} must be NO_PROVENANCE\n{out}"
        assert field in out, f"the report must name the blank field ({field})\n{out}"


def test_the_gate_is_per_case_not_per_file():
    """One clean case plus one anonymous case: the clean case is still measured, the anonymous
    one is refused by name. This is what a file-wide short-circuit cannot pass."""
    bare = {k: BLIND2[k] for k in ("query", "want", "channel")}
    rc, out = run([BLIND, bare])
    assert rc == 1, out
    assert "NO_PROVENANCE" in out, out
    assert BLIND2["query"] in out, f"the anonymous case must be named\n{out}"
    assert "keyword=1/1" in out, f"the clean case must be scored on its own\n{out}"


def test_a_malformed_case_is_refused_not_a_traceback():
    """The gate runs over every line of a jsonl file a human may have hand-edited. A non-dict
    row, and a row whose keys are not strings, must produce a report line -- never an exception
    that replaces the measured run. A swallowed exception is forbidden: the row is REPORTED."""
    rc, out = run([BLIND, [1, 2, 3], BLIND2])
    assert rc == 1, f"a row that carries no provenance must cost the run\n{out}"
    assert "NO_PROVENANCE" in out, f"a non-dict case is an anonymous case\n{out}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}\n     {e}")
        except Exception as e:                                   # noqa: BLE001 -- a crash is a FAIL, with its reason
            failed += 1
            print(f"FAIL {fn.__name__} -- CRASH {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
