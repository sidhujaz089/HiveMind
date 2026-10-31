#!/usr/bin/env python3
"""Every BLOCK check in library_checks fires on its defect AND stays quiet on the safe form.

A check that fires on everything is bypassed within a week; one that fires on nothing is
decoration. Both directions, per check, plus the two scoping rules: tests are not checked
(fixtures must contain defects), and prose about a defect is not the defect.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import library_checks as LC  # noqa: E402
PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED, FAILED = PASSED + ok, FAILED + (not ok)
    print(f"  [{'ok' if ok else 'BAD'}]   {label}: {got}")


def ids(text, rel="scripts/x.py"):
    return sorted({c for c, *_ in LC.run(rel, text)})


CASES = [
    ("truncates-record", 'row["parse_error"] = answer[:400]\n', 'row["parse_error"] = answer\n'),
    ("greedy-extract", 'm = re.search(r"{.*}", raw, re.S)\n', 'r = jsonx.one(raw, ["ops"])\n'),
    ("secret", 'KEY = "sk-ant-' + "a" * 30 + '"\n', 'KEY = os.environ["ANTHROPIC_API_KEY"]\n'),
    ("process-match-by-name", 'subprocess.run(["pgrep", "-f", "coder.py"], timeout=5)\n',
     'subprocess.run(["pgrep", "-x", "ollama"], timeout=5)\n'),
    ("ps-command-column", 'subprocess.run(["ps", "-eo", "pid,etime,cmd"], timeout=5)\n',
     'subprocess.run(["ps", "-eo", "pid=,etimes="], timeout=5)\n'),
]
for cid, bad, good in CASES:
    report(f"{cid} fires on the defect", cid in ids(bad), True)
    report(f"{cid} is quiet on the safe form", cid in ids(good), False)
report("a comment describing a defect is not the defect", ids('# never do row["e"] = answer[:400]\n'), [])
report("a docstring describing a defect is not the defect", ids('"""row["e"] = answer[:400] destroys evidence."""\n'), [])
report("test suites are not checked", LC.run("scripts/tests/test_x.py", 'row["e"] = answer[:400]\n'), [])
report("a named exemption is honoured",
       "process-match-by-name" in ids('x = ["pgrep", "-f", pat]\n', rel="scripts/selfmatch.py"), False)
report("every check blocks (Jas: block the warnings; the ratchet makes it safe)",
       {c[1] for c in LC.CHECKS}, {"block"})
report("a silent content slice is caught",
       "silent-content-slice" in ids('body = p.read_text()[:5000]\n'), True)
report("a model call that never reads why it stopped is caught",
       "model-run-no-stop-reason" in ids('u = "http://127.0.0.1:11434/api/chat"\n'), True)
report("a listing call that cannot be cut off is not flagged",
       "model-run-no-stop-reason" in ids('u = "http://127.0.0.1:11434/api/tags"\n'), False)
report("and one that does read it is not",
       "model-run-no-stop-reason" in ids('u = "http://127.0.0.1:11434/api/chat"\nwhy = d["done_reason"]\n'), False)
report("the four judgement checks are defined for the auditor model",
       {m[0] for m in LC.MODEL_CHECKS} >= {"truncation", "path-as-text", "import-time-path",
                                             "absence-as-success", "unlocked-shared-write"}, True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
