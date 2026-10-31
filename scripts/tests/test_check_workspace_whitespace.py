#!/usr/bin/env python3
"""t1393: scripts/check_workspace.py must refuse non-standard whitespace in docs.

Red today because check_workspace.py defines no NONSTANDARD_WHITESPACE set and no
scan_nonstandard_whitespace(). Evidence: workspace/rca/ac8ea3a366f3.md:38 (the six code
points and the red fixture) and :25 (the tree currently holds 0 of these bytes, so this is
prevention, not repair -- the real-tree test passes on main and must keep passing).

Run: python3 scripts/tests/test_check_workspace_whitespace.py   (pytest is not installed)

Every offending character is built with chr(0x00A0) etc. so this file stays pure ASCII: an
oracle that seeds the tree with an NBSP byte would trip the very lint it is testing.
"""
import importlib.util
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]

REQUIRED = {
    0x00A0: "NO-BREAK SPACE",
    0x2007: "FIGURE SPACE",
    0x200B: "ZERO WIDTH SPACE",
    0x202F: "NARROW NO-BREAK SPACE",
    0x2060: "WORD JOINER",
    0xFEFF: "ZERO WIDTH NO-BREAK SPACE (BOM)",
}


def load_module():
    path = ROOT / "scripts" / "check_workspace.py"
    if not path.is_file():
        return None, "{} is not a file".format(path)
    spec = importlib.util.spec_from_file_location("check_workspace_under_test", path)
    if spec is None or spec.loader is None:
        return None, "cannot build an import spec for {}".format(path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except BaseException as exc:  # an import crash is a finding, not a traceback
        return None, "importing scripts/check_workspace.py raised {}: {}".format(
            type(exc).__name__, exc)
    return mod, None


MOD, IMPORT_ERROR = load_module()

FAILS = []


def check(ok, msg):
    print("  {} {}".format("ok  " if ok else "FAIL", msg))
    if not ok:
        FAILS.append(msg)


def scan(root):
    """Call the function under test. Its absence is a failure, never a crash."""
    if MOD is None:
        check(False, "scan_nonstandard_whitespace: " + IMPORT_ERROR)
        return None
    fn = getattr(MOD, "scan_nonstandard_whitespace", None)
    if fn is None:
        check(False, "scan_nonstandard_whitespace is not defined in scripts/check_workspace.py")
        return None
    return fn(pathlib.Path(root))


def findings_of(results):
    if results is None:
        return None
    return [str(r) for r in results]


def write(tmp, rel, text):
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_flags_each_of_the_six_code_points():
    for cp, name in sorted(REQUIRED.items()):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            rel = "skills/demo/SKILL.md"
            write(tmp, rel, "title: demo\nbody " + chr(cp) + " tail\n")
            found = findings_of(scan(tmp))
            if found is None:
                return
            joined = " | ".join(found)
            check(len(found) == 1,
                  "U+{:04X} ({}): exactly one finding -- got {} in {!r}".format(
                      cp, name, len(found), found))
            check("U+{:04X}".format(cp) in joined,
                  "U+{:04X} ({}): the finding names the code point as U+{:04X} -- got {!r}".format(
                      cp, name, cp, joined))
            check(rel in joined,
                  "U+{:04X} ({}): the finding names the file {!r} -- got {!r}".format(
                      cp, name, rel, joined))
            check(":2:" in joined,
                  "U+{:04X} ({}): the finding names line 2 -- got {!r}".format(cp, name, joined))


def test_finds_the_offender_on_a_later_line():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        rel = "skills/demo/SKILL.md"
        write(tmp, rel, "line one\nline two\nline three " + chr(0x00A0) + " tail\n")
        found = findings_of(scan(tmp))
        if found is None:
            return
        joined = " | ".join(found)
        check(":3:" in joined,
              "the finding carries the real line number 3, not a hardcoded 1 -- got {!r}".format(joined))


def test_clean_markdown_is_not_flagged():
    """U+2014 and U+2192 live in the real SKILL.md files today: flagging them is a defect."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        text = ("title: demo\n\nbody with em dash \u2014 and arrow \u2192 and a\ttab\n"
                "literal text U+00A0 is prose, not the character\n\r\nend\r\n")
        write(tmp, "skills/demo/SKILL.md", text)
        write(tmp, "skills/other/notes.md", "plain ascii only\n")
        found = findings_of(scan(tmp))
        if found is None:
            return
        check(found == [], "legitimate content reports nothing -- got {!r}".format(found))


def test_repo_skill_files_are_clean():
    """Real artefact, read-only: prevention, not repair (rca ac8ea3a366f3.md:25)."""
    skills = ROOT / "docs" / "skills"
    check(skills.is_dir(), "docs/skills exists to be scanned")
    if not skills.is_dir():
        return
    found = findings_of(scan(skills))
    if found is None:
        return
    check(found == [], "docs/skills is clean today; a clean tree must report nothing -- got {!r}".format(found))


def test_recurses_into_nested_directories():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        rel = "skills/a/b/c/SKILL.md"
        write(tmp, rel, "deep " + chr(0x200B) + " nested\n")
        found = findings_of(scan(tmp))
        if found is None:
            return
        joined = " | ".join(found)
        check(len(found) == 1, "exactly one finding for a nested file -- got {!r}".format(found))
        check("SKILL.md" in joined and "U+200B" in joined,
              "the nested file is reported with its path and code point -- got {!r}".format(joined))


def test_undecodable_file_is_reported_not_swallowed():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        p = tmp / "skills" / "broken" / "SKILL.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"abc\n\xff\xfe\n")
        found = findings_of(scan(tmp))
        if found is None:
            return
        joined = " | ".join(found)
        check(len(found) == 1, "an undecodable md file produces one finding -- got {!r}".format(found))
        check("SKILL.md" in joined and ("decode" in joined.lower() or "UnicodeDecodeError" in joined),
              "the finding says why (decode failure) -- got {!r}".format(joined))


def test_the_refused_set_is_exactly_the_six():
    """Inversion: the refused code points are enumerated, and the set is the contract."""
    if MOD is None:
        check(False, "NONSTANDARD_WHITESPACE: " + IMPORT_ERROR)
        return
    table = getattr(MOD, "NONSTANDARD_WHITESPACE", None)
    if table is None:
        check(False, "NONSTANDARD_WHITESPACE is not defined in scripts/check_workspace.py")
        return
    items = list(table.values()) if isinstance(table, dict) else list(table)
    seen = set()
    for entry in items:
        if isinstance(entry, int):
            seen.add(entry)
        elif isinstance(entry, str) and entry.strip().upper().startswith("U+"):
            seen.add(int(entry.strip().upper().replace("U+", ""), 16))
        elif isinstance(entry, str) and len(entry) == 1:
            seen.add(ord(entry))
        else:
            check(False, "NONSTANDARD_WHITESPACE entry {!r} is not a code point, a character, "
                         "a U+XXXX token, or a decimal code point".format(entry))
    check(seen == set(REQUIRED),
          "NONSTANDARD_WHITESPACE covers exactly the six code points of the finding: wanted "
          "{}, got {}".format(sorted(hex(c) for c in REQUIRED), sorted(hex(c) for c in seen)))


TESTS = [
    test_flags_each_of_the_six_code_points,
    test_finds_the_offender_on_a_later_line,
    test_clean_markdown_is_not_flagged,
    test_repo_skill_files_are_clean,
    test_recurses_into_nested_directories,
    test_undecodable_file_is_reported_not_swallowed,
    test_the_refused_set_is_exactly_the_six,
]

if __name__ == "__main__":
    print("t1393: refuse non-standard whitespace under docs (red fixture)")
    for t in TESTS:
        print("[{}]".format(t.__name__))
        t()
    print("\n{} assertion(s) failed".format(len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    sys.exit(1 if FAILS else 0)
