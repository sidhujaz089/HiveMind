#!/usr/bin/env python3
'''t072: meaning-search over Jas's own documents in library/.

RED ON MAIN FOR ONE REASON: scripts/memory.py has no document-corpus search. It ranks its own
memory records and nothing else, so asking which file in library/ explains password hashing has
no answer. Every test below starts with api(), which names that reason, so a red run is red for
the missing change and not for a bug in this file.

The embedder is never called: urllib.request.urlopen is replaced by a fake that serves a scripted
vector per known paragraph, REFUSES any text it was not written to know, answers only the
/api/embed endpoint, and fails any call that arrives without timeout=. The corpus and the index
live in a tempfile directory; the repository is listed around one search, so a run that writes
into it is caught.

Every assertion is a thing an engineer checks by hand: what comes back, in what order, with what
text, where it points in the source, what is written where, and what happens with no embedder.
'''
import contextlib
import io
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import memory  # noqa: E402

MISSING = 'scripts/memory.py has no doc_search: the change this oracle asks for is not in the file'
Q_PASSWORD = 'Which file explains how password hashing works?'
Q_DEPLOY = 'How do I deploy the release over ssh?'

# Seven paragraphs in four files. alpha.md carries the password answer twice (line 1 and the long
# paragraph on line 3) so paragraph-splitting and line-numbering are forced at once; delta.md is
# the decoy the keyword channel cannot see at all (no query token in it) and the vector channel
# ranks first; beta.md is the one the keyword channel wins. Every paragraph is over 90 characters,
# so a floor anywhere up to 90 keeps all seven.
LONG = ('Password hashing uses bcrypt_hash with a cost factor of 12, and the cost factor is '
        'recorded in the header of every hash so a future migration can tell the two schemes '
        'apart, which is why the number is stored and not assumed. '
        + 'filler ' * 200 +
        'The tail marker of this long paragraph is ZZZENDZZZ so that a slice in the indexer '
        'shows up as a missing tail at the end.')
ALPHA = ('Password hashing uses bcrypt_hash with a cost factor of 12, and this same line is the '
         'release note stub.\n\n' + LONG +
         '\n\nSourdough starter needs feeding twice a day at room temperature, and once a day in '
         'the cold fridge.\n')
BETA = ('The deploy step copies the release tarball onto the host and runs the deploy step over '
        'ssh with the deploy key.\n\nNothing in this note talks about passwords, hashing, cost '
        'factors, migrations, baking bread, birds, tides, fridges, gardens, seeds, tools, sheds, '
        'fences, paints, wheels, anchors, ropes, knots, compasses, lanterns, maps, charts, '
        'ledgers, hinges, latches, sails, axles, keels, oars, paddles, rudders, masts.\n')
GAMMA = ('A birdwatching log for the estuary: herons at dawn, the tide table pinned to the shed '
         'door, and a flask of tea on the wall.\n')
DELTA = ('The kitchen garden in March: peas along the fence, rhubarb by the wall, a crate of seed '
         'trays by the door, and the last of the autumn leaves raked into the barrel.\n')
CORPUS = {'alpha.md': ALPHA, 'beta.md': BETA, 'gamma.md': GAMMA, 'delta.md': DELTA}

# One scripted vector per paragraph, keyed on a substring unique to that paragraph, so the fake
# answers only the texts the indexer really produces. axis0 = hashing, axis1 = deploying.
DOC_VECS = [
    ('release note stub', [0.9, 0.1, 0.0, 0.0]),
    ('ZZZENDZZZ', [0.85, 0.1, 0.05, 0.0]),
    ('seed trays', [0.9, 0.1, 0.0, 0.0]),
    ('deploy step', [0.5, 0.5, 0.0, 0.0]),
    ('baking bread', [0.0, 0.2, 0.9, 0.0]),
    ('herons at dawn', [0.0, 0.2, 0.0, 0.9]),
    ('sourdough starter', [0.0, 0.0, 1.0, 0.0]),
]
QUERY_VECS = {Q_PASSWORD: [1.0, 0.0, 0.0, 0.0], Q_DEPLOY: [0.0, 1.0, 0.0, 0.0]}

PASSED = FAILED = 0
CALLED = []


def ok(label, cond, detail=''):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f'  [ok] {label}')
    else:
        FAILED += 1
        print(f'  [BAD] {label}' + (f' -- {detail}' if detail else ''))


class Resp:
    '''The minimum a caller of urlopen reads: a JSON body, used as a context manager.'''

    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def key_for(text):
    for marker, key in DOC_VECS:
        if marker in text:
            return key
    return None


def make_embedder(sink):
    def handler(req, timeout=None, *args, **kw):
        assert req.full_url.endswith('/api/embed'), f'stub answers the embedding endpoint only: {req.full_url}'
        assert timeout is not None, 'an embedder call with no timeout can wait forever'
        body = json.loads(req.data.decode())
        model = body.get('model')
        assert model == memory.EMBED_MODEL, f'embedded with {model!r}, not the module constant'
        CALLED.append({'model': model, 'timeout': timeout, 'n': len(body.get('input', []))})
        vecs = []
        for t in body.get('input', []):
            if t.startswith(memory.QUERY_INSTRUCTION):
                asked = t[len(memory.QUERY_INSTRUCTION):]
                assert asked in QUERY_VECS, f'a query was embedded that this oracle never issued: {asked!r}'
                sink['asked'].append(asked)
                vecs.append(QUERY_VECS[asked])
                continue
            key = key_for(t)
            assert key is not None, f'a document was embedded that this oracle does not know: {t[:60]!r}'
            sink['docs'][key] = sink['docs'].get(key, 0) + 1
            vecs.append(DOC_VECS[key][1])
        return Resp({'embeddings': vecs})
    return handler


def corpus(td):
    root = td / 'library'
    (root / 'notes').mkdir(parents=True)
    for name, text in CORPUS.items():
        (root / name).write_text(text, encoding='utf-8')
    (root / 'notes' / 'scan.pdf').write_bytes(b'%PDF-1.4 this is not a text document')
    return root


def names(hits):
    return [h['source'] for h in hits]


def api():
    assert callable(getattr(memory, 'doc_search', None)), MISSING
    return memory.doc_search


def test_module_exists():
    search = api()
    ok('doc_search(query, root=, index_path=, limit=) exists', callable(search), MISSING)
    ok('documents() is the corpus walk', callable(getattr(memory, 'documents', None)), 'no documents()')
    root_name = Path(str(memory.DOC_ROOT)).name
    ok('the personal-document corpus defaults to library/', root_name == 'library', f'{memory.DOC_ROOT!r}')


def test_fusion_promotes_the_meaning_match():
    search = api()
    sink = {'docs': {}, 'asked': []}
    real = urllib.request.urlopen
    urllib.request.urlopen = make_embedder(sink)
    try:
        with tempfile.TemporaryDirectory(prefix='t072-docs-') as td:
            root = corpus(Path(td))
            hits = search(Q_DEPLOY, root=root, index_path=Path(td) / 'idx.json', limit=7)
    finally:
        urllib.request.urlopen = real
    order = names(hits)
    ok('every paragraph of the corpus is rankable', len(hits) == 7, f'{order}')
    ok('the two channels fuse: keyword wins the exact-identifier query, meaning fills in behind it',
       order[:3] == ['beta.md', 'alpha.md', 'beta.md'], f'{order}')
    keys = set(hits[0]) if hits else set()
    ok('a hit is a pointer into the document, not a blob', keys == {'id', 'source', 'line', 'text', 'score'},
       f'keys {sorted(keys)}')
    kind = type(hits[0]['score'])
    ok('score is a float', isinstance(hits[0]['score'], float), f'{kind}')


def test_a_hit_points_at_the_paragraph():
    search = api()
    sink = {'docs': {}, 'asked': []}
    real = urllib.request.urlopen
    urllib.request.urlopen = make_embedder(sink)
    try:
        with tempfile.TemporaryDirectory(prefix='t072-docs-') as td:
            root = corpus(Path(td))
            hits = search(Q_PASSWORD, root=root, index_path=Path(td) / 'idx.json', limit=7)
    finally:
        urllib.request.urlopen = real
    order = names(hits)
    ok('meaning finds the paragraph that shares no word with the query', 'delta.md' in order, f'{order}')
    ok('and keyword keeps the exact answer first', order[:1] == ['alpha.md'], f'{order}')
    at = order.index('delta.md') if 'delta.md' in order else -1
    ok('the decoy is fused into third place, not appended last', at == 2, f'{order}')
    ids = [h['id'] for h in hits]
    ok('ids are unique', len(set(ids)) == len(ids), f'{ids}')
    longs = [h for h in hits if 'ZZZENDZZZ' in h['text']]
    ok('the long paragraph is indexed as one whole record', len(longs) == 1, f'{len(longs)} carry the tail marker')
    if len(longs) == 1:
        text = longs[0]['text']
        ok('it keeps its head and its tail, so it was not sliced',
           'cost factor of 12' in text and 'ZZZENDZZZ' in text, text[-40:])
        ok('a long paragraph is reported whole, not cut', len(text) > 1200, f'{len(text)} characters')
        ok('it starts on line 3 of its file', longs[0]['line'] == 3, f'line {longs[0]["line"]!r}')
    first = [h for h in hits if h['source'] == 'alpha.md' and 'ZZZENDZZZ' not in h['text']]
    lines = [h['line'] for h in first]
    ok('the opening paragraph of a file reports line 1', lines == [1], f'{lines}')
    body = first[0]['text'] if first else ''
    ok('a hit carries the passage that matched, not just a path',
       'bcrypt_hash' in body and 'Password hashing' in body, body[:60])


def test_documents_are_embedded_once_and_the_query_is_prefixed():
    search = api()
    sink = {'docs': {}, 'asked': []}
    real = urllib.request.urlopen
    urllib.request.urlopen = make_embedder(sink)
    try:
        with tempfile.TemporaryDirectory(prefix='t072-docs-') as td:
            root = corpus(Path(td))
            search(Q_PASSWORD, root=root, index_path=Path(td) / 'idx.json', limit=7)
    finally:
        urllib.request.urlopen = real
    counts = sink['docs']
    asked = sink['asked']
    ok('the embedder is reached at all', bool(CALLED), 'no embedder call in the whole run')
    timeouts = [c['timeout'] for c in CALLED]
    ok('every embedder call carries a timeout', all(t is not None for t in timeouts), f'{timeouts}')
    models = {c['model'] for c in CALLED}
    ok('the embedding model is the module constant', models == {memory.EMBED_MODEL}, f'{models}')
    for marker, key in DOC_VECS:
        ok(f'each document is embedded exactly once ({key})', counts.get(key, 0) == 1, f'{counts}')
    ok('the whole corpus is embedded in one batched call', max(c['n'] for c in CALLED) == 7,
       f'largest batch {max(c["n"] for c in CALLED)}')
    ok('the query is embedded, behind the instruction prefix (documents never get it)',
       asked == [Q_PASSWORD], f'{asked}')


def test_a_second_search_reuses_the_cache():
    search = api()
    sink1 = {'docs': {}, 'asked': []}
    sink2 = {'docs': {}, 'asked': []}
    real = urllib.request.urlopen
    with tempfile.TemporaryDirectory(prefix='t072-docs-') as td:
        root = corpus(Path(td))
        index = Path(td) / 'idx.json'
        urllib.request.urlopen = make_embedder(sink1)
        try:
            first = search(Q_PASSWORD, root=root, index_path=index, limit=7)
        finally:
            urllib.request.urlopen = real
        urllib.request.urlopen = make_embedder(sink2)
        try:
            second = search(Q_PASSWORD, root=root, index_path=index, limit=7)
        finally:
            urllib.request.urlopen = real
    counts = sink1['docs']
    counts2 = sink2['docs']
    asked2 = sink2['asked']
    ok('an unchanged paragraph is never embedded twice', counts == {k: 1 for _, k in DOC_VECS}, f'{counts}')
    ok('re-searching an unchanged corpus embeds no document at all', counts2 == {}, f'{counts2}')
    ok('the query is embedded every search, never cached', asked2 == [Q_PASSWORD], f'{asked2}')
    ok('a cached search returns the same ranking', names(second) == names(first),
       f'{names(second)} vs {names(first)}')


def test_the_index_is_written_where_it_is_told_to():
    search = api()
    ws = ROOT / 'workspace'
    before = {p.name for p in ROOT.iterdir()} | {f'workspace/{p.name}' for p in ws.iterdir()}
    sink = {'docs': {}, 'asked': []}
    real = urllib.request.urlopen
    urllib.request.urlopen = make_embedder(sink)
    try:
        with tempfile.TemporaryDirectory(prefix='t072-docs-') as td:
            root = corpus(Path(td))
            index = Path(td) / 'nested' / 'idx.json'
            search(Q_PASSWORD, root=root, index_path=index, limit=7)
            ok('the index is created at the path the caller named, creating its parent',
               index.is_file(), f'{index}')
            stored = json.loads(index.read_text(encoding='utf-8')) if index.is_file() else {}
    finally:
        urllib.request.urlopen = real
    after = {p.name for p in ROOT.iterdir()} | {f'workspace/{p.name}' for p in ws.iterdir()}
    ok('searching documents never touches the models own index', after == before, f'new: {sorted(after - before)}')
    ok('one cache entry per document', len(stored) == 7, f'{len(stored)} entries: {sorted(stored)[:3]}')
    shas = [e.get('sha') for e in stored.values()]
    ok('a cached document records the text it was embedded from', all(shas), f'{shas[:3]}')
    ok('a cached vector is not empty', all(e.get('v') for e in stored.values()), 'an entry has no vector')


def test_only_the_documents_in_the_corpus_are_indexed():
    search = api()
    sink = {'docs': {}, 'asked': []}
    real = urllib.request.urlopen
    urllib.request.urlopen = make_embedder(sink)
    try:
        with tempfile.TemporaryDirectory(prefix='t072-docs-') as td:
            root = corpus(Path(td))
            hits = search(Q_DEPLOY, root=root, index_path=Path(td) / 'idx.json', limit=20)
    finally:
        urllib.request.urlopen = real
    got = set(names(hits))
    ok('a pdf in the tree is not indexed', 'notes/scan.pdf' not in got, f'{sorted(got)}')
    extra = sorted(got - set(CORPUS))
    ok('nothing outside the corpus is indexed', not extra, f'{extra}')


def test_no_embedding_model_still_answers_by_keyword():
    search = api()
    sink = {'docs': {}, 'asked': []}

    def down(req, timeout=None, *args, **kw):
        raise OSError('no embedding model on this box')

    real = urllib.request.urlopen
    urllib.request.urlopen = down
    buf = io.StringIO()
    try:
        with tempfile.TemporaryDirectory(prefix='t072-docs-') as td:
            root = corpus(Path(td))
            with contextlib.redirect_stderr(buf):
                hits = search(Q_PASSWORD, root=root, index_path=Path(td) / 'idx.json', limit=6)
    finally:
        urllib.request.urlopen = real
    order = names(hits)
    said = buf.getvalue()
    ok('with no embedder, keyword still answers', order == ['alpha.md', 'alpha.md'], f'{order}')
    ok('a paragraph only a vector could find is not invented', 'delta.md' not in order, f'{order}')
    ok('the missing embedder is announced, not silent', 'embedding unavailable' in said, f'stderr: {said[-120:]!r}')


TESTS = [test_module_exists, test_fusion_promotes_the_meaning_match, test_a_hit_points_at_the_paragraph,
          test_documents_are_embedded_once_and_the_query_is_prefixed, test_a_second_search_reuses_the_cache,
          test_the_index_is_written_where_it_is_told_to, test_only_the_documents_in_the_corpus_are_indexed,
          test_no_embedding_model_still_answers_by_keyword]

if __name__ == '__main__':
    for t in TESTS:
        print(f'\n{t.__name__}')
        try:
            t()
        except Exception as e:            # a crash is a failed check, reported with its reason
            FAILED += 1
            print(f'  [BAD] {t.__name__} raised {type(e).__name__}: {e}')
    print(f'\n{PASSED} passed, {FAILED} failed')
    sys.exit(1 if FAILED else 0)
