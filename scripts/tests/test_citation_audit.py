#!/usr/bin/env python3
"""citation_audit (t413): a cited file that never existed is INVENTED; one proposed for creation is
not; a line past the end is caught; a real citation is ok."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import citation_audit as C  # noqa: E402

FAILED = 0


def report(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


def kinds(text):
    return [k for k, _ in C.audit_text(text)]


def main():
    report("a real file is ok", kinds("see `scripts/jsonx.py`"), ["ok"])
    report("a real line is ok", kinds("see `scripts/jsonx.py:1`"), ["ok"])
    report("a line past the end is caught", kinds("see `scripts/jsonx.py:999999`"), ["line-past-end"])
    report("a never-existing file is invented", kinds("as shown in `scripts/no_such_file_qz.py`"), ["invented"])
    report("one proposed for creation is not", kinds("create a new test `scripts/tests/test_qz_new.py`"), ["proposed"])
    report("an elided path is not a claim", kinds("`workspace/tasks/t1--...md`"), [])
    print("test_citation_audit:", "FAILED" if FAILED else "ok")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
