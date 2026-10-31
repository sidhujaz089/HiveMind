#!/usr/bin/env python3
"""A review only counts as done if the current auditor wrote it.

t329: every audit on the ledger is gemma4:26b's, and the ledger is keyed on
(path, sha256) alone -- truncation_review.py:257 -- so a bare re-run skips
everything the old auditor passed, and the unreadable cap
(truncation_review.py:185) inherits the old auditor's strikes. After the
2026-09-15 swap (Gemma -> Q16) the condition must be "audited by the model
that is currently the auditor", not "audited by anyone".

Rows that name no model at all still count: some fixtures predate the field,
and test_review_retry_cap.py's rows have none.

Every case runs against a temp ledger and request file; nothing tracked is
written.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

# Two real tracked non-test scripts: pending_requests() only returns a request
# whose file exists and still hashes to the requested content, so these have to
# be real (same pair as test_review_retry_cap.py).
OLDER = "scripts/truncation_review.py"
NEWER = "scripts/check_enforcement.py"


def write(p, rows):
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def main():
    import truncation_review as tr
    fails = []
    MODEL = tr.MODEL
    OLD = "gemma4:26b" if MODEL != "gemma4:26b" else "gemma4:25b"  # a different auditor
    sha_old, sha_new = tr.sha(REPO / OLDER), tr.sha(REPO / NEWER)
    cap = tr.UNREADABLE_CAP

    with tempfile.TemporaryDirectory() as d:
        led, req = Path(d) / "ledger.jsonl", Path(d) / "requests.jsonl"
        tr.LEDGER, tr.REQUESTS = led, req
        write(req, [{"at": "2026-09-15T01:00:00Z", "path": OLDER, "sha256": sha_old},
                    {"at": "2026-09-15T02:00:00Z", "path": NEWER, "sha256": sha_new}])

        # 0. SETUP SANITY. Empty ledger: both fixtures pending. If the model filter
        #    broke in a way that drops everything, the cases below pass while testing nothing.
        write(led, [])
        got = [q["path"] for q in tr.pending_requests()]
        ok = got == [OLDER, NEWER]
        print(f"  [{'ok' if ok else 'BAD'}]    both fixtures pending with an empty ledger ({got})")
        if not ok:
            fails.append(f"setup: expected [{OLDER}, {NEWER}], got {got}")

        # 1. A review by the CURRENT auditor counts as done (today's behaviour, preserved).
        write(led, [{"at": "2026-09-15T04:00:00Z", "path": OLDER, "sha256": sha_old,
                     "model": MODEL, "status": "ok"}])
        got = [q["path"] for q in tr.pending_requests()]
        ok = got == [NEWER]
        print(f"  [{'ok' if ok else 'BAD'}]    a current-model ok row closes the request ({got})")
        if not ok:
            fails.append(f"an ok review by the current auditor no longer closes a request: {got}")

        # 2. THE t329 GAP: a review by ANOTHER model does not count.
        #    Gemma's 175 rows are exactly this case; skipping them is why the re-audit
        #    can never happen.
        write(led, [{"at": "2026-09-15T03:00:00Z", "path": OLDER, "sha256": sha_old,
                     "model": OLD, "status": "ok"}])
        got = [q["path"] for q in tr.pending_requests()]
        ok = got == [OLDER, NEWER]
        print(f"  [{'ok' if ok else 'BAD'}]    an other-model ok row is NOT done -- re-review ({got})")
        if not ok:
            fails.append(f"a review written by {OLD} still counts as done, so a model swap "
                         f"cannot re-open the corpus: {got}")

        # 3. LEGACY rows that name NO model still count. test_review_retry_cap.py's fixtures
        #    have no `model` key; a filter that drops them silently re-opens everything.
        write(led, [{"at": "2026-09-15T04:30:00Z", "path": OLDER, "sha256": sha_old,
                     "status": "ok"}])
        got = [q["path"] for q in tr.pending_requests()]
        ok = got == [NEWER]
        print(f"  [{'ok' if ok else 'BAD'}]    a model-less (legacy) ok row still closes it ({got})")
        if not ok:
            fails.append(f"rows without a model are no longer honoured -- the pre-field fixtures "
                         f"in test_review_retry_cap break: {got}")

        # 4. THE UNREADABLE CAP IS THE CURRENT AUDITOR'S, NOT THE PREVIOUS ONE'S.
        #    Three strikes by the old model are not three strikes for the new model: a
        #    different model may read what that one could not.
        write(led, [{"at": "2026-09-15T03:00:00Z", "path": OLDER, "sha256": sha_old,
                     "model": OLD, "status": "unreadable (invalid-json, done_reason=stop)"}] * cap)
        got = [q["path"] for q in tr.pending_requests()]
        ok = got == [OLDER, NEWER] and not tr.abandoned()
        print(f"  [{'ok' if ok else 'BAD'}]    the old model's {cap} strikes do not cap the new one ({got})")
        if not ok:
            fails.append(f"an other model's unreadable streak caps the current auditor out: {got}")

        # 5. ... AND THE CURRENT AUDITOR'S STRIKES STILL CAP IT, as test_review_retry_cap pins.
        write(led, [{"at": "2026-09-15T05:00:00Z", "path": OLDER, "sha256": sha_old,
                     "model": MODEL, "status": "unreadable (invalid-json, done_reason=stop)"}] * cap)
        got = [q["path"] for q in tr.pending_requests()]
        ok = (got == [NEWER] and len(tr.abandoned()) == 1
              and tr.abandoned()[0]["path"] == OLDER)
        print(f"  [{'ok' if ok else 'BAD'}]    the current model's {cap} strikes cap it and it is reported ({got})")
        if not ok:
            fails.append(f"the cap no longer fires for the current auditor, or abandoned() lost it: {got}")

        # 6. MIXED: the old auditor passed, then the current one failed cap times.
        #    The file is capped for the current auditor -- and only for it.
        write(led, [{"at": "2026-09-15T03:00:00Z", "path": OLDER, "sha256": sha_old,
                     "model": OLD, "status": "ok"}]
                + [{"at": "2026-09-15T05:00:00Z", "path": OLDER, "sha256": sha_old,
                     "model": MODEL, "status": "unreadable (invalid-json, done_reason=stop)"}] * cap)
        got = [q["path"] for q in tr.pending_requests()]
        ok = got == [NEWER]
        print(f"  [{'ok' if ok else 'BAD'}]    an old-model pass plus current-model strikes caps the current auditor ({got})")
        if not ok:
            fails.append(f"the done set and the cap disagree on whose rows count: {got}")

    for x in fails:
        print("FAIL:", x)
    print("test_review_reauditor:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
