#!/usr/bin/env python3
"""Fixtures for workspace_mcp/auth.py.

The assertions that matter are the ones where a bug would look like success:
an absent header accepted, an empty token accepted, a missing token file
defaulting to open. "Valid token rejected" announces itself; these do not.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workspace_mcp.auth import (  # noqa: E402
    check, load_token, extract_bearer, generate_token, AuthError, MIN_TOKEN_LENGTH,
)

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def refused(fn, *a, **k):
    """The rule name of the refusal, or ACCEPTED if it went through."""
    try:
        fn(*a, **k)
        return "ACCEPTED"
    except AuthError as e:
        return e.rule


def write_token(text, mode=0o600):
    d = Path(tempfile.mkdtemp())
    p = d / "token"
    p.write_text(text)
    p.chmod(mode)
    return p


print("\nworkspace_mcp/auth.py fixtures\n")

GOOD = generate_token()
report("generated tokens are long enough", len(GOOD) >= MIN_TOKEN_LENGTH, True)
report("two generated tokens differ", generate_token() == generate_token(), False)

# --- the accept path --------------------------------------------------------------
report("a correct bearer token is accepted",
       check({"Authorization": f"Bearer {GOOD}"}, expected=GOOD), True)
report("header name is case-insensitive (RFC 7230)",
       check({"authorization": f"Bearer {GOOD}"}, expected=GOOD), True)
report("scheme is case-insensitive",
       check({"Authorization": f"bearer {GOOD}"}, expected=GOOD), True)

# --- refusals where a bug would look like success ---------------------------------
report("NO header is refused", refused(check, {}, expected=GOOD), "no-authorization-header")
report("None headers refused", refused(check, None, expected=GOOD), "no-authorization-header")
report("empty bearer value refused",
       refused(check, {"Authorization": "Bearer "}, expected=GOOD), "malformed-authorization-header")
report("bare token with no scheme refused",
       refused(check, {"Authorization": GOOD}, expected=GOOD), "malformed-authorization-header")
report("wrong scheme refused",
       refused(check, {"Authorization": f"Basic {GOOD}"}, expected=GOOD), "malformed-authorization-header")
report("wrong token refused",
       refused(check, {"Authorization": f"Bearer {generate_token()}"}, expected=GOOD), "token-mismatch")
report("a PREFIX of the token is refused",
       refused(check, {"Authorization": f"Bearer {GOOD[:-1]}"}, expected=GOOD), "token-mismatch")
report("the token plus a character is refused",
       refused(check, {"Authorization": f"Bearer {GOOD}x"}, expected=GOOD), "token-mismatch")
report("empty string is refused",
       refused(check, {"Authorization": "Bearer  "}, expected=GOOD), "malformed-authorization-header")

# --- token file handling ------------------------------------------------------------
p = write_token(GOOD)
report("a well-formed token file loads", load_token(p), GOOD)
report("trailing newline is stripped", load_token(write_token(GOOD + "\n")), GOOD)

report("a MISSING token file refuses, never defaults open",
       refused(load_token, "/nonexistent/token"), "no-token-configured")
report("an EMPTY token file refuses", refused(load_token, write_token("")), "empty-token")
report("a whitespace-only file refuses", refused(load_token, write_token("   \n")), "empty-token")
report("a short token refuses", refused(load_token, write_token("abc123")), "token-too-short")

# A token any other user can read is not a secret. 0644 must refuse.
report("a world-readable token file refuses",
       refused(load_token, write_token(GOOD, mode=0o644)), "token-file-too-permissive")
report("a group-readable token file refuses",
       refused(load_token, write_token(GOOD, mode=0o640)), "token-file-too-permissive")
report("0600 is accepted", load_token(write_token(GOOD, mode=0o600)), GOOD)

# --- the whole path, with no expected= supplied -------------------------------------
p = write_token(GOOD)
report("end to end via the file", check({"Authorization": f"Bearer {GOOD}"}, token_path=p), True)
report("end to end refuses a wrong token",
       refused(check, {"Authorization": "Bearer wrong-but-long-enough-to-pass-length-check"},
               token_path=p), "token-mismatch")

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
