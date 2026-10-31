"""t752 -- scope_lint must refuse an oracle that computes its own expected values
at run time from a repository file the builder is never shown.

RED ON MAIN for one reason: `scope_lint` has no `computed_expectation` function, so every call in
this file raises AttributeError and the CLI run prints no `oracle-computed-expectation` line.

Evidence: workspace/preflight-objections.jsonl at 2026-10-07T143015Z (brief
workspace/briefs/brief-p290-t255-build.gradle.txt, model swift, verdict NOT SATISFIABLE: "the
oracle reads projects/good-dictator-app/app/build.gradle.kts at runtime ... to extract the
signing-config name and the environment-variable names it will assert. That file is not shown to
me and is not listed under FILES I MAY CHANGE") and the same brief re-scoped to QUOTE it, at
2026-10-07T211431Z, verdict SATISFIABLE. workspace/scope-quality.jsonl, scope t255@6acf1c26ba6d.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import scope_lint

REF = "projects/good-dictator-app/app/build.gradle.kts"
EDITED = "projects/indoor-voice/app/build.gradle.kts"
SUITE = "scripts/tests/test_indoor_voice_release_build.py"

# The t255 shape, transcribed from the oracle in that brief: the expected values are pulled out of a
# repository file by a regex and `.group(...)`, and the implementation chunk edits a DIFFERENT file,
# so the builder sees neither the reference file nor the names its own test will assert.
T255_ORACLE = (
    "from pathlib import Path\n"
    "import re\n"
    "ROOT = Path(__file__).resolve().parents[2]\n"
    'REF = ROOT / "projects" / "good-dictator-app" / "app" / "build.gradle.kts"\n'
    "def test_signing_config_matches_the_working_app():\n"
    '    text = REF.read_text(encoding="utf-8")\n'
    "    cfg = re.search(r'signingConfigs\\s*\\{[^}]*create\\(\"([^\"]+)\"\\)', text).group(1)\n"
    "    envs = set(re.findall(r'System.getenv\\(\"([^\"]+)\"\\)', text))\n"
    '    app = (ROOT / "projects" / "indoor-voice" / "app" / "build.gradle.kts").read_text(encoding="utf-8")\n'
    "    assert cfg in app\n"
    "    assert set(re.findall(r'System.getenv\\(\"([^\"]+)\"\\)', app)) == envs\n"
)


def block(impl_file, prose):
    """A two-chunk block in t255's shape: the oracle above, plus an implementation chunk."""
    return [
        {"kind": "oracle", "file": SUITE, "oracle_text": T255_ORACLE,
         "red_because": EDITED + " has no signing config"},
        {"kind": "implementation", "file": impl_file, "target": SUITE,
         "single_change": prose, "done_when": "python3 " + SUITE + " exits 0"},
    ]


def test_the_t255_shape_is_refused(tmp):
    out = scope_lint.computed_expectation(
        block(EDITED, "insert the signing config exactly as the working app has it"), ROOT)
    assert [(c[0], c[1], c[2]) for c in out] == [
        ("oracle-computed-expectation", 0, SUITE)], out
    assert REF in out[0][3], out[0][3]


def test_reading_the_file_the_builder_edits_is_not_a_defect(tmp):
    # A suite that verifies its own edited target reads a file the builder IS shown. A gate that
    # flags this shape gets disabled within a week, so it must be silent here.
    assert scope_lint.computed_expectation(
        block(REF, "add the signing config to this file"), ROOT) == []


def test_naming_the_path_in_prose_is_not_showing_it(tmp):
    # The brief's prose names the reference file. The builder has no shell: naming it does not
    # make its contents visible (preflight 2026-10-07T211431Z: it passed only once QUOTED).
    out = scope_lint.computed_expectation(
        block(EDITED, "match " + REF + " exactly"), ROOT)
    assert [(c[0], c[1], c[2]) for c in out] == [
        ("oracle-computed-expectation", 0, SUITE)], out


def test_the_cli_reports_the_class(tmp):
    d = Path(tmp)
    f = d / "answer.md"
    f.write_text("```chunks\n" + json.dumps(block(EDITED, "match " + REF)) + "\n```\n",
                 encoding="utf-8")
    r = subprocess.run([sys.executable, "-s", str(ROOT / "scripts" / "scope_lint.py"),
                        str(f), "--task", "t752", "--no-run"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert "oracle-computed-expectation" in r.stdout, (r.returncode, r.stdout, r.stderr)
    assert REF in r.stdout, r.stdout


def main():
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for fn in tests:
        try:
            with tempfile.TemporaryDirectory(prefix="t752-") as d:
                fn(Path(d))
        except Exception as e:
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(e).__name__, e))
        else:
            print("ok %s" % fn.__name__)
    print("%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
