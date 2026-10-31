#!/usr/bin/env python3
"""`wake` can be SET, not only read: the field existed for six weeks and nothing could write it (t7521).

Jas, 2026-09-11: *"let's look at this in November"* had two bad endings — the task sat in the active
queue looking like work nobody was doing, or it was moved aside and never seen again. So `wake` was
added: `session_start.py:658` reads `^wake:\\s*(\\d{4}-\\d{2}-\\d{2})$` from the front matter and sorts
the arrived ones to the top.

**And nothing could write it.** `taskstore.py` had no command, `_rewrite` carries the key, and a hand
edit of a task file is refused by `scripts/hooks/guard_task_writes.py`. On 2026-10-26 the repository
contained zero tasks with a wake date. A field that is read every session and cannot be written is
not a feature; it is a dangling reader.

What this suite refuses:

  * a command that silently accepts a malformed date -- "November" must not become a stored date that
    `session_start.py`'s regex then never matches, which would look exactly like "not due yet";
  * a stored date with no note, because a deferral is a decision and the queue must say who made it
    and until when (the same rule as no-silent-reassignment);
  * a clear that leaves the key behind with an empty value, since `^wake:\\s*(\\d{4}...)` would not
    match it but a reader could still see `wake:` and believe something;
  * `wake` becoming a schedule: this is a DATE THE QUEUE RESURFACES A TASK ON, nothing else.

Hermetic: every test points the module's paths at its own temp dir -- no real task file, no real
QUEUE.md, no real lock.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'projects' / 'app-repo'))
import taskstore as ts   # noqa: E402

FAILURES = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILURES.append(msg)
    return bool(cond)


def isolate(tmp):
    tasks = Path(tmp) / 'tasks'
    tasks.mkdir(parents=True, exist_ok=True)
    ts.TASKS = tasks
    ts.DONE = tasks / 'done'
    ts.QUEUE = tasks / 'QUEUE.md'
    ts.LOCK = tasks / '.lock'
    ts.ATTACH = tasks / 'attachments'
    return tasks


def meta_of(tid):
    return ts.find(tid)[0] or {}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        isolate(tmp)
        tid = ts.add('a task to defer', area='workspace', source='dsh-harness')

        # 1. A DATE CAN BE SET, and it lands where session_start.py looks for it.
        ok = ts.set_wake(tid, '2026-11-01', who='dsh-harness')
        check(ok is True, 'set_wake stores a date')
        check(meta_of(tid).get('wake') == '2026-11-01', 'the date round-trips through the front matter')

        # 2. IT IS A NOTE TOO. A deferral with no note is a task that quietly stops being looked at.
        body = ts.find(tid)[1]
        check('2026-11-01' in body and 'dsh-harness' in body,
              'setting a wake date leaves a dated, attributed note')

        # 3. A BAD DATE IS REFUSED, not stored. This is the whole reason it is a command.
        for bad in ('November', '2026-13-01', '01/11/2026', '2026-11-31', ''):
            try:
                got = ts.set_wake(tid, bad, who='dsh-harness')
            except (ValueError, TypeError):
                got = False
            check(got is not True, f'{bad!r} is refused rather than stored')
        check(meta_of(tid).get('wake') == '2026-11-01', 'a refused date left the stored one alone')

        # 4. A CLEAR REMOVES THE KEY, not just its value: `wake:` with nothing after it is a field a
        #    reader can see and misread, while session_start.py's regex silently ignores it.
        ts.clear_wake(tid, who='dsh-harness')
        check('wake' not in meta_of(tid), 'clear_wake removes the key entirely')
        check('wake' not in ts.find(tid)[1] or 'Wake cleared' in ts.find(tid)[1],
              'clearing leaves a note saying it was cleared')

        # 5. IT IS NOT A SCHEDULE. Nothing here starts, runs or queues anything -- the only effect is
        #    a front-matter date. Guard against someone later wiring a timer into this.
        check(ts.find(tid)[0].get('status') == 'queued', 'setting a wake date does not change status')

        # 6. AN UNKNOWN TASK IS FALSE, not an exception and not a silent success.
        check(ts.set_wake('t999999', '2026-11-01') is False, 'an unknown task returns False')

    print()
    print('  %d failure(s)' % len(FAILURES))
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
