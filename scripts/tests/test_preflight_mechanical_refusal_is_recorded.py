#!/usr/bin/env python3
"""A preflight that refuses WITHOUT asking the model still records its objection.

RCA 2026-10-28 (workspace/rca/rca-no-verdict-preflight-holds-2026-10-28.md, RC-A). The dispatcher holds a
brief as "the reviewer is broken" when `runs - recorded_rows >= 2`, so a mechanical refusal that writes
nothing is indistinguishable from a crash. preflight_brief.py makes two refusals before any model is
asked: the MISSING FILES one records its objection (with a comment saying why), and the PRECONDITION one
printed its verdict and returned. Six briefs were held on that gap.

This drives the real main() with the precondition check made to fire, so what is proved is the CONTROL
FLOW -- does the refusal reach record_review -- not the presence of a line of code.
"""
import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def run(monkeypatch_recorder):
    import preflight_brief as P
    d = Path(tempfile.mkdtemp())
    # REAL PATHS, so the missing-files refusal (which already records) does not fire first and mask the
    # precondition path under test.
    brief = d / "brief-t.txt"
    brief.write_text("TASK: t999\n\nTARGET: scripts/tests/test_auto_merge_frontier.py\n\n"
                     "The suite scripts/tests/test_auto_merge_frontier.py is green.\n")
    real = (P.precondition_findings, P.record_review, P.OBJECTIONS)
    calls = []

    def fake_record(*a, **k):
        calls.append({"verdict": a[6] if len(a) > 6 else k.get("verdict"), "note": k.get("note", "")})

    P.precondition_findings = lambda *a, **k: ["PRECONDITION NOT MET: scripts/tests/test_x.py exits 1"]
    P.record_review = monkeypatch_recorder(fake_record)
    P.OBJECTIONS = d / "objections.jsonl"
    argv = sys.argv
    sys.argv = ["preflight_brief.py", "--brief", str(brief), "--files", "scripts/role_store.py",
                "--target", "scripts/tests/test_auto_merge_frontier.py"]
    try:
        with redirect_stdout(io.StringIO()):
            rc = P.main()
    finally:
        sys.argv = argv
        P.precondition_findings, P.record_review, P.OBJECTIONS = real
    return rc, calls


def main():
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    rc, calls = run(lambda f: f)
    check("the precondition refusal still exits 2 (it is a refusal, not a pass)", rc == 2, f"rc={rc}")
    check("  AND it records an objection before returning", len(calls) == 1, f"record_review calls: {calls}")
    if calls:
        check("  the row says NOT SATISFIABLE", calls[0]["verdict"] == "NOT SATISFIABLE", str(calls[0]))
        check("  and says the model was not asked",
              "without asking the model" in str(calls[0]["note"]), str(calls[0]))
        check("  and carries the finding itself, not just a count",
              "PRECONDITION NOT MET" in str(calls[0]["note"]), str(calls[0]))
    print("test_preflight_mechanical_refusal_is_recorded:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
