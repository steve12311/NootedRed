"""VPF 基线冻结与逐项计划对照回归。"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/vpf'))
import compare_plan
import plan_dump
import schema

BASELINE = ROOT / 'tools/vpf/fixtures/baseline.json'
FULL = ROOT / 'build/vpf/baseline-plans.json'


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BASELINE.exists():
            raise unittest.SkipTest(f'缺少基线文件；先运行 {ROOT / "tools/vpf/freeze_baseline.py"}')
        cls.baseline = json.loads(BASELINE.read_text())
        cls.current = plan_dump.dump_plans()

    def test_baseline_metadata(self):
        self.assertEqual(self.baseline['kind'], 'vpf.baseline')
        self.assertEqual(self.baseline['schemaVersion'], schema.SCHEMA_VERSION)
        self.assertEqual(len(self.baseline['commit']), 40)
        self.assertEqual(self.baseline['planCount'], len(self.current['plans']))

    def test_generated_headers_unchanged(self):
        for relative, digest in self.baseline['generatedHeaders'].items():
            self.assertEqual(_hash(ROOT / relative), digest, relative)

    def test_frozen_tools_unchanged(self):
        for relative, digest in self.baseline['tools'].items():
            self.assertEqual(_hash(ROOT / relative), digest, relative)

    def test_plans_match_frozen_digests(self):
        current = {compare_plan.plan_key(plan): plan for plan in self.current['plans']}
        frozen = {entry['key']: entry for entry in self.baseline['plans']}
        self.assertEqual(set(current), set(frozen))
        for key, entry in frozen.items():
            plan = current[key]
            self.assertEqual(schema.digest(plan), entry['digest'], key)
            self.assertEqual(plan['reads'], entry['reads'], key)
            self.assertEqual(len(plan['sites']), entry['sites'], key)
            self.assertEqual(len(plan['images']), entry['images'], key)

    def test_full_dump_matches_if_present(self):
        if not FULL.exists():
            self.skipTest('完整基线计划只在 freeze_baseline 时生成')
        full = json.loads(FULL.read_text())
        errors = compare_plan.compare(full, self.current)
        self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
