'''Oracle for t735: a session-review finding that asserts a live state must carry its check.

RED ON MAIN FOR THE MISSING CHANGE, not for a bug in this file. Run on main today:
  python3 -c "import sys; sys.path.insert(0, 'scripts'); import session_review as s; print(s.LIVE_STATE_KEYS)"
  -> AttributeError: module 'session_review' has no attribute 'LIVE_STATE_KEYS'
and the same for LIVE_STATE_KIND, LiveStateContractError, live_state_violations and
refuse_live_state; sorted(s.DISPOSITIONS) has no 'transcript-state', and s._KIND_DISPOSITION
is {'deferral': 'deferred', 'for-jas': 'for-jas'}. And scripts/tests/test_session_review_disposition.py
is GREEN on main today minting a finding that carries no live_state at all -- proof that the
emit path today writes a finding that checked nothing.

WHY THIS IS THE CHANGE: scripts/session_review.py resolves a finding's disposition as
_KIND_DISPOSITION.get(str(r.get('kind')), 'unresolved'), so every kind that map does not list --
off-flow, and any kind a model invents -- arrives at the actionable word 'unresolved' by code
fallthrough, not by a judgement, and the record written carries no field stating what was checked
against the tree as it stands. That is why a true report of a 2026-08-28 transcript was emitted on
2026-10-01 as an open defect in code fixed on 2026-08-28: RCAs 15628546a2d7, 1a7761b3791e,
1e0e465e0ffc and 6de92924186c each spent a full Senior pass rediscovering that the subject had
been fixed, deleted (41266eb3) or superseded (83a955a3, ed79c47b). 248 files sit in
workspace/session-reviews/findings/ in that state today. The record has no category that can hold
'this is a transcript-state claim, not a live-state claim', so a transcript-state claim is
consumed as a live-state claim.

No pytest (not installed), no model call, no subprocess, no network, no process left running.
FINDINGS, LEDGER and _archive_cover are redirected into a tempfile.TemporaryDirectory in every
case, no quote below carries a tNNN token, and no task store is read: running this suite leaves
the worktree clean.

Run as: python3 scripts/tests/test_session_review_live_state.py
'''
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
sys.path.insert(0, str(ROOT / 'scripts'))
import session_review as sr                          # noqa: E402

ARTIFACT = 'scripts/coder.py'
ARTIFACT2 = 'scripts/check_workspace.py'
CHECK = 'git log --oneline -1 -- scripts/coder.py'
OBSERVED = '41266eb3 retire the gateway shim'
LIVE = {'check': CHECK, 'observed': OBSERVED}
QUOTE = ('the gateway in scripts/coder.py reads the model at up time, so swapping the '
         'served model breaks it silently')
TWIN_QUOTE = ('the gateway in scripts/coder.py and the checker in scripts/check_workspace.py '
              'both read the loaded model at up time')
CHECKED_QUOTE = 'the dispatcher in scripts/coder.py gives every preflight to one lane'
NO_ARTIFACT = 'about forty scripts call the inference API directly, so there is nothing to open'
DEFER_QUOTE = 'I will audit scripts/coder.py for that next session'
TRANSCRIPT_QUOTE = 'on 2026-08-28 the round-36 brief runs collided on coder/brief'
REQ = {'id': 't735-oracle-session', 'harness': 'claude'}
REQ_FILE = Path('claude-t735-part-0.json')


def row(quote=QUOTE, **kw):
    r = {'kind': 'off-flow', 'quote': quote, 'where': 'assistant, mid-session',
         'why_missed': 'no task was filed for it'}
    r.update(kw)
    return r


def mint(*records):
    """Run _keep over these records with every output path inside a fresh temp dir."""
    tmp = tempfile.TemporaryDirectory(prefix='t735-oracle-')
    d = Path(tmp.name)
    sr.FINDINGS = d / 'findings'
    sr.LEDGER = d / 'ledger.jsonl'
    sr._archive_cover = lambda quote: ''
    try:
        req_file = d / REQ_FILE.name
        req_file.write_text(json.dumps(REQ) + '\n', encoding='utf-8')
        ret = sr._keep(req_file, REQ, [dict(r) for r in records])
        written = [json.loads(f.read_text(encoding='utf-8'))
                   for f in sorted(sr.FINDINGS.glob('*.json'))]
        ev = []
        if sr.LEDGER.is_file():
            for line in sr.LEDGER.read_text(encoding='utf-8').splitlines():
                try:
                    ev.append(json.loads(line))
                except ValueError:
                    ev.append({'event': 'UNREADABLE-LEDGER-LINE'})
    finally:
        tmp.cleanup()
    return {'ret': ret, 'records': written, 'events': ev}


def events(out, name):
    return [e for e in out['events'] if e.get('event') == name]


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_01_the_module_names_the_live_state_contract():
    check(tuple(sr.LIVE_STATE_KEYS) == ('check', 'observed'),
          'LIVE_STATE_KEYS must be the two facts a live check carries, got %r' % (sr.LIVE_STATE_KEYS,))
    check(sr.LIVE_STATE_KIND == 'transcript-state',
          'LIVE_STATE_KIND must name the kind that asserts the transcript, got %r' % (sr.LIVE_STATE_KIND,))


def test_02_the_refusal_has_a_type_of_its_own():
    check(issubclass(sr.LiveStateContractError, Exception),
          'LiveStateContractError must be an exception, got %r' % (sr.LiveStateContractError,))
    check('t735' in (sr.LiveStateContractError.__doc__ or '').lower(),
          'its docstring must say why it exists, got %r' % (sr.LiveStateContractError.__doc__,))


def test_03_transcript_state_is_a_disposition_and_today_s_values_survive():
    check('transcript-state' in sr.DISPOSITIONS,
          'DISPOSITIONS must be able to hold a transcript-only claim, got %r' % (sr.DISPOSITIONS,))
    check(sr._KIND_DISPOSITION.get('transcript-state') == 'transcript-state',
          'kind transcript-state must map to disposition transcript-state, got %r' % (sr._KIND_DISPOSITION,))
    for kept in ('unresolved', 'resolved-in-session', 'deferred', 'for-jas', 'refuted'):
        check(kept in sr.DISPOSITIONS,
              'the value %r that is legal today must stay legal, got %r' % (kept, sr.DISPOSITIONS))
    check(sr._KIND_DISPOSITION.get('deferral') == 'deferred'
          and sr._KIND_DISPOSITION.get('for-jas') == 'for-jas',
          'both mappings that exist today must survive, got %r' % (sr._KIND_DISPOSITION,))


def test_04_an_unchecked_claim_about_a_repo_file_is_one_violation_naming_it():
    v = sr.live_state_violations(row())
    check(isinstance(v, list) and len(v) == 1, 'expected one violation, got %r' % (v,))
    m = v[0]
    check(isinstance(m, str), 'a violation must be a string, got %r' % (m,))
    check(ARTIFACT in m, 'the message must name the artefact the finding claims to know about, got %r' % (m,))
    check('live_state' in m, 'the message must name the missing field, got %r' % (m,))
    check('missing' in m.lower(), 'the message must say the check is missing, got %r' % (m,))


def test_05_every_repo_file_the_quote_names_gets_its_own_violation():
    v = sr.live_state_violations(row(quote=TWIN_QUOTE))
    check(len(v) == 2, 'a quote naming two artefacts must yield two violations, got %r' % (v,))
    joined = ' '.join(v)
    check(ARTIFACT in joined and ARTIFACT2 in joined,
          'both artefacts named in the quote must appear, got %r' % (v,))
    check(v[0] != v[1], 'each violation must name its own artefact, got %r' % (v,))


def test_06_the_live_state_must_carry_both_keys_as_non_empty_text():
    empty = sr.live_state_violations(row(live_state={}))
    check(len(empty) == 2, 'an empty live_state must be two violations, got %r' % (empty,))
    joined = ' '.join(empty)
    for key in ('check', 'observed'):
        check(key in joined, 'both keys must be named, got %r' % (empty,))
    bad = sr.live_state_violations(row(live_state={'check': 7, 'observed': '   '}))
    check(len(bad) == 2, 'a non-string and a blank value are two violations, got %r' % (bad,))
    check('not a string' in bad[0] and 'is empty' in bad[1],
          'the messages must distinguish a non-string from a blank, in key order, got %r' % (bad,))


def test_07_a_finding_that_carried_its_check_is_clean():
    check(sr.live_state_violations(row(live_state=dict(LIVE))) == [],
          'a finding naming a file WITH its check recorded must be accepted, got %r'
          % (sr.live_state_violations(row(live_state=dict(LIVE))),))


def test_08_a_finding_that_names_no_repo_file_is_clean():
    check(sr.live_state_violations(row(quote=NO_ARTIFACT)) == [],
          'a quote with no repo path must not be checked, got %r'
          % (sr.live_state_violations(row(quote=NO_ARTIFACT)),))


def test_09_a_deferral_is_exempt_because_t607_owns_that_shape():
    check(sr.live_state_violations(row(quote=DEFER_QUOTE, kind='deferral',
                                       disposition='deferred')) == [],
          'a deferral must be exempt: t607 already requires verify_command and decay_test, got %r'
          % (sr.live_state_violations(row(quote=DEFER_QUOTE, kind='deferral',
                                          disposition='deferred')),))


def test_10_a_transcript_state_finding_is_exempt():
    check(sr.live_state_violations(row(quote=TRANSCRIPT_QUOTE, kind='transcript-state',
                                       disposition='transcript-state')) == [],
          'a transcript-state finding asserts the transcript, not the tree, got %r'
          % (sr.live_state_violations(row(quote=TRANSCRIPT_QUOTE, kind='transcript-state',
                                          disposition='transcript-state')),))


def test_11_refuse_live_state_raises_naming_the_gap():
    try:
        sr.refuse_live_state(row())
    except sr.LiveStateContractError as e:
        m = str(e)
        check(ARTIFACT in m and 'live_state' in m and 'missing' in m,
              'the refusal must name the artefact, the field and the gap, got %r' % (m,))
    else:
        raise AssertionError('refuse_live_state accepted an unchecked claim about %s -- the '
                             'contract is a refusal at the emit path, not a warning' % ARTIFACT)
    check(sr.refuse_live_state(row(live_state=dict(LIVE))) is None,
          'a finding that carries its check must pass quietly')


def test_12_an_unchecked_claim_about_a_repo_file_is_refused_at_the_emit_path():
    out = mint(row())
    check(out['ret'] == (0, 0),
          'a refused finding must be counted as neither new nor covered, got %r' % (out['ret'],))
    check(out['records'] == [], 'a refused finding must not be written to disk: %r'
          % ([r.get('quote') for r in out['records']],))
    rows = events(out, 'finding-refused')
    check(len(rows) == 1, 'exactly one refusal row, got %r' % (rows,))
    r = rows[0]
    check(r.get('quote') == QUOTE,
          'the refusal row must carry the part file it came from, got %r' % (r.get('quote'),))
    check(r.get('request') == REQ_FILE.name,
          'the refusal row must name the part file, got %r' % (r.get('request'),))
    check(r.get('kind') == 'off-flow' and r.get('disposition') == 'unresolved',
          'the refusal row keeps kind and the disposition that was refused, got %r' % (r,))
    check(not events(out, 'finding'),
          'a refused finding must not also be logged as minted: %r' % (events(out, 'finding'),))


def test_13_a_checked_finding_is_written_whole_with_the_check_it_made():
    out = mint(row(live_state=dict(LIVE)))
    check(out['ret'] == (1, 0),
          'a finding that carries its live-state check must be minted, got %r' % (out['ret'],))
    check(len(out['records']) == 1, 'expected one finding file, got %r' % (out['records'],))
    rec = out['records'][0]
    check(rec.get('quote') == QUOTE, 'the finding file must carry the quote whole: %r' % (rec.get('quote'),))
    check(rec.get('kind') == 'off-flow', 'the finding keeps the kind it was written with: %r' % (rec.get('kind'),))
    check(rec.get('disposition') == 'unresolved',
          'an off-flow finding keeps disposition unresolved -- that derivation is not this change: %r'
          % (rec.get('disposition'),))
    ls = rec.get('live_state')
    check(isinstance(ls, dict), 'the minted record must carry a live_state mapping, got %r' % (ls,))
    check(ls.get('check') == CHECK, 'the minted record lost the command that was run: %r' % (ls,))
    check(ls.get('observed') == OBSERVED, 'the minted record lost what the command returned: %r' % (ls,))
    check(rec.get('key') and rec.get('request') == REQ_FILE.name,
          'the minted record must keep its key and part file: %r' % (rec,))
    check(rec.get('session') == REQ['id'] and rec.get('harness') == 'claude',
          'the minted record must keep session and harness: %r' % (rec,))
    ms = events(out, 'finding')
    check(len(ms) == 1 and ms[0].get('quote') == QUOTE and ms[0].get('key') == rec['key'],
          "the existing 'finding' ledger row must keep request, key, kind and quote: %r" % (ms,))
    check(ms[0].get('kind') == 'off-flow', 'the ledger row must keep the kind: %r' % (ms,))
    check(not events(out, 'finding-refused'),
          'a checked finding must not also be refused: %r' % (events(out, 'finding-refused'),))


def test_14_a_deferral_naming_an_artefact_is_still_minted():
    out = mint(row(kind='deferral', quote=DEFER_QUOTE))
    check(out['ret'] == (1, 0),
          'a deferral naming a repo artefact must STILL be minted: %r -- t607 owns deferrals '
          '(DEFERRAL_FIELDS, deferral_violations) and scripts/tests/test_deferral_artefacts.py '
          'mints exactly this shape over scripts/triage.py' % (out['ret'],))
    check(len(out['records']) == 1, 'the deferral was not written: %r' % (out['records'],))
    check(out['records'][0].get('disposition') == 'deferred',
          "the deferral's disposition must stay deferred: %r" % (out['records'][0],))
    check(not events(out, 'finding-refused'),
          'the new rule must not fire on a deferral: %r' % (events(out, 'finding-refused'),))


def test_15_a_transcript_state_finding_is_minted_as_transcript_state():
    out = mint(row(kind='transcript-state', quote=TRANSCRIPT_QUOTE))
    check(out['ret'] == (1, 0),
          'a transcript-state finding must be minted with no live_state: %r -- it asserts the '
          'transcript, not the tree' % (out['ret'],))
    rec = out['records'][0]
    check(rec.get('kind') == 'transcript-state', 'kind lost: %r' % (rec,))
    check(rec.get('disposition') == 'transcript-state',
          'a transcript-only finding stamped unresolved is the exact misdiagnosis this task exists '
          'to stop: %r' % (rec.get('disposition'),))
    check('live_state' not in rec, 'a transcript-state finding must not be given a live_state: %r' % (rec,))
    check(not events(out, 'finding-refused'),
          'a transcript-state finding was refused: %r' % (events(out, 'finding-refused'),))


def test_16_a_finding_that_names_no_repo_file_is_still_minted():
    out = mint(row(quote=NO_ARTIFACT))
    check(out['ret'] == (1, 0),
          'a finding that names no repo artefact must still be minted: %r' % (out['ret'],))
    check(len(out['records']) == 1 and out['records'][0].get('quote') == NO_ARTIFACT,
          'the wrong record was written: %r' % (out['records'],))
    check(not events(out, 'finding-refused'),
          'the rule must not fire on a quote with no repo path: %r' % (events(out, 'finding-refused'),))


def test_17_one_deficient_finding_does_not_destroy_its_siblings():
    out = mint(row(), row(quote=NO_ARTIFACT), row(quote=CHECKED_QUOTE, live_state=dict(LIVE)))
    check(out['ret'][0] == 2,
          'a part with one deficient finding and two sound ones minted %r; expected 2 -- _keep '
          'loops over the whole part, so a refusal must skip one record, not abandon the part'
          % (out['ret'],))
    check(len(out['records']) == 2, 'siblings were lost: %r'
          % ([r.get('quote') for r in out['records']],))
    quotes = sorted(r['quote'] for r in out['records'])
    check(QUOTE not in quotes, 'the deficient finding was written anyway: %r' % (quotes,))
    check(NO_ARTIFACT in quotes and CHECKED_QUOTE in quotes, 'a sound sibling was lost: %r' % (quotes,))
    rows = events(out, 'finding-refused')
    check(len(rows) == 1 and rows[0]['quote'] == QUOTE,
          'exactly one refusal row, naming the deficient quote: %r' % (rows,))
    check(len(events(out, 'finding')) == 2,
          "the two minted findings must each keep a 'finding' ledger row: %r" % (events(out, 'finding'),))


def test_18_keep_still_returns_the_two_tuple():
    out = mint(row(quote=NO_ARTIFACT))
    check(isinstance(out['ret'], tuple) and len(out['ret']) == 2,
          '_keep must return (new, covered) -- scripts/tests/test_finding_dedupe.py and '
          'test_session_review_disposition.py unpack it, got %r' % (out['ret'],))
    check(out['ret'] == (1, 0),
          'a finding that names no repo artefact must still be minted: %r' % (out['ret'],))


def test_19_the_refusal_row_carries_the_reasons_whole():
    out = mint(row())
    rows = events(out, 'finding-refused')
    check(len(rows) == 1, 'expected one refusal row, got %r' % (rows,))
    reasons = rows[0].get('reasons')
    check(isinstance(reasons, list) and reasons and all(isinstance(x, str) and x.strip() for x in reasons),
          'the refusal must record why, as non-empty strings, got %r' % (reasons,))
    expected = sr.live_state_violations({**row(), 'disposition': 'unresolved'})
    check(reasons == expected,
          'the ledger reasons must be the contract violations, whole and in order: %r vs %r'
          % (reasons, expected))
    check(ARTIFACT in ' '.join(reasons),
          'the artefact path must survive into the record, got %r' % (reasons,))


def test_20_a_non_mapping_record_is_a_type_error():
    try:
        sr.live_state_violations('not a record')
    except TypeError as e:
        check('mapping' in str(e),
              "the TypeError must name 'mapping' the way deferral_violations does, got %r" % (str(e),))
    else:
        raise AssertionError('live_state_violations accepted a non-mapping record')


if __name__ == '__main__':
    names = sorted(n for n in dir() if n.startswith('test_'))
    failed = 0
    for n in names:
        try:
            globals()[n]()
        except Exception as e:
            failed += 1
            print('FAIL %s: %s: %s' % (n, type(e).__name__, e))
        else:
            print('PASS %s' % n)
    print('t735 live-state contract: %d of %d case(s) failed' % (failed, len(names)))
    sys.exit(1 if failed else 0)
