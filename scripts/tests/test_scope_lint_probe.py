#!/usr/bin/env python3
"""t1212 -- scope_lint must refuse a UI-affecting chunk that carries no verification_probe.

The incident (workspace/rca/593df3175795.md): a chat-button change on the Hub was accepted
against a screenshot. The screenshot needs the desktop browser pane, which a human can decline,
so the acceptance test became unanswerable. The fix makes the picture unnecessary: a chunk whose
file renders a web page must ship a text-mode probe -- {"command": ..., "expected": ...} -- and
scope_lint refuses the brief that has none, so 'I have not seen it on screen' is unstatable.

Run: python3 scripts/tests/test_scope_lint_probe.py

Red on main because scope_lint.static() produces no 'brief-probe-missing' finding: the rule does
not exist yet. The rule is decided from the text of the chunk's OWN file, so every fixture here
is a file this test writes inside its own temp dir -- no network, no live Hub, no live state.
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import scope_lint

PROBE_CLASS = "brief-probe-missing"

# The real acceptance command, as the RCA states it at workspace/rca/593df3175795.md:79.
# Real constants, so a build that matches on any string cannot pass this suite.
HUB_PROBE = {"command": "curl -s http://100.64.0.10:8091/review",
             "expected": "Chat"}

# A file that renders a web page: the Hub's /review page, as the task describes it.
UI_SRC = ('"""Serves the review page on the Hub at port 8091."""\n'
          'PAGE = "<html><body><button id=chat>Chat</button></body></html>"\n')
# A file that renders nothing a person sees: prints rows to stdout. The rule must ignore it.
PLAIN_SRC = ('"""Prints a table to stdout. No web page, no browser, nothing on screen."""\n'
             'def render_rows(n):\n'
             '    return [str(i) for i in range(n)]\n')

_NO_PROBE = object()


def make_repo(root):
    """The two files the rule reads, written inside this test's own temp dir."""
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "ui_page.py").write_text(UI_SRC, encoding="utf-8")
    (root / "scripts" / "plain_tool.py").write_text(PLAIN_SRC, encoding="utf-8")


def chunk(name, probe=_NO_PROBE):
    c = {"kind": "implementation", "file": "scripts/" + name,
         "target": "scripts/tests/test_scope_lint_probe.py",
         "single_change": "make the review page render the chat button",
         "what_is_wrong": "the change was accepted against a screenshot",
         "done_when": "python3 scripts/tests/test_scope_lint_probe.py exits 0",
         "related_entries": ["t1212", "brief-unsatisfiable"]}
    if probe is not _NO_PROBE:
        c["verification_probe"] = probe
    return c


def probe_findings(chunks, root):
    """The new class only, as emitted by the public entry point static().

    Asserted through static() on purpose: a probe_findings() that exists but is never called
    from static() leaves the rule dead code, and the findings of every other check keep their
    existing shape, so static() is the surface this change has to attach to.
    """
    return [f for f in scope_lint.static(chunks, root) if f[0] == PROBE_CLASS]


def test_ui_chunk_with_no_probe_is_refused():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        make_repo(root)
        fs = probe_findings([chunk("ui_page.py")], root)
        assert len(fs) == 1, f"expected exactly one {PROBE_CLASS} finding, got {fs!r}"
        cls, index, fname, evidence = fs[0]
        assert cls == PROBE_CLASS, f"the finding is class {cls!r}, not {PROBE_CLASS!r}"
        assert index == 0, f"the finding is attached to chunk {index}, not to the UI chunk 0"
        assert fname == "scripts/ui_page.py", f"the finding names {fname!r}, not the chunk's own file"
        assert "verification_probe" in evidence, (
            f"the evidence must name the field the builder has to add: {evidence!r}")


def test_ui_chunk_with_a_well_formed_probe_is_let_through():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        make_repo(root)
        fs = probe_findings([chunk("ui_page.py", HUB_PROBE)], root)
        assert fs == [], f"a probe with a real command and a real expected must satisfy the rule: {fs!r}"


def test_probe_with_a_blank_command_is_refused():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        make_repo(root)
        blank = {"command": "   ", "expected": HUB_PROBE["expected"]}
        fs = probe_findings([chunk("ui_page.py", blank)], root)
        assert len(fs) == 1, f"a whitespace command is not a machine-checkable probe: {fs!r}"
        assert "command" in fs[0][3], f"the evidence must name the missing field: {fs[0][3]!r}"


def test_probe_with_a_blank_expected_is_refused():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        make_repo(root)
        blank = {"command": HUB_PROBE["command"], "expected": ""}
        fs = probe_findings([chunk("ui_page.py", blank)], root)
        assert len(fs) == 1, f"a probe with no expected value answers no question: {fs!r}"
        assert "expected" in fs[0][3], f"the evidence must name the missing field: {fs[0][3]!r}"


def test_a_chunk_that_renders_nothing_on_screen_is_untouched():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        make_repo(root)
        fs = probe_findings([chunk("plain_tool.py")], root)
        assert fs == [], (
            f"the probe rule fired on a file that renders no web page; a gate that blocks every "
            f"brief is bypassed with --no-verify inside a week: {fs!r}")


def test_the_class_is_registered_in_scope_lint():
    assert PROBE_CLASS in scope_lint.CHECK_NAMES, (
        f"{PROBE_CLASS} is not in scope_lint.CHECK_NAMES, so scope_lint.record() can never log a "
        f"pass for it and the guard is invisible in scope_quality: {scope_lint.CHECK_NAMES!r}")


if __name__ == "__main__":
    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    _failed = 0
    for _t in _tests:
        try:
            _t()
            print("PASS " + _t.__name__)
        except AssertionError as _e:
            _failed += 1
            print("FAIL " + _t.__name__ + ": " + str(_e))
    print(f"{len(_tests) - _failed}/{len(_tests)} passed")
    sys.exit(1 if _failed else 0)
