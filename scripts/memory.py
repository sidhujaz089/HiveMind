#!/usr/bin/env python3
"""Memory for the local models: a hot tier that is always injected, a warm tier that is asked for.

    python3 scripts/memory.py add "line-range edits replace whole lines" --tags python,edit \
        --see docs/LESSONS.md#L-118
    python3 scripts/memory.py hot                    what every run is given
    python3 scripts/memory.py recall "indentation"   hybrid search over the warm tier
    python3 scripts/memory.py index                  build/refresh the embedding index
    python3 scripts/memory.py eval                   does hybrid actually beat keyword?

WHY TIERS
---------
Jas, 2026-08-26: *"if we inject all of it, will that cause problems when the memory is
massive? ... this session alone has used more than double what the model can handle."*

He is right and the arithmetic is unkind. `docs/LESSONS.md` grew 118 entries in 15 days --
7.9 a day. At that rate a flat memory injected whole is 44% of a 262,144-token context within
a year and exceeds it entirely by year three. Sizing a buffer for today's model and never
revisiting it is the mistake that produced the 14,000-character excerpt cap.

    HOT   always injected, hard cap. Constraints that apply to nearly every run.
    WARM  searched on request. Grows without bound and costs nothing until asked for.
    COLD  the documents themselves -- LESSONS.md, AGENTS.md, CHECKS.md. Memory POINTS at
          them and never copies them, because a copy drifts (`L-046`).

Promotion is by use: a warm record recalled repeatedly earns a hot slot; a hot record nobody
recalls falls back. Nobody has to guess what matters.

WHEN GREP IS NOT ENOUGH -- THE POINT OF THIS FILE
-------------------------------------------------
Keyword search fails on paraphrase. A record saying "whitespace" is invisible to a search for
"indentation", and this project's own history is full of that gap: `L-116` was `pgrep`
matching itself three times in three different costumes, and no single keyword covers them.

But keyword must stay PRIMARY here, because so much of what is searched for is an exact
token -- `L-114`, `apply_ops`, `coder.py`, `t159`. Pure vector search would be a downgrade for
identifiers.

So: run both channels and fuse the rankings with Reciprocal Rank Fusion,
`score = sum(1 / (k + rank))`. RRF is used because it needs no score normalisation -- the two
channels produce incomparable numbers -- and no tuning beyond `k`, conventionally 60.

If the embedding model is absent the vector channel is skipped and keyword still answers.
Degrading to "worse but working" beats failing, and `eval` exists so "hybrid is better" is a
measurement rather than a belief.
"""
import argparse
import hashlib
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STORE = REPO / "workspace" / "memory.jsonl"
INDEX = REPO / "workspace" / "memory-index.json"
OLLAMA = None           # an explicit override (tests); None -> the server REGISTRY names, below
OLLAMA_DEFAULT = "http://127.0.0.1:11434/api/embed"
EMBED_MODEL = "qwen3-embedding:0.6b"
# THE EMBEDDER RUNS ON THE THINKPAD eGPU (Jas, 2026-10-13: "Yes to moving the embedder to the eGPU").
# It held 4-5 GB of the GX10's pool at a 32k context, and builds were waiting for memory beside
# Swift. Its server is REGISTRY's `server` for EMBED_MODEL, so moving it again is a config edit.
# 8192 is ample for sentences and records; it is 2.86 GB there beside the Q4 (/api/ps, 2026-10-13).
# Vectors match the GX10's to cosine 0.9991-0.9997 (same digest ac6da0dfba84), so the cache stands.
EMBED_CTX = 8192
_URL = None


def _placement(url):
    """(context, cpu?) SERVERS.json declares for EMBED_MODEL on the server at `url`, else (EMBED_CTX, False).
    2026-10-14: the ThinkPad holds the embedder on its CPU (placement 'cpu', context 1024) so the eGPU
    can cycle between the Auditor and Frontline and embedding still works in every mode."""
    try:
        d = json.loads((REPO / "projects" / "local-inference" / "models" / "SERVERS.json").read_text(encoding="utf-8"))
        for s in d.get("servers", {}).values():
            if s.get("url") and url.startswith(s["url"].rstrip("/")):
                m = (s.get("models") or {}).get(EMBED_MODEL) or {}
                return int(m.get("context") or EMBED_CTX), m.get("placement") == "cpu"
    except (OSError, ValueError) as e:
        print(f"  NOTE: SERVERS.json unreadable for the embedder's placement ({e})", file=sys.stderr)
    return EMBED_CTX, False


def _device_paused(url):
    """Is the device behind this URL paused from the Hub (Jas, 2026-10-13: "I may want to disconnect the
    eGPU ... I don't want issues")? A paused ThinkPad means: do not call it, use the GX10."""
    if url == OLLAMA_DEFAULT:
        return False
    try:
        d = REPO / "projects" / "local-inference" / "models"
        srv = json.loads((d / "SERVERS.json").read_text(encoding="utf-8"))
        host = url.split("//", 1)[-1].split(":", 1)[0]
        dev = next((x.get("device") for x in srv.get("servers", {}).values()
                    if x.get("url", "").split("//", 1)[-1].split(":", 1)[0] == host), None)
        return bool(dev) and (REPO / "workspace" / "run-logs" / f"device-paused-{dev}.enabled").exists()
    except (OSError, ValueError) as e:
        print(f"  NOTE: device pause unread ({type(e).__name__}); calling {url}", file=sys.stderr)
        return False


def embed_url():
    """The /api/embed URL: OLLAMA if set, else REGISTRY's server for EMBED_MODEL -- unless that device is
    paused, then the GX10's."""
    global _URL
    if OLLAMA:
        return OLLAMA
    if _URL is None:
        _URL = OLLAMA_DEFAULT
        try:
            d = REPO / "projects" / "local-inference" / "models"
            reg = json.loads((d / "REGISTRY.json").read_text(encoding="utf-8"))
            srv = json.loads((d / "SERVERS.json").read_text(encoding="utf-8"))["servers"]
            sid = next((m.get("server") for m in reg.get("base_models", []) if m.get("tag") == EMBED_MODEL), None)
            if sid and srv.get(sid, {}).get("url"):
                _URL = srv[sid]["url"].rstrip("/") + "/api/embed"
        except (OSError, ValueError, KeyError) as e:
            print(f"  NOTE: embedder server unread ({type(e).__name__}); using the GX10's", file=sys.stderr)
    return OLLAMA_DEFAULT if _device_paused(_URL) or _auditor_beside(_URL) else _URL


def _auditor_beside(url):
    """True when the Auditor has lanes and its model is served from the embedder's host. Jas, 2026-10-14:
    the Q4 on the ThinkPad eGPU takes ~5 GB of Windows RAM, and with the CPU embedder (1.4 GB) beside it
    the memory guard unloaded the Q4 six times in two minutes. While the Auditor runs there, embed on the
    GX10 (slower) instead."""
    try:
        d = REPO / "projects" / "local-inference" / "models"
        auditor = json.loads((d / "ROLES.json").read_text(encoding="utf-8"))["roles"].get("auditor") or {}
        if not int(auditor.get("lanes") or 0):
            return False
        srv = json.loads((d / "SERVERS.json").read_text(encoding="utf-8"))["servers"]
        host = url.split("//", 1)[-1].split(":", 1)[0]
        return any(auditor.get("model") in (x.get("models") or {})
                   and x.get("url", "").split("//", 1)[-1].split(":", 1)[0] == host for x in srv.values())
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"  NOTE: auditor placement unread ({type(e).__name__}); calling {url}", file=sys.stderr)
        return False

HOT_MAX = 40            # a few thousand tokens, forever, whatever the warm tier does
RRF_K = 60              # conventional; the ranking is insensitive to it
STOP = {"the", "a", "an", "is", "are", "to", "of", "in", "and", "or", "it", "that", "this",
        "for", "on", "with", "as", "be", "by", "not", "you", "your", "must", "when"}


def load():
    if not STORE.is_file():
        return []
    out = []
    for line in STORE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"_unreadable": line})
    return out


def save(records):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def digest(r):
    """Integrity, not retrieval. If the text changed, the cached vector is stale."""
    return hashlib.sha256((r.get("fact", "") + " ".join(r.get("tags", []))).encode()).hexdigest()


def tokens(text):
    return [w for w in re.findall(r"[a-z0-9_.\-]+", str(text).lower())
            if w not in STOP and len(w) > 1]


# ---------------------------------------------------------------- keyword channel
def keyword_rank(query, records):
    """Rank by term overlap, with an idf-ish weight so common words do not dominate.

    Deliberately simple and deliberately EXACT: an identifier like `apply_ops` or `L-114`
    either appears or does not, which is precisely what a vector channel is bad at.
    """
    q = tokens(query)
    if not q:
        return []
    df = {}
    for r in records:
        seen = set(tokens(r.get("fact", "")) + [t.lower() for t in r.get("tags", [])])
        for w in seen:
            df[w] = df.get(w, 0) + 1
    n = max(len(records), 1)
    scored = []
    for r in records:
        body = tokens(r.get("fact", "")) + [t.lower() for t in r.get("tags", [])] * 2
        s = 0.0
        for w in q:
            c = body.count(w)
            if c:
                s += (1 + math.log(c)) * math.log(1 + n / (1 + df.get(w, 0)))
        if s > 0:
            scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored]


# ---------------------------------------------------------------- vector channel
# Qwen3-Embedding is asymmetric: a QUERY needs an instruction prefix, a DOCUMENT does not.
# Without it the model barely discriminates -- measured 2026-08-26, the query "indentation"
# ranked an unrelated record about prompt injection ABOVE the record about whitespace, with
# every score bunched between 0.39 and 0.50. With the prefix the right record comes first and
# the spread widens from 0.212 to 0.275.
#
# This is the same class as `chat_template_kwargs` being ignored by Ollama: a parameter the
# model needs, silently absent, producing plausible-looking output that is wrong. It was
# caught only because `eval` was written before the channel was trusted.
QUERY_INSTRUCTION = ("Instruct: Given a question about a coding tool's behaviour or "
                     "constraint, retrieve the recorded fact that answers it\nQuery: ")


def prep(texts, is_query):
    """Queries get the instruction, documents do not. Lifted out of embed() so a test can
    drive it without a network call -- `L-117`, sixth occurrence: asserting that the caller
    passes a flag does not prove the flag does anything."""
    return [QUERY_INSTRUCTION + t for t in texts] if is_query else list(texts)


QUERY_TIMEOUT = 10      # t389: recall answers from keywords if the embedder is slow, never waits 120 s
RECALL_EMBED_MAX = 5    # recall embeds at most this many changed records itself; `index` does the rest


def embed(texts, timeout=120, is_query=False):
    """Vectors from the local embedding model, or None if it is not available (said on stderr)."""
    texts = prep(texts, is_query)
    try:
        url = embed_url()
        ctx, cpu = _placement(url)
        body = {"model": EMBED_MODEL, "input": texts, "options": _options(ctx, cpu), "keep_alive": "24h"}
        if url == OLLAMA_DEFAULT:
            body["keep_alive"] = "5m"     # a fallback on the GX10 must not hold its pool for a day
        t0 = time.time()
        try:
            body = _post(url, body, timeout)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            # THE eGPU UNPLUGGED OR DOWN (2026-10-13): one retry on the GX10's Ollama, said, rather
            # than recall and session review dropping to keywords until someone notices.
            if url == OLLAMA_DEFAULT or OLLAMA:
                raise
            print(f"  NOTE: embedder at {url} unreachable ({type(e).__name__}); using the GX10's",
                  file=sys.stderr)
            url = OLLAMA_DEFAULT
            t0 = time.time()
            ctx, _cpu = _placement(url)
            body = _post(url, {**body, "keep_alive": "5m", "options": _options(ctx)}, timeout)
        # Ollama counts the tokens it embedded (prompt_eval_count): the embedder's tokens/second
        # (Jas, 2026-10-13: "Does the embedder get a tokens/second reading?").
        _log_call(len(texts), time.time() - t0, True, tokens=body.get("prompt_eval_count"), url=url)
        return body.get("embeddings")
    except Exception as e:                                          # noqa: BLE001 -- said, then keyword-only
        _log_call(len(texts), None, False, f"{type(e).__name__}: {e}")
        print(f"  NOTE: embedding unavailable ({type(e).__name__}: {e})", file=sys.stderr)
        return None


_OPTS = {}


def _options(ctx=None, cpu=False):
    """The embed call's options from settings.options() -- the one source (test_options_contract) --
    with num_ctx pinned to the server's declared context and, for a CPU placement, num_gpu 0
    (SERVERS.json; memory._placement). Cached per (ctx, cpu)."""
    key = (ctx or EMBED_CTX, cpu)
    if key not in _OPTS:
        sys.path.insert(0, str(REPO / "projects" / "local-inference"))
        import settings
        _OPTS[key] = settings.options("chat", EMBED_MODEL, num_ctx=key[0], **({"num_gpu": 0} if cpu else {}))
    return _OPTS[key]


def _post(url, body, timeout):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return json.load(f)


CALLS = REPO / "workspace" / "run-logs" / "embed-calls.jsonl"


def _log_call(n, secs, ok, err=None, tokens=None, url=None):
    """One row per embed call (2026-10-13, Jas: "I don't see the embedder role on the lanes or Production
    tabs"). The embedder has no jobs of its own -- session review, recall and the corpus index call it
    inline -- so without this its tile has nothing to say. Read by lane_status.embedder_stats()."""
    try:
        with CALLS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "texts": n,
                                "seconds": round(secs, 2) if secs is not None else None, "tokens": tokens, "ok": ok,
                                "error": err, "url": url or embed_url()}) + "\n")
    except OSError as e:
        print(f"  NOTE: embed call not logged ({type(e).__name__}: {e})", file=sys.stderr)


def cosine(a, b):
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def build_index(records, verbose=True, timeout=120, max_todo=None):
    """Embed anything whose digest changed. Cached by sha256, so a reindex is cheap."""
    cache = {}
    if INDEX.is_file():
        try:
            cache = json.loads(INDEX.read_text())
        except json.JSONDecodeError:
            cache = {}
    todo = [r for r in records if r.get("id") and cache.get(r["id"], {}).get("sha") != digest(r)]
    if todo and max_todo is not None and len(todo) > max_todo:
        if verbose:
            print(f"  {len(todo)} record(s) need embedding -- left for `memory.py index`", file=sys.stderr)
        todo = []
    if todo:
        vecs = embed([r["fact"] for r in todo], timeout=timeout)
        if vecs is None:
            if verbose:
                print(f"  embedding model unavailable ({EMBED_MODEL}); keyword only",
                      file=sys.stderr)
            return cache
        for r, v in zip(todo, vecs):
            cache[r["id"]] = {"sha": digest(r), "v": v}
        INDEX.write_text(json.dumps(cache))
        if verbose:
            print(f"  embedded {len(todo)} record(s)", file=sys.stderr)
    return cache


def vector_rank(query, records, cache):
    qv = embed([query], is_query=True, timeout=QUERY_TIMEOUT)
    if not qv or not cache:
        return []
    qv = qv[0]
    scored = []
    for r in records:
        e = cache.get(r.get("id", ""))
        if e and e.get("v") and qv:          # an empty vector is no measurement, not a zero score
            scored.append((cosine(qv, e["v"]), r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored]


# ---------------------------------------------------------------- fusion
def rrf(*rankings, k=RRF_K):
    """Reciprocal Rank Fusion. No score normalisation, no tuning, robust to one bad channel."""
    score = {}
    holder = {}
    for ranking in rankings:
        for rank, r in enumerate(ranking):
            rid = r.get("id") or id(r)
            score[rid] = score.get(rid, 0.0) + 1.0 / (k + rank + 1)
            holder[rid] = r
    return [holder[i] for i in sorted(score, key=lambda x: -score[x])]


def recall(query, records=None, limit=6, use_vectors=True):
    records = load() if records is None else records
    warm = [r for r in records if "_unreadable" not in r]
    kw = keyword_rank(query, warm)
    if not use_vectors:
        return kw[:limit]
    # KEYWORDS ALWAYS ANSWER; VECTORS ONLY ADD (t389). recall used to embed every changed record
    # and wait up to 120 s for the query vector before returning anything.
    vec = vector_rank(query, warm, build_index(warm, verbose=False, timeout=QUERY_TIMEOUT,
                                               max_todo=RECALL_EMBED_MAX))
    if not vec:
        return kw[:limit]
    return rrf(kw, vec)[:limit]


# ---------------------------------------------------------------- tiers and writing
def hot(records=None):
    """What every run is given, unasked. Ordered by how often it has actually been recalled."""
    records = load() if records is None else records
    live = [r for r in records if "_unreadable" not in r]
    live.sort(key=lambda r: (-int(r.get("hot", 0)), -int(r.get("recalled", 0))))
    return live[:HOT_MAX]


def add(fact, tags, see="", source_run="", hot_flag=False):
    records = load()
    ids = [int(r["id"][1:]) for r in records if str(r.get("id", "")).startswith("m")]
    rec = {"id": f"m{max(ids) + 1 if ids else 1:04d}",
           "tags": [t.strip().lower() for t in tags if t.strip()][:6],
           # STORED WHOLE. This was fact.strip()[:240], which truncated the record itself, so a
           # fact longer than 240 characters was lossy FOREVER -- unrecoverable, not just
           # unshown. The reason for a bound here is real (hot memory is injected into
           # every system prompt) but it belongs where the injection happens, not where
           # the record is written. 2026-09-14.
           "fact": fact.strip(),
           # A POINTER, never a copy. LESSONS.md is the source of truth for the reasoning;
           # duplicating it here guarantees the two drift (`L-046`).
           "see": see.strip(),
           "source_run": source_run,
           "created": time.strftime("%Y-%m-%d"),
           "hot": bool(hot_flag), "recalled": 0}
    records.append(rec)
    save(records)
    return rec


def mark_recalled(hits):
    """Usage drives promotion. Nobody has to guess what matters."""
    if not hits:
        return
    ids = {h.get("id") for h in hits}
    records = load()
    for r in records:
        if r.get("id") in ids:
            r["recalled"] = int(r.get("recalled", 0)) + 1
    save(records)


# ---------------------------------------------------------------- does hybrid actually win?
# t266: evaluate() must JUDGE, not print three rankings. The cases live in a file with a known right
# answer each (EVAL_FILE, JSONL: query, want, channel keyword|vector, why, approved_by), and five gates
# make a bad measurement fail instead of passing quietly:
#   DANGLING    a case names a record that is not in the store
#   UNEXAMINED  a store record no case names -- a coverage hole, not a pass
#   VACUOUS     a VECTOR case whose query shares more than N_SHARED_MAX content words with its record:
#               a paraphrase test answered by copying tests nothing (a keyword case is MEANT to share)
#   MISS        the designated channel ranks something else first
#   UNJUDGED    a vector case with no embeddings -- reported, never dropped
# The hybrid is SCORED on every case and never gates: a hybrid worse than a channel shows as a lower
# number, which is what "does hybrid actually win?" needs to see.
EVAL_FILE = REPO / "workspace" / "memory-eval.jsonl"
N_SHARED_MAX = 2


def load_cases(path=None):
    p = Path(path or EVAL_FILE)
    if not p.is_file():
        return []
    out = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError as e:
                print(f"  NOTE: {p.name}:{n} unreadable, not a case ({e})")
    return out


def evaluate():
    """Judge keyword / vector / hybrid on cases with a known right answer. 0 = every gate passed and
    every designated channel won its cases; 1 otherwise."""
    records = [r for r in load() if "_unreadable" not in r]
    if not records:
        print("  no memory to evaluate")
        return 0
    cases = load_cases()
    if not cases:
        print(f"  FAIL: no eval cases in {EVAL_FILE} -- nothing has a known right answer, so nothing is judged")
        return 1
    cache = build_index(records)
    by = {r["id"]: r for r in records}
    print(f"  {len(records)} record(s), {len(cases)} case(s), embeddings {'available' if cache else 'UNAVAILABLE'}\n")
    problems = []
    for c in cases:
        if c.get("want") not in by:
            problems.append(f"DANGLING  case {c.get('query')!r} wants {c.get('want')}, which is not in the store")
    named = {c.get("want") for c in cases}
    for rid in sorted(set(by) - named):
        problems.append(f"UNEXAMINED  {rid} is named by no case")
    score = {"keyword": [0, 0], "vector": [0, 0], "hybrid": [0, 0]}
    for c in cases:
        q, want, ch = c.get("query", ""), c.get("want"), c.get("channel")
        if want not in by:
            continue
        if ch == "vector":
            shared = set(tokens(q)) & set(tokens(by[want].get("fact", "")))
            if len(shared) > N_SHARED_MAX:
                problems.append(f"VACUOUS  vector case {q[:60]!r} shares {len(shared)} content words with {want} "
                                f"({', '.join(sorted(shared))}) -- a copy, not a paraphrase")
                continue
        kw = keyword_rank(q, records)
        vec = vector_rank(q, records, cache)
        hy = rrf(kw, vec) if vec else kw
        tops = {"keyword": kw[0]["id"] if kw else None, "vector": vec[0]["id"] if vec else None,
                "hybrid": hy[0]["id"] if hy else None}
        print(f"  {q!r} -> want {want} ({ch})  keyword {tops['keyword']}  vector {tops['vector']}  hybrid {tops['hybrid']}")
        score["hybrid"][1] += 1
        score["hybrid"][0] += tops["hybrid"] == want
        if ch == "vector" and not vec:
            problems.append(f"UNJUDGED  vector case {q!r}: no embeddings, so the vector channel was not measured")
            continue
        if ch in ("keyword", "vector"):
            score[ch][1] += 1
            if tops[ch] == want:
                score[ch][0] += 1
            else:
                problems.append(f"MISS  {ch} case {q!r}: ranked {tops[ch]} first, want {want}")
    print("\n  SCORE " + " ".join(f"{k}={w}/{n}" for k, (w, n) in score.items()))
    for p in problems:
        print(f"  {p}")
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("add"); a1.add_argument("fact")
    a1.add_argument("--tags", default=""); a1.add_argument("--see", default="")
    a1.add_argument("--run", default=""); a1.add_argument("--hot", action="store_true")
    sub.add_parser("hot")
    a2 = sub.add_parser("recall"); a2.add_argument("query")
    a2.add_argument("--limit", type=int, default=6)
    a2.add_argument("--no-vectors", action="store_true")
    sub.add_parser("index"); sub.add_parser("eval"); sub.add_parser("list")
    a = ap.parse_args()

    if a.cmd == "add":
        r = add(a.fact, a.tags.split(","), a.see, a.run, a.hot)
        print(f"  {r['id']}  {r['fact']}")
        return 0
    if a.cmd == "hot":
        for r in hot():
            print(f"  {r['id']}  [{','.join(r['tags'])}]  {r['fact']}"
                  + (f"   -> {r['see']}" if r.get("see") else ""))
        return 0
    if a.cmd == "recall":
        hits = recall(a.query, limit=a.limit, use_vectors=not a.no_vectors)
        mark_recalled(hits)
        for r in hits:
            print(f"  {r['id']}  {r['fact']}" + (f"   -> {r['see']}" if r.get("see") else ""))
        if not hits:
            print("  (nothing matched)")
        return 0
    if a.cmd == "index":
        build_index([r for r in load() if "_unreadable" not in r])
        return 0
    if a.cmd == "eval":
        return evaluate()
    if a.cmd == "list":
        for r in load():
            print(f"  {r.get('id','?')}  hot={r.get('hot')} recalled={r.get('recalled')}  "
                  f"{r.get('fact','')[:70]}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
