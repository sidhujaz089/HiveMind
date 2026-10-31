#!/usr/bin/env python3
"""t439 oracle: with a declared --files target, the need path must not serve a
file outside that target, and the refusal must name the declared target.

The 2026-09-27 failed dispatch (p56-comply-coder-swallowed-exception,
gemma4:26b): brief and command line both scoped the run to
projects/local-inference/coder.py, yet the last log line was
`last: ASKED — scripts/library.py` and attempts 5-7 each ended
'asked for 1 file(s), gave 0 new'. Serving an out-of-target `need` silently
turned a one-file dispatch into an unbounded file chase, and the ledger showed
a plain ASKED with no signal that the file was outside the declared target.

Fail-first: today coder.readable's signature is (root, rel)
(coder.py:503), so any `targets=` call is a TypeError, and an out-of-target
path that exists in the sandbox resolves to a real file with an empty error --
the silent drift in the t439 evidence. No subprocess, no network, no live
state: temp dir + one function call.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

TARGET = "projects/local-inference/coder.py"
OTHER = "scripts/library.py"


def main():
    with tempfile.TemporaryDirectory(prefix="t439-") as d:
        root = Path(d)
        (root / "projects" / "local-inference").mkdir(parents=True)
        (root / "projects" / "local-inference" / "coder.py").write_text("x = 1\n")
        (root / "scripts").mkdir()
        (root / "scripts" / "library.py").write_text("y = 2\n")

        # 1. In-target need still resolves (the need contract, coder.py:243-248).
        p, err = coder.readable(root, TARGET, targets=[TARGET])
        assert p is not None and err == "", (p, err)

        # 2. Out-of-target need is refused, not served; the reason names both.
        p, err = coder.readable(root, OTHER, targets=[TARGET])
        assert p is None, f"out-of-target need was served: {p}"
        assert err, "a refusal without a reason is the silent drift this fixes"
        assert TARGET in err, f"refusal must name the declared target, got: {err!r}"
        assert OTHER in err, f"refusal must name the requested file, got: {err!r}"

        # 3. No targets declared -> today's behavior: any sandbox file resolves.
        p, err = coder.readable(root, OTHER)
        assert p is not None and err == "", (p, err)

        # 4. Existing refusals fire first, unchanged (the new check is additive).
        p, err = coder.readable(root, "../escape.py", targets=[TARGET])
        assert p is None and err, (p, err)
        p, err = coder.readable(root, "no/such.py", targets=[TARGET])
        assert p is None and "no such file" in err, (p, err)

    # 5. t437: the refusal reaches the LOG and the ledger row, not only the prompt. Before,
    #    "gave 0 new" was all a reader of the run log could see.
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text(encoding="utf-8")
    assert 'say(f"      refused: {r}")' in src, "the run log must print each refusal"
    assert '" | refused: " + "; ".join(refused)' in src, "the ASKED row must carry the refusals"
    print("t439 oracle: the need path honours the declared --files target")
    return 0


if __name__ == "__main__":
    sys.exit(main())
