#!/usr/bin/env python3
"""B1-B4 (scheduler rebuild, 2026-10-30): the Scheduler, Logs and Throughput pages, and help generated from code.

  * the constants table is GENERATED from source: numbers only, with the comment above or beside each;
  * the Logs page opens only run logs -- a path from the URL cannot leave LOG_ROOTS;
  * the tok/s series is the counter's delta over time, and an engine restart (counter going down) is a gap,
    not a dip;
  * help for every page carries what its Python sources say about themselves (code_notes);
  * the routes exist. Source-level for feedback.py, because importing it starts a server.
Fixtures only; no service, engine or journal is touched.

Run: python3 scripts/tests/test_pages_ops.py
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "app-repo"))
sys.path.insert(0, str(REPO / "scripts"))
import hub_common as H  # noqa: E402
import pages_ops as P  # noqa: E402

FAILED = 0


def check(name, ok, detail=""):
    global FAILED
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {name}" + ("" if ok else f"\n         got: {detail}"))


with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "scripts").mkdir()
    (root / "scripts" / "knobs.py").write_text(
        '"""Knobs.\n\nSecond paragraph."""\n'
        'from pathlib import Path\n'
        '# How long a lane may stay silent\n# before it is called lost.\n'
        'LOST_AFTER_S = 120\n'
        'WINDOW_S = 15 * 60  # the storm window\n'
        'BARE = 3\n'
        'LEDGER = Path("x") / "y.jsonl"\n'
        'NAME = "swift"\n'
        'EXITS = (4, -15)\n'
        'lower_case = 9\n')
    rows = {n: (v, c) for n, v, c in P.tuning_constants("scripts/knobs.py", root)}
    check("a constant's comment block above it is its explanation",
          rows.get("LOST_AFTER_S") == ("120", "How long a lane may stay silent before it is called lost."), rows)
    check("an inline comment is the explanation when there is none above, and arithmetic is shown as written",
          rows.get("WINDOW_S") == ("15 * 60", "the storm window"), rows.get("WINDOW_S"))
    check("an uncommented constant is listed with an empty explanation, not skipped",
          rows.get("BARE") == ("3", ""), rows.get("BARE"))
    check("paths, strings and lower-case names are not knobs",
          not ({"LEDGER", "NAME", "lower_case"} & set(rows)), sorted(rows))
    check("a short tuple of numbers is a knob", rows.get("EXITS", ("",))[0] == "(4, -15)", rows.get("EXITS"))

    (root / "workspace" / "run-logs" / "dispatch").mkdir(parents=True)
    (root / "workspace" / "run-logs" / "dispatch" / "a.log").write_text("".join(f"line {i}\n" for i in range(500)))
    (root / "workspace" / "top.log").write_text("top\n")
    (root / "workspace" / "secret.json").write_text("{}")
    check("a run log opens", P.safe_log_path("workspace/run-logs/dispatch/a.log", root) is not None)
    check("a top-level workspace/*.log opens", P.safe_log_path("workspace/top.log", root) is not None)
    for bad in ("workspace/secret.json", "workspace/run-logs/../secret.json", "../../etc/passwd",
                "scripts/knobs.py", "/etc/passwd", "workspace/run-logs/dispatch/a.log\x00"):
        check(f"refused: {bad!r}", P.safe_log_path(bad, root) is None)
    lines, cut = P.tail(root / "workspace" / "run-logs" / "dispatch" / "a.log", 50)
    check("a tail is the LAST lines and says it was cut", (lines[-1], len(lines), cut) == ("line 499", 50, True),
          (lines[-1:], len(lines), cut))
    page = P.logs_page("file:workspace/run-logs/dispatch/a.log", 50, root)
    check("the log page names the cut", "earlier lines are NOT shown" in page, page[:300])

    code = H.code_notes({"source": ["scripts/knobs.py (the knobs)", "a prose source"]}, root)
    check("help is generated from each Python source's own docstring (first paragraph)",
          code == [("scripts/knobs.py", "Knobs.")], code)

P._SAMPLES.clear()
reads = iter([(1000.0, 0, 2.0), (1500.0, 0, 3.0), (100.0, 0, 1.0), (300.0, 0, 1.0)])
pts = []
for t in (0.0, 5.0, 10.0, 15.0):
    pts = P.sample(now=t, read=lambda: next(reads))
check("tok/s is the counter's delta over the gap", [p["tok_s"] for p in pts][:1] == [100.0], pts)
check("a counter that went DOWN (engine restart) is left out, not drawn as a dip",
      [p["tok_s"] for p in pts] == [100.0, 40.0], pts)
check("  and each point carries the requests running", pts[0]["running"] == 3.0, pts)
P._SAMPLES.clear()
check("a reading closer than MIN_GAP_S is not taken twice",
      len([P.sample(now=50.0, read=lambda: (1.0, 0, 0)), P.sample(now=50.5, read=lambda: 1 / 0)]) == 2)
P._SAMPLES.clear()

src = (REPO / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
for route in ('"/scheduler"', '"/logs"', '"/models/throughput"', '"/models/throughput.json"'):
    check(f"route {route} exists", f"path == {route}" in src)
check("the help page renders the generated part", "code_notes(e)" in src)
check("the throughput page polls its JSON", "/models/throughput.json" in P.throughput_page())
check("the scheduler page links where each editable value IS edited",
      'href="/devices"' in P.scheduler_page() and 'href="/models"' in P.scheduler_page())

print(f"test_pages_ops: {'ok' if not FAILED else f'{FAILED} failed'}")
sys.exit(1 if FAILED else 0)
