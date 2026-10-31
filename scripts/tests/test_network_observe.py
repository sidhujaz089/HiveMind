#!/usr/bin/env python3
"""Reality → store: everything the network actually exposes is declared, or declared-ignored (N4, t8547).

The map so far is what I chose to write down. This is the other direction: ask the machine what is
listening and who is on the tailnet, then require the map to account for it. Without this, a new device
or a new port is invisible until a person notices -- the same failure the Flow chart had before
`flow_coverage`.

**THE BOUNDARY IS PART OF THE CHECK, and it is stated rather than implied.** The first observation on
2026-10-26 found 22 externally-reachable sockets and dozens of loopback ones, most of them ephemeral
ports in the 32k-60k range. A check over all of them would be noise, and noise is how a check gets
ignored (G10). So:

  IN SCOPE   tailnet peers; listening TCP ports BELOW 32768 on a non-loopback address.
  OUT        ephemeral ports (>= 32768): an outbound socket's local end, not a service;
             loopback-only listeners: local doors, covered by the entity that names them.

Every in-scope observation must be accounted for in ONE of two ways, and both are visible:

  entity "observes": ["port:8091", ...]          this entity IS that thing
  ignored: [{"key": "port:111", "why": "..."}]   declared, with a reason, in the map itself

A key with no `why` is not an ignore, it is a silence -- so an ignore without a reason does not count.

Hermetic: the pure function takes the document and the observations; nothing here touches the machine.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace as cw  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def doc(entities=(), ignored=()):
    return {"entities": list(entities), "ignored": list(ignored)}


def ent(eid, **kw):
    base = {"id": eid, "what": "x", "kind": "service", "owner": "jas",
            "reached_by": "loopback", "state": "up", "source": {"command": "true"}}
    base.update(kw)
    return base


def main():
    f = cw.network_undeclared

    # 1. A DECLARED PORT IS CLEAN.
    d = doc([ent("hub", observes=["port:8091", "tailnet:hivemind"])])
    out = f(d, {"port": [8091], "tailnet": ["hivemind"]})
    check("a declared port and peer are clean", out == [], out)

    # 2. AN UNDECLARED PORT IS THE FINDING, and it says what to do about it.
    out = f(d, {"port": [8091, 9433]})
    check("an undeclared port is reported", any("9433" in x for x in out), out)
    check("  the message names the repair", any("ignored" in x or "observes" in x for x in out), out)

    # 3. AN UNDECLARED TAILNET PEER IS THE FINDING -- a new device must not be invisible.
    out = f(d, {"tailnet": ["hivemind", "new-phone"]})
    check("an undeclared tailnet peer is reported", any("new-phone" in x for x in out), out)

    # 4. AN IGNORE WITH A REASON ACCOUNTS FOR IT.
    d2 = doc([ent("hub", observes=["port:8091"])],
             [{"key": "port:111", "why": "rpcbind for NFS; nothing in this repository uses it"}])
    check("a declared-ignored key is clean", f(d2, {"port": [8091, 111]}) == [],
          f(d2, {"port": [8091, 111]}))

    # 5. AN IGNORE WITH NO REASON IS NOT AN IGNORE. Otherwise "ignored" becomes a dumping ground and the
    #    check is silence with extra steps.
    d3 = doc([ent("hub", observes=["port:8091"])], [{"key": "port:111", "why": ""}])
    out = f(d3, {"port": [8091, 111]})
    check("an ignore with no reason does not count", any("111" in x for x in out), out)

    # 6. OUT OF SCOPE IS NOT REPORTED. Ephemeral ports must never appear, whatever the doc says.
    check("ephemeral ports are out of scope", f(doc(), {"port": [34875, 55073]}) == [],
          f(doc(), {"port": [34875, 55073]}))

    # 7. A CATEGORY THAT WAS NOT OBSERVED IS NOT AN ALL-CLEAR. If tailscale could not be read, the peer
    #    question was not answered -- that must not read as "no undeclared peers".
    out = f(doc(), {"port": []}, unobserved=["tailnet"])
    check("an unobserved category is reported, not skipped", any("tailnet" in x for x in out), out)

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
