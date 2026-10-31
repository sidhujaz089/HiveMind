#!/usr/bin/env python3
"""Fail-first oracle for t1601: a DSH profile's trusted hosts must reach the launcher.

WHAT WENT WRONG, 2026-10-25. `~/.dsh/profiles/web/cordis.patch.yml` named
`hivemind.example.ts.net` under `web-runtime: trustedHosts:`, and the DSH page loaded over the
tailnet and then said **"reconnecting"** forever. The fence
(`packages/client/connection/src/api-request-trust.ts`) admits a non-loopback Host only when it is
in the effective `trustedHosts` -- and the effective list is the LAUNCHER'S
(`packages/bundle/web-app/cordis.patch.yml`: `trustedHosts: !!js ctx.webStartup.trustedHosts`),
i.e. the `--trusted-host` flags. A profile patch REPLACES a row's config, it does not add to it.
So the hostname was trusted on paper and 403 'forbidden' in practice, and nothing said so.

The durable fix is a check, not a paragraph: compare what the profile trusts against what the
launcher passes, and fail when they disagree. This oracle pins the comparison itself; the running
check lives in `check_workspace.check_dsh_trust`.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import check_workspace as cw  # noqa: E402

PASSED = FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    check("the comparison is a function", hasattr(cw, "trust_gap"), True)
    if not hasattr(cw, "trust_gap"):
        print(f"{PASSED} passed, {FAILED} failed")
        return 1

    # 1. The live failure, exactly: profile trusts the tailnet name, launcher passes only the LAN.
    gap = cw.trust_gap(["hivemind.example.ts.net"], ["192.0.2.97:3080"])
    check("a profile host missing from the launcher is a gap", gap, ["hivemind.example.ts.net"])

    # 2. Agreement, in both orders, is not a gap.
    check("the same list in the same order is clean",
          cw.trust_gap(["a.ts.net", "192.168.1.2:3080"], ["a.ts.net", "192.168.1.2:3080"]), [])
    check("  and so is the same list in another order",
          cw.trust_gap(["192.168.1.2:3080", "a.ts.net"], ["a.ts.net", "192.168.1.2:3080"]), [])

    # 3. A launcher entry the profile does not name is NOT a gap: the unit may trust more than the
    #    profile documents (the LAN address often lives only there).
    check("extra trust on the launcher line is fine",
          cw.trust_gap(["a.ts.net"], ["a.ts.net", "192.168.1.2:3080"]), [])

    # 4. A default port needs no port; a non-default one does. Both forms must compare equal to
    #    themselves and a :443 suffix must not manufacture a gap.
    check("a :443 suffix on the profile side still matches the bare host",
          cw.trust_gap(["a.ts.net:443"], ["a.ts.net"]), [])
    check("a non-default port is compared as written",
          cw.trust_gap(["a.ts.net:3080"], ["a.ts.net"]), ["a.ts.net:3080"])

    # 5. Empty on either side is not a silent pass: no profile trust means nothing to compare,
    #    no launcher trust means every non-loopback authority is refused (worth saying).
    check("no profile hosts is no gap", cw.trust_gap([], ["a.ts.net"]), [])
    check("no launcher hosts with a profile host IS the gap",
          cw.trust_gap(["a.ts.net"], []), ["a.ts.net"])

    # 5b. The READER must stop at the end of the list. The real patch is a list followed by
    #     "- insert:" and "- id: ..." mapping items, and my first reader swallowed them as hosts
    #     (the live check reported "insert:, id: directory-picker-browse" as untrusted authorities).
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        patch = Path(td) / "cordis.patch.yml"
        patch.write_text(
            "- id: web-runtime\n"
            "  config:\n"
            "    trustedHosts:\n"
            "      - hivemind.example.ts.net\n"
            "- insert:\n"
            "    - id: directory-picker-browse\n"
            "      name: '@deepseek-ai/dsh-host-directory-picker-browse'\n", encoding="utf-8")
        check("the reader stops at the end of the trustedHosts list",
              cw._profile_trusted_hosts(patch), ["hivemind.example.ts.net"])

    # 6. The check is wired into the run, not just written.
    import inspect
    run_src = inspect.getsource(cw.Checker.run)
    check("the check runs with the others", "check_dsh_trust" in run_src, True)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
