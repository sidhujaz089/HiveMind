"""Relay ledger for the browser half of usage-widget (t1017).

content.js is the extension's content script: it receives the API responses the page's
observer saw and relays them to the local writer on loopback. Today every path from
"the pages changed" to "someone learns it" is silent: the post function's tail is a
catch that discards the error, and the relay forwards url, status and body only, so a
working response leaves no row -- no time, no length, no ok flag. Working and broken are
indistinguishable, and STATUS.md's standing gap cannot be checked with the instruments
that exist.

This suite drives the REAL file text in node with a stub window and a recording fake
fetch, so it can go green only by that file recording one whole row per response, and a
row for the failures it swallows today. No live server is contacted: every request is
answered by the fake, and the driver runs against a copy of the file in a temp dir.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "projects" / "usage-widget" / "extension" / "content.js"

# Real constants taken from the file under test, so a build cannot satisfy the suite by
# inventing its own tags or its own writer address.
TAG = "usage-widget-inject"
BASE = "http://127.0.0.1:4599"
URL = "https://claude.ai/api/usage/limits"
UNTAGGED_URL = "https://claude.ai/api/organizations/not-probed"
STATUS = 200
CAUSE = "Failed to fetch"

# A pretty-printed body. json.dumps of the parsed body re-serialises it MORE COMPACTLY, so
# the length of the string that is relayed and the length of a re-serialisation differ. That
# gap is what lets a test tell "the length of the whole body string" from "the length of
# the parsed object", and the fixture asserts the gap exists before it relies on it.
PAYLOAD = {"accounts": {"acct_9f2c": {"usage": {"requests": 1234, "costUsd": 5.25}}},
           "window": {"resetAt": "2026-10-15T00:00:00Z"}}
BODY = json.dumps(PAYLOAD, indent=2)

DRIVER = """// Drives one content script in a stub browser environment and prints what it posted.
// argv: 2=script 3=mode 4=url 5=status 6=body 7=cause 8=untagged-url
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const mode = process.argv[3];
const RELAYED_URL = process.argv[4];
const RELAYED_STATUS = Number(process.argv[5]);
const RELAYED_BODY = process.argv[6];
const CAUSE = process.argv[7];
const UNTAGGED = process.argv[8];
const TAG = process.argv[9];

const calls = [];
const unhandled = [];
process.on("unhandledRejection", (e) => { unhandled.push(String(e && e.message ? e.message : e)); });

function fakeFetch(url, opts) {
  let parsed = null;
  try { parsed = JSON.parse(opts.body); } catch (e) { parsed = { unparsed: String(opts.body) }; }
  calls.push({ url: String(url), method: String(opts && opts.method), body: parsed });
  if (mode === "reject") {
    return Promise.reject(new TypeError(CAUSE));
  }
  return Promise.resolve({ ok: true, status: 202 });
}

const listeners = [];
const win = { addEventListener: (type, fn) => { if (type === "message") { listeners.push(fn); } },
              postMessage: () => {} };
const stubConsole = { log: () => {}, warn: () => {}, error: () => {} };

let evalError = "";
try {
  const run = new Function("window", "console", "fetch", src);
  run(win, stubConsole, fakeFetch);
} catch (e) {
  evalError = String(e && e.message ? e.message : e);
}

function fire(tag, url, status, body) {
  for (const fn of listeners) {
    try { fn({ data: { __src: tag, url: url, status: status, body: body } }); }
    catch (e) { unhandled.push("listener threw: " + String(e && e.message ? e.message : e)); }
  }
}

async function main() {
  fire(TAG, RELAYED_URL, RELAYED_STATUS, RELAYED_BODY);
  fire("some-other-extension", UNTAGGED, RELAYED_STATUS, RELAYED_BODY);
  await new Promise((resolve) => { setTimeout(resolve, 400); });
  process.stdout.write(JSON.stringify({ calls: calls, unhandled: unhandled, evalError: evalError }));
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({ calls: calls, unhandled: unhandled,
                                        evalError: String(e && e.message ? e.message : e) }));
});
"""


def _run(mode, cause=""):
    node = shutil.which("node")
    assert node, ("node is not on PATH: the extension's scripts are JavaScript, so this suite "
                  "cannot run on this box (the repository records `node writer.js` as the way the "
                  "collector runs).")
    assert TARGET.is_file(), ("the file under test is missing: " + str(TARGET)
                              + " -- this suite reads it as text; it must not be replaced")
    src = TARGET.read_text(encoding="utf8")
    with tempfile.TemporaryDirectory(prefix="uw-relay-") as td:
        home = Path(td)
        driver = home / "drive.cjs"
        driver.write_text(DRIVER, encoding="utf8")
        script = home / "content.js"
        script.write_text(src, encoding="utf8")
        res = subprocess.run([node, str(driver), str(script), mode, URL, str(STATUS), BODY,
                              cause, UNTAGGED_URL, TAG],
                             cwd=str(home), capture_output=True, text=True, timeout=90)
        assert res.returncode == 0, ("the driver exited " + str(res.returncode)
                                     + "\nstdout: " + res.stdout + "\nstderr: " + res.stderr)
        lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
        assert lines, ("the driver printed no report. stderr: " + res.stderr)
        report = json.loads(lines[-1])
    assert not report.get("evalError"), ("content.js could not be loaded as a content script: "
                                         + str(report.get("evalError")))
    assert not report["unhandled"], ("content.js let a rejection escape: "
                                     + repr(report["unhandled"]) + " -- a failed POST must be "
                                     "recorded in the row's fields and never propagated")
    return report["calls"]


def _rows(calls):
    """Every POST whose body is a relay row: a whole record, not a forwarded response body."""
    out = []
    for c in calls:
        body = c.get("body")
        if isinstance(body, dict) and "ts" in body and "ok" in body:
            out.append((str(c.get("url", "")), body))
    return out


def test_the_relay_still_forwards_the_whole_parsed_body():
    calls = _run("ok")
    fwd = [c for c in calls if isinstance(c.get("body"), dict) and "body" in c["body"]]
    assert fwd, ("content.js forwarded no response body: the relay must still POST "
                 "{url, status, body} to the writer, which is the behaviour it has today")
    assert fwd[0]["url"] == BASE + "/ingest", ("the forwarded body was posted to "
                                               + repr(fwd[0]["url"]) + ", not " + BASE + "/ingest")
    assert fwd[0]["body"].get("url") == URL, fwd[0]["body"]
    assert fwd[0]["body"].get("status") == STATUS, fwd[0]["body"]
    assert fwd[0]["body"].get("body") == PAYLOAD, ("the relayed body is not the parsed response "
                                                   "text: " + repr(fwd[0]["body"].get("body")))


def test_a_working_response_is_recorded_as_one_whole_row():
    rows = _rows(_run("ok"))
    assert rows, ("content.js recorded no row for a response it forwarded: every relayed response "
                  "must leave a row, so a working collector is visible too, not just a broken one")
    url, row = rows[0]
    assert url == BASE + "/ingest", ("a relay row was posted to " + repr(url)
                                     + "; the writer's capture endpoint is " + BASE + "/ingest")
    for key in ("ts", "url", "status", "bytes", "ok", "error"):
        assert key in row, ("a relay row is missing the field " + repr(key)
                            + "; keys present: " + repr(sorted(row)))
    assert row["url"] == URL, ("the row names " + repr(row["url"]) + ", the endpoint observed is "
                               + URL)
    assert row["status"] == STATUS, ("the row records status " + repr(row["status"]) + ", the "
                                     "response relayed was " + str(STATUS))
    assert isinstance(row["ts"], str) and row["ts"], ("a relay row must carry a timestamp; ts is "
                                                      + repr(row["ts"]))
    assert row["ok"] is True, ("a healthy relay must be recorded as ok true, ok is "
                               + repr(row["ok"]))
    assert row["error"] == "", ("a working response must record an empty error, got "
                                + repr(row["error"]))


def test_the_bytes_are_the_length_of_the_relayed_string_not_of_a_re_serialisation():
    compact = json.dumps(PAYLOAD)
    assert len(BODY) != len(compact), ("the fixture has collapsed: a compact re-serialisation is "
                                       + str(len(compact)) + " characters, the same as the "
                                       "pretty-printed body, so no length here can tell the two "
                                       "apart")
    rows = _rows(_run("ok"))
    assert rows, "no relay row was recorded for a working response"
    recorded = rows[0][1].get("bytes")
    assert isinstance(recorded, int), ("bytes must be a number, got " + repr(recorded)
                                       + " (" + type(recorded).__name__ + ")")
    assert recorded == len(BODY), ("bytes is " + repr(recorded) + "; the body relayed is a string "
                                   "of " + str(len(BODY)) + " characters, and a re-serialisation of "
                                   "the parsed body is " + str(len(compact)) + " -- so a build that "
                                   "measures the object, or measures a cut string, fails here")


def test_a_refused_post_is_recorded_with_the_reason_it_caught():
    rows = _rows(_run("reject", CAUSE))
    assert rows, ("content.js recorded nothing for a relay POST that was refused: its catch "
                  "discards the error, so a dead writer, a refused connection and a success all "
                  "leave the same silence -- this is the defect t1017 exists to close")
    url, row = rows[0]
    assert url == BASE + "/ingest", "the failure row was not sent to the writer: " + repr(url)
    assert row["ok"] is False, ("a refused relay must never read as a success, ok is "
                                + repr(row["ok"]))
    assert row["url"] == URL, repr(row)
    assert row["status"] == STATUS, ("the row must record the status that was being relayed, got "
                                     + repr(row["status"]))
    assert isinstance(row["error"], str) and row["error"], ("a failure row must carry the reason "
                                                            "it caught, error is " + repr(row["error"]))
    assert CAUSE in row["error"], ("the failure row does not carry the message it caught: "
                                   + repr(row["error"]) + "; expected " + repr(CAUSE))


def test_the_failure_row_is_a_whole_record_too():
    rows = _rows(_run("reject", CAUSE))
    assert rows, "no relay row was recorded for a refused POST"
    url, row = rows[0]
    assert isinstance(row.get("ts"), str) and row.get("ts"), ("a failure row needs the time it "
                                                              "happened; ts is " + repr(row.get("ts")))
    assert isinstance(row.get("bytes"), int), ("a failure row needs the size it was relaying; "
                                               "bytes is " + repr(row.get("bytes")))
    assert row["bytes"] == len(BODY), ("bytes is " + repr(row["bytes"]) + ", the body being relayed "
                                       "is " + str(len(BODY)) + " characters")


def test_a_message_from_another_source_is_relayed_and_recorded_nowhere():
    calls = _run("ok")
    for c in calls:
        body = c.get("body")
        if isinstance(body, dict):
            assert body.get("url") != UNTAGGED_URL, ("content.js relayed a message tagged for a "
                                                    "different extension: the listener's __src "
                                                    "filter was removed, so every page message on "
                                                    "the host is captured")
    assert not any(UNTAGGED_URL in str(c.get("url", "")) for c in calls), \
        ("a url only present in an untagged message reached the writer: "
         + repr([str(c.get("url", "")) for c in calls]))


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
