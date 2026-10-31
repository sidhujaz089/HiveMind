#!/usr/bin/env python3
"""t1442 -- a role/station/task-list change that never went through the role store is a finding.

RCA workspace/rca/f0f57939bfb3.md: a permanent station + task-list reassignment (2026-09-29, commit
dee6eaf5) was made as free-text chat turned into ad-hoc code edits, because nothing recorded role
assignment as data at change-time. The store is models/ROLES.json, and every save through
scripts/role_store.py appends a {n, at, who, what, before, after} row to
models/ROLES-CHANGELOG.jsonl. Nothing today reads that changelog against the store, so a hand edit
of ROLES.json -- the RCA's whole defect class -- leaves no trace: the store, SERVING.json and the
Flow chart can all be hand-made to agree, and the record of who/when/why is simply absent.

This suite pins the missing check. role_store.changelog_drift(store=..., log=...) reports a store
file whose content is NOT the content the changelog's last row produced, naming the store file, the
changelog, the sanctioned route (scripts/role_store.py) and the affected role key(s) -- and
role_store.check() surfaces it, so it is a check and not a function nobody calls.

Run: python3 scripts/tests/test_role_store_changelog.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import role_store  # noqa: E402

MAIN = "http://127.0.0.1:11434"          # the module's own MAIN_ENDPOINT
STATION2 = "http://127.0.0.1:11435"      # station 2 (rca f0f57939bfb3, section 1)


def role(name, model="swift", **extra):
    r = {"name": name, "kind": "model", "model": model,
         "tasks": [{"task": "scoping", "interrupt": False}]}
    r.update(extra)
    return r


def doc(*roles):
    return {"roles": dict(roles)}


class ChangelogDrift(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = self.root / "ROLES.json"
        self.log = self.root / "ROLES-CHANGELOG.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, store_obj, rows):
        # written with indent, as _write_json does: the comparison must be by parsed VALUE
        self.store.write_text(json.dumps(store_obj, indent=1) + "\n", encoding="utf-8")
        self.log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def row(self, n, after):
        return {"n": n, "at": "2026-10-18T00:00:00Z", "who": "test",
                "what": "test row", "before": None, "after": after}

    def test_a_store_the_last_changelog_row_describes_is_clean(self):
        # built twice by value, never by identity: a build that compares objects by id fails here
        self.write(doc(("manager", role("Manager"))),
                   [self.row(1, doc(("manager", role("Manager"))))])
        self.assertEqual(role_store.changelog_drift(store=self.store, log=self.log), [])

    def test_a_hand_edit_of_a_role_model_is_a_finding(self):
        self.write(doc(("manager", role("Manager", model="gemma4:26b"))),
                   [self.row(1, doc(("manager", role("Manager", model="swift"))))])
        probs = role_store.changelog_drift(store=self.store, log=self.log)
        self.assertEqual(len(probs), 1, probs)
        for needle in ("ROLES.json", "ROLES-CHANGELOG.jsonl", "scripts/role_store.py", "manager"):
            self.assertIn(needle, probs[0])

    def test_a_station_change_alone_is_a_finding(self):
        # the RCA's instance was a STATION re-point; a diff that reads only task lists misses it
        self.write(doc(("manager", role("Manager", endpoint=STATION2))),
                   [self.row(1, doc(("manager", role("Manager", endpoint=MAIN))))])
        probs = role_store.changelog_drift(store=self.store, log=self.log)
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("manager", probs[0])

    def test_a_task_list_change_alone_is_a_finding(self):
        # the other half of the RCA's instance: the task reassignment, nothing else moved
        self.write(doc(("manager", role("Manager", tasks=[{"task": "scoping", "interrupt": False}]))),
                   [self.row(1, doc(("manager", role("Manager",
                       tasks=[{"task": "session_review", "interrupt": False}]))))])
        probs = role_store.changelog_drift(store=self.store, log=self.log)
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("manager", probs[0])

    def test_a_role_removed_by_hand_is_a_finding(self):
        logged = doc(("manager", role("Manager")),
                     ("auditor", role("Auditor", model="qwen3.8:27b-mtp-q4_K_M-56k")))
        self.write(doc(("manager", role("Manager"))), [self.row(1, logged)])
        probs = role_store.changelog_drift(store=self.store, log=self.log)
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("auditor", probs[0])

    def test_the_last_row_wins_not_the_first(self):
        # the expectation is the state the LAST sanctioned save produced, not any historical row
        self.write(doc(("manager", role("Manager", model="gemma4:26b"))),
                   [self.row(1, doc(("manager", role("Manager", model="swift")))),
                    self.row(2, doc(("manager", role("Manager", model="gemma4:26b"))))])
        self.assertEqual(role_store.changelog_drift(store=self.store, log=self.log), [])

    def test_a_changelog_with_nothing_recorded_is_not_claimed_as_drift(self):
        # scripts/tests/test_skill_change.py repoints the module's STORE/LOG at a temp dir and
        # asserts check() returns [] -- a fresh store with no row is "nothing recorded", not drift
        self.write(doc(("manager", role("Manager"))), [])
        self.assertEqual(role_store.changelog_drift(store=self.store, log=self.log), [])

    def test_a_changelog_file_that_does_not_exist_is_not_claimed_as_drift(self):
        self.store.write_text(json.dumps(doc(("manager", role("Manager")), indent=1) + "\n",
                                          encoding="utf-8")
                              if False else json.dumps(doc(("manager", role("Manager")), indent=1) + "\n", encoding="utf-8"))
        self.assertEqual(role_store.changelog_drift(store=self.store,
                                                   log=self.root / "no-such-log.jsonl"), [])

    def test_a_changelog_that_cannot_be_parsed_at_all_is_said_not_swallowed(self):
        # entries() prints a NOTE and goes on; a drift check that answered "clean" to an
        # unparseable log would be a guard that reads as true while seeing nothing
        self.write(doc(("manager", role("Manager")), []) if False else doc(("manager", role("Manager"))), [])
        self.log.write_text("{not json\n", encoding="utf-8")
        probs = role_store.changelog_drift(store=self.store, log=self.log)
        self.assertTrue(probs, probs)
        self.assertIn("ROLES-CHANGELOG.jsonl", probs[0])

    def test_an_unreadable_store_is_said_not_swallowed(self):
        self.store.write_text("{not json", encoding="utf-8")
        self.log.write_text("", encoding="utf-8")
        probs = role_store.changelog_drift(store=self.store, log=self.log)
        self.assertTrue(probs, probs)
        self.assertIn("ROLES.json", probs[0])

    def test_a_live_call_returns_a_list_of_strings(self):
        # it runs in production through `role_store.py check`: it must raise nothing on the live store
        out = role_store.changelog_drift()
        self.assertIsInstance(out, list)
        for p in out:
            self.assertIsInstance(p, str)

    def test_check_surfaces_the_drift_check(self):
        # without this the fixture is a function nobody calls: omitting the wiring leaves every
        # other test green (the check-satisfied-by-dead-path class)
        sentinel = "DRIFT-SENTINEL-t1442"
        original = role_store.changelog_drift
        role_store.changelog_drift = lambda *a, **k: [sentinel]
        try:
            self.assertIn(sentinel, role_store.check())
        finally:
            role_store.changelog_drift = original

    def test_check_does_not_fire_the_drift_check_for_a_caller_that_passes_its_own_store(self):
        # check(d=<dict>) compares an in-memory store against the live changelog if wired naively:
        # that would red every suite that passes a synthetic store, for a reason that is not a defect
        sentinel = "DRIFT-SENTINEL-t1442"
        original = role_store.changelog_drift
        role_store.changelog_drift = lambda *a, **k: [sentinel]
        try:
            self.assertNotIn(sentinel, role_store.check(d=doc(("manager", role("Manager")))))
        finally:
            role_store.changelog_drift = original


if __name__ == "__main__":
    SUITE = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    sys.exit(0 if unittest.TextTestRunner(verbosity=2).run(SUITE).wasSuccessful() else 1)
