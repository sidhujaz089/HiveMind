#!/usr/bin/env python3
"""The Network tab is a PROJECTION of the store, and nothing on it is authored (N2, t8546).

Jas, 2026-10-26: *"a network map in the HiveMind Hub that has a tab just for this so I can see it and just
like the flowchart tab it needs to be kept up to date."* Kept up to date mechanically, the same way the
Flow tab is: the page reads `workspace/network.json` on every request, and the store is only ever
changed by `scripts/network_change.py`.

What this refuses:

  * a page that caches -- a change to the store must appear on the next render;
  * a page that 500s or renders blank when the store is unreadable (the reason must be on the page);
  * a page that hides the holes -- `state: unknown` entries must be named, because a map that looks
    complete and is not is worse than one that shows its gaps;
  * **map content in the page module.** This is the one that matters most: if a device name, a tunnel
    id or an address is written into the page, there are two truths and the store is already losing.
    The test asserts that distinctive values from the store do NOT appear in the source of the page;
  * a tab that goes nowhere -- appnav has the entry, feedback has the route, and both must agree with
    the page's own help key.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
sys.path.insert(0, str(ROOT / "scripts"))
import hub_common  # noqa: E402
import pages_network as pn  # noqa: E402
import appnav  # noqa: E402

LIVE = ROOT / "workspace" / "network.json"
PAGE_SRC = ROOT / "projects" / "app-repo" / "pages_network.py"
FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def main():
    doc = json.loads(LIVE.read_text(encoding="utf-8"))
    html = pn.network_page()

    # 1. EVERY ENTITY IS ON THE PAGE. A map entry nobody can see is the same as no entry.
    missing = [e["id"] for e in doc["entities"] if e["id"] not in html]
    check("every entity in the store is rendered", missing == [], missing)
    check("the headline counts them", f"Network ({len(doc['entities'])})" in html)

    # 2. THE SOURCE IS SHOWN, because that is how a reader gets from the page to the truth.
    check("a file-backed entry shows its source file", "docs/ACCESS.md" in html, "")
    check("a command-backed entry shows the command", "tailscale status" in html, "")

    # 3. THE HOLES ARE NAMED. Three entities are declared unknown on purpose.
    unknown = [e["id"] for e in doc["entities"] if e.get("state") == "unknown"]
    check("there are declared holes to show", len(unknown) >= 1, unknown)
    check("they are called out as named holes", "Named holes" in html)
    check("and each one appears", all(u in html for u in unknown), unknown)

    # 4. NO CACHING: a change to the store shows on the next render.
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "net.json"
        small = {"vocabulary": doc["vocabulary"], "entities": [
            {"id": "probe-one", "what": "first", "kind": "device", "owner": "jas",
             "reached_by": "lan", "state": "up", "source": {"command": "true"}}]}
        p.write_text(json.dumps(small), encoding="utf-8")
        first = pn.network_page(p)
        check("a temp store renders through the same code path", "probe-one" in first)
        small["entities"].append({"id": "probe-two", "what": "second", "kind": "service",
                                  "owner": "jas", "reached_by": "loopback", "state": "up",
                                  "source": {"command": "true"}})
        p.write_text(json.dumps(small), encoding="utf-8")
        second = pn.network_page(p)
        check("a change to the store appears on the NEXT render (no cache)",
              "probe-two" in second and "probe-two" not in first)

    # 5. AN UNREADABLE STORE IS A REASON ON THE PAGE, not a crash and not a blank.
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "broken.json"
        check("a missing store says so", "does not exist" in pn.network_page(bad), "")
        bad.write_text("{not json", encoding="utf-8")
        out = pn.network_page(bad)
        check("an unreadable store says why", "could not be read" in out, out[:80])

    # 6. NO MAP CONTENT IN THE PAGE MODULE. This is the assertion that keeps the store the only truth.
    src = PAGE_SRC.read_text(encoding="utf-8")
    leaked = [v for v in ("00000000", "192.0.2.2", "mcp.example.invalid", "127.0.0.1:8787",
                          "100.64.0.10") if v in src]
    check("the page module contains no device name, tunnel id or address", leaked == [], leaked)

    # 7. THE HELP ENTRY EXISTS AND POINTS AT THE STORE -- and C3's guard makes this mandatory.
    e = hub_common.PAGES.get("network")
    check("hub_common.PAGES has a network entry", bool(e), sorted(hub_common.PAGES))
    if e:
        check("it names the store as the file the page is a view of",
              any("network.json" in s for s in e.get("source", [])), e.get("source"))
        check("it says what to do when the page and the disk disagree", bool(e.get("wrong")), "")

    # 8. THE TAB GOES SOMEWHERE: the nav entry, the route, and the help key all agree.
    labels = {k: label for k, href, label in appnav.SECTIONS}
    hrefs = {k: href for k, href, label in appnav.SECTIONS}
    check("appnav has a network tab", "network" in labels, sorted(labels))
    check("its href ends in /network", str(hrefs.get("network", "")).endswith("/network"), hrefs.get("network"))
    fb = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    check("feedback.py routes /network", 'path == "/network"' in fb)
    check("and keys the active tab", '"/network": "network"' in fb)

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
