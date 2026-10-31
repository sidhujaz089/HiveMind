#!/usr/bin/env python3
"""The check that surfaces Jas's replies must not go quiet."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jas_replies as jr


def write(d, name, body):
    (d / name).write_text(body, encoding="utf-8")


def note(date, who, text):
    return f"**{date} · {who}** — {text}\n\n"


def main():
    fails = []
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)

        # Jas spoke last -> waiting on us.
        write(d, "t001--x.md", note("2026-08-01", "claude-code", "scoped it")
                             + note("2026-08-02", "jas", "merge"))
        # We answered after him -> not waiting.
        write(d, "t002--x.md", note("2026-08-01", "jas", "Is this good?")
                             + note("2026-08-02", "claude-code", "yes, here is why"))
        # The local model wrote last, after Jas -> STILL waiting. A model run is not
        # an answer to a human question, and treating any later note as a reply is
        # the obvious wrong simplification.
        write(d, "t003--x.md", note("2026-08-01", "jas", "Approved")
                             + note("2026-08-02", "local/qwen3.6", "ACCEPTED"))
        # No notes at all.
        write(d, "t004--x.md", "---\nid: t004\n---\n\n## Notes\n")

        got = {r["task"] for r in jr.waiting(d)}
        for want in ("t001", "t003"):
            if want not in got:
                fails.append(f"{want} should be waiting on us, got {sorted(got)}")
        for no in ("t002", "t004"):
            if no in got:
                fails.append(f"{no} must NOT be waiting, got {sorted(got)}")

        # Same-day notes must resolve by file order, not by date. This is the case
        # that actually bit: everything Jas answered on 2026-08-26 shared a date
        # with the note it replied to.
        write(d, "t005--x.md", note("2026-08-26", "jas", "merge")
                             + note("2026-08-26", "claude-code", "merged"))
        if "t005" in {r["task"] for r in jr.waiting(d)}:
            fails.append("same-day: the LAST note wins, so t005 is answered")

        write(d, "t006--x.md", note("2026-08-26", "claude-code", "ready")
                             + note("2026-08-26", "jas", "Approved"))
        if "t006" not in {r["task"] for r in jr.waiting(d)}:
            fails.append("same-day: t006's last note is Jas's, so it is waiting")

        # A CONVERSATION: he asks, we answer, he asks again. Anchoring on his first
        # note instead of his latest reports the task as answered, because our reply
        # to the OLD question sits after it. This is the ordinary shape of a task
        # thread, so getting it wrong hides live questions behind dead ones.
        write(d, "t007--x.md", note("2026-08-01", "jas", "first question")
                             + note("2026-08-02", "claude-code", "first answer")
                             + note("2026-08-03", "jas", "second question"))
        rows = {r["task"]: r for r in jr.waiting(d)}
        if "t007" not in rows:
            fails.append("t007: his LATEST note is unanswered, so it is waiting")
        elif rows["t007"]["said"] != "second question":
            fails.append(f"t007: must surface the latest question, "
                         f"got {rows['t007']['said']!r}")

        # And the mirror: answered after his latest note -> silent.
        write(d, "t008--x.md", note("2026-08-01", "jas", "q1")
                             + note("2026-08-02", "claude-code", "a1")
                             + note("2026-08-03", "jas", "q2")
                             + note("2026-08-04", "claude-code", "a2"))
        if "t008" in {r["task"] for r in jr.waiting(d)}:
            fails.append("t008: answered after his latest note, must not be waiting")

        # DSH IS A SESSION, NOT A LOCAL-MODEL RUN (2026-10-30). It reads Jas's note and answers it,
        # so its note has to clear the wait exactly as claude-code's does. Without this his queue
        # tells him nobody answered while the answer sits on the task -- and it was live: t1123
        # and t693 were answered by dsh-harness on 10-30 and both stayed on this list.
        write(d, "t009--x.md", note("2026-08-01", "jas", "assign it to the Manager")
                             + note("2026-08-02", "dsh-harness", "done: needs is rework"))
        if "t009" in {r["task"] for r in jr.waiting(d)}:
            fails.append("t009: dsh-harness answered him, so it must not be waiting")

        # The trailer's short form, which older notes carry.
        write(d, "t010--x.md", note("2026-08-01", "jas", "q")
                             + note("2026-08-02", "dsh", "a"))
        if "t010" in {r["task"] for r in jr.waiting(d)}:
            fails.append("t010: dsh answered him, so it must not be waiting")

        # AND THE LINE STILL HOLDS FOR A ROLE'S NOTE. A manager/swift note is a RECORD of a run
        # that never read his question, so it must not clear the wait -- widening the list for
        # DSH must not become "any later note counts", the simplification t003 forbids.
        write(d, "t011--x.md", note("2026-08-01", "jas", "is this right?")
                             + note("2026-08-02", "manager", "OUTCOME: done"))
        if "t011" not in {r["task"] for r in jr.waiting(d)}:
            fails.append("t011: a role's note is a run record, not an answer, so it is waiting")

        # An empty directory is a real answer, not a crash.
        with tempfile.TemporaryDirectory() as empty:
            if jr.waiting(Path(empty)) != []:
                fails.append("an empty tasks dir must yield no rows")

    for f in fails:
        print("FAIL:", f)
    print("test_jas_replies:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
