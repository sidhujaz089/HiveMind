#!/usr/bin/env python3
"""t716: where do the larger Qwen3 embedders beat the live 0.6b, per query?

Run: python3 scripts/tests/test_embed_eval_breakdown.py

Red on main for one stated reason: workspace/embed-eval/embed_eval.py has no
`breakdown` function. Read 2026-10-11: it defines load, ranks, score, bm25, qwen,
gemma, main -- and no breakdown. It scores every run as one aggregate
(score() at embed_eval.py:41-44) and throws the per-query ranks away (qwen()
returns them at embed_eval.py:75; main only puts score() output into `res` at
embed_eval.py:102-103). So the question Jas asked on 2026-10-07 -- where are 4b
and 8b stronger, by document length and by failure case -- has no answer on disk.

No model calls, no Ollama, no server, no files written, no processes.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    'embed_eval', str(ROOT / 'workspace' / 'embed-eval' / 'embed_eval.py'))
assert _SPEC is not None and _SPEC.loader is not None, (
    'embed_eval.py is not importable at workspace/embed-eval/embed_eval.py')
embed_eval = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(embed_eval)

# Six queries and six docs, standing in for the 155 in lesson-queries.jsonl.
# Titles, queries and bodies are all DISTINCT strings: one repeated placeholder
# would let an implementation that ignores which field it reads pass.
QUERIES = [
    'why was the gate bypassed within a week',
    'what made the ledger lose a torn final line',
    'where does the truncation detector have a blind spot',
    'which audit cannot see the script that made the measurement',
    'why a handover is not a sustainment fixture',
    'what happens when two dispatches share one Ollama',
]
TITLES = ['L-001', 'L-014', 'L-046', 'L-114', 'L-150', 'L-235']
SHORT = 's' * 100    # len 100 -> bucket '0-200'
MID = 'm' * 400      # len 400 -> bucket '201-600'
LONG = 'l' * 900     # len 900 -> bucket '601+'
BODIES = [SHORT, SHORT, MID, MID, LONG, LONG]
ROWS = [{'title': t, 'query': q, 'body': b} for t, q, b in zip(TITLES, QUERIES, BODIES)]

# Real model names from workspace/embed-eval/RESULTS.md:55-57, not placeholders.
M06 = 'qwen3-embedding:0.6b'
M4 = 'qwen3-embedding:4b'
M8 = 'qwen3-embedding:8b'
# rank 155 = "the right doc was last of 155", which is what a real rank looks like.
RANKS = {M06: [1, 1, 3, 7, 2, 155], M4: [1, 4, 1, 2, 2, 1], M8: [1, 1, 3, 7, 2, 1]}
CUTS = (200, 600)


def _call(rows=None):
    return embed_eval.breakdown(ROWS if rows is None else rows, RANKS, CUTS)


def test_breakdown_exists():
    assert hasattr(embed_eval, 'breakdown'), (
        'embed_eval.py has no breakdown(rows, rank_map, cuts) -- the per-query '
        'comparison t716 asks for cannot be produced')


def test_totals_and_cuts_are_reported():
    out = _call()
    assert out['n'] == 6, f'n must count the queries: {out["n"]}'
    assert list(out['cuts']) == [200, 600], (
        f'cuts must be the cut points used, as a list: {out["cuts"]!r}')
    assert sum(b['n'] for b in out['buckets'].values()) == 6, (
        'every query must land in exactly one length bucket -- a query dropped by a '
        'bucketing bug leaves a report that looks complete and is not')


def test_metrics_per_length_bucket():
    out = _call()
    expected = {
        '0-200': {'n': 2,
                  M06: {'recall@1': 1.0, 'recall@5': 1.0, 'mrr': 1.0},
                  M4: {'recall@1': 0.5, 'recall@5': 1.0, 'mrr': 0.625},
                  M8: {'recall@1': 1.0, 'recall@5': 1.0, 'mrr': 1.0}},
        '201-600': {'n': 2,
                    M06: {'recall@1': 0.0, 'recall@5': 0.5, 'mrr': 0.238},
                    M4: {'recall@1': 0.5, 'recall@5': 1.0, 'mrr': 0.75},
                    M8: {'recall@1': 0.0, 'recall@5': 0.5, 'mrr': 0.238}},
        '601+': {'n': 2,
                 M06: {'recall@1': 0.0, 'recall@5': 0.5, 'mrr': 0.253},
                 M4: {'recall@1': 0.5, 'recall@5': 1.0, 'mrr': 0.75},
                 M8: {'recall@1': 0.5, 'recall@5': 1.0, 'mrr': 0.75}},
    }
    for label, want in expected.items():
        assert label in out['buckets'], (
            f'bucket {label!r} missing from {sorted(out["buckets"])}')
        got = out['buckets'][label]
        assert got['n'] == want['n'], f'{label} n: {got["n"]} != {want["n"]}'
        for model, metrics in ((m, v) for m, v in want.items() if m != 'n'):
            assert model in got, f'{label} must report every model in rank_map'
            for metric, val in metrics.items():
                assert got[model][metric] == val, (
                    f'{label} {model} {metric}: {got[model][metric]} != {val} '
                    f'(computed by hand from the ranks; the aggregate score() is '
                    f'reused per bucket, so a wrong grouping shows up here)')


def test_disagreements_name_every_split_and_nothing_else():
    out = _call()
    by_index = {d['index']: d for d in out['disagreements']}
    assert len(out['disagreements']) == 4, (
        f'exactly the queries the models rank differently: {sorted(by_index)}')
    assert sorted(by_index) == [1, 2, 3, 5], (
        f'queries 0 and 4 are ranked 1,1,1 and 2,2,2 -- all models agree, so they '
        f'must NOT be in the failure list: {sorted(by_index)}')
    # The failure-case list is read by a person, so it carries the whole query.
    assert by_index[1]['query'] == QUERIES[1], (
        'the entry must carry the full query text, not a slice: '
        f'{by_index[1]["query"]!r}')
    assert by_index[5]['query'] == QUERIES[5], f'{by_index[5]["query"]!r}'
    assert by_index[1]['title'] == TITLES[1]
    assert by_index[1]['doc_chars'] == len(SHORT) == 100
    assert by_index[5]['doc_chars'] == len(LONG) == 900
    assert by_index[1]['ranks'] == {M06: 1, M4: 4, M8: 1}
    assert by_index[5]['ranks'] == {M06: 155, M4: 1, M8: 1}


def test_rank_one_tally():
    assert _call()['model_rank1'] == {M06: 2, M4: 3, M8: 3}, (
        'how often each model puts the right doc first, counted from the ranks')


def test_input_is_not_mutated():
    fresh = [{'title': t, 'query': q, 'body': b} for t, q, b in zip(TITLES, QUERIES, BODIES)]
    _call()
    assert ROWS == fresh, 'breakdown is a report over the rows, not a pass that edits them'


def _run():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith('test_') and callable(v)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f'PASS {name}')
        except AssertionError as exc:
            failed.append(name)
            print(f'FAIL {name}: {exc}')
        except Exception as exc:  # noqa: BLE001 -- a crash is a finding, not a skip
            failed.append(name)
            print(f'ERROR {name}: {type(exc).__name__}: {exc}')
    print(f'\n{len(tests) - len(failed)}/{len(tests)} passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_run())
