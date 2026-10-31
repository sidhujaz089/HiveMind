#!/usr/bin/env python3
'''t1341 -- the held-for-Claude note reason comes from the RECORD, not from the freeze flag.

Why this suite exists (workspace/rca/3bd8970cc469.md section 2): scripts/bridge_trigger.py run()
composed the human-facing note from the `freeze` boolean, so the clause "(the automatic freeze is
off -- see bridge_trigger.freeze_enabled)" was emitted exactly when the freeze flag was ON -- the
inverse of the truth. The tasks the role gate refused (t607, t658, t680, t732) have ledger rows
stage=freeze outcome=held-for-claude evidence="git merge failed: REFUSED BY THE ROLE GATE (t476):
needs scoping on that list": the freeze was ON and was being refused, while every reader of the
task note was told it was off and sent to bridge_trigger.freeze_enabled to confirm a state that
was not the state.

Red on main for the missing change: scripts/bridge_trigger.py has no note_reason, no
recorded_freeze_reason and no held_for_claude_note, and run() emits the off clause when `freeze` is
truthy. Model-free and live-state-free: fakes for git, bridge and the task store, tempfile only.
'''
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import bridge            # run() imports this module by name, so patching it reaches run()
import bridge_trigger
import scope_quality

# Two DISTINCT real reasons, in the recorder's own words, from two different tasks: one placeholder
# string for both would let a constant pass as a per-task lookup.
ROLE_GATE = 'git merge failed: REFUSED BY THE ROLE GATE (t476): needs scoping on that list'
SWITCH_OFF = f'switched off ({bridge_trigger.FREEZE_OFF} exists)'


def _fn(name):
    fn = getattr(bridge_trigger, name, None)
    assert fn is not None, ('scripts/bridge_trigger.py has no ' + name + '(): the held-for-Claude '
                           'note reason must be derived from the record, not from the freeze flag '
                           '(t1341)')
    return fn


def _drive(freeze, task='t607'):
    """bridge_trigger.run() against fakes: no git, no model, no live ledger, no live task store."""
    saved = (bridge.parse, bridge.validate, bridge.main, bridge_trigger._git,
             bridge_trigger.freeze_enabled, scope_quality.LEDGER)
    notes = []

    def fake_git(repo, *args):
        cmd = args[0] if args else ''
        if cmd == 'rev-parse' and '--verify' in args:
            return subprocess.CompletedProcess(['git'], 1, '', '')    # no bridge/t607 branch yet
        out = 'deadbeef\n' if cmd == 'rev-parse' else ('scripts/x.py\n' if cmd == 'diff' else '')
        return subprocess.CompletedProcess(['git'], 0, out, '')

    bridge.parse = lambda text: ([{'kind': 'implementation', 'file': 'scripts/x.py'}], [])
    bridge.validate = lambda chunks, repo: []
    bridge.main = lambda argv: 0
    bridge_trigger._git = fake_git
    bridge_trigger.freeze_enabled = lambda *a, **k: (False, SWITCH_OFF)
    with tempfile.TemporaryDirectory(prefix='t1341-answer-') as ad:
        scope_quality.LEDGER = Path(ad) / 'scope-quality.jsonl'   # never the live ledger
        answer = Path(ad) / 'answer.md'
        answer.write_text('prose\n\n```chunks\n[]\n```\n', encoding='utf-8')
        try:
            rc = bridge_trigger.run(answer, task, 'manager', repo=Path(ad), freeze=freeze,
                                    record=True, note=lambda t, text: notes.append(text))
        finally:
            (bridge.parse, bridge.validate, bridge.main, bridge_trigger._git,
             bridge_trigger.freeze_enabled, scope_quality.LEDGER) = saved
    return rc, notes


def test_note_reason_names_the_refusal_verbatim_and_never_the_off_clause():
    r = _fn('note_reason')(4, True, ROLE_GATE)
    assert ROLE_GATE in r, f'the reason must echo the recorded evidence verbatim, got: {r}'
    assert 'refused' in r, f'a freeze that was refused must say so, got: {r}'
    assert 'freeze is off' not in r, f'the freeze was ON: {r}'


def test_note_reason_says_off_only_when_no_freeze_was_asked():
    note_reason = _fn('note_reason')
    r = note_reason(0, False)
    assert 'freeze is off' in r, f'freeze=False must say the automatic freeze is off, got: {r}'
    assert 'freeze_enabled' in r, f'the reader must be sent to the switch, got: {r}'
    assert 'refused' not in r, f'nothing was refused when no freeze was asked, got: {r}'
    assert note_reason(0, True) == '', f'a merged freeze has no reason to report, got: {note_reason(0, True)}'


def test_note_reason_still_says_refused_when_the_record_is_silent():
    r = _fn('note_reason')(4, True, '')
    assert 'refused' in r, f'an empty record must not be reported as the freeze being off, got: {r}'
    assert 'freeze is off' not in r, f'the freeze was ON: {r}'


def test_held_for_claude_note_keeps_the_branch_and_the_period():
    note = _fn('held_for_claude_note')
    with_reason = note('t607', 'bridge/t607', ROLE_GATE)
    assert 'bridge/t607' in with_reason, f'the branch must stay named, got: {with_reason}'
    assert ROLE_GATE in with_reason, f'the reason must be echoed verbatim, got: {with_reason}'
    assert 'freeze is off' not in with_reason, f'the freeze was ON: {with_reason}'
    bare = note('t607', 'bridge/t607')
    assert bare.endswith('.'), f'the note is a sentence, got: {bare}'
    assert 'waits for Claude' in bare, f'the phrase scripts/tests/test_bridge_trigger.py pins, got: {bare}'


def test_recorded_freeze_reason_reads_the_ledger_for_that_task_only():
    recorded_freeze_reason = _fn('recorded_freeze_reason')
    with tempfile.TemporaryDirectory(prefix='t1341-ledger-') as d:
        led = Path(d) / 'scope-quality.jsonl'
        rows = [
            {'task': 't607', 'stage': 'freeze', 'outcome': 'held-for-claude', 'evidence': ROLE_GATE},
            {'task': 't999', 'stage': 'freeze', 'outcome': 'held-for-claude', 'evidence': SWITCH_OFF},
            {'task': 't607', 'stage': 'freeze', 'outcome': 'merged', 'evidence': 'merged clean'},
            {'task': 't607', 'stage': 'bridge', 'outcome': 'branch', 'evidence': 'branch made'},
            {'task': 't607', 'stage': 'freeze', 'outcome': 'held-for-claude',
             'evidence': 'older evidence stays behind', 'reason': 'the reason field wins'},
        ]
        led.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
        got = recorded_freeze_reason('t607', led)
        assert got == 'the reason field wins', f'the newest held-for-claude row for that task, got: {got}'
        assert recorded_freeze_reason('t999', led) == SWITCH_OFF, 'the reason must follow the task'
        assert recorded_freeze_reason('t777', led) == '', 'no row, no invented reason'
        assert recorded_freeze_reason('t607', Path(d) / 'absent.jsonl') == '', 'must not raise'


def test_run_writes_the_recorded_reason_into_the_note():
    rc, notes = _drive(freeze=True)
    assert rc == 4, f'the patched switch refuses the freeze, so exit 4, got {rc}'
    assert len(notes) == 1, f'a refusal is noted exactly once, got {len(notes)}: {notes}'
    n = notes[0]
    assert SWITCH_OFF in n, f'the note must carry the reason the RECORD gave, got: {n}'
    assert 'refused' in n, f'a refused freeze must say refused, got: {n}'
    assert 'freeze is off' not in n, f'the freeze was ON: {n}'
    assert 'bridge/t607' in n, f'the branch must stay named, got: {n}'


def test_run_says_off_only_when_no_freeze_was_asked():
    rc, notes = _drive(freeze=False)
    assert rc == 0, f'no freeze asked, nothing to refuse, got {rc}'
    assert len(notes) == 1, f'a branch with no freeze is noted once, got {len(notes)}: {notes}'
    assert 'freeze is off' in notes[0], f'freeze=False must say so, got: {notes[0]}'
    assert 'bridge/t607' in notes[0], f'the branch must stay named, got: {notes[0]}'


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print('PASS ' + t.__name__)
        except Exception as e:                            # noqa: BLE001 -- a suite reports, never dies
            failed += 1
            print(f'FAIL {t.__name__}: {type(e).__name__}: {e}')
    print(f'{len(tests) - failed}/{len(tests)} passed')
    sys.exit(1 if failed else 0)
