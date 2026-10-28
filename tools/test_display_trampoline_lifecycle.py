"""DVPF SubmitGuard 跳板生命周期与故障注入回归。

本测试只执行纯状态机；真实 vm_allocate/vm_protect/copyout 仍由现有安装器负责。
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/dvpf/trampoline'


def _compiler() -> list[str]:
    if shutil.which('xcrun'):
        return ['xcrun', 'clang++']
    if shutil.which('clang++'):
        return ['clang++']
    raise RuntimeError('需要 clang++ 或 xcrun clang++')


class DisplayTrampolineLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stub = WORK / 'stubs/IOKit/IOTypes.h'
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(
            '#pragma once\n#include <cstdint>\n'
            'using UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n'
        )

    def _build(self, sanitize: bool) -> Path:
        executable = WORK / ('trampoline_probe-sanitize' if sanitize else 'trampoline_probe')
        command = _compiler() + [
            '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror',
            '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
            str(ROOT / 'tools/dvpf/trampoline_probe.cpp'), '-o', str(executable),
        ]
        if sanitize:
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        subprocess.run(command, check=True, timeout=60)
        return executable

    def test_trampoline_probe(self):
        subprocess.run([str(self._build(False))], check=True, timeout=60)

    def test_trampoline_probe_sanitized(self):
        subprocess.run([str(self._build(True))], check=True, timeout=60)


if __name__ == '__main__':
    unittest.main()
