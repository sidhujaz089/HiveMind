#!/usr/bin/env python3
"""t1022: an endpoint string used as a SEARCH PATTERN is not a model call.

WHY: scripts/library_checks.py treated any file containing the literal "/api/chat" as a
model caller, so scripts/check_working_set.py:36 -- ENGINE_ENDPOINTS = ("/api/chat",
"/api/generate", "/api/embeddings"), a tuple of substrings a census scanner greps OTHER
files' source for -- was refused as model-call-outside-contract and model-run-no-stop-reason.
A scanner cannot write that constant and pass the commit gate: the string is not the signal.

Run: python3 scripts/tests/test_t1022_endpoint_literal_is_not_a_call.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import library_checks as LC

CHECKED = ("model-call-outside-contract", "model-run-no-stop-reason")

# A census scanner: the endpoint literals are SEARCH TARGETS. No HTTP anywhere in the file.
SCANNER = '''"""Counts direct model calls across the tree (t301 census)."""
from pathlib import Path

ENGINE_ENDPOINTS = ("/api/chat", "/api/generate", "/api/embeddings")

def scan_direct_calls(root):
    hits = []
    for path in sorted(Path(root).rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for ep in ENGINE_ENDPOINTS:
                if ep in line:
                    hits.append((str(path), n, ep))
    return hits
'''

# A real caller: it builds the request body and sends it itself, bypassing settings.request,
# and it never reads done_reason. Two distinct models, one distinct URL: a check that fires on
# a repeated placeholder string proves nothing.
CALLER = '''"""Sends a chat request to Ollama directly -- the defect the check exists for."""
import json
import urllib.request

CHAT_URL = "http://127.0.0.1:11434/api/chat"

def ask(messages, model="qwen3.8:27b"):
    body = json.dumps({"model": model, "messages": messages, "stream": False,
                       "options": {"temperature": 0.1}}).encode()
    req = urllib.request.Request(CHAT_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=600).read()
'''

# A caller that goes through the contract: the check must stay silent about it.
COMPLIANT = '''"""Sends a chat request through the contract."""
import settings

def ask(messages, model="qwen3.8:27b"):
    opts = settings.options(model=model, temperature=0.1)
    return settings.request("/api/chat", {"model": model, "messages": messages,
                                         "options": opts, "stream": False})
'''

# A caller that reads the stop reason (t238): no model-run-no-stop-reason finding.
STOP_READ = '''"""Sends a chat request and reads the stop reason (t238)."""
import json
import urllib.request

CHAT_URL = "http://127.0.0.1:11434/api/chat"

def ask(messages, model="qwen3.8:27b"):
    body = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(CHAT_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    reply = json.loads(urllib.request.urlopen(req, timeout=600).read())
    if reply.get("done_reason") != "stop":
        raise RuntimeError("truncated reply: %s" % reply.get("done_reason"))
    return reply
'''


def _findings(text, rel="scripts/census_probe.py"):
    """BLOCK findings for the two endpoint checks -- via the public API the gates use."""
    return [(cid, line, code) for cid, _sev, line, code
            in LC.run(rel, text, severity="block") if cid in CHECKED]


def test_endpoint_tuple_used_as_a_search_pattern_is_not_flagged():
    """The t1022 defect: a scanner's search strings are not a model call."""
    found = _findings(SCANNER)
    assert found == [], "scanner fixture flagged as a model call: %s" % (found,)


def test_a_real_call_site_is_still_flagged_at_its_endpoint_line():
    found = [f for f in _findings(CALLER) if f[0] == "model-call-outside-contract"]
    assert found, "a hand-built request is no longer reported: check over-narrowed"
    want = next(i for i, l in enumerate(CALLER.splitlines(), 1) if "/api/chat" in l)
    got = [f[1] for f in found]
    assert want in got, "endpoint literal line %s not reported; got %s" % (want, got)
    assert any("CHAT_URL" in f[2] for f in found), \
        "finding does not name the call line: %s" % (found,)


def test_a_caller_that_sends_a_body_still_gets_the_stop_reason_check():
    found = [f for f in _findings(CALLER) if f[0] == "model-run-no-stop-reason"]
    assert found, "caller with no done_reason handling not reported: check over-narrowed"
    assert any("/api/chat" in f[2] for f in found), \
        "finding does not name the endpoint line: %s" % (found,)


def test_a_caller_that_reads_the_stop_reason_is_clear():
    found = [f for f in _findings(STOP_READ) if f[0] == "model-run-no-stop-reason"]
    assert found == [], "stop reason is read, and it is still reported: %s" % (found,)


def test_a_caller_using_the_contract_is_clear():
    found = [f for f in _findings(COMPLIANT) if f[0] == "model-call-outside-contract"]
    assert found == [], "a settings.request caller was reported: %s" % (found,)


def test_the_live_file_in_the_t1022_evidence_is_clean():
    """Real artefact, not a synthetic fixture: scripts/check_working_set.py is the file
    t1022's gate finding named. It must report no endpoint findings."""
    text = (ROOT / "scripts" / "check_working_set.py").read_text(encoding="utf-8")
    found = _findings(text, "scripts/check_working_set.py")
    assert found == [], "the live working-set file was flagged: %s" % (found,)


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("ok   %s" % name)
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
    print("%d/%d passed" % (len(tests) - failed, len(tests)))
    sys.exit(1 if failed else 0)
