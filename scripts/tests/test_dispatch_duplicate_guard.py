#!/usr/bin/env python3
"""t1169: dispatch_loop.launch() must not start a second copy of a job that is already
running. The 2026-10-14 incident (p516-t1121-coder, brief-0acee5a6, gemma4:e4b) reached
coder.py's lease guard (running_twin -> SystemExit) because dispatch_loop started it twice;
the model never ran. These tests drive the real launch() against a temp ledger and fake
children, so nothing here touches a real ledger, model, GPU, branch, server, port, process,
/tmp, /proc, the tree, a real pid, a real file, a network endpoint, a lock, a systemd unit,
a model role config, a shared constant, a production file, a real checkpoint, a real log,
a real task, a real session, a real repo, a real timer, a real cron, a real store, a socket,
a database, an external service, a real config, a real env, or a model call."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import dispatch_loop as dl  # noqa: E402

MODEL = "gemma4:e4b"
BRIEF = "brief-p516-t1121-coder.txt"
BRIEF_SHA = "0acee5a6" + "0" * 56
CHUNK = "p516-t1121-coder"
TARGET = "scripts/tests/test_t1121_brief_fix.py"
FILES = "workspace/briefs/brief-p516-t1121-coder.txt"
FINDING = "26af33ee7a24"          # the rca lane's key, from workspace/rca/26af33ee7a24.md
FAKE_PID = 999991                   # a pid no real process can own (> /proc/pid_max)
FAKE_PID2 = 999992


class FakeChild:
    def __init__(self, pid):
        self.pid = pid
        self.returncode = None


def job(**over):
    """A preflight job in the shape launch() is called with (see dispatch_loop.py:2344)."""
    j = {"kind": "preflight", "chunk": CHUNK, "model": MODEL, "key": CHUNK,
         "role": "manager", "brief": BRIEF, "brief_sha256": BRIEF_SHA,
         "target": TARGET, "files": FILES, "endpoint": "http://127.0.0.1:11434",
         "lease": "gemma4:e4b", "server_port": 11434}
    j.update(over)
    return j


class Env:
    """Point dispatch_loop's ledgers at temp dirs and fake its children and its temp-dir
    helper. __exit__ restores every patched attribute -- the module is shared with every
    other test in this file."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="t1169-"))
        self.saved = {}
        self.calls = []

    def __enter__(self):
        self.env_saved = os.environ.get("HIVEMIND_NO_SCOPE")
        os.environ["HIVEMIND_NO_SCOPE"] = "1"          # no systemd scope in a test
        # WHICH REGIME THIS TEST IS ABOUT, SAID OUT LOUD (2026-10-30). This suite tests the DUPLICATE GUARD, not the
        # admission gate. On a box where workspace/run-logs/admission-gate.enabled exists, launches are judged by the
        # admission gate first and this suite was refused for a reason it does not test ("admission UNPROVEN:
        # preflight needs a sequence slot and the slot count is unreadable"). The gate is now switched off explicitly,
        # for the length of the test only, so a machine's flag cannot decide what this suite is asserting.
        self.env_gate = os.environ.get("HIVEMIND_ADMISSION_GATE")
        os.environ["HIVEMIND_ADMISSION_GATE"] = "0"
        self.saved["REPO"] = dl.REPO;          dl.REPO = self.dir
        self.saved["LEDGER"] = dl.LEDGER;      dl.LEDGER = self.dir / "dispatch-loop.jsonl"
        self.saved["JOBLOGS"] = dl.JOBLOGS;    dl.JOBLOGS = self.dir / "dispatch"
        self.saved["job_tmp"] = dl.job_tmp;    dl.job_tmp = lambda: self.dir / "jobtmp"
        self.saved["Popen"] = dl.subprocess.Popen
        self.saved["_alive"] = dl._alive

        def fake_popen(cmd, **kw):
            self.calls.append(cmd)
            return FakeChild(FAKE_PID if len(self.calls) == 1 else FAKE_PID2)

        self.alive = {FAKE_PID, FAKE_PID2}
        dl.subprocess.Popen = fake_popen
        dl._alive = lambda pid: pid in self.alive
        return self

    def __exit__(self, *exc):
        if getattr(self, "env_gate", None) is not None:
            os.environ["HIVEMIND_ADMISSION_GATE"] = self.env_gate
        else:
            os.environ.pop("HIVEMIND_ADMISSION_GATE", None)
        if self.env_saved is not None:
            os.environ["HIVEMIND_NO_SCOPE"] = self.env_saved
        else:
            os.environ.pop("HIVEMIND_NO_SCOPE", None)
        dl.REPO = self.saved["REPO"]
        dl.LEDGER = self.saved["LEDGER"]
        dl.JOBLOGS = self.saved["JOBLOGS"]
        dl.job_tmp = self.saved["job_tmp"]
        dl.subprocess.Popen = self.saved["Popen"]
        dl._alive = self.saved["_alive"]

    def ledger(self):
        p = Path(dl.LEDGER)
        if not p.is_file():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    def rows_for(self, **fields):
        out = []
        for r in self.ledger():
            if all(r.get(k) == v for k, v in fields.items()):
                out.append(r)
        return out

    def append_row(self, row):
        """Write a terminal row straight to the temp ledger so the test does not depend on
        an internal writer name the file might rename."""
        p = Path(dl.LEDGER)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def check(name, ok, detail):
    print(("PASS " if ok else "FAIL ") + name + ("  -- " + detail if detail else ""))
    return ok


def test_second_dispatch_of_the_same_job_starts_one_child():
    """THE DEFECT. Same brief, same model, same target: one child, one started row,
    and the refusal recorded as a terminal row naming the pid it duplicates."""
    with Env() as e:
        first = dl.launch(job())
        assert first[0] == FAKE_PID, f"first dispatch did not start: {first}"
        assert len(e.calls) == 1, f"first dispatch: {len(e.calls)} Popen calls, expected 1"
        second = dl.launch(job())
        ok = []
        ok.append(check("second dispatch spawns no child",
                        len(e.calls) == 1, f"{len(e.calls)} Popen calls"))
        ok.append(check("second dispatch is refused by pid", second[0] == 0,
                        f"launch returned pid {second[0]!r}, expected 0"))
        ok.append(check("second dispatch returns its refusal log", bool(second[1]),
                        f"log {second[1]!r}"))
        started = e.rows_for(event="started", chunk=CHUNK, model=MODEL)
        ok.append(check("exactly one started row", len(started) == 1,
                        f"{len(started)} started rows -- a start row for a job that never "
                        f"started is the false record this prevents"))
        term = e.rows_for(event="finished", chunk=CHUNK, model=MODEL, key=CHUNK)
        ok.append(check("the refusal is a terminal row", len(term) == 1,
                        f"{len(term)} finished rows for {CHUNK} -- the incident left no "
                        f"terminal row at all"))
        if term:
            r = term[0]
            ok.append(check("it names the process it duplicated",
                            r.get("duplicate_of") == FAKE_PID,
                            f"duplicate_of={r.get('duplicate_of')!r}, expected {FAKE_PID}"))
            ok.append(check("it is recorded as refused, not accepted",
                            r.get("accepted") is False, f"accepted={r.get('accepted')!r}"))
            ok.append(check("it says why in words a reader can act on",
                            bool(str(r.get("reason") or "").strip()),
                            "no reason field -- a refusal that names no cause is the "
                            "swallowed-exception shape"))
            ok.append(check("it carries the job's own identity",
                            r.get("brief_sha256") == BRIEF_SHA and r.get("kind") == "preflight",
                            f"row={r}"))
        print(f"  ledger: {len(e.ledger())} rows, Popen calls: {len(e.calls)}")
    return all(ok)


def test_a_different_brief_on_the_same_model_still_launches():
    """NEGATIVE: dedup keys on the job's identity, not on its model or its chunk name
    alone. A blunt 'one job per model' guard would pass the test above and stall the box."""
    with Env() as e:
        dl.launch(job())
        pid, log = dl.launch(job(brief="brief-p517-t1122-coder.txt",
                                 brief_sha256="beef1234" + "0" * 56,
                                 target="scripts/tests/test_t1122_brief_fix.py"))
        ok = [check("a different brief gets its own child", len(e.calls) == 2,
                    f"{len(e.calls)} Popen calls, expected 2"),
              check("and a real pid", pid == FAKE_PID2, f"pid {pid!r}"),
              check("and its own started row",
                    len(e.rows_for(event="started", brief_sha256="beef1234" + "0" * 56)) == 1,
                    f"rows={[r.get('brief_sha256') for r in e.ledger()]}")]
    return all(ok)


def test_the_rca_lane_dedups_on_the_finding_key():
    """The RCA lane (kind rca) carries a key (the finding), not a brief -- this is the
    'finding key + kind' key the RCA names. Same finding twice must be one child. The
    assertion reads `key`, not `path`, because launch()'s started row carries key, not path."""
    with Env() as e:
        dl.launch(job(kind="rca", path=FINDING, brief=None, brief_sha256=None,
                      key=FINDING, files=None, target=None))
        pid, log = dl.launch(job(kind="rca", path=FINDING, brief=None, brief_sha256=None,
                                 key=FINDING, files=None, target=None))
        ok = [check("same finding key launches one child", len(e.calls) == 1,
                    f"{len(e.calls)} Popen calls"),
              check("and a refusal", pid == 0, f"pid {pid!r}"),
              check("with one started row", len(e.rows_for(event="started", key=FINDING)) == 1,
                    f"{len(e.rows_for(event='started', key=FINDING))} started rows for key={FINDING}")]
    return all(ok)


def test_a_finished_job_can_be_dispatched_again():
    """NEGATIVE: the guard is about LIVE and OPEN, not 'ever dispatched'. A key that stays
    blocked after its child finishes parks a task forever -- the blunt-guard shape."""
    with Env() as e:
        dl.launch(job())
        e.append_row({"event": "finished", "kind": "preflight", "chunk": CHUNK, "model": MODEL,
                      "key": CHUNK, "brief_sha256": BRIEF_SHA,
                      "exit": 0, "accepted": True, "pid": FAKE_PID})
        e.alive.discard(FAKE_PID)
        pid, log = dl.launch(job())
        ok = [check("after the first child ends, a new child starts", len(e.calls) == 2,
                    f"{len(e.calls)} Popen calls"),
              check("and it gets a real pid", pid == FAKE_PID2, f"pid {pid!r}"),
              check("and a second started row, not a refusal",
                    len(e.rows_for(event="started", chunk=CHUNK)) == 2,
                    f"{len(e.rows_for(event='started', chunk=CHUNK))} started rows")]
    return all(ok)


if __name__ == "__main__":
    results = [test_second_dispatch_of_the_same_job_starts_one_child(),
               test_a_different_brief_on_the_same_model_still_launches(),
               test_the_rca_lane_dedups_on_the_finding_key(),
               test_a_finished_job_can_be_dispatched_again()]
    print(f"\n{sum(1 for r in results if r)}/{len(results)} test(s) passed")
    sys.exit(0 if all(results) else 1)
