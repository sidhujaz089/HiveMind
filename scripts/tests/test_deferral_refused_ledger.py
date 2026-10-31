#!/usr/bin/env python3
"""t1408 checklist item (2): a deferral the contract refuses must leave a durable record.

THE DEFECT (workspace/rca/c7e73ff6c1e4.md, finding c7e73ff6c1e4; the promise "Then I'll merge
t458 and test it properly."): scripts/session_review.py mints a deferral finding, then calls
record_deferral(...) at :1262 under the t607 deferral contract. When that contract REFUSES the
row, the handler at :1266-1268 prints one NOTE line to stderr and nothing else. A printed note is
not a record: workspace/deferrals.jsonl has 0 rows for key c7e73ff6c1e4 while
workspace/session-reviews/ledger.jsonl has its mint row (:1696), so the refusal reached the durable
ledger silently and every later reader inherits the deferral as unrecorded.

THE CONTRACT, asserted in two halves so neither can be faked:
  1. STATIC -- the refusal handler's body calls the module's OWN durable writer, _log
     (scripts/session_review.py:65, appending to the module global LEDGER, :41), with a dict
     carrying event / key / quote / error and an `event` string that is NOT one the ledger
     already reserves. Proves the wiring, not a wish.
  2. BEHAVIOUR -- _log appends one parseable JSON row per call to session_review.LEDGER, in a temp
     dir, and writes nothing to the live ledger. Proves "calls _log" means "a row lands on disk".
Together: a refused deferral cannot reach the durable ledger silently.

No model, no server, no live state: the module's LEDGER global is redirected into tempfile.mkdtemp()
for the whole write test and restored in `finally`, and the real ledger's size is asserted unchanged.
"""
import ast
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "scripts" / "session_review.py"
MARKER = "deferral row not written"
REQUIRED_KEYS = {"event", "key", "quote", "error"}
RESERVED_EVENTS = {"finding", "reviewed", "rca", "covered", "for-jas"}


def _functions():
    tree = ast.parse(SRC.read_text(encoding="utf-8"), filename=str(SRC))
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _has_marker(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and MARKER in sub.value:
            return True
    return False


def _refusal_handlers():
    """Every ast.ExceptHandler whose body carries the NOTE: deferral row not written text."""
    out = []
    for fn in _functions():
        if not _has_marker(fn):
            continue
        for h in ast.walk(fn):
            if isinstance(h, ast.ExceptHandler) and _has_marker(h):
                out.append((fn.name, h))
    return out


def _log_calls(handler):
    """_log(<dict literal>) calls inside the handler's own body (not in a nested function)."""
    hits = []
    for n in ast.walk(handler):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_log" and n.args and isinstance(n.args[0], ast.Dict)):
            hits.append(n)
    return hits


def _dict_keys(dct):
    return {k.value for k in dct.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def test_refusal_handler_records_the_refusal_in_the_ledger():
    handlers = _refusal_handlers()
    assert handlers, (
        "no `except` handler carrying %r in scripts/session_review.py: the deferral-write "
        "contract this oracle pins has moved. Report the new site (grep -n %r "
        "scripts/session_review.py); do NOT reword the message to make this pass." % (MARKER, MARKER))
    for name, h in handlers:
        calls = _log_calls(h)
        assert calls, (
            "the deferral-refusal handler inside %s() prints to stderr but records nothing: a "
            "deferral the t607 contract refuses leaves no durable row. Add a _log({...}) call as "
            "the FIRST statement of that handler." % name)
        keys = _dict_keys(calls[0].args[0])
        missing = REQUIRED_KEYS - keys
        assert not missing, (
            "the refusal row is missing %s; a row that names no key and no reason is a row nobody "
            "can act on. Required keys: %s, found: %s"
            % (sorted(missing), sorted(REQUIRED_KEYS), sorted(keys)))
        ev = None
        for k, v in zip(calls[0].args[0].keys, calls[0].args[0].values):
            if isinstance(k, ast.Constant) and k.value == "event" and isinstance(v, ast.Constant):
                ev = v.value
        assert isinstance(ev, str) and ev.strip(), (
            "the refusal row's `event` must be a non-empty string literal, not a computed value: "
            "a ledger row whose event type is only known at runtime cannot be counted by anything "
            "that reads the file. Found: %r" % (ev,))
        assert ev not in RESERVED_EVENTS, (
            "`event: %r` is already reserved by the ledger's own vocabulary (status() counts "
            "reviewed/rca at scripts/session_review.py:1592-1603; _log rows with event 'finding' "
            "are the mint itself). Name the refusal its own event, e.g. 'deferral-refused'." % (ev,))


def test_refusal_handler_keeps_the_stderr_note():
    for name, h in _refusal_handlers():
        printed = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"
            and any(getattr(kw, "arg", None) == "file" for kw in n.keywords)
            for n in ast.walk(h))
        assert printed, (
            "the refusal handler in %s() no longer prints to a file= stream: this fix ADDS a "
            "durable row, it does not replace the operator-visible NOTE (swallowed-exception "
            "defence -- record it in the row AND say it out loud)." % name)


def test_log_appends_json_rows_to_the_ledger():
    sys.path.insert(0, str(ROOT / "scripts"))
    import session_review
    tmp = Path(tempfile.mkdtemp(prefix="sr-ledger-"))
    row = {"event": "deferral-refused", "key": "c7e73ff6c1e4", "kind": "deferral",
           "quote": "Then I'll merge t458 and test it properly.",
           "error": "ValueError: t607 contract refused: no resolution evidence recorded"}
    live = Path(session_review.LEDGER)
    live_size = live.stat().st_size if live.is_file() else -1
    old = session_review.LEDGER
    session_review.LEDGER = tmp / "ledger.jsonl"
    try:
        session_review._log(row)
        session_review._log(row)
    finally:
        session_review.LEDGER = old
    out = tmp / "ledger.jsonl"
    assert out.is_file(), "_log wrote nothing to the module's LEDGER: the handler's _log call " \
        "would record to a path that is never opened"
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2, "expected 2 appended rows, got %d: %r" % (len(lines), lines)
    for ln in lines:
        assert json.loads(ln) == row, "row is not the dict passed in: %r" % (json.loads(ln),)
    assert json.loads(lines[0])["key"] == "c7e73ff6c1e4", "the refusal row must name the finding key"
    assert json.loads(lines[0])["event"] == "deferral-refused"
    if live_size >= 0:
        assert live.stat().st_size == live_size, (
            "the test wrote the LIVE ledger (workspace/session-reviews/ledger.jsonl grew). "
            "_log must honour session_review.LEDGER as a module global read at call time, so the "
            "redirect above is honoured; %d -> %d bytes" % (live_size, live.stat().st_size))


TESTS = [test_refusal_handler_records_the_refusal_in_the_ledger,
         test_refusal_handler_keeps_the_stderr_note,
         test_log_appends_json_rows_to_the_ledger]

if __name__ == "__main__":
    failed = []
    for t in TESTS:
        try:
            t()
            print("ok   %s" % t.__name__)
        except Exception as e:
            failed.append(t.__name__)
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
    print("%d of %d test(s) failed%s" % (len(failed), len(TESTS),
          "" if not failed else ": " + ", ".join(failed)))
    sys.exit(1 if failed else 0)
