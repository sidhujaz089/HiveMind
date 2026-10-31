#!/usr/bin/env python3
"""t1181 oracle: the fit check names the state it found.

scripts/check_working_set.py:93-95 reports every "nothing to add up" case with one
sentence -- "NOTHING CHECKED: SERVING.json declares no production lanes -- this is not
a fit" -- and exit 2. Two of the ways in are not that state:

  * the lane IS declared, and its model is pinned to a server on ANOTHER device. The
    2026-10-13 cross-device filter (check_working_set.py:64-68) leaves it out of this
    box's total, so this box has nothing resident and there is nothing to overflow.
  * the lane names a role the roles block does not have, so it resolves to no tag at
    all. That is a config gap, and it is not "no lanes declared" either.

The live case that filed t1181 is the first: projects/local-inference/models/SERVING.json
:31-33 declares production_lanes ["coder"]; its tag gemma4:e4b is pinned to
ollama-thinkpad (REGISTRY.json, SERVERS.json device "thinkpad"). The check called that
"declares no production lanes" and exited 2, and check_workspace.py's wrapper
(check_workspace.py:2330-2340: exit 0 PASS, an EMPTY lane list WARN, anything else FAIL)
turned the 2 into the FAIL that filed this task.

So the check must keep three distinct verdicts, and this suite pins all three --
including the two that must NOT go quiet, so a fix cannot make it vacuously green.

Fakes only: temp-dir repos, no live SERVING.json/REGISTRY.json, no /proc (box passed
explicitly), no model calls, no server.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_working_set as cws  # noqa: E402

BOX = 121.0        # a stand-in for the GX10; /proc is never read
ON_BOX = "gx10"
OFF_BOX = "thinkpad"


def tree(root, lanes, roles, base_models, servers):
    """A throwaway models tree: SERVING.json, REGISTRY.json, SERVERS.json.

    REGISTRY.json uses the live file's shape ({"base_models": [...]} with tag / weight_gb
    / server) so a build that reads a shape the live registry does not have fails here.
    """
    models = Path(root) / "projects" / "local-inference" / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "SERVING.json").write_text(
        json.dumps({"roles": roles, "production_lanes": lanes}), encoding="utf-8")
    (models / "REGISTRY.json").write_text(
        json.dumps({"base_models": base_models}), encoding="utf-8")
    (models / "SERVERS.json").write_text(
        json.dumps({"servers": servers}), encoding="utf-8")
    return root


def run(root):
    """check() as the hourly check runs it: the exit code and the line it printed."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cws.check(root, box_gb=BOX)
    return rc, out.getvalue()


def test_lane_pinned_to_another_device_is_not_a_fit_failure():
    """The case that filed t1181. One declared lane, resident on the ThinkPad eGPU.

    Nothing on THIS box is at risk, so the fit is not a failure -- exit 0 -- and the
    line must say what it found. It must NOT claim the config declares no lanes: it
    declares one. Red on main: exit 2, printing the false sentence.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = tree(tmp, ["coder"], {"coder": {"tag": "gemma4:e4b"}},
                    [{"tag": "gemma4:e4b", "weight_gb": 9.6, "server": "ollama-thinkpad"}],
                    {"ollama-thinkpad": {"device": OFF_BOX}})
        rc, out = run(root)
    assert rc == 0, f"an off-box lane is not this box's fit: exit {rc}, said {out!r}"
    assert "NOTHING ON THIS BOX" in out, f"must name why nothing was added up: {out!r}"
    assert "coder" in out, f"must name the lane it left out: {out!r}"
    assert "declares no production lanes" not in out, \
        f"a lane IS declared; that sentence is false about the config: {out!r}"


def test_lane_that_names_no_role_is_reported_unresolved():
    """A production lane whose role is not in the roles block: a config gap, and loud.

    It is not a fit failure and it is not "no lanes declared" -- it is a lane pointing at
    nothing. Must stay exit 2 (the check's third verdict) and say UNRESOLVED, naming the
    lane. Red on main: exit 2 for the wrong reason, with the no-lanes sentence.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = tree(tmp, ["bulider"], {"coder": {"tag": "gemma4:e4b"}}, [], {})
        rc, out = run(root)
    assert rc == 2, f"an unresolvable lane must stay loud: exit {rc}, said {out!r}"
    assert "UNRESOLVED" in out, f"must name it as unresolved, not as absent: {out!r}"
    assert "bulider" in out, f"must name the lane: {out!r}"
    assert "declares no production lanes" not in out, \
        f"a lane is declared; the sentence is wrong: {out!r}"


def test_empty_lane_list_is_still_loud():
    """The genuine case, kept: an empty approved set is a gap, not a fit."""
    root = tree(tempfile.mkdtemp(), [], {}, [], {})
    rc, out = run(root)
    assert rc == 2, f"no lanes declared is not a fit: exit {rc}, said {out!r}"
    assert "declares no production lanes" in out, f"must keep its words: {out!r}"
    assert "NOTHING ON THIS BOX" not in out, \
        f"an empty config is not the off-box case: {out!r}"


def test_lane_on_this_box_is_measured_and_fits():
    root = tree(tempfile.mkdtemp(), ["manager"], {"manager": {"tag": "swift"}},
                [{"tag": "swift", "weight_gb": 17.6, "server": "vllm-swift"}],
                {"vllm-swift": {"device": ON_BOX}})
    rc, out = run(root)
    assert rc == 0, f"17.6 GB on a 121 GB box fits: exit {rc}, said {out!r}"
    assert "working set 17.6" in out, f"must report the total: {out!r}"
    assert "fits" in out, f"must say fits: {out!r}"


def test_lane_on_this_box_can_still_refuse():
    """The check must keep the ability to say no. This is the assertion that kills a build
    that returns 0 whenever a lane is declared."""
    root = tree(tempfile.mkdtemp(), ["manager"], {"manager": {"tag": "swift"}},
                [{"tag": "swift", "weight_gb": 100.0, "server": "vllm-swift"}],
                {"vllm-swift": {"device": ON_BOX}})
    rc, out = run(root)
    assert rc == 1, f"100 GB on a 121 GB box with 40 GB headroom does not fit: exit {rc}"
    assert "DOES NOT FIT" in out, f"must say so: {out!r}"
    assert "40" in out, f"the headroom must not be relaxed to make it fit: {out!r}"


def test_off_box_lane_is_not_added_to_the_total():
    """One on-box lane plus one off-box lane: the total is the on-box weight only."""
    root = tree(tempfile.mkdtemp(), ["manager", "coder"],
                {"manager": {"tag": "swift"}, "coder": {"tag": "gemma4:e4b"}},
                [{"tag": "swift", "weight_gb": 17.6, "server": "vllm-swift"},
                 {"tag": "gemma4:e4b", "weight_gb": 9.6, "server": "ollama-thinkpad"}],
                {"vllm-swift": {"device": ON_BOX}, "ollama-thinkpad": {"device": OFF_BOX}})
    rc, out = run(root)
    assert rc == 0, f"17.6 GB resident fits: exit {rc}, said {out!r}"
    assert "working set 17.6" in out, f"the off-box model must not be counted: {out!r}"
    assert "9.6" not in out, f"the off-box weight must not appear in the total: {out!r}"


def main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 -- the record names the cause, never a bare FAIL
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
