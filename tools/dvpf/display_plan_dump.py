"""编译并运行 DVPF 显示计划探针，输出规范化 JSON。"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import display_schema as schema

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'build/dvpf'
SOURCE = ROOT / 'tools/dvpf/display_plan_probe.cpp'
STUB = WORK / 'stubs/IOKit/IOTypes.h'


def compiler() -> list[str]:
    if shutil.which('xcrun'):
        return ['xcrun', 'clang++']
    if shutil.which('clang++'):
        return ['clang++']
    raise RuntimeError('需要 clang++ 或 xcrun clang++')


def build_probe(*, sanitize: bool = False) -> Path:
    STUB.parent.mkdir(parents=True, exist_ok=True)
    STUB.write_text(
        '#pragma once\n#include <cstdint>\n'
        'using UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n'
    )
    executable = WORK / ('display_plan_probe-sanitize' if sanitize else 'display_plan_probe')
    command = compiler() + [
        '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror',
        '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
        str(SOURCE), '-o', str(executable),
    ]
    if sanitize:
        command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    subprocess.run(command, check=True, timeout=120)
    return executable


def dump_plans(*, sanitize: bool = False) -> dict:
    executable = build_probe(sanitize=sanitize)
    result = subprocess.run([str(executable), '--json'], check=False, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f'显示计划探针失败: {result.stderr.strip()}')
    document = json.loads(result.stdout)
    if document.get('kind') != 'dvpf.plans' or document.get('schemaVersion') != schema.SCHEMA_VERSION:
        raise RuntimeError('显示计划探针输出的格式版本不匹配')
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=WORK / 'plans.json')
    parser.add_argument('--sanitize', action='store_true', help='使用 ASan/UBSan 构建探针')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()
    document = dump_plans(sanitize=args.sanitize)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(schema.canonical_json(document))
    if not args.quiet:
        print(f'PASS DVPF display plan dump: {len(document["plans"])} plans -> {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
