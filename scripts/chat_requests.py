#!/usr/bin/env python3
"""Let Claude Chat ask the box to restart an idle queue, and read back what happened.

Jas, 2026-09-16: "I need you to put in place something that lets Chat restart an idle queue.
I want to go to chat and tell it the HiveMind is idle, and it's able to fix it. This is not
an option."

HOW CHAT ASKS. It calls the one tool it has that changes a byte over HTTP:

    inbox_post(subject="unstick the hivemind", body="<why you think it is idle>")

The MCP server derives the filename from the subject, so a subject whose slug STARTS WITH
"unstick" is the whole protocol. Chat invents no paths -- that is G1 in ROADMAP.md and the
rule that Code owns every path -- and it needs no tool it does not already have.

WHY THIS IS NOT D-21 BEING QUIETLY ARMED, because that is the first question to ask of it.
`chat_agent.py` executes ARBITRARY file operations from a manifest and is in dry run; arming
it is a human-only commit and remains so. This runs ONE parameterless action, scripts/
unstick.py, which repairs plumbing and refuses to decide work: it may resume a paused
scheduler, reap a dead beat file and run a tick; it may NOT merge (D-34 is untouched), edit a
brief or an oracle, release a park a model earned, or change a ceiling. There is no field in
the request that widens that -- the body is read as prose for the record and never as
instructions. A request that says "also merge everything" gets a tick and a report, because
the request carries no verb.

WHAT CHAT GETS BACK. The reply is written to workspace/outbox/, which Chat can already read,
and the trigger is retired to workspace/inbox/handled/ so the same ask cannot fire twice.

RATE LIMITED, because an agent that can call a thing can call it in a loop. One run per
MIN_GAP_S; a request inside that window is answered with the previous report rather than
running again, which is also the honest answer: nothing has changed in ninety seconds.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INBOX = REPO / "workspace" / "inbox"
HANDLED = INBOX / "handled"
OUTBOX = REPO / "workspace" / "outbox"
LEDGER = REPO / "workspace" / "chat-requests.jsonl"
TRIGGER = "unstick"
MIN_GAP_S = 90


def _record(row):
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        print(f"  WARNING: could not write {LEDGER.name}: {e}", file=sys.stderr, flush=True)


def _last_run_at():
    try:
        rows = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines()
                if l.strip()]
    except (OSError, ValueError):
        return 0.0
    ran = [r for r in rows if r.get("ran")]
    return ran[-1].get("epoch", 0.0) if ran else 0.0


def requests_waiting():
    """Trigger files Chat has posted. Matched on the SLUG the server derives from the subject.

    Starts-with, not contains: an ordinary message whose subject merely mentions unsticking
    must not fire the machine. The server's own naming is `<stamp>--chat--<slug>.md`.
    """
    if not INBOX.is_dir():
        return []
    out = []
    for f in sorted(INBOX.glob("*--chat--*.md")):
        slug = f.name.split("--chat--", 1)[1].rsplit(".md", 1)[0]
        if slug.startswith(TRIGGER):
            out.append(f)
    return out


def handle(f, force=False):
    """Run the action for one request and write the reply where Chat can read it."""
    body = ""
    try:
        body = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        body = "(the request file could not be read)"

    # A FAULT IS NEVER RATE-LIMITED AWAY (2026-09-16, found by the first end-to-end test).
    # The gap used to decide this alone, so a genuinely paused scheduler stayed paused
    # because another request had arrived 84 seconds earlier -- and the reply then said "no
    # plumbing fault was found", which was not a finding but an absence of looking. The
    # cheap probe runs FIRST and always: if something is actually broken, it is repaired
    # whatever the gap says. The gap now only stops a healthy box being ticked in a loop.
    gap = time.time() - _last_run_at()
    faults = []
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import unstick as _u
        faults = _u.quick_faults()
    except Exception as e:                                          # noqa: BLE001
        print(f"  WARNING: could not probe for faults ({e}) -- running anyway, because "
              f"not knowing is not the same as being well", file=sys.stderr, flush=True)
        faults = ["probe-failed"]
    ran = force or bool(faults) or gap >= MIN_GAP_S
    if ran:
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "unstick.py"),
                            "--by", "chat", "--json"],
                           capture_output=True, text=True, timeout=1200,
                           cwd=str(REPO), stdin=subprocess.DEVNULL)
        try:
            payload = json.loads(r.stdout or "{}")
        except ValueError:
            payload = {"verdict": "the action produced no readable result",
                       "report": (r.stdout or "") + (r.stderr or "")}
    else:
        # SAY WHAT WAS CHECKED, not merely what was skipped. "Nothing was found" and
        # "nothing was looked for" are different sentences and only one of them is true here.
        payload = {"verdict": f"checked just now and found no plumbing fault: the scheduler "
                              f"is running and no beat file is orphaned. A full tick was not "
                              f"started because one ran {int(gap)}s ago, inside the "
                              f"{MIN_GAP_S}s minimum gap.",
                   "report": "Cheap probe only -- scheduler state and beat files were read "
                             "and both are healthy. The full planner diagnosis was not run.",
                   "did": [], "blocked": [], "started": [], "probe_only": True}

    # NEVER REUSE A NAME (L-016, and the outbox README says it in as many words). The stamp
    # is second-resolution and two requests handled in the same second silently overwrote the
    # first reply -- observed while testing this, with the earlier report simply gone. Chat
    # reads this folder by name, so a lost reply is an unanswered request that looks answered.
    stamp = time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime())
    reply = OUTBOX / f"{stamp}--code--unstick-report-for-chat.md"
    n = 2
    while reply.exists():
        reply = OUTBOX / f"{stamp}--code--unstick-report-for-chat-{n}.md"
        n += 1
    did = payload.get("did") or []
    blocked = payload.get("blocked") or []
    started = payload.get("started") or []
    lines = [
        f"# Unstick report — answering your request of {f.name}",
        "",
        f"**Verdict: {payload.get('verdict')}**",
        "",
        "## What I repaired",
        ("\n".join(f"- `{x}`" for x in did) if did
         else "- nothing: no plumbing fault was found."
              if not payload.get("probe_only")
              else "- nothing needed repairing -- the scheduler is running and no beat file "
                   "is orphaned."),
        "",
        "## What the tick started",
        ("\n".join(f"- {x}" for x in started) if started
         else "- nothing new was started."),
        "",
        "## What still needs a person",
        ("\n".join(f"- {x}" for x in blocked) if blocked
         else "- nothing. If the queue is still idle after this, it is not a fault this "
              "tool can see, and that itself is worth telling Jas."),
        "",
        "## The full diagnosis",
        "",
        "```",
        payload.get("report") or "(no report)",
        "```",
        "",
        "---",
        "This reply was generated by `scripts/chat_requests.py` from your `inbox_post`.",
        "It repairs plumbing only: it may resume a paused scheduler, reap a dead beat file "
        "and run a scheduler tick. It may NOT merge (D-34), edit a brief or an oracle, "
        "release a park a model earned, or change a ceiling. Those are decisions and they "
        "stay with a person — so if the list above is not empty, that is what to tell Jas.",
        "",
        "### Your original request, verbatim",
        "",
        "```",
        body.strip(),
        "```",
    ]
    OUTBOX.mkdir(parents=True, exist_ok=True)
    reply.write_text("\n".join(lines) + "\n", encoding="utf-8")

    HANDLED.mkdir(parents=True, exist_ok=True)
    dest = HANDLED / f.name
    n = 2
    while dest.exists():
        dest = HANDLED / f"{f.stem}-{n}{f.suffix}"
        n += 1
    f.rename(dest)

    _record({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "epoch": time.time(),
             "request": f.name, "ran": ran, "did": did, "blocked_count": len(blocked),
             "started": started, "reply": reply.name})
    print(f"  handled {f.name} -> {reply.name} (ran={ran})", flush=True)
    return payload


def main():
    force = "--force" in sys.argv
    waiting = requests_waiting()
    if not waiting:
        if "--quiet" not in sys.argv:
            print("  no unstick request waiting", flush=True)
        return 0
    for f in waiting:
        handle(f, force=force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
