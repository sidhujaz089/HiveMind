#!/usr/bin/env python3
"""t951 -- the audit-lane registry is an ALLOWLIST: a model-judgement lane with no
registered falsifier is a finding, and so is the absence of the registry itself.

WHY (RCA 017614dd87ea): every lane whose output is a model judgement with no oracle
inherits the "silent high-severity truncation nobody triages" class. A blacklist of bad
audits needs a librarian, the mechanism this repo has watched fail three times. The fix is
the inversion: enumerate the APPROVED set (scripts/judgement_lanes.json, append-only,
human-merged, each entry carrying its falsifier) and treat anything outside it as a finding.

RED TODAY BECAUSE: scripts/check_workspace.py defines no `judgement_lane_findings` and no
`check_judgement_lanes` (grep -rn 'judgement_lanes' scripts/ --include=*.py returns nothing),
and scripts/judgement_lanes.json does not exist.

THE CONTRACT (build exactly these names -- do not invent others):

    judgement_lane_findings(repo) -> list[str]        # module-level
    Checker.check_judgement_lanes(self)               # records ONE result

  * registry = repo / "scripts" / "judgement_lanes.json"
  * lane population = every repo/"scripts"/**/*.py whose repo-relative POSIX path does NOT
    contain "/tests/" and whose source contains one of
    ("infer.chat", "settings.request", "/api/chat") in CODE, not prose -- blank docstrings
    and comments with harness_inventory.code_only (scripts/harness_inventory.py:68, a sibling
    module); if that import is unavailable fall back to raw source, never raise.
  * a MISSING registry file -> exactly one finding containing the string
    "scripts/judgement_lanes.json", the words "no registry" and "approves nothing".
    A missing allowlist must never report clean.
  * a registry that exists but is unreadable, is not valid JSON, or is not a JSON list ->
    EXACTLY ONE finding naming "judgement_lanes.json" and the word "unreadable".
  * an entry that is not a dict, or a dict with no non-empty "lane" string -> a finding
    naming its list index (as a digit) and the word "malformed".
  * approved = the set of non-empty string "script" values, whitespace-stripped with
    backslashes turned into "/". A lane file not in that set -> a finding containing its
    repo-relative POSIX path AND the marker that matched (so a person sees why).
  * an entry with a non-empty "script" whose file does not exist under repo -> a finding
    naming that script path and the words "no such file" (registry decay).
  * an entry whose "falsifier" is absent, not a string, or blank after strip -> a finding
    naming its "script" path and the word "falsifier".
  * a registry that EXISTS with an empty list is not the "no registry" finding.
  * NEVER truncate a finding ("never-truncate-a-record"): no "..." and no "[truncated" in any.
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("cw_under_test", ROOT / "scripts" / "check_workspace.py")
CW = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CW)

REGISTRY_REL = "scripts/judgement_lanes.json"
LANE_A = "scripts/auditor.py"        # a lane that calls a model through infer.chat
LANE_B = "scripts/collector.py"      # a lane that calls a model through settings.request
FALSIFIER_A = "signature_gate.py:line_truncates_a_record"
FALSIFIER_B = "scripts/tests/test_citation_audit.py"

CALL_A = 'import infer\n\n\ndef go(body):\n    return infer.chat(body, timeout=60)\n'
CALL_B = 'import settings\n\n\ndef ask(body):\n    return settings.request(body)\n'
PROSE = '"""This lane is described as calling infer.chat in its brief."""\n\nX = 1\n'


def _repo(entries, registry):
    """A throwaway tree in a temp dir: {rel path: source}, plus scripts/judgement_lanes.json
    holding `registry` when registry is not None. Nothing here touches the real repository."""
    root = Path(tempfile.mkdtemp(prefix="lanes-"))
    for rel, src in entries.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    if registry is not None:
        reg = root / REGISTRY_REL
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps(registry), encoding="utf-8")
    return root


def test_the_module_exposes_the_callable():
    assert hasattr(CW, "judgement_lane_findings"), (
        "check_workspace.py must define module-level judgement_lane_findings(repo) -> list[str]: "
        "the census of model-judgement lanes and the findings against the registry. Nothing in "
        "this suite runs without it.")
    out = CW.judgement_lane_findings(ROOT)
    assert isinstance(out, list), f"must return a list of strings, got {type(out).__name__}"
    assert all(isinstance(f, str) for f in out), out


def test_a_missing_registry_is_a_finding_that_names_the_path_and_fails_closed():
    if (ROOT / REGISTRY_REL).is_file():
        return  # the data half has landed; the structural tests below cover the semantics
    findings = CW.judgement_lane_findings(ROOT)
    text = "\n".join(findings)
    assert findings, (
        "with no scripts/judgement_lanes.json on disk the check must report something: a missing "
        "allowlist that reports clean is the fail-open shape this task exists to remove")
    assert REGISTRY_REL in text, findings
    assert "no registry" in text.lower(), findings
    assert "approves nothing" in text.lower(), findings
    for f in findings:
        assert "\u2026" not in f, f"finding is truncated: {f}"
        assert "[truncated" not in f.lower(), f"finding is truncated: {f}"


def test_a_model_judgement_lane_absent_from_the_registry_is_a_finding():
    root = _repo({LANE_A: CALL_A, LANE_B: CALL_B},
                 [{"lane": "collector", "script": LANE_B, "falsifier": FALSIFIER_B}])
    findings = CW.judgement_lane_findings(root)
    assert len(findings) == 1, findings
    assert LANE_A in findings[0], findings
    assert LANE_B not in findings[0], findings
    assert "infer.chat" in findings[0], findings


def test_every_lane_registered_with_a_falsifier_is_clean():
    root = _repo({LANE_A: CALL_A, LANE_B: CALL_B},
                 [{"lane": "auditor", "script": LANE_A, "falsifier": FALSIFIER_A},
                  {"lane": "collector", "script": LANE_B, "falsifier": FALSIFIER_B}])
    assert CW.judgement_lane_findings(root) == [], (
        "both lanes are registered with a falsifier, so a clean tree must report nothing -- "
        "otherwise the check fires on the state the fix is meant to reach")


def test_a_registered_lane_without_a_falsifier_is_a_finding_and_a_falsifier_clears_it():
    blank = _repo({LANE_A: CALL_A}, [{"lane": "auditor", "script": LANE_A, "falsifier": "   "}])
    findings = CW.judgement_lane_findings(blank)
    assert len(findings) == 1, findings
    assert LANE_A in findings[0], findings
    assert "falsifier" in findings[0].lower(), findings
    ok = _repo({LANE_A: CALL_A}, [{"lane": "auditor", "script": LANE_A, "falsifier": FALSIFIER_A}])
    assert CW.judgement_lane_findings(ok) == [], (
        "the same lane with a real falsifier must clear: the check must not name every lane "
        "no matter what the registry says")


def test_a_registered_lane_whose_script_no_longer_exists_is_a_finding():
    root = _repo({}, [{"lane": "gone", "script": "scripts/gone.py", "falsifier": FALSIFIER_A}])
    findings = CW.judgement_lane_findings(root)
    assert len(findings) == 1, findings
    assert "scripts/gone.py" in findings[0], findings
    assert "no such file" in findings[0].lower(), findings
    live = _repo({LANE_A: CALL_A}, [{"lane": "auditor", "script": LANE_A, "falsifier": FALSIFIER_A}])
    assert CW.judgement_lane_findings(live) == [], (
        "a registry naming a file that exists is not decay -- the check must not report every entry")


def test_a_lane_that_only_mentions_a_model_call_in_prose_is_not_a_lane():
    root = _repo({"scripts/essay.py": PROSE},
                 [{"lane": "auditor", "script": LANE_A, "falsifier": FALSIFIER_A}])
    findings = CW.judgement_lane_findings(root)
    assert all("essay.py" not in f for f in findings), (
        f"the marker sits only in a docstring; prose is not the defect: {findings}")


def test_a_test_suite_is_not_a_judgement_lane():
    root = _repo({"scripts/tests/test_auditor.py": CALL_A}, [])
    assert CW.judgement_lane_findings(root) == [], (
        "scripts/tests/ is not a judgement lane, and a present empty registry is not the "
        "'no registry' finding")


def test_an_entry_that_names_no_lane_is_reported_not_silently_skipped():
    root = _repo({LANE_A: CALL_A}, ["auditor", {"falsifier": FALSIFIER_A}])
    findings = CW.judgement_lane_findings(root)
    joined = "\n".join(findings)
    assert len(findings) == 3, findings      # two malformed entries + the unregistered lane
    assert "malformed" in joined.lower(), findings
    assert LANE_A in joined, findings


def test_an_unreadable_registry_is_reported_not_silently_clean():
    root = _repo({LANE_A: CALL_A}, None)
    reg = root / REGISTRY_REL
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("{not json", encoding="utf-8")
    findings = CW.judgement_lane_findings(root)
    assert len(findings) == 1, findings
    assert REGISTRY_REL in findings[0], findings
    assert "unreadable" in findings[0].lower(), findings


def test_the_checker_consumes_the_findings_and_gates_its_own_run():
    assert hasattr(CW, "check_judgement_lanes"), (
        "Checker.check_judgement_lanes is the wiring: a census nothing runs fires on nothing")
    with tempfile.TemporaryDirectory() as td:
        root = _repo({LANE_A: CALL_A}, [])
        cw = CW.Checker(root, quick=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cw.check_judgement_lanes()
        out = buf.getvalue()
        assert cw.failures == 1, (cw.failures, cw.warnings, cw.passes, out)
        assert len(getattr(cw, "failed_names", [])) == 1, getattr(cw, "failed_names", [])
        assert REGISTRY_REL in cw.failed_names[0], cw.failed_names
        assert LANE_A in out, out
        clean = _repo({LANE_A: CALL_A},
                      [{"lane": "auditor", "script": LANE_A, "falsifier": FALSIFIER_A}])
        cw2 = CW.Checker(clean, quick=True)
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            cw2.check_judgement_lanes()
        assert cw2.failures == 0, (cw2.failures, cw2.failed_names, buf2.getvalue())
        assert cw2.passes == 1, (cw2.passes, cw2.warnings, buf2.getvalue())


if __name__ == "__main__":
    _tests = sorted((n, f) for n, f in list(globals().items())
                    if n.startswith("test_") and callable(f))
    _bad = []
    for _n, _f in _tests:
        try:
            _f()
            print(f"ok   {_n}")
        except AssertionError as _e:
            print(f"FAIL {_n}: {_e}")
            _bad.append(_n)
        except Exception as _e:  # noqa: BLE001
            print(f"ERROR {_n}: {type(_e).__name__}: {_e}")
            _bad.append(_n)
    print(f"{len(_tests) - len(_bad)} of {len(_tests)} passed")
    raise SystemExit(1 if _bad else 0)
