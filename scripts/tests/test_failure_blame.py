#!/usr/bin/env python3
"""Jas's failure classes, mechanically: worker-output-rejected / harness-fault / stale-record.

Jas, 2026-10-25: "I know we will have quality issues ... when things fail, the reason for that becomes
something mechanical upstream." And: "Great idea about failure classes. Please add that!"

The schema already carried a mature four-way taxonomy (content, external, harness-capability,
environment-precondition) written by hand for t366 -- his own 2026-09-20 instruction. So this does not
add a second vocabulary: it maps the mature one onto his words, keeps `external` separate (collapsing a
restart into "harness fault" would send a model chasing a bug that is not there), and adds the ONE
category that was genuinely missing -- stale-record, where the work was fine and the RECORD lied (p487's
crosswise pids, eleven guard-self-test probes, a lane closed late).

Two things it must get right, and they are the two failures that would make the count useless:
  * an unknown cause_category reports `unclassified`, NOT a default that quietly counts as the
    worker's fault;
  * a harness fault generates the upstream item, with a marker so a recurring class does not become a
    new task every night.

Hermetic: the fixtures are temp JSON files in both shapes the real file has carried.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import failure_schema as fs  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def rows(shape="list"):
    data = [{"class": "bad-shape", "cause_category": "harness-capability", "instances": [{}, {}, {}]},
            {"class": "dead-path", "cause_category": "content", "instances": [{}]},
            {"class": "late-lane", "cause_category": "stale-record", "instances": [{}, {}]},
            {"class": "restart", "cause_category": "external", "instances": [{}]}]
    return data if shape == "list" else {r["class"]: r for r in data}


def main():
    # 1. THE MAP, ON THE LIVE SCHEMA.
    m = fs.blame_map()
    check("content is the worker's output being rejected",
          m.get("content") == "worker-output-rejected", m.get("content"))
    check("harness-capability is a harness fault", m.get("harness-capability") == "harness-fault")
    check("environment-precondition is a harness fault too (nothing failed; it was not ready)",
          m.get("environment-precondition") == "harness-fault")
    check("stale-record is its own class -- the work may have been fine", m.get("stale-record") == "stale-record")
    check("external stays separate on purpose", m.get("external") == "external", m.get("external"))
    live = json.loads((ROOT / "workspace" / "failure-schema.json").read_text())
    check("the live schema DECLARES stale-record with a routing rule",
          "stale-record" in (live.get("_cause_categories") or {}),
          list(live.get("_cause_categories") or {}))
    check("the live schema carries the blame mapping", "_blame" in live)

    # 2. AN UNKNOWN CATEGORY IS NOT SILENTLY THE WORKER'S FAULT.
    check("an unknown category is unclassified, not a default",
          fs.blame_of("something-new-nobody-named") == "unclassified")

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for shape in ("list", "dict"):
            p = d / f"{shape}.json"
            p.write_text(json.dumps({"failures": rows(shape),
                                     "_blame": {"content": "worker-output-rejected"}}))
            counts, classes = fs.blame_report(p)
            check(f"[{shape}] the counts add up by blame",
                  counts.get("harness-fault") == 3 and counts.get("worker-output-rejected") == 1
                  and counts.get("stale-record") == 2 and counts.get("external") == 1, counts)
            check(f"[{shape}] the classes behind each blame are named",
                  "bad-shape" in classes.get("harness-fault", []), classes)

            # 3. THE UPSTREAM ITEM.
            items = fs.upstream_items(p)
            check(f"[{shape}] only harness faults owe an item", len(items) == 1, items)
            check(f"[{shape}]   it names the class and carries a dedup marker",
                  items[0]["class"] == "bad-shape" and "[failure-schema:bad-shape]" in items[0]["note"],
                  items[0] if items else None)
            check(f"[{shape}]   and it says repair the harness rather than the brief",
                  "UNCHANGED" in items[0]["note"], items[0]["note"][:80])

        only_worker = d / "worker.json"
        only_worker.write_text(json.dumps({"failures": [
            {"class": "dead-path", "cause_category": "content", "instances": [{}]}]}))
        check("a schema with no harness fault owes nothing", fs.upstream_items(only_worker) == [])

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
