#!/usr/bin/env python3
'''t1250: a file too large to re-emit must leave the line-range edit as a valid answer.

Dispatch p535-t1137-coder (2026-10-16T05:37:47Z) was briefed to edit coder.py itself. It read
the size guard correctly -- coder.py is 3,148 lines and cannot be re-emitted beside the prompt
-- and was answered twice with the sentence at coder.py:2879:

    refused: projects/local-inference/coder.py: no room for more of it beside the prompt.
    Work from what you already have            (attempt 2, 550 tok; attempt 4, 9319 tok)

The guard is right. What is missing is the door coder.py's own prompt opens: SHAPE B, a line
range plus only the replacement text, which needs NO transcription of a file that will not fit
the window. The model holds a numbered listing (unseen() labels every slice
'lines {upto+1}-{n} of {len(lines)}'), so it can name a range in the part it has NOT seen. The
refusal names the closed door and no open one, so the model re-asked for the same file and the
run burned attempts on it.

The contract pinned here is NOT invented: scripts/tests/test_coder_scope_apply.py already calls
coder.pressure_guidance(rel, total, shown) at its lines 92 and 103. Verified this session by
running it, and by grep -rn 'pressure_guidance' --include=*.py . which finds the name ONLY in
that suite: coder.py defines no such function. A build that greens this suite greens those two
tests as well, which is the cross-check the brief records.

Red on main for exactly one reason: pressure_guidance is not defined. No model call, no
subprocess, no server, no writes -- it imports coder.py and calls a pure function.
'''
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODER = ROOT / 'projects' / 'local-inference' / 'coder.py'
DEAD_END = 'Work from what you already have'
FAILS = []
_MOD = None


def load_coder():
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location('coder_under_test', CODER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def ck(name, cond, detail=''):
    print('  [' + ('ok' if cond else 'BAD') + ']  ' + name +
          ('' if cond else '  ' + str(detail)))
    if not cond:
        FAILS.append(name)


def _statement_containing(src, needle):
    '''The whole append(...) statement that carries the refusal, from its line start to the
    closing parenthesis -- so a def that is never called fails the test below.'''
    i = src.index(needle)
    start = src.rfind('\n', 0, i) + 1
    depth = 0
    k = src.index('(', start)
    while k < len(src):
        if src[k] == '(':
            depth += 1
        elif src[k] == ')':
            depth -= 1
            if depth == 0:
                return src[start:k + 1]
        k += 1
    return src[start:]


def test_the_contract_the_repo_already_calls_exists():
    fn = getattr(load_coder(), 'pressure_guidance', None)
    ck('coder.pressure_guidance is defined', callable(fn),
       'coder.py defines no pressure_guidance: the size refusal leaves the model no route')
    if not callable(fn):
        return
    text = fn('scripts/feedback.py', 1088, 821)
    ck('it returns a non-empty string', isinstance(text, str) and text.strip() != '', repr(text))


def test_it_names_the_range_edit_the_model_can_actually_answer():
    text = load_coder().pressure_guidance('scripts/feedback.py', 1088, 821)
    want = ['feedback.py', '822-1088', 'SHAPE B', 'range', '"path"', '"from"', '"to"', '"new"']
    missing = [w for w in want if w not in text]
    ck('it names the file, the unseen range 822-1088, SHAPE B and its JSON keys',
       not missing, 'missing ' + repr(missing) + ' from: ' + text)


def test_the_numbers_come_from_the_arguments_not_a_constant():
    g = load_coder().pressure_guidance
    a = g('projects/local-inference/coder.py', 3148, 821)
    ck('3148 lines shown to 821 yields the range 822-3148', '822-3148' in a, a)
    ck('and does not reuse 1088 from the first case', '1088' not in a, a)
    b = g('app.py', 1200, 300)
    ck('300 of 1200 yields the range 301-1200', '301-1200' in b, b)
    c = g('scripts/feedback.py', 1088, 821)
    ck('three files with three totals give three different messages', len({a, b, c}) == 3,
       repr([a, b, c]))


def test_shown_zero_states_the_whole_file_is_unseen():
    text = load_coder().pressure_guidance('app.py', 12, 0)
    ck('shown=0 states the range 1-12', '1-12' in text, text)
    ck('shown=0 still opens the SHAPE B route', 'SHAPE B' in text, text)
    ck('shown=0 does not print a nonsense "lines 1-0"', 'lines 1-0' not in text, text)


def test_it_says_so_when_the_file_was_shown_in_full():
    text = load_coder().pressure_guidance('app.py', 40, 40)
    ck('a fully shown file is named as shown in full', 'shown in full' in text, text)
    ck('and the line-range route is still named', 'range' in text.lower(), text)


def test_the_answer_is_pure_and_deterministic():
    g = load_coder().pressure_guidance
    ck('same arguments give the same string', g('app.py', 12, 0) == g('app.py', 12, 0))
    ck('it is one line of prose: no embedded newline', '\n' not in g('app.py', 12, 0),
       repr(g('app.py', 12, 0)))


def test_it_does_not_send_the_model_back_to_the_dead_end():
    g = load_coder().pressure_guidance
    for args in [('scripts/feedback.py', 1088, 821), ('app.py', 40, 40), ('app.py', 12, 0)]:
        text = g(*args)
        ck('no dead-end sentence for ' + repr(args), DEAD_END not in text, text)


def test_the_size_refusal_at_the_slice_guard_builds_it_in():
    '''Defining pressure_guidance and never calling it would satisfy every test above. The text
    the model is made of is built at coder.py:2879, so THAT statement must build it in
    (failure-schema: check-satisfied-by-dead-path).'''
    src = CODER.read_text(encoding='utf-8', errors='replace')
    needle = 'no room for more of it beside the prompt'
    ck('the slice-guard refusal prefix is still in coder.py (pinned by test_coder_slice_room.py)',
       needle in src, repr(needle) + ' is gone from coder.py')
    if needle not in src:
        return
    stmt = _statement_containing(src, needle)
    ck('the refusal at the slice guard builds pressure_guidance into the message',
       'pressure_guidance(' in stmt, stmt)
    ck('the refusal keeps the sentence that is true where the file WAS opened',
       DEAD_END in stmt, stmt)


if __name__ == '__main__':
    _fns = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for _fn in _fns:
        print('\n' + _fn.__name__ + ':')
        try:
            _fn()
        except Exception as _e:                                    # noqa: BLE001
            FAILS.append(_fn.__name__ + ': ' + type(_e).__name__ + ': ' + str(_e))
            print('  EXC  ' + type(_e).__name__ + ': ' + str(_e))
    print('\n' + str(len(FAILS)) + ' failed' + (': ' + ', '.join(FAILS) if FAILS else ''))
    sys.exit(1 if FAILS else 0)
