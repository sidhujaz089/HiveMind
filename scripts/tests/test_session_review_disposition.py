"""Fail-first oracle for t676: a minted finding record must carry a valid `disposition`.

Defect (RCA 80095ffd2448, Rule-5 Rung-1): the finding contract has no `disposition`
field, so a quote the session already resolved (or that is refuted) is forced into
kind=unresolved and queued for a full 24000-token RCA. After the fix, every finding
that _keep writes must carry a `disposition` in the contract enum, and a disposition the
extract model emits must be respected downstream (not dropped or overwritten).

Run as:  python3 scripts/tests/test_session_review_disposition.py   (pytest is NOT installed)
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import session_review  # noqa: E402  (module-level is imports/constants/defs; import is safe)

ALLOWED = {"unresolved", "resolved-in-session", "deferred", "for-jas", "refuted"}
REQ = {"harness": "claude", "id": "oracle-sid"}


def _sandbox(tmp):
    """Point the writer at temp paths and neutralise the one live dependency on the covered path."""
    session_review.FINDINGS = Path(tmp) / "findings"
    session_review.LEDGER = Path(tmp) / "ledger.jsonl"
    # _keep consults _archive_cover(quote) when nothing covers the quote; that imports
    # taskstore (live). Return "" so the finding falls through to the write path under test.
    session_review._archive_cover = lambda quote: ""


def _mint(row):
    with tempfile.TemporaryDirectory() as d:
        _sandbox(d)
        req_file = Path(d) / "req.json"
        n, covered = session_review._keep(req_file, REQ, [row])
        assert n == 1 and covered == 0, f"expected exactly one finding minted, got new={n} covered={covered}"
        files = list(session_review.FINDINGS.glob("*.json"))
        assert len(files) == 1, f"expected one finding file, got {[f.name for f in files]}"
        return json.loads(files[0].read_text(encoding="utf-8"))


def test_minted_finding_carries_a_valid_disposition():
    """RED on main: an unlabeled row must STILL come out with a contract disposition.

    Today _keep writes {**r, ...}; an unlabeled row has no `disposition` key at all, so the
    finding is minted with no bucket -- the exact t676 gap. After the fix the record carries one."""
    rec = _mint({"kind": "unresolved",
                 "quote": "the ledger counter never increments and nobody noticed",
                 "why_missed": "no task was filed for it"})
    assert "disposition" in rec, "finding record is missing the disposition field (the t676 gap)"
    assert rec["disposition"] in ALLOWED, f"disposition {rec['disposition']!r} not in the contract enum {sorted(ALLOWED)}"


def test_model_labeled_disposition_is_respected():
    """A disposition the extract model emits must survive into the record, not be dropped/overwritten."""
    rec = _mint({"kind": "unresolved",
                 "quote": "already fixed -- the two lines compare in both directions, no action needed",
                 "why_missed": "not an unresolved problem",
                 "disposition": "resolved-in-session"})
    assert rec.get("disposition") == "resolved-in-session", f"labeled disposition lost or changed: {rec.get('disposition')!r}"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
            else:
                print(f"PASS {name}")
    sys.exit(1 if failures else 0)
