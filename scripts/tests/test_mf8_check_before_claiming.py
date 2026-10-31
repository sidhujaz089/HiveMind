#!/usr/bin/env python3
"""mf8 oracle: check before claiming.

An ANSWER or SIGNATURE post with no command-and-output quote (a line starting with '$ '
and the output under it) is a sentence, not a verification -- lanes.py refuses it. And
before a post asks for a signature, lanes.py shows any answered question on the same
branch, so nobody asks for what the board already answered.

All against a temp LANES_HOME; the live board is never touched. RED before the fix:
answers and signature posts land with no evidence, and nothing is shown before a
signature request.
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="mf8-lanes-")
os.environ["LANES_HOME"] = _tmp
os.environ.pop("HIVEMIND_LANE", None)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import lanes  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    line = "  [%s] %s" % ("ok" if cond else "BAD", label)
    if not cond and detail:
        line += " -- " + detail
    print(line)
    if not cond:
        FAILS.append(label)


def run(args):
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            lanes.main(args)
    except SystemExit as e:
        return (e.code if isinstance(e.code, int) else 1), out.getvalue(), str(e)
    return 0, out.getvalue(), ""


EVIDENCE = "$ python3 scripts/tests/test_x.py\n0 failed (exit 0)"


def main():
    print("mf8 oracle: check before claiming\n")

    rc, _o, _e = run(["ask", "prod-a", "prod-b", "is cloud/t9 green on your side?"])
    check("a question can be asked (fixture setup)", rc == 0, _e)
    qid = [r for r in map(json.loads, (Path(_tmp) / "board.jsonl")
           .read_text(encoding="utf-8").splitlines()) if r["kind"] == "ask"][-1]["qid"]

    rc, _o, err = run(["answer", "prod-b", qid, "yes it is green, trust me"])
    check("an ANSWER with no quoted command is refused", rc != 0,
          "the bare claim was recorded")
    check("  ...and the refusal says what to quote", "$ " in err, repr(err)[:140])

    rc, _o, _e = run(["answer", "prod-b", qid, f"yes -- checked:\n{EVIDENCE}"])
    check("an ANSWER that quotes the command and its output lands", rc == 0, _e)

    rc, _o, err = run(["post", "prod-a", "requesting SIGNATURE for cloud/t9 -- it is done"])
    check("a SIGNATURE post with no quoted command is refused", rc != 0,
          "the bare claim was recorded")

    rc, out, _e = run(["post", "prod-a",
                       f"requesting SIGNATURE for cloud/t9\n{EVIDENCE}"])
    check("a SIGNATURE post WITH evidence lands", rc == 0, _e)
    check("  ...and the already-answered question on the same branch is shown first",
          qid in out and "green" in out, "stdout: %r" % (out[:220],))

    rc, out, _e = run(["post", "prod-a",
                       f"requesting SIGNATURE for cloud/other-branch\n{EVIDENCE}"])
    check("a signature request for ANOTHER branch shows no unrelated answers",
          rc == 0 and qid not in out, "stdout: %r" % (out[:220],))

    rc, _o, _e = run(["post", "prod-a", "ordinary status -- DONE -- abc123"])
    check("an ordinary post (no SIGNATURE, not an answer) needs no quote", rc == 0, _e)

    check("quotes_evidence wants output under the command, not a bare '$ ' line",
          hasattr(lanes, "quotes_evidence")
          and lanes.quotes_evidence("$ cmd\nout") is True
          and lanes.quotes_evidence("$ cmd") is False
          and lanes.quotes_evidence("no quote at all") is False)

    n = len(FAILS)
    print("\n%d failed -- %s" % (n, "claims carry their evidence"
                                 if not n else "mf8 is not in place"))
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
