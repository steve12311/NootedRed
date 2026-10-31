"""只读解析本机真实 dyld 缓存，核对动态计划、ASLR 和实际扫描成本，不提交 GPU 命令。"""
from pathlib import Path
import ctypes
import struct
import subprocess
import time
from metal_cache import CacheReader

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/user-surface-sync/dynamic-cache'


def rebased_mappings(cache):
    """读取 slide_info2，按 dyld 规则还原 8 字节指针。"""
    result = []
    for start, end, _, handle in cache.mappings:
        handle.seek(312)
        position, count = struct.unpack('<II', handle.read(8))
        handle.seek(position)
        records = handle.read(count * 56)
        for i in range(count):
            address, _, _, slide, size, *_ = struct.unpack_from('<QQQQQQII', records, i * 56)
            if address == start and size:
                handle.seek(slide)
                info = handle.read(40)
                if struct.unpack_from('<I', info)[0] != 2:
                    raise ValueError('需 slide_info2 参考数据')
                mask, add = struct.unpack_from('<QQ', info, 24)
                result.append((start, end, mask, add))
    return result


def main():
    stubs = WORK / 'stubs/IOKit'
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / 'IOTypes.h').write_text('#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    source = WORK / 'probe.cpp'
    source.write_text(r'''
namespace Patch {}
#include <UserSurfaceSyncResolver.hpp>
#include <UserSubmitGuardData.hpp>
extern "C" int submitEntry(UserSurfaceSyncResolver::Reader read, uint64_t base) {
    UserSharedCache::Input input{read, {}};
    uint64_t offset;
    if (!UserSurfaceSyncResolver::resolveSubmit(input, base, offset, true)) { return 0; }
    uint8_t entry[UserSubmitGuardData::MethodPatchSize];
    return read(base + offset, entry, sizeof(entry)) == 0
        && memcmp(entry, UserSubmitGuardData::MethodOriginal, sizeof(entry)) == 0;
}
extern "C" int submitDynamic(UserSurfaceSyncResolver::Reader read, uint64_t base, uint64_t* offset) {
    UserSharedCache::Input input{read, {}};
    return UserSurfaceSyncResolver::resolveSubmit(input, base, *offset, true);
}
extern "C" int probe(UserSurfaceSyncResolver::Reader read, uint64_t base, uint32_t device,
                     uint64_t* offsets, uint32_t* reads) {
    const auto* selected = UserSurfaceSyncData::findDevice(device);
    if (!selected) { return 1; }
    UserSurfaceSyncResolver::Plan plan;
    auto& input = plan.input;
    input.reader = read;
    input.scanBuffer = plan.scan;
    using namespace UserSurfaceSyncData::ScratchResolverData;
    input.selectImages(base, ResolverImages, sizeof(ResolverImages) / sizeof(ResolverImages[0]));
    uint64_t submit;
    if (!UserSurfaceSyncResolver::resolveSubmit(input, base, submit, true)) { return 3; }
    if (!UserSurfaceSyncResolver::resolve(input, base, *selected, true, true, plan, true)) { return 2; }
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
    module = ctypes.CDLL(str(library))
    probe = module.probe
    module.submitEntry.argtypes = [reader_type, ctypes.c_uint64]
    module.submitEntry.restype = ctypes.c_int
    module.submitDynamic.argtypes = [reader_type, ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint64)]
    module.submitDynamic.restype = ctypes.c_int
    probe.argtypes = [reader_type, ctypes.c_uint64, ctypes.c_uint32,
                      ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint32)]
    probe.restype = ctypes.c_int
    cache = CacheReader()
    try:
        submit, _ = cache.function('-[GFX9AMD_MtlCmdQueue submitCommandBuffers:count:]')
        rebased = rebased_mappings(cache)
        for slide in [0, 0x1234000]:
            failures = []

            @reader_type
            def reader(address, output, size):
                try:
                    data = cache.read(address - slide, size)
                    if size == 8:
                        for start, end, mask, add in rebased:
                            if start <= address - slide < end:
                                value = int.from_bytes(data, 'little') & ~mask
                                data = (value + add + slide if value else 0).to_bytes(8, 'little')
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
            assert module.submitEntry(reader, cache.base + slide) == 1, '提交守卫入口字节不匹配'
            submit_offset = ctypes.c_uint64()
            assert module.submitDynamic(reader, cache.base + slide, ctypes.byref(submit_offset)) == 1
            assert cache.base + submit_offset.value == submit, '动态提交守卫没有定位到实际方法'
            reads = ctypes.c_uint32()
            start = time.monotonic()
            result = probe(reader, cache.base + slide, 0x15E7, offsets, ctypes.byref(reads))
            assert result == 0, (result, failures[:3])
            expected = [0x7FFB10D73A28-cache.base, 0x7FFB10D73AFF-cache.base, 0x7FFB10C79708-cache.base]
            assert list(offsets) == expected
            assert reads.value < 32768
            print(f'PASS actual cache / changed UUID / slide {slide:#x}: {reads.value} reads, '
                  f'{time.monotonic()-start:.3f}s (Python callback overhead included)')
    finally:
        cache.close()


if __name__ == '__main__':
    main()
