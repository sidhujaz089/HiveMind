#!/usr/bin/env python3
"""Store → reality: a service that claims `up` must be provable, or it must say it is not (N5, t8550).

N4 asked whether reality is on the map. This asks the opposite: is what the map SAYS still true? A
network map whose entries all claim `up` and none of which is ever checked is worse than no map, because
it reads as knowledge. The Flow chart has the same hazard and answers it the same way -- something fails
when the claim goes stale.

The rule, deliberately narrow:

  a `service` with `state: up` must carry a `probe`, and the probe must succeed.
  `state: unknown`       claims nothing, so nothing to prove. This is how a hole is recorded.
  `state: down-declared` claims it is down, with a date and why. Nothing to prove either.
  `state: retired`       not on the network any more.
  devices and links are NOT required to probe: their presence is N4's job (a tailnet peer is observed,
  not dialled), and inventing a probe for "the GX10 exists" would be theatre.

Two probe forms, both data in the map so no code knows any specific host:

  "probe": {"tcp": "127.0.0.1:8787"}                              a TCP connect must succeed
  "probe": {"command": "systemctl --user is-active --quiet x"}    must exit 0

What this refuses: a service claiming up with no probe (an unprovable assertion), a probe that fails (the
claim is now false), a probe of unknown shape, and a `down-declared` with no reason or date -- because
"down" without "since when and why" is indistinguishable from forgotten.

Hermetic: `run` and `tcp` are injected.
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


def svc(eid, state="up", probe=None, **kw):
    e = {"id": eid, "what": "x", "kind": "service", "owner": "jas",
         "reached_by": "loopback", "state": state, "source": {"command": "true"}}
    if probe:
        e["probe"] = probe
    e.update(kw)
    return e


def main():
    f = cw.network_unproven
    ok_run = lambda cmd: (0, "", "")                    # noqa: E731
    bad_run = lambda cmd: (3, "", "inactive")           # noqa: E731
    ok_tcp = lambda host, port, timeout: True           # noqa: E731
    bad_tcp = lambda host, port, timeout: False         # noqa: E731

    # 1. A CLAIM WITH NO PROBE IS THE FINDING. This is the rule.
    out = f({"entities": [svc("claims-up")]}, run=ok_run, tcp=ok_tcp)
    check("a service claiming up with no probe is reported", len(out) == 1 and "claims-up" in out[0], out)

    # 2. A PROBE THAT SUCCEEDS IS CLEAN.
    out = f({"entities": [svc("hub", probe={"tcp": "127.0.0.1:8091"})]}, run=ok_run, tcp=ok_tcp)
    check("a passing tcp probe is clean", out == [], out)

    # 3. A PROBE THAT FAILS IS THE FINDING, and it says what it ran.
    out = f({"entities": [svc("hub", probe={"tcp": "127.0.0.1:8091"})]}, run=ok_run, tcp=bad_tcp)
    check("a failing tcp probe is reported", len(out) == 1 and "127.0.0.1:8091" in out[0], out)

    # UPDATED 2026-10-26 (t8860): a probe is a vocabulary WORD now, not a command line. This suite used to
    # assert that a `command` probe worked -- which is the behaviour Claude Code flagged, because the
    # runner executed it through a shell. The word is `systemd_unit`, and the assertion follows the code
    # rather than the code being bent back to the assertion.
    out = f({"entities": [svc("unit", probe={"systemd_unit": "x.service"})]}, run=bad_run, tcp=ok_tcp)
    check("a failing unit probe is reported", len(out) == 1 and "x.service" in out[0], out)
    out = f({"entities": [svc("unit", probe={"systemd_unit": "x.service"})]}, run=ok_run, tcp=ok_tcp)
    check("a passing unit probe is clean", out == [], out)
    out = f({"entities": [svc("unit", probe={"command": "systemctl is-active x"})]}, run=ok_run, tcp=ok_tcp)
    check("a command probe is refused -- the map is data, not a shell",
          len(out) == 1 and "command" in out[0], out)

    # 4. A DECLARED HOLE CLAIMS NOTHING, so it is not a problem. This is the escape hatch that keeps the
    #    rule from forcing a guess: say "unknown" instead of "up".
    out = f({"entities": [svc("hole", state="unknown")]}, run=ok_run, tcp=ok_tcp)
    check("state: unknown needs no probe", out == [], out)

    # 5. DOWN-DECLARED NEEDS A DATE AND A REASON, or it is just forgotten.
    out = f({"entities": [svc("off", state="down-declared")]}, run=ok_run, tcp=ok_tcp)
    check("down-declared without why/since is reported", len(out) == 1, out)
    out = f({"entities": [svc("off", state="down-declared",
                              down={"since": "2026-10-26", "why": "swift is off"})]},
            run=ok_run, tcp=ok_tcp)
    check("down-declared with why and since is clean", out == [], out)

    # 6. DEVICES AND LINKS ARE NOT PROBED. Their presence is observed by N4, not dialled here.
    out = f({"entities": [{"id": "dev", "what": "a box", "kind": "device", "owner": "jas",
                           "reached_by": "lan", "state": "up", "source": {"command": "true"}}]},
            run=ok_run, tcp=ok_tcp)
    check("a device claiming up needs no probe", out == [], out)

    # 7. A MALFORMED PROBE IS A PROBLEM, not a silent pass.
    out = f({"entities": [svc("weird", probe={"http": "https://x"})]}, run=ok_run, tcp=ok_tcp)
    check("an unknown probe form is reported", len(out) == 1, out)

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
