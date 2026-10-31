#!/usr/bin/env python3
"""t046: the Corpus tab gains a Checks section that enumerates every scripts/check_*.py,
one row per check, with each check's own docstring line and a link to its own source.

Run: python3 scripts/tests/test_corpus_content.py   (pytest is NOT installed)

Red on main for one reason: feedback.py has no corpus_checks(), and corpus_page's section
list has no "checks", so corpus_page("checks") renders the Models body with no checks pill.

NO LIVE SERVER: corpus_page's Models body opens HTTP sockets via _corpus_models ->
_ollama_facts, so the one test that renders a page stubs _ollama_facts and restores it in a
finally block. Everything else reads real repo files or a temp dir this test owns.
"""
import ast
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "projects" / "app-repo"), str(ROOT / "scripts")]
import feedback  # noqa: E402

FAILS = []


def check(cond, msg):
    print(f"  {'ok  ' if cond else 'FAIL'} {msg}")
    if not cond:
        FAILS.append(msg)


def real_summary(path):
    doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    return next((ln.strip() for ln in doc.splitlines() if ln.strip()), "")


def head_ids(rel):
    text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    return re.findall(r"(?:^###\s+|^\*\*)([LD]-\d+)", text, re.M)


def page_offline(section):
    real = feedback._ollama_facts
    feedback._ollama_facts = lambda: {}
    try:
        return feedback.corpus_page(section)
    finally:
        feedback._ollama_facts = real


def test_enumerates_every_check_script():
    want = sorted(p.stem for p in (ROOT / "scripts").glob("check_*.py"))
    check(len(want) > 0, f"the repo has check scripts to list (found {len(want)})")
    check(hasattr(feedback, "corpus_checks"),
          "feedback.corpus_checks exists (absent on main -- this is the change)")
    rows = feedback.corpus_checks()
    check(isinstance(rows, list), "corpus_checks() returns a list")
    names = sorted(r.get("name", "") for r in rows)
    check(names == want, f"names are exactly the scripts/check_*.py stems ({len(want)} expected, {len(names)} found)")
    for r in rows:
        check(set(r) >= {"name", "path", "summary"}, f"row {r.get('name')!r} has name, path, summary")
        check(r.get("path") == f"scripts/{r.get('name')}.py",
              f"{r.get('name')!r} path names its own file, not a placeholder")
        check(not Path(str(r.get("path"))).is_absolute(), f"path {r.get('path')!r} is repo-relative")
        check((ROOT / r["path"]).is_file(), f"listed path {r['path']!r} exists on disk")


def test_shows_each_check_its_own_real_docstring_line():
    rows = {r["name"]: r for r in feedback.corpus_checks()}
    n = 0
    for p in sorted((ROOT / "scripts").glob("check_*.py")):
        want = real_summary(p)
        if not want:
            continue
        n += 1
        check(rows.get(p.stem, {}).get("summary") == want,
              f"{p.name} summary is its real docstring line {want!r}")
    check(n > 0, f"{n} checks have a real docstring line to compare against")


def test_lists_only_check_scripts():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        (base / "scripts").mkdir()
        (base / "scripts" / "check_alpha.py").write_text('"""Alpha does the thing."""\n', encoding="utf-8")
        (base / "scripts" / "check_notes.md").write_text("not a check\n", encoding="utf-8")
        (base / "scripts" / "notes.py").write_text('"""not a check."""\n', encoding="utf-8")
        rows = feedback.corpus_checks(base=base)
        check([r.get("name") for r in rows] == ["check_alpha"], "only check_*.py files are listed")
        check(bool(rows) and rows[0].get("summary") == "Alpha does the thing.",
              "the summary is the docstring first line")


def test_records_a_script_with_no_docstring():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        (base / "scripts").mkdir()
        (base / "scripts" / "check_bare.py").write_text("print('x')\n", encoding="utf-8")
        errs = []
        rows = feedback.corpus_checks(base=base, problems=errs)
        check([r.get("name") for r in rows] == ["check_bare"], "a check with no docstring is still listed")
        summary = rows[0]["summary"] if rows else ""
        check(isinstance(summary, str) and "docstring" in summary.lower(),
              f"its summary says why there is none: {summary!r}")
        check(errs == [], f"a missing docstring is not a read failure: {errs!r}")


def test_records_an_unreadable_script():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        (base / "scripts").mkdir()
        (base / "scripts" / "check_bin.py").write_bytes(b"\xff\xfe\x00not utf-8\n")
        errs = []
        rows = feedback.corpus_checks(base=base, problems=errs)
        check([r.get("name") for r in rows] == ["check_bin"], "an undecodable check is listed, not dropped")
        check(len(errs) == 1 and "check_bin" in str(errs[0]),
              f"the unreadable file is reported by name: {errs!r}")
        check(bool(rows) and "unreadable" in rows[0]["summary"].lower(),
              "its summary records the read failure")


def test_records_a_missing_scripts_dir():
    with tempfile.TemporaryDirectory() as td:
        errs = []
        rows = feedback.corpus_checks(base=Path(td), problems=errs)
        check(rows == [], "an empty base lists no checks")
        check(len(errs) == 1 and "scripts" in str(errs[0]),
              f"the reason is recorded, not swallowed: {errs!r}")


def test_page_gains_the_checks_section():
    rows = feedback.corpus_checks()
    first = sorted((ROOT / "scripts").glob("check_*.py"))[0]
    page = page_offline("checks")
    check("/corpus?section=checks" in page, "nav offers /corpus?section=checks (absent on main)")
    check(first.stem in page, f"the page names a real check: {first.stem}")
    link = "/file?path=" + urllib.parse.quote(f"scripts/{first.name}")
    check(link in page, f"that check links to its own source: {link}")
    check("docs/CHECKS.md" in page, "the section links docs/CHECKS.md (verified to exist)")
    check("cfilter" in page, "the section carries the same filter input as the others")


def test_existing_sections_still_render():
    dec = feedback.corpus_page("decisions")
    les = feedback.corpus_page("lessons")
    d_ids, l_ids = head_ids("docs/DECISIONS.md"), head_ids("docs/LESSONS.md")
    check(bool(d_ids), f"docs/DECISIONS.md has D-nn headings ({len(d_ids)})")
    check(bool(l_ids), f"docs/LESSONS.md has L-nn headings ({len(l_ids)})")
    for i in d_ids[:3]:
        check(i in dec, f"Decisions still renders {i}")
    for i in l_ids[:3]:
        check(i in les, f"Lessons still renders {i}")
    check("cfilter" in dec and "cfilter" in les, "the filter input stays on every section")
    check("/corpus?section=checks" in dec and "/corpus?section=checks" in les,
          "the new pill is offered from every section")
    check("/corpus?section=decisions" in les and "/corpus?section=lessons" in dec,
          "adding a pill did not remove the old ones")


if __name__ == "__main__":
    for _name in sorted(globals()):
        _fn = globals()[_name]
        if _name.startswith("test_") and callable(_fn):
            print("== " + _name)
            try:
                _fn()
            except Exception as _e:
                check(False, f"{_name} raised {type(_e).__name__}: {_e}")
    print("\n%d failure(s)" % len(FAILS))
    sys.exit(1 if FAILS else 0)
