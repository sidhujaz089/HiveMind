#!/usr/bin/env python3
'''t781 oracle: askmemory.py records the model's proposal inside the repo, with its task link.

Run: python3 scripts/tests/test_askmemory_proposal_lands_in_repo.py
RED on main: askmemory.py has no record_proposal() and no --task/--out-dir/--index. Today it
prints the model's answer to stdout only, so a generated proposal leaves no record at all.
'''
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ASKMEM = REPO / 'projects' / 'local-inference' / 'askmemory.py'
ANSWER = '# Memory I want\n\nStore runs and lessons, not chat.\n'
MODEL = 'qwen3.8:27b-mtp-q4_K_M'
REQUIRED = {'at', 'script', 'model', 'task', 'sha256', 'out_path'}
CHAT_SUFFIX = '/api/' + 'chat'


def _stub_settings():
    '''A settings module that answers any call shape, so importing askmemory touches nothing live.

    askmemory.py imports settings at module level. Without this stub the oracle's red would be an
    import error, not the missing change.
    '''
    m = types.ModuleType('settings')
    m.options = lambda *a, **k: {'temperature': 0.2, 'num_predict': 512}
    m.request = lambda *a, **k: {'model': 'stub', 'messages': [], 'stream': False}
    sys.modules.setdefault('settings', m)


def load_askmemory():
    _stub_settings()
    assert ASKMEM.is_file(), 'missing ' + str(ASKMEM)
    sys.path.insert(0, str(ASKMEM.parent))
    spec = importlib.util.spec_from_file_location('askmemory_under_test', ASKMEM)
    if spec is None or spec.loader is None:
        raise AssertionError('cannot load ' + str(ASKMEM))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StubChat:
    '''One canned chat reply. Refuses every request it was not written to answer.'''

    def __init__(self, content=ANSWER):
        self.content = content
        self.calls = []

    def __call__(self, request, timeout=None):
        url = getattr(request, 'full_url', str(request))
        if not url.endswith(CHAT_SUFFIX):
            raise AssertionError('stub got a request it was not written to answer: ' + url)
        assert timeout is not None, 'the model call must carry a timeout='
        self.calls.append(url)
        payload = {'message': {'content': self.content}, 'done_reason': 'stop', 'eval_count': 42}
        return io.BytesIO(json.dumps(payload).encode('utf-8'))


def _rows(path):
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]


def test_record_proposal_writes_the_answer_into_the_repo():
    mod = load_askmemory()
    assert hasattr(mod, 'record_proposal'), (
        'askmemory.py defines no record_proposal(): the answer is printed and dropped, which is '
        'why a proposal generated on 2026-08-26 was unrecordable 33 days later')
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        row = mod.record_proposal(script='askmemory.py', content=ANSWER, model=MODEL,
                                  task='t162', repo=repo)
        files = sorted((repo / 'workspace' / 'proposals').glob('*.md'))
        assert len(files) == 1, 'expected exactly 1 proposal file, got ' + str([str(f) for f in files])
        md = files[0]
        assert md.read_text(encoding='utf-8') == ANSWER, 'the file is not the model answer verbatim'
        parts = md.name.split('--')
        assert len(parts) == 3, 'file name ' + md.name + ' is not <date>--<script>--<slug>.md'
        assert parts[0] == str(row['at'])[:10], 'file date ' + parts[0] + ' is not the row date'
        assert parts[1] == 'askmemory.py', 'file names the script ' + parts[1]
        assert set(row) == REQUIRED, 'row keys ' + str(sorted(row)) + ' are not the six t781 fields'
        assert row['script'] == 'askmemory.py', 'row script is ' + repr(row.get('script'))
        assert row['model'] == MODEL, 'row model is ' + repr(row.get('model'))
        assert row['task'] == 't162', 'row task is ' + repr(row.get('task')) + ' -- the link is the point'
        assert row['sha256'] == hashlib.sha256(ANSWER.encode('utf-8')).hexdigest(), 'sha256 is not the answer written'
        assert row['out_path'] == 'workspace/proposals/' + md.name, 'out_path ' + repr(row.get('out_path')) + ' is not repo-relative'
        assert not Path(row['out_path']).is_absolute(), 'out_path is absolute: a proposal outside the repo'
        assert (repo / row['out_path']).is_file(), 'out_path does not name a file inside the repo'
        assert str(row['at']).endswith('Z'), 'at is not a UTC Z timestamp: ' + repr(row.get('at'))
        rows = _rows(repo / 'workspace' / 'proposals.jsonl')
        assert len(rows) == 1, 'expected exactly 1 ledger row, got ' + str(len(rows))
        assert rows[0] == row, 'the row returned is not the row appended to the ledger'


def test_the_cli_records_what_the_model_answered():
    mod = load_askmemory()
    assert hasattr(mod, 'main'), 'askmemory.py defines no main() -- the brief anchors on def main'
    stub = StubChat()
    real = urllib.request.urlopen
    old_argv = list(sys.argv)
    buf = io.StringIO()
    sys.argv = ['askmemory.py', '--model', MODEL, '--task', 't162']
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        sys.argv += ['--out-dir', str(repo / 'workspace' / 'proposals'),
                     '--index', str(repo / 'workspace' / 'proposals.jsonl')]
        urllib.request.urlopen = stub
        sys.stdout = buf
        try:
            rc = mod.main()
        finally:
            urllib.request.urlopen = real
            sys.stdout = sys.__stdout__
            sys.argv = old_argv
        assert rc == 0, 'main() returned ' + repr(rc) + ', expected 0'
        assert stub.calls, 'main() made no chat request -- the model call was not preserved'
        assert ANSWER.strip() in buf.getvalue(), 'the proposal is no longer printed to stdout'
        files = sorted((repo / 'workspace' / 'proposals').glob('*.md'))
        assert len(files) == 1, (
            'expected 1 proposal file inside the repo, found ' + str(len(files))
            + ' -- today the answer is printed and sent to a documented path outside the repo')
        assert files[0].read_text(encoding='utf-8') == ANSWER, 'the file is not the model answer verbatim'
        rows = _rows(repo / 'workspace' / 'proposals.jsonl')
        assert len(rows) == 1, 'expected exactly 1 ledger row, found ' + str(len(rows))
        row = rows[0]
        assert set(row) == REQUIRED, 'row keys ' + str(sorted(row)) + ' are not the six t781 fields'
        assert row['script'] == 'askmemory.py', 'row script is ' + repr(row.get('script'))
        assert row['model'] == MODEL, 'row model is ' + repr(row.get('model'))
        assert row['task'] == 't162', 'row task is ' + repr(row.get('task'))
        assert row['sha256'] == hashlib.sha256(ANSWER.encode('utf-8')).hexdigest(), 'sha256 is not the answer written'
        assert not Path(row['out_path']).is_absolute(), 'the recorded path is absolute: outside the repo'
        assert (repo / row['out_path']).is_file(), 'the recorded path is not a file inside the repo'
        assert (repo / row['out_path']).read_text(encoding='utf-8') == ANSWER, 'the row points at a file that is not the answer'


def test_a_path_outside_the_repo_is_refused():
    mod = load_askmemory()
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / 'repo'
        repo.mkdir()
        outside = Path(td) / 'elsewhere' / 'proposals'
        try:
            mod.record_proposal(script='askmemory.py', content='x\n', model=MODEL, task='t162',
                                repo=repo, out_dir=outside)
        except ValueError:
            pass
        else:
            raise AssertionError('record_proposal accepted a path outside the repo: that is the '
                                 'outside-the-repo defect t781 exists to stop')
        assert not (repo / 'workspace' / 'proposals.jsonl').exists(), 'a refused proposal left a row behind'
        assert not outside.exists(), 'a refused proposal created files outside the repo'


def run_all():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith('test_') and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print('PASS ' + name)
        except BaseException as e:
            failed += 1
            print('FAIL ' + name + ': ' + type(e).__name__ + ': ' + e)
    print('')
    print(str(len(tests) - failed) + '/' + str(len(tests)) + ' passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(run_all())
