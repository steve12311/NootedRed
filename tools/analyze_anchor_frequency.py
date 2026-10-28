"""离线统计生成函数数据的字节频度秩，检查 UserCacheResolver 中的锚点表。"""
from pathlib import Path
import argparse
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/anchor-frequency'
RESOLVER = ROOT / 'NootedRed/UserCacheResolver.hpp'

HEADERS = [
    'UserVideoDecodeData.hpp',
    'UserSequoiaVideoData.hpp',
    'UserVideoGuard24Data.hpp',
    'UserVideoGuard25Data.hpp',
    'UserSequoiaSurfaceData.hpp',
    'UserSurfaceConstructorData.hpp',
    'UserSurfaceSyncData.hpp',
]

SOURCE = r'''
#include <cstdio>
#include <UserVideoDecodeData.hpp>
#include <UserSequoiaVideoData.hpp>
#include <UserVideoGuard24Data.hpp>
#include <UserVideoGuard25Data.hpp>
#include <UserSequoiaSurfaceData.hpp>
#include <UserSurfaceConstructorData.hpp>
#include <UserSurfaceSyncData.hpp>
static unsigned long long counts[256];
template <typename Function, size_t Count>
static void collect(const Function (&functions)[Count])
{
    for (const auto& function : functions) {
        for (unsigned i = 0; i < function.size; ++i) { counts[function.original[i]]++; }
    }
}
int main()
{
    collect(UserVideoDecodeData::ResolverFunctions);
    collect(UserSequoiaVideoData::ResolverFunctions);
    collect(UserVideoGuard24Data::ResolverFunctions);
    collect(UserVideoGuard25Data::ResolverFunctions);
    collect(UserSequoiaSurfaceData::ResolverFunctions);
    collect(UserSequoiaSurfaceData::Submit::ResolverFunctions);
    collect(UserSurfaceConstructorData::ResolverFunctions);
    collect(UserSurfaceSyncData::ScratchResolverData::ResolverFunctions);
    for (unsigned i = 0; i < 256; ++i) { printf("%llu\n", counts[i]); }
    return 0;
}
'''


def compile_and_count():
    stubs = WORK / 'stubs'
    (stubs / 'IOKit').mkdir(parents=True, exist_ok=True)
    (stubs / 'IOKit/IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    source = WORK / 'frequencies.cpp'
    source.write_text(SOURCE)
    executable = WORK / 'frequencies'
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-I' + str(stubs), '-I' + str(ROOT / 'NootedRed'),
                    str(source), '-o', str(executable)], check=True, timeout=60)
    output = subprocess.run([str(executable)], check=True, capture_output=True, text=True, timeout=60).stdout
    counts = [int(line) for line in output.split()]
    assert len(counts) == 256
    return counts


def rank_table(counts):
    # 数值越小越稀有；同频度按字节值排序，保证离线与运行时可复现。
    order = sorted(range(256), key=lambda value: (counts[value], value))
    ranks = [0] * 256
    for rank, value in enumerate(order):
        ranks[value] = rank
    return ranks


def embedded_table():
    text = RESOLVER.read_text()
    match = re.search(r'AnchorRarity\[256\]\s*=\s*\{(.*?)\};', text, re.S)
    if not match:
        raise SystemExit('UserCacheResolver.hpp 中未找到 AnchorRarity 表')
    values = [int(value, 0) for value in re.findall(r'0x[0-9A-Fa-f]{1,2}|\b\d{1,3}\b', match.group(1))]
    if len(values) != 256:
        raise SystemExit(f'AnchorRarity 表长度错误：{len(values)}')
    return values


def emit(ranks):
    lines = []
    for start in range(0, 256, 16):
        lines.append('        ' + ', '.join(f'0x{value:02X}' for value in ranks[start:start + 16]) + ',')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--emit', action='store_true', help='输出 C++ 表')
    parser.add_argument('--check', action='store_true', help='核对头文件中的表')
    args = parser.parse_args()
    counts = compile_and_count()
    ranks = rank_table(counts)
    if args.emit:
        print(emit(ranks))
        return
    if args.check:
        embedded = embedded_table()
        if embedded != ranks:
            differences = [(value, embedded[value], ranks[value]) for value in range(256)
                           if embedded[value] != ranks[value]]
            raise SystemExit(f'AnchorRarity 与生成数据统计不一致：{len(differences)} 项，例如 {differences[:8]}')
        print('PASS AnchorRarity matches generated function corpus')
        return
    print('most frequent:', sorted(range(256), key=lambda value: counts[value], reverse=True)[:8])
    print('least frequent:', sorted(range(256), key=lambda value: counts[value])[:8])
    print('total bytes:', sum(counts))


if __name__ == '__main__':
    main()
