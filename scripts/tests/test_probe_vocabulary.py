#!/usr/bin/env python3
"""A probe is a word from a fixed vocabulary, never a command line (Claude's concern 7, t8860).

Claude Code, 2026-10-26: *"network_unproven() runs probe.command with shell=True... it makes a data file a
shell-execution path for every hourly check and every session start, and anything that can write
network.json (network_change.py, a model build) can put a command there."*

That is correct and it was mine (`5cef205ad`). A store that is checked by executing its own contents is a
store that can be turned into a command channel by anything that can write it -- and this repository's own
rule is that a data file is data.

So a probe is now one of two WORDS:

  {"tcp": "host:port"}          a TCP connect, short timeout
  {"systemd_unit": "name"}      `systemctl --user is-active --quiet <name>`, argv, shell=False

and nothing else. `command` is REFUSED with a message saying what to use instead, so an old entry cannot
survive quietly and a new one cannot be introduced by habit. The unit name is validated, because
"systemctl --user is-active --quiet ../../etc" is still a word with a slash in it.

What this refuses:

  * a `command` probe, named as such in the failure;
  * a `systemd_unit` whose name contains anything but [A-Za-z0-9@._:-];
  * an unknown probe key (already refused, kept);
  * and -- the property that matters -- ANY probe whose execution path involves a shell. The argv form is
    asserted structurally, not by hoping.
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


def svc(probe):
    return {"id": "s", "what": "x", "kind": "service", "owner": "jas", "reached_by": "loopback",
            "state": "up", "source": {"command": "true"}, "probe": probe}


def main():
    seen = {}

    def run(argv):
        seen["argv"] = argv
        return 0, "", ""

    ok_tcp = lambda h, p, t: True                       # noqa: E731

    # 1. THE UNIT WORD WORKS, and it is an ARGV -- a list, not a string. A string would mean a shell.
    out = cw.network_unproven({"entities": [svc({"systemd_unit": "cloudflared-hivemind"})]},
                              run=run, tcp=ok_tcp)
    check("a systemd_unit probe passes", out == [], out)
    check("  and it is executed as an ARGV list, never a string",
          isinstance(seen.get("argv"), list), type(seen.get("argv")).__name__)
    check("  with the unit as its own argument, unquoted",
          seen.get("argv", [])[-1] == "cloudflared-hivemind", seen.get("argv"))
    check("  and the executable is systemctl --user is-active --quiet",
          seen.get("argv", [])[:4] == ["systemctl", "--user", "is-active", "--quiet"], seen.get("argv"))

    # 2. A COMMAND PROBE IS REFUSED BY NAME, and the refusal says what to use.
    out = cw.network_unproven({"entities": [svc({"command": "systemctl --user is-active --quiet x"})]},
                              run=run, tcp=ok_tcp)
    check("a command probe is refused", len(out) == 1, out)
    check("  the message names it", "command" in (out[0] if out else ""), out[:1])
    check("  and points at systemd_unit", "systemd_unit" in (out[0] if out else ""), out[:1])

    # 3. A UNIT NAME WITH A PATH OR A SHELL CHARACTER IS REFUSED -- a word can still be a traversal.
    for bad in ("../../etc/passwd", "x; rm -rf /", "$(id)", "a b"):
        out = cw.network_unproven({"entities": [svc({"systemd_unit": bad})]}, run=run, tcp=ok_tcp)
        check(f"unit name {bad!r} is refused", len(out) == 1, out[:1])
        check(f"  and nothing was executed for {bad!r}", "argv" not in seen or seen["argv"][-1] != bad, seen)

    # 4. A VALID UNIT NAME WITH DOTS AND DASHES IS STILL A WORD.
    seen.clear()
    out = cw.network_unproven({"entities": [svc({"systemd_unit": "agent-tools.service"})]},
                              run=run, tcp=ok_tcp)
    check("a dotted unit name is accepted", out == [], out)

    # 5. THE LIVE MAP USES ONLY THE VOCABULARY. This is the check that stops an old entry surviving.
    import json
    doc = json.loads((ROOT / "workspace" / "network.json").read_text(encoding="utf-8"))
    probes = [e.get("probe") for e in doc.get("entities") or [] if isinstance(e, dict) and e.get("probe")]
    bad = [p for p in probes if not (isinstance(p, dict) and set(p) & {"tcp", "systemd_unit"}
                                     and not set(p) & {"command"})]
    check("every probe in the committed map is a vocabulary word", bad == [], bad)
    check("and at least one unit probe exists (the tunnel)", any("systemd_unit" in p for p in probes), probes)

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
