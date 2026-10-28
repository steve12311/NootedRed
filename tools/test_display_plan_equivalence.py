"""DVPF 显示计划等价：OpenGL 页内计划、Metal 静态站点与旧 Resolver 输出逐项一致。"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/dvpf'))
sys.path.insert(0, str(ROOT / 'tools/vpf'))

import compare_display_plan
import display_data
import display_plan_dump
import display_schema as schema
import generate_manifest
import plan_dump


def _hex(value: str) -> bytes:
    return bytes.fromhex(value)


def _skip_refs(data: bytes, refs: list[dict], instruction_offset: int) -> bytes:
    result = bytearray(data)
    for ref in refs:
        index = ref['offset'] - instruction_offset
        if index < 0 or index + 4 > len(result):
            raise AssertionError('引用超出补丁范围')
        for offset in range(index, index + 4):
            result[offset] = 0
    return bytes(result)


class DisplayPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = display_plan_dump.dump_plans()
        cls.features = display_data.build_all()
        cls.index = {(feature['feature'], feature['osMask'], feature['deviceID']): feature
                     for feature in cls.features}

    def test_probe_passes(self):
        executable = display_plan_dump.build_probe()
        subprocess.run([str(executable)], check=True, timeout=120)

    def test_sanitized_probe_passes(self):
        executable = display_plan_dump.build_probe(sanitize=True)
        subprocess.run([str(executable)], check=True, timeout=120)

    def test_plan_schema_and_features(self):
        plans = self.document['plans']
        self.assertEqual(len(plans), 108)
        keys = set()
        for plan in plans:
            key = compare_display_plan.plan_key(plan)
            self.assertNotIn(key, keys)
            keys.add(key)
            self.assertIn(plan['mode'], ('exact', 'moved', 'patched'))
            self.assertIn(plan['state'], ('AllOriginal', 'AllPatched'))
            self.assertIn(plan['feature'], ('OpenGLHwInfo', 'OpenGLDualQuad'))
            self.assertTrue(plan['exact'] or plan['dynamic'])
            self.assertEqual(len(plan['original']), plan['size'] * 2)
            self.assertEqual(len(plan['replacement']), plan['size'] * 2)
            if plan['mode'] == 'moved':
                self.assertTrue(plan['dynamic'] and not plan['exact'])
            if plan['mode'] == 'patched':
                self.assertEqual(plan['state'], 'AllPatched')
            if plan['mode'] != 'patched':
                self.assertEqual(plan['state'], 'AllOriginal')

    def test_plans_match_manifest_bytes(self):
        for plan in self.document['plans']:
            manifest = self.index[(plan['feature'], schema.OS_BITS[plan['os']], plan['deviceID'])]
            site = manifest['sites'][0]
            self.assertEqual(int(plan['referenceOffset'], 16), site['offset'])
            self.assertEqual(plan['size'], site['size'])
            self.assertEqual(plan['function'], site['functionIndex'])
            self.assertEqual(plan['instructionOffset'], site['instructionOffset'])
            replacement = _hex(plan['replacement'])
            original = _hex(plan['original'])
            if plan['mode'] == 'moved':
                self.assertEqual(
                    _skip_refs(replacement, manifest['refs']['patched'], site['instructionOffset']),
                    _skip_refs(site['replacement'], manifest['refs']['patched'], site['instructionOffset']),
                )
                self.assertEqual(
                    _skip_refs(original, manifest['refs']['original'], site['instructionOffset']),
                    _skip_refs(site['original'], manifest['refs']['original'], site['instructionOffset']),
                )
            elif plan['mode'] == 'patched':
                self.assertEqual(replacement, site['replacement'])
                self.assertEqual(original, site['replacement'])
            else:
                self.assertEqual(replacement, site['replacement'])
                self.assertEqual(original, site['original'])

    def test_metal_manifest_matches_vpf_tables(self):
        vpf_features = {feature['key']: feature for feature in generate_manifest.build_all()}
        for feature in self.features:
            if feature['domain'] != 'MetalSharedCache' or not feature['sharedSites']:
                continue
            source = vpf_features.get(feature['key'])
            if source is None:
                # Darwin 23 的精确 profile 不在共享 VPF Manifest 中，由基线单独记录。
                continue
            self.assertEqual(len(feature['sharedSites']), len(source['patches']))
            for site, patch in zip(feature['sharedSites'], source['patches']):
                self.assertEqual(site['offset'], patch['referenceOffset'])
                self.assertEqual(site['size'], patch['size'])
                self.assertEqual(site['original'], patch['_original'])
                self.assertEqual(site['replacement'], patch['_patched'])

    def test_shared_cache_plans_exist(self):
        plans = plan_dump.dump_plans()['plans']
        keys = {(plan['feature'], plan['os'], plan['deviceID']) for plan in plans}
        for device in display_data.supported_devices():
            self.assertIn(('DisplaySRD', 'Darwin24', device), keys)
            self.assertIn(('DisplayComputeScratch', 'Darwin25', device), keys)
        for plan in plans:
            if plan['feature'] == 'DisplaySRD':
                self.assertEqual(len(plan['sites']), 4)
            if plan['feature'] == 'DisplayComputeScratch':
                self.assertEqual(len(plan['sites']), 4)

    def test_compare_identical(self):
        self.assertEqual(compare_display_plan.compare(self.document, copy.deepcopy(self.document)), [])

    def test_compare_detects_byte_change(self):
        changed = copy.deepcopy(self.document)
        site = changed['plans'][0]
        site['replacement'] = ('00' if site['replacement'][:2] != '00' else '11') + site['replacement'][2:]
        self.assertTrue(compare_display_plan.compare(self.document, changed))

    def test_compare_detects_missing_plan(self):
        changed = copy.deepcopy(self.document)
        changed['plans'].pop()
        errors = compare_display_plan.compare(self.document, changed)
        self.assertTrue(any('仅旧计划存在' in error for error in errors))

    def test_dump_is_deterministic(self):
        first = display_plan_dump.dump_plans()
        self.assertEqual(schema.canonical_json(first), schema.canonical_json(self.document))


if __name__ == '__main__':
    unittest.main()
