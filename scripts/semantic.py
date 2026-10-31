#!/usr/bin/env python3
"""Meaning-based matching for the session-review script: embeddings with a disk cache.

    import semantic
    semantic.close(query, texts, threshold)   # the best cosine of query against texts, or None

Measured before it was used (workspace/embed-eval/RESULTS.md, 2026-10-07): on the 20 hand-graded
sessions, "resolved later?" and "already covered?" by meaning at 0.70 took the script's false flags
576 -> 462 with every real miss kept; looser than 0.68 it starts losing real misses.

The embedder is the live one (scripts/memory.py: qwen3-embedding:0.6b via Ollama). When it does not
answer, every call returns None and the caller keeps its word test alone -- said once on stderr,
never raised: the review must not stop because an index model is down.

Vectors are cached by sha256 of (role, text) in workspace/session-reviews/index_cache/vectors.json
(git-ignored: index_cache/), so a task or transcript sentence is embedded once.
"""
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import memory  # noqa: E402

# SEMANTIC_CACHE (t790 step 6): the script audit scores in a sandbox worktree, where index_cache/ is
# git-ignored and absent -- every score there re-embedded every task and sentence from nothing. The
# audit points its children at the live cache instead.
import os  # noqa: E402
CACHE_FILE = Path(os.environ.get("SEMANTIC_CACHE") or
                  REPO / "workspace" / "session-reviews" / "index_cache" / "vectors.json")
RESOLVE_AT = 0.70        # "resolved later in the session?" -- measured, see the module docstring
COVER_AT = 0.70          # "already covered by a task?"
_cache = None
_dirty = 0
_down = False


def _key(text, query):
    return hashlib.sha256((("q:" if query else "d:") + text).encode("utf-8")).hexdigest()[:24]


# THE CACHE IS ADDITIVE (t790 step 6, 2026-10-12). It was one 268 MB JSON that every process loaded
# whole and wrote back whole on save -- session reviews, RCAs and the audit's scorer share it, so the
# last writer silently dropped every vector the others had added. Lost vectors were embedded again
# (466 of 487 s of one session's score was waiting on the embedder), and a text embedded twice in a
# different batch can come back a hair different, so the same code on the same snapshot scored
# 481 then 477 flags. Now new entries are APPENDED to LOG under a lock and every load replays the log
# over the base file: nothing is ever overwritten. `compact()` folds the log into the base.
LOG = CACHE_FILE.with_name(CACHE_FILE.stem + ".log.jsonl")
_new = {}


def _load():
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
        torn = 0
        if LOG.is_file():
            with LOG.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        k, v = json.loads(line)
                    except ValueError:
                        torn += 1             # a torn line from a killed writer: the rest stand
                        continue
                    _cache[k] = v
        if torn:
            print(f"  NOTE: {torn} torn line(s) in {LOG.name} skipped; those vectors are re-made",
                  file=sys.stderr)
    return _cache


def _put(k, v):
    global _dirty
    _cache[k] = v
    _new[k] = v
    _dirty += 1


def save():
    """Append this process's new entries to the log, under a lock. Never rewrites the base."""
    global _dirty
    if not _new:
        _dirty = 0
        return
    import fcntl
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write("".join(json.dumps([k, v]) + "\n" for k, v in _new.items()))
            f.flush()
            fcntl.flock(f, fcntl.LOCK_UN)
        _new.clear()
        _dirty = 0
    except OSError as e:
        print(f"  NOTE: semantic cache not saved ({e}); vectors are re-made next run", file=sys.stderr)


def compact():
    """Fold the log into the base file (under the log's lock), then empty the log."""
    import fcntl
    with LOG.open("a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        global _cache
        _cache = None
        c = _load()
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(c), encoding="utf-8")
        tmp.replace(CACHE_FILE)
        f.truncate(0)
        fcntl.flock(f, fcntl.LOCK_UN)
    return len(c)


def vectors(texts, query=False):
    """[vector] for texts, or None when the embedder is down (the caller falls back to words)."""
    global _dirty, _down
    if _down:
        return None
    c = _load()
    todo = [t for t in dict.fromkeys(texts) if _key(t, query) not in c]
    for i in range(0, len(todo), 64):
        got = memory.embed(todo[i:i + 64], timeout=300, is_query=query)
        if got is None:
            _down = True
            print("  NOTE: embedder down -- the session-review script matches by words only this run",
                  file=sys.stderr)
            return None
        for t, v in zip(todo[i:i + 64], got):
            _put(_key(t, query), [round(x, 5) for x in v])
    if _dirty >= 500:
        save()
    return [c[_key(t, query)] for t in texts]


_unit = {}


def _units(texts, query=False):
    """Unit-length vectors, made once per text per process, so a cosine is one dot product."""
    import math
    vs = vectors(texts, query=query)
    if vs is None:
        return None
    out = []
    for t, v in zip(texts, vs):
        k = _key(t, query)
        u = _unit.get(k)
        if u is None:
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            u = _unit[k] = [x / n for x in v]
        out.append(u)
    return out


def best(query, texts):
    """(best cosine, index) of query against texts, or None when there is nothing to compare or
    the embedder is down.

    MEMOISED BY ITS EXACT INPUTS (t790 step 6, 2026-10-12). The script audit re-scores the whole
    reference set after every proposed edit, and almost every (candidate, later sentences) question
    is the same one each time: the set score took 923 s against ~20 s before meaning matching, so a
    10-edit audit would have run for hours. The answer is a pure function of the two texts' vectors,
    so it is cached under sha256(query, texts) beside the vectors; an edit pays only for the
    questions it actually changes. Vectors are normalised once, so each comparison is a dot product."""
    import operator
    if not texts:
        return None
    c = _load()
    h = hashlib.sha256(("b:" + query + "\x00" + "\x00".join(texts)).encode("utf-8")).hexdigest()[:24]
    hit = c.get("b:" + h)
    if hit is not None:
        return hit[0], hit[1]
    q = _units([query], query=True)
    d = _units(texts)
    if q is None or d is None:
        return None
    qv = q[0]
    score, idx = max((sum(map(operator.mul, qv, v)), i) for i, v in enumerate(d))
    _put("b:" + h, [score, idx])
    return score, idx
