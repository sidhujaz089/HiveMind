#!/usr/bin/env python3
"""t675 oracle: dispatch-loop tick rows are SLIM; the full queue lives in one latest-tick file.

The ledger was 67 MB and 65.3 MB of it was 4,729 tick rows each carrying the whole
queued/skipped listing, while every reader wanted only the NEWEST tick. The fix:
write_tick_row writes the full lists to workspace/run-logs/dispatch-tick-latest.json
(atomic replace) and appends a slim row -- counts and by-kind, never the lists -- to
the ledger. jsonl_cache.latest_tick serves readers from that file.

Written 2026-10-31 for the cloud harness run. RED before the fix because the ledger
row would carry the full queued/skipped lists (or write_tick_row would not exist).
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

_failures = []


def check(label, cond, detail=""):
    line = "  [%s] %s" % ("ok" if cond else "BAD", label)
    if not cond and detail:
        line += " -- " + detail
    print(line)
    if not cond:
        _failures.append(label)


def main():
    print("t675 slim-tick-row oracle (dispatch_loop.write_tick_row)\n")
    try:
        import dispatch_loop
    except Exception as e:  # noqa: BLE001 -- reported and fatal, not swallowed
        print("ORACLE BUG (import failed): %s: %s" % (type(e).__name__, e))
        return 2

    have = hasattr(dispatch_loop, "write_tick_row")
    check("dispatch_loop.write_tick_row exists", have,
          "tick rows are still written inline with their full lists")
    if not have:
        print("\n%d failed" % len(_failures))
        return 1

    queued = [{"kind": "production", "chunk": "p1-t1-a"},
              {"kind": "production", "chunk": "p2-t2-b"},
              {"kind": "review", "chunk": "p3-t3-c"}]
    skipped = [{"chunk": "p4-t4-d", "why": "held"}]
    ledger_rows = []

    with tempfile.TemporaryDirectory() as td:
        latest = Path(td) / "run-logs" / "dispatch-tick-latest.json"
        full = dispatch_loop.write_tick_row(
            running=[], priority=[], started=[], preempted=[],
            queued=queued, skipped=skipped, idle=True,
            record=ledger_rows.append, latest_tick_path=latest)

        check("exactly one ledger row is appended per tick", len(ledger_rows) == 1,
              "got %d row(s)" % len(ledger_rows))
        slim = ledger_rows[0] if ledger_rows else {}
        check("the ledger row does NOT carry the queued list", "queued" not in slim,
              "the 65.3 MB class: full lists back in the ledger")
        check("the ledger row does NOT carry the skipped list", "skipped" not in slim)
        check("the ledger row keeps the event name", slim.get("event") == "tick",
              "got %r" % (slim.get("event"),))
        check("the ledger row carries queued_n (the count)", slim.get("queued_n") == 3,
              "got %r" % (slim.get("queued_n"),))
        check("the ledger row carries skipped_n", slim.get("skipped_n") == 1,
              "got %r" % (slim.get("skipped_n"),))
        check("the ledger row carries queued_by_kind",
              slim.get("queued_by_kind") == {"production": 2, "review": 1},
              "got %r" % (slim.get("queued_by_kind"),))

        check("the latest-tick file exists after the tick (parents created)", latest.is_file())
        tick = json.loads(latest.read_text(encoding="utf-8")) if latest.is_file() else {}
        check("the latest-tick file carries the FULL queued list", tick.get("queued") == queued,
              "got %r" % (tick.get("queued"),))
        check("the latest-tick file carries the FULL skipped list", tick.get("skipped") == skipped)
        check("the latest-tick file is a replace, not an append: no .tmp left behind",
              not latest.with_name(latest.name + ".tmp").exists())

        check("write_tick_row returns the full row (callers may keep using it)",
              full.get("queued") == queued and full.get("skipped") == skipped)

        # A second tick REPLACES the file -- the reader contract is "the newest tick only".
        ledger_rows.clear()
        dispatch_loop.write_tick_row(
            running=[], priority=[], started=[], preempted=[],
            queued=[], skipped=[], idle=True,
            record=ledger_rows.append, latest_tick_path=latest)
        tick2 = json.loads(latest.read_text(encoding="utf-8"))
        check("a second tick replaces the latest-tick file (newest only)",
              tick2.get("queued") == [] and tick2.get("skipped") == [],
              "got queued=%r" % (tick2.get("queued"),))
        check("the second slim row counts zero", ledger_rows
              and ledger_rows[0].get("queued_n") == 0 and ledger_rows[0].get("skipped_n") == 0)

        # jsonl_cache.latest_tick is the readers' path: it serves the full tick from the file.
        import jsonl_cache
        wsdir = Path(td) / "ws"
        (wsdir / "run-logs").mkdir(parents=True)
        ledger = wsdir / "dispatch-loop.jsonl"
        ledger.write_text("", encoding="utf-8")
        dispatch_loop.write_tick_row(
            running=[], priority=[], started=[], preempted=[],
            queued=queued, skipped=skipped, idle=False,
            record=ledger_rows.append,
            latest_tick_path=wsdir / "run-logs" / "dispatch-tick-latest.json")
        served = jsonl_cache.latest_tick(ledger)
        check("jsonl_cache.latest_tick serves the full queue from the latest-tick file",
              served.get("queued") == queued and served.get("skipped") == skipped,
              "got %r" % (served,))

    n = len(_failures)
    print("\n%d failed -- %s" % (n, "tick rows are slim and the full queue is on disk"
                                 if not n else "the slim-tick contract does not hold"))
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
