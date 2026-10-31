#!/usr/bin/env python3
'''Oracle for t1270 -- a 'read X first' handover line must carry a tNNN.

THE DEFECT (workspace/rca/e6121f169196.md). Session 721de5a0 closed its handover with:

    First thing for the next session: **read `workspace/q16/FIXTURES-from-q16.md`.**
    33 KB, unread, and one scratch-directory clean-up from having been lost.

The next session read that handover, cited the file in prose, opened it zero times, and 29 days
later it is still unread. Nothing in scripts/check_workspace.py fired: DEFERRAL_PHRASE only knows
the 'still unprobed / ran out of budget / left for the next session' family, which matches
neither 'read X first' nor 'first thing for the next session'. The deferral carried no tNNN, so
nothing had a handle on it and no check could ever close it.

WHAT THIS SUITE PINS, in the order the defect needs it:
  1. the RCA's own sentence IS a finding, and it names the real path -- the actual artefact,
     not a placeholder;
  2. the other word order, 'read X first', is a finding too: a check that matches one phrasing
     is a check the next session's phrasing walks past;
  3. the cure silences it -- a tNNN on the SAME line;
  4. a tNNN on a NEIGHBOURING line does not: the unit is the line, so a number in an unrelated
     paragraph cannot buy silence for a deferral three screens away (that is why the existing
     deferral check's paragraph counting is the neighbour here and not the mechanism);
  5. two unnumbered deferrals are two findings, and a tNNN on one does not pay for the other --
     two DISTINCT real paths, so an implementation hard-wired to one path fails;
  6. the same-session-Read discharge is USED, not merely accepted: a path passed in read_paths
     is silenced while an unnumbered path absent from it is still reported;
  7. ordinary prose stays silent -- the decay guard, and the only thing that keeps this from
     becoming the gate everyone routes around with --no-verify;
  8. 'READ THIS FIRST' as a heading fires the regex and produces NO finding, because the rule is
     a deferral that names a file to read: no path, no finding. This is the live line in
     workspace/HANDOVER-2026-09-01.md, and it is why the gate can ship as FAIL;
  9. a finding is a record: the line is carried WHOLE, tail included (the fact that demands the
     number -- '33 KB, unread' -- sits at the end of it), the row has exactly the keys ids,
     line, path, and a reported row carries no tNNN of its own;
 10. the idiom lives in one compiled regex named HANDOVER_READ_PHRASE, so the phrase list has a
     librarian and the next idiom has an obvious home;
 11. the check is wired at close: Checker.check_deferral_reads exists and sits in the check list
     next to the deferral checks, where a FAIL blocks the push.

RED ON MAIN: scripts/check_workspace.py defines no HANDOVER_READ_PHRASE, no
handover_read_findings() and no Checker.check_deferral_reads, so HAS_RE, HAS_FN and HAS_M are
False and every assertion below reports BAD naming that as the cause. Nothing calls a missing
name directly, so the red is the change this scope adds and never an AttributeError; a keyword
argument the implementation does not accept is reported as BAD naming the keyword, not as a
traceback.

Touches no live state: no file is written, no subprocess is started, no transcript under the
home directory is read (the same-session-Read discharge is exercised by PASSING PATHS AS DATA;
the transcript scanner is a later scope, and this is why the function takes the set as a
parameter), and the only read is this file's own module source, read-only.
'''
import importlib.util
import re
import sys
from pathlib import Path

_MODULE = Path(__file__).resolve().parents[1] / 'check_workspace.py'
_spec = importlib.util.spec_from_file_location('check_workspace', str(_MODULE))
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)

fails = []

HAS_RE = isinstance(getattr(cw, 'HANDOVER_READ_PHRASE', None), re.Pattern)
HAS_M = callable(getattr(getattr(cw, 'Checker', None), 'check_deferral_reads', None))
HAS_FN = callable(getattr(cw, 'handover_read_findings', None))

NO_RE = ('check_workspace.py defines no compiled HANDOVER_READ_PHRASE: this is the change this '
         'scope adds')
NO_FN = ('check_workspace.py defines no handover_read_findings(): this is the change this scope '
         'adds')
NO_M = ('check_workspace.py defines no Checker.check_deferral_reads: this is the change this '
        'scope adds')

RCA_PATH = 'workspace/q16/FIXTURES-from-q16.md'
OTHER_PATH = 'docs/skills/never-truncate-a-record/SKILL.md'
KEYS = ['ids', 'line', 'path']
TAIL = '33 KB, unread'
RCA_LINE = ('First thing for the next session: **read `' + RCA_PATH + '`.** ' + TAIL +
            ', and one scratch-directory clean-up from having been lost.')
TWO_LINES = ('First thing for the next session: read ' + RCA_PATH + '\n'
             'And read ' + OTHER_PATH + ' first.\n')

PROSE = [
    'The gate is exactly two regex checks, under 40 lines.',
    'Already read the queue; it is short today.',
    'The reading of the ledger happens in session_start.py, before any work.',
    'This session read scripts/check_workspace.py and found nothing to report.',
    'The next session inherits the working set.',
]


def check(label, cond, detail=''):
    print('  [%s] %s%s' % ('ok' if cond else 'BAD', label,
                           ('  :: ' + str(detail)) if detail else ''))
    if not cond:
        fails.append(label)


def findings(text, **kw):
    '''Call the new function, returning [] when it does not exist, so an assertion about the
    missing function reports BAD (the change this scope adds) and never AttributeError (the
    oracle's own bug). A keyword the implementation does not accept is raised as an
    AssertionError naming it, so a signature miss reads as a contract miss, not a crash.'''
    if not HAS_FN:
        return []
    try:
        return list(cw.handover_read_findings(text, **kw) or [])
    except TypeError as exc:
        raise AssertionError('handover_read_findings does not accept the signature the brief '
                             'names (%s): %s' % (sorted(kw), exc))


def paths_of(rows):
    return sorted(str(r.get('path')) for r in rows)


def test_the_rca_sentence_is_a_finding_naming_its_path():
    if not HAS_FN:
        check('the RCA sentence is a finding naming the real path', False, NO_FN)
        return
    got = findings(RCA_LINE)
    check('the RCA sentence is a finding naming the real path',
          len(got) == 1 and str(got[0].get('path')) == RCA_PATH,
          'findings=%r' % (got,))


def test_the_read_x_first_word_order_is_a_finding():
    if not HAS_FN:
        check("'read X first' word order is a finding naming the real path", False, NO_FN)
        return
    line = ('Before anything else, read `' + OTHER_PATH + '` first -- it decides how the '
            'per-task cap is reported.')
    got = findings(line)
    check("'read X first' word order is a finding naming the real path",
          len(got) == 1 and str(got[0].get('path')) == OTHER_PATH,
          'findings=%r' % (got,))


def test_a_tnnn_on_the_same_line_silences_it():
    if not HAS_FN:
        check('a tNNN on the same line silences the deferral', False, NO_FN)
        return
    line = ('First thing for the next session: read ' + RCA_PATH +
            ' -- filed as t1270, next actor Frontline.')
    check('a tNNN on the same line silences the deferral', findings(line) == [],
          'findings=%r' % (findings(line),))


def test_a_tnnn_on_a_neighbouring_line_does_not_silence_it():
    if not HAS_FN:
        check('a tNNN on a neighbouring line does not silence it', False, NO_FN)
        return
    text = ('First thing for the next session: read ' + RCA_PATH + '\n'
            '\n'
            'The sweep is tracked as t1092 and t900.\n')
    got = findings(text)
    check('a tNNN on a neighbouring line does not silence it',
          len(got) == 1 and str(got[0].get('path')) == RCA_PATH,
          'findings=%r' % (got,))


def test_two_unnumbered_deferrals_are_two_findings():
    if not HAS_FN:
        check('two unnumbered deferrals are two distinct findings', False, NO_FN)
        return
    check('two unnumbered deferrals are two distinct findings',
          paths_of(findings(TWO_LINES)) == sorted([RCA_PATH, OTHER_PATH]),
          'paths=%r' % (paths_of(findings(TWO_LINES)),))


def test_a_tnnn_on_one_deferral_leaves_the_other_reported():
    if not HAS_FN:
        check('a tNNN on one deferral leaves the other reported', False, NO_FN)
        return
    text = ('First thing for the next session: read ' + RCA_PATH + ' -- t1270\n'
            'And read ' + OTHER_PATH + ' first.\n')
    check('a tNNN on one deferral leaves the other reported',
          paths_of(findings(text)) == [OTHER_PATH],
          'paths=%r' % (paths_of(findings(text)),))


def test_read_evidence_is_used_not_just_accepted():
    if not HAS_FN:
        check('a path in read_paths is discharged, an absent path is not', False, NO_FN)
        return
    got = findings(TWO_LINES, read_paths={RCA_PATH})
    check('a path in read_paths is discharged, an absent path is not',
          paths_of(got) == [OTHER_PATH],
          'paths=%r' % (paths_of(got),))


def test_ordinary_prose_is_silent():
    if not HAS_FN:
        check('ordinary prose produces no finding', False, NO_FN)
        return
    for line in PROSE:
        check('ordinary prose is silent: %s' % (line,), findings(line) == [],
              'findings=%r' % (findings(line),))


def test_a_read_this_first_heading_names_no_path():
    if not HAS_RE:
        check('the READ THIS FIRST heading fires the regex', False, NO_RE)
    else:
        check('the READ THIS FIRST heading fires the regex',
              bool(cw.HANDOVER_READ_PHRASE.search('## READ THIS FIRST')),
              'the regex does not match the heading in workspace/HANDOVER-2026-09-01.md')
    if HAS_FN:
        check('a READ THIS FIRST heading with no path is not a finding',
              findings('## READ THIS FIRST') == [],
              'findings=%r' % (findings('## READ THIS FIRST'),))
    else:
        check('a READ THIS FIRST heading with no path is not a finding', False, NO_FN)


def test_a_finding_is_a_full_record():
    if not HAS_FN:
        check('a finding carries its line whole', False, NO_FN)
        check('a reported row has exactly the keys line, path, ids', False, NO_FN)
        check('a reported row carries no tNNN of its own', False, NO_FN)
        return
    got = findings(RCA_LINE)
    line = str(got[0].get('line')) if got else ''
    check('a finding carries its line whole, tail included',
          bool(got) and TAIL in line and line.strip() in RCA_LINE,
          'line=%r' % (line,))
    check('a reported row has exactly the keys line, path, ids',
          bool(got) and sorted(got[0]) == KEYS,
          'keys=%r' % (sorted(got[0]) if got else None,))
    check('a reported row carries no tNNN of its own',
          bool(got) and list(got[0].get('ids') or []) == [],
          'ids=%r' % (list(got[0].get('ids') or []) if got else None,))


def test_the_idiom_is_one_compiled_regex():
    if not HAS_RE:
        check('the idiom list is one compiled regex named HANDOVER_READ_PHRASE', False, NO_RE)
        return
    check('the idiom list is one compiled regex named HANDOVER_READ_PHRASE', HAS_RE, NO_RE)
    check('the regex fires on the RCA word order',
          bool(cw.HANDOVER_READ_PHRASE.search(
              'First thing for the next session: read docs/x.md first')),
          'no match')
    check('the regex fires on the read-X-first word order',
          bool(cw.HANDOVER_READ_PHRASE.search('read docs/x.md first')),
          'no match')
    for line in PROSE:
        check('the regex stays silent on prose: %s' % (line,),
              not cw.HANDOVER_READ_PHRASE.search(line), 'matched')


def test_the_check_is_wired_at_close():
    if not HAS_M:
        check('Checker.check_deferral_reads exists', False, NO_M)
    src = _MODULE.read_text(encoding='utf-8', errors='replace')
    anchor = src.find('self.check_deferral_numbers')
    check('the deferral checks are the neighbour named in the brief (anchor found in the source)',
          anchor >= 0, 'the source contains no "self.check_deferral_numbers"')
    near = src[anchor:anchor + 400] if anchor >= 0 else ''
    check('check_deferral_reads is in the check list next to the deferral checks',
          'check_deferral_reads' in near, 'window=%r' % (near,))


if __name__ == '__main__':
    print('test_check_workspace_handover_reads (t1270): a "read X first" line must carry a tNNN')
    print('  HANDOVER_READ_PHRASE present: %s; handover_read_findings present: %s; '
          'check_deferral_reads present: %s' % (HAS_RE, HAS_FN, HAS_M))
    for _name in sorted(k for k in globals() if k.startswith('test_')):
        _fn = globals()[_name]
        print('== ' + _name)
        try:
            _fn()
        except AssertionError as exc:
            check(_name + ' raised AssertionError', False, exc)
    print()
    if fails:
        print('FAIL -- %d assertion(s): %s' % (len(fails), '; '.join(fails)))
        sys.exit(1)
    print('PASS -- a "read X first" handover line must carry a tNNN or a recorded read')
    sys.exit(0)
