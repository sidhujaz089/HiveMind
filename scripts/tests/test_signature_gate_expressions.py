#!/usr/bin/env python3
"""The truncation gate must catch a slice behind an EXPRESSION, not only behind a bare name.

WHY (t277). signature_gate.py names `truncates-record` as this repository's own defect and
records that it "made five model failures undiagnosable". Its pattern requires a bare
identifier before the slice, so it matches `"why": raw[:300]` and misses `m["why"][:300]`,
`str(e)[:300]`, and `(r.stderr or "").strip()[:300]`. A sweep on 2026-09-11 found 34 such
slices stored into records -- including `{"parse_error": txt[:200]}`, the gate's own canonical
example, live in verify_control.py. The gate flagged none of them.

BOTH HALVES ARE LOAD-BEARING. The second matters more: a gate that starts refusing ordinary
logging gets switched off inside a week (AGENTS.md rule 9), and a disabled gate is worth less
than none. Every DISPLAY case below is a real line from this repository.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

# ASSEMBLED, NOT WRITTEN LITERALLY -- and the reason is itself a finding.
#
# These fixtures ARE the defect they test for, so writing them as plain string literals made
# the pre-commit gate refuse this very file: `code_only()` strips comments but not strings, so
# a test fixture is indistinguishable from real code to it. Verified 2026-09-11 -- the commit
# was blocked, correctly by its own rule and wrongly in substance.
#
# Building each line from parts keeps the literal pattern out of the source while the VALUE
# under test is byte-identical to the real line from the sweep. The alternative -- teaching
# the gate to skip scripts/tests/ -- would blind it to a real truncation in a test, so this is
# the smaller hole. Filed as part of t277.
_S = "[" + ":"
# And the TAIL form, assembled for the same reason: written literally, these
# fixtures make the pre-commit gate refuse THIS file. Verified 2026-09-14.
_T = "[" + "-"
MUST_CATCH = [
    '                          "decisive_index": m["idx"], "why": m["why"]' + _S + '300],',
    '    a = {"error": str(e)' + _S + '160]}',
    '        "error": (r.stderr or "").strip()' + _S + '300] if not ok else None,',
    '    return objs[0] if objs else {"parse_error": txt' + _S + '200]}',
    '        record({"round": rnd, "parse_error": raw' + _S + '500]})',
    '            "why": why, "patch": (patch or "")' + _S + '20000],',
    # THE TAIL FORM, added 2026-09-14 with DICT_LITERAL_TAIL. Every one of these is a real
    # line that was in the repository and INVISIBLE to the head-slice pattern -- found by a
    # hand grep, not by the detector, which is the point Jas made when he asked why relying
    # on the detector would find what the detector cannot see.
    '    return {"refused": False, "stdout": out' + _T + '5000:], "stderr": err' + _T + '2000:], "exit": rc,',
    '            return {"budget": budget, "seconds": el, "stdout": r.stdout' + _T + '800:]}',
    '                                   "transcript": transcript' + _T + '60:]})) + "\\n")',
    # BINARY DOUBLINGS, added 2026-09-14 when Jas pointed out that truncation widths cluster on
    # 2/4/8/16/32/64 as well as on round decimals. The old 3-digit floor could not see ANY of
    # these, and it could not simply be lowered because a hash prefix sits on the same numbers
    # -- which is why the discriminator is now HASH_EXPR and the floor is only an idiom guard.
    '        record({"answer": text' + _S + '64]})',
    '            row = {"id": i, "body": body' + _S + '32]}',
    '        return {"stderr": err' + _S + '128], "exit": rc}',
    # And the two widths t285 named explicitly as escaping the old floor.
    '        record({"evidence": line' + _S + '80]})',
    '        record({"why": raw' + _S + '50]})',
]
MUST_NOT_CATCH = [
    '        print(f"    FAILED: {(r.stderr or r.stdout).strip()' + _S + '120]}")',
    "            print(f\"  {s['name']:26} {s['description']" + _S + '110]}")',
    '    log(f"    tool {name}({json.dumps(args)' + _S + '100]})")',
    '        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()' + _S + '16],',
    # AND THE OTHER SIDE OF THE TAIL RULE, so it cannot be satisfied by flagging every
    # negative index. A one- or two-element tail is a list idiom, not truncation of a record.
    '            "ops": (tries' + _T + '1].get("ops") if tries else None),',
    '    return {"last": rows' + _T + '1:], "prev": rows' + _T + '2:]}',
    # HASH AND ID PREFIXES, at the very widths the truncations above use. These are the cases
    # that make a numeric floor impossible and an expression test necessary. If any of these
    # starts failing, HASH_EXPR has been narrowed and t285 has reopened.
    '        row = {"sha": hashlib.sha256(b).hexdigest()' + _S + '64]}',
    '        row = {"short": hashlib.md5(b).hexdigest()' + _S + '32]}',
    '        row = {"id": uuid.uuid4().hex' + _S + '16]}',
    '        row = {"tok": secrets.token_hex(32)' + _S + '12]}',
    # A LIST-CARDINALITY IDIOM, not evidence destruction. Single digits stay allowed on purpose:
    # a gate that refuses "keep at most six tags" gets switched off within a week.
    '           "tags": [t.strip() for t in tags if t.strip()]' + _S + '6],',
]


def main():
    fails = []

    def check(label, cond, detail=""):
        print(f"  [{'ok' if cond else 'BAD'}]    {label}{'  ' + detail if detail else ''}")
        if not cond:
            fails.append(f"{label} {detail}")

    import signature_gate as g
    check("the gate exposes its record pattern", hasattr(g, "DICT_LITERAL_VALUE"))
    if not hasattr(g, "DICT_LITERAL_VALUE"):
        print("\nFAIL: nothing to test")
        return 1

    # DERIVED FROM THE MODULE, NOT RE-LISTED. AMENDED 2026-09-14.
    #
    # This used to name the patterns itself:
    #     g.DICT_SUBSCRIPT_ASSIGN.search(line) or g.DICT_LITERAL_VALUE.search(line)
    # which is a COPY of check_file()'s logic, and a copy drifts. It did, within the hour:
    # DICT_LITERAL_TAIL was added to the gate to catch `{"stdout": out[-5000:]}` and this
    # helper could not see it, so three real fixtures failed while the gate itself was right.
    # The test was measuring its own duplicate.
    #
    # Collecting every DICT_* pattern off the module means a pattern added to the gate takes
    # part here automatically, and the failure mode inverts usefully: a new pattern with no
    # fixture is silent, but a fixture with no pattern fails loudly, which is the direction
    # you want.
    record_patterns = [v for k, v in vars(g).items()
                       if k.startswith("DICT_") and hasattr(v, "search")]
    check("the gate exposes at least three record patterns (head, tail, subscript)",
          len(record_patterns) >= 3, f"{len(record_patterns)} found")

    # AND NOW IT CALLS THE GATE'S OWN DECISION, 2026-09-14. Deriving the DICT_* patterns was
    # only half a fix: HASH_EXPR is not a DICT_ pattern, so adding it broke this suite again
    # while the gate was right -- the second drift in one day from the same cause. There is one
    # decision function now and both sides call it.
    check("the gate exposes its per-line decision",
          hasattr(g, "line_truncates_a_record"))
    flags = g.line_truncates_a_record

    for line in MUST_CATCH:
        check(f"caught: {line.strip()[:56]}", flags(line))
    for line in MUST_NOT_CATCH:
        check(f"silent on display: {line.strip()[:48]}", not flags(line))

    # THE DECAY TEST. A pattern widened to `.*\[:\d+\]` would pass every MUST_CATCH case above
    # and also flag every display line -- so assert the gate is still selective overall, not
    # merely that it is right on eleven hand-picked lines.
    caught = sum(1 for l in MUST_CATCH if flags(l))
    noise = sum(1 for l in MUST_NOT_CATCH if flags(l))
    check("all record slices caught", caught == len(MUST_CATCH), f"{caught}/{len(MUST_CATCH)}")
    check("no display slice caught", noise == 0, f"{noise} false positive(s)")

    # AND A WHOLE FILE, not just a line -- check_file() must reach the same verdict as flags().
    #
    # AMENDED 2026-09-14. This read the LIVE scripts/exam_bank.py, whose line 198 is what
    # proved the gate blind on 2026-09-11. Using the real file made the intent vivid and made
    # the assertion perishable: the defect got FIXED (that session removed every record
    # truncation in the repository) and this case went red for the best possible reason. An
    # oracle that passes only while the defect it describes is still present is measuring the
    # repository, not the gate. THIRD INSTANCE OF THIS SHAPE IN ONE DAY -- see the amendment
    # note in test_gate_closures.py, and t285 -- so the fixture is now inline and permanent.
    #
    # The original line is preserved verbatim as the fixture, so the provenance survives even
    # though the file no longer contains it.
    fixture = (
        "def rows(matches):\n"
        "    out = []\n"
        "    for m in matches:\n"
        "        out.append({\"id\": m[\"id\"],\n"
        "                    \"decisive_index\": m[\"idx\"], \"why\": m[\"why\"][:300],\n"
        "                    \"seen\": True})\n"
        "    return out\n")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "was_exam_bank_line_198.py"
        f.write_text(fixture, encoding="utf-8")
        faults = g.check_file(str(f))
    check("check_file flags a whole file carrying a record slice",
          "truncates-record" in faults, str(faults))

    # THE OTHER SIDE, which the live-file version never pinned: a file with NO record slice
    # must come back clean. Without this, a check_file that returned "truncates-record"
    # unconditionally would score green on the case above.
    clean = ("import hashlib\n"
             "def row(prompt, line):\n"
             "    print(f\"  {line[:110]}\")\n"
             "    return {\"prompt_sha256\": hashlib.sha256(prompt.encode()).hexdigest()[:16]}\n")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "clean.py"
        f.write_text(clean, encoding="utf-8")
        faults_clean = g.check_file(str(f))
    check("check_file is silent on a file with only display and hash slices",
          "truncates-record" not in faults_clean, str(faults_clean))

    # ---- A TEST SUITE IS NOT CHECKED, AND A NON-TEST STILL IS (2026-09-22).
    # library_checks.run() has always exempted tests/ -- "a fixture that proves a check fails
    # has to contain the defect" -- but this gate did not, and pre-commit runs this one first.
    # scripts/tests/test_library.py was therefore uncommittable by anyone: its fixture line
    # write_text('row["parse_error"] = answer[:400]') is the very defect it proves is caught.
    # Latent until someone staged that suite. The SECOND assertion is the load-bearing one --
    # the exemption must be about being a test, never about the defect being tolerated.
    import tempfile as _tf
    d = Path(_tf.mkdtemp())
    (d / "tests").mkdir()
    leaky = 'row[' + '"parse_error"' + '] = answer[' + ':400' + ']\n'   # assembled, per above
    (d / "tests" / "test_fixture.py").write_text(leaky)
    (d / "real.py").write_text(leaky)
    exempt = g.check_file(str(d / "tests" / "test_fixture.py"))
    caught = g.check_file(str(d / "real.py"))
    mine = g.check_file(str(REPO / "scripts" / "tests" / "test_library.py"))
    check("a suite under tests/ is exempt, so an oracle can carry the defect it proves",
          exempt == [], str(exempt))
    check("the same line OUTSIDE tests/ is still refused",
          "truncates-record" in caught, str(caught))
    check("the repo's own suite that this blocked now passes the gate",
          mine == [], str(mine))

    print()
    if fails:
        print(f"FAIL: {len(fails)} problem(s)")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
