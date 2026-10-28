"""DVPF：显示语义审计、精确字节校验与通配/短签名负样本。"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vpf/display-semantics'
sys.path.insert(0, str(ROOT / 'tools/vpf'))

import display_semantics as semantics


class DisplaySemanticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sites = semantics.load_sites()
        cls.report = semantics.build_report(cls.sites)

    def test_report_is_exact_and_emits_nothing(self):
        self.assertEqual(self.report['siteCount'], 69)
        self.assertEqual(self.report['exactValidated'], 69)
        self.assertEqual(self.report['rejected'], [])
        self.assertEqual(self.report['emittedManifests'], 0)
        self.assertEqual(len(self.report['dequantizationCandidates']), 6)
        for candidate in self.report['dequantizationCandidates']:
            self.assertTrue(candidate['bounded'])
            self.assertTrue(candidate['requiresReview'])
            self.assertGreaterEqual(len(candidate['immediates']), 1)
        self.assertTrue(all(site['staticPayload'] is False for site in self.report['sites']
                            if site['size'] >= 8))

    def test_every_exact_replacement_validates(self):
        for site in self.sites:
            valid, reason = semantics.validate_candidate(
                site, {'feature': site.feature, 'deviceID': site.deviceID,
                       'original': site.original.hex(), 'replacement': site.replacement.hex()})
            self.assertTrue(valid, f'{site.key}: {reason}')

    def test_wildcard_short_and_truncated_are_rejected(self):
        site = next(site for site in self.sites if site.key.startswith('DisplaySRD_Darwin24_1638#1'))
        base = {'feature': site.feature, 'deviceID': site.deviceID,
                'original': site.original.hex(), 'replacement': site.replacement.hex()}
        for mutation in ({'mask': 'FF00'}, {'wildcard': True}, {'signature': base['original'][:8]},
                         {'signatureLength': 8}):
            candidate = dict(base, **mutation)
            valid, reason = semantics.validate_candidate(site, candidate)
            self.assertFalse(valid)
            self.assertEqual(reason, 'wildcard_or_short_signature')
        truncated = dict(base, replacement=base['replacement'][:-2])
        valid, reason = semantics.validate_candidate(site, truncated)
        self.assertFalse(valid)
        self.assertEqual(reason, 'length_mismatch')

    def test_bounded_immediate_candidate_is_accepted_but_not_emitted(self):
        site = next(site for site in self.sites if site.key.startswith('DisplaySRD_Darwin24_1638#1'))
        immediates = semantics.immediate_candidate(site)['immediates']
        replacement = bytearray(site.replacement)
        for item in immediates:
            replacement[item['offset']] ^= 0x01
        candidate = {'feature': site.feature, 'deviceID': site.deviceID,
                     'original': site.original.hex(), 'replacement': bytes(replacement).hex(),
                     'immediates': immediates}
        valid, reason = semantics.validate_candidate(site, candidate)
        self.assertTrue(valid, reason)
        self.assertEqual(reason, 'bounded_immediate')

    def test_unproven_byte_and_reference_changes_are_rejected(self):
        site = next(site for site in self.sites if site.key.startswith('DisplaySRD_Darwin24_1638#1'))
        immediate = {'offset': 0, 'size': 1}
        candidate = {'feature': site.feature, 'deviceID': site.deviceID,
                     'original': site.original.hex(), 'replacement': site.replacement.hex(),
                     'immediates': [immediate]}
        value = bytearray(site.replacement)
        references = semantics._reference_mask(site)
        allowed = set(range(immediate['offset'], immediate['offset'] + immediate['size']))
        index = next(index for index in range(site.size) if index not in references and index not in allowed)
        value[index] ^= 0x01
        candidate['replacement'] = bytes(value).hex()
        valid, reason = semantics.validate_candidate(site, candidate)
        self.assertFalse(valid)
        self.assertEqual(reason, 'unproven_byte_change')
        if site.references:
            candidate = {'feature': site.feature, 'deviceID': site.deviceID,
                         'original': site.original.hex(), 'replacement': site.replacement.hex(),
                         'immediates': [{'offset': site.references[0].offset, 'size': 1}]}
            valid, reason = semantics.validate_candidate(site, candidate)
            self.assertFalse(valid)
            self.assertEqual(reason, 'immediate_overlaps_reference')

    def test_report_is_deterministic(self):
        first = semantics.build_report(self.sites)
        second = semantics.build_report(self.sites)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_cli_reports_zero_manifests(self):
        WORK.mkdir(parents=True, exist_ok=True)
        json_path = WORK / 'report.json'
        markdown = WORK / 'report.md'
        result = subprocess.run([sys.executable, str(ROOT / 'tools/vpf/analyze_display_variants.py'),
                                 '--json', str(json_path), '--markdown', str(markdown),
                                 '--fail-on-rejected'], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(json_path.read_text())
        self.assertEqual(report['emittedManifests'], 0)
        self.assertTrue(markdown.read_text().startswith('# DVPF 显示语义变体报告'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
