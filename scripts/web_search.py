#!/usr/bin/env python3
"""Search the web for the models -- the web_search tool (SearXNG, since 2026-10-05; t488).

    python3 scripts/web_search.py "<query>"      # what a model would get back

Jas, 2026-09-30: "just add web search as a placeholder thing, and once I figure out my side, we can
make it fully functional." Which provider is his choice (t488: A self-hosted SearXNG, B a paid API
with a key, C not now), so the tool exists, is listed on every role's Tools page, is OFF for every
role, and until models/WEB-SEARCH.json names a provider every call answers NOT SET UP -- in words a
model can act on (use web_fetch on an address you already know), never an empty result that reads
like "the web had nothing".

LIVE since 2026-10-05 (t488, Jas: "set up searXNG"): WEB-SEARCH.json names SearXNG at 127.0.0.1:8888,
started by scripts/searxng_up.sh, run end to end against the real instance. The tool is still OFF for
every role until Jas switches it on for one on the Models tab. Measured the same day: only the `google cse`
engine answered; brave was "Suspended: too many requests" and duckduckgo hit a CAPTCHA, so results
come from one engine and may thin out if that one is throttled.

HOW IT WAS MADE LIVE -- one edit to models/WEB-SEARCH.json once t488 was answered:
  * SearXNG (option A): {"provider": "searxng", "url": "http://127.0.0.1:8888"} -- its JSON API
    (`/search?format=json`, which must be enabled in the instance's settings.yml). The JSON format is enabled in
    projects/local-inference/searxng/settings.yml.
  * An API with a key (option B): add an adapter below named for it; the key stays in ~/.secrets.
The configured URL is OURS, not the model's, so it may be local; the result links are only text --
a model that wants a page still goes through web_fetch, which refuses every non-public address.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "projects" / "local-inference" / "models" / "WEB-SEARCH.json"
MAX_RESULTS = 8
TIMEOUT_S = 20
NOT_SET_UP = ("web_search is NOT SET UP yet: no search provider has been chosen (task t488, Jas's "
              "decision). Nothing was searched, so this is not 'no results'. If you already know the "
              "address of the page you need, use web_fetch on it; otherwise say that you could not search.")


class NotSetUp(Exception):
    pass


def config(path=None):
    p = Path(path or CONFIG)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"provider": None}


def _searxng(cfg, query, get):
    url = cfg.get("url", "").rstrip("/")
    if not url:
        raise NotSetUp("WEB-SEARCH.json names searxng but gives no url")
    data = get(f"{url}/search?" + urllib.parse.urlencode({"q": query, "format": "json"}))
    rows = data.get("results") or []
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
            for r in rows[:MAX_RESULTS]], len(rows)


PROVIDERS = {"searxng": _searxng}


def _get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}),
                                timeout=TIMEOUT_S) as f:
        return json.load(f)


LOG = REPO / "workspace" / "run-logs" / "web-search.jsonl"


def _log_call(provider, total, data):
    """One row per real search: how many results, and which engines did not answer and why (a CAPTCHA, a
    rate limit). Jas, 2026-10-05: "how often the local models get stopped by captchas". The QUERY is not
    recorded -- the models' searches can be private -- only what the engines did."""
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "provider": provider, "results": total,
           "unresponsive": [list(x) for x in (data.get("unresponsive_engines") or [])],
           "engines": sorted({e for r in (data.get("results") or []) for e in (r.get("engines") or [])})}
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        print(f"  (web search not logged: {e})", file=sys.stderr)


def search(query, cfg=None, get=_get_json):
    """[{title, url, snippet}], total -- or NotSetUp when no provider is configured."""
    cfg = config() if cfg is None else cfg
    query = (query or "").strip()
    if not query:
        raise ValueError("web_search needs a query")
    prov = cfg.get("provider")
    if not prov:
        raise NotSetUp(NOT_SET_UP)
    if prov not in PROVIDERS:
        raise NotSetUp(f"web_search: provider {prov!r} in WEB-SEARCH.json has no adapter in web_search.py")
    seen = {}

    def tapped(url):
        seen["data"] = get(url)
        return seen["data"]

    out = PROVIDERS[prov](cfg, query, tapped)
    if get is _get_json:                    # a fake `get` is a test: it must not write the live ledger
        _log_call(prov, out[1], seen.get("data") or {})
    return out


def render(results, total):
    if not results:
        return "0 results."
    lines = [f"{len(results)} of {total} result(s)" + (" (the rest not shown)" if total > len(results) else "")]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
    return "\n".join(lines)


def stats(path=None):
    """How often each engine failed, and why, over every logged search: the answer to "how often do the
    models get stopped by CAPTCHAs". Returns (searches, {engine: {reason: n}}, searches with no engine up)."""
    searches, by, none_up = 0, {}, 0
    p = Path(path or LOG)
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                print(f"  NOTE: unreadable row in {p.name}", file=sys.stderr)
                continue
            searches += 1
            none_up += not r.get("engines")
            for eng, why in r.get("unresponsive") or []:
                by.setdefault(eng, {}).setdefault(why, 0)
                by[eng][why] += 1
    return searches, by, none_up


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "--stats":
        n, by, none_up = stats()
        print(f"{n} logged search(es); {none_up} with no engine answering")
        for eng, whys in sorted(by.items()):
            print(f"  {eng}: " + ", ".join(f"{w} x{c}" for w, c in sorted(whys.items(), key=lambda x: -x[1])))
        return 0
    try:
        res, total = search(" ".join(argv))
    except NotSetUp as e:
        print(e)
        return 3
    print(render(res, total))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
