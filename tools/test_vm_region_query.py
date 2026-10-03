"""使用真实 Mach API 验证两处权限查询；仅检查内存映射，不提交 GPU 工作。"""
from pathlib import Path
import subprocess

from test_vcn_native_load import function

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vm-region-query"


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    helpers = []
    for module in ["UserSurfaceSync", "UserVideoDecode"]:
        source = (ROOT / f"NootedRed/{module}.cpp").read_text()
        body = function(source, "bool leafProtection(")
        helpers.append(body.replace("leafProtection", module + "Protection"))
    harness = r'''
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/vm_region.h>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstdint>
#include <initializer_list>
using UInt32 = uint32_t;
static kern_return_t lastError = 0;
static unsigned calls = 0;
static void error(kern_return_t value) { lastError = value; }
static kern_return_t queryRegion(vm_map_t map, mach_vm_address_t* address, mach_vm_size_t* size,
                                natural_t* depth, vm_region_recurse_info_t info,
                                mach_msg_type_number_t* count) {
    assert(*count == VM_REGION_SUBMAP_SHORT_INFO_COUNT_64);
    ++calls;
    return mach_vm_region_recurse(map, address, size, depth, info, count);
}
HELPERS
static double now() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
static void compare(mach_vm_address_t page) {
    natural_t depth = 0;
    double fullStart = now();
    vm_prot_t expected = VM_PROT_NONE;
    for (unsigned i = 0; i < 8; ++i) {
        auto address = page;
        mach_vm_size_t size = 0;
        vm_region_submap_info_data_64_t info{};
        mach_msg_type_number_t count = VM_REGION_SUBMAP_INFO_COUNT_64;
        assert(mach_vm_region_recurse(mach_task_self(), &address, &size, &depth,
                                     (vm_region_recurse_info_t)&info, &count) == KERN_SUCCESS);
        assert(count >= VM_REGION_SUBMAP_INFO_COUNT_64 && address <= page);
        assert(page - address < size && size - (page - address) >= PAGE_SIZE);
        if (!info.is_submap) { expected = info.protection; break; }
        assert(depth != ~0U);
        ++depth;
    }
    double fullUs = (now() - fullStart) * 1e6;
    vm_prot_t surface = -1, video = -1;
    double shortStart = now();
    assert(UserSurfaceSyncProtection(mach_task_self(), page, surface));
    assert(UserVideoDecodeProtection(mach_task_self(), page, video));
    double shortUs = (now() - shortStart) * 1e6 / 2;
    assert(surface == expected && video == expected && !lastError);
    printf("protection=%d full_us=%.3f short_us=%.3f\n", expected, fullUs, shortUs);
}
int main() {
    static_assert(VM_REGION_SUBMAP_SHORT_INFO_COUNT_64 < VM_REGION_SUBMAP_INFO_V0_COUNT_64);
    compare((mach_vm_address_t)&mach_vm_region_recurse & ~(mach_vm_address_t)(PAGE_SIZE - 1));
    mach_vm_address_t memory = 0;
    assert(mach_vm_allocate(mach_task_self(), &memory, PAGE_SIZE * 2, VM_FLAGS_ANYWHERE) == KERN_SUCCESS);
    for (vm_prot_t protection : {VM_PROT_READ | VM_PROT_WRITE, VM_PROT_READ, VM_PROT_NONE}) {
        assert(mach_vm_protect(mach_task_self(), memory, PAGE_SIZE * 2, false, protection) == KERN_SUCCESS);
        compare(memory);
    }
    vm_prot_t protection = -1;
    assert(!UserSurfaceSyncProtection(mach_task_self(), 0x1000, protection));
    assert(!UserVideoDecodeProtection(mach_task_self(), 0x1000, protection));
    assert(mach_vm_deallocate(mach_task_self(), memory, PAGE_SIZE * 2) == KERN_SUCCESS);
    assert(calls);
    puts("PASS: real short/full protection parity; shared cache, RW/R/NONE and unmapped addresses");
}
'''.replace("HELPERS", "\n".join(helpers))
    cpp = WORK / "test-query.cpp"
    executable = WORK / "test-query"
    cpp.write_text(harness)
    subprocess.run(["xcrun", "clang++", "-std=c++23", "-O2", "-Wall", "-Wextra", "-Werror",
                    str(cpp), "-o", str(executable)], check=True, timeout=60)
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=True, timeout=60)
    (WORK / "result.txt").write_text(result.stdout + result.stderr)
    print(result.stdout, end="")


if __name__ == "__main__":
    main()
