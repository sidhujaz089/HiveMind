"""t989: coder.py must honour the dispatch's file scope when it WRITES, must not hand the model
a file the brief scoped out, and must leave a line-range edit as the answer when a file is too
big to re-emit whole.

Run: python3 scripts/tests/test_coder_scope_apply.py
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CODER = REPO / "projects" / "local-inference" / "coder.py"


def load_coder():
    """Import coder.py from the path it really lives on. A top-level side effect shows up here
    as a traceback with a real cause, not as a swallowed pass."""
    spec = importlib.util.spec_from_file_location("coder_under_test", CODER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FROZEN = "test_frozen_oracle.py"
ALLOWED = ["app.py"]


def sandbox():
    """A temp root with ONE editable file and ONE frozen oracle -- the shapes of p325-t781,
    where --files named askmemory.py and the model wrote the frozen suite it is judged by.
    Every path here is under a temp dir; the repository is never written."""
    root = Path(tempfile.mkdtemp())
    (root / "app.py").write_text("VALUE = 1\nRESULT = 2\nTAIL = 3\n", encoding="utf-8")
    (root / FROZEN).write_text("def test_x():\n    assert VALUE == 1\n", encoding="utf-8")
    return root


def test_a_frozen_file_cannot_be_rewritten():
    """The defect: --files named ONE file, the model emitted a range op for the frozen oracle,
    and coder.py applied it (attempts 1-2: \"asked for 2 file(s), gave 2 new\")."""
    c = load_coder()
    root = sandbox()
    before = (root / FROZEN).read_text(encoding="utf-8")
    ok, detail = c.apply_ops(root, [{"path": FROZEN, "from": 2, "to": 2}], ALLOWED)
    assert ok is False, "an op against a file outside --files must be refused, got " + repr((ok, detail))
    assert FROZEN in str(detail), "the refusal must name the file asked for, got " + repr(detail)
    assert "scope" in str(detail).lower(), \
        "it must say the file is outside the dispatch scope, got " + repr(detail)
    assert "room" not in str(detail).lower(), (
        "a scope refusal must not read as a size refusal -- that is how the frozen oracle came "
        "to be filed as context pressure, got " + repr(detail))
    after = (root / FROZEN).read_text(encoding="utf-8")
    assert after == before, "the frozen file on disk must be byte-identical, got " + repr(after)


def test_a_new_file_outside_the_scope_cannot_be_written():
    """Briefs say \"do not create any new file\"; a path that is not the allowed file must not
    appear on disk. Basename matching is not good enough -- it must be the allowed path."""
    c = load_coder()
    root = sandbox()
    ok, detail = c.apply_ops(root, [{"path": "test_new_suite.py", "from": 1, "to": 1}], ALLOWED)
    assert ok is False, "a path that is not in --files must be refused, got " + repr((ok, detail))
    assert "test_new_suite.py" in str(detail), repr(detail)
    assert not (root / "test_new_suite.py").exists(), "it must not be created on disk"


def test_an_allowed_range_edit_is_still_accepted():
    """The gate must not swallow the allowed file: the brief's whole job is a range edit inside
    the one file named by --files, which apply_ops already accepts."""
    c = load_coder()
    root = sandbox()
    ok, detail = c.apply_ops(
        root, [{"path": "app.py", "from": 2, "to": 2, "new": "RESULT = 99"}], ALLOWED)
    assert ok is True, "an op against the allowed file must be applied, got " + repr((ok, detail))


def test_ops_are_unscoped_when_no_allowlist_is_given():
    """apply_ops is also called with two arguments by the twin build. A default of None must
    keep today's behaviour, not refuse every op."""
    c = load_coder()
    root = sandbox()
    ok, detail = c.apply_ops(root, [{"path": "app.py", "from": 2, "to": 2, "new": "RESULT = 99"}])
    assert ok is True, "no allowlist passed must mean no scope check, got " + repr((ok, detail))


def test_pressure_guidance_names_the_range_edit_that_is_answerable():
    """The model was shown lines 1-821 of a 1,088-line file and asked for the rest. It cannot
    re-emit a file that does not fit the window, so the reply must point at the range edit the
    harness accepts -- not at \"work from what you already have\"."""
    c = load_coder()
    text = c.pressure_guidance("projects/local-inference/askmemory.py", 1088, 821)
    assert "askmemory.py" in text, text
    assert "822" in text, "must name the first line not shown: " + text
    assert "1088" in text, "must bound the range by the file's real length: " + text
    assert "range" in text.lower(), "must name the line-range edit: " + text
    assert "Work from what you already have" not in text, \
        "the sentence that left the model no valid path must be gone: " + text


def test_pressure_guidance_says_so_when_the_file_is_already_shown_whole():
    c = load_coder()
    text = c.pressure_guidance("app.py", 40, 40)
    assert "shown in full" in text, text
    assert "range" in text.lower(), text
    assert "Work from what you already have" not in text, text


if __name__ == "__main__":
    _fns = [v for k, v in sorted(globals().items())
            if k.startswith("test_") and callable(v) and k != "test"]
    _fails = []
    for _fn in _fns:
        try:
            _fn()
            print("ok   " + _fn.__name__)
        except Exception as _e:                                   # noqa: BLE001
            _fails.append(_fn.__name__)
            print("FAIL " + _fn.__name__ + ": " + repr(_e))
    print("\n%d/%d passed" % (len(_fns) - len(_fails), len(_fns)))
    sys.exit(1 if _fails else 0)
