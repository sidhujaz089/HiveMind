#!/usr/bin/env python3
"""t1368: a Swift start on an UNMEASURED footprint must be refused, loudly.

RCA workspace/rca/6ff75679d02b.md, cause C4: serve.py's need_gib()
(projects/local-inference/serve.py:140-148) returns NEED_GIB = 102 (serve.py:100, whose own
comment reads "the unmeasured fallback: the 2026-10-09 crash size (99) + the 2 GB reserve") when
the active size_key is absent from workspace/swift-footprints.json, and preflight_problems()
(serve.py:326-350) accepts that fallback in silence. Nothing refuses, nothing flags, nothing names
the command that closes the obligation -- which is why "record the peak" has stayed open since
2026-10-09 (RCA thread C1).

THE CASE THIS MODULE EXISTS FOR, pinned by test_unmeasured_footprint_is_a_refusal: the active
config has NO row in the footprints file. Today preflight says ok for it.

Real artefacts, not placeholders -- both values are rows of workspace/swift-footprints.json read
on 2026-10-17:
  * "seqs=4,kv=12,mtp=3,fp8_hybrid=1,draft_vocab=1,model=swift-serve-int8head": 91.0, the measured
    peak of the 4-lane config. Its need is 91.0 + RESERVE_GIB (serve.py:101) = 93.0, a number an
    implementation that always answers the fallback can never produce.
  * the 1-lane config, seqs=1 with kv = max(8, min(12, 3*seqs)) = 8 by engine_size's own
    arithmetic (serve.py:130), has no row: its need is the 102 fallback and its status is
    UNMEASURED. Two distinct real values, so one hard-coded constant cannot satisfy both halves.
Both keys are built by calling serve.size_key, never typed out, so they stay correct if FLAGS
(serve.py:87-95) change.

No live state: the footprints file is a tempfile; every docker, /proc and network read
preflight_problems() makes is replaced for the duration and restored in __exit__. No model call,
no server, no process left running. The one subprocess is `serve.py start --help`, which exits
before it touches docker (timeout=60). pytest is not installed; the __main__ block runs every test.
"""
import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))

import serve  # noqa: E402

# REAL, read from workspace/swift-footprints.json (9 rows) on 2026-10-17.
MEASURED_SEQS, MEASURED_KV, MEASURED_PEAK_GB = 4, 12, 91.0
# The 1-lane config: kv = max(8, min(12, 3*seqs)) -- engine_size's formula, serve.py:130.
UNMEASURED_SEQS, UNMEASURED_KV = 1, 8

MEASURED_KEY = serve.size_key(MEASURED_SEQS, MEASURED_KV)
UNMEASURED_KEY = serve.size_key(UNMEASURED_SEQS, UNMEASURED_KV)


class _Inert:
    """Swap out every live read preflight_problems() performs; restore them on exit.

    A fixture that leaves one real call in place measures that machinery too, so all six are
    replaced: image_present (docker), weights_are_safe (rglob over ~/models), TABLE_DIR (disk),
    ollama_resident (urlopen to 127.0.0.1:11434), mem_available_gib (/proc/meminfo), engine_size
    (task_routing's live lane config). FOOTPRINTS points at the tempfile carrying
    footprints_text -- the ONLY footprint file the code under test sees.
    """

    def __init__(self, footprints_text, seqs=MEASURED_SEQS, kv=MEASURED_KV):
        self.footprints_text = footprints_text
        self.seqs, self.kv = seqs, kv

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = {}
        d = Path(self._tmp.name)
        (d / "DONE").touch()                      # the PLE table marker, serve.py:339
        fp = d / "swift-footprints.json"
        fp.write_text(self.footprints_text, encoding="utf-8")
        swaps = (("TABLE_DIR", d),
                 ("FOOTPRINTS", fp),
                 ("image_present", lambda ref: True),
                 ("weights_are_safe", lambda root: (True, "no pickle weights")),
                 ("ollama_resident", lambda: []),
                 ("mem_available_gib", lambda: 1e6),
                 ("engine_size", lambda: (self.seqs, self.kv)))
        for name, new in swaps:
            self._saved[name] = getattr(serve, name)
            setattr(serve, name, new)
        return self

    def __exit__(self, *exc):
        for name, old in self._saved.items():
            setattr(serve, name, old)
        self._tmp.cleanup()
        return False


MEASURED_FILE = json.dumps({MEASURED_KEY: MEASURED_PEAK_GB})


def test_unmeasured_footprint_is_a_refusal():
    """The defect: today preflight says ok for a config nobody has measured."""
    with _Inert(MEASURED_FILE, seqs=UNMEASURED_SEQS, kv=UNMEASURED_KV):
        probs = serve.preflight_problems()
    flagged = [p for p in probs if "UNMEASURED" in p]
    assert flagged, (
        f"preflight accepted an UNMEASURED footprint: {probs!r}. The active config's key has no "
        f"row in the footprints file, and today nothing says so -- the RCA's C4.")
    assert UNMEASURED_KEY in flagged[0], (
        f"the refusal must name the exact size_key it could not measure, so the operator knows "
        f"which config to measure; got {flagged[0]!r}, key {UNMEASURED_KEY!r}")
    assert str(serve.NEED_GIB) in flagged[0], (
        f"the refusal must show the fallback number it is falling back to ({serve.NEED_GIB}), "
        f"not just complain; got {flagged[0]!r}")
    assert "serve.py footprint" in flagged[0], (
        f"the refusal must name the command that closes the obligation, per RCA 6ff75679d02b "
        f"item 4; got {flagged[0]!r}")


def test_allow_unmeasured_stops_the_refusal_and_nothing_else_is_invented():
    """The operator's escape hatch must unblock ONLY the unmeasured refusal."""
    with _Inert(MEASURED_FILE, seqs=UNMEASURED_SEQS, kv=UNMEASURED_KV):
        probs = serve.preflight_problems(allow_unmeasured=True)
    assert not [p for p in probs if "UNMEASURED" in p], (
        f"--allow-unmeasured must clear the unmeasured refusal, the whole point of the flag; "
        f"got {probs!r}")
    assert probs == [], (
        f"with every other precondition satisfied and the flag given, there is nothing to refuse; "
        f"an implementation that invents extra problems cannot be deployed, got {probs!r}")


def test_measured_footprint_is_never_flagged():
    """The negative half: a measured config must pass. A gate that always complains is a gate
    that gets bypassed with --allow-unmeasured on every start, which is worth less than nothing."""
    with _Inert(MEASURED_FILE, seqs=MEASURED_SEQS, kv=MEASURED_KV):
        probs = serve.preflight_problems()
        flag = serve.unmeasured_flag(MEASURED_SEQS, MEASURED_KV)
        need = serve.need_gib(MEASURED_SEQS, MEASURED_KV)
    assert probs == [], f"a measured, unobstructed config must produce no problems, got {probs!r}"
    assert flag == "", f"a measured config must produce no flag, got {flag!r}"
    assert need == MEASURED_PEAK_GB + serve.RESERVE_GIB, (
        f"the measured peak 91.0 GB must be what need_gib reports, plus the "
        f"{serve.RESERVE_GIB} GiB reserve = 93.0, got {need!r} -- a gate that reports the 102 "
        f"fallback for a MEASURED config has thrown away the measurement the task is protecting")


def test_the_fallback_and_the_flag_agree():
    """Unmeasured: need_gib keeps its 102 (existing callers unchanged) and the flag says so."""
    with _Inert(MEASURED_FILE):
        flag = serve.unmeasured_flag(UNMEASURED_SEQS, UNMEASURED_KV)
        need = serve.need_gib(UNMEASURED_SEQS, UNMEASURED_KV)
    assert flag, (
        f"an unmeasured config must yield a non-empty flag string -- cmd_start prints it, and "
        f"silence is the defect this suite ends")
    assert str(serve.NEED_GIB) in flag, f"the flag must state the fallback in force: {flag!r}"
    assert UNMEASURED_KEY in flag, f"the flag must name the key: {flag!r}"
    assert "serve.py footprint" in flag, f"the flag must name the recorder: {flag!r}"
    assert need == serve.NEED_GIB, (
        f"an unmeasured config's need must stay the {serve.NEED_GIB} fallback (140-148 keeps "
        f"working for fleet.py/chatui.py), got {need!r}")


def test_start_accepts_the_flag_on_the_command_line():
    """The operator's escape must exist in argparse, not only in the function signature --
    a flag the builder forgets on the subparser is a start that dies on 'unrecognized
    arguments'. --help exits before any docker call."""
    r = subprocess.run([sys.executable, str(ROOT / "projects" / "local-inference" / "serve.py"),
                        "start", "--help"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (
        f"`serve.py start --help` exited {r.returncode}: {r.stderr[-400:]} -- argparse rejects "
        f"the new flag, so no operator can start Swift on an unmeasured config at all")
    assert "--allow-unmeasured" in r.stdout, (
        f"`start --help` must list --allow-unmeasured, the documented escape; got {r.stdout[-400:]}")


def test_cmd_start_prints_the_flag_when_it_is_allowed():
    """Wiring, source-checked: cmd_start's print needs a live container to exercise, so this
    pins that the refusal is PRINTED, not swallowed -- the flag's whole job is to be loud. The
    string's CONTENT is asserted behaviourally in test_the_fallback_and_the_flag_agree."""
    src = inspect.getsource(serve.cmd_start)
    assert "allow_unmeasured" in src, (
        "cmd_start must read args.allow_unmeasured; a start that starts in silence is the RCA's "
        "defect reproduced in the new code")
    assert "unmeasured_flag(" in src, (
        "cmd_start must print the sentence unmeasured_flag() builds -- one string, two callers, "
        "so preflight and start can never drift apart")


TESTS = [test_unmeasured_footprint_is_a_refusal,
         test_allow_unmeasured_stops_the_refusal_and_nothing_else_is_invented,
         test_measured_footprint_is_never_flagged,
         test_the_fallback_and_the_flag_agree,
         test_start_accepts_the_flag_on_the_command_line,
         test_cmd_start_prints_the_flag_when_it_is_allowed]

if __name__ == "__main__":
    fails = 0
    for t in TESTS:
        try:
            t()
        except Exception as e:                                  # noqa: BLE001 -- report, never swallow
            fails += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"PASS {t.__name__}")
    print(f"{len(TESTS) - fails}/{len(TESTS)} test(s) passed")
    sys.exit(1 if fails else 0)
