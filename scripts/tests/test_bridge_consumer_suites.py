#!/usr/bin/env python3
"""t1117 -- bridge.py must name a changed file's existing test consumers in the brief it writes.

WHY THIS EXISTS. A brief's BEHAVIOUR THAT MUST STILL HOLD section is assembled in bridge.py's
main() from ONE hand-typed field, behaviour_oracle. Nothing in the harness asks the repository
which existing suites read the file a chunk edits, so that section is only as complete as the
brief author's memory. scripts/tests/ is the only test directory (bridge.validate requires every
oracle to live there), so the repository can answer the question mechanically: grep it for the
changed file's basename. projects/local-inference/models/FLOW.json is referenced 139 times across
34 files in scripts/tests/ (measured 2026-10-13 with `grep -ho "FLOW.json" scripts/tests/*.py`),
so a chunk editing it has dozens of consumers and a hand-written list is all that stands between
the builder and a suite it was never told to keep green.

The fix is computed, not remembered. consumer_suites() scans scripts/tests/ for suites that
mention the changed file's basename as a whole token at the end of a path, and brief_text() -- the
function this scope lifts out of main() so the emitted text can be tested without dispatching a
model -- puts that list in the preservation section, alongside the author's own text.

RED TODAY: scripts/bridge.py defines neither consumer_suites nor brief_text, so every test below
that calls them raises AttributeError on the imported bridge module, and the last test fails
because main() still builds the brief inline: its body contains the literal text BEHAVIOUR THAT
MUST STILL HOLD and never calls brief_text. That is exactly the change this scope asks for, and
nothing else here is red for its own reason -- bridge is imported by path from the real file, the
fixtures are built under tempfile.TemporaryDirectory, and this suite reads no live repository state.

The consumer names are the two real suites named in t1117's evidence; their bodies are synthetic,
which is the point: consumer_suites' contract is pinned by construction, not by the shape of
today's files.
"""
import importlib.util
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("bridge_under_test", ROOT / "scripts" / "bridge.py")
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)

FLOW = "projects/local-inference/models/FLOW.json"

CHUNK = {
    "kind": "implementation",
    "file": FLOW,
    "target": "scripts/tests/test_flow_preferences_node.py",
    "single_change": "append one procedure node to the procedures array",
    "what_is_wrong": "the chart cannot address a standing rule",
    "done_when": "python3 scripts/tests/test_flow_preferences_node.py exits 0",
    "related_entries": ["brief-unsatisfiable"],
}


def _fake_repo(tmp):
    """A throwaway repo: two suites read FLOW.json, one reads my_app.py, one reads nothing."""
    tests = Path(tmp) / "scripts" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_flow_preferences_node.py").write_text(
        'data = json.loads((REPO / "FLOW.json").read_text())\n', encoding="utf-8")
    (tests / "test_flow_procedure_node.py").write_text(
        'FLOW = "projects/local-inference/models/FLOW.json"\n', encoding="utf-8")
    (tests / "test_my_app.py").write_text('TARGET = "scripts/my_app.py"\n', encoding="utf-8")
    (tests / "test_unrelated.py").write_text('print("nothing here")\n', encoding="utf-8")
    return Path(tmp)


def test_consumer_suites_names_the_suites_that_read_the_file():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(tmp)
        assert sorted(bridge.consumer_suites(repo, FLOW)) == [
            "test_flow_preferences_node.py", "test_flow_procedure_node.py"]


def test_consumer_suites_is_empty_when_nothing_reads_the_file():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(tmp)
        assert list(bridge.consumer_suites(repo, "docs/UNHELD.md")) == []


def test_consumer_suites_is_empty_when_there_is_no_tests_directory():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(bridge.consumer_suites(Path(tmp), FLOW)) == []


def test_consumer_suites_does_not_match_a_longer_filename():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(tmp)
        # "app.py" sits inside "my_app.py". A plain substring test reports test_my_app.py as a
        # consumer of scripts/app.py, so the brief would carry a preservation constraint for a file
        # that suite does not read. The basename must match as a whole token, at the end of a path.
        assert list(bridge.consumer_suites(repo, "scripts/app.py")) == []
        assert list(bridge.consumer_suites(repo, "scripts/my_app.py")) == ["test_my_app.py"]


def test_brief_text_names_the_consumers_in_the_preservation_section():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(tmp)
        text = bridge.brief_text("t1117", CHUNK, repo)
        assert text.startswith("TASK: t1117")
        assert text.count("BEHAVIOUR THAT MUST STILL HOLD") == 1
        head = text.index("BEHAVIOUR THAT MUST STILL HOLD")
        assert "CONSUMER SUITES" in text
        # rindex, not index: the target's name also appears in DONE WHEN and FROZEN ORACLE, both of
        # which sit above the section. The consumer list must be the last mention of it.
        assert text.rindex("test_flow_preferences_node.py") > head
        assert text.index("test_flow_procedure_node.py") > head
        assert "test_unrelated.py" not in text
        assert "DONE WHEN python3 scripts/tests/test_flow_preferences_node.py exits 0" in text
        assert "FROZEN ORACLE: scripts/tests/test_flow_preferences_node.py" in text
        assert "ONE file: " + FLOW + "." in text
        assert "EDIT PROTOCOL" in text
        assert text.endswith("\n")


def test_brief_text_states_the_consumer_list_as_one_exact_sentence():
    """Assert the value, not that a word appears: a brief that mentions the files in prose the
    builder chooses satisfies a presence check. This is the sentence the builder must emit."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(tmp)
        text = bridge.brief_text("t1117", CHUNK, repo)
        assert ("CONSUMER SUITES (they read " + FLOW + "; they must stay green): "
                "test_flow_preferences_node.py, test_flow_procedure_node.py") in text


def test_brief_text_creates_the_section_when_the_author_wrote_none():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(tmp)
        chunk = dict(CHUNK)
        chunk.pop("behaviour_oracle", None)
        text = bridge.brief_text("t1058", chunk, repo)
        assert "BEHAVIOUR THAT MUST STILL HOLD" in text
        assert "test_flow_procedure_node.py" in text
        assert text.count("BEHAVIOUR THAT MUST STILL HOLD") == 1


def test_brief_text_keeps_the_authors_preservation_text():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(tmp)
        chunk = dict(CHUNK)
        chunk["behaviour_oracle"] = ("python3 scripts/tests/test_flow_procedure_node.py stays "
                                     "green; proc_env_probe_cycle keeps its name, owner and steps")
        text = bridge.brief_text("t1058", chunk, repo)
        assert "proc_env_probe_cycle keeps its name, owner and steps" in text
        assert "test_flow_preferences_node.py, test_flow_procedure_node.py" in text
        assert text.count("BEHAVIOUR THAT MUST STILL HOLD") == 1


def test_main_emits_its_briefs_through_brief_text():
    """main() must CALL the function. An extracted brief_text that main never calls leaves every
    brief on disk exactly as incomplete as the one that prompted this task. The slice is main's own
    body -- from def main( to the __main__ guard -- because a correct build may define brief_text
    after main, and reading to the end of the file would fail a good implementation."""
    src = (ROOT / "scripts" / "bridge.py").read_text(encoding="utf-8")
    body = src[src.index("def main("):src.index('if __name__ == "__main__":')]
    assert "brief_text(a.task, c, repo)" in body
    assert "BEHAVIOUR THAT MUST STILL HOLD" not in body


if __name__ == "__main__":
    _fails = 0
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("PASS", _name)
            except Exception as _e:            # the runner reports every failure, never swallows
                _fails += 1
                print(f"FAIL {_name}: {type(_e).__name__}: {_e}")
    print(f"{_fails} failure(s)")
    raise SystemExit(1 if _fails else 0)
