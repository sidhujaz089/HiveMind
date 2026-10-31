#!/usr/bin/env python3
'''t1066 item 3 -- session start RECORDS the bounded view it printed.

Run:  python3 scripts/tests/test_session_start_coverage.py
Exit 0 if every case passes, 1 otherwise. pytest is not installed: everything runs
from the __main__ block at the bottom.

The gap (workspace/rca/8bdb1013c532.md, dead end D2): session start prints a slice of
STATE.md and, since 2026-10-20, states its bound on stdout (t1066 item 1, merged as
2c5dbeeee). Nothing RECORDS it. 'Did the session see all of STATE.md' is answerable only
by asking the agent, and an agent whose context has been compacted is a dead end. So the
emission endpoint appends one row per printed section to
workspace/session-start-coverage.jsonl, carrying the byte and line counts of what it
emitted against what is on disk.

Five cases, each in its own temporary repo root, so the live repository is never touched
and no file under it is written:
  A  a truncated section appends ONE row whose counts are arithmetic over the seeded
     file, with truncated True, and the printed bound is still on stdout (item 1's
     guarantee is not traded away for the record).
  B  decay: a no-op append is inserted before the __main__ guard, so the row vanishes.
     A build that prints the line inline and records nothing fails here.
  C  a section running to the end of the file appends its row too, truncated False:
     an uncut view is still a view, and 'the session saw all of STATE.md' must be
     recorded, not inferred from the absence of a row.
  D  no '## Right now' heading appends NO row: a view that was never printed is not
     recorded as one.
  E  no STATE.md file appends NO row, for the same reason.

Every number asserted is computed from the seeded text, never a literal. Every subprocess
carries timeout=. Everything read and written lives under the temporary root.
'''
import ast
import datetime
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / 'scripts' / 'session_start.py'

SECTION = 'Right now'
SESSION = 't1066-oracle'

# Seeded STATE.md: the printed section is bounded by the next '## ' heading, so the tail
# below it is text the banner must NOT print and must NOT claim to have emitted.
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

# A STATE.md whose section runs to the end of the file: the view is uncut, and the record
# must say so with truncated False: no later '##' heading cut it.
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

FIELDS = ('session', 'ts', 'section', 'path', 'bytes_emitted', 'bytes_on_disk',
          'lines_emitted', 'lines_on_disk', 'truncated')

# The mutation: a module-level function of that name that records nothing and reports that
# it recorded nothing. Inserted AFTER the real definition and BEFORE the __main__ guard, so
# when main() runs the global name resolves to this one. Appending it to the END of the
# source would be dead code -- the guard calls main() before an appended def executes --
# which is why this file parses the source with ast to find the insertion point.
MUTATION = '\n'.join([
    'def append_session_start_coverage(rows, coverage_path):  # t1066 mutation: the collector, removed',
    '    print("session-start coverage: NOT recorded (mutation fixture)")',
    '    return 0',
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


def section_counts(text):
    '''What the emitter must record, computed from the file on disk with the same formulas
    the brief states: the printed section runs from the '## Right now' heading to the next
    '##' heading. Every number asserted below is arithmetic, not a magic constant.'''
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
        'lines_emitted': len(body),
        'bytes_emitted': len(shown.encode('utf-8')),
        'lines_on_disk': len(lines),
        'bytes_on_disk': len(text.encode('utf-8')),
    }


def stub_names(src):
    '''Sibling scripts the banner runs as subprocesses, read from the source: every quoted
    *.py on a line that mentions sys.executable. Derived, not hard-coded, so a build that
    adds a subprocess call is still sandboxed, and a name this test guessed wrong cannot
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
    '''Bind the name append_session_start_coverage to a no-op AFTER the real definition and
    BEFORE the module's __main__ guard, so main() calls the neutered one.'''
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


def sandbox(state_text, mutate=False, write_state=True):
    '''A throwaway repo root: a copy of session_start.py under scripts/, empty stubs for the
    siblings it runs, and the seeded STATE.md. session_start's own paths are derived from
    Path(__file__).resolve().parent.parent, so everything this run reads and writes lands
    under `root`, never under the live repository.'''
    root = Path(tempfile.mkdtemp(prefix='t1066cov-'))
    scripts = root / 'scripts'
    scripts.mkdir(parents=True)
    src = SRC.read_text(encoding='utf-8')
    if mutate:
        src = neutered(src)
    (scripts / 'session_start.py').write_text(src, encoding='utf-8')
    for name in stub_names(src):
        (scripts / name).write_text(STUB_BODY, encoding='utf-8')
    if write_state:
        (root / 'STATE.md').write_text(state_text, encoding='utf-8')
    return root


def run(root, argv=()):
    return subprocess.run([sys.executable, '-s', str(root / 'scripts' / 'session_start.py'),
                           '--session', SESSION] + list(argv),
                          capture_output=True, text=True, timeout=120,
                          stdin=subprocess.DEVNULL)


def read_rows(root):
    '''Every row the run appended, parsed. (None, []) when no file was written at all.'''
    target = root / 'workspace' / 'session-start-coverage.jsonl'
    if not target.is_file():
        return None, []
    rows = []
    for raw in target.read_text(encoding='utf-8').splitlines():
        if raw.strip():
            rows.append(json.loads(raw))
    return target, rows


def state_rows(rows):
    return [r for r in rows if r.get('path', '').endswith('STATE.md')]


def test_a_truncated_section_appends_a_row():
    print('A. A truncated section appends ONE row with the real counts:')
    root = sandbox(STATE_TEXT)
    try:
        r = run(root)
        out = r.stdout
        check('the sandboxed run completed (exit 0)', r.returncode, 0)
        c = section_counts(STATE_TEXT)
        target, rows = read_rows(root)
        check('a coverage file was written inside the sandbox, not the repo',
              target is not None, True)
        if target is None:
            return
        check('it is the file the task names, under the sandbox root',
              str(target), str(root / 'workspace' / 'session-start-coverage.jsonl'))
        sr = state_rows(rows)
        check('exactly one coverage row for STATE.md', len(sr), 1)
        if len(sr) != 1:
            return
        row = sr[0]
        check('the row carries exactly the fields the task names, no more, no less',
              sorted(row), sorted(FIELDS))
        check('bytes_emitted is the printed slice (' + str(c['bytes_emitted']) + ')',
              row.get('bytes_emitted'), c['bytes_emitted'])
        check('bytes_on_disk is the whole file (' + str(c['bytes_on_disk']) + ')',
              row.get('bytes_on_disk'), c['bytes_on_disk'])
        check('lines_emitted is the printed slice (' + str(c['lines_emitted']) + ')',
              row.get('lines_emitted'), c['lines_emitted'])
        check('lines_on_disk is the whole file (' + str(c['lines_on_disk']) + ')',
              row.get('lines_on_disk'), c['lines_on_disk'])
        check('bytes_emitted < bytes_on_disk, so a reader can see the cut',
              row.get('bytes_emitted') < row.get('bytes_on_disk'), True)
        check('truncated is True (the exact value, not a truthy one)',
              row.get('truncated'), True)
        check('section names the section that was printed', row.get('section'), SECTION)
        check('path is the repo-relative file the section was read from',
              row.get('path'), 'STATE.md')
        check('session is the id this run was given', row.get('session'), SESSION)
        check('ts is an ISO-8601 UTC second stamp',
              bool(re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$',
                            str(row.get('ts'))), ), True)
        # The record must not be bought by losing the printed bound (item 1).
        check('the printed bound is still on stdout (item 1 is not traded away)',
              'bytes' in out and 'lines on disk' in out and 'STATE.md' in out, True)
        check('the un-printed tail is still NOT emitted', 'gamma three' in out, False)
        check('the run says it recorded the row',
              'NOT recorded' in out, False)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_b_mutating_the_collector_goes_silent():
    print('B. Mutate the collector (a no-op append): the row must vanish:')
    root = sandbox(STATE_TEXT, mutate=True)
    try:
        r = run(root)
        out = r.stdout
        check('the mutated run completed (exit 0)', r.returncode, 0)
        check('the section body still prints (so the run got that far)',
              'alpha one' in out, True)
        target, rows = read_rows(root)
        check('with the collector neutered, no coverage file is written',
              target is None, True)
        check('and no STATE.md row is recorded', len(state_rows(rows)), 0)
        check('and the run says the row was NOT recorded',
              'NOT recorded' in out, True)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_c_uncut_view_is_recorded_too():
    print('C. A section running to the end of the file is recorded, truncated False:')
    root = sandbox(STATE_LAST)
    try:
        r = run(root)
        check('the sandboxed run completed (exit 0)', r.returncode, 0)
        c = section_counts(STATE_LAST)
        target, rows = read_rows(root)
        check('a coverage file was written', target is not None, True)
        if target is None:
            return
        sr = state_rows(rows)
        check('exactly one coverage row for STATE.md', len(sr), 1)
        if len(sr) != 1:
            return
        row = sr[0]
        # CLAUDE 2026-10-28: the body never includes the heading, so emitted < on_disk always;
        # an uncut view is one where NO later '##' heading stopped the section.
        check('bytes_emitted is the whole section body (' + str(c['bytes_emitted']) + ')',
              row.get('bytes_emitted'), c['bytes_emitted'])
        check('lines_emitted is the whole section body (' + str(c['lines_emitted']) + ')',
              row.get('lines_emitted'), c['lines_emitted'])
        check('truncated is False: an uncut view is recorded, not inferred from absence',
              row.get('truncated'), False)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_d_no_section_records_nothing():
    print("D. STATE.md with no '## Right now' heading: today's message, no invented row:")
    root = sandbox(STATE_NO_SECTION)
    try:
        r = run(root)
        out = r.stdout
        check('the sandboxed run completed (exit 0)', r.returncode, 0)
        check("the existing 'no Right now section' message survives",
              "has no '## Right now' section" in out, True)
        _t, rows = read_rows(root)
        check('and no row is invented for a section that was never printed',
              len(state_rows(rows)), 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_e_missing_file_records_nothing():
    print('E. STATE.md absent: today\'s message, no invented row:')
    root = sandbox('', write_state=False)
    try:
        r = run(root)
        out = r.stdout
        check('the sandboxed run completed (exit 0)', r.returncode, 0)
        check("the existing 'STATE.md is missing' message survives",
              'STATE.md is missing' in out, True)
        _t, rows = read_rows(root)
        check('and no row is invented for a file that was never read',
              len(state_rows(rows)), 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    for _fn in (test_a_truncated_section_appends_a_row,
                test_b_mutating_the_collector_goes_silent,
                test_c_uncut_view_is_recorded_too,
                test_d_no_section_records_nothing,
                test_e_missing_file_records_nothing):
        _fn()
    print('')
    print('test_session_start_coverage: ' + str(PASSED) + ' passed, ' + str(FAILED) + ' failed')
    sys.exit(1 if FAILED else 0)
