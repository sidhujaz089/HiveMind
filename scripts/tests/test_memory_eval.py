#!/usr/bin/env python3
"""t266 oracle: memory.py evaluate() must JUDGE, not just print three rankings.

Red today because scripts/memory.py (read 2026-09-28) has no EVAL_FILE constant,
no load_cases(), and no DANGLING/UNEXAMINED/VACUOUS/MISS/UNJUDGED gates: evaluate()
(memory.py:284-313) hits the 'judged == 0' path and returns 1 with
'no eval case has a ground-truth answer' -- it can never score a channel.

Hermetic by rule: temp dirs and fakes only. workspace/memory.jsonl, the index
file and Ollama are never touched. memory.embed is monkeypatched per test;
the fake is the test's instrument (a deterministic bag-of-words space, or an
explicit vector table). The quality of the real embedding model is a live
measurement by design and is deliberately NOT pinned here.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import memory  # scripts/memory.py

N_SHARED_MAX = 2  # must equal the VACUOUS threshold the implementation pins

# Fixture records (facts chosen so no fact contains 'indentation' or 'pgrep';
# 'apply_ops' is a single token in the keyword class [a-z0-9_.-]+).
M9001 = {"id": "m9001", "fact": "Line-range edits replace whole lines, so the new text must carry the surrounding block's leading whitespace", "tags": ["whitespace", "edit"]}
M9002 = {"id": "m9002", "fact": "apply_ops is the internal name of the patch applier", "tags": ["apply_ops"]}
M9003 = {"id": "m9003", "fact": "An anchored replace must match exactly once in the file or the whole patch is refused", "tags": ["edit"]}
M9004 = {"id": "m9004", "fact": "A completely different record about backups", "tags": ["backup"]}

CASE_APPLYOPS = {"query": "apply_ops", "want": "m9002", "channel": "keyword", "why": "exact identifier; no other record contains the token", "approved_by": "oracle-fixture"}
CASE_INDENT   = {"query": "indentation", "want": "m9001", "channel": "vector", "why": "paraphrase of 'whitespace'; keyword returns nothing for it", "approved_by": "oracle-fixture"}
CASE_ANCHORED = {"query": "anchored replace must match exactly once", "want": "m9003", "channel": "keyword", "why": "identifier-ish phrase unique to m9003", "approved_by": "oracle-fixture"}


def _bag(text):
    """Deterministic 16-dim bag-of-words vector in memory.tokens' vocabulary,
    with one engineered synonym so the vector channel can win a paraphrase case."""
    # FIXED 2026-09-30: this hashed words into 16 buckets by sum(ord)%16, and 'internal' and
    # 'applier' (m9002) collided with 'whitespace', so the vector case could not pass under ANY
    # correct ranking. 1024 md5 buckets: no collision among this file's fixture words (checked).
    import hashlib
    v = [0.0] * 1024
    for w in memory.tokens(text):
        w = {"indentation": "whitespace"}.get(w, w)
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 1024] += 1.0
    return v


def _fake_bag_embed(texts, timeout=120, is_query=False):
    return [_bag(t) for t in texts]


def run_evaluate(records, cases, embed=None):
    """Point memory at a temp store/index/cases-file, swap embed, call evaluate(),
    capture stdout, and restore all four globals."""
    saved = (memory.STORE, memory.INDEX, memory.EVAL_FILE, memory.embed)
    embed = embed if embed is not None else _fake_bag_embed
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = root / "memory.jsonl"
        store.write_text("".join(json.dumps(r) + "\n" for r in records))
        casesf = root / "memory-eval.jsonl"
        casesf.write_text("".join(json.dumps(c) + "\n" for c in cases))
        memory.STORE = store
        memory.INDEX = root / "index.json"  # fresh: build_index must build via fake embed
        memory.EVAL_FILE = casesf
        memory.embed = embed
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = memory.evaluate()
        finally:
            (memory.STORE, memory.INDEX, memory.EVAL_FILE, memory.embed) = saved
    return rc, buf.getvalue()


def test_judge_happy_path_scores_and_returns_zero():
    """All gates pass, both designated channels win: rc 0 and a SCORE line."""
    rc, out = run_evaluate([M9001, M9002, M9003], [CASE_APPLYOPS, CASE_INDENT, CASE_ANCHORED])
    assert rc == 0, f"expected 0, got {rc}\n{out}"
    assert "SCORE" in out
    assert "keyword=2/2" in out, out  # apply_ops->m9002 and anchored->m9003, keyword top-1
    assert "vector=1/1" in out, out   # indentation->m9001 via the fake's synonym
    assert "hybrid=" in out, out     # hybrid is REPORTED, not asserted, per case


def test_keyword_channel_wins_the_identifier_case_directly():
    """No model involved: keyword_rank on the fixture puts m9002 first for apply_ops."""
    ranked = memory.keyword_rank("apply_ops", [M9001, M9002, M9003])
    assert ranked and ranked[0]["id"] == "m9002", ranked


def test_coverage_hole_fails_and_names_the_record():
    """A store record named by no case is a FINDING: fail, name the id."""
    rc, out = run_evaluate([M9001, M9002, M9003, M9004], [CASE_APPLYOPS, CASE_INDENT, CASE_ANCHORED])
    assert rc == 1
    assert "UNEXAMINED" in out and "m9004" in out, out


def test_dangling_want_fails_and_names_the_id():
    """A want naming a deleted record must FAIL, not be skipped."""
    bad = dict(CASE_ANCHORED, want="m9999", query="ghost record query")
    rc, out = run_evaluate([M9001, M9002, M9003], [CASE_APPLYOPS, CASE_INDENT, bad])
    assert rc == 1
    assert "DANGLING" in out and "m9999" in out, out


def test_vacuity_gate_fails_a_copied_query():
    """A query that copies its record's text shares more than N content words: fail."""
    copied = {"query": M9001["fact"], "want": "m9001", "channel": "vector", "why": "vacuity fixture: verbatim copy", "approved_by": "oracle-fixture"}
    rc, out = run_evaluate([M9001, M9002, M9003], [CASE_APPLYOPS, copied, CASE_ANCHORED])
    assert rc == 1
    assert "VACUOUS" in out, out


def test_designated_channel_miss_fails_and_names_the_case():
    """Want exists but the designated channel ranks something else first: a MISS, rc 1."""
    flipped = dict(CASE_INDENT, want="m9003")  # vector's top-1 is m9001, not m9003
    rc, out = run_evaluate([M9001, M9002, M9003], [CASE_APPLYOPS, flipped, CASE_ANCHORED])
    assert rc == 1
    assert "MISS" in out, out
    assert "indentation" in out, out


def test_unjudged_vector_case_fails_when_embeddings_absent():
    """No embeddings: a vector-designated case is UNJUDGED, reported, rc 1 -- never dropped."""
    def dead_embed(texts, timeout=120, is_query=False):
        return [[] for _ in texts]  # empty vectors: cosines all 0, vector_rank returns []
    rc, out = run_evaluate([M9001, M9002, M9003], [CASE_APPLYOPS, CASE_INDENT, CASE_ANCHORED], embed=dead_embed)
    assert rc == 1
    assert "UNJUDGED" in out, out
    assert "indentation" in out, out


def test_hybrid_worse_than_a_channel_is_visible_in_the_score():
    """The guard against 'a hybrid worse than both still passes': the exit code is
    judged on designated channels, and the hybrid score must show it lost.

    Explicit fake vectors (the test's instrument): query [1,0]; mH [0.9,0.1]
    (cosine 0.9939) vs mD [0.1,0.9] (cosine 0.1104) -> vector=[mH,mD].
    Keyword token zeta9x only in mD's fact -> keyword=[mD].
    rrf tie (1/61+1/62 either way) resolves by stable sort to [mD,mH]:
    hybrid top-1 = mD != want mH. hybrid=1/2; designated channels both win.
    """
    M_H = {"id": "mH", "fact": "a record about zeta-less operations", "tags": []}
    M_D = {"id": "mD", "fact": "the zeta9x flag is set by the caller", "tags": []}
    case_vec = {"query": "zeta9x semantics", "want": "mH", "channel": "vector", "why": "fixture", "approved_by": "oracle-fixture"}
    case_kw = {"query": "zeta9x flag", "want": "mD", "channel": "keyword", "why": "fixture", "approved_by": "oracle-fixture"}

    def table_embed(texts, timeout=120, is_query=False):
        table = {"zeta9x semantics": [1.0, 0.0],
                 M_H["fact"]: [0.9, 0.1],
                 M_D["fact"]: [0.1, 0.9]}
        return [table.get(t, [0.0, 0.0]) for t in texts]

    rc, out = run_evaluate([M_H, M_D], [case_vec, case_kw], embed=table_embed)
    assert rc == 0, f"designated channels both win, so rc must be 0\n{out}"
    assert "hybrid=1/2" in out, out  # hybrid lost the vector case; the score says so


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
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
