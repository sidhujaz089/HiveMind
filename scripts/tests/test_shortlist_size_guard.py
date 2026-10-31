#!/usr/bin/env python3
"""t1097 oracle: a staffing shortlist entry sized only by a derived estimate must be REFUSED.

workspace/RESEARCH-PROTOCOL-model-staffing.md section 1 (commit 7fd6fbe2) says: measure, don't
derive. Nothing refuses a shortlist entry carrying resident_estimate_gb and no
resident_measured_gb. That shape voided a whole shortlist -- "0 of 15 fit", at a derived 28.2 GB
against 17.6 measured (workspace/rca/b5a09e9fdca4.md). The proof sits in
workspace/SHORTLIST-model-staffing.json: one model tag appears twice, its FRONTLINE entry
carrying resident_measured_gb 3.3 and its pool entry carrying only resident_estimate_gb 20.0, a
6x error, because weight+1 assumes every parameter is resident and an MoE loads active experts.

This suite is the recorder that makes the rule mechanical: scripts/check_shortlist_size.py. It
asserts the entry's identity, the tier, the tag, the field that is missing, and the exit code.

Run: python3 scripts/tests/test_shortlist_size_guard.py    (pytest is not installed)
"""
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True          # running this suite must not litter the tree

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "check_shortlist_size.py"
REAL_SHORTLIST = ROOT / "workspace" / "SHORTLIST-model-staffing.json"

# Both entry shapes are copied from workspace/SHORTLIST-model-staffing.json. GEMMA12 is the void
# shape: sized by the derived formula, never measured. GEMMA26 is the shape that passes: it
# carries resident_measured_gb. A guard keyed on the model name cannot satisfy both.
GEMMA12 = {
    "model": "Gemma 4 12B",
    "ollama_tag": "gemma4:12b",
    "lab": "Google",
    "rank": 94,
    "coding_score": 0.485,
    "weight_gb": 7.6,
    "resident_estimate_gb": 8.6,
    "tier": "FRONTLINE",
    "source": "https://ollama.com/library/gemma4",
}
GEMMA26 = {
    "model": "Gemma 4 26B A4B",
    "ollama_tag": "gemma4:26b",
    "lab": "Google",
    "rank": 109,
    "coding_score": 0.463,
    "weight_gb": 19,
    "resident_estimate_gb": 20.0,
    "tier": "FRONTLINE",
    "source": "https://ollama.com/library/gemma4",
    "resident_measured_gb": 3.3,
}

RESULTS = []


def record(label, ok, detail=""):
    ok = bool(ok)
    RESULTS.append((label, ok))
    line = ("PASS  " if ok else "FAIL  ") + label
    if not ok and detail != "":
        line += "  :: " + str(detail)
    print(line)
    return ok


def get_module(why):
    """Import the guard by the path it must live on. Absent or unimportable is a recorded failure
    naming the path, so a red run states the missing change and never crashes."""
    if not MODULE_PATH.is_file():
        record(why + ": scripts/check_shortlist_size.py exists", False,
               "scripts/check_shortlist_size.py is not in the repository. Authoring it is the "
               "change this suite asks for.")
        return None
    try:
        spec = importlib.util.spec_from_file_location("t1097_check_shortlist_size", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:                              # noqa: BLE001
        record(why + ": scripts/check_shortlist_size.py imports", False,
               type(exc).__name__ + ": " + str(exc))
        return None
    return module


def test_module_exposes_the_api():
    module = get_module("api")
    if module is None:
        return
    for name in ("SIZE_WAIVER_FIELD", "SIZE_WAIVER_VALUES", "find_estimate_only", "main"):
        record("the guard exposes " + name, hasattr(module, name),
               "scripts/check_shortlist_size.py defines no " + name)
    if hasattr(module, "SIZE_WAIVER_FIELD"):
        record("SIZE_WAIVER_FIELD is the shortlist field size_status",
               module.SIZE_WAIVER_FIELD == "size_status",
               repr(getattr(module, "SIZE_WAIVER_FIELD", None)))
    if hasattr(module, "SIZE_WAIVER_VALUES"):
        record("SIZE_WAIVER_VALUES is exactly VOID and PENDING_SIZE",
               set(module.SIZE_WAIVER_VALUES) == {"VOID", "PENDING_SIZE"},
               sorted(set(getattr(module, "SIZE_WAIVER_VALUES", set()))))


def test_estimate_only_entry_is_flagged_with_its_identity():
    module = get_module("estimate-only")
    if module is None:
        return
    doc = {"SENIOR": [], "FRONTLINE": [dict(GEMMA12)], "pool": [], "rejected": []}
    findings = module.find_estimate_only(doc)
    record("an estimate-only entry produces exactly one finding", len(findings) == 1, findings)
    if not findings:
        return
    finding = findings[0]
    record("the finding is a dict naming the entry's model",
           isinstance(finding, dict) and finding.get("model") == "Gemma 4 12B", finding)
    record("the finding names the tier list it was found in",
           isinstance(finding, dict) and finding.get("tier") == "FRONTLINE", finding)
    record("the finding names the ollama_tag, so a reader can find the entry",
           isinstance(finding, dict) and finding.get("ollama_tag") == "gemma4:12b", finding)
    record("the finding names the field that is missing",
           isinstance(finding, dict) and finding.get("missing") == "resident_measured_gb", finding)


def test_a_measured_entry_passes():
    module = get_module("measured")
    if module is None:
        return
    doc = {"SENIOR": [dict(GEMMA26)], "FRONTLINE": [], "pool": [], "rejected": []}
    findings = module.find_estimate_only(doc)
    record("an entry carrying resident_measured_gb produces no finding",
           list(findings) == [], findings)


def test_a_size_waiver_on_the_allowlist_passes():
    module = get_module("waiver")
    if module is None:
        return
    for value in ("PENDING_SIZE", "VOID"):
        entry = dict(GEMMA12)
        entry["size_status"] = value
        doc = {"SENIOR": [], "FRONTLINE": [], "pool": [entry], "rejected": []}
        findings = module.find_estimate_only(doc)
        record("an estimate-only entry with size_status " + repr(value) + " produces no finding",
               list(findings) == [], findings)


def test_a_waiver_value_outside_the_allowlist_is_still_flagged():
    """The rule is an ALLOWLIST. A guard that waives on truthiness -- any non-empty size_status --
    passes this suite with the rule's force removed. That is how a requirement written only in
    prose dies."""
    module = get_module("allowlist")
    if module is None:
        return
    entry = dict(GEMMA12)
    entry["size_status"] = "MEASURED_LATER"
    doc = {"SENIOR": [], "FRONTLINE": [], "pool": [entry], "rejected": []}
    findings = list(module.find_estimate_only(doc))
    record("size_status MEASURED_LATER does NOT waive: the entry is still flagged",
           len(findings) == 1, findings)


def test_the_real_shortlist_is_reported_entry_by_entry():
    """The file that runs, not a fixture: a guard that hard-codes today's model names passes the
    synthetic pair above and fails here. gemma4:26b appears TWICE -- the FRONTLINE copy carries
    resident_measured_gb, the pool copy does not. So a guard that keys on the model name, that
    caches a verdict per tag, or that stops at the first tier, fails."""
    module = get_module("real file")
    if module is None:
        return
    if not REAL_SHORTLIST.is_file():
        record("the real shortlist is readable", False, str(REAL_SHORTLIST) + " is missing")
        return
    doc = json.loads(REAL_SHORTLIST.read_text(encoding="utf-8"))
    flagged = {(f.get("tier"), f.get("ollama_tag")) for f in module.find_estimate_only(doc)}
    record("the SENIOR entry sized by the derived formula is flagged",
           ("SENIOR", "gpt-oss:20b") in flagged, sorted(flagged))
    record("the SENIOR gemma4:31b entry sized by the derived formula is flagged",
           ("SENIOR", "gemma4:31b") in flagged, sorted(flagged))
    record("the pool entry sized by the derived formula is flagged",
           ("pool", "qwen3.8:27b") in flagged, sorted(flagged))
    record("the FRONTLINE gemma4:26b entry that carries resident_measured_gb is NOT flagged",
           ("FRONTLINE", "gemma4:26b") not in flagged, sorted(flagged))
    record("the pool gemma4:26b entry that carries ONLY the estimate IS flagged",
           ("pool", "gemma4:26b") in flagged, sorted(flagged))
    record("the rejected entries, which carry no size field, produce no finding",
           not any(tier == "rejected" for tier, tag in flagged), sorted(flagged))


def test_it_does_not_mutate_the_document_it_reads():
    module = get_module("no mutation")
    if module is None:
        return
    doc = {"SENIOR": [], "FRONTLINE": [dict(GEMMA12)], "pool": [], "rejected": []}
    before = json.dumps(doc, sort_keys=True)
    module.find_estimate_only(doc)
    record("find_estimate_only leaves the document byte-identical",
           json.dumps(doc, sort_keys=True) == before, json.dumps(doc, sort_keys=True))


def test_the_cli_is_red_on_a_void_shortlist_and_green_on_a_measured_one():
    module = get_module("cli")
    if module is None:
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        void_path = Path(tmpdir) / "void.json"
        measured_path = Path(tmpdir) / "measured.json"
        void_path.write_text(json.dumps(
            {"SENIOR": [], "FRONTLINE": [dict(GEMMA12)], "pool": [], "rejected": []}),
            encoding="utf-8")
        measured_path.write_text(json.dumps(
            {"SENIOR": [], "FRONTLINE": [dict(GEMMA26)], "pool": [], "rejected": []}),
            encoding="utf-8")

        void_run = subprocess.run([sys.executable, str(MODULE_PATH), str(void_path)],
                                  capture_output=True, text=True, timeout=60)
        record("the CLI exits non-zero on a shortlist of estimate-only entries",
               void_run.returncode != 0,
               "returncode " + str(void_run.returncode) + " stdout " + repr(void_run.stdout))
        record("the CLI names the flagged entry by its tag",
               "gemma4:12b" in void_run.stdout, repr(void_run.stdout))
        record("the CLI says which field is missing",
               "resident_measured_gb" in void_run.stdout, repr(void_run.stdout))

        clean = subprocess.run([sys.executable, str(MODULE_PATH), str(measured_path)],
                               capture_output=True, text=True, timeout=60)
        record("the CLI exits 0 on a shortlist where every entry is measured",
               clean.returncode == 0,
               "returncode " + str(clean.returncode) + " stdout " + repr(clean.stdout))
        record("the clean run names no entry",
               "gemma4" not in clean.stdout, repr(clean.stdout))


def test_an_unreadable_path_is_loud_not_silent():
    """except Exception: pass made gemma-decide.py report 1385 healthy heartbeats for a request
    that had never succeeded. A guard that reads nothing and reports success is the same defect at
    the shortlist endpoint: a non-zero code and a printed reason, never 0 and silence."""
    module = get_module("loud")
    if module is None:
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        missing = Path(tmpdir) / "no-such-shortlist.json"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = module.main([str(missing)])
        out = buffer.getvalue()
        record("main() returns 2, not 0, on a path it could not read", code == 2, repr(code))
        record("main() prints a line naming the unreadable path", str(missing) in out, repr(out))
        record("main() states why the file was unusable, not just that it was",
               out.strip() != "" and str(missing) in out, repr(out))


def test_a_file_that_is_not_a_json_object_is_refused():
    """A shortlist truncated mid-write is an empty list on disk. The guard must not report a
    shortlist of zero entries as success: that is the void shortlist passing as a clean one."""
    module = get_module("not an object")
    if module is None:
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        bad = Path(tmpdir) / "torn.json"
        bad.write_text("[1, 2, 3]", encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = module.main([str(bad)])
        record("main() returns 2 on a file that is not a JSON object", code == 2, repr(code))
        record("main() prints a reason for refusing the file",
               buffer.getvalue().strip() != "", repr(buffer.getvalue()))


TESTS = [
    test_module_exposes_the_api,
    test_estimate_only_entry_is_flagged_with_its_identity,
    test_a_measured_entry_passes,
    test_a_size_waiver_on_the_allowlist_passes,
    test_a_waiver_value_outside_the_allowlist_is_still_flagged,
    test_the_real_shortlist_is_reported_entry_by_entry,
    test_it_does_not_mutate_the_document_it_reads,
    test_the_cli_is_red_on_a_void_shortlist_and_green_on_a_measured_one,
    test_an_unreadable_path_is_loud_not_silent,
    test_a_file_that_is_not_a_json_object_is_refused,
]

if __name__ == "__main__":
    for test in TESTS:
        try:
            test()
        except Exception as exc:                          # noqa: BLE001
            record(test.__name__ + " raised", False, type(exc).__name__ + ": " + str(exc))
    failed = [label for label, ok in RESULTS if not ok]
    print("")
    print(str(len(RESULTS) - len(failed)) + "/" + str(len(RESULTS)) + " assertions passed")
    for label in failed:
        print("FAILED: " + label)
    sys.exit(1 if failed else 0)
