"""DVPF Metal 计划执行器：读回状态、跨页、故障回滚与权限恢复。"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/display-transaction'


def main():
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text(
        '#pragma once\n#include <cstdint>\n'
        'using UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    exe = WORK / 'metal-transaction-probe'
    flags = ['-fsanitize=address,undefined'] if '--sanitize' in sys.argv else []
    subprocess.run(['clang++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror', *flags,
                    '-I', str(WORK / 'stubs'), '-I', str(ROOT / 'NootedRed'),
                    str(ROOT / 'tools/dvpf/metal_transaction_probe.cpp'), '-o', str(exe)],
                   check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
