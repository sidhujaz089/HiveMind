#!/usr/bin/env python3
"""Fail-first oracle for scripts/transcript_audit.py (t414).

The mechanical evidence pass: for each standing process in a catalog, surface
the transcript turns that reference it, and flag processes with no findable cause.
It must not make the keep/retire judgment -- that is a model pass, and retire
decisions go to Jas (AGENTS.md rule 9, per t414 note:14).

Temp dirs and fakes only: no live index, no live workspace writes.
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

MODULE = Path(__file__).resolve().parent.parent / "transcript_audit.py"


def _load_audit():
    spec = importlib.util.spec_from_file_location("transcript_audit", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FAKE_INDEX = [
    {"session": "sess-aaaa", "origin": "hivemind", "n": 12, "role": "user",
     "at": "2026-08-24T10:00:00Z", "workdir": "-home-user",
     "text": "Jas: retire check_fake_gate, Drive is gone and the pipeline was severed by hand."},
    {"session": "sess-bbbb", "origin": "hivemind", "n": 31, "role": "assistant",
     "at": "2026-08-25T09:30:00Z", "workdir": "-home-user",
     "text": "Removed CHECK_FAKE_GATE from the gate list; the cause was the Drive handoff."},
    {"session": "sess-cccc", "origin": "hivemind", "n": 4, "role": "user",
     "at": "2026-09-01T08:00:00Z", "workdir": "-home-user",
     "text": "Unrelated turn about the weather and lunch."},
]


def main():
    audit = _load_audit()

    ev = audit.find_evidence(FAKE_INDEX, "check_fake_gate")
    assert len(ev) == 2, f"expected 2 evidence turns for check_fake_gate (case-insensitive), got {len(ev)}: {ev}"
    assert {r["session"] for r in ev} == {"sess-aaaa", "sess-bbbb"}, f"wrong rows matched: {ev}"
    for r in ev:
        for field in ("session", "n", "at", "role", "text"):
            assert field in r, f"evidence row missing {field}: {r}"

    assert audit.find_evidence(FAKE_INDEX, "check_never_mentioned") == [], \
        "a name absent from the index must match nothing"

    catalog = [
        {"name": "check_fake_gate", "where": "scripts/checks.py", "author_model": "gemma4:26b"},
        {"name": "check_never_mentioned", "where": "scripts/checks.py", "author_model": "qwen3.8:27b"},
    ]
    report = audit.build_report(FAKE_INDEX, catalog)
    assert len(report) == 2, f"one row per catalog process, in catalog order; got {len(report)}"
    assert [r["process"] for r in report] == ["check_fake_gate", "check_never_mentioned"]
    by_name = {r["process"]: r for r in report}

    found = by_name["check_fake_gate"]
    assert found["evidence_found"] is True
    assert len(found["evidence"]) == 2
    assert found["verdict"] not in ("keep", "retire"), \
        "the mechanical pass must not judge keep/retire; that is a model pass, and retire goes to Jas"

    missing = by_name["check_never_mentioned"]
    assert missing["evidence_found"] is False
    assert missing["verdict"] == "no-cause-found", \
        "a process whose cause nobody can state is the finding (t123:30-31)"
    assert missing["author_model"] == "qwen3.8:27b", \
        "author_model must flow through so the same-weights rule can be enforced"

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "report.jsonl"
        audit.write_report(report, out)
        lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 2, f"report must be JSONL with one row per process, got {len(lines)}"
        assert all("process" in l and "verdict" in l for l in lines)

    print("test_transcript_audit OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
