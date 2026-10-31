"""Oracle for t1017 (chunk A): the usage-widget collector must honour UW_HOME and UW_PORT.

RED ON MAIN for the stated reason: projects/usage-widget/collector/config.js resolves outFile,
reconFile and the listen port from __dirname and a literal 4599, and reads no environment
variable. A collector started with UW_HOME and UW_PORT set therefore (a) binds 4599 and never
answers GET /health on the port the test asked for, and (b) writes usage.json and the recon
capture into the repo's own projects/usage-widget/ tree instead of the throwaway home. All three
tests assert exactly that. Once config.js reads the two variables, all three pass.

The writer is driven by a generated Node script (DRIVER_SRC) written into the temp home, so every
process the oracle starts is a bounded subprocess.run with a timeout and nothing outlives it.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = ROOT / "projects" / "usage-widget" / "collector"
REPO_DATA = ROOT / "projects" / "usage-widget" / "data"
DRIVER = "relay_driver.mjs"

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


def _prepare(home):
    dst = home / "collector"
    shutil.copytree(str(COLLECTOR), str(dst), ignore=shutil.ignore_patterns("node_modules"))
    (home / DRIVER).write_text(DRIVER_SRC, encoding="utf8")
    return dst


def _snapshot(d):
    if not d.exists():
        return {}
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in d.iterdir()}


def _drive(home, port, requests):
    """Start the copied writer, run `requests` against it, return the driver's JSON report.

    Every request body is passed as a pre-serialised string so the byte length the writer sees
    is the byte length the test computes -- a length the ledger must record exactly.
    """
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
        "the writer never answered GET /health on port %s (last: %s). config.js:16 hardcodes "
        "port 4599 and reads no environment variable, so UW_PORT=%s is ignored. "
        "writer output: %s" % (port, report["health"].get("last"), port, report.get("writerLog")))
    return report


def test_usage_json_lands_where_uw_home_points():
    with tempfile.TemporaryDirectory(prefix="uw-cfg-") as td:
        home = Path(td)
        _prepare(home)
        body = json.dumps({"claudeai": {"ok": True, "requests": 7}}, separators=(",", ":"))
        rep = _drive(home, _free_port(), [{"path": "/usage", "raw": body}])
        assert [r.get("status") for r in rep["requests"]] == [200], rep["requests"]
        out = home / "data" / "usage.json"
        assert out.is_file(), ("UW_HOME was ignored: no %s. config.js:9 resolves outFile from "
                               "__dirname, so the collector wrote into the repo's own tree." % out)
        doc = json.loads(out.read_text(encoding="utf8"))
        assert doc.get("claudeai") == {"ok": True, "requests": 7}, doc
        assert doc.get("api") is None, doc
        assert doc.get("generatedAt"), doc


def test_recon_capture_lands_under_uw_home():
    with tempfile.TemporaryDirectory(prefix="uw-cfg-") as td:
        home = Path(td)
        _prepare(home)
        entry = json.dumps({"url": "https://claude.ai/api/usage/probe", "status": 200,
                            "body": {"probe": True}}, separators=(",", ":"))
        rep = _drive(home, _free_port(), [{"path": "/ingest", "raw": entry}])
        assert [r.get("status") for r in rep["requests"]] == [200], rep["requests"]
        cap = home / "recon" / "browser-capture.json"
        assert cap.is_file(), ("UW_HOME was ignored: no %s. config.js:12 resolves reconFile from "
                               "__dirname, so captures land in the repo." % cap)
        urls = [e.get("url") for e in rep["capture"]]
        assert "https://claude.ai/api/usage/probe" in urls, urls


def test_the_repos_own_data_dir_is_never_the_target():
    before = _snapshot(REPO_DATA)
    with tempfile.TemporaryDirectory(prefix="uw-cfg-") as td:
        home = Path(td)
        _prepare(home)
        body = json.dumps({"api": {"ok": True, "cost_usd": 1.25}}, separators=(",", ":"))
        _drive(home, _free_port(), [{"path": "/usage", "raw": body}])
    after = _snapshot(REPO_DATA)
    assert after == before, ("a collector run against a temp UW_HOME wrote into the repo's own "
                             "data dir: %s -> %s" % (sorted(before), sorted(after)))


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
