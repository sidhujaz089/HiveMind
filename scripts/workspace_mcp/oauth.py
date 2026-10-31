#!/usr/bin/env python3
"""The smallest OAuth 2.1 surface claude.ai actually asks for.

WHAT IT ASKED FOR, observed rather than assumed (2026-08-19, from mcp-calls.jsonl):

    POST /mcp                                      -> 401
    GET  /.well-known/oauth-protected-resource/mcp    RFC 9728
    GET  /.well-known/oauth-protected-resource        RFC 9728
    GET  /.well-known/oauth-authorization-server      RFC 8414
    POST /register                                    RFC 7591

Everything here exists to answer those, and nothing else does.

TWO DELIBERATE DEVIATIONS

**No /register.** Dynamic client registration would let anything inside Anthropic's
shared egress range register itself a client. Instead the client_id and client_secret
are generated once, live in a 0600 file, and are pasted into the connector's two
optional fields. Strictly tighter: no self-registration, and revoking is one command.

**/authorize auto-approves.** A consent screen exists so a human can decide whether to
grant an application access to *their* account on a multi-user service. There is one
user, one client and one server here, so the page would be a button Jas presses to say
yes to himself. The real controls are the IP allowlist, the client secret, and PKCE.

WHAT IS ACTUALLY ENFORCED

  - client_id must match, at both /authorize and /token
  - client_secret must match at /token, compared in constant time
  - the code must exist, be unexpired, and be UNUSED -- replay is refused
  - the PKCE verifier must hash to the challenge presented at /authorize
  - redirect_uri at /token must equal the one at /authorize
  - the code is bound to its client; another client cannot redeem it
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

CLIENT_FILE = Path.home() / ".secrets" / "workspace-mcp-oauth.json"
TOKEN_STORE = Path.home() / ".secrets" / "workspace-mcp-tokens.json"

CODE_TTL_SECONDS = 120          # a browser round trip, not a session
TOKEN_TTL_SECONDS = 30 * 86400  # long: this is one user's own server, and rotation is a
                                # single command. Short expiry here would buy little and
                                # cost a broken connector every time it lapsed.


class OAuthError(Exception):
    """Carries an RFC 6749 error code plus a rule name for the log."""

    def __init__(self, code, rule, detail=""):
        super().__init__(f"{code}: {rule}")
        self.code = code
        self.rule = rule
        self.detail = detail


# ---------------------------------------------------------------------------------
# Client credentials
# ---------------------------------------------------------------------------------

def load_or_create_client(path=None):
    """Return {client_id, client_secret}, creating them once if absent."""
    p = Path(path) if path else CLIENT_FILE
    if p.is_file():
        mode = p.stat().st_mode & 0o777
        if mode & 0o077:
            raise OAuthError("server_error", "client-file-too-permissive",
                             f"{p} is {oct(mode)}, want 0600")
        return json.loads(p.read_text(encoding="utf-8"))

    creds = {
        "client_id": "wsmcp_" + secrets.token_urlsafe(16),
        "client_secret": secrets.token_urlsafe(32),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch(mode=0o600, exist_ok=True)
    p.chmod(0o600)
    p.write_text(json.dumps(creds, indent=2) + "\n", encoding="utf-8")
    return creds


# ---------------------------------------------------------------------------------
# Discovery documents
# ---------------------------------------------------------------------------------

def protected_resource_metadata(issuer):
    """RFC 9728. Tells the client which authorization server guards this resource."""
    return {
        "resource": f"{issuer}/mcp",
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
    }


def authorization_server_metadata(issuer):
    """RFC 8414. Advertises only what is implemented -- claiming more would send a client
    down a path that returns 404, which is a worse failure than not offering it."""
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post",
                                                  "client_secret_basic"],
    }


# ---------------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------------

def pkce_challenge(verifier):
    """S256 only. 'plain' is in the spec and is worthless -- it makes the challenge and
    the verifier the same string, so an intercepted authorization request hands over
    everything needed to redeem the code."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify_pkce(verifier, challenge, method="S256"):
    if method != "S256":
        raise OAuthError("invalid_request", "pkce-method-unsupported", method)
    if not verifier:
        raise OAuthError("invalid_grant", "pkce-verifier-missing")
    return hmac.compare_digest(pkce_challenge(verifier), challenge)


# ---------------------------------------------------------------------------------
# Codes and tokens
# ---------------------------------------------------------------------------------

class Store:
    """Codes in memory, tokens on disk.

    Codes live 120 seconds and are single-use, so losing them on restart costs a retry.
    Tokens are persisted because losing those breaks the connector until someone notices
    -- a silent outage is worse than a file.
    """

    def __init__(self, token_path=None):
        self.codes = {}
        self.token_path = Path(token_path) if token_path else TOKEN_STORE

    # -- authorization codes ------------------------------------------------------
    def issue_code(self, client_id, redirect_uri, code_challenge, method="S256"):
        if method != "S256":
            raise OAuthError("invalid_request", "pkce-method-unsupported", method)
        if not code_challenge:
            raise OAuthError("invalid_request", "pkce-challenge-missing")
        code = secrets.token_urlsafe(32)
        self.codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "challenge": code_challenge,
            "method": method,
            "expires": time.time() + CODE_TTL_SECONDS,
            "used": False,
        }
        return code

    def redeem_code(self, code, verifier, client_id, redirect_uri):
        rec = self.codes.get(code)
        if rec is None:
            raise OAuthError("invalid_grant", "code-unknown")
        # Single use. Marked before any other check so a replay cannot be retried
        # against a different failure path.
        if rec["used"]:
            raise OAuthError("invalid_grant", "code-already-used")
        if time.time() > rec["expires"]:
            raise OAuthError("invalid_grant", "code-expired")
        rec["used"] = True
        if not hmac.compare_digest(rec["client_id"], client_id or ""):
            raise OAuthError("invalid_grant", "code-belongs-to-another-client")
        if rec["redirect_uri"] != redirect_uri:
            raise OAuthError("invalid_grant", "redirect-uri-mismatch")
        if not verify_pkce(verifier, rec["challenge"], rec["method"]):
            raise OAuthError("invalid_grant", "pkce-verifier-mismatch")
        return True

    # -- access tokens ------------------------------------------------------------
    def _read_tokens(self):
        if not self.token_path.is_file():
            return {}
        try:
            return json.loads(self.token_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # An unreadable token store must not silently become an empty one that
            # accepts nothing OR everything -- fail loudly.
            raise OAuthError("server_error", "token-store-unreadable", str(self.token_path))

    def _write_tokens(self, tokens):
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.touch(mode=0o600, exist_ok=True)
        self.token_path.chmod(0o600)
        self.token_path.write_text(json.dumps(tokens, indent=2) + "\n", encoding="utf-8")

    def issue_token(self, client_id):
        token = secrets.token_urlsafe(32)
        tokens = self._read_tokens()
        now = time.time()
        # Drop anything already expired while we are here, so the file does not grow
        # without bound and a stale entry cannot be resurrected by a clock change.
        tokens = {k: v for k, v in tokens.items() if v.get("expires", 0) > now}
        tokens[token] = {"client_id": client_id, "issued": now,
                         "expires": now + TOKEN_TTL_SECONDS}
        self._write_tokens(tokens)
        return token, TOKEN_TTL_SECONDS

    def validate_token(self, presented):
        if not presented:
            return False
        tokens = self._read_tokens()
        for token, rec in tokens.items():
            if hmac.compare_digest(token, presented):
                return rec.get("expires", 0) > time.time()
        return False

    def identify_token(self, presented):
        """The client_id behind a valid token, or None. Separate from validate_token so
        that stays a plain boolean -- an authorisation check that starts returning a record
        is one refactor away from being truthy when it should not be."""
        if not presented:
            return None
        for token, rec in self._read_tokens().items():
            if hmac.compare_digest(token, presented):
                if rec.get("expires", 0) > time.time():
                    return rec.get("client_id")
                return None
        return None

    def revoke_all(self):
        self._write_tokens({})


def check_client_secret(presented, expected):
    if not presented or not expected:
        raise OAuthError("invalid_client", "client-secret-missing")
    if not hmac.compare_digest(presented, expected):
        raise OAuthError("invalid_client", "client-secret-mismatch")
    return True
