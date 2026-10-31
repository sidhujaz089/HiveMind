#!/usr/bin/env python3
"""t1062 -- coder.py must refuse a patch whose emitted block leaves the target file's
COMPUTED indent profile (RCA 81b58c23c5de, item 1).

RED ON MAIN FOR THE STATED REASON: coder.py has no `indent_drift` and no `indent_gate`. qwen3.8's
line-range edits re-indented blocks to 10 spaces where the surrounding scope uses 8 (transcript
6cc34e3b:3416) and produced a Python IndentationError on t018 -- the error quoted in py_syntax's
own docstring. coder.py checks that a Python file still PARSES (py_syntax) and normalises a
UNIFORMLY shifted block (_reindent, which returns the block untouched when its first line already
matches the target and declines tab/space mixtures), but nothing compares an emitted block's
indentation with the enclosing scope the harness already holds. The residues _reindent declines are
the class this suite pins.

Hermetic by construction: pure functions over strings, one tempfile.TemporaryDirectory, no model
calls, no live server, no subprocess, no fixed /tmp path, nothing written outside a temp dir.
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
import coder  # noqa: E402


def _need(name):
    fn = getattr(coder, name, None)
    if not callable(fn):
        raise AssertionError(
            "coder.%s is not defined. The acceptance-path indent gate is the change this oracle "
            "asks for (RCA 81b58c23c5de): a line-range patch whose emitted block lands off the "
            "target file's own indent profile must be refused." % name)
    return fn


# ---- fixtures. Every case carries DISTINCT text: one repeated placeholder string would let an
# ---- implementation pass while telling an 8-indent block and a 10-indent block apart by nothing.

RENDER_BEFORE = (
    'def render(rows):\n'
    '    out = []\n'
    '    for row in rows:\n'
    '        out.append(row["name"])\n'
    '        out.append(row["score"])\n'
    '    return out\n'
)
RENDER_TEN = (
    'def render(rows):\n'
    '    out = []\n'
    '    for row in rows:\n'
    '          out.append("name=" + row["name"])\n'
    '          out.append("score=" + row["score"])\n'
    '    return out\n'
)
RENDER_OFFGRID = (
    'def render(rows):\n'
    '    out = []\n'
    '    for row in rows:\n'
    '        out.append("name=" + row["name"])\n'
    '          out.append("score=" + row["score"])\n'
    '    return out\n'
)
RENDER_CLEAN = (
    'def render(rows):\n'
    '    out = []\n'
    '    for row in rows:\n'
    '        out.append("name=" + row["name"])\n'
    '        out.append("score=" + row["score"])\n'
    '    return out\n'
)
RENDER_ALIGNED = (
    'def render(rows):\n'
    '    out = []\n'
    '    for row in rows:\n'
    '        out.append("value=" + row["score"] +\n'
    '         row["label"] + row["extra"])\n'
    '    return out\n'
)
DOC_BEFORE = (
    'def render(rows):\n'
    '    out = []\n'
    '    for row in rows:\n'
    '        out.append(row["name"])\n'
    '        out.append(row["score"])\n'
    '        out.append(row["tag"])\n'
    '    return out\n'
)
DOC_INSIDE_STRING = (
    'def render(rows):\n'
    '    out = []\n'
    '    for row in rows:\n'
    '        out.append("""label\n'
    '      value = row["score"]\n'
    '      score""")\n'
    '    return out\n'
)
DEEP_BEFORE = (
    'def render(rows):\n'
    '    for row in rows:\n'
    '        for key in row:\n'
    '            out.append(key)\n'
    '            out.append(str(key))\n'
    '    return out\n'
)
DEEP_CLEAN = (
    'def render(rows):\n'
    '    for row in rows:\n'
    '        for key in row:\n'
    '            out.append(str(key))\n'
    '            out.append(key + "=" + str(key))\n'
    '    return out\n'
)
DEEP_OFFGRID = (
    'def render(rows):\n'
    '    for row in rows:\n'
    '        for key in row:\n'
    '            out.append(str(key))\n'
    '              out.append(key + "=" + str(key))\n'
    '    return out\n'
)
TAB_BEFORE = (
    'def render(rows):\n'
    '\tout = []\n'
    '\tfor row in rows:\n'
    '\t\tout.append(row["name"])\n'
    '\t\tout.append(row["score"])\n'
    '\treturn out\n'
)
TAB_CLEAN = (
    'def render(rows):\n'
    '\tout = []\n'
    '\tfor row in rows:\n'
    '\t\tout.append("name=" + row["name"])\n'
    '\t\tout.append("score=" + row["score"])\n'
    '\treturn out\n'
)
TAB_MIXED = (
    'def render(rows):\n'
    '\tout = []\n'
    '\tfor row in rows:\n'
    '\t\tout.append("name=" + row["name"])\n'
    '\t  \tout.append("score=" + row["score"])\n'
    '\treturn out\n'
)
LONG = ('          out.append("score=" + row["score"] + row["label"] + row["extra"]'
        ' + row["note"] + row["tag"] + row["label2"] + row["extra2"])')
RENDER_TEN_LONG = (
    'def render(rows):\n'
    '    out = []\n'
    '    for row in rows:\n'
    '        out.append("name=" + row["name"])\n'
    + LONG + '\n'
    '    return out\n'
)


def test_indent_drift_refuses_a_block_emitted_at_10_spaces_in_an_8_space_scope():
    f = _need("indent_drift")
    out = f(RENDER_BEFORE, RENDER_TEN, 4, 5)
    assert isinstance(out, list), "indent_drift must return a list of findings, got %r" % type(out)
    assert out, ("an 8-space block replaced by a 10-space block, in a file whose surrounding scope "
                 "is 8, is the recorded defect -- and it was accepted")
    joined = "\n".join(out)
    assert "8" in joined and "10" in joined, (
        "the finding must name both depths: the file's enclosing scope (8) and the emitted depth "
        "(10). Got: %r" % joined)
    assert 'out.append("score=" + row["score"]' in joined, (
        "the finding must quote the EMITTED line, whose text is distinct from the line it "
        "replaces, so a generic message cannot pass: %r" % joined)


def test_indent_drift_accepts_a_block_at_the_depth_the_file_uses():
    f = _need("indent_drift")
    assert f(RENDER_BEFORE, RENDER_CLEAN, 4, 5) == [], (
        "a correctly indented replacement must NOT be refused -- a gate that fires on well-formed "
        "patches is routed around with --no-verify inside a week")


def test_indent_drift_refuses_an_interior_line_off_the_files_computed_grid():
    f = _need("indent_drift")
    out = f(RENDER_BEFORE, RENDER_OFFGRID, 4, 5)
    assert out, ("the block's second line sits at 10 where the scope is 8 and the file's own "
                 "computed step is 4. _reindent leaves this shape untouched: its first line already "
                 "matches the target, so it returns the block unchanged. Got: %r" % out)


def test_indent_drift_allows_a_bracket_aligned_continuation_line():
    f = _need("indent_drift")
    out = f(RENDER_BEFORE, RENDER_ALIGNED, 4, 5)
    assert out == [], ("the second line of the block continues a bracket left open on the first, so "
                       "its column is set by alignment, not by the file's indent step. Refusing it "
                       "is a false positive on real code: %r" % out)


def test_indent_drift_allows_text_inside_a_multiline_string():
    f = _need("indent_drift")
    out = f(DOC_BEFORE, DOC_INSIDE_STRING, 4, 6)
    assert out == [], ("the replaced block opens a triple-quoted string on line 4 and closes it on "
                       "line 6: line 5 sits at column 6 INSIDE that string, so its whitespace is "
                       "content, not indentation. coder._string_spans is the existing helper that "
                       "knows this. Got: %r" % out)


def test_indent_drift_is_computed_from_the_file_not_a_stored_constant():
    """The same arithmetic at a deeper scope. A gate carrying a stored rule table ('8 is the body
    depth', 'the step is 4') passes one of these two assertions and rots at the first file written
    in a new style."""
    f = _need("indent_drift")
    assert f(DEEP_BEFORE, DEEP_CLEAN, 4, 5) == [], (
        "in a file whose enclosing scope is 12, 12-indent lines ARE the profile")
    out = f(DEEP_BEFORE, DEEP_OFFGRID, 4, 5)
    assert out, ("14 where the scope is 12 is the same off-the-file's-step arithmetic as 10 where "
                 "the scope is 8. Got: %r" % out)


def test_indent_drift_refuses_a_line_that_mixes_tabs_and_spaces():
    f = _need("indent_drift")
    assert f(TAB_BEFORE, TAB_MIXED, 4, 5), (
        "a tab-indented file whose replacement line leads with tabs AND spaces is the shape "
        "_reindent refuses to normalise, so it is this gate's to name")


def test_indent_drift_accepts_a_tab_indented_block_in_a_tab_file():
    f = _need("indent_drift")
    assert f(TAB_BEFORE, TAB_CLEAN, 4, 5) == [], (
        "tab-indented replacement lines in a tab-indented file are clean -- the gate must not "
        "invent a defect where the file is self-consistent")


def test_indent_gate_refuses_a_block_emitted_at_10_spaces_against_a_pre_state_of_8():
    f = _need("indent_gate")
    rel = "src/render.py"
    ops = [{"path": rel, "from": 4, "to": 5, "new": (
        '          out.append("name=" + row["name"])\n'
        '          out.append("score=" + row["score"])')}]
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "src").mkdir()
        (root / rel).write_text(RENDER_TEN, encoding="utf-8")
        ok, msg = f(root, ops, {rel: RENDER_BEFORE})
        assert ok is False, ("a block whose every line sits at 10 in a file whose scope is 8 must "
                             "be refused (RCA 81b58c23c5de): ok=%r msg=%r" % (ok, msg))
        assert isinstance(msg, str) and msg.strip(), "a refusal must say why, in full"
        assert rel in msg, "the refusal must name the file: %r" % msg
        assert "8" in msg and "10" in msg, "the refusal must name both depths: %r" % msg
        assert 'out.append("name=" + row["name"])' in msg, (
            "the refusal must quote the emitted lines so the model can re-emit them: %r" % msg)


def test_indent_gate_refuses_an_off_grid_line_and_quotes_a_long_line_uncut():
    f = _need("indent_gate")
    rel = "src/render.py"
    ops = [{"path": rel, "from": 4, "to": 5, "new": (
        '        out.append("name=" + row["name"])\n'
        + LONG)}]
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "src").mkdir()
        (root / rel).write_text(RENDER_TEN_LONG, encoding="utf-8")
        ok, msg = f(root, ops, {rel: RENDER_BEFORE})
        assert ok is False, (
            "the block's first line is right and its interior line sits at 10 while the scope is "
            "8 -- the residue _reindent cannot fix. ok=%r msg=%r" % (ok, msg))
        assert len(LONG) > 90, "the fixture line is long on purpose: a [:N] slice cannot pass"
        assert LONG in msg, (
            "the offending line must appear WHOLE, leading whitespace and full length included -- "
            "a truncated record is the defect docs/skills/never-truncate-a-record exists for: %r" % msg)
        assert "5" in msg, "the refusal must name the offending line's number: %r" % msg


def test_indent_gate_lets_a_correctly_indented_block_through():
    f = _need("indent_gate")
    rel = "src/render.py"
    ops = [{"path": rel, "from": 4, "to": 5, "new": (
        '        out.append("name=" + row["name"])\n'
        '        out.append("score=" + row["score"])')}]
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "src").mkdir()
        (root / rel).write_text(RENDER_CLEAN, encoding="utf-8")
        ok, msg = f(root, ops, {rel: RENDER_BEFORE})
        assert ok is True, "a patch that keeps the file's own profile must not be refused: %r" % msg
        assert msg == "", "a passing gate returns an empty message: %r" % msg


def test_indent_gate_ignores_what_it_cannot_measure():
    f = _need("indent_gate")
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "src").mkdir()
        (root / "src/render.py").write_text(RENDER_BEFORE, encoding="utf-8")
        ok, msg = f(root, [{"path": "src/render.py",
                            "old": 'out.append(row["name"])',
                            "new": 'out.append(row["name"].upper())'}], {})
        assert ok is True, "an anchored op has no emitted indentation to measure: %r" % msg
        ok2, msg2 = f(root, [{"path": "src/gone.py", "from": 1, "to": 2, "new": "x = 1"}], {})
        assert ok2 is True, "a path that is not a file must be skipped, not crashed: %r" % msg2
        ok3, msg3 = f(root, [{"path": "src/render.py", "from": 4, "to": 5,
                             "new": '        out.append("x")'}], {})
        assert ok3 is True, ("with no pre-state for the file in the baseline the gate must not "
                             "invent one: %r" % msg3)


def test_the_indent_gate_is_called_from_the_acceptance_path():
    """Detection without repair is just documentation: a gate that exists and is never called
    passes every suite, so the call site is asserted, not just the definition."""
    _need("indent_gate")
    src = (REPO / "projects" / "local-inference" / "coder.py").read_text(encoding="utf-8")
    assert src.count("indent_gate(") >= 2, (
        "indent_gate is defined and nothing calls it, so no patch is ever refused by it. Call it "
        "in the attempt loop the way library_gate(root, ops, before) is called, spend the attempt "
        "on a False, and feed its message back to the model.")
    assert src.count("indent_drift(") >= 2, (
        "indent_gate must call indent_drift: a gate that recomputes nothing is a stub")


if __name__ == "__main__":
    names = sorted(n for n, v in globals().items() if n.startswith("test_") and callable(v))
    failed = []
    for name in names:
        try:
            globals()[name]()
            print("ok   %s" % name)
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print("FAIL %s: %s: %s" % (name, type(e).__name__, e))
    print("%d/%d passed" % (len(names) - len(failed), len(names)))
    sys.exit(1 if failed else 0)
