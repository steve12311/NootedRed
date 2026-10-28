"""DVPF 显示清单生成、结构、预算与生成头编译回归。"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/dvpf'))
sys.path.insert(0, str(ROOT / 'tools/vpf'))

import display_data
import display_schema as schema
import generate_display_manifest as generate


class DisplayManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.features = display_data.build_all()

    def test_generated_header_is_current(self):
        self.assertTrue(generate.OUTPUT.exists())
        self.assertEqual(generate.OUTPUT.read_text(), generate.emit_cpp(display_data.build_all()))

    def test_generation_is_deterministic(self):
        self.assertEqual(generate.emit_cpp(display_data.build_all()), generate.emit_cpp(display_data.build_all()))

    def test_feature_and_site_counts(self):
        self.assertEqual(len(self.features), 55)
        self.assertEqual(len([f for f in self.features if f['domain'] == 'OpenGLValidatedPage']), 36)
        self.assertEqual(len([f for f in self.features if f['domain'] == 'MetalSharedCache']), 15)
        self.assertEqual(len([f for f in self.features if f['feature'] == 'MetalSubmitGuard']), 3)
        self.assertEqual(len([f for f in self.features if f['domain'] in
                              ('KernelGraphics', 'KernelFramebuffer')]), 3)
        self.assertEqual(len([f for f in self.features if f['feature'] == 'HDRResearch']), 1)
        self.assertEqual(sum(len(f['sites']) + len(f['sharedSites']) for f in self.features), 70)

    def test_manifest_json_schema(self):
        document = {
            'schemaVersion': schema.SCHEMA_VERSION,
            'kind': 'dvpf.manifest',
            'features': [display_data.strip(feature) for feature in self.features],
        }
        parsed = json.loads(schema.canonical_json(document))
        self.assertEqual(parsed['schemaVersion'], schema.SCHEMA_VERSION)
        for feature in parsed['features']:
            self.assertIn(feature['feature'], schema.FEATURES)
            self.assertIn(feature['domain'], schema.DOMAINS)
            self.assertIn(feature['group'], schema.GROUPS)
            self.assertIn(feature['evidence'], schema.EVIDENCE)
            for site in feature['sites'] + feature['sharedSites']:
                self.assertEqual(len(site['original']), site['size'] * 2)
                self.assertEqual(len(site['replacement']), site['size'] * 2)

    def test_negative_mutations_are_rejected(self):
        opengl = next(feature for feature in self.features if feature['feature'] == 'OpenGLHwInfo')
        metal = next(feature for feature in self.features if feature['feature'] == 'MetalSrdShared')
        kernel = next(feature for feature in self.features if feature['feature'] == 'KernelGFX9Capabilities')
        mutations = []

        bad = copy.deepcopy(opengl)
        bad['feature'] = 'NotAFeature'
        mutations.append(bad)

        bad = copy.deepcopy(opengl)
        bad['group'] = 'NotAGroup'
        mutations.append(bad)

        bad = copy.deepcopy(opengl)
        bad['deviceID'] = 0
        mutations.append(bad)

        bad = copy.deepcopy(opengl)
        bad['sites'][0]['size'] = 4097
        mutations.append(bad)

        bad = copy.deepcopy(opengl)
        bad['sites'][0]['dynamicOffset'] = bad['sites'][0]['size']
        bad['sites'][0]['dynamicSize'] = 1
        mutations.append(bad)

        bad = copy.deepcopy(opengl)
        bad['sites'][0]['offset'] = 0xFF0
        mutations.append(bad)

        bad = copy.deepcopy(opengl)
        bad['sites'][0]['original'] = b''
        mutations.append(bad)

        bad = copy.deepcopy(metal)
        bad['sharedFeature'] = None
        mutations.append(bad)

        bad = copy.deepcopy(kernel)
        bad['installable'] = True
        mutations.append(bad)

        bad = copy.deepcopy(opengl)
        bad['osMask'] = 0
        mutations.append(bad)

        bad = copy.deepcopy(opengl)
        bad['requiredGroupMask'] = 0x100
        mutations.append(bad)

        for bad in mutations:
            with self.assertRaises(ValueError):
                display_data.validate([bad])

        with self.assertRaises(ValueError):
            display_data.validate([copy.deepcopy(opengl), copy.deepcopy(opengl)])

    def test_generated_header_compiles(self):
        compiler = 'clang++'
        try:
            subprocess.run(['xcrun', '--find', 'clang++'], check=True, capture_output=True, timeout=10)
            compiler = 'xcrun'
        except (OSError, subprocess.SubprocessError):
            pass
        (ROOT / 'build').mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as directory:
            work = Path(directory)
            stub_root = work / 'stubs'
            stub = stub_root / 'IOKit'
            stub.mkdir(parents=True)
            (stub / 'IOTypes.h').write_text(
                '#pragma once\n#include <cstdint>\n'
                'using UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n'
            )
            source = work / 'test.cpp'
            source.write_text(
                '#include <VPF/DisplayManifestData.hpp>\n'
                '#include <VPF/DisplayPlanAdapters.hpp>\n'
                'int main() {\n'
                '    const auto* entry = VPF::DisplayManifestData::findOpenGL(25, 0x1638, 0);\n'
                '    if (!entry || entry->pageSiteCount != 1) { return 1; }\n'
                '    if (VPF::DisplayManifestData::FeatureCount != 55) { return 2; }\n'
                '    return 0;\n'
                '}\n'
            )
            executable = work / 'test'
            command = ([compiler, 'clang++'] if compiler == 'xcrun' else [compiler])
            subprocess.run(
                command + ['-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror', '-I' + str(stub_root),
                           '-I' + str(ROOT / 'NootedRed'), str(source), '-o', str(executable)],
                check=True,
                timeout=60,
            )
            subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == '__main__':
    unittest.main()
