"""VPF 生命周期短回归：真实 Ledger/init/销毁回调，不运行共享缓存桥接矩阵。"""
from __future__ import annotations

import shutil
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vpf/lifecycle'


def _compiler() -> list[str]:
    if shutil.which('xcrun'):
        return ['xcrun', 'clang++']
    if shutil.which('clang++'):
        return ['clang++']
    raise RuntimeError('需要 clang++ 或 xcrun clang++')


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stub = WORK / 'stubs/IOKit/IOTypes.h'
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(
            '#pragma once\n#include <cstdint>\n'
            'using UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n'
        )

    def _build(self, sanitize: bool) -> Path:
        executable = WORK / ('lifecycle_probe-sanitize' if sanitize else 'lifecycle_probe')
        command = _compiler() + [
            '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror',
            '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
            str(ROOT / 'tools/vpf/lifecycle_probe.cpp'), '-o', str(executable),
        ]
        if sanitize:
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        subprocess.run(command, check=True, timeout=60)
        return executable

    def test_lifecycle_probe(self):
        subprocess.run([str(self._build(False))], check=True, timeout=60)

    def test_lifecycle_probe_sanitized(self):
        subprocess.run([str(self._build(True))], check=True, timeout=60)

    def test_production_lifecycle_bridge(self):
        source = (ROOT / 'NootedRed/UserVideoDecode.cpp').read_text()
        ledger = source[source.index('    VPF::CapabilityState ledgerState('):source.index('    // 统一执行器')]

        def function(marker):
            start = source.index(marker)
            end = source.index('{', start)
            depth = 1
            while depth:
                end += 1
                depth += (source[end] == '{') - (source[end] == '}')
            return source[start:end + 1]

        template = (ROOT / 'tools/fixtures/vpf-lifecycle-bridge.cpp.in').read_text()
        declaration = re.search(r'    VideoLedger\s+videoLedger;', source).start()
        template = template.replace('static VPF::CapabilityLedger videoLedger;',
                                    source[source.index('    class VideoLedger'):declaration]
                                    + 'static VideoLedger videoLedger;')
        template = template.replace('LEDGER', ledger)
        template = template.replace('MAP_DESTROY', function('    void wrappedMapDestroy('))
        template = template.replace('MAP_TARGET', function('    mach_vm_address_t mapDestroyTarget('))
        template = template.replace('INIT', source[source.index('void UserVideoDecode::init('):])
        file = WORK / 'lifecycle_bridge.cpp'
        file.write_text(template)
        executable = WORK / 'lifecycle_bridge'
        subprocess.run(_compiler() + [
            '-std=c++23', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
            '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
            str(file), '-o', str(executable),
        ], check=True, timeout=20)
        subprocess.run([str(executable)], check=True, timeout=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
