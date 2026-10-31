"""Oracle for t1017 (chunk B): the usage-widget writer must RECORD every relay event.

RED ON MAIN for the stated reasons:
  * writer.js writes no ledger at all, so data/relay.jsonl never exists: the 400 returned by a
    malformed POST /usage and the 404 returned by an unknown path leave no record behind.
  * nothing records the length of a body, so a body cut to a fixed prefix is undetectable
    (extension/inject.js slices at 60000 before the body reaches this process).
  * the 200-entry capture ring (MAX_RECON, and the log.shift() inside POST /ingest) deletes the
    oldest captures, so the 200 pre-seeded captures come back 200-minus-3.

Self-sufficient: it copies projects/usage-widget/collector/ into a temp home and pins the listen
port in the COPY's config.js, so it runs against an unmodified config.js today, and it also
passes UW_PORT so it keeps working once config.js honours the environment. Each run is one
bounded subprocess.run; the driver kills the writer, so nothing is left running.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = ROOT / "projects" / "usage-widget" / "collector"
DRIVER = "relay_driver.mjs"

LONG_BLOB = "x" * 120000
SEED_URLS = ["https://claude.ai/api/seed-%03d" % i for i in range(200)]
NEW_URLS = ["https://claude.ai/api/usage/one", "https://claude.ai/api/usage/two",
            "https://claude.ai/api/usage/three"]
KEYS = ("ts", "path", "status", "bytes", "ok", "error")

DRIVER_SRC = r"""
import { spawn } from "node:child_process";
import http from "node:http";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";

const HOME = process.argv[2];
const PORT = Number(process.argv[3]);
const SPEC = JSON.parse(readFileSync(process.argv[4], "utf8"));
const COLLECTOR = path.join(HOME, "collector");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function request(method, route, bodyStr) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: "127.0.0.1", port: PORT, path: route, method,
        headers: { "content-type": "application/json",
                   "content-length": Buffer.byteLength(bodyStr) } },
      (res) => {
        let data = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => { data += chunk; });
        res.on("end", () => {
          let body = null;
          try { body = JSON.parse(data); }
          catch (e) { body = { unparsed: data, parseError: String(e) }; }
          resolve({ path: route, status: res.statusCode, body: body });
        });
      });
    req.setTimeout(8000, () => req.destroy(new Error("no response in 8000ms")));
    req.on("error", reject);
    req.write(bodyStr);
    req.end();
  });
}

function readJsonl(file) {
  if (!existsSync(file)) return [];
  return readFileSync(file, "utf8").split("\n").map((l) => l.trim())
    .filter(Boolean).map((l) => JSON.parse(l));
}

function readCaptureDir(dir) {
  const found = [];
  if (!existsSync(dir)) return found;
  for (const name of readdirSync(dir).sort()) {
    const file = path.join(dir, name);
    const txt = readFileSync(file, "utf8");
    if (name.endsWith(".json")) {
      const doc = JSON.parse(txt);
      for (const e of (Array.isArray(doc) ? doc : [doc])) found.push(e);
    } else {
      for (const e of readJsonl(file)) found.push(e);
    }
  }
  return found;
}

const child = spawn(process.execPath, [path.join(COLLECTOR, "writer.js")], { cwd: COLLECTOR });
let writerLog = "";
child.stdout.on("data", (c) => { writerLog += c; });
child.stderr.on("data", (c) => { writerLog += c; });

const report = { health: null, requests: [], relay: [], capture: [], writerLog: "" };
try {
  const deadline = Date.now() + 15000;
  let last = "";
  while (Date.now() < deadline) {
    try {
      const r = await request("GET", "/health", "");
      if (r.status === 200) { report.health = r.body; break; }
      last = "status " + r.status;
    } catch (e) { last = String(e && e.message ? e.message : e); }
    await sleep(200);
  }
  if (!report.health) report.health = { notAnswering: true, last: last };
  for (const spec of SPEC.requests) {
    const bodyStr = spec.raw === undefined ? JSON.stringify(spec.body) : spec.raw;
    try {
      report.requests.push(await request(spec.method || "POST", spec.path, bodyStr));
    } catch (e) {
      report.requests.push({ path: spec.path, transportError: String(e && e.message ? e.message : e) });
    }
  }
  await sleep(300);
  report.relay = readJsonl(path.join(HOME, "data", "relay.jsonl"));
  report.capture = readCaptureDir(path.join(HOME, "recon"));
} catch (e) {
  report.driverError = String(e && e.stack ? e.stack : e);
} finally {
  child.kill("SIGKILL");
  report.writerLog = writerLog;
  process.stdout.write(JSON.stringify(report) + "\n");
}
"""


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _compact(obj):
    return json.dumps(obj, separators=(",", ":"))


def _prepare(home, port, seeds=None):
    """Copy the collector into the temp home and pin its port there, so the real config.js and
    the real data/ and recon/ directories are never touched by this suite."""
    dst = home / "collector"
    shutil.copytree(str(COLLECTOR), str(dst), ignore=shutil.ignore_patterns("node_modules"))
    cfg = dst / "config.js"
    text = cfg.read_text(encoding="utf8")
    patched = re.sub(r"(port:\s*)\d+", lambda m: m.group(1) + str(port), text, count=1)
    if patched == text:
        print("note: the copied config.js has no `port: <number>` literal; relying on UW_PORT")
    else:
        cfg.write_text(patched, encoding="utf8")
    (home / DRIVER).write_text(DRIVER_SRC, encoding="utf8")
    if seeds is not None:
        (home / "recon").mkdir(parents=True, exist_ok=True)
        (home / "recon" / "browser-capture.json").write_text(json.dumps(seeds), encoding="utf8")
    return dst


def _drive(home, port, requests):
    node = shutil.which("node")
    assert node, ("node is not on PATH: the collector is a node program, so this suite cannot "
                  "run on this box (the repository records `node writer.js` as the way it runs).")
    spec = home / "spec.json"
    spec.write_text(json.dumps({"requests": requests}), encoding="utf8")
    env = dict(os.environ)
    env["UW_HOME"] = str(home)
    env["UW_PORT"] = str(port)
    res = subprocess.run([node, str(home / DRIVER), str(home), str(port), str(spec)],
                         cwd=str(home), capture_output=True, text=True, timeout=90, env=env)
    assert res.returncode == 0, ("the driver exited %s\nstdout: %s\nstderr: %s"
                                 % (res.returncode, res.stdout, res.stderr))
    lines = [line for line in res.stdout.splitlines() if line.strip()]
    assert lines, "the driver printed no report. stderr: " + res.stderr
    report = json.loads(lines[-1])
    assert not report.get("driverError"), "the driver raised: " + report["driverError"]
    assert not report["health"].get("notAnswering"), (
        "the writer never answered GET /health on port %s (last: %s). The suite pinned that port "
        "in the copied config.js, so the writer is not listening where it was asked to. "
        "writer output: %s" % (port, report["health"].get("last"), report.get("writerLog")))
    return report


def test_every_request_leaves_one_whole_ledger_row():
    with tempfile.TemporaryDirectory(prefix="uw-relay-") as td:
        home = Path(td)
        port = _free_port()
        _prepare(home, port)
        usage = _compact({"claudeai": {"ok": True, "requests": 7}})
        ingest = _compact({"url": "https://claude.ai/api/usage/1", "status": 200,
                           "body": {"probe": 1}})
        rep = _drive(home, port, [{"path": "/usage", "raw": usage},
                                  {"path": "/ingest", "raw": ingest},
                                  {"path": "/no-such-route", "raw": "{}"}])
        assert [r.get("status") for r in rep["requests"]] == [200, 200, 404], rep["requests"]
        rows = rep["relay"]
        assert rows, ("data/relay.jsonl does not exist: writer.js records no relay event at all, "
                      "which is the defect this task exists to end")
        for row in rows:
            for key in KEYS:
                assert key in row, "relay row is missing the field %r: %r" % (key, row)
        by_path = {}
        for row in rows:
            by_path.setdefault(row["path"], []).append(row)
        assert sorted(by_path) == ["/ingest", "/no-such-route", "/usage"], sorted(by_path)
        assert by_path["/usage"][0]["ok"] is True, by_path["/usage"]
        assert by_path["/usage"][0]["status"] == 200, by_path["/usage"]
        assert by_path["/usage"][0]["bytes"] == len(usage.encode("utf-8")), by_path["/usage"]
        assert by_path["/ingest"][0]["ok"] is True, by_path["/ingest"]
        assert by_path["/ingest"][0]["bytes"] == len(ingest.encode("utf-8")), by_path["/ingest"]
        miss = by_path["/no-such-route"][0]
        assert miss["ok"] is False, miss
        assert "/no-such-route" in str(miss["error"]), miss


def test_bytes_records_the_whole_body_length_not_a_prefix():
    with tempfile.TemporaryDirectory(prefix="uw-relay-") as td:
        home = Path(td)
        port = _free_port()
        _prepare(home, port)
        payload = _compact({"url": "https://claude.ai/api/usage/big", "status": 200,
                            "body": {"blob": LONG_BLOB}})
        rep = _drive(home, port, [{"path": "/ingest", "raw": payload}])
        assert [r.get("status") for r in rep["requests"]] == [200], rep["requests"]
        rows = [r for r in rep["relay"] if r.get("path") == "/ingest"]
        assert rows, "no /ingest row in data/relay.jsonl: %r" % (rep["relay"],)
        expected = len(payload.encode("utf-8"))
        sizes = [r.get("bytes") for r in rows]
        assert expected in sizes, ("no relay row records the WHOLE body length %d; recorded %r. "
                                  "A capped slice passes at the cap, which is the defect this "
                                  "assertion exists for." % (expected, sizes))


def test_a_failure_records_the_cause_the_caller_is_given():
    with tempfile.TemporaryDirectory(prefix="uw-relay-") as td:
        home = Path(td)
        port = _free_port()
        _prepare(home, port)
        rep = _drive(home, port, [{"path": "/usage", "raw": "{not json"}])
        got = rep["requests"][0]
        assert got.get("status") == 400, got
        assert got["body"].get("ok") is False, got
        reported = str(got["body"].get("error", ""))
        assert len(reported) > 5, "the 400 body carries no cause: %r" % (got,)
        rows = [r for r in rep["relay"] if r.get("path") == "/usage" and r.get("ok") is False]
        assert rows, ("the failed /usage POST recorded nothing in data/relay.jsonl: %r"
                      % (rep["relay"],))
        assert reported in str(rows[0].get("error", "")), (reported, rows[0])


def test_nothing_already_recorded_is_deleted():
    seeds = [{"at": "2026-01-01T00:00:00Z", "url": url, "status": 200, "body": {}}
             for url in SEED_URLS]
    with tempfile.TemporaryDirectory(prefix="uw-relay-") as td:
        home = Path(td)
        port = _free_port()
        _prepare(home, port, seeds=seeds)
        requests = [{"path": "/ingest",
                     "raw": _compact({"url": url, "status": 200, "body": {}})} for url in NEW_URLS]
        rep = _drive(home, port, requests)
        assert [r.get("status") for r in rep["requests"]] == [200, 200, 200], rep["requests"]
        urls = {e.get("url") for e in rep["capture"]}
        lost = [url for url in SEED_URLS if url not in urls]
        assert not lost, ("the capture store lost %d of the 200 pre-existing captures (%s). "
                          "The 200-entry ring deletes its oldest entries as it fills; recording "
                          "something must never delete it." % (len(lost), lost[:3]))
        for url in NEW_URLS:
            assert url in urls, "the new capture %s was not persisted" % url
        assert len(urls) == 203, sorted(urls)[:5]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except Exception:
            failures.append(name)
            print("FAIL %s" % name)
            traceback.print_exc()
    print("\n%d/%d passed" % (len(tests) - len(failures), len(tests)))
    sys.exit(1 if failures else 0)
