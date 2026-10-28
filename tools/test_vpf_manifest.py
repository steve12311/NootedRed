"""VPF Manifest 生成、预算、字节证据与生成头编译回归。"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/vpf'))
import cppdata
import generate_manifest
import schema


class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.features = generate_manifest.build_all()

    def test_generated_header_is_current(self):
        text = generate_manifest.emit_cpp(generate_manifest.build_all())
        self.assertTrue(generate_manifest.OUTPUT.exists())
        self.assertEqual(generate_manifest.OUTPUT.read_text(), text)

    def test_generation_is_deterministic(self):
        first = generate_manifest.emit_cpp(generate_manifest.build_all())
        second = generate_manifest.emit_cpp(generate_manifest.build_all())
        self.assertEqual(first, second)

    def test_feature_keys_and_counts(self):
        keys = [feature['key'] for feature in self.features]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertGreater(len(self.features), 0)
        self.assertEqual(
            {feature['feature'] for feature in self.features},
            {'Vcn2Decode', 'Vcn2SafetyGuard', 'DisplaySRD', 'DisplayLegacyBlend', 'DisplayComputeScratch',
             'DisplayPipelineConstructor', 'DisplaySubmit'},
        )
        for feature in self.features:
            generate_manifest.validate(feature)

    def test_patch_bytes_match_target_function(self):
        for feature in self.features:
            for index, patch in enumerate(feature['patches']):
                if patch['function'] is None:
                    continue
                function = feature['functions'][patch['function']]
                offset = patch['instructionOffset']
                self.assertEqual(
                    patch['_original'],
                    function['_original'][offset:offset + patch['size']],
                    f"{feature['key']} 补丁 {index} 不属于目标函数",
                )

    def test_manifest_json_schema(self):
        document = {
            'schemaVersion': schema.SCHEMA_VERSION,
            'kind': 'vpf.manifest',
            'features': [generate_manifest._strip(feature) for feature in self.features],
        }
        text = schema.canonical_json(document)
        parsed = json.loads(text)
        self.assertEqual(parsed['schemaVersion'], 1)
        for feature in parsed['features']:
            self.assertIn(feature['feature'], schema.FEATURE_IDS)
            self.assertIn(feature['evidence'], schema.EVIDENCE_KINDS)
            for patch in feature['patches']:
                self.assertEqual(len(patch['original']), patch['size'] * 2)
                self.assertEqual(len(patch['patched']), patch['size'] * 2)

    def test_parser_ignores_using_namespace(self):
        (ROOT / 'build').mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as directory:
            path = Path(directory) / 'data.hpp'
            path.write_text(
                'using namespace Foo;\n'
                'namespace Bar {\n'
                'inline constexpr UInt8 A[] = {1, 2};\n'
                '}\n'
            )
            header = cppdata.parse(path)
            self.assertEqual(header.byte_array('Bar', 'A'), b'\x01\x02')

    def test_negative_mutations_are_rejected(self):
        original = next(feature for feature in self.features if feature['key'] == 'Vcn2Decode_Darwin25_1638')
        mutations = []

        bad = copy.deepcopy(original)
        bad['patches'][0]['size'] = generate_manifest.MAX_PATCH_SIZE + 1
        mutations.append(bad)

        bad = copy.deepcopy(original)
        bad['patches'][0]['_original'] = bytes([bad['patches'][0]['_original'][0] ^ 0xFF]) + bad['patches'][0]['_original'][1:]
        mutations.append(bad)

        bad = copy.deepcopy(original)
        bad['functions'][0]['originalRefs'][0]['image'] = 999
        mutations.append(bad)

        bad = copy.deepcopy(original)
        bad['patches'][0]['instructionOffset'] = 1 << 30
        mutations.append(bad)

        bad = copy.deepcopy(original)
        bad['patches'][0]['_patched'] = b''
        mutations.append(bad)

        bad = copy.deepcopy(original)
        bad['feature'] = 'NotAFeature'
        mutations.append(bad)

        for bad in mutations:
            with self.assertRaises(ValueError):
                generate_manifest.validate(bad)

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
                '#include <VPF/PatchManifestData.hpp>\n'
                'static_assert(VPF::ManifestData::FeatureCount > 0);\n'
                'int main() { return VPF::ManifestData::find(VPF::FeatureId::Vcn2Decode, VPF::OsDarwin25, 0x1638) ? 0 : 1; }\n'
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
