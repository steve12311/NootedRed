"""DVPF：参考页迁移、动态完整块匹配与负样本；不要求实机启动。"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vpf/gl-dynamic-variants'
_SYSTEM_ROOT = os.environ.get('NRED_GL_SYSTEM_ROOT')
SEQUOIA = Path(_SYSTEM_ROOT) if _SYSTEM_ROOT else None
sys.path.insert(0, str(ROOT / 'tools/vpf'))
sys.path.insert(0, str(ROOT / 'tools/dvpf'))

import gl_evidence


def _compiler() -> list[str]:
    result = subprocess.run(['xcrun', '--find', 'clang++'], capture_output=True, text=True, timeout=10)
    return ['xcrun', 'clang++'] if result.returncode == 0 else ['clang++']


def _build(*, sanitize: bool) -> Path:
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text(
        '#pragma once\n#include <cstdint>\n'
        'using UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n'
    )
    executable = WORK / ('probe-sanitize' if sanitize else 'probe')
    command = _compiler() + [
        '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror',
        '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
        str(ROOT / 'tools/vpf/gl_dynamic_variants_probe.cpp'), '-o', str(executable),
    ]
    if sanitize:
        command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    subprocess.run(command, check=True, timeout=120)
    return executable


class DynamicVariantTests(unittest.TestCase):
    def test_probe_passes(self):
        result = subprocess.run([str(_build(sanitize=False))], check=True, capture_output=True, text=True, timeout=120)
        self.assertIn('PASS', result.stdout)

    def test_sanitized_probe_passes(self):
        result = subprocess.run([str(_build(sanitize=True))], check=True, capture_output=True, text=True, timeout=120)
        self.assertIn('PASS', result.stdout)

    def test_evidence_table_is_complete(self):
        evidence = gl_evidence.build_fixture_evidence()
        self.assertEqual(len(evidence), 36)
        keys = {(item['major'], item['deviceID'], item['kind']) for item in evidence}
        self.assertEqual(len(keys), 36)
        for item in evidence:
            self.assertGreater(item['blockSize'], 0)
            self.assertGreater(item['pageDigest'], 0)
            self.assertGreater(item['originalDigest'], 0)
            self.assertGreater(item['patchedDigest'], 0)
            self.assertNotEqual(item['originalDigest'], item['patchedDigest'])

    @unittest.skipUnless(SEQUOIA is not None, '需要设置 NRED_GL_SYSTEM_ROOT')
    def test_real_evidence_is_reference_compatible(self):
        self.assertTrue(SEQUOIA.is_dir(), 'NRED_GL_SYSTEM_ROOT 不是有效目录')
        result = gl_evidence.inspect_system_root(SEQUOIA)
        self.assertTrue(result['allReferenceBlocksFound'])
        variants = gl_evidence.variants_document(result)
        self.assertEqual(variants['emittedManifests'], 0)
        for entry in variants['variants']:
            self.assertEqual(entry['status'], 'matched')
            self.assertTrue(entry['runtimeCompatible'])
            self.assertTrue(entry['referencePageMatched'])
        self.assertEqual(len(variants['oldNewEquivalence']), 12)
        self.assertTrue(all(item['sameSize'] for item in variants['oldNewEquivalence']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
