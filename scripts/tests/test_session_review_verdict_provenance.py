'''t1052 verdict provenance on triage rows, a per-batch ledger, and no code-default finding.

Red on main for ONE reason: scripts/session_review.py triage() records a row only with the verdicts
the Manager can choose, keeps no record of the batch it sent, throws the model reply away, and files
every item the asker did not answer as verdict='missed' -- a claim about the session -- instead of
verdict='unanswered', the receipt for a reviewer call that did not answer. In the file as it stands
the parse loop prints NOTE: unreadable triage line with the reply sliced to 120 characters and counts
nothing, and extract_scripted() turns every row whose verdict is not drop into a finding, so a batch
that parsed as nothing becomes one finding per item.

Measured on the 2026-10-08 review of session aeef1f57 (workspace/rca/7485f9bf459e.md): 9 of 12
batches ended done_reason=length with zero characters out, 12 replies came back as one-key-per-line
JSON this parse loop cannot read, and 3 batches died on the swift lease held by t785. Every one of
those reviewer-capability failures was filed as a content finding.

This suite pins, and only this:
  1 a row for an item the asker did not answer carries verdict='unanswered', never 'missed', and
    verdict_source='code-default';
  2 a row for an item it did answer carries verdict_source='model' and keeps the fields it has today;
  3 every batch appends one JSONL row to sr.TRIAGE_BATCHES with exactly the nine fields
    request, batch, item_ns, turns, n_items, prompt_chars, answer_chars, parse_failures, answered_ns;
  4 a batch with any unreadable line has its whole reply saved unsliced under sr.TRIAGE_RAW as
    SID-batch.txt, and a fully parsed batch saves nothing;
  5 extract_scripted() excludes the non-finding verdicts by name, so a dead batch mints zero findings.

The ask() side done_reason, the budget sizing, BATCH size, _terms() determinism, the t607 deferral
write-gate (t1048 owns it: scripts/tests/test_t1048_deferral_write_gate.py) and the reviewer-capability
class in workspace/failure-schema.json are other scopes, and this suite does not ask for them.

No live state: sr.TRIAGE_BATCHES and sr.TRIAGE_RAW are redirected into a tempfile.mkdtemp directory
and restored in a finally. No subprocess, no urlopen, no model call, nothing under workspace/.
Run: python3 scripts/tests/test_session_review_verdict_provenance.py (pytest is not installed).
Do not invent a function, a module, or a file path: everything used exists in the source named.
'''
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import session_review as sr

SID = 'aeef1f57'
FIELDS = {'request', 'batch', 'item_ns', 'turns', 'n_items', 'prompt_chars',
          'answer_chars', 'parse_failures', 'answered_ns'}
LONG = 'x' * 400
QUOTES = ['I will surface only a stall, a genuine crash, another false clearance, '
          'or a finding that touches Live.',
          'we should record a reviewer capability failure, not call it dropped work']


def items(n, start_n=1, turn0=10):
    out = []
    for i in range(n):
        k = start_n + i
        out.append({'n': k, 'quote': QUOTES[i % 2],
                    'context': 'context sentence %d: nothing later in the session returns to it' % k,
                    'type': 'DEFER', 'turn': turn0 + 3 * i, 'repeats': [], 'near': [],
                    'commits': [], 'offered': []})
    return out


def swift_reply(n, start=1):
    # the real 2026-10-08 shape: one JSON key per line, so no line the parse loop can use
    lines = []
    for i in range(start, start + n):
        lines.append('{')
        lines.append('  %s,' % json.dumps({'n': i})[1:-1])
        lines.append('  %s' % json.dumps({'verdict': 'drop'})[1:-1])
        lines.append('}')
    return '\n'.join(lines)


def long_swift_reply(n, start=1):
    lines = []
    for i in range(start, start + n):
        lines.append('{')
        lines.append('  %s,' % json.dumps({'n': i})[1:-1])
        lines.append('  %s,' % json.dumps({'verdict': 'drop'})[1:-1])
        lines.append('  %s,' % json.dumps({'what': LONG})[1:-1])
        lines.append('}')
    return '\n'.join(lines)


def tidy_reply(n, start=1):
    out = []
    for i in range(start, start + n):
        if i % 2:
            out.append(json.dumps({'n': i, 'verdict': 'drop'}))
        else:
            out.append(json.dumps({'n': i, 'verdict': 'missed', 'kind': 'unresolved',
                                   'what': 'file the ledger row for item %d' % i}))
    return '\n'.join(out)


class Asker:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.purposes = []

    def __call__(self, prompt, purpose):
        self.prompts.append(prompt)
        self.purposes.append(purpose)
        if not self.replies:
            raise AssertionError('triage asked the model %d times for %d scripted replies'
                                 % (len(self.prompts), len(self.replies) + len(self.prompts)))
        return self.replies.pop(0)


def read_jsonl(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def run(items_list, replies):
    tmp = Path(tempfile.mkdtemp(prefix='t1052-'))
    saved = (getattr(sr, 'TRIAGE_BATCHES', None), getattr(sr, 'TRIAGE_RAW', None),
             sr.BASE, sr.FINDINGS, sr.LEDGER, sr.PENDING)
    sr.TRIAGE_BATCHES = tmp / 'triage-batches.jsonl'
    sr.TRIAGE_RAW = tmp / 'raw'
    asker = Asker(replies)
    try:
        rows = sr.triage(items_list, asker, 'swift', SID, '2026-10-08')
        ledger = read_jsonl(sr.TRIAGE_BATCHES)
        raws = {}
        if sr.TRIAGE_RAW.is_dir():
            for p in sorted(sr.TRIAGE_RAW.glob('*.txt')):
                raws[p.name] = p.read_text(encoding='utf-8')
    finally:
        sr.TRIAGE_BATCHES, sr.TRIAGE_RAW, sr.BASE, sr.FINDINGS, sr.LEDGER, sr.PENDING = saved
        shutil.rmtree(tmp, ignore_errors=True)
    return rows, ledger, raws, asker


def source_of(name):
    src = (ROOT / 'scripts' / 'session_review.py').read_text(encoding='utf-8')
    return src[src.index('def %s(' % name):]


def test_an_unanswered_item_is_verdict_unanswered_and_source_code_default():
    rows, _ledger, _raws, asker = run(items(sr.BATCH), [swift_reply(sr.BATCH)])
    assert len(asker.prompts) == 1, 'one ask per batch, got %d' % len(asker.prompts)
    assert asker.purposes == ['session review: triage 1/1'], asker.purposes
    assert len(rows) == sr.BATCH, '%d rows for %d items' % (len(rows), sr.BATCH)
    assert [r['n'] for r in rows] == list(range(1, sr.BATCH + 1)), [r['n'] for r in rows]
    assert {r['verdict'] for r in rows} == {'unanswered'}, sorted({r['verdict'] for r in rows})
    assert {r.get('verdict_source') for r in rows} == {'code-default'}, sorted(
        {str(r.get('verdict_source')) for r in rows})
    for r in rows:
        assert r.get('kind') == 'deferral', r
        assert r.get('what') == '', r
        assert r.get('cover') == '', r


def test_an_answered_item_carries_verdict_source_model():
    reply = tidy_reply(sr.BATCH)
    rows, _ledger, raws, asker = run(items(sr.BATCH), [reply])
    assert asker.prompts[0].startswith('You are triaging sentences from a finished working session')
    assert [r['verdict'] for r in rows] == ['drop' if k % 2 else 'missed'
                                            for k in range(1, sr.BATCH + 1)], [r['verdict'] for r in rows]
    assert {r.get('verdict_source') for r in rows} == {'model'}, sorted(
        {str(r.get('verdict_source')) for r in rows})
    assert rows[1]['what'] == 'file the ledger row for item 2', rows[1]['what']
    assert rows[1]['kind'] == 'unresolved', rows[1]['kind']
    assert rows[0]['quote'] == QUOTES[0], rows[0]['quote']
    assert rows[0]['turn'] == 10, rows[0]['turn']
    assert raws == {}, 'a batch with no unreadable line saves no raw reply: %r' % sorted(raws)


def test_the_batch_row_records_the_nine_fields_of_its_own_batch():
    reply = swift_reply(sr.BATCH)
    _rows, ledger, _raws, asker = run(items(sr.BATCH), [reply])
    assert len(ledger) == 1, 'one row per batch, got %d' % len(ledger)
    row = ledger[0]
    assert set(row) == FIELDS, sorted(set(row) ^ FIELDS)
    assert row['request'] == SID, row['request']
    assert row['batch'] == 1, row['batch']
    assert row['item_ns'] == list(range(1, sr.BATCH + 1)), row['item_ns']
    assert row['turns'] == [10 + 3 * i for i in range(sr.BATCH)], row['turns']
    assert row['n_items'] == sr.BATCH, row['n_items']
    assert row['parse_failures'] == sr.BATCH, 'every one-key-per-line object is one failure: %r' % row
    assert row['answered_ns'] == [], row['answered_ns']
    assert row['prompt_chars'] == len(asker.prompts[0]) > 200, (row['prompt_chars'], len(asker.prompts[0]))
    assert row['answer_chars'] == len(reply) > 300, (row['answer_chars'], len(reply))


def test_one_ledger_row_per_batch_with_that_batch_s_own_numbers():
    first, second = swift_reply(sr.BATCH), tidy_reply(5, start=sr.BATCH + 1)
    _rows, ledger, _raws, asker = run(items(sr.BATCH) + items(5, start_n=sr.BATCH + 1, turn0=500),
                                      [first, second])
    assert len(asker.prompts) == 2 and len(ledger) == 2, (len(asker.prompts), len(ledger))
    assert [r['batch'] for r in ledger] == [1, 2], [r['batch'] for r in ledger]
    assert ledger[0]['n_items'] == sr.BATCH and ledger[1]['n_items'] == 5, (
        ledger[0]['n_items'], ledger[1]['n_items'])
    assert ledger[1]['item_ns'] == list(range(sr.BATCH + 1, sr.BATCH + 6)), ledger[1]['item_ns']
    assert ledger[1]['turns'] == [500 + 3 * i for i in range(5)], ledger[1]['turns']
    assert ledger[1]['parse_failures'] == 0, ledger[1]['parse_failures']
    assert ledger[1]['answered_ns'] == list(range(sr.BATCH + 1, sr.BATCH + 6)), ledger[1]['answered_ns']
    assert ledger[0]['answer_chars'] == len(first), ledger[0]['answer_chars']
    assert ledger[1]['answer_chars'] == len(second), ledger[1]['answer_chars']
    assert asker.purposes == ['session review: triage 1/2', 'session review: triage 2/2'], asker.purposes


def test_an_unparseable_reply_is_saved_whole_and_unsliced():
    reply = long_swift_reply(sr.BATCH)
    _rows, _ledger, raws, _asker = run(items(sr.BATCH), [reply])
    name = '%s-1.txt' % SID
    assert list(raws) == [name], sorted(raws)
    saved = raws[name]
    assert saved == reply, 'the saved reply must be the exact text the asker returned'
    assert len(saved) > 120, 'the reply is longer than the 120-character NOTE slice it replaces'
    assert LONG in saved, 'the whole reply is saved, not a leading slice of it'
    assert saved.count('\n') == reply.count('\n'), 'no line of the reply is dropped'


def test_a_dead_batch_mints_zero_findings():
    rows, ledger, _raws, _asker = run(items(2 * sr.BATCH), ['', ''])
    assert len(rows) == 2 * sr.BATCH, len(rows)
    assert {r['verdict'] for r in rows} == {'unanswered'}, sorted({r['verdict'] for r in rows})
    assert len(ledger) == 2, len(ledger)
    assert [r['answered_ns'] for r in ledger] == [[], []], [r['answered_ns'] for r in ledger]
    body = source_of('extract_scripted')
    assert body.count('unanswered') >= 1, (
        'extract_scripted must name the verdict it refuses to file, or a dead batch mints one '
        'finding per item: %s' % body[:400])
    assert 'verdict_source' in body, 'the finding record must carry the verdict provenance'


def test_triage_leaves_the_live_state_paths_alone():
    before = (sr.BASE, sr.FINDINGS, sr.LEDGER, sr.PENDING, sr.DONE, sr.RCA_DIR)
    _rows, _ledger, _raws, asker = run(items(2), [''])
    after = (sr.BASE, sr.FINDINGS, sr.LEDGER, sr.PENDING, sr.DONE, sr.RCA_DIR)
    assert after == before, 'the fixture must restore the module paths it redirected'
    assert len(asker.prompts) == 1, len(asker.prompts)


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    bad = []
    for fn in fns:
        try:
            fn()
        except Exception as e:
            bad.append(fn.__name__)
            print('FAIL %s: %s: %s' % (fn.__name__, type(e).__name__, e))
        else:
            print('ok   %s' % fn.__name__)
    print('%d/%d passed' % (len(fns) - len(bad), len(fns)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
