#!/usr/bin/env python3
"""Fetch a public web page and return its readable text -- the models' web_fetch tool.

    python3 scripts/web_fetch.py <url>          # what a model would get back

Jas, 2026-09-29 (Models / Roles / Flow plan, step 5): "Web tools for the models." web_fetch is
built here; web_search needs a search provider, which is his choice, so it is a task held for him.

WHAT IT REFUSES, AND WHY EACH ONE
  * anything but http:// and https:// -- file://, ftp://, gopher:// reach things a URL should not.
  * any address that is not PUBLIC: loopback, private (10/8, 172.16/12, 192.168/16), link-local,
    the tailnet (100.64.0.0/10 -- Tailscale's range, where the Hub and the NAS live), multicast,
    reserved, and their IPv6 forms. A model must not be able to reach the Hub, Ollama, the router
    or the NAS by asking for a URL, and every one of those is on a non-public address.
  * a hostname that resolves to ANY non-public address, even if it also has a public one.
  * a redirect to any of the above: every hop is checked the same way, at most MAX_REDIRECTS.
THE ADDRESS IS PINNED. The host is resolved once, checked, and the connection is made to THAT
address (with the hostname kept for TLS and the Host header). Resolving again at connect time
would let a DNS answer change between the check and the use -- the rebinding trick.

CAPPED: MAX_BYTES read from the wire (the rest is not downloaded), TIMEOUT_S per request, and
MAX_CHARS of text returned -- and a cut is SAID, with how much was left out, never silent.

It is a normal Console tool, OFF for every role by default (role_caps.py), and every call is
logged to workspace/manager-actions.jsonl like run_command.
"""
import html
import http.client
import ipaddress
import re
import socket
import ssl
import sys
from urllib.parse import urljoin, urlsplit

TIMEOUT_S = 20
MAX_BYTES = 2_000_000
MAX_CHARS = 40_000
MAX_REDIRECTS = 5
UA = "HiveMind-web_fetch/1 (+local research tool)"
TAILNET = ipaddress.ip_network("100.64.0.0/10")
HTML_SNIFF = re.compile(r"<html|<body|<p[ >]", re.I)


class Refused(Exception):
    pass


def public(ip):
    """Is this address on the public internet? Everything else is refused."""
    a = ipaddress.ip_address(ip)
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    if isinstance(a, ipaddress.IPv4Address) and a in TAILNET:
        return False
    return a.is_global and not (a.is_multicast or a.is_reserved or a.is_loopback or a.is_link_local
                                or a.is_private or a.is_unspecified)


def resolve(host, port, getaddrinfo=socket.getaddrinfo):
    """The one address to connect to, after checking EVERY address the name resolves to."""
    try:
        infos = getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise Refused(f"cannot resolve {host}: {e}") from None
    addrs = sorted({i[4][0] for i in infos})
    if not addrs:
        raise Refused(f"{host} resolves to nothing")
    bad = [a for a in addrs if not public(a)]
    if bad:
        raise Refused(f"{host} resolves to a non-public address ({', '.join(bad)}): the Hub, the "
                      "models, the router and the NAS live on those, so web_fetch never goes there")
    return addrs[0]


def check_url(url):
    u = urlsplit(url)
    if u.scheme not in ("http", "https"):
        raise Refused(f"only http:// and https:// URLs, not {u.scheme or 'no scheme'}://")
    if not u.hostname:
        raise Refused("the URL has no host")
    if u.username or u.password:
        raise Refused("a URL carrying a user name or password is refused")
    return u


class _Pinned(http.client.HTTPConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedTLS(http.client.HTTPSConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _get(url, getaddrinfo):
    u = check_url(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    ip = resolve(u.hostname, port, getaddrinfo)
    conn = (_PinnedTLS if u.scheme == "https" else _Pinned)(u.hostname, ip, port, TIMEOUT_S)
    try:
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        conn.request("GET", path, headers={"User-Agent": UA, "Accept": "text/html, text/plain, */*;q=0.5",
                                           "Accept-Encoding": "identity"})
        r = conn.getresponse()
        body = r.read(MAX_BYTES + 1)
        return r.status, dict((k.lower(), v) for k, v in r.getheaders()), body
    finally:
        conn.close()


def readable(raw, ctype):
    """Text a model can read: HTML stripped of scripts, styles and tags; anything textual as is."""
    charset = (re.search(r"charset=([\w-]+)", ctype or "", re.I) or [None, "utf-8"])[1]
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    if "html" in (ctype or "").lower() or HTML_SNIFF.search(text, 0, 2000):   # sniff the head only
        text = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article)>", "\n", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def fetch(url, getaddrinfo=socket.getaddrinfo, get=None):
    """-> {"url", "status", "text", "cut", "bytes"}; raises Refused for anything it will not fetch."""
    get = get or (lambda u: _get(u, getaddrinfo))
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url)
        status, headers, body = get(url)
        if status in (301, 302, 303, 307, 308) and headers.get("location"):
            url = urljoin(url, headers["location"])
            continue
        ctype = headers.get("content-type", "")
        if not re.search(r"text/|html|json|xml", ctype, re.I) and ctype:
            raise Refused(f"{url} is {ctype}, not text -- web_fetch returns readable text only")
        over = len(body) > MAX_BYTES
        text = readable(body[:MAX_BYTES], ctype)
        cut = ""
        if over:
            cut = f"[the page is over {MAX_BYTES:,} bytes; only the first {MAX_BYTES:,} were downloaded]"
        if len(text) > MAX_CHARS:
            cut = (cut + " " if cut else "") + f"[{len(text) - MAX_CHARS:,} more characters of text not shown]"
            text = text[:MAX_CHARS]
        return {"url": url, "status": status, "text": text, "cut": cut, "bytes": len(body[:MAX_BYTES])}
    raise Refused(f"more than {MAX_REDIRECTS} redirects")


def main(argv):
    if len(argv) != 2:
        print(__doc__.split("\n\n")[0])
        return 2
    try:
        r = fetch(argv[1])
    except Refused as e:
        print(f"REFUSED: {e}")
        return 1
    except (OSError, http.client.HTTPException) as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        return 1
    print(f"{r['status']} {r['url']} ({r['bytes']:,} bytes){' ' + r['cut'] if r['cut'] else ''}\n")
    print(r["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
