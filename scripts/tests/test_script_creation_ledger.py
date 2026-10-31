#!/usr/bin/env python3
'''t819 fail-first oracle: script creation is recorded AT CREATION, with a required flow node.

Field case (workspace/rca/73f5fdbc079f.md): a model-pull script was written into a scratch
directory, ran, and finished, and left no repo trace. The census walks the repository tree
only (scripts/script_census.py, function scripts()), so a script written outside that tree
is invisible to it -- and so is every other post-hoc check (git, systemd, test suites). The
fix is a creation endpoint: when a session writes a script, workspace/script-creations.jsonl
gains a row carrying {path, session, actor, ts, purpose, flow_node, task}, and a write whose
flow_node is empty is REFUSED -- so a session running an unregistered script cannot be
written down at all.

RED ON MAIN FOR ONE REASON: scripts/script_census.py defines no record, load_ledger,
Refused or LEDGER today (search_repo for script_create matches no .py file), so every test
below fails inside _have(), naming the missing name. It is not red for its own bug: it writes
no live state (every write goes to a temp-dir ledger passed as ledger=), and it uses no
subprocess, no network, no model call, no fixed /tmp path and no pytest.

Run: python3 scripts/tests/test_script_creation_ledger.py   (pytest is not installed)
'''
import importlib.util
import io
import json
import sys
import tempfile
from contextlib import redirect_stderr
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / 'scripts' / 'script_census.py'
REQUIRED = ('path', 'session', 'actor', 'ts', 'purpose', 'flow_node', 'task')
# Distinct real values from the finding and its neighbour: two scripts, two purposes, two
# flow nodes, two tasks, two actors. One repeated placeholder lets a stub pass (oracle-weak).
PULL = 'scratch/pull-qwen3.8.sh'
RESUME = 'scratch/resume-coder-download.sh'
NODE_PULL = 'models/pull'
NODE_RESUME = 'workers/resume'
TASK = 't819'
TASK_OTHER = 't743'
SESSION = '6cc34e3b-0c16-4f3b-8f78-98893304830f'
ACTOR = 'claude'
ACTOR_OTHER = 'dsh-harness'
PURPOSE = 'pull the qwen3.8 model'


def _have(*names):
    assert MODULE_PATH.is_file(), 'missing change: %s is not a file' % (MODULE_PATH,)
    spec = importlib.util.spec_from_file_location('t819_script_census', MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in names:
        assert hasattr(mod, name), 'missing change: scripts/script_census.py defines no %r (t819 adds it)' % (name,)
    return mod


def test_record_writes_the_row_the_finding_needed():
    # The row that did not exist for the qwen3.8 pull: all seven fields, verbatim, on disk.
    mod = _have('record', 'load_ledger', 'Refused', 'LEDGER')
    assert issubclass(mod.Refused, Exception), 'Refused must be an exception, not a return code'
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / 'script-creations.jsonl'
        row = mod.record(PULL, purpose=PURPOSE, flow_node=NODE_PULL, task=TASK,
                         session=SESSION, actor=ACTOR, ledger=str(ledger))
        assert ledger.is_file(), 'record() created no ledger at %s' % (ledger,)
        lines = [ln for ln in ledger.read_text(encoding='utf-8').splitlines() if ln.strip()]
        assert len(lines) == 1, 'expected exactly 1 row on a fresh ledger, got %d: %r' % (len(lines), lines)
        stored = json.loads(lines[0])
        assert set(stored) == set(REQUIRED), 'row keys %r, expected exactly %r' % (sorted(stored), sorted(REQUIRED))
        for key, want in (('path', PULL), ('flow_node', NODE_PULL), ('task', TASK),
                          ('session', SESSION), ('actor', ACTOR), ('purpose', PURPOSE)):
            assert stored.get(key) == want, 'row[%r] == %r, want %r' % (key, stored.get(key), want)
        stamp = stored['ts']
        assert isinstance(stamp, str) and stamp.strip(), 'row[ts] must be a non-empty stamp, got %r' % (stamp,)
        datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        assert row == stored, 'record() returned %r, which is not the row it wrote %r' % (row, stored)


def test_empty_flow_node_is_refused_and_leaves_no_row():
    # The class is unstateable: a write with no flow node is refused and writes nothing.
    mod = _have('record', 'Refused')
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / 'script-creations.jsonl'
        for blank in ('', '   ', chr(9) + chr(10)):
            try:
                mod.record(PULL, purpose=PURPOSE, flow_node=blank, task=TASK,
                           session=SESSION, actor=ACTOR, ledger=str(ledger))
            except mod.Refused:
                continue
            raise AssertionError('flow_node %r was accepted; the write must be refused' % (blank,))
        assert not ledger.exists(), 'a refused write created the ledger file at %s' % (ledger,)
        # A non-blank flow node of the same shape is NOT refused: no blanket-refuse stub passes.
        mod.record(PULL, purpose=PURPOSE, flow_node=NODE_PULL, task=TASK,
                   session=SESSION, actor=ACTOR, ledger=str(ledger))
        assert ledger.is_file(), 'the first non-blank flow_node must be accepted'
        kept = [ln for ln in ledger.read_text(encoding='utf-8').splitlines() if ln.strip()]
        assert len(kept) == 1, 'the accepted write must append exactly one row, got %r' % (kept,)
        before = ledger.read_text(encoding='utf-8')
        for empty_path in ('', '   '):
            try:
                mod.record(empty_path, purpose=PURPOSE, flow_node=NODE_PULL, task=TASK,
                           session=SESSION, actor=ACTOR, ledger=str(ledger))
            except mod.Refused:
                continue
            raise AssertionError('script path %r was accepted; the row would record nothing' % (empty_path,))
        assert ledger.read_text(encoding='utf-8') == before, 'a refused write appended a row to the ledger'


def test_two_scripts_are_distinguishable_in_a_later_census():
    # Downstream use: a census reads the ledger back, tells two scripts apart, and reports
    # a torn line instead of swallowing it.
    mod = _have('record', 'load_ledger')
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / 'script-creations.jsonl'
        mod.record(PULL, purpose=PURPOSE, flow_node=NODE_PULL, task=TASK,
                   session=SESSION, actor=ACTOR, ledger=str(ledger))
        mod.record(RESUME, purpose='resume a coder download', flow_node=NODE_RESUME,
                   task=TASK_OTHER, session=SESSION, actor=ACTOR_OTHER, ledger=str(ledger))
        rows = mod.load_ledger(str(ledger))
        assert isinstance(rows, list) and len(rows) == 2, 'expected 2 rows, got %r' % (rows,)
        by_path = {r['path']: r for r in rows}
        assert set(by_path) == {PULL, RESUME}, 'a census keyed by path lost a script: %r' % (sorted(by_path),)
        assert by_path[PULL]['flow_node'] == NODE_PULL, 'the flow node must round-trip verbatim'
        assert by_path[RESUME]['flow_node'] == NODE_RESUME, 'two scripts must carry two flow nodes'
        assert by_path[RESUME]['task'] == TASK_OTHER, 'two scripts must carry two tasks'
        assert by_path[RESUME]['actor'] == ACTOR_OTHER, 'two scripts must carry two actors'
        assert sum(1 for r in rows if r['flow_node'] == NODE_RESUME) == 1, \
            'filtering by flow node must return exactly the scripts on that node'
        # The finding itself: the disk walk the census uses cannot report a scratch script.
        repo_scripts = {str(p) for p in mod.scripts()}
        assert [p for p in repo_scripts if p.endswith('pull-qwen3.8.sh')] == [], \
            'the repository walk must not report the scratch script: the ledger is its only record'
        # A reader that keeps only the first line, or that drops a torn line in silence, fails here.
        lines = [json.dumps(r) for r in rows]
        lines.append('')
        lines.append(lines[0][:18])
        ledger.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        buf = io.StringIO()
        with redirect_stderr(buf):
            kept = mod.load_ledger(str(ledger))
        assert [r['path'] for r in kept] == [PULL, RESUME], \
            'a blank and a torn line must not cost the intact rows: %r' % (kept,)
        note = buf.getvalue()
        assert ('torn' in note.lower()) or ('skipped' in note.lower()) or ('unparseable' in note.lower()), \
            'an unparseable ledger line must be REPORTED, never skipped silently: %r' % (note,)
        assert '4' in note, 'the report must name the line number it could not parse: %r' % (note,)


def test_ledger_is_the_repo_file_the_census_reads():
    # The default target is the file the task names, and an empty history reads as empty.
    mod = _have('LEDGER', 'load_ledger')
    target = Path(str(mod.LEDGER))
    assert target.name == 'script-creations.jsonl', 'LEDGER must be the file named in the task, got %r' % (mod.LEDGER,)
    assert target.parent == ROOT / 'workspace', 'LEDGER must live in the repo workspace/, got %r' % (target.parent,)
    assert mod.load_ledger(str(ROOT / 'workspace' / 'no-such-creations.jsonl')) == [], \
        'load_ledger of an absent ledger must return [], not raise: the first session has none'


TESTS = (
    test_record_writes_the_row_the_finding_needed,
    test_empty_flow_node_is_refused_and_leaves_no_row,
    test_two_scripts_are_distinguishable_in_a_later_census,
    test_ledger_is_the_repo_file_the_census_reads,
)

if __name__ == '__main__':
    failed = 0
    for case in TESTS:
        try:
            case()
        except Exception as exc:
            failed += 1
            print('FAIL %s: %s: %s' % (case.__name__, type(exc).__name__, exc))
        else:
            print('ok   %s' % case.__name__)
    print('%d/%d passed' % (len(TESTS) - failed, len(TESTS)))
    sys.exit(1 if failed else 0)
