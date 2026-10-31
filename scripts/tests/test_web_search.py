#!/usr/bin/env python3
"""web_search is a placeholder until t488: listed, OFF for every role, and it SAYS it is not set up.

Jas, 2026-09-30: "just add web search as a placeholder thing, and once I figure out my side, we can
make it fully functional." The failure this guards against is a not-set-up search that returns an
empty list, which a model reads as "the web has nothing on this".
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]
import web_search as ws  # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f" (expected {want!r})"))


def raises(fn, exc):
    try:
        fn()
    except exc as e:
        return str(e)
    return None


why = raises(lambda: ws.search("anything", cfg={"provider": None}), ws.NotSetUp)
report("no provider -> NotSetUp, not an empty result", bool(why), True)
report("  and it names t488 and says nothing was searched", ("t488" in why, "Nothing was searched" in why),
       (True, True))
# 2026-10-05 (t488): the provider is SearXNG on loopback. A shipped config naming any other host would
# send the models' queries somewhere nobody chose.
report("the shipped config names SearXNG on loopback", (ws.config().get("provider"), ws.config().get("url")),
       ("searxng", "http://127.0.0.1:8888"))
# CAPTCHA / rate-limit visibility (Jas, 2026-10-05): every REAL search is logged with which engines did not
# answer; a fake `get` (every test here) must not touch the live ledger.
import tempfile
_tmp = Path(tempfile.mkdtemp()) / "web-search.jsonl"
_live = ws.LOG.stat().st_size if ws.LOG.exists() else 0
ws.search("q", cfg={"provider": "searxng", "url": "http://x"},
          get=lambda u: {"results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]})
report("a fake get does not write the live ledger", (ws.LOG.stat().st_size if ws.LOG.exists() else 0), _live)
ws.LOG = _tmp
ws._log_call("searxng", 3, {"results": [{"engines": ["google cse"]}],
                            "unresponsive_engines": [["duckduckgo", "CAPTCHA"], ["brave", "too many requests"]]})
ws._log_call("searxng", 0, {"results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]})
n, by, none_up = ws.stats(_tmp)
report("stats counts searches, CAPTCHAs per engine, and searches with nothing answering",
       (n, by["duckduckgo"], by["brave"], none_up), (2, {"CAPTCHA": 2}, {"too many requests": 1}, 1))
import json
report("the query text is never recorded", {"query", "q"} & set(json.loads(_tmp.read_text().splitlines()[0])), set())
report("an unknown provider is refused by name",
       "no adapter" in (raises(lambda: ws.search("q", cfg={"provider": "bingo"}), ws.NotSetUp) or ""), True)
report("an empty query is refused", bool(raises(lambda: ws.search("  ", cfg={"provider": "searxng"}), ValueError)),
       True)
seen = []


def fake(url):
    seen.append(url)
    return {"results": [{"title": f"T{i}", "url": f"https://e.example/{i}", "content": "s"} for i in range(12)]}


res, total = ws.search("red team", cfg={"provider": "searxng", "url": "http://127.0.0.1:8888/"}, get=fake)
report("searxng: the JSON API is asked, with the query encoded",
       seen[0], "http://127.0.0.1:8888/search?q=red+team&format=json")
report("  results capped at MAX_RESULTS, total kept", (len(res), total), (ws.MAX_RESULTS, 12))
report("  and the cap is SAID when rendered", "the rest not shown" in ws.render(res, total), True)

import role_caps  # noqa: E402
report("web_search is listed as a tool", "web_search" in [n for n, _ in role_caps.TOOLS], True)
report("web_search is OFF by default for every role (switching it on is Jas's, on the Models tab; the LIVE store is not pinned here -- t657)", [r for r in ("manager", "auditor", "coder", "senior", "x") if role_caps._default(r).get("web_search")], [])
src = (ROOT / "projects" / "local-inference" / "console.py").read_text()
report("the console runs it and logs every call",
       ('"web_search": tool_web_search' in src, '_action({"tool": "web_search"' in src), (True, True))
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
