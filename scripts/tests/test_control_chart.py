#!/usr/bin/env python3
'''Fail-first oracle for t706: the I-MR control-chart core the Flow tab needs.

Pure arithmetic over a list of run records: no ledger is read, no file is written, no
model is called, no subprocess, no network, no /tmp path. A run record is a dict:

    at               str    label only; the order is list order
    duration_seconds float  the run wall time, greater than zero
    prompt_chars     int    optional; the prompt size for that run
    anchor           bool   optional; True is a manual review, which re-baselines
    annotation       bool   optional; True is a continuous-improvement edit

Red on main because scripts/flow_layout.py has no control_chart_* functions, so every
call below raises AttributeError and the runner records it as a failure.
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import flow_layout as CC  # noqa: E402


def _run(duration_seconds, at='', **kw):
    r = {'at': at, 'duration_seconds': float(duration_seconds)}
    r.update(kw)
    return r


BASE = [_run(v) for v in (60, 58, 62, 57, 63, 59, 61, 56, 64, 58,
                          62, 57, 63, 59, 61, 56, 64, 58, 62, 57)]
POST = [_run(v) for v in (300, 298, 302, 296, 304, 299, 301, 297, 303, 300,
                          298, 302, 296, 304, 299, 301, 297, 303, 300, 298)]


def test_frozen_limits_do_not_move_with_new_runs():
    base = CC.control_chart_limits_for(BASE)
    extended = BASE + [_run(90) for _ in range(6)] + [_run(1000)]
    assert CC.control_chart_limits_for(extended) == base, (base, extended)
    assert base['n'] == 20, base
    assert base['sigma'] > 0, base
    assert base['mean_seconds'] > 0, base
    assert base['ucl_seconds'] > base['mean_seconds'], base
    assert base['lcl_seconds'] < base['mean_seconds'], base


def test_manual_review_anchor_rebaselines_the_limits():
    base = CC.control_chart_limits_for(BASE)
    new = CC.control_chart_limits_for(BASE + [_run(60, anchor=True)] + POST)
    assert new['n'] == 20, new
    assert new['mean_seconds'] > base['mean_seconds'] + 100, (base, new)
    assert new['ucl_seconds'] > base['ucl_seconds'], (base, new)


def test_ci_edit_only_annotates_and_leaves_limits_frozen():
    plain = CC.control_chart_limits_for(BASE)
    with_ann = BASE + [_run(70, annotation=True, prompt_chars=12345)]
    assert CC.control_chart_limits_for(with_ann) == plain, plain
    assert CC.control_chart_annotations_for(with_ann) == [12345]
    assert CC.control_chart_annotations_for(BASE) == []


def test_a_normal_spread_raises_no_signal():
    assert CC.control_chart_detect_signals(BASE) == []


def test_one_outlier_raises_exactly_one_review_task():
    sig = CC.control_chart_detect_signals(BASE + [_run(1000)])
    assert sig == ['outlier'], sig
    r = CC.control_chart_decide_review('scoping', sig, {})
    assert r['action'] == 'raise', r
    assert r['job_type'] == 'scoping', r
    assert r['rule'] == 'outlier', r
    assert r['title'].startswith('Review scoping'), r


def test_eight_run_drift_raises_one_review_and_seven_do_not():
    sig = CC.control_chart_detect_signals(BASE + [_run(65) for _ in range(8)])
    assert sig == ['run8_above'], sig
    assert CC.control_chart_detect_signals(BASE + [_run(65) for _ in range(7)]) == []
    r = CC.control_chart_decide_review('building', sig, {})
    assert r['action'] == 'raise', r
    assert r['rule'] == 'run8_above', r
    assert r['title'].startswith('Review building'), r


def test_six_increasing_runs_is_a_signal():
    ramp = [_run(v) for v in (60, 61, 62, 63, 64, 65, 66)]
    assert 'six_increasing' in CC.control_chart_detect_signals(ramp), ramp
    short = [_run(v) for v in (60, 61, 62, 63, 64)]
    assert 'six_increasing' not in CC.control_chart_detect_signals(short), short


def test_an_open_review_gets_a_note_and_other_job_types_still_get_a_task():
    sig = CC.control_chart_detect_signals(BASE + [_run(1000)])
    note = CC.control_chart_decide_review('scoping', sig, {'scoping': 't900'})
    assert note['action'] == 'note', note
    assert note.get('task') == 't900', note
    assert CC.control_chart_decide_review('rca', sig, {'scoping': 't900'})['action'] == 'raise'
    assert CC.control_chart_decide_review('scoping', [], {})['action'] == 'none'


def _all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failed = []
    for t in tests:
        try:
            t()
            print('PASS  ' + t.__name__)
        except AssertionError as e:
            failed.append(t.__name__)
            print('FAIL  ' + t.__name__ + ': ' + str(e))
        except Exception as e:
            failed.append(t.__name__)
            print('EXC   ' + t.__name__ + ': ' + type(e).__name__ + ': ' + str(e))
    print('%d/%d passed' % (len(tests) - len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_all())
