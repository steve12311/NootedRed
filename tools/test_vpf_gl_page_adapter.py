"""DVPF：旧 OpenGL 页路径与 VPF 页后端的逐字节/状态等价，以及生产接线检查。"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vpf/gl-page-adapter'
sys.path.insert(0, str(ROOT / 'tools/dvpf'))
sys.path.insert(0, str(ROOT / 'tools/vpf'))

import display_data
import generate_display_manifest as generate


def _compiler() -> list[str]:
    xcrun = subprocess.run(['xcrun', '--find', 'clang++'], capture_output=True, text=True, timeout=10)
    if xcrun.returncode == 0:
        return ['xcrun', 'clang++']
    return ['clang++']


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
        str(ROOT / 'tools/dvpf/gl_page_adapter_probe.cpp'), '-o', str(executable),
    ]
    if sanitize:
        command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    subprocess.run(command, check=True, timeout=120)
    return executable


class PageAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.features = display_data.build_all()
        cls.opengl = [feature for feature in cls.features if feature['domain'] == 'OpenGLValidatedPage']

    def test_probe_matches_old_path(self):
        result = subprocess.run([str(_build(sanitize=False))], check=True, capture_output=True, text=True, timeout=120)
        self.assertIn('PASS', result.stdout)

    def test_sanitized_probe_matches_old_path(self):
        result = subprocess.run([str(_build(sanitize=True))], check=True, capture_output=True, text=True, timeout=120)
        self.assertIn('PASS', result.stdout)

    def test_manifest_covers_every_supported_page(self):
        self.assertEqual(len(self.opengl), 36)
        groups = {}
        for feature in self.opengl:
            key = (feature['feature'], feature['deviceID'])
            groups.setdefault(key, []).append(feature['os'])
            self.assertEqual(len(feature['sites']), 1)
            self.assertTrue(feature['pageLocalFallback'])
            self.assertEqual(feature['evidence'], 'ExactKnownPage')
            site = feature['sites'][0]
            self.assertIn(site['functionIndex'], (0, 1))
            self.assertGreater(site['size'], 0)
            self.assertLessEqual(site['size'], feature['maxPatchedBytes'])
        for feature in ('OpenGLHwInfo', 'OpenGLDualQuad'):
            self.assertEqual(len(groups[(feature, 0x15D8)]), 3)
            self.assertEqual(len(groups[(feature, 0x15E7)]), 3)

    def test_generated_manifest_is_current(self):
        self.assertEqual(generate.OUTPUT.read_text(), generate.emit_cpp(display_data.build_all()))

    def test_production_uses_backend(self):
        source = (ROOT / 'NootedRed/UserOpenGL.cpp').read_text()
        backend = (ROOT / 'NootedRed/VPF/ValidatedPageBackend.hpp').read_text()
        self.assertIn('VPF::inspectValidatedPage', source)
        self.assertIn('VPF::applyValidatedPage', source)
        self.assertIn('NRedOpenGLVPFPlanVerified', source)
        self.assertIn('NRedOpenGLVPFManifestMask', source)
        self.assertNotIn('memcpy(const_cast<UInt8*>(bytes) + plan.offset', source)
        self.assertIn('UserOpenGLResolver::resolvePage', backend)
        self.assertIn('adaptOpenGLPage', backend)
        for forbidden in ('_mmap', 'vm_allocate', 'UserPatchMapGate', 'thread_call_enter', 'IOSleep'):
            self.assertNotIn(forbidden, backend)
        self.assertIn('const_cast<UInt8*>(bytes)', source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
