"""Whole-body relay for the page-side observer of usage-widget (t1017).

inject.js runs in the page's main world and watches the API calls the page already makes,
posting JSON responses to the extension's content script. It slices the body to 60000
characters and says nothing about the cut: every large response reaches the writer with
its tail gone and no record that a tail existed, so a truncated record looks complete --
worse than no record, because the gap is invisible.

This suite drives the REAL file text in node: a stub window records every posted message,
a fake XHR class whose base open and send COUNT that they were called, and a stub fetch
returning a 70048-character JSON body whose tail carries a marker. No live server, no
port bound, nothing written outside the temp dir.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "projects" / "usage-widget" / "extension" / "inject.js"

# Real constants: the tag the content script filters on, and a long JSON body whose tail
# sits far past any cap a build might keep. The tail marker is the load-bearing value: a
# prefix, of any length, cannot contain it.
TAIL = "TAIL-MARKER-9c4e"
BODY = '{"data":{"pad":"' + ("x" * 70000) + '","tail":"' + TAIL + '"}}'
TAG = "usage-widget-inject"
FETCH_URL = "https://claude.ai/api/usage/limits"
XHR_URL = "https://claude.ai/api/organization/spend"
PLAIN_URL = "https://claude.ai/api/usage/plain"
IRRELEVANT = "https://www.gstatic.com/s/px/cell.js"

DRIVER = """// Drives one page-side observer in a stub page environment and prints what it posted.
// argv: 2=script 3=fetch-url 4=xhr-url 5=body 6=irrelevant-url 7=plain-url
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const FETCH_URL = process.argv[3];
const XHR_URL = process.argv[4];
const BODY = process.argv[5];
const IRRELEVANT = process.argv[6];
const PLAIN_URL = process.argv[7];

const posts = [];
const stubConsole = { log: () => {}, warn: () => {}, error: () => {} };
let baseOpen = 0;
let baseSend = 0;

class FakeXHR {
  constructor() { this.listeners = {}; this.status = 0; this.responseText = ""; }
  open(method, url) { baseOpen += 1; this.__baseOpened = method + " " + url; }
  send() { baseSend += 1; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  getResponseHeader(name) {
    return String(name).toLowerCase() === "content-type" ? "application/json" : "";
  }
}

function bodyOf(text) { return String(text).toLowerCase(); }

function jsonResponse(ct) {
  return {
    status: 200,
    headers: { get: (n) => (bodyOf(n) === "content-type" ? ct : "") },
    clone: () => ({ text: () => Promise.resolve(BODY) }),
    text: () => Promise.resolve(BODY),
  };
}

const RESPONSE = jsonResponse("application/json");
const PLAIN = jsonResponse("text/plain");
const ORIG_FETCH = async function (url) {
  if (String(url) === PLAIN_URL) { return PLAIN; }
  return RESPONSE;
};
const win = { fetch: ORIG_FETCH, postMessage: (msg) => { posts.push(msg); } };

let evalError = "";
let contractError = "";
try {
  const run = new Function("window", "console", "location", "XMLHttpRequest", src);
  run(win, stubConsole, { host: "claude.ai" }, FakeXHR);
} catch (e) {
  evalError = String(e && e.message ? e.message : e);
}

async function main() {
  const r1 = await win.fetch(FETCH_URL);
  if (r1 !== RESPONSE) { contractError += "the fetch wrapper did not return the original response object; "; }
  await win.fetch(PLAIN_URL);
  await win.fetch(IRRELEVANT);

  const xhr = new FakeXHR();
  xhr.open("GET", XHR_URL);
  xhr.status = 200;
  xhr.responseText = BODY;
  xhr.send();
  const load = xhr.listeners.load;
  if (typeof load !== "function") {
    contractError += "the send wrapper registered no load listener; ";
  } else {
    load.call(xhr);
  }
  if (baseOpen < 1) { contractError += "the open wrapper never called through to the original open; "; }
  if (baseSend < 1) { contractError += "the send wrapper never called through to the original send; "; }

  await new Promise((resolve) => { setTimeout(resolve, 500); });
  process.stdout.write(JSON.stringify({ posts: posts, evalError: evalError,
                                        contractError: contractError,
                                        baseOpen: baseOpen, baseSend: baseSend }));
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({ posts: posts, evalError: evalError,
                                        contractError: String(e && e.message ? e.message : e),
                                        baseOpen: baseOpen, baseSend: baseSend }));
});
"""

_REPORT = {}


def _report():
    if "r" not in _REPORT:
        node = shutil.which("node")
        assert node, ("node is not on PATH: the extension's scripts are JavaScript, so this suite "
                      "cannot run on this box (the repository records `node writer.js` as the way "
                      "the collector runs).")
        assert TARGET.is_file(), ("the file under test is missing: " + str(TARGET)
                                  + " -- this suite reads it as text; it must not be replaced")
        src = TARGET.read_text(encoding="utf8")
        with tempfile.TemporaryDirectory(prefix="uw-observe-") as td:
            home = Path(td)
            driver = home / "drive.cjs"
            driver.write_text(DRIVER, encoding="utf8")
            script = home / "inject.js"
            script.write_text(src, encoding="utf8")
            res = subprocess.run([node, str(driver), str(script), FETCH_URL, XHR_URL, BODY,
                                  IRRELEVANT, PLAIN_URL],
                                 cwd=str(home), capture_output=True, text=True, timeout=90)
            assert res.returncode == 0, ("the driver exited " + str(res.returncode)
                                         + "\nstdout: " + res.stdout + "\nstderr: " + res.stderr)
            lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
            assert lines, ("the driver printed no report. stderr: " + res.stderr)
            rep = json.loads(lines[-1])
        assert not rep.get("evalError"), ("inject.js could not be loaded in a page world: "
                                          + str(rep.get("evalError")))
        _REPORT["r"] = rep
    return _REPORT["r"]


def _tagged():
    return [p for p in _report()["posts"] if p.get("__src") == TAG]


def test_the_whole_body_is_relayed_with_its_tail_intact():
    posts = _tagged()
    assert posts, ("inject.js posted nothing tagged " + repr(TAG) + ": the relay channel from the "
                   "page to the content script is the observer's whole job")
    on_fetch = [p for p in posts if p.get("url") == FETCH_URL]
    assert on_fetch, ("no message carries the fetch-channel url " + repr(FETCH_URL)
                      + "; urls seen: " + repr([p.get("url") for p in posts]))
    body = on_fetch[0].get("body")
    assert isinstance(body, str), ("the relayed body must be the response text, got "
                                   + type(body).__name__)
    assert TAIL in body, ("the relayed body is missing the marker that sits at its tail: "
                          + str(len(body)) + " characters were relayed out of " + str(len(BODY))
                          + " -- the body is being sliced before it is relayed")
    assert body == BODY, ("the relayed body is not byte-identical to the response text: "
                          + str(len(body)) + " of " + str(len(BODY)) + " characters")


def test_the_length_of_the_whole_body_is_recorded_beside_it():
    posts = _tagged()
    assert posts, "inject.js relayed nothing, so no length is recorded"
    for p in posts:
        assert "bytes" in p, ("a relay message carries no length of the body it carries: keys "
                              + repr(sorted(k for k in p)))
        assert isinstance(p["bytes"], int), ("bytes must be a number, got " + repr(p["bytes"])
                                             + " (" + type(p["bytes"]).__name__ + ")")
        assert p["bytes"] == len(BODY), ("bytes is " + repr(p["bytes"]) + ", the response text is "
                                         + str(len(BODY)) + " characters: the length recorded is "
                                         "not the length of the whole body")


def test_the_xhr_channel_reports_the_same_whole_body():
    posts = _report()["posts"]
    xhr = [p for p in posts if p.get("url") == XHR_URL]
    assert xhr, ("no message carries the XHR-channel url " + repr(XHR_URL) + "; urls seen: "
                 + repr([p.get("url") for p in posts]) + " -- the fetch path cannot stand in for "
                 "the XHR path, they are separate call sites")
    assert xhr[0].get("body") == BODY, ("the XHR channel relays "
                                        + str(len(str(xhr[0].get("body")))) + " of "
                                        + str(len(BODY)) + " characters")
    assert xhr[0].get("bytes") == len(BODY), ("the XHR channel records bytes "
                                              + repr(xhr[0].get("bytes")) + ", expected "
                                              + str(len(BODY)))


def test_a_url_outside_the_relevant_pattern_is_never_relayed():
    posts = _report()["posts"]
    assert not [p for p in posts if p.get("url") == IRRELEVANT], ("inject.js relayed a response "
                                                                 "whose url matches none of the "
                                                                 "patterns it watches: every "
                                                                 "response on the page would be "
                                                                 "posted to the extension")


def test_a_non_json_response_is_never_relayed():
    posts = _report()["posts"]
    assert not [p for p in posts if p.get("url") == PLAIN_URL], ("inject.js relayed a text/plain "
                                                                "response: it posts only JSON "
                                                                "bodies, per its own content-type "
                                                                "condition")


def test_the_observer_still_hands_the_call_back_to_the_page():
    rep = _report()
    assert not rep["contractError"], ("the observer no longer behaves as a passive observer: "
                                      + str(rep["contractError"]) + " -- it must return the "
                                      "response it was handed and call through to the original "
                                      "open and send")
    assert rep["baseOpen"] == 1, ("the page saw " + str(rep["baseOpen"])
                                 + " calls to the original open; the wrapper must delegate exactly "
                                 "once per XHR")
    assert rep["baseSend"] == 1, ("the page saw " + str(rep["baseSend"])
                                 + " calls to the original send: an observer that replaces send "
                                 "without delegating breaks every request the page makes")


if __name__ == "__main__":
    tests = sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f))
    failures = []
    for name, fn in tests:
        try:
            fn()
            print("PASS " + name)
        except Exception:
            failures.append(name)
            print("FAIL " + name)
            traceback.print_exc()
    print(str(len(tests) - len(failures)) + "/" + str(len(tests)) + " passed")
    sys.exit(1 if failures else 0)
