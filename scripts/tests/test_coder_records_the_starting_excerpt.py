#!/usr/bin/env python3
"""t991: coder.py must record what the STARTING CONTEXT already showed.

The dispatch that filed this (p327-t778-session-review, on swift) shows the harness refusing
the model the file it was told to edit:

    refused: scripts/session_review.py: no room for more of it beside the prompt. Work from
    what you already have

The model DID have that file: it was in STARTING CONTEXT, served whole. coder.py's ask loop
asks unseen(root, rel, shown_to.get(rel, 0)) for the REST of a file already opened, and
shown_to is written in exactly one place: the ask loop's `if rel in opened:` branch. The
starting-context build (shown = {rel: excerpt(rel) for rel, _ in files}, line 2678 of that
file) never records it. So a file served whole is treated as if NONE of it had been served:
the next request re-serves lines 1..N, bytes the model already has, and the fit guard refuses
that duplicate with the message above. A whole-file excerpt of a large file is exactly the
size that will not fit a second time, so the model is denied the source for the edit its
brief asked for.

The fix is a record, and the record has to be the right quantity. unseen() serves every line
AFTER the line number it is given and says nothing about the lines before it, so the record
must be the run of lines 1, 2, 3, ... that the excerpt shows with NO GAP -- not the largest
line number in it. numbered() shows keyword windows for a file too long to show whole, and the
highest window of a big file sits far below the head: recording it would tell the harness the
model holds lines 1..9000 when it has never seen them, and the model would be served the tail
and refused the head by the same guard. That is t991 wearing the fix.

Tests 1-7 pin that contract as behaviour, computed from coder.py's own listings so that no
assertion hard-codes a numbering format this suite invented. Test 8 pins that the run loop
CALLS the helper: a correct function nobody calls fixes nothing, which is the failure mode
this repository files tasks about.

Run: python3 scripts/tests/test_coder_records_the_starting_excerpt.py
"""
import ast
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
CODER_PATH = ROOT / 'projects' / 'local-inference' / 'coder.py'

_SPEC = importlib.util.spec_from_file_location('coder_under_test', CODER_PATH)
coder = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(coder)

_LEADING = re.compile(r'^\s*(\d+)')


def body_of(n):
    # A file whose lines never begin with a digit, so a leading integer in an excerpt can
    # only be the harness's own line-number prefix -- never file content.
    return '\n'.join('line %d of the target file' % i for i in range(1, n + 1))


def numbered_as_harness_does(text, upto):
    # The shape the harness shows: the line number, then the line.
    return '\n'.join('%d %s' % (i, line) for i, line in enumerate(text.splitlines()[:upto], start=1))


def line_numbers_in(excerpt):
    # Every leading integer in a numbered listing. Derived from the text, so nothing here
    # hard-codes coder.py's format (it pads to five columns today; a format change must not
    # make this suite red for the wrong reason).
    found = set()
    for line in (excerpt or '').splitlines():
        m = _LEADING.match(line)
        if m:
            found.add(int(m.group(1)))
    return found


def gapless_prefix_of(excerpt, limit):
    # How many lines from the start of the file `excerpt` shows with no gap, capped by the
    # file's length. This is the expectation the helper is held to, computed from the listing
    # the harness produced: the same rule unseen() needs, because it serves the rest AFTER the
    # line it is handed and is silent about everything before it.
    present = line_numbers_in(excerpt)
    n = 0
    while n + 1 in present and n + 1 <= limit:
        n += 1
    return n


def test_records_a_file_shown_whole():
    text = body_of(40)
    shown = numbered_as_harness_does(text, 40)
    assert coder.excerpt_last_line(text, shown) == 40, (
        'an excerpt running to the end of the file must report 40, so that a later request '
        'for that file is answered with the you-already-have-it repeat note instead of a '
        'refused duplicate')


def test_a_partial_excerpt_reports_where_it_stopped():
    text = body_of(200)
    shown = numbered_as_harness_does(text, 40)
    assert coder.excerpt_last_line(text, shown) == 40, (
        'the record is where the excerpt STOPPED: lines 41..200 are the rest the model is '
        'entitled to ask for, and 40 is what makes the next slice the rest, not a repeat')


def test_a_line_number_past_the_end_of_the_file_is_clamped_to_the_file():
    text = body_of(10)
    impossible = '\n'.join('%d l%d' % (i, i) for i in range(1, 501))
    n = coder.excerpt_last_line(text, impossible)
    assert n == 10, (
        'excerpt_last_line returned %r for a 10-line file. The record feeds a fit check, so '
        'a number past the end inflates what the harness believes it served and buys room the '
        'window does not have; it cannot exceed the lines the file has' % (n,))


def test_a_listing_with_no_line_numbers_proved_nothing():
    text = body_of(10)
    assert coder.excerpt_last_line(text, 'Here is roughly a class and a main.') == 0, (
        'an excerpt carrying no line numbers showed no recorded lines. Claiming coverage from '
        'prose is how a model ends up refused a file it was never given')
    assert coder.excerpt_last_line(text, '') == 0, 'an empty excerpt showed nothing'
    assert coder.excerpt_last_line('', '') == 0, 'an empty file has no lines to have shown'


def test_the_numbering_coder_itself_emits_is_understood():
    text = '\n'.join('def f%d():\n    return %d' % (i, i) for i in range(1, 61))   # 120 lines
    shown = coder.numbered(text, ('f57',))
    assert shown, 'coder.numbered() returned nothing for a file with a matching keyword'
    n = coder.excerpt_last_line(text, shown)
    assert n == gapless_prefix_of(shown, 120) > 0, (
        'excerpt_last_line read %r from a listing of 120 lines whose line numbers run from 1 '
        'to %d; the record must parse the format the harness actually produces, not one this '
        'test invented' % (n, max(line_numbers_in(shown) or {0})))
    assert n >= 57, (
        'the window around line 57 must be recorded as reaching at least line 57; got %r' % (n,))


def test_a_file_too_long_for_one_excerpt_reports_where_it_stopped():
    n_lines = (int(coder.EXCERPT_CAP) // 22) + 500      # numbered() is smaller than this
    text = body_of(n_lines)
    shown = coder.numbered(text, ())
    assert shown, 'coder.numbered() returned nothing for a plain long file'
    n = coder.excerpt_last_line(text, shown)
    assert n == gapless_prefix_of(shown, n_lines) > 0, (
        'excerpt_last_line read %r from a listing of %d lines whose line numbers run from 1 '
        'to %d' % (n, n_lines, max(line_numbers_in(shown) or {0})))
    if len(shown) <= len(text):
        assert n < n_lines, (
            'the excerpt was cut at %d chars of a %d-char file, so it cannot reach line %d. '
            'The record must say where the excerpt STOPPED -- that gap is the whole reason the '
            'model asks for the rest' % (len(shown), len(text), n_lines))


def test_a_windowed_excerpt_records_no_more_than_the_head_the_model_can_have():
    # The assertion that separates a record from a guess. numbered() shows keyword windows, not
    # a prefix: asking for a long file with a keyword at line 9000 yields a window around it,
    # and the head of the file is in none of them. Taking the largest line number in such a
    # listing -- the shortest implementation that passes tests 1-6 -- records 9000, and
    # unseen(root, rel, 9000) then serves the tail forever. The model that asked for the file
    # it was told to edit receives the tail, and is told by the same fit guard that there is no
    # room for more. That is t991 wearing the fix.
    n_lines = (int(coder.EXCERPT_CAP) // 22) + 500
    lines = ['line %d of the target file' % i for i in range(1, n_lines + 1)]
    lines[8999] = 'needle: the target suite must be read in full'      # line 9000
    text = '\n'.join(lines)
    shown = coder.numbered(text, ('needle',))
    assert shown and '9000' in shown, (
        'the fixture no longer puts a window around line 9000; if numbered() changed, read and '
        'update this suite -- do not delete it')
    n = coder.excerpt_last_line(text, shown)
    assert n == gapless_prefix_of(shown, n_lines), (
        'excerpt_last_line reported %r for a listing that starts at line %d. A file shown in '
        'windows has shown no prefix at all; the record is the run of lines from line 1 with no '
        'gap, because unseen() serves the rest AFTER the line it is given and says nothing about '
        'the lines before it' % (n, min(line_numbers_in(shown))))
    assert n < 9000, (
        'the excerpt showed a window around line 9000 and never showed lines 1..8970, so '
        'recording %r tells the harness the model holds the head of this file. It does not, and '
        'the missing head is the defect this suite exists to prevent' % (n,))


def test_the_run_loop_records_it_when_it_builds_the_starting_context():
    # The wiring. A helper the run loop never calls fixes nothing, and a suite that goes green
    # on it is a check satisfied by a dead path.
    src = CODER_PATH.read_text(encoding='utf-8')
    tree = ast.parse(src)
    hosts = [node for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and 'shown = {rel: excerpt(rel)' in (ast.get_source_segment(src, node) or '')]
    assert len(hosts) >= 1, (
        'the starting-context build (shown = {rel: excerpt(rel) ...}) was not found inside a '
        'function. If it moved, the record has to move with it, so this suite must be read and '
        'updated -- not deleted')
    body = ast.get_source_segment(src, hosts[0])
    assert 'excerpt_last_line' in body, (
        'the function that builds the starting context must CALL excerpt_last_line; adding it '
        'beside the loop is a decoration')
    assert 'shown_to[' in body, (
        'the record has to land in shown_to, the map the ask loop already reads at '
        'unseen(root, rel, shown_to.get(rel, 0))')


TESTS = [test_records_a_file_shown_whole,
         test_a_partial_excerpt_reports_where_it_stopped,
         test_a_line_number_past_the_end_of_the_file_is_clamped_to_the_file,
         test_a_listing_with_no_line_numbers_proved_nothing,
         test_the_numbering_coder_itself_emits_is_understood,
         test_a_file_too_long_for_one_excerpt_reports_where_it_stopped,
         test_a_windowed_excerpt_records_no_more_than_the_head_the_model_can_have,
         test_the_run_loop_records_it_when_it_builds_the_starting_context]

if __name__ == '__main__':
    failed = 0
    for t in TESTS:
        try:
            t()
            print('  PASS  %s' % t.__name__)
        except AssertionError as e:
            failed += 1
            print('  FAIL  %s: %s' % (t.__name__, e))
        except Exception as e:                       # noqa: BLE001 -- a crash is a finding
            failed += 1
            print('  FAIL  %s: %s: %s' % (t.__name__, type(e).__name__, e))
    print('\n%d/%d passed' % (len(TESTS) - failed, len(TESTS)))
    sys.exit(1 if failed else 0)
