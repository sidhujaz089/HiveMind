#!/usr/bin/env python3
'''RED FIXTURE for t1016: the budget/residency keys must come from settings.py.

WHY THIS EXISTS
---------------
scripts/tests/test_sampling_single_source.py:30 lists only the vendor's sampling keys, and :127
asserts that a literal num_ctx / num_predict value is NOT a finding. That exemption is why
L-119 recurred a third time: NUM_CTX = 262144 was typed into a caller in good faith
(methodology.py:46, and the same idiom at askmemory.py:49, coder.py:142, probe.py:80) and no
check in the tree could call it out. This is the red fixture for the SECOND rule: a LITERAL
budget/residency value in a caller is a finding, the legal forms read it from settings, and
today's real population is dispositioned by name so the gate lands green and every FUTURE
caller is caught.

RED ON MAIN FOR ONE REASON AND ONE ONLY: the module under test defines no budget_literals()
and no BUDGET_CALLERS, so every budget assertion below fails with a message naming the missing
symbol. Nothing here starts a model, a server, a subprocess, a socket, a timer or a /tmp file:
importing the target module runs its module-level 'import settings' (:26), which it already
does every time run_tests.py runs it.
'''
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
TARGET = ROOT / 'scripts' / 'tests' / 'test_sampling_single_source.py'

_spec = importlib.util.spec_from_file_location('sss_under_test', TARGET)
sss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sss)

FAILS = []

# Every file my own AST audit found with a budget/residency literal, in the two directories
# main() scans, excluding settings.py (the source) and scripts/tests/ (which :32 sanctions).
# 12 files / 27 sites, audited 2026-10-10. A file on this list with no disposition is a gap in
# the gate; a future caller that is NOT on it is a finding -- that is the inversion.
POPULATION = [
    'askmemory.py', 'chatui.py', 'coder.py', 'deliberate.py', 'interview.py', 'methodology.py',
    'probe.py', 'ask-gemma-supervise.py', 'gemma-decide.py', 'inference_health.py',
    'truncation_review.py', 'verify_control.py',
]


def check(ok, msg):
    print('  [%s]  %s' % ('ok' if ok else 'FAIL', msg))
    if not ok:
        FAILS.append(msg)


def budget_hits(src, name='probe.py'):
    '''budget_literals(src, path) on a real-looking file name. A missing function is a FAILURE
    that names the gap -- never a silent empty list: that is how a fixture passes for nothing.'''
    fn = getattr(sss, 'budget_literals', None)
    if fn is None:
        raise AssertionError('test_sampling_single_source.py defines no budget_literals '
                             'symbol: the second rule is not implemented')
    return list(fn(src, Path(name)))


def hit(hits, key, value):
    '''A finding for `key` carrying EXACTLY `value`, on the tuple shape the existing literals()
    returns (f'{key}={value}', lineno). Exact comparison after the equals sign, so 1 is not
    satisfied by 12000 and 262144 is not satisfied by 2621440.'''
    want = str(value)
    for h in hits:
        parts = str(h[0]).split('=', 1)
        if len(parts) == 2 and parts[0] == key and parts[1] == want:
            return True
    return False


def test_dict_literal_is_a_finding():
    # 262144 is settings.py:51's coder num_ctx -- a real constant, not a placeholder.
    hits = budget_hits('body = {"model": m, "options": {"num_ctx": 262144, "temperature": 1.0}}')
    assert hit(hits, 'num_ctx', 262144), 'num_ctx=262144 in a dict literal must be a finding -> %r' % (hits,)
    assert not hit(hits, 'temperature', 1.0), 'temperature is the vendor rule\'s business, not the budget rule\'s -> %r' % (hits,)


def test_subscript_assignment_is_a_finding():
    hits = budget_hits('opts["num_predict"] = 16000')
    assert hit(hits, 'num_predict', 16000), 'opts["num_predict"] = 16000 must be a finding -> %r' % (hits,)


def test_keyword_argument_is_a_finding():
    hits = budget_hits('settings.options("coder", model, keep_alive="24h")')
    assert hit(hits, 'keep_alive', '24h'), 'keep_alive="24h" as a keyword must be a finding -> %r' % (hits,)


def test_module_constant_is_a_finding():
    # THE SHAPE THAT ACTUALLY RECURRED (methodology.py:46 'NUM_CTX = 262144' inside
    # 'except Exception:'). Dict / subscript / keyword cannot see it, and a gate that is blind
    # to the defect that motivated it reports "clean" while the defect is present.
    hits = budget_hits('NUM_CTX = 1')
    assert hit(hits, 'num_ctx', 1), 'NUM_CTX = 1 must be a finding -> %r' % (hits,)
    hits = budget_hits('KEEP_ALIVE = "30m"')
    assert hit(hits, 'keep_alive', '30m'), 'KEEP_ALIVE = "30m" must be a finding -> %r' % (hits,)


def test_reading_the_value_from_settings_is_legal():
    # Every one of these is real code in the tree. A rule that flags them makes the legal form
    # a finding and gets itself bypassed within a week, which is worth less than no rule.
    cases = [
        ('NUM_CTX = _D["coder"]["num_ctx"]', 'askmemory.py:47'),
        ('_cfg = {"num_ctx": NUM_CTX}', 'coder.py:1030'),
        ('o = settings.options("coder", model, num_ctx=NUM_CTX)', 'methodology.py:87'),
        ('num_ctx = num_ctx or NUM_CTX', 'coder.py:1256'),
        ('opts["keep_alive"] = settings.DEFAULTS["chat"]["keep_alive"]', 'settings.py:43'),
    ]
    for src, where in cases:
        hits = budget_hits(src)
        assert hits == [], '%s reads the value from settings, so it is NOT a finding -> %r' % (where, hits)


def test_keys_that_are_not_the_budget_trio_are_untouched():
    # temperature / top_p belong to VENDOR_KEYS; seed and stop are neither. A rule that sweeps
    # every numeric key in a file is not the rule asked for, and it fails here.
    for src, key, value in [
        ('opts["temperature"] = 0.3', 'temperature', 0.3),
        ('opts["top_p"] = 0.95', 'top_p', 0.95),
        ('opts["seed"] = 7', 'seed', 7),
        ('opts["stop"] = ["END"]', 'stop', 'END'),
    ]:
        hits = budget_hits(src)
        assert not hit(hits, key, value), '%s is not a budget/residency key -> %r' % (key, hits)


def test_today_s_population_is_dispositioned_by_name():
    # The gate must land GREEN the day it lands, which is what a disposition record is for. This
    # is membership, not equality: the four fallback files are removed from the list by their own
    # chunks later, and a list that has a name a file no longer needs must not fail them.
    allowed = getattr(sss, 'BUDGET_CALLERS', None)
    assert allowed is not None, 'test_sampling_single_source.py defines no BUDGET_CALLERS '
    assert isinstance(allowed, dict), 'BUDGET_CALLERS must be a dict of file name to reason, got %s' % type(allowed).__name__
    assert allowed, 'BUDGET_CALLERS is empty: the audited population is undispositioned, so the gate fails on day one'
    for name in POPULATION:
        assert name in allowed, '%s holds a real budget/residency literal (verified by AST) and is not dispositioned -> %r' % (name, sorted(allowed))
    for name, reason in allowed.items():
        assert isinstance(name, str) and name.endswith('.py'), 'BUDGET_CALLERS key %r is not a .py file name' % (name,)
        assert isinstance(reason, str) and reason.strip(), 'BUDGET_CALLERS[%r] carries no reason: an exemption with no reason is a silent one' % (name,)


def test_the_vendor_rule_still_catches_a_literal_temperature():
    # The PRE-EXISTING contract (:55-74): it passes today and must still pass. Green on main
    # by design -- it is here so the new rule cannot be bought by weakening the old one.
    hits = list(sss.literals('opts["temperature"] = 0.3', Path('probe.py')))
    assert len(hits) == 1, 'the vendor rule must catch exactly one literal at :55-74 -> %r' % (hits,)


TESTS = [
    test_dict_literal_is_a_finding,
    test_subscript_assignment_is_a_finding,
    test_keyword_argument_is_a_finding,
    test_module_constant_is_a_finding,
    test_reading_the_value_from_settings_is_legal,
    test_keys_that_are_not_the_budget_trio_are_untouched,
    test_today_s_population_is_dispositioned_by_name,
    test_the_vendor_rule_still_catches_a_literal_temperature,
]


def main():
    print('t1016: a literal num_ctx / num_predict / keep_alive in a caller is a finding')
    for t in TESTS:
        try:
            t()
        except AssertionError as e:
            check(False, '%s: %s' % (t.__name__, e))
        except Exception as e:            # a broken oracle is a failure, never a pass
            check(False, '%s: raised %s: %s' % (t.__name__, type(e).__name__, e))
    print('test_sampling_budget_gate: %s' % ('FAILED (%d)' % len(FAILS) if FAILS else 'ok'))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
