#!/usr/bin/env python3
"""t688: truncation_review.report() must render each finding's check id.

Findings carry the key 'check' (the rubric asks the model for it -- prompt_for,
scripts/truncation_review.py:72 -- and the mechanical off_flow finding sets it -- :184).
The report line rendered f.get('kind'), which no current finding source sets, so the
report printed '[None, ...]' for every finding (workspace/truncation-review.md).

Hermetic: report() is driven by monkeypatched rows()/targets()/abandoned() and a temp
REPORT path, so no live state, no model call, no subprocess.
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import truncation_review as tr  # noqa: E402


def _render(findings):
    """Drive tr.report() over a single synthetic ledger row; return the markdown."""
    tmp = Path(tempfile.mkdtemp(prefix="t688_"))
    report = tmp / "truncation-review.md"
    tr.REPO = tmp  # so REPORT.relative_to(REPO) inside report() cannot raise
    tr.REPORT = report
    tr.rows = lambda: [{"path": "scripts/example.py", "status": "ok", "findings": findings}]
    tr.targets = lambda: ["scripts/example.py"]
    tr.abandoned = lambda: []
    tr.report()
    return report.read_text(encoding="utf-8")


def test_check_finding_renders_its_id():
    f = {"check": "truncation", "line": 42, "silent": True, "severity": "high",
         "why": "the reason", "code": "x = 1", "verified": True}
    md = _render([f])
    assert "[truncation, silent]" in md, "check id 'truncation' must render, got:\n" + md
    high = md.split("## high")[1].split("## ")[0]
    assert "None" not in high, "the finding's check id rendered as None:\n" + high


def test_off_flow_finding_renders_its_id():
    f = {"check": "off-flow", "line": 0, "silent": False, "severity": "high",
         "why": "not on the Flow chart", "code": "", "verified": True}
    md = _render([f])
    assert "[off-flow, declared]" in md, "check id 'off-flow' must render, got:\n" + md


def test_kind_fallback_still_renders():
    # A historical ledger row carries 'kind' (workspace/truncation-review.md shows
    # [model-input, ...] rows that only f.get('kind') could have produced). The fix
    # must keep those rendering, not regress them to None.
    f = {"kind": "model-input", "line": 8, "silent": True, "severity": "high",
         "why": "the reason", "code": "y = 2", "verified": True}
    md = _render([f])
    assert "[model-input, silent]" in md, "historical 'kind' finding must still render:\n" + md


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    if failed:
        print(f"{failed} test(s) failed")
        sys.exit(1)
    print("all tests passed")
    sys.exit(0)
