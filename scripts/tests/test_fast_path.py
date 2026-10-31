#!/usr/bin/env python3
"""The fast path: a finished job starts the next queued item NOW (t1309).

Jas, 2026-10-16: the Auditor sits idle ~50% of the time. Measured on
qwen3.8:27b-mtp-q4_K_M-56k: six session-reviews ran 1-2.5 min each, with 2-3.7 min of
nothing between one finishing and the next starting. The lane is free for minutes, and only a
full cron tick -- every 2 minutes, ~2 minutes of work of its own, and skipped outright while
another tick holds the lock (crontab line 2; scripts/dispatch_loop.py line 2536) -- starts what
is next.

What exists today: retick() (scripts/dispatch_loop.py lines 2539-2568) re-runs the WHOLE tick.
It Popen's 'flock -n TICK_LOCK python dispatch_loop.py' -- the entire script: memory checks,
thinkpad_cycle, intake, enrolment, all of it. And the gate that calls it
(scripts/dispatch_loop.py line 2665) fires only for RETICK_KINDS = {script-review, preflight,
console, supervise} (line 2523), which excludes session-review and rca -- the two kinds in
Jas's measurement.

This suite pins the replacement: a finished job hands its lane, and every other lane that is
free, to the next priority item AT ONCE, under schedule()'s rules, with no tick run.

Every assertion is about a value a caller consumes: the job dict launch() receives carries the
kind, model, key, chunk and path a worker needs, and the started items are the list retick()
returns. Every case carries a negative assertion, so an implementation that starts everything
queued, starts the finished role only, caches the order, starts a build, starts a second job
on a model with no free lane, or runs a full tick, fails.

No model, no live server, no live state: items, running, models, slots and launch are all
passed in, and the default (read-from-disk) path is exercised with every read source patched
to an in-memory fake. Nothing is written outside a temp dir; no subprocess is started.
"""
import contextlib
import fcntl
import inspect
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import dispatch_loop as DL   # noqa: E402

# WHICH REGIME THIS SUITE IS ABOUT (2026-10-30). These cases are about the FAST PATH -- a finishing job starting the
# next one without a full tick. On a box with workspace/run-logs/admission-gate.enabled the byte gate judges admission
# first and refuses with "admission UNPROVEN: ... the slot count is unreadable" inside a sandbox, which is not the
# rule this suite tests. The gate is switched off explicitly for the length of the test so a machine's flag cannot
# decide what is being asserted.
os.environ["HIVEMIND_ADMISSION_GATE"] = "0"
import parallel_dispatch as pd   # noqa: E402

PASSED = FAILED = 0

# The two roles of Jas's measurement, as real kinds and the models that serve them: the
# Auditor's session reviews and the Manager's RCAs. Distinct keys, so one repeated
# placeholder string cannot stand in for both.
AUD = 'qwen3.8:27b-mtp-q4_K_M-56k'
MGR = 'gemma4:26b'


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print('  [ok]   ' + label + ': ' + repr(got))
    else:
        FAILED += 1
        print('  [BAD]  ' + label + ': expected ' + repr(want) + ', got ' + repr(got))


def item(key, model, kind='session-review', role='auditor'):
    """A queued priority item, shaped like the dicts priority_items() builds (its loop at
    lines 1225-1230): kind, role, model, key, chunk, path, brief, brief_sha256, interrupt,
    level, created."""
    return {'kind': kind, 'role': role, 'model': model, 'key': key, 'chunk': key,
            'path': 'workspace/run-logs/' + key, 'brief': None,
            'brief_sha256': None, 'interrupt': False, 'level': 1, 'created': ''}


def running_row(key, model, kind='session-review', role='auditor'):
    """A lane that is occupied, in the shape schedule() reads (its uses of running: lines
    1376, 1405, 1407, 1426, 1458, 1460): model, kind, pid, elapsed_s, role."""
    return {'event': 'started', 'kind': kind, 'key': key, 'chunk': key, 'model': model,
            'role': role, 'pid': 4242, 'elapsed_s': 60}


def finished_job(key, model, kind='session-review', role='auditor'):
    """The job that just finished, as run_job() has it in hand in the block that reaches the
    gate (its own job dict, built in run_job before the finished row is written)."""
    return {'kind': kind, 'role': role, 'model': model, 'key': key, 'chunk': key,
            'path': 'workspace/run-logs/' + key + '.jsonl', 'brief': None,
            'brief_sha256': None}


def keys(launches):
    return [j.get('key') for j in launches]


def identity(launches):
    """The fields a worker needs to run the job at all -- the value the fast path must hand
    over whole, not a shape, not a count."""
    return sorted((j.get('key'), j.get('model'), j.get('kind')) for j in launches)


def rec_factory(store):
    def rec(job):
        store.append(job)
        return (4321, DL.REPO / 'workspace' / 'run-logs' / 'fast-path-test.log')
    return rec


def test_two_roles():
    """A finished job starts the next item for EVERY role with a free lane, in one call
    (Jas, 2026-10-16: 'Will this speed up apply to all roles?')."""
    launches = []

    out = DL.retick(
        finished_job('session-review--A.jsonl', AUD),
        items=[item('session-review--A.jsonl', AUD), item('rca--B.jsonl', MGR, 'rca', 'manager')],
        running=[], models=[AUD, MGR], slots=lambda m: 1, launch=rec_factory(launches))
    want = sorted([('session-review--A.jsonl', AUD, 'session-review'),
                   ('rca--B.jsonl', MGR, 'rca')])
    report('one call starts the top item for BOTH roles with a free lane', identity(launches), want)
    report('it returns what it started, in the form launch() got', identity(out), want)
    report('the job dict carries the chunk and path a worker opens',
           sorted((j.get('chunk'), j.get('path')) for j in launches),
           sorted([('session-review--A.jsonl', 'workspace/run-logs/session-review--A.jsonl'),
                   ('rca--B.jsonl', 'workspace/run-logs/rca--B.jsonl')]))


def test_no_double_start():
    """A model whose lanes are full gets nothing: the finished job's row is in the ledger, the
    ledger is what says the lane is free, and over-starting is the 2026-10-15 double-start."""
    launches = []

    out = DL.retick(items=[item('session-review--A.jsonl', AUD)],
                    running=[running_row('session-review--A.jsonl', AUD)],
                    models=[AUD], slots=lambda m: 1, launch=rec_factory(launches))
    report('a model with no free lane starts nothing', keys(launches), [])
    report('and the fast path returns nothing as started', out, [])


def test_builds_stay_on_the_tick():
    """Builds are excluded even when the lane is free (the task's WHAT)."""
    launches = []

    out = DL.retick(items=[item('p99', AUD, 'production', 'worker')],
                    running=[], models=[AUD], slots=lambda m: 1, launch=rec_factory(launches))
    report('a build is not taken by the fast path', keys(launches), [])
    report('and is not returned as started', out, [])


def test_a_running_tick_does_not_hold_it_up():
    """A TICK IN PROGRESS NO LONGER BLOCKS THE FAST PATH (Jas, 2026-10-18: "the point was adding tasks
    to the lane as soon as it's free"). The first version stood down whenever cron's tick held its
    lock -- a tick holds it ~2 min of every 2 -- and 11 of 21 finished jobs skipped. Double starts are
    now prevented where the job starts: launch() re-checks under LAUNCH_LOCK, held for milliseconds
    (test_dispatch_duplicate_guard.py, test_launch_cap below)."""
    tmp = Path(tempfile.mkdtemp())
    lock = tmp / 'dispatch_loop.lock'
    real = DL.TICK_LOCK
    launches = []

    fh = lock.open('a+')
    fcntl.flock(fh, fcntl.LOCK_EX)
    DL.TICK_LOCK = str(lock)
    try:
        out = DL.retick(items=[item('session-review--A.jsonl', AUD)], running=[],
                        models=[AUD], slots=lambda m: 1, launch=rec_factory(launches))
        report('a tick holding its lock does not stop the lane being filled', keys(launches),
               ['session-review--A.jsonl'])
        report('  and it is returned as started', keys(out), ['session-review--A.jsonl'])
    finally:
        DL.TICK_LOCK = real
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def test_launch_cap():
    """launch(job, cap) refuses a job on a model already running `cap` jobs -- the guard that lets a
    stale plan (the tick's, or a finishing job's) never over-fill a model. Real launch(), temp ledger,
    no child started."""
    import os as _os
    import json as _json
    d = Path(tempfile.mkdtemp())
    saved = (DL.REPO, DL.LEDGER, DL.JOBLOGS, DL.LAUNCH_LOCK, DL._alive, DL.subprocess.Popen)
    popen = []

    class Child:
        pid = 777001

    try:
        DL.REPO, DL.LEDGER, DL.JOBLOGS = d, d / 'ledger.jsonl', d / 'dispatch'
        DL.LAUNCH_LOCK = d / 'launch.lock'
        DL._alive = lambda pid: pid == 777000
        DL.subprocess.Popen = lambda *a, **k: (popen.append(1), Child())[1]
        DL.LEDGER.write_text(_json.dumps({'event': 'started', 'kind': 'rca', 'key': 'busy', 'chunk': 'busy',
                                          'model': AUD, 'pid': 777000}) + '\n')
        _os.environ['HIVEMIND_NO_SCOPE'] = '1'
        pid, log = DL.launch(item('session-review--C.jsonl', AUD), cap=1)
        report('a model at its cap starts nothing', (pid, len(popen)), (0, 0))
        report('  and the refusal says why', 'lane' in Path(log).read_text(), True)
        pid, log = DL.launch(item('session-review--C.jsonl', AUD), cap=2)
        report('a model under its cap starts the job', (pid, len(popen)), (777001, 1))
    finally:
        (DL.REPO, DL.LEDGER, DL.JOBLOGS, DL.LAUNCH_LOCK, DL._alive, DL.subprocess.Popen) = saved


def test_order_is_read_live():
    """No precomputed list (Jas, 2026-10-16: 'if it queues 300 tasks and we move up a task,
    will it need to rerun the checklist?'). The fast path asks the store for the order AT CALL
    TIME, so a reorder between a tick and a job finishing takes effect at the next free lane.
    The live list is the same content across calls; an implementation that caches it fails the
    fourth assertion, and one that asks the store only once fails the fifth."""
    calls = []
    live = [item('session-review--A.jsonl', AUD)]

    def fake_priority_items(chunks, running, red_of, history=None, objections=None, preflight=True):
        calls.append((list(chunks), list(running), preflight))
        return list(live)

    saved = (DL.priority_items, DL.chunks_in_task_order, DL.rows, DL.ledger_running,
             DL.cached_red, DL.main_preflights, DL.senior_station, pd.chunks)
    launches = []

    try:
        DL.priority_items = fake_priority_items
        DL.chunks_in_task_order = lambda c: list(c)
        DL.rows = lambda *a, **k: []
        DL.ledger_running = lambda *a, **k: []
        DL.cached_red = lambda run, state=None: (lambda p: False)
        DL.main_preflights = lambda s: True
        DL.senior_station = lambda *a, **k: None
        pd.chunks = lambda: []

        out = DL.retick(items=None, running=[], models=[AUD], slots=lambda m: 1,
                        launch=rec_factory(launches))
        report('with no items passed in it asks the role store once', len(calls), 1)
        report('  and starts the item the store puts on top', keys(launches), ['session-review--A.jsonl'])
        report('  and returns it', keys(out), ['session-review--A.jsonl'])

        # A reorder, and A has now started: same call, the live list changed under it.
        live[:] = [item('session-review--B.jsonl', AUD)]
        launches = []
        out = DL.retick(items=None, running=[running_row('session-review--A.jsonl', AUD)],
                        models=[AUD], slots=lambda m: 2, launch=rec_factory(launches))
        report('a reorder takes effect at the next freed lane', keys(launches), ['session-review--B.jsonl'])
        report('  one ask per call -- nothing is precomputed', len(calls), 2)
        report('  and the running lane is passed to the store, so a job already running is not started again',
               [r.get('key') for r in calls[1][1]], ['session-review--A.jsonl'])
    finally:
        (DL.priority_items, DL.chunks_in_task_order, DL.rows, DL.ledger_running,
         DL.cached_red, DL.main_preflights, DL.senior_station, pd.chunks) = saved


def test_no_full_tick():
    """The point of the change: the lane is filled WITHOUT running the tick (memory checks,
    thinkpad_cycle, intake, enrolment). If the fast path grows a tick, this catches it."""
    def boom(*a, **k):
        raise AssertionError('the fast path ran a full tick -- that is the defect t1309 exists to fix')

    real = DL.tick
    launches = []

    DL.tick = boom
    try:
        out = DL.retick(items=[item('session-review--A.jsonl', AUD)], running=[],
                        models=[AUD], slots=lambda m: 1, launch=rec_factory(launches))
        report('the lane is filled without running tick()', keys(out), ['session-review--A.jsonl'])
    finally:
        DL.tick = real


def isolate():
    """THE SUITE ASSERTS THE FAST PATH, NOT THIS MACHINE (Claude, 2026-10-17, finishing t1309). Run
    against live state, schedule() refused gemma4:26b as "does not fit beside" Q4 (fits() reads live
    memory) and held a second Auditor item because the role store gives the Auditor 1 lane -- both
    correct in production, both outside what this suite tests. Memory always fits and roles carry no
    lane cap here; `slots` alone decides lanes, as every case passes it explicitly."""
    import task_routing
    DL.fits = lambda model, busy: True
    task_routing.role_lanes = lambda d, role: None


def main():
    global FAILED
    isolate()
    params = inspect.signature(DL.retick).parameters
    need = ('items', 'running', 'models', 'slots', 'launch')
    missing = [p for p in need if p not in params]
    if missing:
        print('  [RED]  retick() accepts no arguments: ' + ', '.join(missing)
              + ' are missing -- the fast path does not exist yet.')
        print('  Today retick() (line 2539) spawns a WHOLE new tick with flock -n (line 2563);')
        print('  it starts no item itself, and the gate at line 2665 skips session-review and rca.')
        return 1
    tests = [test_two_roles, test_no_double_start, test_builds_stay_on_the_tick,
             test_a_running_tick_does_not_hold_it_up, test_launch_cap,
             test_order_is_read_live, test_no_full_tick]
    for t in tests:
        print('-- ' + t.__name__)
        try:
            t()
        except Exception as e:                                          # noqa: BLE001
            FAILED += 1
            print('  [BAD]  ' + t.__name__ + ' raised ' + type(e).__name__ + ': ' + str(e))
    print('passed ' + str(PASSED) + '  failed ' + str(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
