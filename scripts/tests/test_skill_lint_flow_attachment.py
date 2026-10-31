#!/usr/bin/env python3
'''skill_lint refuses a LIVE skill that the Flow chart does not show (t721).

WHY A NEW FILE: scripts/tests/test_skill_lint.py exists and is green on main (measured 2026-10-11:
22 passed, 0 failed), and scripts/bridge.py refuses an oracle chunk whose file already exists with
different text. Every case already in that suite stays untouched; the rule added here has its own id,
flow-attachment.

THE CHART IS INJECTED IN EVERY CASE. An oracle that read the live projects/local-inference/models/
FLOW.json would be red for the 16 of the 24 live skills that are off-chart today (measured 2026-10-11)
and could never go green from editing scripts/skill_lint.py alone.

THE DEFAULT (no attachments argument) IS PINNED BY CASE 6. scripts/skill_change.py lints a skill as
live inside approve() BEFORE its attach step writes FLOW.json, so a lint() whose default resolved the
chart from disk would refuse every new skill, and this file own fixture skill, which is on no chart,
would make case 6 permanently red.

Bodies carry a date, a task id, a commit hash, a repo path and a lesson id, all of which skill_lint
evidence rule accepts, so no case can pick up a finding for anything but the rule it is about.
'''
import inspect
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
import skill_lint as sl  # noqa: E402

PASSED = FAILED = 0
RULE = 'flow-attachment'
SKILL = 'why-it-crashed'

FM = ('---\nname: why-it-crashed\n'
      'description: Find why a job died: read the log from the end, then the first error.\n'
      'kind: task\n'
      'applies_when: a job died and the reason is not in the last lines\n'
      '---\n')
BODY = ('# Checking\n\nMeasured 2026-09-30 (t477): it held. Commit 6b93b546 added the gate; '
        'see docs/skills/SCHEMA.md and lesson L-046.\n')
LIVE = FM + BODY
DRAFT = FM.replace('kind: task\n', 'kind: task\nattach_to: [rca]\n')


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f'  ok    {label}')
    else:
        FAILED += 1
        print(f'  BAD   {label}\n          got  {got!r}\n          want {want!r}')


def ids(text, **kw):
    return [r for r, _ in sl.lint(text, **kw)]


def first_message(text, **kw):
    for r, m in sl.lint(text, **kw):
        if r == RULE:
            return m
    return ''


def cases():
    report('live skill attached to a task on the chart is clean',
           ids(LIVE, dirname=SKILL, attachments={SKILL: (['rca'], [])}), [])
    report('a role attachment counts as an attachment',
           ids(LIVE, dirname=SKILL, attachments={SKILL: ([], ['manager'])}), [])
    report('on the chart with an empty list is still unattached',
           ids(LIVE, dirname=SKILL, attachments={SKILL: ([], [])}), [RULE])
    report('a live skill absent from the chart is refused',
           ids(LIVE, dirname=SKILL, attachments={}), [RULE])
    report('declared in search_only is accepted',
           ids(LIVE, dirname=SKILL, attachments={}, search_only={SKILL}), [])
    report('the default resolves no chart (skill_change lints before it attaches)',
           ids(LIVE, dirname=SKILL), [])
    report('the message names the fix command',
           'skill_change.py' in first_message(LIVE, dirname=SKILL, attachments={}), True)
    report('the message names the search_only escape',
           'search_only' in first_message(LIVE, dirname=SKILL, attachments={}), True)
    report('a DRAFT gains no flow-attachment finding',
           RULE in ids(DRAFT, draft=True, names={}, tasks={'rca'}, roles={'manager'},
                       attachments={SKILL: ([], [])}, search_only=set()), False)
    report('chart_attachments reads the chart in the order it is written',
           sl.chart_attachments(
               flow={'tasks': [{'id': 'rca', 'skills': [SKILL]},
                               {'id': 'scoping', 'skills': [SKILL]},
                               {'id': 'no-skills-key'}]},
               roles={'roles': {'manager': {'skills': [SKILL]}, 'jas': {'skills': None}}}),
           {SKILL: (['rca', 'scoping'], ['manager'])})
    report('chart_attachments returns no entry for a skill the chart does not list',
           sl.chart_attachments(flow={'tasks': [{'id': 'rca'}]}, roles={'roles': {}}), {})
    report('chart_attachments accepts a chart with no tasks key and no roles key',
           sl.chart_attachments(flow={}, roles={}), {})
    res = sl.lint_all()
    report('lint_all keeps its {name: [(rule, message)]} shape',
           (isinstance(res, dict)
            and all(isinstance(k, str) and isinstance(v, list)
                    and all(isinstance(p, tuple) and len(p) == 2
                            and isinstance(p[0], str) and isinstance(p[1], str) for p in v)
                    for k, v in res.items())),
           True)


if __name__ == '__main__':
    sig = inspect.signature(sl.lint).parameters
    missing = []
    if not hasattr(sl, 'chart_attachments'):
        missing.append('skill_lint.chart_attachments()')
    if 'attachments' not in sig:
        missing.append('lint(attachments=...)')
    if 'search_only' not in sig:
        missing.append('lint(search_only=...)')
    if missing:
        print('  BAD   flow-attachment: not implemented -- missing ' + ', '.join(missing))
        print('\n0 passed, 1 failed')
        sys.exit(1)
    cases()
    print(f'\n{PASSED} passed, {FAILED} failed')
    sys.exit(1 if FAILED else 0)
