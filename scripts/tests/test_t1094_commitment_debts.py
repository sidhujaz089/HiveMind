#!/usr/bin/env python3
'''Oracle for t1094 -- a number-less queue/next-step commitment is an open debt.

THE DEFECT (workspace/rca/aeaf578817c2.md, finding of session 7a0e9f2d, turn 336 of 364):
the assistant wrote, verbatim,

    So: the one that's fully specified from real data, I'll queue now.

and the session closed GREEN -- 'committed and pushed, origin 1903838, 0 unpushed, all
checks green'. The commitment lived only in prose: it never became a numbered row in
taskstore.py, so no later session inherited it, and no close-out endpoint asked whether a
sentence of the form 'I'll queue / this is next / I'll file X' had acquired a task number.
The RCA records the CLASS, not the instance -- the same session self-flagged the shape three
times: 'No. I said those are next and then reported on the timer instead -- that is the exact
gap this session keeps finding'. A per-brief fix cannot catch a class. The on-disk trace is
workspace/session-reviews/promise-ledger.jsonl: 38 rows, every one 'undischarged', every one
'artefact: []', all written at the REVIEW endpoint (scripts/session_review.py:71).

WHAT THIS SUITE PINS, in the order the defect needs it:
  1. the incident sentence, verbatim, IS a debt, and the record names the whole sentence;
  2. the cure silences it: a tNNN in the commitment's own line discharges it -- the number
     must be USED, not merely present somewhere;
  3. every idiom in the RCA's list is a debt, not one idiom (a regex that knows only
     'queue' satisfies nothing);
  4. a number on a LATER line does not discharge an earlier commitment -- the number must
     belong to the commitment's own line, which is what 'unmatched' means at the close;
  5. ordinary prose stays silent -- the decay guard, and the only thing that keeps this from
     becoming the gate everyone routes around with --no-verify within a week;
  6. a debt is a record: the quote is reported whole, never cut (never-truncate-a-record);
  7. the contract's shape: one text argument, a list of dicts with keys quote and number,
     and number always '' for a debt.

RED ON MAIN: scripts/session_end.py defines no commitment_debts, so HAS_FN is False and
every assertion below reports BAD with that stated reason. Nothing calls a missing name
directly, so the red is the missing change and never an AttributeError.

Touches no live state: every fixture is a string literal in this file. No file is read or
written, no subprocess, no urlopen, no model call, no process left running.
Run: python3 scripts/tests/test_t1094_commitment_debts.py   (pytest is not installed)
'''
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fails = []
total = 0

try:
    import session_end as se
except Exception as exc:  # recorded, not swallowed: an unimportable module is a finding
    se = None
    fails.append('scripts/session_end.py cannot be imported: %r' % (exc,))

HAS_FN = bool(se) and callable(getattr(se, 'commitment_debts', None))
NO_FN = ('scripts/session_end.py defines no commitment_debts() -- that is the change this '
         'scope adds, so every assertion is reported BAD for that one reason')

# The incident, as one line of a final summary. Verbatim shape from the RCA's finding record.
INCIDENT = ("The oracle work needs a fresh session. So: the one that's fully specified "
            "from real data, I'll queue now.")

# The cure: the same commitment, carrying its number in its own line.
DISCHARGED = ("So: the one that's fully specified from real data, I'll queue now -- "
              "filed as t812.")

# The RCA's own list of the shape, each a DISTINCT sentence with a DISTINCT object.
IDIOMS = [
    "I'll queue the sweep oracle.",
    "I'll file the migration check as its own task.",
    "This is next: probe the end-to-end pipeline.",
    "Single next step: review and merge the four branches.",
    "I'll write the restore test after the timers land.",
]

# Ordinary prose that names a queue, a runner, a next session -- and owes nothing.
SILENT = [
    "The night queue runs at 02:00 and the queue runner builds the list.",
    "A later session can pick the sweep up after the timers land.",
    "We have queue timers and a queue runner in this session.",
    "The queue runner was rewritten to read QUEUE.md before dispatch.",
]

# A long commitment, to prove the record is reported whole and not sliced.
LONG = ("I'll queue the oracle for the backup restore path, covering the prune, the bundle "
        "index, the report row and the NAS verify, as its own task.")


def check(label, cond, detail=''):
    global total
    total += 1
    print('  [%s] %s' % ('ok' if cond else 'BAD', label)
          + ('  :: ' + detail if detail else ''))
    if not cond:
        fails.append(label)


def have_fn(label):
    if HAS_FN:
        return True
    check(label, False, NO_FN)
    return False


def show(debts):
    # never-truncate-a-record: print every debt whole, in full.
    return ' | '.join(repr(d) for d in debts) if debts else 'none'


def test_the_incident_sentence_is_a_debt():
    if not have_fn('incident: commitment_debts exists'):
        return
    debts = se.commitment_debts(INCIDENT)
    check('incident: the sentence yields exactly one debt', len(debts) == 1,
          'found %d: %s' % (len(debts), show(debts)))
    if debts:
        check('incident: the debt quotes the WHOLE line the model wrote',
              debts[0].get('quote') == INCIDENT, 'quote=%r' % (debts[0].get('quote'),))
        check('incident: a debt carries no number (that is what makes it a debt)',
              debts[0].get('number') == '', 'number=%r' % (debts[0].get('number'),))


def test_a_numbered_deferral_is_discharged():
    if not have_fn('discharge: commitment_debts exists'):
        return
    debts = se.commitment_debts(DISCHARGED)
    check('discharge: a commitment whose own line names t812 is NOT a debt', debts == [],
          'found %d: %s' % (len(debts), show(debts)))


def test_every_idiom_in_the_rca_list_is_a_debt():
    if not have_fn('idioms: commitment_debts exists'):
        return
    text = '\n'.join(IDIOMS)
    debts = se.commitment_debts(text)
    quotes = set()
    for d in debts:
        if isinstance(d, dict):
            quotes.add(d.get('quote'))
    missing = [s for s in IDIOMS if s not in quotes]
    check('idioms: all five of the RCA sentences are reported, each whole', not missing,
          'missing=%r from: %s' % (missing, show(debts)))
    check('idioms: five commitment lines yield five debts (one per line, no merging)',
          len(debts) == 5, 'found %d: %s' % (len(debts), show(debts)))


def test_a_number_on_a_later_line_does_not_discharge_it():
    if not have_fn('later-line: commitment_debts exists'):
        return
    text = "I'll queue the sweep oracle.\n\nFiled the migration check as t812."
    debts = se.commitment_debts(text)
    check('later-line: a number in a DIFFERENT line leaves the commitment a debt',
          len(debts) == 1, 'found %d: %s' % (len(debts), show(debts)))
    if debts:
        q = debts[0].get('quote') or ''
        check('later-line: the debt is the commitment line, not the line that carries t812',
              q.startswith("I'll queue") and 't812' not in q, 'quote=%r' % (q,))


def test_ordinary_prose_is_silent():
    if not have_fn('silent: commitment_debts exists'):
        return
    hits = []
    for line in SILENT:
        for d in se.commitment_debts(line):
            hits.append((line, d))
    check('silent: prose that names a queue, a runner or a later session yields NO debt '
          '(a gate that fires on ordinary prose is --no-verify-d within a week)',
          not hits,
          'false matches: %s' % (' | '.join('%s -> %r' % (l, d) for l, d in hits),))


def test_a_debt_is_reported_whole():
    if not have_fn('whole: commitment_debts exists'):
        return
    debts = se.commitment_debts(LONG)
    check('whole: the long commitment yields one debt', len(debts) == 1,
          'found %d: %s' % (len(debts), show(debts)))
    if debts:
        q = debts[0].get('quote') or ''
        check('whole: the quote is the ENTIRE sentence, not a prefix of it',
              q == LONG, 'len=%d of %d quote=%r' % (len(q), len(LONG), q))
        check('whole: the tail of the commitment survives (a cut record looks complete)',
              'NAS verify' in q and q.startswith("I'll queue"),
              'quote=%r' % (q,))


def test_the_contract_shape():
    if not have_fn('shape: commitment_debts exists'):
        return
    params = list(inspect.signature(se.commitment_debts).parameters)
    check('shape: it takes one text argument (the next scope calls it with the transcript)',
          params == ['text'], 'params=%r' % (params,))
    debts = se.commitment_debts(IDIOMS[0])
    shape_ok = (isinstance(debts, list) and len(debts) == 1
                and isinstance(debts[0], dict)
                and set(debts[0]) == {'quote', 'number'}
                and debts[0]['number'] == '')
    check('shape: a debt is a record with keys quote and number, number empty',
          shape_ok, 'found %s' % (show(debts),))


TESTS = [
    test_the_incident_sentence_is_a_debt,
    test_a_numbered_deferral_is_discharged,
    test_every_idiom_in_the_rca_list_is_a_debt,
    test_a_number_on_a_later_line_does_not_discharge_it,
    test_ordinary_prose_is_silent,
    test_a_debt_is_reported_whole,
    test_the_contract_shape,
]

if __name__ == '__main__':
    for _t in TESTS:
        _t()
    print('%d/%d assertions ok' % (total - len(fails), total))
    if fails:
        print('FAIL: ' + '; '.join(fails))
        sys.exit(1)
    print('ok: t1094 -- a number-less queue/next-step commitment is an open debt')
