#!/usr/bin/env python3
"""t1088: `library.py register` must refuse a block finding that vanishes with no disposition.

THE DEFECT (workspace/rca/a791f4fba5bb.md): `silent-content-slice` passes when the DETECTOR's
regex is quiet, not when the bounded read is gone. So rewriting `text[:2000]` into a bounded
`search(text, 0, 2000)` -- the same bounded read, invisible to the regex -- takes a registered
script from red to green and records no revocable decision. The gate under test: a BLOCK
finding present in HEAD's committed text of the file and absent from the text being registered
is a red to green transition; with neither --accept "why" nor --fixed "why", the register must
print REFUSED, exit non-zero, and leave the library entry untouched.

HEAD's form carries TWO block findings (a silent slice and a swallowed exception) so the
refusal is tested for naming EVERY vanished finding and printing each code line whole.

Run: python3 scripts/tests/test_library_register_evasive_rewrite.py
State: a temp git repo and temp files only. The live scripts/library.json is never touched.
"""
import io
import json
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import library  # noqa: E402

# Composed at runtime so this file carries no quoted absolute path of its own: the oracle is
# scanned as a live-state file by the builder (scope_lint runs the checks on scripts/...), and
# the fixed-tmp-path rule matches a quoted path in a string literal.
BAD_LOG = "/tmp" "/live.log"

SNIFF_SLICE = "    return text[:2000]\n"
SNIFF_BOUNDED = '    return re.search(r"<html|<body", text, 0, 2000) is not None\n'
SNIFF_FIXED = '    return re.search(r"<html|<body", text) is not None\n'
SWALLOWED = "except Exception:\n    pass\n"
LOGGED = "except Exception as e:\n    print('sniff failed', e)\n"

# HEAD's committed form: two BLOCK findings -- a silent slice of a content variable
# (library_checks: silent-content-slice) and an except whose next line is pass (swallowed-exception).
HEAD_FORM = ("import re\n" + f'LOG = "{BAD_LOG}"\n' + "def sniff(text):\n"
             + SNIFF_SLICE + "try:\n    sniff('x')\n" + SWALLOWED)
# The evasive rewrite: both findings vanish from the DETECTOR, neither is fixed in substance.
# No bracket slice, so silent-content-slice is silent. The exception is printed, not passed, so
# swallowed-exception is silent. The bounded read of the stored body is still there.
EVASIVE = ("import re\n" + f'LOG = "{BAD_LOG}"\n' + "def sniff(text):\n"
           + SNIFF_BOUNDED + "try:\n    sniff('x')\n" + LOGGED)
# A real fix of the slice: the bound is gone, not hidden. No finding in HEAD, so this form is
# also the one with no transition to disposition.
FIXED_FORM = ("import re\n" + f'LOG = "{BAD_LOG}"\n' + "def sniff(text):\n"
              + SNIFF_FIXED + "try:\n    sniff('x')\n" + LOGGED)


def _git(tmp, *args):
    r = subprocess.run(["git", "-c", "user.email=fixture@invalid", "-c", "user.name=fixture",
                        "-c", "commit.gpgsign=false", *args],
                       cwd=str(tmp), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stdout}{r.stderr}"


def _setup(tmp, head_text):
    """A temp git repo whose HEAD carries head_text, with library's paths redirected into tmp.

    The entry for scripts/tool.py is seeded, so `register` is a RE-registration: the flow-chart
    gate (library.py:450, `p not in s`) short-circuits before on_chart is called -- a fixture
    must not depend on the live FLOW.json. The seeded check count mirrors head_text, so a
    refusal that leaves the entry alone is distinguishable from a registration that rewrites it.
    """
    (tmp / "scripts").mkdir(parents=True)
    (tmp / "scripts" / "tool.py").write_text(head_text, encoding="utf-8")
    _git(tmp, "init", "-q")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-qm", "seed")
    library.REPO = tmp
    library.LIB = tmp / "scripts" / "library.json"
    library.MD = tmp / "SCRIPTS.md"
    library.REQUESTS = tmp / "requests.jsonl"
    blocks = 2 if head_text == HEAD_FORM else 0
    library.LIB.write_text(json.dumps({"version": 1, "scripts": {
        "scripts/tool.py": {"kind": "tool", "description": "sniffs the page type",
                            "registered": "2026-10-13", "by": "fixture",
                            "checks": {"blocks": blocks, "warns": 0}}}}, indent=1) + "\n",
                           encoding="utf-8")
    return str(tmp / "scripts" / "tool.py")


def _register(path, *flags):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = library.main(["register", path, "sniffs the page type", *flags])
    return rc, buf.getvalue()


def _entry():
    return json.loads(library.LIB.read_text(encoding="utf-8"))["scripts"]["scripts/tool.py"]


def test_evasive_rewrite_is_refused_and_leaves_the_entry_alone():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td).resolve()
        p = _setup(tmp, HEAD_FORM)
        (tmp / "scripts" / "tool.py").write_text(EVASIVE, encoding="utf-8")
        rc, out = _register(p)
        assert rc != 0, f"register accepted an evasive rewrite (rc={rc}, out={out!r})"
        assert "REFUSED" in out, f"the refusal did not say REFUSED: {out!r}"
        assert "silent-content-slice" in out, f"refusal does not name the vanished check: {out!r}"
        e = _entry()
        assert e["checks"]["blocks"] == 2, f"the entry was rewritten to green: {e}"
        assert "accepted" not in e and "fixed" not in e, f"an undispositioned transition was recorded: {e}"


def test_accept_is_a_working_low_friction_exit():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td).resolve()
        p = _setup(tmp, HEAD_FORM)
        (tmp / "scripts" / "tool.py").write_text(EVASIVE, encoding="utf-8")
        why = "head-only sniff of the page type; the decoded body is stored whole"
        rc, out = _register(p, "--accept", why)
        assert rc == 0, f"--accept did not clear a dispositioned transition (rc={rc}, out={out!r})"
        e = _entry()
        assert e.get("accepted") == why, f"the exception was not recorded revocably: {e}"
        assert e["checks"]["blocks"] == 0, f"the entry does not match what was registered: {e}"


def test_a_real_fix_records_a_fix_marker():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td).resolve()
        p = _setup(tmp, HEAD_FORM)
        (tmp / "scripts" / "tool.py").write_text(FIXED_FORM, encoding="utf-8")
        why = "bounded sniff deleted; the whole body is searched"
        rc, out = _register(p, "--fixed", why)
        assert rc == 0, f"--fixed did not clear a real fix (rc={rc}, out={out!r})"
        e = _entry()
        assert e.get("fixed") == why, f"the fix marker was not recorded revocably: {e}"
        assert e["checks"]["blocks"] == 0, f"the entry does not match what was registered: {e}"


def test_no_transition_needs_no_disposition():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td).resolve()
        p = _setup(tmp, FIXED_FORM)
        rc, out = _register(p)
        assert rc == 0, f"an ordinary registration was refused (rc={rc}, out={out!r})"
        assert "REFUSED" not in out, f"a file with no vanished finding was refused: {out!r}"
        e = _entry()
        assert e["description"] == "sniffs the page type", f"the entry was not written: {e}"
        assert e["checks"]["blocks"] == 0, f"the check count is not the current text's: {e}"


def test_refusal_names_every_vanished_finding_whole():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td).resolve()
        p = _setup(tmp, HEAD_FORM)
        (tmp / "scripts" / "tool.py").write_text(EVASIVE, encoding="utf-8")
        rc, out = _register(p)
        assert rc != 0, f"two vanished findings were accepted (rc={rc}, out={out!r})"
        for cid in ("silent-content-slice", "swallowed-exception"):
            assert cid in out, f"the refusal omits a vanished finding ({cid}): {out!r}"
        assert SNIFF_SLICE.strip() in out, f"the slice finding's code is cut: {out!r}"
        assert SWALLOWED.replace("\n", " ").strip() in out, f"the swallow finding's code is cut: {out!r}"


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in fns:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:  # noqa: BLE001 -- a fixture reports what it caught, never passes silently
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(fns) - len(failed)}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
