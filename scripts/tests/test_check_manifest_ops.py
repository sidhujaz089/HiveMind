'''Fail-first oracle for Checker.check_manifest_ops (scripts/check_workspace.py).

Red on main because Checker has no check_manifest_ops method yet (the missing change);
green once the builder adds it and registers it. Uses a temp fixture and only the
verified Checker API (Checker.__init__ and the printed verdict line from Checker.result),
never the live README or chat_agent.py. Pinned behaviour: drift is a FAIL that names the
op, agreement is a PASS, an unreadable source is a SKIP that is never counted as a PASS
or a FAIL (Checker has no skips counter, so the verdict is asserted from the printed
line, not a counter).
'''

import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]  # scripts/
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_workspace import Checker  # noqa: E402

# The REAL seven-op vocabulary from scripts/chat_agent.py ALLOWED_OPS.
CODE_SEVEN = 'ALLOWED_OPS = ("create", "write", "move", "rename", "delete", "append", "replace")'
# The README six-op Operations line, as it stands today (the live drift).
README_SIX = '**Operations:** `create` `write` `move` `rename` `delete` `append`. Nothing else, and there is no shell execution.'
# The README seven-op Operations line, after the instance repair.
README_SEVEN = '**Operations:** `create` `write` `move` `rename` `delete` `append` `replace`. There is no shell execution.'


def _fixture(root, code_text, readme_text):
    base = Path(root)
    if code_text is not None:
        (base / 'scripts').mkdir(parents=True, exist_ok=True)
        (base / 'scripts' / 'chat_agent.py').write_text(code_text, encoding='utf-8')
    d = base / 'workspace' / 'commands'
    d.mkdir(parents=True, exist_ok=True)
    (d / 'README.md').write_text(readme_text, encoding='utf-8')


def _run(root):
    buf = io.StringIO()
    ck = Checker(root)
    with redirect_stdout(buf):
        ck.check_manifest_ops()
    return ck, buf.getvalue()


def test_drift_is_fail_and_names_replace():
    with tempfile.TemporaryDirectory() as d:
        _fixture(d, CODE_SEVEN, README_SIX)
        ck, out = _run(d)
        assert ck.failures == 1, 'a six-op README against a seven-op executor must FAIL'
        assert ck.passes == 0, 'a drift must not be reported as PASS'
        assert '[FAIL]' in out, 'the check must print a FAIL verdict'
        assert 'replace' in out, 'the FAIL must name the op that drifted'


def test_agreement_is_pass():
    with tempfile.TemporaryDirectory() as d:
        _fixture(d, CODE_SEVEN, README_SEVEN)
        ck, out = _run(d)
        assert ck.passes == 1, 'equal vocabularies must PASS'
        assert ck.failures == 0, 'agreement must not FAIL'
        assert '[PASS]' in out, 'the check must print a PASS verdict'


def test_unreadable_source_is_skip_not_pass():
    with tempfile.TemporaryDirectory() as d:
        _fixture(d, None, README_SEVEN)
        ck, out = _run(d)
        assert ck.passes == 0, 'an unreadable source must not be reported as PASS'
        assert ck.failures == 0, 'an unreadable source is a SKIP, not a FAIL'
        assert '[SKIP]' in out, 'the check must print a SKIP verdict'


if __name__ == '__main__':
    tests = [
        test_drift_is_fail_and_names_replace,
        test_agreement_is_pass,
        test_unreadable_source_is_skip_not_pass,
    ]
    bad = 0
    for fn in tests:
        try:
            fn()
            print('  PASS ' + fn.__name__)
        except Exception as e:
            bad += 1
            print('  FAIL ' + fn.__name__ + ' [' + type(e).__name__ + ']: ' + str(e))
    if bad:
        raise SystemExit(str(bad) + ' test(s) failed')
    print('  ' + str(len(tests)) + ' passed')
