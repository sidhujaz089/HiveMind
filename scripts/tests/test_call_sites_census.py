#!/usr/bin/env python3
"""t754: a census of direct engine call sites, with a ratchet that fails on growth.

WHY. scripts/check_working_set.py (96 lines, read whole) is the t301 working-set FIT check: it
compares the declared production lanes' GB against the box. It records nothing about engine call
sites -- no census writer, no row-level record, no ceiling. The population is prose only:
docs/ENGINES.md:113 says "about 30" call sites post to an engine directly; the session review that
filed this task said 40 (workspace/rca/15f9422b5ae7.md). workspace/call-sites.jsonl does not exist
(verified 2026-10-10: ls -> No such file or directory). No number, no denominator, no way to watch
the class shrink.

THE CHANGE UNDER TEST -- all of it in scripts/check_working_set.py:
  CENSUS_PATH, SCAN_DIRS, ENGINE_ENDPOINTS, SCAN_SKIP, ALLOWED_DIRECT        (constants)
  scan_direct_calls(root=None) -> [{"path", "line", "endpoint", "migrated"}, ...]
  write_census(root=None) -> int          rewrites <root>/workspace/call-sites.jsonl
  check_census(root=None, write=False) -> 0 clean / 1 GROWTH or STALE / 2 MISSING or CORRUPT
  check(..., census_write=False) folds that verdict into its own; --census regenerates the file.
The census FILE is the ceiling: a site in today's scan that is not in the file is GROWTH and fails;
a site in the file that the scan no longer finds is STALE and fails. The count can only move down,
and a census that does not exist is a finding, never a pass.

THIS FILE NAMES NO ENDPOINT. Every engine path is read from cw.ENGINE_ENDPOINTS, the real constant
the census scans for; a model-endpoint literal in this file would also trip library_checks'
model-call-outside-contract, which the scope lint runs against oracle text.

RED ON MAIN for the missing change and nothing else. Case 6 pins the behaviour that must NOT
change: the fit verdict is still printed, and a tree that fits with a matching census exits 0.

No live state: every file created here lives under tempfile.mkdtemp and is removed. The one read of
the real repository (case 9) is read-only. No model call, no subprocess, no network, no fixed
temp-directory path, nothing written outside the temp dir. Run:
    python3 scripts/tests/test_call_sites_census.py
(pytest is not installed; the __main__ block runs every test and exits non-zero on failure.)
"""
import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_working_set as cw  # noqa: E402

FAILS = []


def check(cond, why):
    if not cond:
        FAILS.append(why)
        print("FAIL: " + why)


def ep(i):
    """Engine endpoint i, read from the module under test -- never re-typed here (see header)."""
    return cw.ENGINE_ENDPOINTS[i]


def site(i):
    """A .py whose line 3 posts directly to engine endpoint i, in the shape real callers use: a
    base URL plus an endpoint string. settings.request builds the body; this fixture is a call
    site, not a body."""
    return ('import json\n'
            'import urllib.request\n'
            'url = base + "%s"\n' % ep(i) +
            'body = json.dumps(req).encode()\n')


ROUTED = 'import infer\nd = infer.chat(body, timeout=1800)\n'


def make_root():
    """A tree that FITS (1 GB on a 100 GB box), so a non-zero exit below is the census verdict and
    never a GB argument."""
    d = Path(tempfile.mkdtemp(prefix="t754-"))
    models = d / "projects" / "local-inference" / "models"
    models.mkdir(parents=True)
    (models / "SERVING.json").write_text(json.dumps(
        {"production_lanes": ["manager"], "roles": {"manager": {"tag": "m"}}}), encoding="utf-8")
    (models / "REGISTRY.json").write_text(
        json.dumps({"models": {"m": {"weight_gb": 1.0}}}), encoding="utf-8")
    return d


def write_file(root, rel, text):
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return f


def census_rows(root):
    p = root / "workspace" / "call-sites.jsonl"
    if not p.is_file():
        return None
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def run_check(root):
    """check() with the fit arithmetic pinned. An exception is recorded and named -- never
    swallowed -- so a fixture-shape problem reads as one legible FAIL, not a dead suite."""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = cw.check(root, box_gb=100.0, headroom_gb=0.0)
    except Exception as exc:
        FAILS.append("check() raised %s: %s (fixture shape, not the change under test)"
                     % (type(exc).__name__, exc))
        print("FAIL: check() raised %s: %s" % (type(exc).__name__, exc))
        return None, buf.getvalue()
    return rc, buf.getvalue()


def test_1_scan_finds_every_direct_site_and_only_those():
    """Case 1 -- the census counts SITES, not files, and does not count routed callers, comments,
    files outside the scanned lanes, or this suite's own fixtures."""
    root = make_root()
    write_file(root, "scripts/probe.py", site(0))
    write_file(root, "scripts/calibrate.py", site(1))
    write_file(root, "projects/local-inference/infer.py", site(0))   # the transport: allowlisted
    write_file(root, "scripts/served.py", ROUTED)                   # routed via infer: not a row
    write_file(root, "scripts/notes.py", '# we used to POST to "%s" here\n' % ep(0))
    write_file(root, "docs/notes.py", site(0))                      # outside the scanned lanes
    write_file(root, "scripts/tests/test_fixture.py", site(0))       # a test fixture
    rows = cw.scan_direct_calls(root)
    check(isinstance(rows, list), "scan_direct_calls returned %r, not a list" % (rows,))
    if not isinstance(rows, list):
        return
    got = sorted((r.get("path"), r.get("line"), r.get("endpoint"), r.get("migrated")) for r in rows)
    want = sorted([("scripts/probe.py", 3, ep(0), False),
                   ("scripts/calibrate.py", 3, ep(1), False),
                   ("projects/local-inference/infer.py", 3, ep(0), True)])
    check(got == want, "the scan did not return exactly the direct sites: got %r want %r" % (got, want))
    shutil.rmtree(str(root), ignore_errors=True)


def test_2_write_census_rewrites_the_file_with_one_row_per_site():
    """Case 2 -- the machine-readable record: one JSON row per site, four fields, and the file is
    REWRITTEN (an append-only census drifts into duplicates and inflates the denominator)."""
    root = make_root()
    write_file(root, "scripts/probe.py", site(0))
    write_file(root, "scripts/calibrate.py", site(1))
    write_file(root, "scripts/embed.py", site(2))
    n = cw.write_census(root)
    check(n == 3, "write_census returned %r; this tree has exactly three direct sites" % (n,))
    rows = census_rows(root)
    check(rows is not None, "write_census did not create workspace/call-sites.jsonl")
    if rows is not None:
        check(sorted((r["path"], r["line"], r["endpoint"], r["migrated"]) for r in rows) == sorted([
            ("scripts/probe.py", 3, ep(0), False),
            ("scripts/calibrate.py", 3, ep(1), False),
            ("scripts/embed.py", 3, ep(2), False)]),
            "the census rows are not the scanned sites: %r" % (rows,))
        check(all(set(r) == {"path", "line", "endpoint", "migrated"} for r in rows),
              "a census row must carry exactly path, line, endpoint, migrated: %r" % (rows,))
        cw.write_census(root)
        rows2 = census_rows(root)
        check(rows2 == rows, "a second write_census appended instead of rewriting: %r then %r"
              % (rows, rows2))
    shutil.rmtree(str(root), ignore_errors=True)


def test_3_a_new_direct_post_fails_the_check_by_name():
    """Case 3 -- THE RATCHET. A direct POST added after the census is written must fail the check."""
    root = make_root()
    write_file(root, "scripts/probe.py", site(0))
    cw.write_census(root)
    write_file(root, "scripts/sneaky.py", site(0))
    rc, out = run_check(root)
    check(rc is not None and rc != 0, "check() returned %r after a new direct POST appeared at "
          "scripts/sneaky.py; growth on an engine endpoint must fail the check, not pass it" % (rc,))
    check("GROWTH" in out, "the output does not say GROWTH: %r" % (out,))
    check("scripts/sneaky.py" in out, "the growth finding does not name the file that grew: %r"
          % (out,))
    shutil.rmtree(str(root), ignore_errors=True)


def test_4_a_missing_census_is_a_finding_not_a_pass():
    """Case 4 -- the ratchet must not read as clean before it exists (the t235 shape: detection
    that is documentation, a label nobody runs)."""
    root = make_root()
    write_file(root, "scripts/probe.py", site(0))
    rc, out = run_check(root)
    check(rc is not None and rc == 2, "check() returned %r with no census on disk; a population "
          "that has never been counted is not a clean population -- 2 (a finding), never 0" % (rc,))
    check("MISSING" in out and "call-sites.jsonl" in out,
          "the output does not name the census that is missing: %r" % (out,))
    shutil.rmtree(str(root), ignore_errors=True)


def test_5_a_census_that_no_longer_matches_the_scan_is_stale():
    """Case 5 -- a site migrated away without regenerating the census. The census is the
    denominator: a drifted count must be loud, not quietly trusted."""
    root = make_root()
    write_file(root, "scripts/probe.py", site(0))
    write_file(root, "scripts/calibrate.py", site(1))
    cw.write_census(root)
    (root / "scripts" / "calibrate.py").unlink()
    rc, out = run_check(root)
    check(rc is not None and rc != 0, "check() returned %r with a census naming a site the scan "
          "no longer finds; a stale census must fail, not read as clean" % (rc,))
    check("STALE" in out, "the output does not say STALE: %r" % (out,))
    check("scripts/calibrate.py" in out, "the stale finding does not name the site: %r" % (out,))
    shutil.rmtree(str(root), ignore_errors=True)


def test_6_a_matching_census_is_clean_and_the_fit_verdict_is_untouched():
    """Case 6 -- the anti-regression pin on t301: a tree that fits with a matching census exits 0
    and still prints the working-set line."""
    root = make_root()
    write_file(root, "scripts/probe.py", site(0))
    cw.write_census(root)
    rc, out = run_check(root)
    check(rc is not None and rc == 0, "check() returned %r on a tree that fits and whose census "
          "matches its scan; the fit verdict must not get stricter" % (rc,))
    check("working set" in out and "fits" in out,
          "the fit line is gone from check()'s output: %r" % (out,))
    check("GROWTH" not in out and "STALE" not in out and "CORRUPT" not in out,
          "a clean census reported a finding: %r" % (out,))
    shutil.rmtree(str(root), ignore_errors=True)


def test_7_a_torn_census_line_is_corrupt_not_ignored():
    """Case 7 -- the census is a JSONL record: the case a JSONL reader exists for. A torn line is
    a finding naming what failed to parse, never a swallowed pass."""
    root = make_root()
    write_file(root, "scripts/probe.py", site(0))
    cw.write_census(root)
    p = root / "workspace" / "call-sites.jsonl"
    p.write_text(p.read_text(encoding="utf-8") + '{"path": "scripts/probe.py", "line"\n',
                 encoding="utf-8")
    rc, out = run_check(root)
    check(rc is not None and rc == 2, "check() returned %r with a torn census line on disk; a record "
          "that cannot be parsed must be a finding (2), not a pass" % (rc,))
    check("CORRUPT" in out, "the output does not say CORRUPT: %r" % (out,))
    check("JSONDecodeError" in out or "Expecting" in out,
          "the corrupt finding does not name what failed to parse: %r" % (out,))
    shutil.rmtree(str(root), ignore_errors=True)


def test_8_a_growth_finding_names_the_line_and_the_endpoint():
    """Case 8 -- a reader must be able to ACT: the finding names file, line and endpoint, not a
    bare number that differs from a ceiling."""
    root = make_root()
    write_file(root, "scripts/probe.py", site(0))
    cw.write_census(root)
    write_file(root, "scripts/health.py", site(2))     # a second endpoint, a second file
    rc, out = run_check(root)
    check(rc is not None and rc != 0, "check() returned %r after a new direct POST to endpoint %r "
          "appeared; growth on any engine endpoint must fail" % (rc, ep(2)))
    check("scripts/health.py" in out and ep(2) in out and "3" in out,
          "the growth finding does not name the file, the line and the endpoint a reader needs: %r"
          % (out,))
    shutil.rmtree(str(root), ignore_errors=True)


def test_9_the_live_census_rows_point_at_real_lines():
    """Case 9 -- the decay test, and the guard against a scan that invents rows. Reads the REAL
    tree, read-only: if the rule rots (invents rows, points at the wrong line, counts fixtures,
    stops finding the sites that exist) this goes red without anyone remembering to look."""
    rows = cw.scan_direct_calls(cw.REPO)
    check(isinstance(rows, list) and len(rows) >= 30,
          "scan_direct_calls(REPO) returned %r; docs/ENGINES.md:113 says 'about 30' call sites "
          "post to an engine directly, the session review said 40, and 42 were counted by hand on "
          "2026-10-10 -- a count under 30 means the rule is too narrow and the census would read "
          "as clean while the sites sit under it. If this is the ONLY failure in this case, "
          "re-measure the count by hand before changing the rule."
          % (len(rows) if isinstance(rows, list) else rows,))
    if not isinstance(rows, list):
        return
    seen = set()
    for r in rows:
        check(set(r) == {"path", "line", "endpoint", "migrated"},
              "a census row is not the four required fields: %r" % (r,))
        key = (r.get("path"), r.get("line"))
        check(key not in seen, "duplicate census row %r: a site counted twice inflates the "
                               "population" % (key,))
        seen.add(key)
        rel = r.get("path")
        f = cw.REPO / str(rel)
        check(f.is_file(), "a census row names a file that does not exist: %r" % (rel,))
        check("/tests/" not in "/" + str(rel),
              "a census row is a test fixture: %r -- counting fixtures inflates the production "
              "population, and this suite's own oracle is one" % (rel,))
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines() if f.is_file() else []
        ln = r.get("line")
        ok_line = isinstance(ln, int) and 1 <= ln <= len(lines)
        check(ok_line and str(r.get("endpoint")) in (lines[ln - 1] if ok_line else ""),
              "census row %r is not a real line that really contains that endpoint" % (r,))
    check(all(isinstance(r.get("migrated"), bool) for r in rows),
          "the migrated flag must be a bool per row: %r"
          % ([r for r in rows if not isinstance(r.get("migrated"), bool)][:3],))
    for rel in cw.ALLOWED_DIRECT:
        mine = [r for r in rows if r.get("path") == rel]
        check(all(r["migrated"] is True for r in mine),
              "%s is allowlisted, so its rows must carry migrated True: %r" % (rel, mine))


if __name__ == "__main__":
    for _name in sorted(n for n in dir() if n.startswith("test_")):
        try:
            globals()[_name]()
        except Exception as _exc:   # recorded, never swallowed: on main this IS the missing change
            FAILS.append("%s raised %s: %s" % (_name, type(_exc).__name__, _exc))
            print("FAIL: %s raised %s: %s" % (_name, type(_exc).__name__, _exc))
    print("%d failure(s)" % len(FAILS))
    sys.exit(1 if FAILS else 0)
