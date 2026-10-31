'''t1055: group open backlog items into batches by shared target file.

Run: python3 scripts/tests/test_batch_scoping.py

Red on main for ONE reason: scripts/scope_queue.py has no MAX_BATCH,
shared_targets() or build_batches(), so these assertions raise AttributeError.
No pytest, no model calls, no subprocess, no writes to the repository.
'''
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]          # repo root, not scripts/
sys.path.insert(0, str(ROOT / 'scripts'))                   # scope_queue's own bootstrap
import scope_queue as sq                                    # noqa: E402

# Real repository paths as target files -- three DISTINCT constants, never one
# placeholder repeated, so a batch that cannot tell two files apart fails.
BRIDGE = 'scripts/bridge.py'
SCOPE_QUEUE = 'scripts/scope_queue.py'
SCOPING_LANES = 'scripts/scoping_lanes.py'


def tasks():
    return [
        {'id': 't1', 'files': [SCOPE_QUEUE]},
        {'id': 't2', 'files': [SCOPE_QUEUE]},
        {'id': 't3', 'files': [SCOPE_QUEUE, BRIDGE]},
        {'id': 't4', 'files': [BRIDGE]},
        {'id': 't5', 'files': [SCOPING_LANES]},
    ]


def test_max_batch_is_five():
    assert sq.MAX_BATCH == 5, sq.MAX_BATCH


def test_exposes_the_public_api():
    for name in ('MAX_BATCH', 'shared_targets', 'build_batches'):
        assert hasattr(sq, name), name


def test_shared_targets_lists_only_multi_touch_files():
    shared = sq.shared_targets(tasks())
    assert set(shared) == {SCOPE_QUEUE, BRIDGE}, shared
    assert SCOPING_LANES not in shared, shared          # single-touch file is NOT shared
    assert shared[SCOPE_QUEUE] == ['t1', 't2', 't3'], shared
    assert shared[BRIDGE] == ['t3', 't4'], shared


def test_batch_groups_the_tasks_that_share_a_file():
    assert sq.build_batches(tasks()) == [
        {'task_ids': ['t1', 't2', 't3', 't4'],
         'files': [BRIDGE, SCOPE_QUEUE],
         'saved_reads': 3},
        {'task_ids': ['t5'], 'files': [], 'saved_reads': 0},
    ], sq.build_batches(tasks())


def test_three_tasks_sharing_one_file_save_two_reads():
    src = [{'id': 't1', 'files': [BRIDGE]},
           {'id': 't2', 'files': [BRIDGE]},
           {'id': 't3', 'files': [BRIDGE]}]
    assert sq.build_batches(src) == [
        {'task_ids': ['t1', 't2', 't3'], 'files': [BRIDGE], 'saved_reads': 2}
    ], sq.build_batches(src)


def test_batch_never_exceeds_five_and_drops_no_task():
    src = [{'id': 't%02d' % i, 'files': [SCOPE_QUEUE]} for i in range(1, 13)]
    batches = sq.build_batches(src)
    assert [b['saved_reads'] for b in batches] == [4, 4, 1], batches
    assert [b['task_ids'] for b in batches] == [
        ['t01', 't02', 't03', 't04', 't05'],
        ['t06', 't07', 't08', 't09', 't10'],
        ['t11', 't12'],
    ], batches
    assert all(len(b['task_ids']) <= sq.MAX_BATCH for b in batches), batches


def test_tasks_that_share_nothing_are_never_paired():
    src = [{'id': 'b1', 'files': [SCOPING_LANES]},
           {'id': 'a1', 'files': []},
           {'id': 'a0', 'files': [BRIDGE]}]
    assert sq.build_batches(src) == [
        {'task_ids': ['a0'], 'files': [], 'saved_reads': 0},
        {'task_ids': ['a1'], 'files': [], 'saved_reads': 0},
        {'task_ids': ['b1'], 'files': [], 'saved_reads': 0},
    ], sq.build_batches(src)


def test_build_batches_does_not_mutate_its_input():
    src = tasks()
    sq.build_batches(src)
    assert src == tasks(), src


def _run():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print('ok   ' + name)
        except Exception as exc:
            failed += 1
            print('FAIL ' + name + ': ' + type(exc).__name__ + ': ' + str(exc))
    print('%d/%d passed' % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_run())
