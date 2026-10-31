#!/usr/bin/env python3
"""t4642: a review reply that is complete JSON EXCEPT for raw double quotes inside a
string value must be recorded as a definite outcome, not `unreadable`.

The evidence is the real ledger (workspace/truncation-review.jsonl): 49 rows for
scripts/intake.py were recorded `unreadable`, and the reply text shows the property -- Swift
wrote code into the `why` string verbatim, quotes and all:

    "why": "The code explicitly drops all files ... (`.get("files")[:1]`) ..."

json.loads closes the string at `.get(` and reads `files` as a bare token -> jsonx.one reports
`invalid-json` (scripts/jsonx.py: no object parsed). truncation_review.py's existing recoveries
miss it: the escaped-string one is gated on '\\"' in raw (scripts/truncation_review.py:118) and
jsonx.repair_brackets repairs brackets, not quotes. Verified 2026-10-28: replaying all 49
unreadable intake.py replies through today's read_reply, 0 of 49 parse (29 invalid-json,
20 no-match).

Read-only: this test reads the ledger, it writes nothing outside a temp dir.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import truncation_review as T  # noqa: E402

LEDGER = ROOT / "workspace" / "truncation-review.jsonl"
MARKER = '.get("files")[:1]'


def _real_reply():
    """The actual reply text recorded for scripts/intake.py, taken from the ledger.

    Built from the real artefact on purpose: a synthetic fixture encodes what I imagine Swift's
    replies look like; the gap between that and the bytes on disk is where a vacuous pass hides.
    """
    if not LEDGER.exists():
        return None
    for line in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if (r.get("path") == "scripts/intake.py"
                and str(r.get("status", "")).startswith("unreadable")
                and MARKER in (r.get("reply") or "")):
            return r["reply"]
    return None


def test_real_intake_reply_becomes_readable():
    raw = _real_reply()
    if raw is None:
        print("SKIP: no unreadable scripts/intake.py reply carrying the real code-quote text "
              "is present in the ledger; running the synthetic cases only")
        return
    r = T.read_reply(raw)
    assert r["status"] == "ok", f"reply still unreadable: {r['status']}"
    found = r["obj"].get("findings")
    assert isinstance(found, list), f"findings is not a list: {found!r}"
    assert len(found) == 2, f"want the 2 findings in the recorded reply, got {found!r}"
    assert [f["check"] for f in found] == ["absence-as-success", "truncation"], found
    assert found[0]["line"] == 181, found[0]
    assert found[0]["severity"] == "high", found[0]
    assert MARKER in found[1]["why"], f"the repair dropped the quoted code: {found[1]['why']!r}"


SYNTH = ('{\n  "findings": [\n    {\n      "check": "truncation",\n      "line": 273,\n'
         '      "silent": true,\n      "severity": "medium",\n'
         '      "why": "The slice `.get("files")[:1]` discards all but the first entry of '
         '`paths`, and the row in intake.jsonl looks complete."\n    }\n  ]\n}\n')


def test_synthetic_unescaped_quote_reply_is_readable():
    r = T.read_reply(SYNTH)
    assert r["status"] == "ok", f"unescaped-quote reply not repaired: {r['status']}"
    found = r["obj"]["findings"]
    assert len(found) == 1, found
    assert found[0]["check"] == "truncation", found[0]
    assert found[0]["line"] == 273, found[0]
    assert found[0]["severity"] == "medium", found[0]
    assert MARKER in found[0]["why"], found[0]["why"]
    assert found[0]["silent"] is True, found[0]


def test_a_declared_empty_review_still_reads():
    r = T.read_reply('{"findings": []}')
    assert r["status"] == "ok", r
    assert r["obj"]["findings"] == [], r


def test_repair_cannot_invent_a_verdict():
    """NEGATIVE assertion: a reply with no findings-bearing object is NOT turned into ok.

    A repair broad enough to satisfy 'parse the reply' by any means passes these: if it can
    conjure a findings list out of prose, every unreadable reply becomes 'ok' and the WARN goes
    quiet on a lie.
    """
    for raw in ['{"note": "prose mentioning `.get("files")[:1]` and then stopping"}',
                'the model answered in prose, with no braces at all: a["k"] = 1',
                '{"findings": "not a list"}',
                '{"findings": {"check": "truncation", "line": 3}}']:
        r = T.read_reply(raw)
        assert r["status"] != "ok", f"fabricated a verdict from {raw[:48]!r}: {r}"


def test_a_wellformed_reply_is_untouched():
    """The repair must not alter replies the reader already reads (regression guard)."""
    raw = ('{"findings": [{"check": "truncation", "line": 51, "silent": true, '
           '"severity": "high", "why": "FILES_KEPT caps the list written to the ledger"}]}')
    r = T.read_reply(raw)
    assert r["status"] == "ok", r
    assert r["obj"]["findings"][0]["line"] == 51, r
    assert r["obj"]["findings"][0]["why"] == "FILES_KEPT caps the list written to the ledger", r


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"{'OK' if not fails else str(fails) + ' failure(s)'}")
    sys.exit(1 if fails else 0)
