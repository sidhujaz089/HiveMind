#!/usr/bin/env python3
'''t115 -- the dictation cleanup must carry a glossary of Jas's own proper nouns.

clean.py's SYSTEM prompt is the only place the cleanup model learns that a word is
Jas's real name rather than a misheard common word. On main it is a generic
'repair speech-to-text' instruction with none of his proper nouns, so the model can
only guess -- which is how 'app droid' became 'Android' / 'App Droid' instead of
'F-Droid'.

Loads clean.py by path (no import side effects, no network) and checks the shipped
system prompt for the real, distinct proper nouns the task names, plus the owner
anchor 'Jas'. Makes no model calls.
'''
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLEAN_PY = ROOT / 'projects' / 'good-dictator-app' / 'cleanup' / 'clean.py'

# Six real, distinct proper nouns from the task (t115) and from clean.py's own SAMPLES
# ground truth ('The F-Droid app store...'). Distinct values, so one repeated
# placeholder cannot satisfy this.
REQUIRED_NOUNS = ['F-Droid', 'SuperButton', 'HiveMind', 'Good Dictator', 'Indoor Voice', 'Tailscale']


def _load_clean():
    if not CLEAN_PY.exists():
        raise AssertionError('clean.py not found at ' + str(CLEAN_PY))
    spec = importlib.util.spec_from_file_location('t115_clean', str(CLEAN_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # clean.py does no I/O or network at import time
    return mod


def test_clean_py_exists():
    assert CLEAN_PY.exists(), 'missing ' + str(CLEAN_PY)


def test_system_prompt_is_nonempty():
    system = getattr(_load_clean(), 'SYSTEM', '')
    assert isinstance(system, str) and system.strip(), 'clean.SYSTEM is missing or empty'


def test_system_prompt_carries_jas_glossary():
    system = getattr(_load_clean(), 'SYSTEM', '').lower()
    missing = [n for n in REQUIRED_NOUNS if n.lower() not in system]
    assert not missing, 'system prompt is missing proper nouns: ' + str(missing)


def test_system_prompt_names_the_owner():
    # A glossary of Jas's own nouns must say whose vocabulary it is; a bare dump of
    # random tokens that happens to include the six names should not pass.
    system = getattr(_load_clean(), 'SYSTEM', '').lower()
    assert 'jas' in system, 'system prompt lists the nouns but does not name the owner'


def main():
    checks = [test_clean_py_exists, test_system_prompt_is_nonempty,
              test_system_prompt_carries_jas_glossary, test_system_prompt_names_the_owner]
    failures = []
    for fn in checks:
        try:
            fn()
        except AssertionError as e:
            failures.append((fn.__name__, str(e)))
    for name, msg in failures:
        print('FAIL ' + name + ': ' + msg)
    if failures:
        print(str(len(failures)) + ' check(s) failed')
        return 1
    print('ok: clean.py system prompt carries a Jas proper-noun glossary')
    return 0


if __name__ == '__main__':
    sys.exit(main())
