#!/usr/bin/env python3
"""Fixtures for workspace_mcp/oauth.py.

Weighted at the failures that LOOK like success: a code redeemed twice, a mismatched
PKCE verifier, a code redeemed by a different client, an expired token still accepted.
"Valid flow rejected" announces itself; these do not.
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workspace_mcp import oauth  # noqa: E402
from workspace_mcp.oauth import (  # noqa: E402
    Store, OAuthError, pkce_challenge, verify_pkce, check_client_secret,
    load_or_create_client, protected_resource_metadata, authorization_server_metadata,
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
    try:
        fn(*a, **k)
        return "ACCEPTED"
    except OAuthError as e:
        return e.rule


def store():
    d = Path(tempfile.mkdtemp())
    return Store(token_path=d / "tokens.json")


ISSUER = "https://mcp.example.invalid"
CID = "wsmcp_test"
RURI = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "a" * 64

print("\nworkspace_mcp/oauth.py fixtures\n")

# --- discovery: advertise only what exists ------------------------------------------
prm = protected_resource_metadata(ISSUER)
report("protected-resource names the server", prm["authorization_servers"], [ISSUER])
report("protected-resource names the resource", prm["resource"], f"{ISSUER}/mcp")

asm = authorization_server_metadata(ISSUER)
report("only S256 is advertised", asm["code_challenge_methods_supported"], ["S256"])
report("only authorization_code is advertised", asm["grant_types_supported"], ["authorization_code"])
report("no registration_endpoint is advertised", "registration_endpoint" in asm, False)
report("endpoints are absolute", asm["token_endpoint"], f"{ISSUER}/token")

# --- PKCE ----------------------------------------------------------------------------
CHAL = pkce_challenge(VERIFIER)
report("challenge has no base64 padding", CHAL.endswith("="), False)
report("correct verifier passes", verify_pkce(VERIFIER, CHAL), True)
report("wrong verifier fails", verify_pkce("b" * 64, CHAL), False)
report("verifier one char short fails", verify_pkce("a" * 63, CHAL), False)
report("empty verifier is refused", refused(verify_pkce, "", CHAL), "pkce-verifier-missing")
report("plain method is refused", refused(verify_pkce, VERIFIER, VERIFIER, "plain"),
       "pkce-method-unsupported")

# --- the happy path -------------------------------------------------------------------
s = store()
code = s.issue_code(CID, RURI, CHAL)
report("a code is issued", len(code) > 20, True)
report("the full exchange succeeds", s.redeem_code(code, VERIFIER, CID, RURI), True)

# --- replay: the one that looks like success ------------------------------------------
report("the SAME code cannot be redeemed twice",
       refused(s.redeem_code, code, VERIFIER, CID, RURI), "code-already-used")

s = store()
code = s.issue_code(CID, RURI, CHAL)
report("a wrong verifier is refused",
       refused(s.redeem_code, code, "b" * 64, CID, RURI), "pkce-verifier-mismatch")
report("and that code is now burnt, not retryable",
       refused(s.redeem_code, code, VERIFIER, CID, RURI), "code-already-used")

# --- binding --------------------------------------------------------------------------
s = store()
code = s.issue_code(CID, RURI, CHAL)
report("another client cannot redeem it",
       refused(s.redeem_code, code, VERIFIER, "wsmcp_other", RURI),
       "code-belongs-to-another-client")

s = store()
code = s.issue_code(CID, RURI, CHAL)
report("a different redirect_uri is refused",
       refused(s.redeem_code, code, VERIFIER, CID, "https://evil.example/cb"),
       "redirect-uri-mismatch")

report("an unknown code is refused",
       refused(store().redeem_code, "never-issued", VERIFIER, CID, RURI), "code-unknown")

# --- expiry ----------------------------------------------------------------------------
s = store()
code = s.issue_code(CID, RURI, CHAL)
s.codes[code]["expires"] = time.time() - 1
report("an expired code is refused", refused(s.redeem_code, code, VERIFIER, CID, RURI),
       "code-expired")

report("a challenge-less request is refused",
       refused(store().issue_code, CID, RURI, ""), "pkce-challenge-missing")

# --- tokens -----------------------------------------------------------------------------
s = store()
tok, ttl = s.issue_token(CID)
report("an issued token validates", s.validate_token(tok), True)
report("a random token does not", s.validate_token("not-a-real-token"), False)
report("an empty token does not", s.validate_token(""), False)
report("None does not", s.validate_token(None), False)

toks = json.loads(s.token_path.read_text())
toks[tok]["expires"] = time.time() - 1
s.token_path.write_text(json.dumps(toks))
report("an EXPIRED token is refused", s.validate_token(tok), False)

s = store()
t1, _ = s.issue_token(CID)
t2, _ = s.issue_token(CID)
report("two issued tokens both work", (s.validate_token(t1), s.validate_token(t2)), (True, True))
s.revoke_all()
report("revoke_all kills both", (s.validate_token(t1), s.validate_token(t2)), (False, False))

report("the token file is 0600", oct(s.token_path.stat().st_mode & 0o777), "0o600")

# --- client secret -----------------------------------------------------------------------
report("a matching secret passes", check_client_secret("abc", "abc"), True)
report("a wrong secret is refused", refused(check_client_secret, "abc", "xyz"),
       "client-secret-mismatch")
report("a missing secret is refused", refused(check_client_secret, "", "xyz"),
       "client-secret-missing")
report("a prefix of the secret is refused", refused(check_client_secret, "ab", "abc"),
       "client-secret-mismatch")

# --- client file --------------------------------------------------------------------------
d = Path(tempfile.mkdtemp())
cf = d / "client.json"
c1 = load_or_create_client(cf)
report("client credentials are created", c1["client_id"].startswith("wsmcp_"), True)
report("they are stable across reads", load_or_create_client(cf), c1)
report("the client file is 0600", oct(cf.stat().st_mode & 0o777), "0o600")
cf.chmod(0o644)
report("a world-readable client file is refused",
       refused(load_or_create_client, cf), "client-file-too-permissive")
shutil.rmtree(d, ignore_errors=True)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
