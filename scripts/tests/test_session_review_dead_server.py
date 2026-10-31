"""t1288: a session-review RCA whose model's server does not answer must not cost the finding an attempt.

RED ON MAIN, for the change and nothing else: the reviewer has no ServerNotAnswering and no
server_not_answering, so rca() cannot tell a dead model server from an empty answer and charges
every empty reply as an unusable attempt reading "no answer (timed out or empty)". On t1288 the
child printed `TIMED OUT after 0 min: 'swift' is offered by vllm-swift, and none answers` -- the
server named in the Models tab answered nothing in 0 seconds -- and finding 06dd17f8276e was
moved to workspace/session-reviews/failed/ at 898 candidates_consulted, its RCA never asked of a
live server.

Runs with no model, no server, no live state: temp dirs, a fake child process, a stub skills.
"""
import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

DEAD_LINE = "  TIMED OUT after 0 min: 'swift' is offered by vllm-swift, and none answers"
CAUSE = 'is offered by vllm-swift, and none answers'


def _load():
    spec = importlib.util.spec_from_file_location('sr_under_test', SCRIPTS / 'session_review.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load()


def _stub_skills():
    stub = types.ModuleType('skills')
    stub.with_skills = lambda prompt, *a, **k: (prompt, [])
    return stub


def _pin_env(logs, charged):
    """Stub the prompt builder and the two writers rca() touches: the ledger, the give-up count."""
    real_skills = sys.modules.get('skills')
    real_log, real_unusable = MOD._log, MOD._unusable
    sys.modules['skills'] = _stub_skills()
    MOD._log = lambda row: logs.append(row)
    MOD._unusable = lambda *a, **k: charged.append(a)

    def restore():
        MOD._log, MOD._unusable = real_log, real_unusable
        if real_skills is None:
            sys.modules.pop('skills', None)
        else:
            sys.modules['skills'] = real_skills
    return restore


def test_module_declares_the_cause_type_and_the_detector():
    assert hasattr(MOD, 'ServerNotAnswering'), 'no ServerNotAnswering in session_review.py'
    assert issubclass(MOD.ServerNotAnswering, Exception)
    assert callable(MOD.server_not_answering), 'no server_not_answering() to read the cause line'


def test_detector_takes_the_real_line_and_leaves_clean_output_alone():
    got = MOD.server_not_answering('== ask_q16 ==\n' + DEAD_LINE + '\n  this run produced nothing\n')
    assert CAUSE in got, got
    assert 'swift' in got, got
    assert MOD.server_not_answering('  -> done_reason=stop 12 min 4321 chars (3 tool round(s))') == ''
    assert MOD.server_not_answering('the model is offered by swift only') == ''
    assert MOD.server_not_answering('') == ''


def test_ask_raises_the_cause_instead_of_returning_an_empty_answer():
    class FakeCP:
        returncode = 1
        stdout = '== ask_q16 ==\n' + DEAD_LINE + '\n  wrote an answer file  (0 chars, complete=False)\n'
        stderr = ''

    seen = []

    def fake(cmd, **kw):
        seen.append((cmd, kw))
        assert kw.get('timeout'), 'the child call must stay bounded'
        return FakeCP()

    real = MOD.subprocess.run
    MOD.subprocess.run = fake
    raised, msg = False, ''
    try:
        try:
            MOD.ask('a prompt', 'RCA: session-review finding 06dd17f8276e', limit_s=6000)
        except MOD.ServerNotAnswering as e:
            raised, msg = True, str(e)
    finally:
        MOD.subprocess.run = real
    assert raised, 'ask() swallowed a dead-server reply as an empty answer -- that is what burned t1288'
    assert CAUSE in msg, msg
    assert len(seen) == 1, f'expected one bounded child call, saw {len(seen)}'


def test_a_dead_server_attempt_is_not_charged_to_the_finding():
    tmp = Path(tempfile.mkdtemp(prefix='t1288-dead-'))
    finding = tmp / '06dd17f8276e.json'
    finding.write_text(json.dumps({'key': '06dd17f8276e', 'kind': 'unresolved',
                                   'quote': 'Session review could not do the RCA',
                                   'request': 't1288'}), encoding='utf-8')
    logs, charged = [], []

    def dead_asker(*a, **k):
        raise MOD.ServerNotAnswering(CAUSE)

    restore = _pin_env(logs, charged)
    try:
        ids = MOD.rca(finding, asker=dead_asker)
    finally:
        restore()
    assert ids == [], ids
    assert charged == [], f'a dead-server attempt charged the finding: {charged}'
    assert finding.is_file(), 'a server failure moved the finding out of its queue directory'
    assert not json.loads(finding.read_text(encoding='utf-8')).get('unusable'), 'unusable was appended'
    rec = [r for r in logs if 'server' in str(r.get('event', '')).lower()]
    assert rec, f'the dead server was not recorded in the job log: {logs}'
    assert CAUSE in json.dumps(rec), f'the record does not carry the cause: {rec}'


def test_a_plain_empty_answer_still_costs_an_attempt():
    tmp = Path(tempfile.mkdtemp(prefix='t1288-empty-'))
    finding = tmp / 'abc123.json'
    finding.write_text(json.dumps({'key': 'abc123', 'kind': 'unresolved', 'quote': 'q'}),
                       encoding='utf-8')
    charged, logs = [], []
    restore = _pin_env(logs, charged)
    try:
        ids = MOD.rca(finding, asker=lambda *a, **k: '')
    finally:
        restore()
    assert ids == []
    assert len(charged) == 1, f'the give-up machinery was switched off for empty answers too: {charged}'
    assert 'no answer (timed out or empty)' in json.dumps(charged), json.dumps(charged)


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failed = []
    for t in tests:
        try:
            t()
            print(f'PASS {t.__name__}')
        except Exception as e:
            failed.append(t.__name__)
            print(f'FAIL {t.__name__}: {type(e).__name__}: {e}')
    print(f'{len(tests) - len(failed)}/{len(tests)} passed')
    sys.exit(1 if failed else 0)
