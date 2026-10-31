#!/usr/bin/env python3
"""t301 oracle: the declared production working set must fit the box, mechanically.

The 2026-09-14 observation (t301) was that three wanted models did not fit and
Ollama's LRU thrashed them. The staffing since (SERVING.json production_lanes)
is the standing order 'two models co-resident' (Jas, 2026-09-17). Nothing
enforces that order: the fit claim is prose arithmetic in handovers
(out/t301.md:24-28). This test pins the check:

  * fits() is pure arithmetic: total <= box - headroom
  * the working set is exactly the production_lanes roles' tags, deduped by tag
  * a production tag with no declared size is a LOUD failure (exit 2), not a default

Fakes only: temp-dir repos, no live Ollama, no /proc reads (box passed explicitly).
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check_working_set as cws  # noqa: E402


def fake_repo(root, lanes, roles, registry):
    models = root / "projects" / "local-inference" / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "SERVING.json").write_text(
        json.dumps({"roles": roles, "production_lanes": lanes}))
    (models / "REGISTRY.json").write_text(json.dumps(registry))
    return root


class Arithmetic(unittest.TestCase):
    def test_passes_when_working_set_leaves_headroom(self):
        self.assertTrue(cws.fits(20.0, box_gb=121.0, headroom_gb=40.0))

    def test_fails_when_working_set_exceeds_headroom(self):
        # the 2026-09-14 scenario: 53 + 17 + 18 = 88 GB on a 121 GB box
        self.assertFalse(cws.fits(88.0, box_gb=121.0, headroom_gb=40.0))

    def test_exactly_at_the_line_passes(self):
        self.assertTrue(cws.fits(81.0, box_gb=121.0, headroom_gb=40.0))


class WorkingSet(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(shutil.rmtree, self._tmp.name, ignore_errors=True)
        self.root = Path(self._tmp.name)

    def test_counts_production_lanes_only_and_dedupes_tags(self):
        # manager and auditor share a tag today: counted once. 'chat' is a role
        # but not a production lane: not in the working set.
        roles = {"manager": {"tag": "q"}, "auditor": {"tag": "q"},
                 "coder": {"tag": "g"}, "chat": {"tag": "x"}}
        fake_repo(self.root, ["manager", "auditor", "coder"], roles,
                  {"q": {"weight_gb": 16.7}, "g": {"weight_gb": 3.3},
                   "x": {"weight_gb": 50.0}})
        tags, missing = cws.working_set(self.root)
        self.assertEqual(tags, {"q": 16.7, "g": 3.3})
        self.assertEqual(missing, [])
        self.assertNotIn("x", tags)

    def test_registry_wrapped_under_models_key(self):
        fake_repo(self.root, ["manager"], {"manager": {"tag": "q"}},
                  {"models": {"q": {"weight_gb": 16.7}}})
        tags, missing = cws.working_set(self.root)
        self.assertEqual(tags, {"q": 16.7})
        self.assertEqual(missing, [])

    def test_undeclared_size_is_loud_not_silent(self):
        fake_repo(self.root, ["manager"], {"manager": {"tag": "q"}},
                  {"q": {"digest": "sha256:deadbeef"}})
        tags, missing = cws.working_set(self.root)
        self.assertEqual(missing, ["q"])
        self.assertEqual(cws.check(self.root, box_gb=121.0), 2)

    def test_check_exit_codes(self):
        roles = {"manager": {"tag": "q"}, "coder": {"tag": "g"}}
        fake_repo(self.root, ["manager", "coder"], roles,
                  {"q": {"weight_gb": 16.7}, "g": {"weight_gb": 3.3}})
        self.assertEqual(cws.check(self.root, box_gb=121.0), 0)   # 20 <= 121-40
        self.assertEqual(cws.check(self.root, box_gb=50.0), 1)    # 20 > 50-40

    def test_a_footprint_that_includes_its_kv_needs_only_the_os_reserve(self):
        # Swift on vLLM: 95 GB measured with its fixed KV pool allocated (2026-10-06). It served
        # with 25 GB free; 95 + 40 > 122 would have refused it anyway.
        roles = {"manager": {"tag": "s"}}
        fake_repo(self.root, ["manager"], roles, {"s": {"weight_gb": 95.0, "includes_kv": True}})
        self.assertEqual(cws.check(self.root, box_gb=122.0), 0)    # 95 <= 122-8
        self.assertEqual(cws.check(self.root, box_gb=100.0), 1)    # 95 > 100-8: still refuses

    def test_a_mixed_set_keeps_the_full_headroom(self):
        # Control: one Ollama-sized tag in the set and the 40 GB comes back.
        roles = {"manager": {"tag": "s"}, "coder": {"tag": "g"}}
        fake_repo(self.root, ["manager", "coder"], roles,
                  {"s": {"weight_gb": 75.0, "includes_kv": True}, "g": {"weight_gb": 10.0}})
        self.assertEqual(cws.check(self.root, box_gb=122.0), 1)    # 85 > 122-40


if __name__ == "__main__":
    unittest.main()
