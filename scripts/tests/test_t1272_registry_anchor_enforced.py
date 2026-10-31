#!/usr/bin/env python3
"""t1272 -- a site's RECORDED guard must be where it was recorded, and a clearance must say what it guards.

THE DEFECT THIS SUITE EXISTS FOR (workspace/rca/e84789eac92b.md, threads T2/T3/T4).
`python3 scripts/check_vacuous.py` prints
`[PASS] 68 verdict site(s), all accounted for (58 cleared, 7 fixed, ...)` and exits 0 while
the registry's canonical record for `scripts/check_ledger.py::problems#0` claims a fix
(`must_contain: "return None"`, `guard_at: "scripts/check_ledger.py:144"`, reason: "line 144 now
returns None (distinct third state)") that the live code does NOT have at that site: the live
code at scripts/check_ledger.py:183-184 returns True, and the string `return None` exists in the
file only at line 176, as `return None, None`, inside a DIFFERENT function (`declared_sections`,
the "no protocol source found" sentinel). The scan at scripts/check_vacuous.py:90 searches the
whole file for `must_contain` and throws the recorded line number away (lines 76-86 explain that
this was deliberate, to survive ordinary editing), and `expect_matches` (101-105) is optional.
So a guard that has been moved, deleted, or never existed at its recorded site is invisible.

WHY A SUBSTRING SCAN IS THE WHOLE PROBLEM, IN ONE SENTENCE: a registry entry that a grep can
satisfy from an unrelated line is a record the next reader inherits as a fact -- which is exactly
what the exam author inherited, and why the element-list defect item could not be built
(seq 3176: "Rather than reconstruct one by hand -- which is how bad exams get made").

WHAT THIS SUITE PINS, and why each assertion is the shape a person would check by hand:
  1. a guard recorded INSIDE a function must be found INSIDE that function -- not anywhere; and
     the failure message must name the file, the recorded line, and the line it was found at
     (an error that names a symptom, not a cause, is undiagnosable -- never-truncate-a-record).
  2. line drift INSIDE the recorded function must still PASS. This is the anti-over-tightening
     control: the registry's own history is 144 -> 176, and the file's comment at 76-86 insists a
     demonstration must survive ordinary editing. A fix that pins exact line numbers dies on the
     first edit above the site, which is the failure this check has had three times (lines 56-62).
  3. a `cleared` site carrying no `defective_form` is a FINDING, named by site id, exit non-zero.
  4. a `defect-fixed` site carrying no `demonstration` is a FINDING. main() at lines 144-145
     only holds a `defect-fixed` site to a demonstration it HAS, so a fixed site recorded with no
     demonstration at all is never checked -- the T3 half of "the enforcement is not biting".
  5. a complete site (guard inside its recorded function, demonstration present, defective_form
     present) reports NOTHING and exits 0. Without this control the fix is unfalsifiable.

EVERYTHING HERE IS TEMPORARY. The live workspace/vacuous-registry.json, the live scripts/ tree and
workspace/exams/ are never read, written, or asserted against (oracles never touch live state; a
fixture that shares a channel with the machinery it is not modelling measures that machinery --
docs/skills/writing-oracles-for-delegation).
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK_VACUOUS = ROOT / "scripts" / "check_vacuous.py"

# One fixture file, used by every test. `if not problems:` on line 3 is what sites() keys on
# (scripts/check_vacuous.py:50-67: ast.If whose test name contains a KEYS substring such as
# "problem"), so the key below is the key sites() produces for this file -- no fake key.
G_PY = (
    "def check_thing(x):\n"
    "    problems = []\n"
    "    if not problems:\n"
    "        return None, [\"not checked -- nothing examined\"]\n"
    "    return True, []\n"
    "\n"
    "def other(x):\n"
    "    return True, [\"ok\"]\n"
)

# A second file where the guard string lives in a function OTHER than the recorded one.
F_PY = (
    "def check_thing(x):\n"
    "    problems = []\n"
    "    return True, [\"nothing was examined\"]\n"
    "\n"
    "def other(x):\n"
    "    return None, [\"the string the registry means\"]\n"
)

SITE = "scripts/g.py::problems#0"
GUARD = 'return None, ["not checked -- nothing examined"]'


def _load():
    """A FRESH module instance per test, so REPO/REGISTRY/SCRIPTS never leak between tests."""
    spec = importlib.util.spec_from_file_location("check_vacuous_under_test", CHECK_VACUOUS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _mini_repo(tmp, registry, g_py=G_PY):
    """A repo-shaped temp tree: scripts/g.py + workspace/vacuous-registry.json."""
    _write(tmp, "scripts/g.py", g_py)
    reg = _write(tmp, "workspace/vacuous-registry.json", json.dumps({"sites": registry}))
    return reg


def _run_main(tmp, reg_path):
    """Run check_vacuous.main() against the temp tree. Returns (rc, output). timeout= is mandatory
    in an oracle; -c so the builder is never asked to create a file."""
    prog = (
        "import sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {json.dumps(str(ROOT / 'scripts'))})\n"
        "import check_vacuous as c\n"
        f"c.REPO = Path({json.dumps(str(tmp))})\n"
        f"c.REGISTRY = Path({json.dumps(str(reg_path))})\n"
        f"c.SCRIPTS = Path({json.dumps(str(tmp / 'scripts'))})\n"
        "sys.argv = ['check_vacuous.py', '--quiet']\n"
        "sys.exit(c.main())\n"
    )
    r = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True,
                       timeout=120, cwd=str(tmp))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def test_guard_recorded_outside_its_function_is_a_finding():
    """THE DEFECT, IN ISOLATION. The string is present in the file -- on main that is enough."""
    mod = _load()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write(tmp, "scripts/f.py", F_PY)
        mod.REPO = tmp
        ok, why = mod.demonstration_holds(
            "scripts/f.py::problems#0",
            {"status": "cleared",
             "demonstration": {"guard_at": "scripts/f.py:2",
                               "must_contain": "the string the registry means"}})
        assert ok is False, (
            "the guard was recorded at scripts/f.py:2, inside check_thing, and was accepted "
            "only because its string appears at line 6, inside a DIFFERENT function -- this is "
            "the live check_ledger.py::problems#0 shape (recorded at 144, found at 176). "
            f"got ok={ok!r} why={why!r}")
        # A reason a reader can act on: name the file, where it was recorded, where it lives.
        assert "f.py" in why, f"the reason must name the file: {why!r}"
        assert "2" in why, f"the reason must name the recorded line (2): {why!r}"
        assert "6" in why, f"the reason must name the line the string is actually at (6): {why!r}"
        assert "the string the registry means" in why, f"the reason must quote the guard: {why!r}"


def test_a_guard_that_only_moved_within_its_function_still_holds():
    """CONTROL (green before and after the fix). Drift INSIDE the recorded function is ordinary
    editing: the registry's own live drift is 144 -> 176, and check_vacuous.py:76-86 states a
    demonstration must survive it. This is the assertion that stops the fix being an exact-line
    equality, which is the shape that broke the check three times before (see lines 56-62)."""
    mod = _load()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pad = "".join(f"    inserted_{i} = {i}\n" for i in range(12))
        # guard now sits at line 16; the registry still records line 4. Same function.
        _write(tmp, "scripts/g.py",
               "def check_thing(x):\n"
               "    problems = []\n"
               "    if not problems:\n"
               f'        return None, ["not checked -- nothing examined"]\n'   # recorded at 4
               f"{pad}"
               "    return True, []\n"
               "\n"
               "def other(x):\n"
               '    return None, ["not checked -- nothing examined"]\n')
        mod.REPO = tmp
        ok, why = mod.demonstration_holds(
            SITE,
            {"status": "cleared",
             "demonstration": {"guard_at": "scripts/g.py:4", "must_contain": GUARD}})
        assert ok is True, (
            "line drift inside the recorded function must NOT invalidate a real guard "
            f"(that is the 144->176 drift the live registry already carries): {why!r}")


def test_a_guard_recorded_on_its_own_line_still_holds():
    """CONTROL: a site recorded exactly where its guard sits reports nothing."""
    mod = _load()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write(tmp, "scripts/g.py", G_PY)
        mod.REPO = tmp
        ok, why = mod.demonstration_holds(
            SITE, {"status": "cleared",
                   "demonstration": {"guard_at": "scripts/g.py:4", "must_contain": GUARD}})
        assert ok is True, f"a correctly recorded guard must pass: {why!r}"


def test_a_cleared_site_without_defective_form_is_a_finding():
    """A clearance that does not say what it guards is a record the next authoring pass cannot
    use -- the dead end that made the element-list exam item unbuildable (RCA T4)."""
    reg = {SITE: {"status": "cleared",
                  "reason": "guard is present, reason is prose",
                  "demonstration": {"guard_at": "scripts/g.py:4", "must_contain": GUARD}}}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rc, out = _run_main(tmp, _mini_repo(tmp, reg))
        assert "defective_form" in out, (
            "a cleared site with no defective_form must be reported, naming the field literally "
            f"so a future reader can grep for it: {out!r}")
        assert SITE in out, f"the finding must name the site id: {out!r}"
        assert rc != 0, f"an unbacked clearance must fail the run: rc={rc} out={out!r}"


def test_a_defect_fixed_site_with_no_demonstration_is_a_finding():
    """main() at 144-145 holds a defect-fixed site to a demonstration it HAS, so a fixed site
    recorded with no demonstration is never checked at all (RCA T3)."""
    reg = {SITE: {"status": "defect-fixed",
                  "reason": "was a vacuous pass on an empty element list",
                  "defective_form": "if not declared: return True, [...]  --  empty list -> not-a-pass"}}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rc, out = _run_main(tmp, _mini_repo(tmp, reg))
        assert "defect-fixed" in out, (
            f"a defect-fixed site with no demonstration must be reported by status: {out!r}")
        assert SITE in out, f"the finding must name the site id: {out!r}"
        assert rc != 0, f"a fixed site with no demonstration must fail the run: rc={rc} out={out!r}"


def test_a_fully_recorded_site_reports_nothing():
    """CONTROL (green before and after): guard in its recorded function, demonstration present,
    defective_form present. Without this, 'fails on everything' is a passing fix."""
    reg = {SITE: {"status": "cleared",
                  "reason": "guard present",
                  "defective_form": "input: element list absent from protocol -> not-a-pass",
                  "demonstration": {"guard_at": "scripts/g.py:4", "must_contain": GUARD}}}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rc, out = _run_main(tmp, _mini_repo(tmp, reg))
        assert "defective_form" not in out, f"a complete site must not be reported: {out!r}"
        assert "defect-fixed" not in out, f"no defect-fixed finding expected here: {out!r}"
        assert "no longer prove it" not in out, f"no broken-demonstration finding: {out!r}"
        assert rc == 0, f"a complete site must exit 0: rc={rc} out={out!r}"


if __name__ == "__main__":
    _tests = sorted((v for k, v in globals().items()
                     if k.startswith("test_") and callable(v)), key=lambda f: f.__name__)
    _failed = 0
    for _t in _tests:
        try:
            _t()
            print(f"  ok   {_t.__name__}")
        except Exception:
            _failed += 1
            print(f"  FAIL {_t.__name__}")
            traceback.print_exc()
    print(f"{len(_tests) - _failed}/{len(_tests)} passed")
    sys.exit(1 if _failed else 0)
