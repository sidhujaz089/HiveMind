#!/usr/bin/env python3
"""Bearer-token authentication for the http transport, with the failure modes fixtured.

WHERE THIS SITS

It is the THIRD layer, not the first, and that ordering is the point:

  1. cloudflared    no inbound port on this machine at all
  2. Cloudflare WAF  only 160.79.104.0/21 reaches the tunnel -- the public stops here
  3. this module     distinguishes Jas's Chat from anything else in that shared range
  4. HTTP_TOOLS      even an authenticated caller cannot write, replace, or touch chat/

Layer 2 is doing most of the work. This exists because Anthropic's egress range is shared
across all their traffic, so "came from Anthropic" is not "came from Jas".

WHY A BEARER TOKEN AND NOT A FULL OAUTH SERVER

claude.ai custom connectors speak OAuth, and implementing an OAuth 2.1 authorization
server means writing authorization-code exchange, PKCE verification, token issuance,
refresh and revocation. That is a lot of security-critical code, and the honest record
from the last two days is eight defects found in checking code -- four of which looked
like success. A smaller surface I can actually fixture beats a larger one I cannot.

So: a single long random token, compared in constant time, with every refusal logged.
If claude.ai turns out to require a full OAuth handshake, this becomes the credential
store behind it rather than being thrown away.

THE FAILURE MODE THAT MATTERS

A missing header, a malformed one, or an empty configured token must all REFUSE. The
dangerous bug here is not "valid token rejected" -- that announces itself loudly. It is
"absent token accepted", which looks exactly like everything working.
"""

import hmac
import os
import secrets
from pathlib import Path

TOKEN_FILE = Path.home() / ".secrets" / "workspace-mcp-token"
MIN_TOKEN_LENGTH = 32


class AuthError(Exception):
    """Carries a rule name so the log records WHY, not merely that something failed."""

    def __init__(self, rule, detail=""):
        super().__init__(f"{rule}: {detail}" if detail else rule)
        self.rule = rule
        self.detail = detail


def generate_token(nbytes=32):
    """256 bits from the OS CSPRNG. urlsafe so it survives a header unescaped."""
    return secrets.token_urlsafe(nbytes)


def load_token(path=None):
    """Read the expected token from disk.

    Refuses rather than defaulting. A server that starts with no token and serves
    everyone is the exact shape this file exists to prevent -- and it would look
    completely healthy from the outside.
    """
    p = Path(path) if path else TOKEN_FILE
    if not p.is_file():
        raise AuthError("no-token-configured", str(p))

    mode = p.stat().st_mode & 0o777
    if mode & 0o077:
        raise AuthError("token-file-too-permissive", f"{p} is {oct(mode)}, want 0600")

    token = p.read_text(encoding="utf-8").strip()
    if not token:
        raise AuthError("empty-token", str(p))
    if len(token) < MIN_TOKEN_LENGTH:
        raise AuthError("token-too-short", f"{len(token)} chars, minimum {MIN_TOKEN_LENGTH}")
    return token


def extract_bearer(headers):
    """Pull the credential out of an Authorization header.

    Header names are case-insensitive per RFC 7230, and getting that wrong would mean
    a client sending 'authorization' sails past a check looking for 'Authorization'.
    """
    value = None
    for k, v in (headers or {}).items():
        if k.lower() == "authorization":
            value = v
            break
    if value is None:
        raise AuthError("no-authorization-header")

    parts = value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthError("malformed-authorization-header")
    presented = parts[1].strip()
    if not presented:
        raise AuthError("empty-bearer-token")
    return presented


def check(headers, expected=None, token_path=None):
    """Authorise a request, or raise AuthError. Returns True only on a real match.

    Comparison is constant-time. A plain == leaks how much of the token matched through
    timing, which over enough requests recovers it byte by byte -- and this endpoint is
    reachable by anything inside Anthropic's range, so "enough requests" is cheap.
    """
    if expected is None:
        expected = load_token(token_path)
    presented = extract_bearer(headers)
    if not hmac.compare_digest(presented, expected):
        raise AuthError("token-mismatch")
    return True
