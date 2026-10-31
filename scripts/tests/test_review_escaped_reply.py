"""A script review whose model wrote its answer INSIDE its own unclosed JSON string is read, not
given up on (2026-10-02, gateway.py: three identical 'invalid-json' replies, the third capped it)."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import truncation_review as T

REPLY = ('{"findings": [{"check": "truncation", "line": 2, "silent": true, "why": "x is cut, but wait, '
         'actually it is not. \\n\\nRe-checking.\\n\\n```json\\n{\\"findings\\": [{\\"check\\": '
         '\\"truncation\\", \\"line\\": 3, \\"silent\\": true, \\"severity\\": \\"low\\", \\"why\\": '
         '\\"y[:5] drops the rest\\"}]}\\n```')
d = Path(tempfile.mkdtemp(dir=T.REPO / "workspace"))
try:
    f = d / "s.py"
    f.write_text("a = 1\nx = 'abc'\ny = z[:5]\n")
    rel = str(f.relative_to(T.REPO))
    r = T.review(rel, ask_fn=lambda p: (REPLY, "stop"))
    ok = r["status"] == "ok" and [x.get("line") for x in r["findings"] if x["check"] != "off-flow"] == [3]
    print(f"  [{'ok' if ok else 'FAIL'}]   an answer escaped inside an unclosed string is read: {r['status']}")
    plain = T.review(rel, ask_fn=lambda p: ('{"findings": []}', "stop"))
    ok2 = plain["status"] == "ok"
    print(f"  [{'ok' if ok2 else 'FAIL'}]   a plain reply is unchanged: {plain['status']}")
    junk = T.review(rel, ask_fn=lambda p: ('{"findings": [{"why": "never \\"closed', "stop"))
    ok3 = junk["status"].startswith("unreadable")
    print(f"  [{'ok' if ok3 else 'FAIL'}]   real junk is still unreadable: {junk['status']}")
finally:
    import shutil; shutil.rmtree(d)
n = sum((ok, ok2, ok3))
# auto_merge.py's shape: the outer object never closed ("}] at the end, no final }).
d2 = Path(tempfile.mkdtemp(dir=T.REPO / "workspace"))
try:
    g = d2 / "s.py"
    g.write_text("a = 1\nb = 2\ny = z[:5]\n")
    unclosed = ('{"findings": [{"check": "truncation", "line": 3, "silent": true, "severity": "low", '
                '"why": "y[:5] drops the rest. I will stick with 3."}]')
    r4 = T.review(str(g.relative_to(T.REPO)), ask_fn=lambda p: (unclosed, "stop"))
    ok4 = r4["status"] == "ok" and [x.get("line") for x in r4["findings"] if x["check"] != "off-flow"] == [3]
    print(f"  [{'ok' if ok4 else 'FAIL'}]   an unclosed outer object is repaired and read: {r4['status']}")
finally:
    shutil.rmtree(d2)
n += ok4
print(f"\n{n} passed, {4 - n} failed")
sys.exit(0 if n == 4 else 1)
