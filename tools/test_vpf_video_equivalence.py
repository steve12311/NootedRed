"""既有 Resolver 与当前生产计划的视频等价性。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vpf/video-equivalence'
sys.path.insert(0, str(ROOT / 'tools/vpf'))
import plan_dump
import schema

LEGACY_REVISION = '6fb1aa6'


def _compiler() -> list[str]:
    if shutil.which('xcrun'):
        return ['xcrun', 'clang++']
    if shutil.which('clang++'):
        return ['clang++']
    raise RuntimeError('需要 clang++ 或 xcrun clang++')


def _key(plan: dict) -> tuple[str, str, int]:
    return plan['feature'], plan['os'], plan['deviceID']


class EquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        old = WORK / 'legacy-headers'
        old.mkdir(exist_ok=True)
        for name in ('UserVideoDecodeResolver.hpp',):
            try:
                content = subprocess.check_output(
                    ['git', 'show', f'{LEGACY_REVISION}:NootedRed/{name}'], cwd=ROOT, text=True, timeout=15
                )
            except subprocess.CalledProcessError:
                raise unittest.SkipTest(f'缺少 {LEGACY_REVISION} 的旧 Resolver 头')
            (old / name).write_text(content)
        stub = WORK / 'stubs/IOKit/IOTypes.h'
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(
            '#pragma once\n#include <cstdint>\n'
            'using UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n'
        )
        executable = WORK / 'video_legacy_probe'
        subprocess.run(
            _compiler() + [
                '-std=c++23', '-O2', '-I' + str(old), '-I' + str(WORK / 'stubs'),
                '-I' + str(ROOT / 'NootedRed'), '-I' + str(ROOT / 'tools/fixtures'),
                str(ROOT / 'tools/vpf/video_legacy_probe.cpp'), '-o', str(executable),
            ],
            check=True, timeout=120,
        )
        cls.old = json.loads(subprocess.check_output([str(executable)], text=True, timeout=60))
        cls.current = plan_dump.dump_plans()
        cls.old_by_key = {_key(plan): plan for plan in cls.old['plans']}
        cls.current_by_key = {_key(plan): plan for plan in cls.current['plans'] if plan['feature'] == 'Vcn2Decode'}

    def test_plan_set_is_complete(self):
        self.assertEqual(self.old['kind'], 'vpf.video-legacy')
        self.assertEqual(len(self.old_by_key), 8)
        self.assertEqual(set(self.old_by_key), set(self.current_by_key))
        self.assertEqual(len(self.current_by_key), 8)
        for plan in self.current_by_key.values():
            self.assertEqual(plan['state'], 'AllOriginal')
            self.assertEqual(len(plan['images']), 3)

    def test_old_and_new_patch_bytes_match(self):
        for key, old in self.old_by_key.items():
            current = self.current_by_key[key]
            self.assertEqual(old['base'], current['base'], key)
            self.assertEqual(old['reads'], current['reads'], key)
            self.assertEqual(len(old['sites']), len(current['sites']), key)
            for index, (left, right) in enumerate(zip(old['sites'], current['sites'])):
                self.assertEqual(left['offset'], right['offset'], (key, index))
                self.assertEqual(left['size'], right['size'], (key, index))
                self.assertEqual(int(right['address'], 16), int(current['base'], 16) + int(right['offset'], 16),
                                 (key, index))
                self.assertEqual(left['original'].lower(), right['original'].lower(), (key, index, 'original'))
                self.assertEqual(left['replacement'].lower(), right['replacement'].lower(), (key, index, 'replacement'))

    def test_frozen_baseline_digests_match(self):
        baseline = json.loads((ROOT / 'tools/vpf/fixtures/baseline.json').read_text())
        frozen = {entry['key']: entry for entry in baseline['plans']}
        for key, plan in self.current_by_key.items():
            name = f"{plan['feature']}/{plan['os']}/{plan['deviceID']:04X}/{plan['state']}"
            self.assertIn(name, frozen)
            self.assertEqual(schema.digest(plan), frozen[name]['digest'], name)

    def test_only_metadata_difference_is_image_bounds(self):
        # 旧 Resolver 不暴露 ImageBounds；当前计划包含映像元数据，站点字节与读取数不变。
        for key, old in self.old_by_key.items():
            current = self.current_by_key[key]
            self.assertNotIn('images', old)
            self.assertEqual(old['reads'], current['reads'], key)
            self.assertEqual(
                [(site['offset'], site['size'], site['original'], site['replacement']) for site in old['sites']],
                [(site['offset'], site['size'], site['original'], site['replacement']) for site in current['sites']],
                key,
            )


if __name__ == '__main__':
    unittest.main(verbosity=2)
