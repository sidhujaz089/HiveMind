#!/usr/bin/env python3
'''t715 -- Chat's intake route into the Review tab (Jas, 2026-10-06 and 2026-10-07).

RED ON MAIN FOR ONE REASON: class Workspace in scripts/workspace_mcp/core.py has no
review_item and no review_resolve -- grep -rn "review_item" --include=*.py . returns no
source hit anywhere in the repository -- so every test here dies on AttributeError before
it asserts anything. Nothing fails for a reason of its own: no subprocess, no urlopen, no
model call, no live server, no fixed /tmp path. Each test builds its own tempfile.mkdtemp
workspace and tears it down in a finally.

Jas, 2026-10-07, on this task: "I want the MCP connection to let chat make review items
for me. I think they just need to be able to be marked as done and they go away. Chat
should also be able to mark them as done in case I tell chat what I reviewed." That
REVERSES rule 5 of the task body (no self-closing), which the first scope of this task
(workspace/run-logs/scoping-queue/out/t715.md) pinned the other way. This file is the
corrected contract: a chat caller may close, every other model caller may not.
'''
import importlib.util
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE_PATH = ROOT / 'scripts' / 'workspace_mcp' / 'core.py'

TITLE = 'Keep, change or drop the 30-minute session-capture heartbeat?'
DECISION = 'Decide whether the 30-minute Chat session-capture heartbeat stays as it is'
OPTIONS = 'keep it / change to 60 minutes / drop the heartbeat entirely'
DEFAULT = 'leave it as it is and revisit at the next weekly review'
SOURCE = 'workspace/tasks/t715--give-claude-chat-a-way-to-put-items-that-need-ja.md:51'
OTHER_SOURCE = 'chat/projects/family/INSTRUCTIONS.txt:12'
DATE = '2026-10-07'
CHAT_MARKER = '[chat-review]'
RELAYED = '(via Chat, relaying Jas) '


def _core():
    sys.path.insert(0, str(CORE_PATH.parent))
    spec = importlib.util.spec_from_file_location('t715_core', CORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CORE = _core()


def _ws(caller='claude-chat'):
    root = Path(tempfile.mkdtemp(prefix='t715-'))
    (root / 'workspace' / 'tasks').mkdir(parents=True)
    return CORE.Workspace(root, caller=caller, log_path=root / 'mcp-calls.jsonl'), root


def _files(root):
    return sorted(p.name for p in (root / 'workspace' / 'tasks').glob('t*--*.md'))


def _rows(root):
    p = root / 'mcp-calls.jsonl'
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]


def _ts():
    sys.path.insert(0, str(ROOT / 'projects' / 'app-repo'))
    import taskstore
    return taskstore


def _listed(ws, who='jas'):
    return json.dumps(ws.my_tasks(who), default=str)


def test_filed_item_records_every_required_field():
    '''Six fields, read back off DISK through taskstore's own API. A return value is a
    promise; the file is the record. Each value is asserted on its own, so a build that
    writes the title prettily and drops the decision, the options, the default, the source
    or the date fails here.'''
    ws, root = _ws()
    try:
        res = ws.review_item(TITLE, DECISION, options=OPTIONS, default=DEFAULT,
                             source=SOURCE, date=DATE)
        tid = res.get('id')
        assert isinstance(tid, str) and tid.startswith('t'), f'no task id came back: {res!r}'
        assert res.get('created') is True, f'a first filing must create: {res!r}'
        assert res.get('duplicate') is False, f'a first filing is not a duplicate: {res!r}'
        assert res.get('assigned') == 'jas', f'an intake item is held for Jas: {res!r}'

        ts = _ts()
        meta, body = ts.find(tid)
        assert meta, f'{tid} is not a task on disk: {ts.find(tid)!r}'
        assert meta.get('title') == TITLE, f'title not stored: {meta!r}'
        assert ts.assignee(meta.get('needs', meta.get('assigned'))) == 'jas', \
            f'not held for Jas, so the Review tab will not list it: {meta!r}'
        for label, value in (('title', TITLE), ('decision', DECISION), ('options', OPTIONS),
                             ('default', DEFAULT), ('source', SOURCE), ('date', DATE)):
            assert value in body, f'the body does not carry the {label}: {body[:400]}'
        for label in ('Decision:', 'Options:', 'Default:', 'Source:', 'Date:'):
            assert label in body, f'fields must be labelled for a phone read, no {label}: {body[:400]}'
        assert CHAT_MARKER in body, f'no Chat label, so the Review-tab pills cannot count it: {body[:400]}'
        assert RELAYED not in body, 'the Chat ask written in the voice of Jas (task_note hardcodes that prefix)'
        assert 'someone should think about' not in body.lower(), body[:400]

        rows = _rows(root)
        assert any(r.get('tool') == 'review_item' and r.get('outcome') == 'ok' for r in rows), \
            f'the filing was not logged: {rows!r}'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_filed_item_is_listed_in_jas_review_list():
    '''The value is USED downstream. A method that files a file the Review tab's own source
    does not list satisfies every return-shape assertion and delivers nothing.'''
    ws, root = _ws()
    try:
        tid = ws.review_item(TITLE, DECISION, default=DEFAULT, source=SOURCE, date=DATE)['id']
        listing = _listed(ws, 'jas')
        assert tid in listing, f'{tid} filed with needs=jas is not listed by my_tasks("jas"): {listing[:400]}'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_incomplete_item_is_refused_and_files_nothing():
    '''Rule 3: no decision and no default is a refusal, not a vague item.'''
    ws, root = _ws()
    try:
        before = _files(root)
        try:
            ws.review_item(TITLE, '', options='', default='', source=SOURCE, date=DATE)
        except CORE.Denied as e:
            assert 'decision' in str(e).lower(), f'the refusal must name what is missing, said: {e}'
        else:
            raise AssertionError('an item with no decision and no default was filed')
        after = _files(root)
        assert after == before, f'a refused item left a file behind: {set(after) - set(before)}'
        rows = _rows(root)
        assert any(r.get('tool') == 'review_item' and r.get('outcome') == 'refused' for r in rows), \
            f'the refusal was not logged: {rows!r}'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_decision_without_a_default_is_filed():
    '''The rule is "no decision AND no default". Requiring both is stricter than Jas asked
    and silently drops half of the Chat items.'''
    ws, root = _ws()
    try:
        res = ws.review_item(TITLE, DECISION, options='', default='', source=SOURCE, date=DATE)
        assert res.get('created') is True, f'a stated decision is enough to file: {res!r}'
        assert len(_files(root)) == 1, _files(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_default_without_a_decision_is_filed():
    '''The symmetric half: the default alone tells Jas what happens if he does nothing.'''
    ws, root = _ws()
    try:
        res = ws.review_item(TITLE, '', options='', default=DEFAULT, source=SOURCE, date=DATE)
        assert res.get('created') is True, f'a stated default is enough to file: {res!r}'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_filing_the_same_item_twice_returns_the_existing_one():
    '''Rule 4. The id, not just a result, has to come back -- a second id is a second item.'''
    ws, root = _ws()
    try:
        first = ws.review_item(TITLE, DECISION, options=OPTIONS, default=DEFAULT,
                               source=SOURCE, date=DATE)
        n = len(_files(root))
        second = ws.review_item(TITLE, DECISION, options=OPTIONS, default=DEFAULT,
                                source=SOURCE, date=DATE)
        assert second.get('duplicate') is True, f'not reported as a duplicate: {second!r}'
        assert second.get('created') is False, f'a duplicate was created: {second!r}'
        assert second.get('id') == first.get('id'), f'the duplicate returned a new item: {first!r} then {second!r}'
        assert len(_files(root)) == n, f'the duplicate wrote a second file: {_files(root)}'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_same_title_different_source_is_not_a_duplicate():
    '''The key is (title, source). Keying on title alone files one item per idea forever.'''
    ws, root = _ws()
    try:
        a = ws.review_item(TITLE, DECISION, default=DEFAULT, source=SOURCE, date=DATE)
        b = ws.review_item(TITLE, DECISION, default=DEFAULT, source=OTHER_SOURCE, date=DATE)
        assert a.get('created') is True and b.get('created') is True, f'{a!r} / {b!r}'
        assert b.get('duplicate') is False, f'distinct sources collapsed: {b!r}'
        assert a.get('id') != b.get('id'), f'same id for two sources: {a!r} / {b!r}'
        assert len(_files(root)) == 2, _files(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_chat_can_mark_it_done_for_jas():
    '''Jas, 2026-10-07, reversing rule 5: "Chat should also be able to mark them as done in
    case I tell chat what I reviewed." Asserted on the ITEM, not the return value: the
    assignee changes and the item leaves his list -- "they go away" is the ask.'''
    ws, root = _ws(caller='claude-chat')
    try:
        tid = ws.review_item(TITLE, DECISION, default=DEFAULT, source=SOURCE, date=DATE)['id']
        res = ws.review_resolve(tid, by='claude-chat')
        assert res.get('released') is True, f'a chat caller was not allowed to close it: {res!r}'
        assert res.get('closed_by') == 'claude-chat', f'the close must name who did it: {res!r}'

        ts = ws._ts()
        meta, body = ts.find(tid)
        assert meta, f'{tid} vanished; a close is a hand-back, not a delete'
        assert ts.assignee(meta.get('needs', meta.get('assigned'))) == ts.PRODUCTION, \
            f'after the Chat close it is still in the Jas list: {meta!r}'
        assert tid not in _listed(ws, 'jas'), 'it is marked done and still shows on the Review tab'
        assert 'claude-chat' in body, f'a close made by Chat must record who closed it, for audit: {body[:400]}'
        rows = _rows(root)
        assert any(r.get('tool') == 'review_resolve' and r.get('outcome') == 'ok' for r in rows), \
            f'the close was not logged: {rows!r}'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_an_unauthorised_caller_cannot_close_it():
    '''The part of rule 5 Jas did NOT waive: a model gets to ask, not to decide. Only Jas, and
    Chat relaying him, take an item off his list.'''
    ws, root = _ws(caller='q16')
    try:
        tid = ws.review_item(TITLE, DECISION, default=DEFAULT, source=SOURCE, date=DATE)['id']
        for who in ('q16', 'production', 'session-review', 'claude', ''):
            try:
                ws.review_resolve(tid, by=who)
            except CORE.Denied as e:
                assert 'not' in str(e).lower() or 'refus' in str(e).lower(), \
                    f'the refusal for by={who!r} must say why: {e}'
            else:
                raise AssertionError(f'caller {who!r} cleared a review item it had no right to close')
        ts = ws._ts()
        meta, _body = ts.find(tid)
        assert meta, f'{tid} vanished after a refused close'
        assert ts.assignee(meta.get('needs', meta.get('assigned'))) == 'jas', \
            f'a refused close moved the item out of the Jas list: {meta!r}'
        assert tid in _listed(ws, 'jas'), 'a refused close still took it off the tab'
        rows = _rows(root)
        assert any(r.get('tool') == 'review_resolve' and r.get('outcome') == 'refused' for r in rows), \
            f'the refused closes were not logged: {rows!r}'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_review_tools_are_declared_on_both_transports():
    '''The tools Chat can call are these tuples (core.py). An implementation that adds the
    methods to the class and not to the transport is invisible from Chat, which is the whole
    ask, and no return-shape assertion would notice.'''
    for name in ('review_item', 'review_resolve'):
        assert name in CORE.STDIO_TOOLS, f'{name} is not in STDIO_TOOLS'
        assert name in CORE.HTTP_TOOLS, f'{name} is not in HTTP_TOOLS'


def main():
    # _ts() mutates the SHARED taskstore module's path globals. Snapshot before, restore in the
    # finally, so no exit path -- pass, fail or crash -- leaves the repo's tasks pointed at a
    # temp dir (oracle-dirty: running a suite must not change the worktree).
    sys.path.insert(0, str(ROOT / 'projects' / 'app-repo'))
    snap = {}
    try:
        import taskstore as _ts_mod
        for attr in ('WORKSPACE', 'TASKS', 'DONE', 'QUEUE', 'LOCK'):
            snap[attr] = getattr(_ts_mod, attr, None)
    except Exception as e:                                      # said, not swallowed
        print(f'NOTE: taskstore pre-import failed ({type(e).__name__}: {e}); '
              f'restoring by attribute presence only')
        snap = {}

    tests = sorted((n, f) for n, f in list(globals().items())
                   if n.startswith('test_') and callable(f))
    failed = []
    try:
        for name, fn in tests:
            try:
                fn()
                print(f'PASS {name}')
            except Exception as e:                              # noqa: BLE001
                failed.append(name)
                print(f'FAIL {name}: {type(e).__name__}: {e}')
                traceback.print_exc()
    finally:
        if snap:
            import taskstore as _ts_mod
            for attr, value in snap.items():
                setattr(_ts_mod, attr, value)

    print(f'{len(tests) - len(failed)}/{len(tests)} passed'
          + (f' -- FAILED: {", ".join(failed)}' if failed else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
