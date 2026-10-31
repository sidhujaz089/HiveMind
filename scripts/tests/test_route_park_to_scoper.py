#!/usr/bin/env python3
'''t1071 -- dispatch_loop.route_waiting: a Senior objection and a 'came to nothing' park are
BRIEF defects, so the FIRST one goes back to the scoper -- the role whose Models-tab list owns
Scoping -- carrying its evidence, and only a SECOND identical failure goes to Claude.

Not brief defects, so they stay with Claude: a harness-bug park (futile(),
dispatch_loop.py:686-688) and a 'HELD: red on main' hold (dispatch_loop.py:561). Editing the
brief releases neither. Those are the negative assertions: a routing built on the word PARKED
alone fails them.

Hermetic: a fake task store (the shape scripts/tests/test_route_waiting.py uses), the
auto-assigned ledger under tempfile.mkdtemp(), no model call, no subprocess, no live file
written, nothing left running.
'''
import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import dispatch_loop as D  # noqa: E402
import task_routing as TR  # noqa: E402

# The scoper's role key, COMPUTED the way the loop computes it: task_routing.owner(d,
# 'manager') at dispatch_loop.py:885, whose KIND_TASK map (scripts/task_routing.py:44-46)
# sends the kind 'manager' to the task 'scoping'. No role name is hardcoded here.
SCOPER = TR.owner(D.ROUTING, 'scoping')

# Reason strings written from the code that writes them, so the assertions describe real text.
OBJ = ('HELD FOR CLAUDE: Q16 objected to this brief '
       '(workspace/preflight-objections.jsonl)')
PARK = ('PARKED: 1 attempt(s) in a row came to nothing (exit 1; on swift). The brief or its '
        'oracle is the thing to fix -- edit the brief to release it. '
        'Evidence: python3 scripts/supervise.py --failures')
HARNESS = ("PARKED: BLOCKED BY A HARNESS BUG: 1 failures on this brief were the harness's "
           'fault (supervise.py --failures), so retrying it cannot help. Fix the harness '
           'task, then edit the brief to release it.')
RED = 'HELD: red on main: scripts/tests/test_x.py is red on main'
# A brief edit changes the reason futile() writes: a new attempt count, new text.
REASON_V2 = ('PARKED: 2 attempt(s) in a row came to nothing (exit 1; on swift). The brief '
             'itself is the thing to fix -- edit the brief to release it.')

SKIPPED = [('p1-t1-a', OBJ), ('p2-t2-a', PARK), ('p3-t3-a', HARNESS), ('p4-t4-a', RED)]

FAILED = []
COUNT = [0]


def check(label, got, want):
    COUNT[0] += 1
    ok = got == want
    tag = 'ok' if ok else 'BAD'
    print(f'  [{tag}] {COUNT[0]}. {label}: got {got!r} want {want!r}')
    if not ok:
        FAILED.append(f'{COUNT[0]}. {label}: got {got!r} want {want!r}')


class Store:
    '''The four taskstore calls route_waiting makes (dispatch_loop.py:589-606).'''
    PRODUCTION = 'production'

    def __init__(self):
        self.t = {tid: {'status': 'doing', 'needs': 'production'}
                  for tid in ('t1', 't2', 't3', 't4')}
        self.notes = []

    def find(self, tid):
        return (self.t.get(tid), '')

    def assignee(self, v):
        return v or 'production'

    def set_meta(self, tid, needs=None):
        self.t[tid]['needs'] = needs or 'production'

    def add_note(self, tid, text, who=''):
        self.notes.append((tid, text))


def fresh():
    return Store(), Path(tempfile.mkdtemp()) / 'auto.json'


def notes_for(st, tid):
    return [n for t, n in st.notes if t == tid]


def test_config_guard():
    check('Scoping is owned by a role and it is not Claude -- if this fails the Models tab has '
          'taken this task away, so its routing is config-suppressed, not broken',
          bool(SCOPER) and SCOPER != 'claude', True)


def test_a_first_brief_defect_goes_to_the_scoper():
    st, led = fresh()
    D.route_waiting(SKIPPED, path=led, store=st)
    check('a first Senior objection goes to the scoper', st.t['t1']['needs'], SCOPER)
    check('a first Senior objection is NOT sent to Claude', st.t['t1']['needs'] == 'claude', False)
    check("a first 'came to nothing' park goes to the scoper", st.t['t2']['needs'], SCOPER)
    check("a first 'came to nothing' park is NOT sent to Claude",
          st.t['t2']['needs'] == 'claude', False)
    n1 = notes_for(st, 't1')
    check('one note on the first tick', len(n1), 1)
    check("the objection's evidence travels with the task",
          any('preflight-objections.jsonl' in n for n in n1), True)
    check('the note names the action: revise the brief',
          any('revise the brief' in n.lower() for n in n1), True)
    n2 = notes_for(st, 't2')
    check("the park's failure trail travels with the task",
          any('supervise.py --failures' in n for n in n2), True)
    check("the note says what to do with a 'came to nothing'",
          any('revise the brief' in n.lower() for n in n2), True)


def test_a_brief_defect_never_lands_on_claude_first():
    st, led = fresh()
    D.route_waiting(SKIPPED, path=led, store=st)
    check('a harness-bug park stays with Claude -- a brief edit releases nothing there',
          st.t['t3']['needs'], 'claude')
    # AMENDED (Claude, 2026-10-14, Jas's policy that day): a red-on-main hold assigns NOTHING --
    # red_on_main.py files one owner task for the suite, so the held tasks stay in production.
    check("a 'red on main' hold assigns nothing -- the suite's own red-on-main task owns it",
          st.t['t4']['needs'], 'production')


def test_the_reason_is_carried_verbatim():
    st, led = fresh()
    D.route_waiting(SKIPPED, path=led, store=st)
    check("the park's reason text reaches the note unchanged (task_board.py:195 and "
          'lane_status.py:274 key off its prefix)', any(PARK in n for n in notes_for(st, 't2')), True)
    check("the objection's reason text reaches the note unchanged (feedback.py:2543 keys off "
          'its prefix)', any(OBJ in n for n in notes_for(st, 't1')), True)


def test_the_ledger_records_the_route():
    st, led = fresh()
    D.route_waiting(SKIPPED, path=led, store=st)
    rec = json.loads(led.read_text())['t1']
    check('the ledger records the scoper as the target', rec['to'], SCOPER)
    check('the ledger records which chunk', rec['chunk'], 'p1-t1-a')
    check('the ledger records the reason it routed on', rec['why'], OBJ)
    check('the ledger stamps the handoff', bool(rec.get('at')), True)


def handed_back(st, *tids):
    """The scoper finishing its Assigned item: OUTCOME: done unassigns the task (assigned_worker.py).
    AMENDED 2026-10-14: the next TICK is not a second failure -- the chunk is still parked two minutes
    later because the scoper is still revising the brief; only the same reason AFTER the hand-back is."""
    for t in tids:
        st.t[t]['needs'] = 'production'


def test_a_second_failure_escalates_to_claude():
    st, led = fresh()
    D.route_waiting(SKIPPED, path=led, store=st)
    D.route_waiting(SKIPPED, path=led, store=st)
    check('the next tick, scoper still revising: no escalation and no new note',
          (st.t['t1']['needs'], len(notes_for(st, 't1'))), (SCOPER, 1))
    handed_back(st, 't1', 't2')
    D.route_waiting(SKIPPED, path=led, store=st)
    check('a SECOND identical failure is Claude\'s', st.t['t1']['needs'], 'claude')
    check("the scoper's second park is Claude's too", st.t['t2']['needs'], 'claude')
    n1 = notes_for(st, 't1')
    check('one note per handoff: two ticks, two notes', len(n1), 2)
    check('the escalation says why: a second failure',
          any('second' in n.lower() for n in n1), True)
    D.route_waiting(SKIPPED, path=led, store=st)
    check('a third tick on the same brief adds no note', len(notes_for(st, 't1')), 2)
    check('the escalated task is not bounced back to the scoper', st.t['t1']['needs'], 'claude')


def test_an_edited_brief_is_a_first_failure_again():
    st, led = fresh()
    D.route_waiting(SKIPPED, path=led, store=st)
    handed_back(st, 't1', 't2')
    D.route_waiting(SKIPPED, path=led, store=st)
    check('escalated to Claude by the second failure', st.t['t1']['needs'], 'claude')
    D.route_waiting([('p1-t1-a', REASON_V2), ('p2-t2-a', REASON_V2)], path=led, store=st)
    check('a revised brief (a new reason) goes back to the scoper, not to Claude',
          st.t['t1']['needs'], SCOPER)
    check("the second park's revised brief goes back to the scoper", st.t['t2']['needs'], SCOPER)
    check('a chunk that stopped being parked goes back to production',
          st.t['t3']['needs'], 'production')


def test_a_humans_assignment_is_never_overridden():
    st, led = fresh()
    st.t['t1']['needs'] = 'jas'
    D.route_waiting(SKIPPED, path=led, store=st)
    check("Jas's own assignment is left alone", st.t['t1']['needs'], 'jas')
    check('and no note is filed against it', notes_for(st, 't1'), [])


TESTS = [('config guard', test_config_guard),
         ('first brief defect -> scoper', test_a_first_brief_defect_goes_to_the_scoper),
         ('not the brief\'s fault -> Claude', test_a_brief_defect_never_lands_on_claude_first),
         ('reason carried verbatim', test_the_reason_is_carried_verbatim),
         ('ledger records the route', test_the_ledger_records_the_route),
         ('second failure -> Claude', test_a_second_failure_escalates_to_claude),
         ('edited brief -> scoper again', test_an_edited_brief_is_a_first_failure_again),
         ('a human\'s assignment stands', test_a_humans_assignment_is_never_overridden)]

if __name__ == '__main__':
    for name, fn in TESTS:
        print(f'== {name}')
        try:
            fn()
        except Exception as e:                                    # said, not swallowed
            FAILED.append(f'{name} raised {type(e).__name__}: {e}')
            print(f'  BAD {name} raised {type(e).__name__}: {e}')
            traceback.print_exc()
    if FAILED:
        print(f'{len(FAILED)} assertion(s) failed:')
        for f in FAILED:
            print(f'  - {f}')
    else:
        print(f'all {COUNT[0]} assertions passed')
    sys.exit(1 if FAILED else 0)
