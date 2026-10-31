#!/usr/bin/env python3
"""Intake (Jas, 2026-10-14; embedder only, no model judgement): the description excludes notes; the
function nearest in meaning is a target; identifiers in the text add exact symbol hits; a row is
current only while the description is unchanged; measure() scores guesses against the files a scope's
chunks edit; scope_batch links two plain-language tasks through their intake files. A fake embedder
and a fake symbol index; nothing live is read or written."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import intake as I  # noqa: E402
import scope_batch as SB  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


body = "---\nid: t1\ntitle: Clean up abandoned temp copies\n---\nRAM fills with leftovers.\n\n## Notes\n\nscripts/dispatch_loop.py failed\n"
d = I.description(body)
report("the description keeps the title and body", "abandoned temp copies" in d and "leftovers" in d, True)
report("the description drops the notes", "dispatch_loop" in d, False)

idx = {"defs": {"sweep_tmp": [{"file": "scripts/tmp_sweep.py", "line": 10, "sig": "sweep_tmp(root)",
                               "doc": "Delete abandoned temporary copies."}],
                "render_board": [{"file": "projects/app-repo/feedback.py", "line": 5, "sig": "render_board()",
                                  "doc": "Draw the Hub board."}],
                "helper": [{"file": "scripts/tests/test_x.py", "line": 1, "sig": "helper()", "doc": "a test"}]},
       "refs": {}, "files": ["scripts/tmp_sweep.py", "projects/app-repo/feedback.py", "scripts/tests/test_x.py"]}
units = I.catalogue(idx)
report("tests are not candidates", any("/tests/" in u for u, _ in units), False)
# A fake embedder: a 2-d vector, 'temp' words point one way, 'board' words the other.
emb = lambda texts, q: [[1.0, 0.0] if ("temp" in t.lower() or "tmp" in t.lower()) else [0.0, 1.0] for t in texts]
vecs = {I._sha(t): emb([t], False)[0] for _u, t in units}
c = I.candidates(d, idx, vecs, embed=emb, top_k=1)
report("the function nearest in meaning is the target", [(t["file"], t["function"]) for t in c["targets"]
                                                         if t["via"] == "embed"], [("scripts/tmp_sweep.py", "sweep_tmp")])
report("its file is ranked first", c["files"][0], "scripts/tmp_sweep.py")
c2 = I.candidates("Fix render_board in the Hub", idx, vecs, embed=emb, top_k=1)
report("an identifier in the text adds an exact symbol hit",
       any(t["via"] == "symbol" and t["file"] == "projects/app-repo/feedback.py" for t in c2["targets"]), True)

have = {"t1": {"task": "t1", "desc_sha": I._sha(d), "files": ["scripts/tmp_sweep.py"]}}
report("a row is current while the description is unchanged", bool(I.current("t1", body, have)), True)
report("an edited description needs a new intake",
       I.current("t1", body.replace("leftovers", "old copies"), have), None)

summary, per = I.measure(have={"t1": {"files": ["scripts/tmp_sweep.py", "scripts/x.py"]}},
                         truth={"t1": {"scripts/tmp_sweep.py"}})
report("measure: precision is hits over guesses", per[0]["precision"], 0.5)
report("measure: recall is hits over real files", per[0]["recall"], 1.0)

m = Path(tempfile.mkdtemp()) / "measure.jsonl"
I.measure = lambda: ({"at": I.time.strftime("%Y-%m-%dT%H:%M:%SZ", I.time.gmtime()), "tasks": 1,
                      "precision": 0.5, "recall": 1.0}, [])
I.measure_daily(m)
I.measure_daily(m)
report("the daily measurement is written once a day", len(m.read_text().splitlines()), 1)

called = []
I.DIR = Path(tempfile.mkdtemp())
I.pending_count = lambda: 10
I.refresh = lambda *a, **k: called.append("refresh") or (0, 0)
I.main(["tick"])
report("below INTAKE_MIN waiting tasks the tick embeds nothing", called, [])
report("the trigger is 50", I.INTAKE_MIN, 50)

bodies = {"t1": body, "t2": "---\nid: t2\ntitle: Stop temp leaks\n---\nno paths named\n"}
files = {"t1": {"scripts/tmp_sweep.py"}, "t2": {"scripts/tmp_sweep.py"}}
report("plain-language tasks link through their intake files",
       SB.members("t1", ["t2"], bodies.get, files=lambda tid, b: files[tid]), ["t1", "t2"])
report("without intake files the same two tasks do not link", SB.members("t1", ["t2"], bodies.get), ["t1"])
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
