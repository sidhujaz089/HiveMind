#!/usr/bin/env python3
"""The network map must POINT AT sources of truth, and a pointer that resolves to nothing is the finding.

Jas, 2026-10-26: *"There is no source of Truth or knowledge or disclosure of the devices that are in the
hive mind and which ones are connected via the MCP or tail net or local area network."* And the
constraint that shapes this file: *"when I do fill it in, it could be with you who are a different
model"* -- a model that has never seen this system must be able to tell which entry is authoritative,
which is a pointer, and which is a hole.

So the checks here are about the SHAPE, not the network:

  * every entity says where its facts live (`source`), or is the authority itself (`address`), or both;
  * a `source.file` must exist -- a pointer to a deleted document is worse than no pointer, because it
    reads as evidence;
  * `kind`, `reached_by` and `state` come from the file's own declared vocabulary, so "state: fine"
    cannot slip in;
  * a link's `joins` must name entities that exist -- the same foreign-key rule the Flow chart learned;
  * ids are unique, because a duplicated id makes every link that names it ambiguous;
  * `state: unknown` is ALLOWED and is not a problem: a named hole with a source is the honest shape,
    and a map that cannot record holes is a map that will be filled with guesses.

Hermetic: the pure function takes a document and a repo path.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace as cw  # noqa: E402

LIVE = ROOT / "workspace" / "network.json"
FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def ent(**kw):
    base = {"id": "x", "what": "something", "kind": "service", "owner": "jas",
            "reached_by": "loopback", "state": "up",
            "source": {"file": "AGENTS.md", "key": "n/a"}}
    base.update(kw)
    return base


def doc(*entities):
    return {"_what": "test", "vocabulary": {"kind": ["device", "link", "service"],
                                            "reached_by": ["cable", "tailnet", "lan", "docker",
                                                           "loopback", "edge", "unknown"],
                                            "state": ["up", "down-declared", "retired", "unknown"]},
            "entities": list(entities)}


def main():
    # 0. THE LIVE MAP IS CLEAN. If this fails, the map is wrong, not the checker.
    live = json.loads(LIVE.read_text(encoding="utf-8"))
    probs = cw.network_map_problems(live, ROOT)
    check("the committed network.json has no problems", probs == [], probs[:3])

    # 1. A POINTER THAT RESOLVES TO NOTHING. This is the whole point of the file.
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        (repo / "AGENTS.md").write_text("x", encoding="utf-8")
        p = cw.network_map_problems(doc(ent(source={"file": "docs/GONE.md"})), repo)
        check("a source.file that does not exist is a problem", any("GONE.md" in x for x in p), p)

        # 2. AN ENTITY THAT IS NEITHER A POINTER NOR AN AUTHORITY. It states a fact with no provenance.
        p = cw.network_map_problems(doc(ent(source=None)), repo)
        check("an entity with no source and no address is a problem", any("source" in x for x in p), p)

        # 3. THE VOCABULARIES COME FROM THE FILE, so a new word is a decision, not a typo that passes.
        p = cw.network_map_problems(doc(ent(state="probably-fine")), repo)
        check("a state outside the declared vocabulary is a problem", any("state" in x for x in p), p)
        p = cw.network_map_problems(doc(ent(kind="router")), repo)
        check("a kind outside the declared vocabulary is a problem", any("kind" in x for x in p), p)

        # 4. REFERENTIAL INTEGRITY: a link that names a device nobody declared.
        p = cw.network_map_problems(doc(ent(id="a"), ent(id="l", kind="link", joins=["a", "ghost"])), repo)
        check("a link naming an undeclared entity is a problem", any("ghost" in x for x in p), p)

        # 5. DUPLICATE IDS make every reference to them ambiguous.
        p = cw.network_map_problems(doc(ent(id="dup"), ent(id="dup")), repo)
        check("a duplicate id is a problem", any("dup" in x for x in p), p)

        # 6. A NAMED HOLE IS NOT A DEFECT. state: unknown with a source is how a gap is recorded, and a
        #    map that rejects it will be filled with guesses instead.
        p = cw.network_map_problems(doc(ent(state="unknown")), repo)
        check("state: unknown is allowed when it carries a source", p == [], p)

        # 7. MISSING REQUIRED FIELDS are named by id and key, so the repair is obvious.
        bad = ent()
        del bad["owner"]
        p = cw.network_map_problems(doc(bad), repo)
        check("a missing required field names the id and the field",
              any("x" in x and "owner" in x for x in p), p)

        # 8. AN UNREADABLE MAP MUST NOT READ AS AN EMPTY ONE.
        p = cw.network_map_problems(None, repo)
        check("a missing document is a problem, not an empty map", bool(p), p)
        p = cw.network_map_problems({"entities": "nope"}, repo)
        check("a malformed entities value is a problem", bool(p), p)

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
