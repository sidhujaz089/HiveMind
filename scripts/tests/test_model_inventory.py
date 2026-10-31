'''Oracle for t810 -- record a ~/models inventory row at session start.

Red on main for one reason: scripts/session_start.py defines none of the eight
names in WANTED below, so every test reports MISSING-CHANGE and the runner exits
1. The behaviour pinned is the one the RCA finding needs
(workspace/rca/6bc783739c45.md): for each entry found in ~/models, one row saying
what that entry IS -- kind, executable bit, PATH status, opened flag -- appended
to workspace/model-inventory.jsonl, so a negative claim about an entry nobody
opened ('isn't a command on the PATH. I didn't look inside it') can be checked
after the session that made it has ended.

API the brief defines, all in scripts/session_start.py:
    MODELS_DIR, INVENTORY_COLUMNS, INVENTORY_PATH
    classify_model_entry(path) -> str
    model_on_path(name, entry_path) -> bool
    model_inventory_rows(models_dir, session, emitter, at=None, opened=()) -> list[dict]
    append_model_inventory(rows, inventory_path) -> int
    model_inventory_main(argv=None) -> int

Read by AST slice, not by import: scripts/session_start.py is the session
briefing banner and its module-level code is not the subject of this test, so a
slice that pulls out only the names above cannot be dragged by anything the
banner does. That keeps the red reason exactly the missing change.
'''
import ast
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SESSION_START = ROOT / 'scripts' / 'session_start.py'

WANTED = ('MODELS_DIR', 'INVENTORY_COLUMNS', 'INVENTORY_PATH',
          'classify_model_entry', 'model_on_path', 'model_inventory_rows',
          'append_model_inventory', 'model_inventory_main')

HEADER = (
    'import hashlib\nimport json\nimport os\nimport shutil\nimport sys\n'
    'from datetime import datetime, timezone\nfrom pathlib import Path\n'
)

ROW_KEYS = {'at', 'session', 'name', 'path', 'kind', 'executable',
            'on_path', 'opened', 'sha256', 'emitter'}
KIND_SET = {'binary', 'dir', 'link', 'file'}
AT = '2026-10-09T18:18:08Z'


def _slice_namespace():
    '''Exec only the WANTED top-level defs and constants from scripts/session_start.py.

    Each new function must be self-contained (it imports what it uses inside
    itself), so the slice runs on its own. __file__ is set to the real path so a
    path built from it resolves as it would in the module.
    '''
    src = SESSION_START.read_text(encoding='utf-8')
    tree = ast.parse(src)
    picked = []
    for node in tree.body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if any(n in WANTED for n in names):
            seg = ast.get_source_segment(src, node)
            if seg:
                picked.append(seg)
    ns = {'__name__': 'model_inventory_under_test', '__file__': str(SESSION_START)}
    exec(compile(HEADER + '\n\n'.join(picked), str(SESSION_START), 'exec'), ns)
    return ns


def _api():
    try:
        ns = _slice_namespace()
    except Exception as exc:            # an unreadable file is a finding, not a crash
        return None, 'could not read the inventory code from %s: %s: %s' % (
            SESSION_START, type(exc).__name__, exc)
    absent = [n for n in WANTED if n not in ns]
    if absent:
        return None, ('MISSING-CHANGE: scripts/session_start.py defines none of %s -- no '
                      '~/models inventory row is ever written, which is the finding in '
                      'workspace/rca/6bc783739c45.md' % ', '.join(absent))
    return ns, None


def _population(root):
    '''Names taken from the live ~/models listing (9 entries, read 2026-10-09): directories,
    the plain log file, an executable, a symlink named `hf` -- the entry the finding is about
    -- a broken symlink and a dotfile, so every kind the real population has appears.'''
    d = Path(root)
    (d / 'embeddinggemma-2').mkdir()
    (d / 'hf').mkdir()
    (d / '.cache').mkdir()
    (d / 'eg2-venv.log').write_text('pull log\n')
    exe = d / 'swift-serve'
    exe.write_text('#!/bin/sh\necho serve\n')
    exe.chmod(0o755)
    (d / 'hf' / 'xet.log').write_text('xet\n')
    os.symlink(str(d / 'hf' / 'xet.log'), str(d / 'ple-table-fp8'))
    os.symlink(str(d / 'gone'), str(d / 'swift-w4a16-autoround'))
    return d


def test_api_surface_and_the_ten_inventory_fields():
    ns, why = _api()
    if ns is None:
        return why
    for n in ('classify_model_entry', 'model_on_path', 'model_inventory_rows',
              'append_model_inventory', 'model_inventory_main'):
        if not callable(ns.get(n)):
            return 'scripts/session_start.py: %s is not callable' % n
    cols = tuple(ns.get('INVENTORY_COLUMNS') or ())
    want = ('at', 'session', 'name', 'path', 'kind', 'executable',
            'on_path', 'opened', 'sha256', 'emitter')
    if cols != want:
        return 'INVENTORY_COLUMNS is %r, expected the RCA\'s ten fields in order %r' % (list(cols), list(want))
    inv = Path(str(ns.get('INVENTORY_PATH')))
    if inv.name != 'model-inventory.jsonl' or inv.parent.name != 'workspace':
        return 'INVENTORY_PATH is %r: must end workspace/model-inventory.jsonl' % (inv,)
    if not isinstance(ns.get('MODELS_DIR'), Path):
        return 'MODELS_DIR is %r, expected a Path to ~/models' % (ns.get('MODELS_DIR'),)
    return None


def test_classify_model_entry_separates_the_four_kinds():
    ns, why = _api()
    if ns is None:
        return why
    root = tempfile.mkdtemp(prefix='t810-classify-')
    try:
        d = _population(root)
        got = {}
        got['dir'] = ns['classify_model_entry'](d / 'embeddinggemma-2')
        got['file'] = ns['classify_model_entry'](d / 'eg2-venv.log')
        got['binary'] = ns['classify_model_entry'](d / 'swift-serve')
        got['symlink to a file'] = ns['classify_model_entry'](d / 'ple-table-fp8')
        got['broken symlink'] = ns['classify_model_entry'](d / 'swift-w4a16-autoround')
        (d / 'swift-recipe').mkdir()
        os.symlink(str(d / 'swift-recipe'), str(d / 'vosk'))
        got['symlink to a dir'] = ns['classify_model_entry'](d / 'vosk')
        want = {'dir': 'dir', 'file': 'file', 'binary': 'binary',
                'symlink to a file': 'link', 'broken symlink': 'link',
                'symlink to a dir': 'link'}
        for key in sorted(want):
            if got[key] != want[key]:
                return 'classify_model_entry(%s) = %r, expected %r' % (key, got[key], want[key])
        if len(set(got.values())) != 4:
            return 'the four kinds are not distinct values: %r' % (got,)
        return None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_model_on_path_names_the_entry_and_not_just_its_name():
    ns, why = _api()
    if ns is None:
        return why
    root = tempfile.mkdtemp(prefix='t810-path-')
    saved = os.environ.get('PATH', '')
    try:
        d = Path(root)
        first, second = d / 'first', d / 'second'
        first.mkdir()
        second.mkdir()
        for where in (first, second):
            f = where / 'swift-serve'
            f.write_text('#!/bin/sh\n')
            f.chmod(0o755)
        os.environ['PATH'] = os.pathsep.join([str(first), str(second), saved])
        if ns['model_on_path']('swift-serve', first / 'swift-serve') is not True:
            return 'model_on_path is not True for the entry PATH resolves the name to'
        if ns['model_on_path']('swift-serve', second / 'swift-serve') is not False:
            return ('model_on_path True for second/swift-serve while PATH resolves the name to '
                    'first/swift-serve: the finding\'s claim is about one specific entry, not about '
                    'the name existing somewhere on PATH')
        os.environ['PATH'] = str(d / 'empty-dir')
        if ns['model_on_path']('swift-serve', first / 'swift-serve') is not False:
            return 'model_on_path True when no PATH directory contains the name'
        return None
    finally:
        os.environ['PATH'] = saved
        shutil.rmtree(root, ignore_errors=True)


def test_model_inventory_rows_record_one_row_per_entry_with_the_ten_fields():
    ns, why = _api()
    if ns is None:
        return why
    root = tempfile.mkdtemp(prefix='t810-rows-')
    try:
        d = _population(root)
        rows = [dict(r) for r in ns['model_inventory_rows'](
            d, session='s-810', emitter='test:session_start', at=AT)]
        names = sorted(str(r.get('name')) for r in rows)
        want_names = sorted(['embeddinggemma-2', 'hf', '.cache', 'eg2-venv.log',
                             'swift-serve', 'ple-table-fp8', 'swift-w4a16-autoround'])
        if names != want_names:
            return ('rows cover %r, expected %r -- one row per entry found, dotfiles included'
                    % (names, want_names))
        by = {}
        for r in rows:
            if set(r) != ROW_KEYS:
                return ('row %s has keys %r, expected exactly the ten inventory fields %r'
                        % (r.get('name'), sorted(r), sorted(ROW_KEYS)))
            by[str(r['name'])] = r
            if r['at'] != AT:
                return 'at not carried through to the row: %r' % (r['at'],)
            if r['session'] != 's-810':
                return 'session not carried through to the row: %r' % (r['session'],)
            if r['emitter'] != 'test:session_start':
                return 'emitter not carried through to the row: %r' % (r['emitter'],)
            if r['kind'] not in KIND_SET:
                return 'kind %r is not one of %r' % (r['kind'], sorted(KIND_SET))
            if not Path(str(r['path'])).is_absolute():
                return 'path is not absolute: %r' % (r['path'],)
        want_kind = {'embeddinggemma-2': 'dir', 'hf': 'dir', '.cache': 'dir',
                     'eg2-venv.log': 'file', 'swift-serve': 'binary',
                     'ple-table-fp8': 'link', 'swift-w4a16-autoround': 'link'}
        for name in sorted(want_kind):
            if by[name]['kind'] != want_kind[name]:
                return 'kind of %s = %r, expected %r' % (name, by[name]['kind'], want_kind[name])
        if by['swift-serve']['executable'] is not True:
            return 'executable is not True for a 0755 regular file'
        for name in ('embeddinggemma-2', 'hf', '.cache', 'eg2-venv.log',
                     'ple-table-fp8', 'swift-w4a16-autoround'):
            if by[name]['executable'] is not False:
                return ('executable True for %s: only a regular file carrying the user-execute bit '
                        'is executable -- a directory and a symlink are not' % name)
        digest = hashlib.sha256(b'pull log\n').hexdigest()
        if by['eg2-venv.log']['sha256'] != digest:
            return ('sha256 of eg2-venv.log = %r, expected the file\'s own digest %s'
                    % (by['eg2-venv.log']['sha256'], digest))
        for name in ('embeddinggemma-2', 'hf', 'ple-table-fp8'):
            if by[name]['sha256'] is not None:
                return ('sha256 of %s = %r, expected null: it is not a regular file'
                        % (name, by[name]['sha256']))
        for r in rows:
            if r['opened'] is not False:
                return ('opened defaults to True on row %s: at session start nothing has been '
                        'opened, and the finding\'s negative claim is only checkable if the entries '
                        'nobody opened say so' % r['name'])
        return None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_opened_flag_names_what_the_endpoint_actually_opened():
    ns, why = _api()
    if ns is None:
        return why
    root = tempfile.mkdtemp(prefix='t810-opened-')
    try:
        d = _population(root)
        rows = [dict(r) for r in ns['model_inventory_rows'](
            d, session='s', emitter='e', at=AT, opened=['swift-serve'])]
        by = {str(r['name']): r for r in rows}
        if by['swift-serve']['opened'] is not True:
            return 'opened is not True for the entry named in opened='
        if by['eg2-venv.log']['opened'] is not False:
            return 'opened True for an entry NOT named in opened='
        if not isinstance(by['swift-serve']['opened'], bool):
            return 'opened is %r, must be a JSON boolean' % (by['swift-serve']['opened'],)
        return None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_caller_that_passes_no_stamp_still_stamps_the_row():
    ns, why = _api()
    if ns is None:
        return why
    root = tempfile.mkdtemp(prefix='t810-at-')
    try:
        d = _population(root)
        rows = [dict(r) for r in ns['model_inventory_rows'](d, session='s', emitter='e')]
        for r in rows:
            stamp = str(r['at'])
            if not stamp.endswith('Z'):
                return 'at = %r, expected a Z-suffixed UTC stamp like %s' % (stamp, AT)
            try:
                datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ')
            except ValueError as exc:
                return 'at = %r is not %s: %s' % (stamp, '%Y-%m-%dT%H:%M:%SZ', exc)
        return None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_append_model_inventory_appends_and_keeps_earlier_rows():
    ns, why = _api()
    if ns is None:
        return why
    root = tempfile.mkdtemp(prefix='t810-append-')
    try:
        d = _population(root)
        inv = Path(root) / 'model-inventory.jsonl'
        prior = '{"name": "from-a-previous-session"}'
        inv.write_text(prior + '\n')
        rows = [dict(r) for r in ns['model_inventory_rows'](d, session='s', emitter='e', at=AT)]
        n = ns['append_model_inventory'](rows, inv)
        lines = inv.read_text(encoding='utf-8').splitlines()
        if lines[:1] != [prior]:
            return 'append_model_inventory rewrote the file: the prior row is gone (%r)' % (lines[:1],)
        if len(lines) != 1 + len(rows):
            return ('the inventory has %d lines, expected %d (1 prior + %d new) -- session rows '
                    'accumulate' % (len(lines), 1 + len(rows), len(rows)))
        if n != len(rows):
            return 'append_model_inventory returned %r, expected %d' % (n, len(rows))
        for line in lines[1:]:
            try:
                row = json.loads(line)
            except ValueError as exc:
                return 'appended line is not one JSON object: %r (%s)' % (line[:60], exc)
            if set(row) != ROW_KEYS:
                return ('appended row keys %r, expected exactly the ten inventory fields %r'
                        % (sorted(row), sorted(ROW_KEYS)))
        return None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_writer_appends_one_row_per_entry_to_the_path_it_is_given():
    ns, why = _api()
    if ns is None:
        return why
    root = tempfile.mkdtemp(prefix='t810-main-')
    try:
        models = Path(root) / 'models'
        models.mkdir()
        (models / 'vosk').mkdir()
        recipe = models / 'swift-recipe'
        recipe.write_text('#!/bin/sh\n')
        recipe.chmod(0o755)
        inv = Path(root) / 'model-inventory.jsonl'
        argv = ['--models-dir', str(models), '--out', str(inv),
                '--session', 's-cli', '--emitter', 'test:model_inventory_main']
        if ns['model_inventory_main'](argv) != 0:
            return 'model_inventory_main returned non-zero for a clean run'
        lines = inv.read_text(encoding='utf-8').splitlines()
        if len(lines) != 2:
            return 'the writer wrote %d rows, expected 2 (vosk, swift-recipe)' % len(lines)
        if ns['model_inventory_main'](argv) != 0:
            return 'the second model_inventory_main call returned non-zero'
        lines = inv.read_text(encoding='utf-8').splitlines()
        if len(lines) != 4:
            return ('the second run replaced the file: %d rows total, expected 4 -- session-start '
                    'rows must accumulate, not overwrite' % len(lines))
        kinds = []
        for line in lines:
            row = json.loads(line)
            if row['session'] != 's-cli' or row['emitter'] != 'test:model_inventory_main':
                return 'row lost the session/emitter it was called with: %r' % (row,)
            if row['opened'] is not False:
                return 'a session-start row claims something was opened: %r' % (row,)
            kinds.append(row['kind'])
        if sorted(kinds) != sorted(['dir', 'binary'] * 2):
            return 'the writer recorded kinds %r, expected dir and binary twice' % (kinds,)
        return None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _main():
    tests = sorted((n, fn) for n, fn in list(globals().items())
                   if n.startswith('test_') and callable(fn))
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
        except Exception as exc:
            detail = 'raised %s: %s' % (type(exc).__name__, exc)
        if detail:
            failed += 1
            print('FAIL %s -- %s' % (name, detail))
        else:
            print('ok   %s' % name)
    print('%d passed, %d failed of %d' % (len(tests) - failed, failed, len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_main())
