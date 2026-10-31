#!/usr/bin/env python3
"""Oracle for t1224 -- a judgement-bearing task cannot be assigned a model tag that has not
qualified for it.

WHY (workspace/rca/cce460d3b012.md, cause K2). The bar ran and recorded FAIL in git (8ce54aa3) and
had no consumer. workspace/exams/results/ENDURANCE-swift.json says 4/18, traps -2/8, fabricated on
the missing file; workspace/exams/SWIFT-PASS-CONDITIONS.md says 'Verdict: FAIL. Nothing is retired'
-- and 4m25s later Swift was routed to the judgement seats. ROLES.json has no field to bind a
verdict to, so a measured FAIL cannot stop a routing decision.

RED ON MAIN: scripts/role_store.py (460 lines) has no JUDGEMENT_TASKS, no QUALIFICATIONS, no
NotQualified, no qualification_rows, no unqualified_judgement_saves, no record_override, and save()
takes no force. Every assertion below fails on that.

NO LIVE STATE: every qualification file is created under tempfile.mkdtemp. Nothing here calls
save() (it takes models/.roles.lock in the repo), makes a network call, starts a process, reads
today's date for an assertion (today is passed in), or writes outside its temp dirs.
"""
import ast
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import role_store  # noqa: E402

JUDGEMENT = ("rca", "script_review", "session_review", "scoping", "supervision")
RESULTS = REPO / "workspace" / "exams" / "results" / "ENDURANCE-swift.json"


def _store(role="manager", tag="swift", tasks=("rca",)):
    return {"roles": {role: {"name": "Manager", "kind": "model", "model": tag,
                             "tasks": [{"task": t} for t in tasks]}}}


def _tmp(name="QUALIFICATIONS.json"):
    return Path(tempfile.mkdtemp(prefix="t1224-")) / name


def test_the_gate_symbols_exist():
    for name in ("JUDGEMENT_TASKS", "QUALIFICATIONS", "NotQualified", "qualification_rows",
                 "unqualified_judgement_saves", "record_override"):
        assert hasattr(role_store, name), f"role_store.py has no {name}"


def test_the_judgement_set_is_the_five_named_lanes():
    assert set(role_store.JUDGEMENT_TASKS) == set(JUDGEMENT), sorted(role_store.JUDGEMENT_TASKS)


def test_the_qualifications_path_is_the_endpoint_the_rca_names():
    assert str(role_store.QUALIFICATIONS).replace("\\", "/").endswith(
        "workspace/exams/QUALIFICATIONS.json"), role_store.QUALIFICATIONS


def test_a_missing_qualifications_file_is_empty_not_fatal():
    """A Models-tab save must not crash on a box where the papers have never been written."""
    rows = role_store.qualification_rows(_tmp())
    assert rows == [], f"missing file gave {rows!r}"


def test_a_malformed_qualifications_file_is_reported_not_swallowed():
    p = _tmp()
    p.write_text("{not json", encoding="utf-8")
    assert role_store.qualification_rows(p) == [], "a malformed file must yield []"


def test_a_tag_with_no_row_is_unqualified_for_a_judgement_task():
    """The live shape: ROLES.json gives rca to tags that have no qualification row at all."""
    f = role_store.unqualified_judgement_saves(_store(), [], today="2026-10-15")
    assert ("manager", "rca", "swift") in f, f"not gated: {f!r}"


def test_a_non_judgement_task_is_not_gated():
    """Negative assertion: a gate that blocks building, console and corpus_index is useless."""
    d = _store(tasks=("building", "console", "corpus_index", "intake"))
    assert role_store.unqualified_judgement_saves(d, [], today="2026-10-15") == [], "non-judgement tasks gated"


def test_a_role_with_no_model_is_not_gated():
    d = {"roles": {"jas": {"name": "Jas", "kind": "person", "tasks": [{"task": "rca"}]}}}
    assert role_store.unqualified_judgement_saves(d, [], today="2026-10-15") == [], "a person role was gated"


def test_a_pass_row_for_the_tag_closes_the_gate():
    rows = [{"kind": "pass", "tag": "swift", "verdict": "pass"}]
    assert role_store.unqualified_judgement_saves(_store(), rows, today="2026-10-15") == [], "a pass row did not qualify"


def test_a_fail_row_for_the_real_swift_sitting_does_not_qualify():
    """The numbers are read out of the committed ENDURANCE-swift.json, not invented."""
    d = json.loads(RESULTS.read_text(encoding="utf-8"))
    row = {"kind": "pass", "tag": "swift", "paper": "ENDURANCE", "verdict": "fail",
           "real_score": d["real_score"], "real_max": d["real_max"],
           "trap_score": d["trap_score"], "trap_max": d["trap_max"],
           "fabricated_on_missing_file": d["fabricated_on_missing_file"]}
    assert row["real_score"] == 4 and row["trap_score"] == -2 and row["fabricated_on_missing_file"] is True
    f = role_store.unqualified_judgement_saves(_store(), [row], today="2026-10-15")
    assert ("manager", "rca", "swift") in f, f"a FAIL sat at 4/18 qualified the seat: {f!r}"


def test_a_pass_row_marked_fabricated_does_not_qualify():
    """The RCA's own rule: fabricated_on_missing_file is a disqualifier, not a score."""
    rows = [{"kind": "pass", "tag": "swift", "verdict": "pass",
             "fabricated_on_missing_file": True}]
    f = role_store.unqualified_judgement_saves(_store(), rows, today="2026-10-15")
    assert ("manager", "rca", "swift") in f, f"a fabricated pass qualified the seat: {f!r}"


def test_a_pass_row_sat_at_a_different_config_does_not_qualify_the_deployed_tag():
    """K7: Swift sat W4A16 and was deployed NVFP4; -56k has no rows at all."""
    rows = [{"kind": "pass", "tag": "swift", "verdict": "pass", "sat_against_current_tag": False}]
    f = role_store.unqualified_judgement_saves(_store(), rows, today="2026-10-15")
    assert ("manager", "rca", "swift") in f, f"a row sat at another config qualified this tag: {f!r}"


def test_a_pass_row_for_a_different_tag_does_not_qualify_this_one():
    rows = [{"kind": "pass", "tag": "qwen3.8:27b-mtp-q4_K_M", "verdict": "pass"}]
    f = role_store.unqualified_judgement_saves(_store(tag="qwen3.8:27b-mtp-q4_K_M-56k"), rows,
                                               today="2026-10-15")
    assert ("manager", "rca", "qwen3.8:27b-mtp-q4_K_M-56k") in f, (
        f"a row for a sibling tag qualified -56k: {f!r}")


def test_an_override_is_dated_attributed_and_opens_the_gate():
    p = _tmp()
    row = role_store.record_override(p, "swift", "rca", "Jas", "interim: sit 5 and 6 before the bar", expires="2026-11-01")
    assert row["kind"] == "override", row
    assert row["tag"] == "swift" and row["task"] == "rca", row
    assert row["who"] == "Jas" and row["reason"].startswith("interim"), row
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row["date"])), f"override date not an ISO date: {row!r}"
    assert row["expires"] == "2026-11-01", row
    back = role_store.qualification_rows(p)
    assert back == [row], f"the override row was not appended to the file: {back!r}"
    assert role_store.unqualified_judgement_saves(_store(), back, today="2026-10-15") == [], \
        "a live override did not open the gate"
    assert role_store.unqualified_judgement_saves(_store(), back, today="2026-11-02") != [], \
        "an override that had expired still opened the gate"


def test_an_override_needs_a_who_and_a_reason():
    """Inversion rule: the approved list is attributable and never written by the thing checked.
    A gate that can be waved through by an anonymous flag is a gate that is bypassed."""
    p = _tmp()
    for who, reason in (("", "reason here"), ("Jas", ""), ("   ", "reason here")):
        try:
            role_store.record_override(p, "swift", "rca", who, reason)
        except ValueError:
            continue
        raise AssertionError(f"an override with who={who!r} reason={reason!r} was accepted")
    assert role_store.qualification_rows(p) == [], "a refused override was still written"


def test_the_gate_is_wired_into_the_save_that_the_models_tab_uses():
    """Static, no save() call: _save must consult the rows and refuse BEFORE it writes anything,
    and save must offer the human override. A gate that runs after _write_json is a log, not a gate."""
    src = (REPO / "scripts" / "role_store.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    save = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "save"), None)
    assert save is not None, "role_store.py has no top-level save() -- the Models tab writes elsewhere"
    kw = [a.arg for a in save.args.args] + [a.arg for a in save.args.kwonlyargs]
    assert "force" in kw, f"save() has no force parameter: {kw}"
    assert "qualifications" in kw, f"save() has no qualifications parameter: {kw}"
    body = save.body[0] if (save.body and isinstance(save.body[0], ast.With)) else None
    assert body is not None, "save() no longer holds the writer lock"
    call = next((n for n in body.body if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "_save"), None)
    assert call is not None, "save() no longer calls _save"
    passed = [getattr(a, "id", None) for a in call.args] + [k.arg for k in call.keywords]
    assert "force" in passed and "qualifications" in passed, (
        f"save() does not forward its override to _save: {passed}")
    _save = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_save"), None)
    assert _save is not None, "role_store.py has no top-level _save() -- the writer moved"
    calls = [n for n in ast.walk(_save)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) in
             ("unqualified_judgement_saves", "qualification_rows", "_write_json")]
    def first(name):
        hits = sorted(n.lineno for n in calls if getattr(n.func, "id", None) == name)
        return hits[0] if hits else None
    gate, reads, write = first("unqualified_judgement_saves"), first("qualification_rows"), first("_write_json")
    assert gate, "_save never calls unqualified_judgement_saves"
    assert reads and reads < gate, "_save must read the qualification rows before gating"
    assert write and gate < write, (
        f"the gate fires at line {gate}, after the first _write_json at {write} -- a refusal that "
        "writes first is a log, not a gate")
    raises = [n for n in ast.walk(_save) if isinstance(n, ast.Raise)]
    assert any("NotQualified" in ast.unparse(n) for n in raises), (
        "_save does not raise NotQualified: an unqualified judgement save is not refused")


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as exc:  # noqa: BLE001 -- a refusal must name the roles
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
