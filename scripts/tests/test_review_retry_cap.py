#!/usr/bin/env python3
"""An unreadable script-review is given up on, because it holds the head of the queue.

The scheduler takes the OLDEST pending request every tick. On 2026-09-15
scripts/check_workspace.py came back unreadable 40 times over 7.7 hours and the two requests
behind it were never attempted once -- not a wasted lane, a blocked queue.

Every case here runs against temporary ledger and request files; nothing tracked is written.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

# Two real, tracked, non-test scripts. pending_requests() only returns a request whose file
# exists and still hashes to the requested content, so these have to be real.
OLDER = "scripts/truncation_review.py"
NEWER = "scripts/check_enforcement.py"


def write(p, rows):
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def main():
    import truncation_review as tr
    fails = []
    sha_old, sha_new = tr.sha(REPO / OLDER), tr.sha(REPO / NEWER)
    cap = tr.UNREADABLE_CAP

    with tempfile.TemporaryDirectory() as d:
        led, req = Path(d) / "ledger.jsonl", Path(d) / "requests.jsonl"
        tr.LEDGER, tr.REQUESTS = led, req
        write(req, [{"at": "2026-09-15T01:00:00Z", "path": OLDER, "sha256": sha_old},
                    {"at": "2026-09-15T02:00:00Z", "path": NEWER, "sha256": sha_new}])

        # SETUP SANITY. If these two are filtered for some unrelated reason the cases below
        # would pass while testing nothing.
        write(led, [])
        base = [q["path"] for q in tr.pending_requests()]
        ok = base == [OLDER, NEWER]
        print(f"  [{'ok' if ok else 'BAD'}]    both fixtures are pending, oldest first ({base})")
        if not ok:
            fails.append(f"setup: expected [{OLDER}, {NEWER}], got {base} -- the cases below "
                         f"prove nothing until this holds")

        # 1. ONE SHORT OF THE CAP IS STILL PENDING. A cap that fires early turns a flake into
        #    an abandonment, which is the opposite failure.
        write(led, [{"at": "2026-09-15T03:00:00Z", "path": OLDER, "sha256": sha_old,
                     "status": "unreadable (invalid-json, done_reason=stop)"}] * (cap - 1))
        got = [q["path"] for q in tr.pending_requests()]
        ok = got == [OLDER, NEWER] and not tr.abandoned()
        print(f"  [{'ok' if ok else 'BAD'}]    {cap - 1} unreadable replies is still pending "
              f"({got})")
        if not ok:
            fails.append(f"the cap fired at {cap - 1} attempts, one short: {got}")

        # 2. AT THE CAP IT LEAVES THE QUEUE, AND THE ONE BEHIND IT IS REACHED. This is the
        #    whole bug: the second half matters more than the first.
        write(led, [{"at": "2026-09-15T03:00:00Z", "path": OLDER, "sha256": sha_old,
                     "status": "unreadable (invalid-json, done_reason=stop)"}] * cap)
        got = [q["path"] for q in tr.pending_requests()]
        ok = got == [NEWER]
        print(f"  [{'ok' if ok else 'BAD'}]    at {cap} it is dropped and the next one is head "
              f"of the queue ({got})")
        if not ok:
            fails.append(f"a capped-out request did not release the queue: {got}")

        # 3. IT IS REPORTED. Dropped and mentioned nowhere is t324's failure, not a fix.
        gone = tr.abandoned()
        ok = len(gone) == 1 and gone[0]["path"] == OLDER and gone[0]["attempts"] == cap
        print(f"  [{'ok' if ok else 'BAD'}]    the abandoned request is reported, with its count "
              f"({gone})")
        if not ok:
            fails.append(f"abandoned() did not name the dropped request: {gone}")

        # 4. THE CAP IS WHAT DOES IT -- not the sha check, not `done`. Raise the cap and the
        #    same ledger must put it back in the queue.
        tr.UNREADABLE_CAP = cap + 99
        try:
            got = [q["path"] for q in tr.pending_requests()]
            ok = got == [OLDER, NEWER] and not tr.abandoned()
        finally:
            tr.UNREADABLE_CAP = cap
        print(f"  [{'ok' if ok else 'BAD'}]    raising the cap returns it to the queue ({got})")
        if not ok:
            fails.append(f"with the cap raised the request stayed out, so something other than "
                         f"the cap is excluding it: {got}")

        # 5. A SUCCESSFUL REVIEW STILL ENDS IT. The cap must not have replaced the ok path.
        write(led, [{"at": "2026-09-15T04:00:00Z", "path": OLDER, "sha256": sha_old,
                     "status": "ok"}])
        got = [q["path"] for q in tr.pending_requests()]
        ok = got == [NEWER]
        print(f"  [{'ok' if ok else 'BAD'}]    a reviewed file leaves the queue as before ({got})")
        if not ok:
            fails.append(f"an ok review no longer closes a request: {got}")

        # 6. A CHANGED FILE IS NOT HELD AGAINST ITS OLD CONTENT. The tally is keyed on sha, so
        #    editing the file must earn a fresh review rather than inherit the abandonment.
        write(led, [{"at": "2026-09-15T03:00:00Z", "path": OLDER, "sha256": "stale" * 12,
                     "status": "unreadable (invalid-json, done_reason=stop)"}] * (cap + 5))
        got = [q["path"] for q in tr.pending_requests()]
        ok = got == [OLDER, NEWER] and not tr.abandoned()
        print(f"  [{'ok' if ok else 'BAD'}]    failures against older content do not block the "
              f"current content ({got})")
        if not ok:
            fails.append(f"the cap is not keyed on content -- an edit cannot clear it: {got}")

    for x in fails:
        print("FAIL:", x)
    print("test_review_retry_cap:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
