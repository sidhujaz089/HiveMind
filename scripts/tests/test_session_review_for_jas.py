#!/usr/bin/env python3
'''GATE (t681): a Decide task arrives self-contained.

Jas, t633 note 2026-10-04 -- he cannot see the context of a Decide task from its title and body;
when input is needed from him the task must summarize the whole situation and give options with
pros and cons.

Today scripts/session_review.py, inside _for_jas, titles the task from the first 80 characters of
the quote, and its body is the opening sentence, the quote, the where label and the one-line
why_missed -- no situation, no options, no pros and cons, no recommendation, nothing about what an
answer authorizes, no turns around the quote, no date. The EXTRACT prompt that produces the row
asks the model for none of those fields.

This gate feeds _for_jas a row that DOES carry the fields and asserts the body a person can act
on, a title that is the question and never a cut-off quote, ending on a word boundary, the two
session turns the harness attaches, and a row missing its options logged as for-jas-incomplete
instead of filed bare.

Hermetic: tempfile.mkdtemp only; sr.BASE, sr.FOR_JAS and sr.LEDGER are redirected into it; a fake
file_task; no model call, no live queue, no repository file written, nothing left running.
'''
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import session_review as sr  # noqa: E402

P = F = 0
Q = chr(34)   # a double quote, spelled, so this file needs no escaped quotes in its own source


def report(name, got, want):
    global P, F
    ok = got == want
    P += ok
    F += not ok
    label = 'PASS' if ok else 'FAIL'
    print(f'  {label}  {name}' + ('' if ok else f'   got {got!r}, want {want!r}'))


def missing(text, needles):
    return [n for n in needles if n not in text]


def events():
    if not sr.LEDGER.exists():
        return []
    return [json.loads(line) for line in
            sr.LEDGER.read_text(encoding='utf-8').splitlines() if line.strip()]


tmp = Path(tempfile.mkdtemp(prefix='t681-for-jas-'))
sr.BASE = tmp
sr.FOR_JAS = tmp / 'for-jas'
sr.LEDGER = tmp / 'ledger.jsonl'

EPOCH = 1759651200
DATE = time.strftime('%Y-%m-%d', time.gmtime(EPOCH))
REQ = {'id': 's-77', 'harness': 'claude', 'ended_epoch': EPOCH}
QUOTE = 'Want me to add the whole situation to every Decide task, Jas?'
QUESTION = ('Which of these three ways should session review ask you for input: the full brief '
            'in the task body, a one-paragraph summary, or a note on the queue?')
SITUATION = ('The session offered Jas a choice about how much context a Decide task carries, '
             'nobody answered it, and the queue row kept only a fragment of the offer.')
OPTIONS = [
    {'label': 'full brief in the body',
     'pros': 'everything needed to answer is in one place',
     'cons': 'every Decide task body gets longer'},
    {'label': 'one-paragraph summary',
     'pros': 'short enough to read on a phone',
     'cons': 'the options and their pros and cons are not in it'},
]
RECOMMENDATION = 'Full brief in the body: a decision made without the options is not a decision.'
AUTHORIZES = 'A yes adopts the body template; a no leaves Decide tasks as they are today.'
PRECEDING = [('assistant', 'I can put the whole situation in every Decide task body you want.'),
             ('user', 'Yes -- the whole situation, with options and pros and cons, not a quote.')]
COMPLETE = {'kind': 'for-jas', 'quote': QUOTE, 'where': 'assistant, turn 4 of 9',
            'why_missed': 'the question was never answered', 'situation': SITUATION,
            'question': QUESTION, 'options': OPTIONS, 'recommendation': RECOMMENDATION,
            'authorizes': AUTHORIZES, 'preceding': PRECEDING}
BARE = {'kind': 'for-jas', 'quote': QUOTE, 'where': 'assistant, turn 4 of 9',
        'why_missed': 'the question was never answered'}


def test_complete_row_files_a_self_contained_task():
    cap = []

    def filing(title, body):
        cap.append((title, body))
        return 't900'

    report('a for-jas row is filed, and the call says so',
           sr._for_jas('k1', REQ, COMPLETE, filing), True)
    title, body = cap[-1]
    report('the body carries the situation, BOTH options with their own pros and cons, the '
           'recommendation, and what an answer authorizes',
           missing(body, [SITUATION, OPTIONS[0]['label'], OPTIONS[0]['pros'], OPTIONS[0]['cons'],
                          OPTIONS[1]['label'], OPTIONS[1]['pros'], OPTIONS[1]['cons'],
                          RECOMMENDATION, AUTHORIZES]), [])
    report('the body is laid out under headings a person can scan on a phone',
           missing(body, ['Situation:', 'Question:', 'Options:', 'Pros:', 'Cons:',
                          'Recommendation:', 'What your answer authorizes', 'Evidence:']), [])
    report('the verbatim quote, the where and the why are still in the body',
           missing(body, [QUOTE, 'assistant, turn 4 of 9', 'the question was never answered']), [])
    report('the two turns before the quote are in the body, as two distinct texts',
           missing(body, [PRECEDING[0][1], PRECEDING[1][1]]), [])
    report('they are labelled as the turns that preceded the quote',
           'Preceding turns' in body, True)
    report('the evidence block names the session and the date its review ran',
           missing(body, ['s-77', DATE]), [])
    report('the group marker that folds a session into one task is kept',
           'GROUP: session:s-77' in body, True)
    report('the body still opens by naming where it came from',
           'From the session review' in body, True)
    rest = title[len('Decide: '):]
    report('the title opens the Decide family', title.startswith('Decide: '), True)
    report('the title is the question, and the quote is NOT in it',
           (rest == QUESTION[:len(rest)], QUOTE in title), (True, False))
    report('the title is a complete sentence under 90 characters', len(title) < 90, True)
    report('a truncated title ends on a word boundary, not inside a word',
           rest == QUESTION or (len(rest) < len(QUESTION) and QUESTION[len(rest)] == ' '), True)
    report('the same key does not file a second task',
           sr._for_jas('k1', REQ, COMPLETE, filing), False)
    report('and the repeat filed nothing', len(cap), 1)


def test_a_row_without_its_fields_is_logged_not_filed_bare():
    cap = []

    def filing(title, body):
        cap.append((title, body))
        return 't901'

    report('an incomplete row is still filed, because it is a real question for Jas',
           sr._for_jas('k2', REQ, BARE, filing), True)
    ev = events()
    report('the ledger records the ordinary event AND the incomplete one',
           sorted({e.get('event') for e in ev}), ['for-jas', 'for-jas-incomplete'])
    report('the incomplete event names the same key as the row',
           sorted({str(e.get('key')) for e in ev if e.get('event') == 'for-jas-incomplete'}),
           ['k2'])
    ititle, ibody = cap[-1]
    report('the body says what the model did not supply, in words',
           missing(ibody.replace(QUOTE, ''), ['situation', 'options', 'recommendation']), [])
    report('and it keeps the quote, the where and the why',
           missing(ibody, [QUOTE, 'assistant, turn 4 of 9', 'the question was never answered']), [])
    report('an incomplete title is a complete sentence under 90 characters',
           (ititle.startswith('Decide: '), len(ititle) < 90), (True, True))
    report('a row whose session has no end date is filed, and does not raise',
           sr._for_jas('k3', {'id': 's-78', 'harness': 'claude'}, BARE, filing), True)
    report('and its body says the date was not recorded',
           'date not recorded' in cap[-1][1], True)
    report('  without naming a date that was never given', DATE in cap[-1][1], False)


def test_the_harness_attaches_the_preceding_turns():
    fn = getattr(sr, 'preceding_turns', None)
    report('session_review defines preceding_turns(turns, quote)', fn is not None, True)
    if fn is None:
        return
    LONG = 'the scoper automation needs a gate ' + 'and the gate must fire by itself ' * 60
    SESSION = [('user', 'turn one of the session'),
               ('assistant', LONG),
               ('user', 'turn three: what about the Decide tasks?'),
               ('assistant', QUOTE),
               ('user', 'turn five')]
    got = fn(SESSION, QUOTE)
    report('it returns the two turns before the turn carrying the quote, in order',
           [s.split(':', 1)[0] for s in got], ['assistant', 'user'])
    report('the turn carrying the quote is not in it', any(QUOTE in s for s in got), False)
    report('a turn before those two is not in it',
           any('turn one of the session' in s for s in got), False)
    report('each entry carries its role and its own text, not one placeholder',
           ('scoper' in got[0], 'turn three' in got[1]), (True, True))
    report('a long turn is trimmed and says it was cut',
           (len(got[0]) <= 340, got[0].endswith('[...]')), (True, True))
    report('a quote in the first turn of the session yields no turns',
           fn(SESSION, 'turn one of the session'), [])
    report('a quote the session does not contain yields no turns',
           fn(SESSION, 'no such sentence anywhere here'), [])
    report('an empty quote yields no turns', fn(SESSION, ''), [])


def test_the_extract_prompt_asks_the_model_for_the_fields():
    need = ['situation', 'question', 'options', 'pros', 'cons', 'recommendation', 'authorizes',
            'at least 2']
    low = sr.EXTRACT.lower()
    report('the EXTRACT prompt asks for every field a Decide task needs',
           [w for w in need if w not in low], [])
    lo = sr.EXTRACT.find('(d) ')
    hi = sr.EXTRACT.find('Do NOT report')
    item = sr.EXTRACT[lo:hi]
    report('in the for-jas item (d), where the model reads the rules',
           (lo >= 0, hi > lo, 'situation' in item.lower(), 'authorizes' in item.lower()),
           (True, True, True, True))
    a = sr.EXTRACT.find('End your answer with ONLY a JSON list')
    b = sr.EXTRACT.find('SESSION TEXT')
    schema = sr.EXTRACT[a:b]
    report('and the JSON schema the answer must match carries those keys',
           missing(schema, [f'{Q}situation{Q}', f'{Q}question{Q}', f'{Q}options{Q}', f'{Q}pros{Q}',
                            f'{Q}cons{Q}', f'{Q}recommendation{Q}', f'{Q}authorizes{Q}']), [])
    report('the schema is still a .format() template: its braces are doubled',
           (schema.count('{{') >= 1, schema.count('}}') >= 1), (True, True))


if __name__ == '__main__':
    for f in (test_complete_row_files_a_self_contained_task,
              test_a_row_without_its_fields_is_logged_not_filed_bare,
              test_the_harness_attaches_the_preceding_turns,
              test_the_extract_prompt_asks_the_model_for_the_fields):
        print(f.__name__ + ':')
        f()
    print(f'\n{P} passed, {F} failed')
    sys.exit(1 if F else 0)
