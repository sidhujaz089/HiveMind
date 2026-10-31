#!/usr/bin/env python3
"""The ceiling keeps itself honest: green suites come OFF, the count equals the list, nothing is guessed.

With production on, this drifted twice in one hour: a suite production declared failing outran the
committed number (the "may not rise" check read the line's own declaration as growth), and suites that
went green sat on the list warning about nothing. Jas, 2026-10-27: "Yes make the sweep good idea."

The three things this pins: it REMOVES a green suite, it SYNCS the promised count, and it LEAVES ALONE a
suite with no recorded verdict -- a suite nobody has run is a question, and a job that answers it is an
instrument that guesses.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def fixture(tmp, declared, verdicts, count):
    d = Path(tmp)
    (d / "workspace").mkdir(parents=True)
    (d / "workspace" / "test-failures-ceiling.json").write_text(json.dumps(
        {"allowed_to_fail": declared, "set": "x"}))
    (d / "workspace" / "test-failures-ceiling-count.json").write_text(json.dumps(
        {"count": count, "why": "test"}))
    rows = [{"suite": k, "exit_code": v, "ts": "2026-10-28T00:00:00Z"} for k, v in verdicts.items()]
    (d / "workspace" / "suite-state.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")
    return d


def main():
    import ceiling_maintain as CM
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'BAD'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    # A green suite comes off; a red one stays; an unrecorded one is left alone and named.
    with tempfile.TemporaryDirectory() as tmp:
        d = fixture(tmp, ["a.py", "b.py", "c.py"], {"a.py": 0, "b.py": 1}, 3)
        drop, declared, count_before, unrecorded = CM.plan(d)
        check("a green suite is the only thing dropped", drop == ["a.py"], str(drop))
        check("  the declared number follows the drop", declared == 2, str(declared))
        check("  a suite with no verdict is named, not guessed", unrecorded == ["c.py"], str(unrecorded))
        changed, _ = CM.repair(d)
        after = json.loads((d / "workspace" / "test-failures-ceiling.json").read_text())
        cnt = json.loads((d / "workspace" / "test-failures-ceiling-count.json").read_text())["count"]
        check("  after the repair the list and the number agree", ("a.py" not in after["allowed_to_fail"]
              and "c.py" in after["allowed_to_fail"] and cnt == 2), f"{after['allowed_to_fail']} {cnt}")
        check("  and it says what it did", bool(changed), str(changed))

    # The count lagging the list is repaired on its own, with nothing to drop.
    with tempfile.TemporaryDirectory() as tmp:
        d = fixture(tmp, ["a.py"], {"a.py": 1}, 0)
        changed, _ = CM.repair(d)
        cnt = json.loads((d / "workspace" / "test-failures-ceiling-count.json").read_text())["count"]
        check("a count that lags the declared list is synced", cnt == 1, f"count={cnt} changed={changed}")

    # Nothing to do: no change, and no commit attempted.
    with tempfile.TemporaryDirectory() as tmp:
        d = fixture(tmp, ["a.py"], {"a.py": 1}, 1)
        check("an honest ceiling changes nothing", CM.repair(d)[0] == [], "expected no changes")

    check("it never ADDS a suite: the ceiling file only ever shrinks by this job",
          "d[\"allowed_to_fail\"] = [n for n in d[\"allowed_to_fail\"] if n not in set(drop)]"
          in (ROOT / "scripts" / "ceiling_maintain.py").read_text())
    print("test_ceiling_maintain:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
