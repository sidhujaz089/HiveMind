#!/usr/bin/env python3
"""coder.py refuses an edit that introduces a name bound nowhere (code_shape, 2026-10-01).

The lesson of coder/brief-3c1c1ede (`OLLAMA_URL = settings.request` with no import: a NameError
on import). Real entry point, throwaway clone, a fake model whose edit parses but uses an
undefined name. The attempt must end BAD-SHAPE, naming the name, and nothing is accepted.
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coder_harness as H  # noqa: E402

OPS = {"ops": [{"path": "scripts/jsonx.py", "old": "def objects(text):",
                "new": "ROGUE = not_defined_anywhere_xyz\n\n\ndef objects(text):"}],
       "why": "an edit that parses and cannot run"}
FAILED = 0


def report(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


def main():
    tmp = H.sandbox("badshape-")
    srv = None
    try:
        clone, err = H.build_clone(tmp)
        if clone is None:
            print(err)
            return 1
        srv, port = H.serve_ops(tmp, OPS)
        r = H.run_coder(clone, port, "add a constant to jsonx", files=["scripts/jsonx.py"])
        rows = H.ledger_rows(clone)
        trail = (rows[-1].get("trail") or []) if rows else []
        report("the attempt is refused as BAD-SHAPE", [t.get("outcome") for t in trail][:1], ["BAD-SHAPE"])
        report("and the reason names the unbound name",
               "not_defined_anywhere_xyz" in str(trail[:1]), True)
        report("nothing is accepted", str(rows[-1].get("outcome", "")).startswith("ACCEPTED") if rows else None, False)
        if FAILED:
            print(r.stdout[-2500:], r.stderr[-1000:])
    finally:
        if srv:
            srv.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    print("test_coder_bad_shape:", "FAILED" if FAILED else "ok")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
