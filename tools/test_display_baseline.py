"""DVPF 显示基线冻结、字节摘要与审计报告一致性回归。"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/dvpf'))
sys.path.insert(0, str(ROOT / 'tools/vpf'))

import display_data
import display_schema as schema
import freeze_display_baseline as freeze


class DisplayBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not freeze.BASELINE.exists() or not freeze.DOCUMENT.exists():
            raise unittest.SkipTest('缺少 DVPF 基线；先运行 tools/dvpf/freeze_display_baseline.py --write')
        cls.baseline = json.loads(freeze.BASELINE.read_text())
        cls.features = display_data.build_all()

    def test_baseline_is_current(self):
        self.assertEqual(freeze.BASELINE.read_text(), schema.canonical_json(freeze.build_baseline()))
        self.assertEqual(freeze.DOCUMENT.read_text(), freeze.render_markdown(self.baseline))

    def test_metadata(self):
        self.assertEqual(self.baseline['kind'], 'dvpf.baseline')
        self.assertEqual(self.baseline['schemaVersion'], schema.SCHEMA_VERSION)
        self.assertEqual(self.baseline['baselineCommit'], freeze.BASELINE_COMMIT)
        self.assertEqual(self.baseline['designBaselineCommit'], freeze.DESIGN_COMMIT)
        self.assertEqual(len(self.baseline['features']), 54)
        self.assertEqual(sum(len(feature['sites']) + len(feature['sharedSites'])
                             for feature in self.baseline['features']), 70)

    def test_sources_unchanged(self):
        for relative, digest in self.baseline['sources'].items():
            self.assertEqual(schema.file_digest(ROOT / relative), digest, relative)

    def test_feature_and_site_digests(self):
        keys = set()
        for index, feature in enumerate(self.features):
            key = freeze._feature_key(feature)
            self.assertNotIn(key, keys)
            keys.add(key)
            self.assertEqual(self.baseline['featureDigests'][key], schema.digest(display_data.strip(feature)))
            hashes = self.baseline['siteHashes'][feature['key']]
            sites = feature['sites'] + feature['sharedSites']
            self.assertEqual(len(hashes), len(sites))
            for site, entry in zip(sites, hashes):
                self.assertEqual(entry['originalSha256'], schema.format_digest(site['original']))
                self.assertEqual(entry['replacementSha256'], schema.format_digest(site['replacement']))
                self.assertEqual(entry['offset'], site['offset'])
                self.assertEqual(entry['size'], site['size'])

    def test_boot_arguments_and_properties(self):
        self.assertEqual(self.baseline['bootArgs']['enabled'], [schema.DISPLAY_FIX_ARG])
        self.assertEqual(tuple(self.baseline['bootArgs']['inactive']), schema.INACTIVE_ARGS)
        self.assertIn('NRedDisplayFixVersion', self.baseline['properties'])
        self.assertIn('NRedOpenGLSEApplied', self.baseline['properties'])
        self.assertIn('NRedSubmitGuardWindowServerApplied', self.baseline['properties'])
        self.assertEqual(self.baseline['deviceWhitelist'], display_data.supported_devices())

    def test_hardware_validation_boundary(self):
        hardware = self.baseline['hardware']
        self.assertIn('尚未完成', hardware['status'])
        self.assertNotIn('bootUUID', hardware)
        self.assertNotIn('kextUUID', hardware)
        self.assertNotIn('source', hardware)
        self.assertIn(hardware['status'], freeze.DOCUMENT.read_text())

    def test_byte_mutation_changes_digest(self):
        feature = self.features[0]
        changed = copy.deepcopy(feature)
        site = changed['sites'][0]
        site['replacement'] = bytes([site['replacement'][0] ^ 0xFF]) + site['replacement'][1:]
        self.assertNotEqual(schema.digest(display_data.strip(feature)), schema.digest(display_data.strip(changed)))


if __name__ == '__main__':
    unittest.main()
