#!/usr/bin/env python3
"""t1066 -- session start must STATE THE BOUND of every file section it prints.

Run:  python3 scripts/tests/test_session_start_coverage_line.py
Exit 0 if every case passes, 1 otherwise. pytest is not installed: everything runs
from the __main__ block at the bottom.

The defect (workspace/rca/8bdb1013c532.md): session start prints the 'Right now'
section of STATE.md as a slice -- forward from the heading to the next '## ' heading --
and nothing in the output says where the slice ends. In the record, 'seen' and
'complete' are indistinguishable. That is the shape the never-truncate-a-record skill
names: a truncated record looks complete, so the reader concludes the missing part does
not exist. The fix is a bound statement, in the same breath as the section.

Four cases, each in its own temporary repo root, so the live STATE.md is never read:
  A  a truncated section prints ONE coverage line whose byte and line counts are the
     numbers THIS test computes from the seeded file; the section body is still printed
     and the un-printed tail is still not printed.
  B  the decay test: a module-level no-op coverage_line is inserted into the source,
     after the real definition and before the __main__ guard, so main() calls the
     neutered name. The coverage line must vanish. A build that prints the string as an
     inline literal in main() fails here, and so does one that goes silent later.
  C  a section that runs to the end of the file states its bound too, with true counts
     -- this kills an emitter that prints the line only when a cut exists.
  D  a STATE.md with no '## Right now' heading keeps today's message and invents no
     coverage line -- pins the branch at scripts/session_start.py:669, which must not
     regress.

Every number asserted is arithmetic over the seeded text, never a literal.
The run is read-only outside its own temp directories: the sandboxed script writes
nothing (it reads files and prints), and the STATE.md it reads is the seeded one.
"""
import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / 'scripts' / 'session_start.py'

SECTION = 'Right now'

# Seeded STATE.md: the printed section is bounded by the next '## ' heading, so the tail
# below it is text the banner must NOT print and must NOT claim to have printed.
STATE_TEXT = '\n'.join([
    '# STATE',
    '',
    '## Right now',
    'alpha one',
    'beta two',
    '',
    '## Later sections are not printed',
    'gamma three',
    'delta four',
    '',
])

# A STATE.md whose section runs to the end of the file: the bound must still be stated,
# with the true fraction, and must not be summarised away because nothing was cut.
STATE_LAST = '\n'.join([
    '# STATE',
    '',
    '## Right now',
    'only section here',
    'and one more line',
    '',
])

STATE_NO_SECTION = '\n'.join([
    '# STATE',
    '',
    '## Something else entirely',
    'no right-now heading at all',
    '',
])

# The mutation: a module-level function of that name that returns nothing. It is inserted
# AFTER the real definition and BEFORE the __main__ guard, so that when main() runs, the
# global name resolves to this one. Appending it to the END of the source would be dead
# code (the guard calls main() before the appended def executes) -- that is why this file
# parses the source with ast to find the insertion point.
MUTATION = '\n'.join([
    'def coverage_line(*args, **kwargs):  # t1066 mutation fixture: the emitter, removed',
    '    return ""',
    '',
])

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print('  [ok]   ' + label)
    else:
        FAILED += 1
        print('  [BAD]  ' + label + ': got ' + repr(got) + ', want ' + repr(want))


def norm(s):
    # Dash cosmetics are not the contract; the numbers are. Normalise so an em dash, an
    # en dash or a hyphen all satisfy the format stated in the brief.
    return s.replace('\u2014', '-').replace('\u2013', '-')


def section_counts(text):
    '''What the emitter must report, computed from the file on disk -- the same formulas
    the brief states. Every number asserted below is arithmetic, not a magic constant.'''
    lines = text.split('\n')
    start = None
    for i, line in enumerate(lines):
        if line.startswith('## ' + SECTION):
            start = i
            break
    if start is None:
        return None
    body = []
    for line in lines[start + 1:]:
        if line.startswith('##'):
            break
        body.append(line)
    shown = '\n'.join(body)
    return {
        'body': body,
        'lines_shown': len(body),
        'bytes_shown': len(shown.encode('utf-8')),
        'lines_total': len(lines),
        'bytes_total': len(text.encode('utf-8')),
    }


def coverage_lines(stdout):
    '''stdout lines that announce a bound for STATE.md. Found by content, not by format:
    a line must name the file and state both a byte count and a line count on disk.'''
    out = []
    for i, raw in enumerate(stdout.splitlines()):
        line = norm(raw.strip())
        if 'bytes' in line and 'lines on disk' in line and 'STATE.md' in line:
            out.append((i, line))
    return out


def stub_names(src):
    '''Sibling scripts the banner runs as subprocesses, read from the source: every quoted
    *.py on a line that mentions sys.executable. Derived, not hard-coded, so a build that
    adds a subprocess call is still sandboxed, and a name the test guessed wrong cannot
    make main() die with FileNotFoundError before it reaches the STATE.md section.'''
    names = set()
    for line in src.splitlines():
        if 'sys.executable' in line:
            names.update(re.findall(r"[\"']([A-Za-z0-9_\-]+\.py)[\"']", line))
    names.discard('session_start.py')
    return sorted(names)


STUB_BODY = '''"""t1066 sandbox stub: a sibling script, not modelled.

Exits 0 when run as a script. Every attribute is a no-op, so importing this module
from the banner cannot raise.
"""


def __getattr__(name):
    return lambda *args, **kwargs: None
'''


def neutered(src):
    '''The decay test's mutation: bind the name coverage_line to a no-op AFTER the real
    definition and BEFORE the module's __main__ guard, so main() calls the neutered one.
    Falls back to a plain append if the module has no top-level If (in that shape a
    trailing def also lands too late, and case B is red on a correct build: that is the
    honest limit of this mutation, recorded here rather than hidden).'''
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src + '\n' + MUTATION
    for node in tree.body:
        if isinstance(node, ast.If):
            at = max(0, node.lineno - 1)
            lines = src.splitlines(True)
            lines.insert(at, MUTATION)
            return ''.join(lines)
    return src + '\n' + MUTATION


def sandbox(state_text, mutate=False):
    root = Path(tempfile.mkdtemp(prefix='t1066-'))
    scripts = root / 'scripts'
    scripts.mkdir(parents=True)
    src = SRC.read_text(encoding='utf-8')
    if mutate:
        src = neutered(src)
    (scripts / 'session_start.py').write_text(src, encoding='utf-8')
    for name in stub_names(src):
        (scripts / name).write_text(STUB_BODY, encoding='utf-8')
    (root / 'STATE.md').write_text(state_text, encoding='utf-8')
    return root


def run(root):
    return subprocess.run([sys.executable, '-s', str(root / 'scripts' / 'session_start.py')],
                          capture_output=True, text=True, timeout=120,
                          stdin=subprocess.DEVNULL)


def test_a_truncated_section_states_its_cut():
    print('A. A truncated section prints ONE coverage line with the real counts:')
    root = sandbox(STATE_TEXT)
    try:
        r = run(root)
        out = r.stdout
        check('the sandboxed run completed (exit 0)', r.returncode, 0)
        c = section_counts(STATE_TEXT)
        check('the section body is still printed (first line)', 'alpha one' in out, True)
        check('the section body is still printed (second line)', 'beta two' in out, True)
        cl = coverage_lines(out)
        check('exactly one coverage line for STATE.md', len(cl), 1)
        if len(cl) != 1:
            return
        idx, line = cl[0]
        check('it names the file it read', 'STATE.md' in line, True)
        check('it names the section', SECTION in line, True)
        check('it states bytes shown of bytes on disk (' + str(c['bytes_shown'])
              + ' of ' + str(c['bytes_total']) + ')',
              str(c['bytes_shown']) + ' of ' + str(c['bytes_total']) + ' bytes' in line, True)
        check('it states lines shown of lines on disk (' + str(c['lines_shown'])
              + ' of ' + str(c['lines_total']) + ')',
              str(c['lines_shown']) + ' of ' + str(c['lines_total']) + ' lines on disk' in line,
              True)
        check('it says the rest is not shown', 'not shown' in line, True)
        check('it does NOT claim the whole file was printed',
              str(c['bytes_total']) + ' of ' + str(c['bytes_total']) + ' bytes' in line, False)
        body_idx = [i for i, raw in enumerate(out.splitlines())
                    if raw.strip() in ('alpha one', 'beta two')]
        check('it is printed AFTER the section body it bounds',
              bool(body_idx) and idx > max(body_idx), True)
        check('the un-printed tail is NOT emitted (no print-the-whole-file shortcut)',
              'gamma three' in out, False)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_b_mutating_the_emitter_goes_silent():
    print('B. Mutate the emitter (a no-op coverage_line): the line must vanish:')
    root = sandbox(STATE_TEXT, mutate=True)
    try:
        r = run(root)
        out = r.stdout
        check('the mutated run completed (exit 0)', r.returncode, 0)
        check('the section body still prints (so the run got that far)', 'alpha one' in out, True)
        check('with the emitter neutered, NO coverage line is printed', len(coverage_lines(out)), 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_c_whole_file_section_states_its_bound_too():
    print('C. A section running to the end of the file states its bound, with true counts:')
    root = sandbox(STATE_LAST)
    try:
        r = run(root)
        out = r.stdout
        check('the sandboxed run completed (exit 0)', r.returncode, 0)
        c = section_counts(STATE_LAST)
        cl = coverage_lines(out)
        check('exactly one coverage line for STATE.md', len(cl), 1)
        if len(cl) != 1:
            return
        _, line = cl[0]
        check('bytes shown of bytes on disk (' + str(c['bytes_shown']) + ' of '
              + str(c['bytes_total']) + ')',
              str(c['bytes_shown']) + ' of ' + str(c['bytes_total']) + ' bytes' in line, True)
        check('lines shown of lines on disk (' + str(c['lines_shown']) + ' of '
              + str(c['lines_total']) + ')',
              str(c['lines_shown']) + ' of ' + str(c['lines_total']) + ' lines on disk' in line,
              True)
        check('the bound is stated even when nothing was cut, not summarised away',
              'not shown' in line, True)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_d_missing_section_keeps_todays_message():
    print("D. STATE.md with no '## Right now' heading: today's message, no invented bound:")
    root = sandbox(STATE_NO_SECTION)
    try:
        r = run(root)
        out = r.stdout
        check('the sandboxed run completed (exit 0)', r.returncode, 0)
        check("the existing 'no Right now section' message survives",
              "has no '## Right now' section" in out, True)
        check('and no coverage line is invented for a section that was never printed',
              len(coverage_lines(out)), 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    for _fn in (test_a_truncated_section_states_its_cut,
                test_b_mutating_the_emitter_goes_silent,
                test_c_whole_file_section_states_its_bound_too,
                test_d_missing_section_keeps_todays_message):
        _fn()
    print('')
    print('test_session_start_coverage_line: ' + str(PASSED) + ' passed, '
          + str(FAILED) + ' failed')
    sys.exit(1 if FAILED else 0)
