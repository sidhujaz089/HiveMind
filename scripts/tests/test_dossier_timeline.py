#!/usr/bin/env python3
'''t472: scripts/dossier.py must show one task's Flow trail as ONE chronological timeline.

RED on today's code for the change and for nothing else: scripts/dossier.py defines task_file,
scope, build, sessions, extras, gather, content, render, index_text, write, main -- there is no
`timeline`, no `timeline_text` and no `--timeline` flag. The four fixture tests therefore raise
AttributeError on dossier.timeline, and test_cli_flag exits 2 because argparse refuses an unknown
--timeline. Read the printed FAIL lines: every one is one of those two causes.

Why the work is not 'walk gather()': build()'s ledger() collapses a whole ledger into ONE artifact,
so a trail built from artifacts shows one step per ledger, not one step per record.

Fixtures are the real artefact shapes: the Notes line `**2026-10-05 · jas** —` that task_file's
regex matches, the `TASK: <id>` line of workspace/briefs/*.txt that chunks_of matches, the
`name|brief|target|oracle` line of scripts/autonomy_loop.sh that build() parses, and ledger rows
modelled on the head of workspace/scope-quality.jsonl. The module is pointed at a temp repo the way
scripts/tests/test_dossier.py does it; the live workspace is never read by the fixture tests.
'''
import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import dossier  # noqa: E402

KEYS = {'at', 'task', 'step', 'who', 'inputs', 'outputs', 'triggers', 'links'}
FAILS = []


def ok(cond, msg):
    if not cond:
        FAILS.append(msg)


def fixture(tmp):
    w = tmp / 'workspace'
    (w / 'tasks').mkdir(parents=True)
    (w / 'briefs').mkdir(parents=True)
    (w / 'task-sessions' / 't472').mkdir(parents=True)
    (w / 'run-logs' / 'manager-queue' / 'out').mkdir(parents=True)
    (w / 'run-logs' / 'manager-queue' / 'in').mkdir(parents=True)
    (tmp / 'scripts').mkdir(parents=True)
    (w / 'tasks' / 't472--flow-audit-trail.md').write_text(
        '---\ntitle: Flow audit trail\n---\n\n## Notes\n\n**2026-10-05 · jas** — (Jas - Human) .\n',
        encoding='utf-8')
    (w / 'briefs' / 'brief-t472-timeline.txt').write_text(
        'TASK: t472\nFILE: scripts/dossier.py\n', encoding='utf-8')
    (w / 'task-sessions' / 't472' / '20261005T000000Z-swift.jsonl').write_text(
        json.dumps({'at': '2026-10-05T00:00:00Z', 'type': 'user'}) + '\n', encoding='utf-8')
    (w / 'run-logs' / 'manager-queue' / 'out' / 't472.md').write_text('(c) SCOPE\n', encoding='utf-8')
    (w / 'run-logs' / 'manager-queue' / 'in' / 't472.txt').write_text('scope this\n', encoding='utf-8')
    # The real autonomy_loop shape: name|brief|target|oracle. The oracle is a file that does NOT
    # exist in this tree, so build() emits an `oracle` artifact with no path -- the case the task's
    # own limit is about: a source that is gone is shown as 'input not kept', never skipped.
    (tmp / 'scripts' / 'autonomy_loop.sh').write_text(
        'p12-t472-timeline|brief-t472-timeline.txt|scripts/dossier.py|'
        'scripts/tests/test_dossier_timeline.py\n', encoding='utf-8')
    rows = [
        {'at': '2026-10-01T03:00:00Z', 'scope': 't472@aaa111111111', 'task': 't472',
         'model': 'qwen3.8:27b-mtp-bf16', 'prompt_version': 'dd16a235c4fd', 'stage': 'lint',
         'outcome': 'passed', 'chunk': '', 'class': '', 'scope_fault': False, 'evidence': '',
         'source': 'scope_lint', 'check': 'chunks-invalid'},
        {'at': '1999-01-01T00:00:00Z', 'scope': 't472@bbb222222222', 'task': 't472',
         'model': 'gemma4:26b', 'prompt_version': 'dd16a235c4fd', 'stage': 'preflight',
         'outcome': 'objected', 'chunk': 'p12-t472-timeline', 'class': 'brief-anchor',
         'scope_fault': True, 'evidence': 'anchor not in file', 'source': 'preflight_brief',
         'check': 'brief-anchor'},
        {'at': '2026-10-03T03:00:00Z', 'scope': 't999@ccc333333333', 'task': 't999',
         'model': 'deepseek-r1:32b', 'prompt_version': 'dd16a235c4fd', 'stage': 'lint',
         'outcome': 'passed', 'chunk': '', 'class': '', 'scope_fault': False, 'evidence': '',
         'source': 'scope_lint', 'check': 'chunks-invalid'},
    ]
    (w / 'scope-quality.jsonl').write_text(
        '\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8')
    return tmp


@contextlib.contextmanager
def pointed_at(tmp):
    saved = (dossier.REPO, dossier.W, dossier.ITEMS)
    dossier.REPO, dossier.W, dossier.ITEMS = tmp, tmp / 'workspace', tmp / 'workspace' / 'items'
    try:
        yield
    finally:
        dossier.REPO, dossier.W, dossier.ITEMS = saved


def steps_from(steps, needle):
    return [s for s in steps if any(needle in l for l in s['links'])]


def test_timeline_exists():
    tmp = Path(tempfile.mkdtemp())
    try:
        fixture(tmp)
        with pointed_at(tmp):
            ok(callable(getattr(dossier, 'timeline', None)),
               'dossier.py defines no callable `timeline` -- the task asks for one trail per task')
            ok(callable(getattr(dossier, 'timeline_text', None)),
               'dossier.py defines no callable `timeline_text` -- the trail needs a renderer')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_one_step_per_ledger_record():
    tmp = Path(tempfile.mkdtemp())
    try:
        fixture(tmp)
        with pointed_at(tmp):
            steps = dossier.timeline('t472')
        ok(isinstance(steps, list) and bool(steps), 'timeline("t472") returned no steps')
        ok(all(set(s) == KEYS for s in steps),
           'every step must carry exactly the keys at/task/step/who/inputs/outputs/triggers/links; got '
           + str(sorted({k for s in steps for k in s})))
        ok(all(s['task'] == 't472' for s in steps), 'a step reported a task other than t472')
        rows = steps_from(steps, 'scope-quality.jsonl')
        ok(len(rows) == 2,
           'workspace/scope-quality.jsonl holds 2 records for t472, so the trail must show 2 steps '
           'from it; got ' + str(len(rows)) + ' -- a ledger collapsed into one step is the defect')
        ok({s['who'] for s in rows} == {'qwen3.8:27b-mtp-bf16', 'gemma4:26b'},
           "each step's who must be the actor named on THAT record; got "
           + str(sorted({s['who'] for s in rows})))
        ok({s['triggers'] for s in rows} == {'scope_lint', 'preflight_brief'},
           "each step's triggers must be that record's own source; got "
           + str(sorted({s['triggers'] for s in rows})))
        ok(all(s['at'] for s in rows), 'a step lost the time written on its own record')
        ok(not any(s['who'] == 'deepseek-r1:32b' for s in steps),
           "t999's record leaked into t472's timeline -- the trail is per task")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_order_is_chronological():
    tmp = Path(tempfile.mkdtemp())
    try:
        fixture(tmp)
        with pointed_at(tmp):
            steps = dossier.timeline('t472')
        ats = [s['at'] for s in steps]
        ok(ats == sorted(ats, key=lambda a: (a == '', a)),
           'steps must be oldest-first, with a step that has no time last; got ' + str(ats))
        ok(ats[:1] == ['1999-01-01T00:00:00Z'],
           'the 1999 record must head the trail, which proves a sort and not the input order; got '
           + str(ats[:3]))
        ok(ats[-1:] == [''],
           'the one step whose source recorded no time must sort last; got ' + str(ats[-3:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_inputs_said_not_skipped():
    tmp = Path(tempfile.mkdtemp())
    try:
        fixture(tmp)
        with pointed_at(tmp):
            steps = dossier.timeline('t472')
        gone = [s for s in steps if s['step'] == 'oracle']
        ok(bool(gone),
           "the enrolled chunk's oracle is missing from the trail: a step was SKIPPED, and the task's "
           "limit says a source that is gone is shown as 'input not kept', never dropped")
        for o in gone:
            ok(o['inputs'] == ['input not kept'],
               'a step whose input file is gone must say input not kept, not hide it; got ' + str(o['inputs']))
            ok(o['outputs'] == ['input not kept'],
               'a step whose output file is gone must say input not kept; got ' + str(o['outputs']))
        ok(any(any('brief-t472-timeline.txt' in i for i in s['inputs']) for s in steps),
           'a ledger step must name the brief that produced the chunk its record is about')
        ok(any(any('scope-quality.jsonl' in l for l in s['links']) for s in steps),
           'the trail must link the ledger file the record lives in')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli_flag():
    for args, want in ((['t472', '--timeline'], 'Timeline for t472'), (['t472'], 'Dossier for t472')):
        r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'dossier.py')] + args,
                           capture_output=True, text=True, timeout=120, cwd=str(ROOT))
        ok(r.returncode == 0,
           'scripts/dossier.py ' + ' '.join(args) + ' exited ' + str(r.returncode) + ': ' + r.stderr[:400])
        ok(r.stdout.startswith(want),
           'scripts/dossier.py ' + ' '.join(args) + ' must open with ' + repr(want) + '; got '
           + repr(r.stdout[:120]))
    r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'dossier.py'), 't472', '--timeline'],
                       capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    ok(r.returncode == 0 and 'wrote' not in r.stdout,
       '--timeline is a view, not a build: it must not write the dossier file; got ' + repr(r.stdout[:200]))


if __name__ == '__main__':
    for _n, _fn in sorted(globals().items()):
        if _n.startswith('test_') and callable(_fn):
            _fn()
    for f in FAILS:
        print('FAIL:', f)
    print('PASS' if not FAILS else 'FAILURES: ' + str(len(FAILS)))
    sys.exit(1 if FAILS else 0)
