"""用系统真实 Mach ABI 验证两个 FVRC 桥接只查询映射属性，不请求逐页统计。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/fvrc-mapping-probe'


def main():
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text(
        '#pragma once\n#include <cstdint>\n'
        'using UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    failures = []
    for module in ('UserSurfaceSync', 'UserVideoDecode'):
        source = (ROOT / f'NootedRed/{module}.cpp').read_text()
        probe = source[source.index('struct MappingProbeState'):]
        probe = probe[:probe.index('originalCheck')]
        probe = probe[:probe.rfind('mach_vm_address_t')]
        harness = r'''
#include <cassert>
#include <cstdio>
#include <mach/mach.h>
#include <VPF/ResolutionIdentity.hpp>
using RegionRecurse = kern_return_t (*)(vm_map_t, mach_vm_address_t*, mach_vm_size_t*, natural_t*,
                                        vm_region_recurse_info_t, mach_msg_type_number_t*);
PROBE
static unsigned calls=0, mode=0;
static kern_return_t query(vm_map_t, mach_vm_address_t* address, mach_vm_size_t* size, natural_t* depth,
                           vm_region_recurse_info_t raw, mach_msg_type_number_t* count) {
    ++calls;
    // 完整结构和短结构的大小、布局不同；禁止测试桩用别名掩盖错误请求。
    static_assert(VM_REGION_SUBMAP_SHORT_INFO_COUNT_64 < VM_REGION_SUBMAP_INFO_COUNT_64);
    assert(*count == VM_REGION_SUBMAP_SHORT_INFO_COUNT_64);
    if (mode == 1) { return KERN_FAILURE; }
    auto& info = *reinterpret_cast<vm_region_submap_short_info_data_64_t*>(raw);
    *address = 0x100000000ULL;
    *size = 0x40000000ULL;
    info.object_id = 0x1234;
    info.share_mode = SM_TRUESHARED;
    info.shadow_depth = 0;
    info.protection = VM_PROT_READ | VM_PROT_EXECUTE;
    info.is_submap = *depth == 0;
    if (mode == 2) { *count = VM_REGION_SUBMAP_SHORT_INFO_COUNT_64 - 1; }
    if (mode == 3) { info.protection |= VM_PROT_WRITE; }
    if (mode == 4) { info.share_mode = SM_COW; }
    if (mode == 5) { info.shadow_depth = 1; }
    if (mode == 6) { info.object_id = 0; }
    if (mode == 7) { info.share_mode = SM_COW; info.shadow_depth = 5; }
    if (mode == 9) { info.share_mode = SM_COW; info.shadow_depth = 2; }
    if (mode == 8) { info.share_mode = SM_PRIVATE; }
    return KERN_SUCCESS;
}
int main() {
    MappingProbeState state{mach_task_self(), query};
    VPF::MappingInfo info{};
    assert(probeMapping(&state, 0x100001000ULL, 0, info));
    assert(info.start == 0x100000000ULL && info.size == 0x40000000ULL && info.isSubmap);
    VPF::MappingFacts facts{};
    calls = 0;
    assert(VPF::inspectMapping(probeMapping, &state, 0x100001000ULL, 0x800000, facts));
    assert(calls == 2 && facts.objectId == 0x1234 && facts.shareMode == SM_TRUESHARED && !facts.writable);
    for (mode = 1; mode <= 2; ++mode) {
        assert(!probeMapping(&state, 0x100001000ULL, 1, info));
    }
    // 共享缓存 __TEXT 实际以 SM_COW 上报；未产生 shadow 时仍是缓存文件原始对象，可复用。
    mode = 4;
    assert(VPF::inspectMapping(probeMapping, &state, 0x100001000ULL, 0x800000, facts));
    assert(facts.objectId == 0x1234 && facts.shareMode == SM_COW && facts.shadowDepth == 0 && !facts.writable);
    assert((VPF::lastMappingReject & 0xFF) == VPF::MappingRejectNone);
    // Tahoe 实测：机器级共享子映射的原始缓存对象为 SM_COW 且 shadow_depth=2。
    mode = 9;
    assert(VPF::inspectMapping(probeMapping, &state, 0x100001000ULL, 0x800000, facts));
    assert(facts.objectId == 0x1234 && facts.shareMode == SM_COW && facts.shadowDepth == 2);
    for (mode = 3; mode <= 8; ++mode) {
        if (mode == 4) { continue; }
        assert(!VPF::inspectMapping(probeMapping, &state, 0x100001000ULL, 0x800000, facts));
        assert((VPF::lastMappingReject & 0xFF) != VPF::MappingRejectNone);
    }
    mode = 7;
    assert(!VPF::inspectMapping(probeMapping, &state, 0x100001000ULL, 0x800000, facts));
    assert((VPF::lastMappingReject & 0xFF) == VPF::MappingRejectShadow);
    state.query = nullptr;
    assert(!probeMapping(&state, 0x100001000ULL, 1, info));
    puts("PASS real Mach ABI: short query, large shared region, submap identity, clean COW reuse, shadow/private rejection, failure and truncated reply");
}
'''.replace('PROBE', probe)
        path, executable = WORK / f'{module}.cpp', WORK / module
        path.write_text(harness)
        subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-I' + str(WORK / 'stubs'),
                        '-I' + str(ROOT / 'NootedRed'), str(path), '-o', str(executable)],
                       check=True, timeout=60)
        result = subprocess.run([str(executable)], timeout=60)
        if result.returncode:
            failures.append(module)
    assert not failures, f'FVRC mapping probe failed: {failures}'


if __name__ == '__main__':
    main()
