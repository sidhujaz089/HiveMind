#!/usr/bin/env python3
'''A work term is legal ONLY in the position it names (t9536 note 2).

The routing maps a kind of work to a task id with KIND_TASK.get(word, word), so a word that is a
legal name in the wrong position answers with a default: owner(store, 'manager') returned None (a
scoping queue with no scoper, and nothing raised) and max_lanes(store, 'manager') returned 0, which
every caller reads as NO CAP. This suite pins the position-typed lookup: a real kind resolves to the
task id on its role's list, a role key used where a kind belongs RAISES naming the word and the
position it is wrong in, and every word that is not a role key keeps the answer it has today.
'''
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

FAILS = []


def check(label, ok, detail=""):
    print("  [" + ("ok" if ok else "FAIL") + "]  " + label + ("" if ok else "  -> " + str(detail)))
    if not ok:
        FAILS.append(label)


def finish():
    if FAILS:
        print("test_work_terms_resolve: FAILED (" + str(len(FAILS)) + " of them)")
        return 1
    print("test_work_terms_resolve: PASS")
    return 0


def call(fn, *args):
    '''(value, None) if fn returned a value, (None, 'ExcType: message') if it raised.'''
    try:
        return fn(*args), None
    except Exception as exc:
        return None, type(exc).__name__ + ': ' + str(exc)


def row_kind(row):
    if isinstance(row, str):
        return row
    if isinstance(row, dict):
        return row.get('kind')
    return None


def main():
    import task_routing as T

    # The store under test is the routing's OWN default, deep-copied: real roles, real task ids,
    # owned by this test, so no edit on the Models tab can turn it red on main.
    store = copy.deepcopy(T.DEFAULT)

    rt = getattr(T, 'resolve_term', None)
    check('task_routing.resolve_term exists (the position-typed lookup t9536 asks for)',
          callable(rt), 'no resolve_term: a word naming work is looked up with a .get(word, word), '
          'which answers a legal word in the wrong position with a default')
    if not callable(rt):
        return finish()

    for kind, task in sorted(T.KIND_TASK.items()):
        got, err = call(rt, kind, store)
        check('resolve_term(' + repr(kind) + ') == ' + repr(task) + ' (the task id on its role list)',
              err is None and got == task, ('raised ' + err) if err else ('got ' + repr(got)))

    for tid in ('building', 'assigned_items', 'triage'):
        got, err = call(rt, tid, store)
        check('resolve_term(' + repr(tid) + ') == ' + repr(tid) + ' (a task id, not a kind name)',
              err is None and got == tid, ('raised ' + err) if err else ('got ' + repr(got)))

    got, err = call(rt, 'manager', store)
    check('a ROLE key where a kind of work belongs RAISES (owner(store, "manager") today answers '
          + repr(got) + ')', err is not None, 'returned ' + repr(got))
    if err:
        check('  the message names the word', 'manager' in err, err)
        check('  the message names the position it is wrong in', 'role' in err.lower(), err)

    ut = getattr(T, 'UnknownTerm', None)
    check('UnknownTerm is a LookupError and NOT a KeyError (so the except KeyError handlers in '
          'task_routing.py cannot swallow the refusal)',
          isinstance(ut, type) and issubclass(ut, LookupError) and not issubclass(ut, KeyError),
          'UnknownTerm is ' + repr(ut))

    got, err = call(T.owner, store, 'manager')
    check('owner(store, "manager") RAISES (today it answers None: a scoping queue with no scoper '
          'and nothing raised)', err is not None, 'returned ' + repr(got))
    lanes, lerr = call(T.max_lanes, store, 'manager')
    check('max_lanes(store, "manager") RAISES (today it answers ' + repr(lanes) + ', and 0 reads as '
          'NO CAP: a scoping grant would have taken every free lane)', lerr is not None,
          'returned ' + repr(lanes))

    owner_of = {'preflight': 'senior', 'supervise': None, 'script-review': 'auditor',
                'session-review': 'senior', 'rca': 'senior', 'production': 'coder',
                'console': 'manager', 'scoping': 'manager', 'script-audit': None,
                'done-check': None}
    check('the owner map below covers every kind in KIND_TASK',
          set(owner_of) == set(T.KIND_TASK), 'KIND_TASK is ' + repr(sorted(T.KIND_TASK)))
    for kind, want in sorted(owner_of.items()):
        got, err = call(T.owner, store, kind)
        check('owner(store, ' + repr(kind) + ') still answers ' + repr(want),
              err is None and got == want, ('raised ' + err) if err else ('got ' + repr(got)))

    got, err = call(T.entry, store, 'senior', 'preflight')
    check('entry(store, "senior", "preflight") is the task own entry dict',
          err is None and isinstance(got, dict) and got.get('task') == 'preflight',
          ('raised ' + err) if err else ('got ' + repr(got)))
    names = [t.get('task') for t in T.tasks_of(store, 'manager')]
    got, err = call(T.position, store, 'manager', 'console')
    check('position(store, "manager", "console") is the index of console on the manager list',
          err is None and got == names.index('console'),
          ('raised ' + err) if err else ('got ' + repr(got)))
    lanes, lerr = call(T.max_lanes, store, 'production')
    check('max_lanes(store, "production") still answers an int',
          lerr is None and isinstance(lanes, int) and not isinstance(lanes, bool),
          ('raised ' + lerr) if lerr else ('got ' + repr(lanes)))

    # A word that is not a role key keeps the answer it has today: two existing suites pin it
    # (scripts/tests/test_task_routing.py and test_roles_are_pointers.py), and this suite must not
    # fight them. Raising on every unknown word is NOT this scope.
    got, err = call(T.owner, store, 'web-research')
    check('owner(store, "web-research") still answers None (a task nobody lists: no owner, no raise)',
          err is None and got is None, ('raised ' + err) if err else ('got ' + repr(got)))
    got, err = call(T.role_name, 'session_embed', store)
    check('role_name("session_embed", store) keeps its loud string',
          err is None and got == 'no role has session_embed',
          ('raised ' + err) if err else ('got ' + repr(got)))

    lv, verr = call(T.levels, store)
    check('levels(store) still answers', verr is None, 'raised ' + str(verr))
    bad = []
    for row in (lv if isinstance(lv, dict) else []):
        k = row_kind(row)
        if not k:
            continue
        got, err = call(T.owner, store, k)
        if err:
            bad.append(str(k) + ': ' + err)
    check('every kind levels(store) reports has an owner lookup that answers, raising nothing',
          not bad, '; '.join(bad[:3]))

    return finish()


if __name__ == '__main__':
    sys.exit(main())
