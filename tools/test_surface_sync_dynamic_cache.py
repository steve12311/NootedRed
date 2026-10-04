"""只读解析本机真实 dyld 缓存，核对动态计划、ASLR 和实际扫描成本，不提交 GPU 命令。"""
from pathlib import Path
import ctypes
import subprocess
import time
from metal_cache import CacheReader

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/user-surface-sync/dynamic-cache'


def main():
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    source = WORK / 'probe.cpp'
    source.write_text(r'''
namespace Patch {}
#include <UserSurfaceSyncResolver.hpp>
extern "C" int probe(UserSurfaceSyncResolver::Reader read, uint64_t base, uint32_t device,
                     uint64_t* offsets, uint32_t* reads) {
    const auto* selected = UserSurfaceSyncData::findDevice(device);
    if (!selected) { return 1; }
    UserSurfaceSyncResolver::Plan plan;
    if (!UserSurfaceSyncResolver::resolve(read, base, *selected, true, true, plan)) { return 2; }
    for (unsigned i=0;i<3;i++) { offsets[i] = plan.patches[i].offset; }
    *reads = plan.reads;
    return 0;
}
''')
    library = WORK / 'probe.dylib'
    subprocess.run(['clang++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Wshadow', '-Werror', '-dynamiclib',
                    '-I', str(WORK / 'stubs'), '-I', str(ROOT / 'NootedRed'), str(source), '-o', str(library)],
                   check=True, timeout=60)
    reader_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t)
    probe = ctypes.CDLL(str(library)).probe
    probe.argtypes = [reader_type, ctypes.c_uint64, ctypes.c_uint32,
                      ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint32)]
    probe.restype = ctypes.c_int
    cache = CacheReader()
    try:
        for slide in [0, 0x1234000]:
            failures = []

            @reader_type
            def reader(address, output, size):
                try:
                    data = cache.read(address - slide, size)
                    # 模拟另一 UUID；其他内容取真实系统文件，未写入共享缓存。
                    if address - slide == cache.base:
                        changed = bytearray(data)
                        if len(changed) >= 104:
                            changed[88] ^= 1
                        data = bytes(changed)
                    ctypes.memmove(output, data, size)
                    return 0
                except Exception as error:
                    failures.append(str(error))
                    return 1

            offsets = (ctypes.c_uint64 * 3)()
            reads = ctypes.c_uint32()
            start = time.monotonic()
            result = probe(reader, cache.base + slide, 0x15E7, offsets, ctypes.byref(reads))
            assert result == 0, (result, failures[:3])
            expected = [0x7FFB10D71A28-cache.base, 0x7FFB10D71AFF-cache.base, 0x7FFB10C77708-cache.base]
            assert list(offsets) == expected
            assert reads.value < 32768
            print(f'PASS actual cache / changed UUID / slide {slide:#x}: {reads.value} reads, '
                  f'{time.monotonic()-start:.3f}s (Python callback overhead included)')
    finally:
        cache.close()


if __name__ == '__main__':
    main()
