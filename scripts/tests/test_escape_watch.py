#!/usr/bin/env python3
"""t208: a defect reported in a file a kept build changed is a possible escape; before the merge,
or about another file, or by the build's own task, it is not."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import escape_watch as E  # noqa: E402

fails = []


def ck(label, ok):
    print(f"  [{'ok' if ok else 'BAD'}]  {label}")
    if not ok:
        fails.append(label)


E.kept_merges = lambda days: [("abc12345", "2026-10-01T12:00:00+00:00", "coder/brief-aa", "t100",
                                ["scripts/widget.py"])]
E.open_tasks = lambda: [
    ({"id": "t201", "title": "widget.py crashes on empty input", "created": "2026-10-01T130000Z"}, ""),
    ({"id": "t202", "title": "widget.py was slow", "created": "2026-09-30T130000Z"}, ""),
    ({"id": "t203", "title": "gadget.py crashes", "created": "2026-10-01T130000Z"}, ""),
    ({"id": "t100", "title": "the build's own task names widget.py", "created": "2026-10-01T130000Z"}, ""),
    ({"id": "t204", "title": "Scope RCA: oracle-weak", "created": "2026-10-01T130000Z"}, "scripts/widget.py"),
]
got = [c["task"] for c in E.candidates()]
ck("a defect filed AFTER the merge naming its file is a candidate", "t201" in got)
ck("one filed BEFORE the merge is not", "t202" not in got)
ck("one about another file is not", "t203" not in got)
ck("the build's own task is not", "t100" not in got)
ck("a Scope RCA collector is not", "t204" not in got)
for f in fails:
    print("FAIL:", f)
print("test_escape_watch:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
