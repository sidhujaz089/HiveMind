#!/usr/bin/env python3
"""t1463 -- a builder working on an OVERSIZED file under symbol-slicing can ask for the
exact REGION it needs, not the next slice from the top of the file.

FILED FROM the 2026-10-18T17:30:57Z dispatch of brief p696-t1340-dispatch-loop
(qwen3.8:27b-mtp-q4_K_M-56k, window 65536, output budget 55731): the file under edit,
scripts/dispatch_loop.py (3518 lines), is over WINDOW_BYTES, so the builder was shown a
symbol slice; it asked for that file four times (attempts 2, 3, 4, 7) and was answered four
times with "no room for more of it beside the prompt. Work from what you already have".
Attempts 5 and 6 answered anyway and produced a commit-gate refusal and a test failure -- the
model wrote into regions it had never been shown.

WHY IT IS DEADLOCKED TODAY: the loop that answers a file request can reply to a file it has
already opened in exactly one shape -- unseen(), which walks FORWARD from a per-file
high-water mark -- and slice_fits() refuses that slice when it will not sit beside the prompt.
There is no way to ask for "lines 93-98". sliced_by_lines(), whose job is a numbered window of
pad lines around named lines, is the missing channel, and the reply protocol cannot reach it.

RED ON MAIN FOR ONE REASON: coder.need_request and coder.region_of are not defined in
projects/local-inference/coder.py, so every test below that calls one raises AttributeError,
and test_the_need_loop_parses_and_serves_ranges is red because the loop calls neither. Not red
for its own bug: it imports coder the way scripts/tests/test_coder_range_hint.py does (repo
root = parents[2], sys.path to projects/local-inference), builds its own file with
tempfile.mkdtemp(), reads the repository read-only, spawns no subprocess, opens no model,
writes nothing outside the temp dir, leaves no process.
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

FAILURES = []
NEED_ANCHOR = 'obj["need"]'
PINNED = "no room for more of it beside the prompt"


def check(name, cond, detail=""):
    if cond:
        print("ok   " + name)
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


def test_the_two_functions_exist():
    check("coder.need_request is callable", callable(getattr(coder, "need_request", None)),
          "module 'coder' defines no callable need_request -- that is the change")
    check("coder.region_of is callable", callable(getattr(coder, "region_of", None)),
          "module 'coder' defines no callable region_of -- that is the change")


def test_a_plain_request_behaves_exactly_as_today():
    """No behaviour change smuggled in: today's request format keeps its exact meaning."""
    for spec in ("scripts/dispatch_loop.py",
                 "projects/local-inference/coder.py",
                 "scripts/tests/test_coder_line_range_path_after_size_refusal.py"):
        check(f"{spec!r} is a path with no range",
              coder.need_request(spec) == (spec, None),
              f"need_request({spec!r}) -> {coder.need_request(spec)!r}, expected ({spec!r}, None)")


def test_a_range_is_split_off_the_path():
    got = coder.need_request("scripts/dispatch_loop.py:93-98")
    check("the file part of a colon range is the real path",
          got[0] == "scripts/dispatch_loop.py",
          f"got {got[0]!r} -- the loop keys opened[] and readable() on the path, so a path that "
          f"keeps its ':93-98' suffix matches nothing and the deadlock stays")
    check("the range expands to every line in it, as ints, ascending",
          list(got[1]) == [93, 94, 95, 96, 97, 98], f"got {got[1]!r}")
    check("a single line number is a range of one",
          coder.need_request("scripts/dispatch_loop.py:97") == ("scripts/dispatch_loop.py", [97]),
          repr(coder.need_request("scripts/dispatch_loop.py:97")))
    check("a reversed range comes back ascending",
          list(coder.need_request("scripts/dispatch_loop.py:98-93")[1])
          == [93, 94, 95, 96, 97, 98], repr(coder.need_request("scripts/dispatch_loop.py:98-93")))
    check("spaces around the colon and the dash are tolerated",
          coder.need_request("scripts/dispatch_loop.py: 93 - 98")
          == ("scripts/dispatch_loop.py", [93, 94, 95, 96, 97, 98]),
          repr(coder.need_request("scripts/dispatch_loop.py: 93 - 98")))
    check("a colon that is not a line range leaves the path whole",
          coder.need_request("docs/2026:10-notes.md") == ("docs/2026:10-notes.md", None),
          repr(coder.need_request("docs/2026:10-notes.md")))


def test_region_of_shows_the_real_source_of_the_real_file():
    """Real artefact, not an imagined one: the very file the filed dispatch died on."""
    p = ROOT / "scripts" / "dispatch_loop.py"
    if not p.is_file():
        check("scripts/dispatch_loop.py is readable", False,
              "the file named in the filing is not in the tree -- repoint this fixture at an "
              "oversized file that is")
        return
    src = p.read_text(encoding="utf-8", errors="replace").splitlines()
    check("scripts/dispatch_loop.py is long enough for this to mean anything",
          len(src) > 200, f"only {len(src)} lines")
    out = coder.region_of("\n".join(src), list(range(93, 99)))
    check("region_of returns text for in-range lines", isinstance(out, str) and out.strip(),
          f"got {out!r}")
    if isinstance(out, str):
        for n in (93, 94, 95, 96, 97, 98):
            if src[n - 1].strip():
                check(f"the slice carries line {n} verbatim", src[n - 1] in out,
                      f"{src[n - 1]!r} is not in the region shown")
        check("the slice carries the file's own line number for the first requested line",
              f"{93:5d}" in out, f"no column-formatted '   93' in the slice")
        check("the slice does not sweep in the end of the file",
              f"{len(src):5d} " not in out,
              f"line {len(src)} of {len(src)} appeared in a region asked for at 93-98")
        check("the slice says how long the file is, so a reader knows it is a slice",
              f"{len(src)} lines" in out, f"no 'N lines' header; got {out[:220]!r}")


def test_region_of_on_a_file_the_fixture_owns():
    """Its own fixture (no live state): 400 lines, every one carrying a DISTINCT value, so a
    region cannot be satisfied by one repeated placeholder string."""
    d = Path(tempfile.mkdtemp(prefix="t1463-"))
    try:
        body = "\n".join(f"marker_{i} = {i * 7}" for i in range(1, 401))
        f = d / "synthetic.py"
        f.write_text(body + "\n", encoding="utf-8")
        out = coder.region_of(f.read_text(encoding="utf-8"), [97, 98, 99])
        for n in (97, 98, 99):
            check(f"line {n}'s own value is in the slice", f"marker_{n} = {n * 7}" in out,
                  f"marker_{n} = {n * 7} missing from {out!r}")
        check("a line outside the region is NOT in the slice", "marker_200 = 1400" not in out,
              "the region swept in a line far from 97-99")
        check("the fixture's own file is left byte-identical",
              f.read_text(encoding="utf-8") == body + "\n", "the oracle rewrote its fixture")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_request_inside_a_function_shows_the_enclosing_def():
    """briefing-the-local-model: excerpt to the enclosing def, never by line arithmetic."""
    text = ("import os\n"
            "\n"
            "\n"
            "def terminal_row(item):\n"
            "    x = 1\n"
            "    y = 2\n"
            "    z = 3\n"
            "    return x\n"
            "\n"
            "\n"
            "def other():\n"
            "    pass\n")
    out = coder.region_of(text, [6])
    check("the def line of the enclosing function is shown", "def terminal_row(item):" in out,
          f"requested line 6; a builder cannot edit a function it cannot see. got: {out!r}")
    check("the following function is NOT swept in", "def other():" not in out,
          "the region reached past line 6's own function -- pad must stay small enough to be "
          "affordable and large enough to reach the def (2..4 satisfies both assertions)")


def test_a_request_past_the_end_of_the_file_yields_nothing_to_show():
    text = "\n".join(f"line {i}" for i in range(1, 21))
    out = coder.region_of(text, [999])
    check("out-of-range lines yield None so the caller can say why", out is None,
          f"got {out!r}, expected None")


def test_the_need_loop_parses_and_serves_ranges():
    """A function nobody calls fixes nothing (oracle-weak, RCA t613): assert the value is USED."""
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text(
        encoding="utf-8", errors="replace")
    check("the file's existing windowing helper is still defined (region_of must reuse it)",
          "def sliced_by_lines(" in src, "sliced_by_lines is gone from the file")
    at = src.find(NEED_ANCHOR)
    check("the loop that answers a file request is findable", at > 0,
          f"the anchor {NEED_ANCHOR!r} is gone -- re-aim this fixture at the loop that answers a "
          "file request")
    if at < 0:
        return
    tail = src[at:]          # the definitions sit higher up, so a hit below the anchor is a call
    check("the loop parses each request with need_request()", "need_request(" in tail,
          "the loop that answers a file request never calls need_request, so 'path:93-98' is "
          "still treated as a file name and the deadlock stays exactly as filed")
    check("the loop serves a parsed range with region_of()", "region_of(" in tail,
          "the loop never calls region_of, so a range is parsed and then thrown away")
    check("the size refusal tells the builder how to ask for a region",
          'ask for "path:LO-HI"' in tail,
          'the refusal must name the syntax: a stop that is not actionable is a delay, not a door')
    check("the size refusal keeps the wording the repo already pins",
          PINNED in src, f"{PINNED!r} must stay verbatim -- it is asserted by "
          "scripts/tests/test_coder_line_range_path_after_size_refusal.py:135-137")


if __name__ == "__main__":
    for _name in sorted(k for k in globals()
                        if k.startswith("test_") and callable(globals()[k])):
        print("\n" + _name + ":")
        try:
            globals()[_name]()
        except Exception as _e:                                   # noqa: BLE001
            check(_name, False, f"raised {type(_e).__name__}: {_e}")
    print("\n" + "=" * 66)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for _f in FAILURES:
            print("  - " + _f)
        sys.exit(1)
    print("all checks passed")
    sys.exit(0)
