import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import scope_lint as L


def test_models_tab_store_is_live_state():
    hits = L.live_state_hits('json.load(open workspace/SERVING.json)')
    assert any('SERVING.json' in h for h in hits), (
        'workspace/SERVING.json is the Models tab live store and the defect own mechanism, '
        'yet the detector reports ' + repr(hits))


def test_task_queue_is_live_state():
    hits = L.live_state_hits('open workspace/tasks/t1209.md read()')
    assert any('workspace/tasks' in h for h in hits), (
        'the live task queue is not detected: ' + repr(hits))


def test_status_document_is_live_state():
    hits = L.live_state_hits('open STATE.md read()')
    assert any('STATE.md' in h for h in hits), (
        'the live status doc is not detected: ' + repr(hits))


def test_session_review_dir_is_live_state():
    hits = L.live_state_hits('open workspace/session-reviews/findings/x.json')
    assert any('session-reviews' in h for h in hits), (
        'the live session-review dir is not detected: ' + repr(hits))


def test_existing_sources_still_detected():
    for src in ('ROLES.json', 'dispatch-loop.jsonl', 'run-logs/', 'task_routing.load()', 'board_page()'):
        hits = L.live_state_hits('x ' + src)
        assert hits, 'regressed out of the detector: ' + src


def test_its_own_fixture_is_exempt():
    assert L.live_state_hits('import tempfile d tempfile.mkdtemp() x ROLES.json') == []
    assert L.live_state_hits('import copy r deepcopy(BASE)') == []
    assert L.live_state_hits('clean 1 1') == []


def test_finding_is_emitted_for_a_models_tab_oracle():
    chunks = [{'kind': 'oracle', 'file': 'scripts/tests/test_x.py',
               'oracle_text': 'json.load(open workspace/SERVING.json)'}]
    out = L.live_state_findings(chunks, ROOT)
    assert out, 'no finding for an oracle reading the Models-tab store'
    cls, idx, f, ev = out[0]
    assert cls == 'oracle-live-state', 'wrong class ' + repr(cls)
    assert idx == 0, 'finding points at the wrong chunk index ' + repr(idx)
    assert f == 'scripts/tests/test_x.py', 'finding points at the wrong file ' + repr(f)
    assert 'SERVING.json' in ev, 'evidence must name the live source found: ' + repr(ev)


def test_implementation_chunk_is_not_this_class():
    chunks = [{'kind': 'implementation', 'file': 'scripts/scope_lint.py',
               'single_change': 'read workspace/ROLES.json in the shipped module',
               'target': 'scripts/tests/test_scope_lint_live_state_sources.py',
               'done_when': 'python3 scripts/tests/test_scope_lint_live_state_sources.py exits 0',
               'related_entries': ['t1209']}]
    assert L.live_state_findings(chunks, ROOT) == [], \
        'the class is an oracle reading live state, not an implementation module that reads it'


if __name__ == '__main__':
    _fails = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith('test_') and callable(_fn):
            try:
                _fn()
                print('PASS  ' + _name)
            except AssertionError as _e:
                _fails += 1
                print('FAIL  ' + _name + ': ' + str(_e))
    print(str(_fails) + ' failed')
    sys.exit(1 if _fails else 0)
