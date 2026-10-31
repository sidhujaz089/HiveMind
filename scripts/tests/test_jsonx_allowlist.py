#!/usr/bin/env python3
"""t1421 oracle: model-output parsing is allowed at exactly ONE call site -- scripts/jsonx.py.

RED ON MAIN because scripts/check_workspace.py has no Checker.check_jsonx_allowlist: I read
scripts/check_workspace.py lines 1-434 of 2639 and there is no such def, and a repo-wide search
for "jsonx" in that file hits only a comment at line 1565. On main every test that calls it raises
AttributeError, which the runner below records as a failure, so the suite is red for the missing
change and for nothing else.

Why these assertions, and not a term list: a blacklist of bad shapes is what let scope_queue.py
carry a correct-looking hand-written brace scanner for three days, and let coder.py carry a second
copy of jsonx (docs/skills/planning-for-sustainment, inversion). The gate is computed from the
AST, and this oracle pins BOTH directions -- a second brace-scan site is a finding, a file that
imports jsonx is cleared, and json.loads over a file's contents is NOT a model parse. A gate that
fires on every json.loads in scripts/ is a gate that gets --no-verify'd inside a week.
"""

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_workspace as cw  # noqa: E402

CHECK = "check_jsonx_allowlist"
SENTENCE = "parses model output without importing jsonx"

# The shape scope_queue.py carried before c800f3b9 deleted the fork: first brace to last brace,
# greedy, with re.S. Quoted from scripts/jsonx.py's own docstring, line 4.
BRACE_REGEX = (
    "import re\n"
    "\n"
    "\n"
    "def parse(answer):\n"
    "    m = re.search(r'\\{.*\\}', answer, re.S)\n"
    "    return m.group(0)\n"
)

# The approved pattern: the same brace scan in a file that imports jsonx. Must be CLEAR.
BRACE_REGEX_WITH_JSONX = "import jsonx\n\n" + BRACE_REGEX

# First-brace-to-last-brace SLICING. The bounds are inline calls, not names, so the braces are
# visible in the same AST node -- the check computes them from the expression, not from a name.
BRACE_SLICE = (
    "import json\n"
    "\n"
    "\n"
    "def parse(answer):\n"
    "    return json.loads(answer[answer.find('{'):answer.rfind('}') + 1])\n"
)

# json.loads over a file's contents: not model-output parsing, and NOT to be flagged.
FILE_JSON = (
    "import json\n"
    "from pathlib import Path\n"
    "\n"
    "\n"
    "def read_config(path):\n"
    "    return json.loads(Path(path).read_text(encoding='utf-8'))\n"
)


def _write(tmp, name, source):
    p = Path(tmp) / name
    p.write_text(source, encoding="utf-8")
    return p


def _cited(finding):
    """(path, lineno, rest) of the required format '<path>:<lineno>: <what>'. A finding whose
    citation is not a real line of a real file cannot be acted on, so the format is asserted,
    not assumed."""
    m = re.match(r"^(.*):(\d+): (.*)$", finding, re.S)
    assert m, f"finding is not '<path>:<line>: <what>': {finding!r}"
    return (ROOT / m.group(1)).resolve(), int(m.group(2)), m.group(3)


def _findings(name, source):
    """Run the gate over one synthetic file. tempfile only -- never a fixed /tmp path, and the
    directory is removed, so running the oracle leaves the worktree untouched."""
    with tempfile.TemporaryDirectory(prefix="jsonx-allowlist-") as td:
        p = _write(td, name, source)
        return [f for f in cw.Checker(ROOT).check_jsonx_allowlist([p]) if p.name in f]


def test_the_check_exists():
    assert callable(getattr(cw.Checker, CHECK, None)), \
        f"Checker.{CHECK} does not exist -- this is the gate t1421 asks for"


def test_flags_a_forked_brace_scan_regex():
    findings = _findings("fork_regex.py", BRACE_REGEX)
    assert len(findings) == 1, \
        f"one brace-scan regex site must produce exactly one finding, got: {findings}"
    path, lineno, rest = _cited(findings[0])
    src = path.read_text(encoding="utf-8").splitlines()
    assert 1 <= lineno <= len(src), \
        f"finding cites line {lineno}, past the end of a {len(src)}-line file: {findings[0]}"
    assert "re.search" in src[lineno - 1], \
        f"the cited line {lineno} is not the brace-scan regex: {src[lineno - 1]!r}"
    assert SENTENCE in rest, f"a finding must say what is wrong, in words: {findings[0]}"
    assert "brace-scan regex" in rest, \
        f"a finding must name the shape it found, so the fixer knows what to replace: {findings[0]}"


def test_a_jsonx_import_clears_the_site():
    findings = _findings("imports_jsonx.py", BRACE_REGEX_WITH_JSONX)
    assert findings == [], \
        f"a brace scan in a file that imports jsonx IS the approved pattern: {findings}"


def test_flags_a_forked_brace_scan_slice():
    findings = _findings("fork_slice.py", BRACE_SLICE)
    assert len(findings) == 1, \
        f"one brace-scan slice must produce exactly one finding, got: {findings}"
    path, lineno, rest = _cited(findings[0])
    src = path.read_text(encoding="utf-8").splitlines()
    assert 1 <= lineno <= len(src), \
        f"finding cites line {lineno}, past the end of a {len(src)}-line file: {findings[0]}"
    assert "answer[" in src[lineno - 1], \
        f"the cited line {lineno} is not the slice: {src[lineno - 1]!r}"
    assert "brace-scan slice" in rest, \
        f"a finding must name the shape it found: {findings[0]}"


def test_json_loads_on_a_file_is_not_a_model_parse():
    findings = _findings("reads_a_file.py", FILE_JSON)
    assert findings == [], \
        ("json.loads over a file's contents is not model-output parsing; flagging it fires the "
         f"gate on every reader in scripts/ and it gets --no-verify'd: {findings}")


def test_jsonx_itself_is_the_one_approved_site():
    src = ROOT / "scripts" / "jsonx.py"
    assert src.is_file(), f"{src} missing -- the allowlist's one approved path"
    findings = [f for f in cw.Checker(ROOT).check_jsonx_allowlist([src]) if "jsonx.py" in f]
    assert findings == [], f"scripts/jsonx.py is the approved call site: {findings}"


def test_the_tree_scan_is_computed_from_source_not_rehearsed():
    """files=None scans the tracked tree. A stub that returns canned findings fails here: every
    citation must be a real line of a real file, and that line must carry the braces."""
    findings = cw.Checker(ROOT).check_jsonx_allowlist(None)
    assert isinstance(findings, list), f"expected a list of findings, got {type(findings).__name__}"
    for f in findings:
        path, lineno, rest = _cited(f)
        assert path.is_file(), f"finding cites a file that is not on disk: {f}"
        src = path.read_text(encoding="utf-8", errors="replace").splitlines()
        assert 1 <= lineno <= len(src), f"finding cites line {lineno}, past the end of {path}: {f}"
        if "brace-scan" in rest:
            line = src[lineno - 1]
            assert "{" in line and "}" in line, \
                f"a brace-scan finding must cite a line carrying the braces it flags: {f} -> {line!r}"
            assert "jsonx" in rest, f"a finding must name its own cure: {f}"
            assert path.resolve() != (ROOT / "scripts" / "jsonx.py").resolve(), \
                f"scripts/jsonx.py is the ONE approved call site: {f}"


def test_the_check_is_called_by_the_run():
    """A check defined and never called is documentation, not a gate. This is the wiring the RCA
    asks for: 'attached to check_workspace.py so session_end.py blocks it'."""
    text = (ROOT / "scripts" / "check_workspace.py").read_text(encoding="utf-8")
    assert text.count(f"def {CHECK}(") == 1, f"expected exactly one definition of {CHECK}"
    assert text.count(f".{CHECK}(") >= 1, \
        f"{CHECK} is defined but never called from the run: a check no session runs catches nothing"


def _main():
    names = [n for n in sorted(globals()) if n.startswith("test_") and callable(globals()[n])]
    failed = []
    for n in names:
        try:
            globals()[n]()
            print(f"PASS  {n}")
        except Exception as e:
            failed.append(n)
            print(f"FAIL  {n}: {type(e).__name__}: {e}")
    print(f"\n{len(names) - len(failed)}/{len(names)} passed"
          + ("" if not failed else " -- FAILING: " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
