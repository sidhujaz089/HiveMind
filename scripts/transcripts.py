#!/usr/bin/env python3
"""Make past Claude Code sessions searchable instead of merely stored.

    python3 scripts/transcripts.py index            build the searchable index
    python3 scripts/transcripts.py search "routing.csv"
    python3 scripts/transcripts.py sessions         what is on this machine
    python3 scripts/transcripts.py import DIR       transcripts copied from another machine

WHY
---
Jas, 2026-08-26: *"I don't care about data retention as I care about your memory
recollection."*

`~/.claude/projects/<dir>/<uuid>.jsonl` holds every message, tool call and result of every
session — 31 MB across five here. It is the only record of how a decision was actually
reached, and until now nothing could read it. Chat can `search` the whole corpus through the
bridge; I had seven hand-written memory files and 31 MB I could not query.

The cost showed up the same day. Asked whether two corpus files had been deleted deliberately,
the answer was in a transcript I could not reach. I found it through `git log` instead — which
worked only because that particular fact had also been committed — and in the meantime I
asserted "the only surviving copy", which was false (`L-119`).

WHAT IS INDEXED, AND WHAT IS DELIBERATELY NOT
---------------------------------------------
Kept: user turns, assistant text, and the COMMAND of each tool call. Those carry the
reasoning, the corrections and what was actually run.

Dropped: tool RESULTS, which are the bulk of the bytes and are almost always a file this
repository still contains — reindexing them would bloat the index to re-serve what `read`
already serves. Also dropped: queue-operation, title and system rows, which are UI mechanics.

That is the same discipline `latest_brief` applies to task notes: a memory that keeps
everything is a log, not a memory.

RETRIEVAL
---------
The same three layers as everything else (`D-32`): exact-token keyword for identifiers like
`routing.csv` or `t131`, embeddings for paraphrase, fused by reciprocal rank. Sessions from
other machines are imported into the same index and marked with their origin, because
`~/.claude/` is per-machine and the ThinkPad's history is invisible here.
"""
import argparse
import hashlib
import time
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SESSIONS = Path.home() / ".claude" / "projects"
IMPORTED = REPO / "workspace" / "imported-sessions"
INDEX = REPO / "workspace" / "transcript-index.jsonl"

KEEP_TYPES = {"user", "assistant"}
MAX_CHARS = 1400          # a turn longer than this is truncated, not dropped
VECTORS_DIR = REPO / 'workspace' / 'transcript-vectors'
DAY = 86400


def _text(msg):
    """The readable content of a transcript row, or None if it carries none."""
    c = msg.get("message", msg).get("content") if isinstance(msg.get("message"), dict) else msg.get("content")
    if isinstance(c, str):
        return c
    if not isinstance(c, list):
        return None
    parts = []
    results = 0
    for b in c:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and b.get("text"):
            parts.append(b["text"])
        elif b.get("type") == "tool_use":
            # The COMMAND, never the result. What was run is signal; what it printed is
            # usually a file this repository still has.
            inp = b.get("input") or {}
            cmd = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
            if cmd:
                parts.append(f"[{b.get('name','tool')}] {str(cmd)[:300]}")
        elif b.get("type") == "tool_result":
            results += 1
    if parts:
        return "\n".join(parts)
    # A turn that is ONLY a tool result keeps a placeholder, never the payload: the row has to
    # exist so a locator like "turn 88 of 395" resolves, and the payload's bytes are `read`'s
    # job, not the index's.
    return "[tool_result]" * results if results else None


def read_session(path, origin="hivemind"):
    """(session_id, [rows]) for one transcript."""
    rows = []
    sid = path.stem
    for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            print(f"  NOTE: unreadable line {n + 1} in {path.name} -- not kept", file=sys.stderr)
            continue
        if m.get("type") not in KEEP_TYPES:
            continue
        t = _text(m)
        if not t or not t.strip():
            continue
        rows.append({"session": sid, "origin": origin, "n": n,
                     "role": m.get("type"), "at": m.get("timestamp", ""),
                     # `text` stays the cut display field that search() and transcript_audit's
                     # FIELDS read; `full_text` is the WHOLE turn, so a quote sitting past
                     # MAX_CHARS is still findable, and `turn` is its 0-based index among this
                     # session's kept turns -- the anchor a locator like "turn 88 of 395" needs.
                     "text": t.strip()[:MAX_CHARS],
                     "turn": len(rows),
                     "full_text": t.strip(),
                     "sha256": hashlib.sha256(t.strip().encode("utf-8")).hexdigest()})
    return sid, rows


def previous_session(workdir=None, skip_live=True, live_window=900):
    """The transcript of the session BEFORE this one, for this working directory.

    Ordered by mtime. The newest file is the session that is asking (it is being
    written right now), so it is skipped when it was touched inside `live_window`
    seconds -- the same trap `L-148` describes for the context guard, which used to
    hand every new session the *previous* one's numbers by taking the newest file
    blindly. Here we want the previous one, so the rule is inverted, not absent.

    Returns (path, is_live_skipped) or (None, False).
    """
    # THE HOME DIRECTORY TOO (2026-09-25). Claude Code files a transcript under the directory a
    # session was LAUNCHED from, and background jobs start in ~ -- so the 09-23..25 sessions
    # sat in -home-user/ and `tail` handed the next session one from 09-22 instead.
    dirs = [SESSIONS / (workdir or _workdir_key())]
    if workdir is None:
        dirs.append(SESSIONS / _workdir_key(Path.home()))
    files = sorted((f for d in dirs if d.is_dir() for f in d.glob("*.jsonl")),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return None, False
    now = time.time()
    if skip_live and (now - files[0].stat().st_mtime) < live_window:
        return (files[1] if len(files) > 1 else None), True
    return files[0], False


def _workdir_key(root=None):
    """Claude Code's folder name for a working directory: the path with / as -."""
    return str(root or REPO).replace("/", "-")


def tail(path, turns=14, chars=700):
    """The last `turns` readable turns of one transcript, oldest first."""
    _, rows = read_session(path)
    return rows[-turns:] if turns > 0 else rows


def sources():
    """Every transcript this machine can see, local and imported."""
    out = []
    if SESSIONS.is_dir():
        for d in sorted(SESSIONS.iterdir()):
            if d.is_dir():
                for f in sorted(d.glob("*.jsonl")):
                    out.append((f, "hivemind", d.name))
    if IMPORTED.is_dir():
        for d in sorted(IMPORTED.iterdir()):
            if d.is_dir():
                for f in sorted(d.rglob("*.jsonl")):
                    out.append((f, d.name, f.parent.name))
    return out


def build():
    rows, seen = [], 0
    for path, origin, workdir in sources():
        sid, r = read_session(path, origin)
        for x in r:
            x["workdir"] = workdir
        rows.extend(r)
        seen += 1
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return seen, len(rows)


def load():
    if not INDEX.is_file():
        return []
    out = []
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"  NOTE: unreadable line in {INDEX} -- not listed", file=sys.stderr)
    return out


def row_id(row):
    '''The key a turn is stored and found under: '<session>:<n>'. n is the jsonl line index
    read_session records (transcripts.py:110-119), so it is stable across reindexes.'''
    return '{}:{}'.format(row['session'], row['n'])


def parse_at(at):
    '''Epoch seconds (float) for the timestamp form read_session stores -- '2026-10-13T09:15:00.000Z'
    and the same without milliseconds -- in UTC. Returns None for '' and for anything unparseable.
    It must NEVER return 0 for a bad value: 0 means 1970, which a 30-day window drops.
    Accepts a float/int through unchanged. No third-party dependency: time.strptime with the two
    formats, and a single except ValueError -> None, said on stderr.'''
    if isinstance(at, (int, float)):
        return at
    s = str(at).strip()
    if not s:
        return None
    try:
        try:
            t = time.strptime(s, '%Y-%m-%dT%H:%M:%S.%fZ')
        except ValueError:
            t = time.strptime(s, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError as e:
        print('  NOTE: unparseable timestamp {!r} -- kept as undated, not 1970 ({})'.format(s, e),
              file=sys.stderr)
        return None

    # strptime throws away the fractional seconds, and the index stores them. Recover them from the
    # string so .500Z does not silently become :00.
    frac = 0.0
    dot = s.find('.')
    if dot != -1:
        digits = s[dot + 1:].rstrip('Z')[:6]  # cut to 6 digits deliberately: strptime %f is microseconds.
        if digits.isdigit():
            frac = float(digits) / (10 ** len(digits))

    # No calendar import in this file: convert the civil date to days since 1970-01-01 (UTC)
    # with the standard days-from-civil algorithm, so parse_at is testable on a bare box.
    y = t.tm_year
    m = t.tm_mon
    d = t.tm_mday
    y_adj = y - (1 if m < 3 else 0)
    era = (y_adj if y_adj >= 0 else y_adj - 399) // 400
    yoe = y_adj - era * 400
    mp = m + (-3 if m > 2 else 9)
    doy = (153 * mp + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    days = era * 146097 + doe - 719468
    return float(days * DAY + t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec + frac)


def recent_rows(days=30, now=None, rows=None):
    '''The turns a query should search: the last `days` days, plus every console thread.

    rows=None -> the corpus: load() for the hivemind side, and read_session(path, origin='console')
    for every file in REPO / 'workspace' / 'console' / 'threads' with the glob '*.jsonl' (read_session
    returns (session_id, rows) and already tolerates unreadable lines). rows=[...] -> filter those
    rows; this is the path the tests use, and it must not touch the disk.
    now: epoch seconds, or a timestamp string parsed by parse_at, or None = time.time().
    A row is kept when parse_at(row.get('at', '')) is None (undated is KEPT, never dropped --
    no timestamp is not evidence of being old) or is >= now - days * DAY.
    Return the input row dicts UNCHANGED: session, n, turn, role, origin, at, text, full_text,
    sha256 all present. Do not rebuild rows from scratch and do not cut full_text.'''
    if rows is None:
        rows = load()
        threads = REPO / 'workspace' / 'console' / 'threads'
        if threads.is_dir():
            for path in sorted(threads.glob('*.jsonl')):
                try:
                    _, r = read_session(path, origin='console')
                except OSError as e:
                    print('  NOTE: unreadable console thread {} -- not searched ({})'.format(path, e),
                          file=sys.stderr)
                    continue
                rows.extend(r)

    if now is None:
        cutoff = time.time() - days * DAY
    else:
        when = parse_at(now)
        if when is None:
            # An invalid caller-supplied bound is not evidence that every turn is old; fall back
            # to 'now' and say so, rather than dropping the whole corpus.
            print('  NOTE: recent_rows now={!r} did not parse; using time.time()'.format(now),
                  file=sys.stderr)
            cutoff = time.time() - days * DAY
        else:
            cutoff = when - days * DAY

    out = []
    for row in rows:
        ts = parse_at(row.get('at', ''))
        if ts is None or ts >= cutoff:
            out.append(row)
    return out


def append_turns(rows, vectors, out_dir=VECTORS_DIR):
    '''Append-only store of turn vectors. rows and vectors are the same length, aligned by index;
    vectors is what the CALLER embedded -- append_turns must NOT call memory.embed. Keeping the
    model out of the writer is what lets the index be built at night (the embedder shares the
    ThinkPad with the Q4) while a new Console turn can be embedded by its own caller.

    out_dir/'meta.jsonl': one JSON line per stored row, appended, keys exactly
      {'id', 'session', 'n', 'turn', 'role', 'origin', 'at'}   -- NO text, NO full_text: the text
      already lives in workspace/transcript-index.jsonl and a second copy is a drift hazard.
    out_dir/'vectors.f32': the vectors themselves as struct.pack('<%df' % len(v), *v), appended,
    row order matching meta.jsonl. Float32 little-endian binary, not JSON, not text.
    Idempotent by id: read the ids already in meta.jsonl and skip any row whose row_id is present.
    Returns the number of rows newly written (0 when nothing new).
    Open both files in append mode ('a' / 'ab') -- never 'w'/'wb': rewriting must be impossible.
    mkdir parents=True, exist_ok=True on out_dir.
    A length mismatch between rows and vectors is a caller bug: print what it is on stderr and
    raise ValueError. Do not zip silently -- zip drops the extra rows in silence.'''
    import struct

    if len(rows) != len(vectors):
        msg = ('append_turns: {} rows and {} vectors are not aligned; '
               'the caller must embed every row').format(len(rows), len(vectors))
        print(msg, file=sys.stderr)
        raise ValueError(msg)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / 'meta.jsonl'
    vec_path = out_dir / 'vectors.f32'

    seen = set()
    if meta_path.is_file():
        for line in meta_path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print('  NOTE: unreadable meta line in {} -- id not marked ({})'.format(meta_path, e),
                      file=sys.stderr)
                continue
            rid = obj.get('id')
            if rid is not None:
                seen.add(rid)

    written = 0
    with meta_path.open('a', encoding='utf-8') as mf, vec_path.open('ab') as vf:
        for row, v in zip(rows, vectors):
            rid = row_id(row)
            if rid in seen:
                continue
            meta = {'id': rid,
                    'session': row.get('session'),
                    'n': row.get('n'),
                    'turn': row.get('turn'),
                    'role': row.get('role'),
                    'origin': row.get('origin'),
                    'at': row.get('at')}
            mf.write(json.dumps(meta) + '\n')
            vf.write(struct.pack('<%df' % len(v), *v))
            seen.add(rid)
            written += 1
    return written


def load_vectors(out_dir=VECTORS_DIR):
    '''{row_id: [floats]} for every stored vector, read from out_dir/'meta.jsonl' paired with
    out_dir/'vectors.f32' (dim is len of the first meta row's vector, or read the file in
    fixed-size records). Missing files -> {}. An empty vector is no measurement, not a zero
    score -- skip such a row rather than ranking it, the rule in memory.vector_rank.'''
    import struct

    out_dir = Path(out_dir)
    meta_path = out_dir / 'meta.jsonl'
    vec_path = out_dir / 'vectors.f32'
    if not meta_path.is_file() or not vec_path.is_file():
        return {}

    ids = []
    for line in meta_path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            print('  NOTE: unreadable meta line in {} -- skipped ({})'.format(meta_path, e),
                  file=sys.stderr)
            continue
        rid = obj.get('id')
        if rid is None:
            print('  NOTE: meta row without an id in {} -- skipped'.format(meta_path), file=sys.stderr)
            continue
        ids.append(rid)

    if not ids:
        return {}
    raw = vec_path.read_bytes()

    # meta.jsonl cannot carry the dimension without duplicating the vector, so use the fixed-size
    # record layout the writer produces: one equal-length float32 record per stored row.
    if len(raw) % 4:
        print('  NOTE: {} is not a whole number of float32 values -- not loaded'.format(vec_path),
              file=sys.stderr)
        return {}
    floats = len(raw) // 4
    if floats % len(ids):
        print('  NOTE: {} has {} float32 values for {} meta rows -- '
              'not fixed-size records; not loaded'.format(vec_path, floats, len(ids)),
              file=sys.stderr)
        return {}
    dim = floats // len(ids)

    out = {}
    off = 0
    for rid in ids:
        if dim == 0:
            # An empty vector is not a measurement. Skip it; ranking it as zeros is the bug
            # memory.vector_rank warns about.
            continue
        v = list(struct.unpack_from('<%df' % dim, raw, off))
        off += dim * 4
        if not v:
            continue
        out[rid] = [float(x) for x in v]
    return out


def find_quote(needle, session=None, rows=None):
    """Where a quote was said: every kept turn whose FULL text carries it.

    Reads `full_text`, never the cut display field, so a quote from deep inside a long turn
    is findable; `offset` is an index into that full text. An empty needle asks nothing.
    """
    if not needle:
        return []
    hits = []
    for row in (rows if rows is not None else load()):
        if session and row.get("session") != session:
            continue
        hay = row.get("full_text") or row.get("text") or ""
        if needle in hay:
            hits.append({"session": row.get("session"), "turn": row.get("turn"),
                         "n": row.get("n"), "role": row.get("role"),
                         "at": row.get("at"), "offset": hay.find(needle)})
    return hits


def search(query, limit=8, use_vectors=True):
    """Keyword first, embeddings as the paraphrase net, fused by RRF -- the same three-layer
    shape as `memory.py`, reusing its ranking rather than growing a second one (`L-046`)."""
    sys.path.insert(0, str(REPO / "scripts"))
    import memory
    rows = load()
    if not rows:
        return []
    docs = [{"id": f"{r['session'][:8]}:{r['n']}", "fact": r["text"],
             "tags": [r["role"], r["origin"]], "_row": r} for r in rows]
    kw = memory.keyword_rank(query, docs)
    if not use_vectors:
        return [d["_row"] for d in kw[:limit]]
    # Embedding 30 MB of turns on every search would be absurd; embed only what keyword
    # surfaced plus a sample, so the vector channel reranks rather than scans.
    pool = kw[:40] or docs[:40]
    vecs = memory.embed([d["fact"] for d in pool])
    if not vecs:
        return [d["_row"] for d in kw[:limit]]
    qv = memory.embed([query], is_query=True)
    if not qv:
        return [d["_row"] for d in kw[:limit]]
    scored = sorted(zip(pool, vecs), key=lambda p: -memory.cosine(qv[0], p[1]))
    fused = memory.rrf(kw, [d for d, _ in scored])
    return [d["_row"] for d in fused[:limit]]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index")
    sub.add_parser("sessions")
    s = sub.add_parser("search"); s.add_argument("query")
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--no-vectors", action="store_true")
    q = sub.add_parser("find", help="locate a quote in the indexed turns; prints session/turn/line/offset")
    q.add_argument("needle")
    q.add_argument("--session", default="")
    q.add_argument("--limit", type=int, default=10)
    t = sub.add_parser("tail", help="the last turns of the PREVIOUS session")
    t.add_argument("--turns", type=int, default=14)
    t.add_argument("--chars", type=int, default=700)
    t.add_argument("--session", default="", help="a session id, instead of the previous one")
    i = sub.add_parser("import"); i.add_argument("directory")
    i.add_argument("--origin", default="", help="machine name; defaults to the folder name")
    a = ap.parse_args()

    if a.cmd == "index":
        n, rows = build()
        print(f"  {n} session(s), {rows} turns indexed -> {INDEX.relative_to(REPO)}")
        return 0
    if a.cmd == "sessions":
        for path, origin, workdir in sources():
            sid, r = read_session(path, origin)
            mb = path.stat().st_size / 1e6
            print(f"  {origin:12} {workdir[:34]:34} {sid[:8]}  {mb:5.1f} MB  {len(r):5d} turns")
        return 0
    if a.cmd == "search":
        hits = search(a.query, limit=a.limit, use_vectors=not a.no_vectors)
        for h in hits:
            print(f"  [{h['origin']}/{h['session'][:8]} {h['role']:9} {str(h['at'])[:16]}]")
            print(f"    {h['text'][:200].replace(chr(10),' ')}")
        if not hits:
            print("  (nothing matched)")
        return 0
    if a.cmd == "find":
        rows = load()
        hits = find_quote(a.needle, session=a.session, rows=rows)
        by_turn = {(r.get("session"), r.get("turn")): r for r in rows}
        for h in hits[:a.limit]:
            print(f"  {h['session'][:8]} turn {h['turn']}  jsonl line {h['n']}  "
                  f"{h['role']}  {h['at']}  offset {h['offset']}")
            r = by_turn.get((h["session"], h["turn"])) or {}
            hay = r.get("full_text") or r.get("text") or ""
            # A window around the match, cut deliberately and labelled as context -- the row's
            # full text is what is stored; this is only what prints.
            ctx = hay[max(0, h["offset"] - 60):h["offset"] + len(a.needle) + 60]
            print(f"    context: ...{ctx}...")
        if not hits:
            print("  (no turn in the index carries that text)")
        return 0
    if a.cmd == "tail":
        if a.session:
            cand = [p for p, _, _ in sources() if p.stem.startswith(a.session)]
            if not cand:
                print(f"  no transcript matching {a.session}", file=sys.stderr)
                return 1
            path, skipped = cand[0], False
        else:
            path, skipped = previous_session()
        if path is None:
            print("  no earlier session on this machine for this directory.")
            return 0
        rows = tail(path, turns=a.turns)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
        print(f"  {path.stem[:8]}  last written {when}  ({len(rows)} of the final turns)")
        if skipped:
            print("  (the newest transcript is THIS session; showing the one before it)")
        print()
        for r in rows:
            print(f"  [{r['role']:9} {str(r['at'])[:16]}]")
            for line in r["text"][:a.chars].split("\n"):
                print(f"    {line}")
            print()
        return 0
    if a.cmd == "import":
        src = Path(a.directory).expanduser().resolve()
        if not src.is_dir():
            print(f"  no such directory: {src}", file=sys.stderr)
            return 1
        origin = a.origin or src.name
        dest = IMPORTED / origin
        dest.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in src.rglob("*.jsonl"):
            out = dest / f.parent.name / f.name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(f.read_bytes())
            n += 1
        print(f"  imported {n} transcript(s) from {src} as origin '{origin}'")
        print("  now run:  python3 scripts/transcripts.py index")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
