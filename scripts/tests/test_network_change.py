#!/usr/bin/env python3
"""The network map has one write path, and its changelog can be replayed (N3, t8440).

The chart's own history is why this exists: `FLOW-CHANGELOG.jsonl` has 82 consecutive rows and SIX do
not chain -- row N's `after` is not row N+1's `before` -- so the chart was changed outside
`flow_change.py`, and the changelog cannot be replayed to the present state. A changelog nobody can
replay is a log, not a ledger.

What this suite refuses:

  * a write that would make the map invalid -- a state outside the declared vocabulary, a `joins` naming
    an entity that is not there, a `source.file` that does not exist. The tool validates with the SAME
    function the bar runs, so it cannot create what the checker would reject;
  * a refused write that changes the file anyway (the refusal must leave it byte-identical);
  * a duplicate id, because every reference to it would be ambiguous;
  * a guard that passes a file the tool did not write -- the whole point is provenance;
  * ROWS THAT DO NOT CHAIN. This is the assertion the chart would fail: row N's `after` must equal row
    N+1's `before`, so the changelog itself proves nothing was changed behind it.

Hermetic: NETWORK and LOG point into a temp dir, and the documents the map points at are stubbed there.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import network_change as nc  # noqa: E402

LIVE_MAP = ROOT / "workspace" / "network.json"
POINTED_AT = ("projects/local-inference/models/SERVERS.json", "docs/ACCESS.md",
              "docs/TOOLCHAIN.md", "AGENTS.md")
FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def setup(tmp):
    """A temp repo whose map is the real one, with every file it points at stubbed."""
    repo = Path(tmp)
    for rel in POINTED_AT:
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("stub\n", encoding="utf-8")
    (repo / "workspace").mkdir(parents=True, exist_ok=True)
    net = repo / "workspace" / "network.json"
    shutil.copy(LIVE_MAP, net)
    nc.NETWORK = net
    nc.LOG = repo / "workspace" / "NETWORK-CHANGELOG.jsonl"
    return repo, net


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return False
    except SystemExit:
        return True


def main():
    with tempfile.TemporaryDirectory() as tmp:
        repo, net = setup(tmp)
        original = net.read_text(encoding="utf-8")

        # 0. GUARD REFUSES A FILE WITH NO CHANGELOG: the map on disk predates the tool.
        check("guard refuses a map with no changelog rows", len(nc.guard()) == 1, nc.guard())

        # 1. SEED ADOPTS IT, and then the guard passes: row 1's after IS the file.
        nc.seed("test adoption")
        check("seed writes row 1", len(nc._rows()) == 1, nc._rows())
        check("guard passes after seed", nc.guard() == [], nc.guard())

        # 2. A REFUSED WRITE CHANGES NOTHING. State outside the vocabulary.
        before = net.read_text(encoding="utf-8")
        check("set state=banana is refused", refused(nc.set_field, "gx10", "state", "banana"))
        check("  ... and the file is unchanged", net.read_text(encoding="utf-8") == before)

        # 3. A LINK TO NOTHING IS REFUSED: referential integrity, enforced at write time.
        check("set joins to a missing entity is refused",
              refused(nc.set_field, "link-tailnet", "joins", '["gx10", "ghost"]'))

        # 4. A POINTER TO A FILE THAT IS NOT THERE IS REFUSED.
        check("set source to a missing file is refused",
              refused(nc.set_field, "gx10", "source", '{"file": "docs/NOT-THERE.md"}'))

        # 5. A VALID WRITE LANDS, and the row records before/after.
        n = nc.set_field("gx10", "_note", "written by the tool in a test")
        rows = nc._rows()
        check("a valid set writes a row", n == 2 and rows[-1]["n"] == 2, n)
        check("  the row carries before and after", rows[-1].get("before") and rows[-1].get("after"))

        # 6. ADD AND DUPLICATE-ID REFUSAL.
        ent = {"id": "test-device", "what": "a device the test invented", "kind": "device",
               "owner": "jas", "reached_by": "lan", "state": "unknown",
               "source": {"file": "AGENTS.md"}}
        nc.add(ent)
        check("add writes a row", nc._rows()[-1]["n"] == 3, nc._rows()[-1]["n"])
        check("adding the same id twice is refused", refused(nc.add, ent))

        # 7. RETIRE KEEPS THE REASON.
        nc.retire("test-device", "the test is over")
        e = [x for x in nc.load()["entities"] if x["id"] == "test-device"][0]
        check("retire sets state and keeps why", (e.get("state"), e.get("_retired")) ==
              ("retired", "the test is over"), e.get("state"))

        # 8. THE GUARD REFUSES A HAND EDIT: the file no longer matches the newest row.
        doc = nc.load()
        doc["entities"].append({"id": "sneaked-in", "what": "hand added", "kind": "service",
                                "owner": "jas", "reached_by": "loopback", "state": "up",
                                "source": {"file": "AGENTS.md"}})
        net.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        probs = nc.guard()
        check("guard refuses a file edited outside the tool", len(probs) == 1, probs)

        # 9. REVERT RESTORES THE PREVIOUS DOCUMENT, as a new row (so it can be undone).
        rows = nc._rows()
        nc.revert(rows[-1]["n"])
        check("revert restores the row's before", nc.guard() == [], nc.guard())

        # 10. THE CHANGELOG CHAINS while every write went through the tool. This is the property the
        #     chart's changelog fails, and it is what makes a replay possible.
        rows = nc._rows()
        through_the_tool = [r for r in rows if r["n"] <= 4]
        breaks = [(a["n"], b["n"]) for a, b in zip(through_the_tool, through_the_tool[1:])
                  if a.get("after") != b["before"]]
        check("rows written through the tool chain", breaks == [], breaks)

        # 10b. AN OUT-OF-BAND EDIT SHOWS UP AS A DISCONTINUITY, and that is not a bug to hide: the row's
        #      `before` is the state the tool actually found, so a hand edit is visible in the ledger
        #      rather than silently absorbed. This is how a replay can be trusted.
        all_rows = nc._rows()
        all_breaks = [(a["n"], b["n"]) for a, b in zip(all_rows, all_rows[1:])
                      if a.get("after") != b["before"]]
        check("a hand edit between rows is recorded as a visible break", all_breaks == [(4, 5)], all_breaks)

    # 11. THE REAL CHANGELOG IN THE REPOSITORY CHAINS TOO.
    live_rows = nc._rows(ROOT / "workspace" / "NETWORK-CHANGELOG.jsonl")
    live_breaks = [(a["n"], b["n"]) for a, b in zip(live_rows, live_rows[1:])
                   if a.get("after") != b.get("before")]
    check("the committed changelog chains", live_breaks == [], live_breaks)
    check("and its newest row equals the committed map",
          live_rows and live_rows[-1]["after"] == json.loads(LIVE_MAP.read_text(encoding="utf-8")),
          "the map and the changelog disagree in the repository")

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
