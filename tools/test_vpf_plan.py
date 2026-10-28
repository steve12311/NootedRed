"""VPF VerifiedPatchPlan 适配、计划序列化与比较回归。"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/vpf'))
import compare_plan
import plan_dump
import schema


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = plan_dump.dump_plans()

    def test_probe_passes(self):
        executable = plan_dump.build_probe()
        subprocess.run([str(executable)], check=True, timeout=120)

    def test_sanitized_probe_passes(self):
        executable = plan_dump.build_probe(sanitize=True)
        subprocess.run([str(executable)], check=True, timeout=120)

    def test_plan_schema_and_bounds(self):
        plans = self.document['plans']
        self.assertEqual(len(plans), 22)
        keys = set()
        for plan in plans:
            key = compare_plan.plan_key(plan)
            self.assertNotIn(key, keys)
            keys.add(key)
            self.assertIn(plan['state'], schema.PLAN_STATES)
            self.assertNotEqual(plan['state'], 'Unknown')
            self.assertGreater(plan['reads'], 0)
            self.assertTrue(plan['sites'])
            base = int(plan['base'], 16)
            for site in plan['sites']:
                self.assertEqual(len(site['original']), site['size'] * 2)
                self.assertEqual(len(site['replacement']), site['size'] * 2)
                self.assertEqual(int(site['address'], 16), base + int(site['offset'], 16))
                self.assertLessEqual(site['size'], 512)
            offsets = [int(site['offset'], 16) for site in plan['sites']]
            self.assertEqual(len(offsets), len(set(offsets)))
            for image in plan['images']:
                self.assertTrue(image['path'])
                self.assertNotEqual(int(image['address'], 16), 0)

    def test_compare_identical(self):
        self.assertEqual(compare_plan.compare(self.document, copy.deepcopy(self.document)), [])

    def test_compare_detects_byte_change(self):
        changed = copy.deepcopy(self.document)
        site = changed['plans'][0]['sites'][0]
        site['replacement'] = ('00' if site['replacement'][:2] != '00' else '11') + site['replacement'][2:]
        errors = compare_plan.compare(self.document, changed)
        self.assertTrue(errors)
        self.assertTrue(any('replacement' in error for error in errors))

    def test_compare_detects_missing_plan(self):
        changed = copy.deepcopy(self.document)
        changed['plans'].pop()
        errors = compare_plan.compare(self.document, changed)
        self.assertTrue(any('仅旧计划存在' in error for error in errors))

    def test_compare_ignore_reads(self):
        changed = copy.deepcopy(self.document)
        changed['plans'][0]['reads'] += 1
        self.assertTrue(compare_plan.compare(self.document, changed))
        self.assertEqual(compare_plan.compare(self.document, changed, ignore_reads=True), [])


if __name__ == '__main__':
    unittest.main()
