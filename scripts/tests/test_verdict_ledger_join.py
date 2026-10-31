"""Fail-first oracle for t1311: refuse to mint a deferral the reviewer already graded.

Minting the SAME normalised quote+context twice must refuse the second mint
(no new finding file, prior_seen stamped on the first, last_seen advanced,
first_seen unchanged), while a DISTINCT quote must still mint. Red on main
because session_review.mint_finding does not exist yet (no verdict-ledger
lookup/refusal in the mint path).

Run:  python3 scripts/tests/test_verdict_ledger_join.py   (no pytest; __main__ runner)
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # scripts/tests/ -> scripts/ -> repo root
sys.path.insert(0, str(REPO / "scripts"))

import session_review as sr                        # module exists; mint_finding is new (this scope)

QUOTE = "I'll update you once the handover is ready for review."
CTX = "turn 49 of 54"
T1 = "2026-10-16T00:00:00Z"
T2 = "2026-10-16T01:00:00Z"


def _mk(quote, context, when, fdir, lpath):
    return sr.mint_finding(quote, context=context, verdict="deferred",
                           when=when, findings_dir=fdir, ledger_path=lpath)


def test_second_mint_refused_first_recognised():
    with tempfile.TemporaryDirectory() as td:
        fdir, lpath = Path(td) / "findings", Path(td) / "verdict-ledger.jsonl"
        fdir.mkdir()
        first = _mk(QUOTE, CTX, T1, fdir, lpath)
        assert first["minted"] is True, "first mint must mint"
        assert len(list(fdir.glob("*.json"))) == 1, "first mint must write one finding"

        second = _mk(QUOTE, CTX, T2, fdir, lpath)
        assert second["minted"] is False, "second mint of the same quote must be refused"
        assert len(list(fdir.glob("*.json"))) == 1, "refusal must not add a finding file"

        finding = next(iter(fdir.glob("*.json")))
        rec = json.loads(finding.read_text(encoding="utf-8"))
        assert rec.get("prior_seen") == T1, "prior_seen must be the first mint's stamp"

        rows = [json.loads(l) for l in lpath.read_text(encoding="utf-8").splitlines() if l.strip()]
        row = [r for r in rows if r.get("quote") == QUOTE][-1]
        assert row["last_seen"] == T2, "last_seen must advance on the second mint"
        assert row["first_seen"] == T1, "first_seen must not change"


def test_distinct_quote_still_mints():
    # Load-bearing negative: a blanket 'refuse everything' build fails this.
    with tempfile.TemporaryDirectory() as td:
        fdir, lpath = Path(td) / "findings", Path(td) / "verdict-ledger.jsonl"
        fdir.mkdir()
        a = _mk("Promise A about the handover.", "ctx A", T1, fdir, lpath)
        assert a["minted"] is True
        b = _mk("Promise B about the handover.", "ctx A", T2, fdir, lpath)
        assert b["minted"] is True, "a different quote must still mint (not blanket-refused)"
        assert len(list(fdir.glob("*.json"))) == 2, "two distinct quotes -> two finding files"


if __name__ == "__main__":
    import traceback
    failed = False
    for fn in (test_second_mint_refused_first_recognised, test_distinct_quote_still_mints):
        try:
            fn()
            print("ok:", fn.__name__)
        except Exception as e:
            failed = True
            print("FAIL:", fn.__name__, "-", type(e).__name__, e)
            traceback.print_exc()
    if failed:
        sys.exit(1)
    print("ALL PASS")
