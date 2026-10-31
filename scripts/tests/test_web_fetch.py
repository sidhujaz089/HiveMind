#!/usr/bin/env python3
"""web_fetch refuses every non-public address, checks every redirect, and says when it cuts
(plan step 5). Fakes only: a fake resolver and a fake HTTP getter, no network."""
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import web_fetch as wf  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def refused(fn):
    try:
        fn()
    except wf.Refused as e:
        return str(e)
    return None


def dns(table):
    def gai(host, port, type=None):
        if host not in table:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in table[host]]
    return gai


def main():
    for ip, want in (("93.184.215.14", True), ("127.0.0.1", False), ("10.1.2.3", False),
                     ("172.20.0.1", False), ("192.168.1.1", False), ("169.254.169.254", False),
                     ("100.101.102.103", False), ("::1", False), ("fe80::1", False),
                     ("::ffff:127.0.0.1", False), ("224.0.0.1", False), ("0.0.0.0", False)):
        report(f"public({ip}) is {want}", wf.public(ip), want)
    report("a name with one public AND one private address is refused",
           bool(refused(lambda: wf.resolve("mixed.example", 443, dns({"mixed.example": ["93.184.215.14", "10.0.0.5"]})))), True)
    report("a public-only name resolves to its address",
           wf.resolve("ok.example", 443, dns({"ok.example": ["93.184.215.14"]})), "93.184.215.14")
    for url in ("file:///etc/passwd", "ftp://x.example/", "http://user:pw@x.example/", "gopher://x"):
        report(f"{url.split(':')[0]}:// or credentials in the URL is refused", bool(refused(lambda u=url: wf.check_url(u))), True)

    pages = {"https://ok.example/": (302, {"location": "http://100.64.0.9/admin"}, b""),
             "https://good.example/": (200, {"content-type": "text/html; charset=utf-8"},
                                       b"<html><head><style>x{}</style></head><body><p>Hello &amp; bye"
                                       b"</p><script>evil()</script></body></html>"),
             "https://big.example/": (200, {"content-type": "text/plain"}, b"a" * (wf.MAX_BYTES + 1)),
             "https://bin.example/": (200, {"content-type": "image/png"}, b"\x89PNG")}
    names = {"ok.example": ["93.184.215.14"], "good.example": ["93.184.215.14"],
             "big.example": ["93.184.215.14"], "bin.example": ["93.184.215.14"], "100.64.0.9": ["100.64.0.9"]}
    resolver = dns(names)

    def get(url):
        u = wf.check_url(url)
        wf.resolve(u.hostname, 443, resolver)        # the real _get resolves and checks every hop
        return pages[url]
    why = refused(lambda: wf.fetch("https://ok.example/", resolver, get))
    report("a redirect into the tailnet is refused at the hop", bool(why and "100.64.0.9" in why), True)
    r = wf.fetch("https://good.example/", resolver, get)
    report("HTML comes back as text: tags, scripts and styles gone, entities decoded",
           r["text"], "Hello & bye")
    r = wf.fetch("https://big.example/", resolver, get)
    report("a page over the byte cap is cut AND says so", (r["bytes"], "only the first" in r["cut"]),
           (wf.MAX_BYTES, True))
    report("  and text over the character cap says how much was left out",
           ("more characters of text not shown" in r["cut"], len(r["text"])), (True, wf.MAX_CHARS))
    report("a binary content type is refused, not dumped",
           bool(refused(lambda: wf.fetch("https://bin.example/", resolver, get))), True)

    import role_caps
    report("web_fetch is OFF by default for every role (switching it on is Jas's, on the Models tab; the LIVE store is not pinned here -- t657)", [r for r in ("manager", "auditor", "coder", "senior", "x") if role_caps._default(r).get("web_fetch")], [])
    src = (ROOT / "projects" / "local-inference" / "console.py").read_text()
    report("the console runs it and logs every call to manager-actions.jsonl",
           ('"web_fetch": tool_web_fetch' in src, '_action({"tool": "web_fetch"' in src), (True, True))
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
