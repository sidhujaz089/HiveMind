#!/usr/bin/env python3
"""t078 -- the task list is searchable by the corpus machinery, semantically.

Run:  python3 scripts/tests/test_memory_task_records_semantic.py
Exit 0 = every assertion passed. Red on scripts/memory.py as it stands: it has no
task_records() and no search_tasks() (the file is 416 lines, neither name appears in
it), so every test below dies on AttributeError. That is the change the task asks
for, not a bug in this test.

What is pinned, and why each assertion is here
  1. task_records(dir) turns one task file into one record in the shape the corpus
     machinery already consumes (id / tags / fact / see, the keys add writes), with
     the WHOLE body in fact: the tail sentinel below is a real sentence at the end of
     the file, so a slice loses it.
  2. search_tasks(query) ranks by the VECTOR channel. The query shares not one content
     word with the record it must rank first, and the keyword channel is asserted to be
     silent on it, which is what makes this a semantics test and not a substring test.
     is_query is asserted on both sides of the embed call, because the asymmetric
     instruction prefix (prep, lifted out of embed so a test can drive it without a
     network call) is the only thing that makes a query vector comparable to a document
     vector.
  3. The fake embedder refuses any text it was not written to answer, so the suite
     cannot pass by embedding the wrong thing.
  4. Nothing is written outside the temp dir: the repo's own memory.jsonl and
     memory-index.json are byte-compared before and after, and the embedding cache must
     land at the redirected INDEX, which is what proves the code used the module's own
     cache path and not a private second index (in a throwaway worktree the live files
     may be untracked, so the byte-comparison alone proves nothing).
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import memory  # noqa: E402

QUERY = "how do I find a half-formed thought I already wrote down"
TAIL_SENTINEL = "blocked on an embedding model."

T078 = """---
id: t078
title: A search bar for the task list
area: ppc
kind: build
status: queued
assigned: production
source: added from the phone
created: 2026-08-22T024208Z
---

I'm going to keep pumping out ideas and I want to have a search bar that finds ideas
so I can add notes to existing ideas easily. It can't be key word based. Whatever we
build for the corpus should be used so it understand the intent of what I want

## Notes

**2026-08-24** - Deferred deliberately, and it is blocked on something real rather
than on effort: building a keyword search now would be the wrong thing shipped twice.

**2026-08-26** - The blocker named above is gone. Deferred deliberately, blocked on an
embedding model.
"""

T114 = """---
id: t114
title: The training database
area: ppc
kind: build
status: queued
---

Store every dispatch outcome so a future model can be trained on it.
"""


def _write_tasks(tmp):
    (tmp / "t078--a-search-bar-for-this-list.md").write_text(T078, encoding="utf-8")
    (tmp / "t114--the-training-database.md").write_text(T114, encoding="utf-8")


def _fake_embed(calls):
    """Answers exactly three texts and refuses everything else."""
    vectors = {QUERY: [1.0, 0.0, 0.0],
               "search bar for the task list": [1.0, 0.0, 0.0],
               "The training database": [0.0, 1.0, 0.0]}

    def fake(texts, timeout=120, is_query=False):
        out = []
        for t in texts:
            hits = [v for key, v in vectors.items() if key in t]
            if len(hits) != 1:
                raise AssertionError(
                    "embed() was asked for " + repr(t[:80]) + ", which this fixture does not "
                    "know: a stub must answer only what it was written to answer.")
            out.append(list(hits[0]))
            calls.append((t, bool(is_query)))
        return out
    return fake


def _search_in(tmp, calls):
    """One search, with every live path redirected into tmp and the embedder faked."""
    saved = (memory.STORE, memory.INDEX, memory.embed)
    memory.STORE = tmp / "memory.jsonl"
    memory.INDEX = tmp / "memory-index.json"
    memory.embed = _fake_embed(calls)
    try:
        return memory.task_records(tmp), memory.search_tasks(QUERY, limit=2, tasks_dir=tmp)
    finally:
        memory.STORE, memory.INDEX, memory.embed = saved


def test_task_records_read_the_task_files():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _write_tasks(tmp)
        recs = memory.task_records(tmp)
        by = {r["id"]: r for r in recs}
        assert set(by) == {"t078", "t114"}, "ids " + str(sorted(by))
        r = by["t078"]
        assert isinstance(r["tags"], list), "tags must be a list: digest() joins them"
        tags = [str(t).lower() for t in r["tags"]]
        assert "ppc" in tags and "build" in tags, "tags " + str(tags)
        assert "pumping out ideas" in r["fact"], r["fact"][:120]
        assert TAIL_SENTINEL in r["fact"], "the body was cut: a record must be stored whole"
        assert str(r["see"]).endswith("t078--a-search-bar-for-this-list.md"), r["see"]


def test_the_search_is_semantic_not_a_keyword_grep():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _write_tasks(tmp)
        calls = []
        recs, hits = _search_in(tmp, calls)
        kw = memory.keyword_rank(QUERY, recs)
        assert kw == [], (
            "keyword search already ranks " + str([h.get("id") for h in kw]) + " for "
            + repr(QUERY) + ": this fixture would then test substring matching, not the semantic channel")
        assert hits, "search_tasks returned nothing"
        assert hits[0]["id"] == "t078", str([h["id"] for h in hits])
        assert str(hits[0]["see"]).endswith("t078--a-search-bar-for-this-list.md"), str(hits[0])
        asked = [c for c in calls if c[1]]
        assert len(asked) == 1 and asked[0][0] == QUERY, (
            "the query must be embedded exactly once, with is_query=True so the instruction "
            "prefix is applied to it; got " + str(asked))
        docs = [c for c in calls if not c[1]]
        assert set(c[0] for c in docs) == set(r["fact"] for r in recs), str(
            sorted(c[0][:40] for c in docs))
        assert all(c[1] is False for c in docs), "documents must not get the query prefix"


def test_a_search_writes_nothing_outside_its_temp_dir():
    live = [REPO / "workspace" / "memory.jsonl", REPO / "workspace" / "memory-index.json"]
    before = [(p, p.read_bytes() if p.is_file() else None) for p in live]
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _write_tasks(tmp)
        _search_in(tmp, [])
        assert (tmp / "memory-index.json").is_file(), (
            "the embedding cache was not written into the redirected INDEX path: the code "
            "reached around the module's own cache and would have written the repo's index")
    for p, b in before:
        now = p.read_bytes() if p.is_file() else None
        assert now == b, str(p) + " changed on disk"


def main():
    tests = [test_task_records_read_the_task_files,
             test_the_search_is_semantic_not_a_keyword_grep,
             test_a_search_writes_nothing_outside_its_temp_dir]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print("FAIL " + t.__name__ + ": " + type(e).__name__ + ": " + str(e))
        else:
            print("ok   " + t.__name__)
    print(str(len(tests) - failed) + "/" + str(len(tests)) + " passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
