// 在进程私有代码页应用精确版本的 Metal 修复，并保留原生调用与页面保护。
#include <Headers/kern_util.hpp>
#include <NRed.hpp>
#include <PenguinWizardry/KernelVersion.hpp>
#include <UserSurfaceSync.hpp>
#include <UserSurfaceSyncData.hpp>
#include <kern/task.h>
#include <mach/vm_map.h>
#include <mach/vm_region.h>
#include <mach/i386/vm_param.h>
#include <sys/proc.h>

namespace {

struct SharedRegionCheckArgs
{
    user_addr_t startAddress;
};
static_assert(sizeof(SharedRegionCheckArgs) == 8);
using SharedRegionCheck = int (*)(proc_t, SharedRegionCheckArgs*, int*);
using GetTaskMap = vm_map_t (*)(task_t);
using RegionRecurse = kern_return_t (*)(vm_map_t, mach_vm_address_t*, mach_vm_size_t*, natural_t*,
                                       vm_region_recurse_info_t, mach_msg_type_number_t*);
using CopyIn = int (*)(user_addr_t, void*, size_t);
using CopyOut = int (*)(const void*, user_addr_t, size_t);

mach_vm_address_t originalCheck{0};
GetTaskMap getTaskMap{nullptr};
RegionRecurse queryRegion{nullptr};
CopyIn readUser{nullptr};
CopyOut writeUser{nullptr};
UInt32 seenStages{0}, appliedCount{0}, errorCount{0}, windowServerCount{0}, weatherCount{0};
bool srdShared{false}, legacyBlend{false}, computeScratch{false};
constexpr auto PatchCount = arrsize(UserSurfaceSyncData::ComputeScratchPatches);
constexpr auto LegacyPatchCount = arrsize(UserSurfaceSyncData::LegacyBlendPatches);
constexpr auto BaselinePatchCount = arrsize(UserSurfaceSyncData::Patches);
static_assert(BaselinePatchCount == arrsize(UserSurfaceSyncData::SrdSharedPatches));
static_assert(PatchCount <= 16 && UserSurfaceSyncData::PageCount <= 8);
static_assert(UserSurfaceSyncData::MaxPatchSize <= 512);

enum class Stage : UInt32 {
    Called, OriginalError, Argument, CacheRead, CacheMismatch, CodeRead, CodeMismatch, Already,
    Mixed, NoMap, Region, Protection, Write, Restore, Rollback, Applied
};

void stage(const Stage value)
{
    static const char* const keys[] = {
        "NRedImmediateSyncD00", "NRedImmediateSyncD01", "NRedImmediateSyncD02", "NRedImmediateSyncD03",
        "NRedImmediateSyncD04", "NRedImmediateSyncD05", "NRedImmediateSyncD06", "NRedImmediateSyncD07",
        "NRedImmediateSyncD08", "NRedImmediateSyncD09", "NRedImmediateSyncD10", "NRedImmediateSyncD11",
        "NRedImmediateSyncD12", "NRedImmediateSyncD13", "NRedImmediateSyncD14", "NRedImmediateSyncD15"
    };
    const auto index = static_cast<UInt32>(value);
    if ((__atomic_fetch_or(&seenStages, 1U << index, __ATOMIC_RELAXED) & (1U << index)) == 0) {
        NRed::singleton().setProp32(keys[index], 1);
        SYSLOG("UserSurfaceSync", "%s stage=%u",
               computeScratch ? "ComputeScratch-v1" :
                   (legacyBlend ? "LegacyBlend-v1" : (srdShared ? "SrdShared-v1" : "ImmediateSync-v1")), index);
    }
}

void error(const kern_return_t value)
{
    auto count = __atomic_load_n(&errorCount, __ATOMIC_RELAXED);
    while (count < 8) {
        if (__atomic_compare_exchange_n(&errorCount, &count, count + 1, false, __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
            NRed::singleton().setProp32("NRedImmediateSyncLastError", static_cast<UInt32>(value));
            SYSLOG("UserSurfaceSync", "%s rejected: error=0x%X",
                   legacyBlend ? "LegacyBlend-v1" : (srdShared ? "SrdShared-v1" : "ImmediateSync-v1"), value);
            break;
        }
    }
}

bool leafProtection(vm_map_t map, const mach_vm_address_t page, vm_prot_t& protection)
{
    natural_t depth{0};
    for (UInt32 attempt = 0; attempt < 8; ++attempt) {
        auto address = page;
        mach_vm_size_t size{0};
        // 只查询映射与权限，避免完整查询逐页统计整个共享缓存区域。
        vm_region_submap_short_info_data_64_t info{};
        mach_msg_type_number_t count = VM_REGION_SUBMAP_SHORT_INFO_COUNT_64;
        const auto result = queryRegion(map, &address, &size, &depth,
                                        reinterpret_cast<vm_region_recurse_info_t>(&info), &count);
        if (result != KERN_SUCCESS) { error(result); return false; }
        if (count < VM_REGION_SUBMAP_SHORT_INFO_COUNT_64 || address > page || page - address >= size
            || size - (page - address) < PAGE_SIZE) { return false; }
        if (!info.is_submap) { protection = info.protection; return true; }
        if (depth == ~0U) { break; }
        ++depth;
    }
    return false;
}

struct Page {
    mach_vm_address_t address{0};
    vm_prot_t protection{VM_PROT_NONE};
    bool writable{false};
};

bool restorePages(vm_map_t map, Page* pages, const UInt32 count)
{
    bool restored = true;
    for (UInt32 i = 0; i < count; ++i) {
        if (!pages[i].writable) { continue; }
        auto result = vm_protect(map, pages[i].address, PAGE_SIZE, FALSE, pages[i].protection);
        if (result != KERN_SUCCESS) {
            error(result);
            result = vm_protect(map, pages[i].address, PAGE_SIZE, FALSE, pages[i].protection);
        }
        if (result == KERN_SUCCESS) { pages[i].writable = false; }
        else { restored = false; }
    }
    return restored;
}

bool rollback(vm_map_t map, Page* pages, const UInt32 pageCount, const UInt64 base,
              const UInt8 saved[PatchCount][UserSurfaceSyncData::MaxPatchSize], const UInt32 touched)
{
    using namespace UserSurfaceSyncData;
    const auto* const patches = computeScratch ? ComputeScratchPatches :
        (legacyBlend ? LegacyBlendPatches : (srdShared ? SrdSharedPatches : Patches));
    bool ok = true;
    for (UInt32 i = 0; i < touched; ++i) {
        const auto address = base + patches[i].offset;
        const auto pageAddress = address & ~static_cast<UInt64>(PAGE_SIZE - 1);
        Page* page = nullptr;
        for (UInt32 p = 0; p < pageCount; ++p) {
            if (pages[p].address == pageAddress) {
                page = &pages[p];
                break;
            }
        }
        if (page == nullptr) { ok = false; continue; }
        if (!page->writable) {
            const auto result = vm_protect(map, pageAddress, PAGE_SIZE, FALSE,
                                           VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
            if (result != KERN_SUCCESS) { error(result); ok = false; continue; }
            page->writable = true;
        }
        if (writeUser(saved[i], address, patches[i].size) != 0) { ok = false; }
    }
    if (!restorePages(map, pages, pageCount)) { ok = false; }
    if (!ok) { stage(Stage::Rollback); error(KERN_FAILURE); }
    return ok;
}

int wrappedSharedRegionCheck(proc_t process, SharedRegionCheckArgs* args, int* returnValue)
{
    stage(Stage::Called);
    const auto result = reinterpret_cast<SharedRegionCheck>(originalCheck)(process, args, returnValue);
    if (result != 0) { stage(Stage::OriginalError); return result; }
    if (args == nullptr || args->startAddress == 0) { stage(Stage::Argument); return result; }
    using namespace UserSurfaceSyncData;
    const auto* const patches = computeScratch ? ComputeScratchPatches :
        (legacyBlend ? LegacyBlendPatches : (srdShared ? SrdSharedPatches : Patches));
    const auto patchCount = computeScratch ? PatchCount : (legacyBlend ? LegacyPatchCount : BaselinePatchCount);
    UInt64 base{0};
    UInt8 header[104];
    if (readUser(args->startAddress, &base, sizeof(base)) != 0 || base < CacheBase
        || base - CacheBase > 0x100000000ULL || readUser(base, header, sizeof(header)) != 0) {
        stage(Stage::CacheRead); return result;
    }
    if (memcmp(header, CacheMagic, sizeof(CacheMagic)) != 0 || memcmp(header + 88, CacheUUID, sizeof(CacheUUID)) != 0) {
        stage(Stage::CacheMismatch); return result;
    }

    // 禁止在残留另一策略的进程中把旧模式误报为已生效；切换参数仍需重启。
    if (!computeScratch) {
        UInt8 ctx[MaxPatchSize];
        for (UInt32 i = patchCount; i < PatchCount; ++i) {
            const auto& extra = ComputeScratchPatches[i];
            if (readUser(base + extra.offset, ctx, extra.size) != 0
                || memcmp(ctx, extra.original, extra.size) != 0) { stage(Stage::CodeMismatch); return result; }
        }
    }
    // 旧候选的两处全局 VT 跳转已撤回；不把混合策略误报为新模式已生效。
    if (computeScratch) {
        for (const auto& withdrawn : WithdrawnTransferGuards) {
            UInt8 bytes[6];
            if (readUser(base + withdrawn.offset, bytes, withdrawn.size) != 0
                || memcmp(bytes, withdrawn.original, withdrawn.size) != 0) {
                stage(Stage::CodeMismatch); return result;
            }
        }
    }
    UInt8 saved[PatchCount][MaxPatchSize];
    Page pages[PageCount];
    UInt32 pageCount{0}, originals{0}, patched{0};
    for (UInt32 i = 0; i < patchCount; ++i) {
        const auto address = base + patches[i].offset;
        const auto page = address & ~static_cast<UInt64>(PAGE_SIZE - 1);
        if (patches[i].size > MaxPatchSize || address + patches[i].size > page + PAGE_SIZE
            || readUser(address, saved[i], patches[i].size) != 0) { stage(Stage::CodeRead); return result; }
        if (memcmp(saved[i], patches[i].original, patches[i].size) == 0) { ++originals; }
        else if (memcmp(saved[i], patches[i].patched, patches[i].size) == 0) { ++patched; }
        else { stage(Stage::CodeMismatch); error(KERN_INVALID_ARGUMENT); return result; }
        bool found = false;
        for (UInt32 p = 0; p < pageCount; ++p) {
            if (pages[p].address == page) {
                found = true;
                break;
            }
        }
        if (!found) {
            if (pageCount == PageCount) { stage(Stage::CodeMismatch); return result; }
            pages[pageCount++].address = page;
        }
    }
    if (patched == patchCount) { stage(Stage::Already); return result; }
    if (originals != patchCount) { stage(Stage::Mixed); return result; }
    vm_map_t map = getTaskMap(current_task());
    if (map == nullptr) { stage(Stage::NoMap); return result; }
    for (UInt32 p = 0; p < pageCount; ++p) {
        if (!leafProtection(map, pages[p].address, pages[p].protection)
            || pages[p].protection != (VM_PROT_READ | VM_PROT_EXECUTE)) { stage(Stage::Region); return result; }
    }
    // 所有匹配和保护检查完成后才进入写入；各页均先 COW、去掉 EXECUTE。
    for (UInt32 p = 0; p < pageCount; ++p) {
        const auto protection = vm_protect(map, pages[p].address, PAGE_SIZE, FALSE,
                                          VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
        if (protection != KERN_SUCCESS) {
            stage(Stage::Protection); error(protection);
            if (!restorePages(map, pages, pageCount)) { stage(Stage::Rollback); }
            return result;
        }
        pages[p].writable = true;
    }
    for (UInt32 i = 0; i < patchCount; ++i) {
        if (writeUser(patches[i].patched, base + patches[i].offset, patches[i].size) != 0) {
            stage(Stage::Write); error(KERN_FAILURE);
            rollback(map, pages, pageCount, base, saved, i + 1);
            return result;
        }
    }
    if (!restorePages(map, pages, pageCount)) {
        stage(Stage::Restore);
        rollback(map, pages, pageCount, base, saved, patchCount);
        return result;
    }
    stage(Stage::Applied);
    // 仅记录已完整写入并恢复 RX 的 WindowServer，避免用全局计数代替故障进程证据。
    if (process != nullptr) {
        const auto pid = proc_pid(process);
        char name[32]{};
        if (pid > 0) { proc_name(pid, name, sizeof(name)); }
        name[sizeof(name) - 1] = '\0';
        if (computeScratch && strcmp(name, "Weather") == 0) {
            auto observed = __atomic_load_n(&weatherCount, __ATOMIC_RELAXED);
            while (observed < 4) {
                if (__atomic_compare_exchange_n(&weatherCount, &observed, observed + 1, false,
                                                __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
                    NRed::singleton().setProp32("NRedComputeScratchWeatherPID", static_cast<UInt32>(pid));
                    NRed::singleton().setProp32("NRedComputeScratchWeatherApplied", observed + 1);
                    SYSLOG("UserSurfaceSync", "ComputeScratch-v1 Weather patched and RX restored: pid=%d", pid);
                    break;
                }
            }
        }
        if (strcmp(name, "WindowServer") == 0) {
            auto observed = __atomic_load_n(&windowServerCount, __ATOMIC_RELAXED);
            while (observed < 4) {
                if (__atomic_compare_exchange_n(&windowServerCount, &observed, observed + 1, false,
                                                __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
                    NRed::singleton().setProp32("NRedImmediateSyncWindowServerPID", static_cast<UInt32>(pid));
                    NRed::singleton().setProp32("NRedImmediateSyncWindowServerApplied", observed + 1);
                    if (srdShared) {
                        NRed::singleton().setProp32("NRedSrdSharedWindowServerPID", static_cast<UInt32>(pid));
                        NRed::singleton().setProp32("NRedSrdSharedWindowServerApplied", observed + 1);
                    }
                    if (legacyBlend) {
                        NRed::singleton().setProp32("NRedLegacyBlendWindowServerPID", static_cast<UInt32>(pid));
                        NRed::singleton().setProp32("NRedLegacyBlendWindowServerApplied", observed + 1);
                    }
                    SYSLOG("UserSurfaceSync", "ImmediateSync-v1 WindowServer patched: pid=%d count=%u", pid,
                           observed + 1);
                    break;
                }
            }
        }
    }
    auto count = __atomic_load_n(&appliedCount, __ATOMIC_RELAXED);
    while (count < 16) {
        if (__atomic_compare_exchange_n(&appliedCount, &count, count + 1, false, __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
            NRed::singleton().setProp32("NRedImmediateSyncApplied", count + 1);
            if (srdShared) { NRed::singleton().setProp32("NRedSrdSharedApplied", count + 1); }
            if (legacyBlend) { NRed::singleton().setProp32("NRedLegacyBlendApplied", count + 1); }
            if (computeScratch) { NRed::singleton().setProp32("NRedComputeScratchApplied", count + 1); }
            SYSLOG("UserSurfaceSync", "ImmediateSync-v1 private Metal initialization patched: count=%u", count + 1);
            break;
        }
    }
    return result;
}

} // namespace

void UserSurfaceSync::init(KernelPatcher& patcher)
{
    NRed::singleton().setProp32("NRedImmediateSyncVersion", 1);
    NRed::singleton().setProp32("NRedSrdSharedVersion", 1);
    NRed::singleton().setProp32("NRedLegacyBlendVersion", 1);
    NRed::singleton().setProp32("NRedComputeScratchVersion", 1);
    NRed::singleton().setProp32("NRedTransfer1x1Version", 2);
    const bool withdrawnRequested = checkKernelArgument("-NRedTransfer1x1");
    computeScratch = checkKernelArgument("-NRedComputeScratch") || withdrawnRequested;
    if (withdrawnRequested) {
        NRed::singleton().setProp32("NRedTransfer1x1Redirected", 1);
        SYSLOG("UserSurfaceSync", "Transfer1x1 withdrawn: using device-gated ComputeScratch-v1 instead");
    }
    legacyBlend = checkKernelArgument("-NRedLegacyBlend") || computeScratch;
    srdShared = checkKernelArgument("-NRedSrdShared") || legacyBlend;
    if (!checkKernelArgument("-NRedImmediateSync") && !srdShared) { return; }
    if (NRed::singleton().getDeviceID() != 0x1638 || currentKernelVersion().major() != 25
        || currentKernelVersion().minor() != 6) { return; }
    getTaskMap = reinterpret_cast<GetTaskMap>(patcher.solveSymbol(KernelPatcher::KernelID, "_get_task_map"));
    patcher.clearError();
    queryRegion = reinterpret_cast<RegionRecurse>(
        patcher.solveSymbol(KernelPatcher::KernelID, "_mach_vm_region_recurse"));
    patcher.clearError();
    readUser = reinterpret_cast<CopyIn>(patcher.solveSymbol(KernelPatcher::KernelID, "_copyin"));
    patcher.clearError();
    writeUser = reinterpret_cast<CopyOut>(patcher.solveSymbol(KernelPatcher::KernelID, "_copyout"));
    patcher.clearError();
    if (!getTaskMap || !queryRegion || !readUser || !writeUser) {
        NRed::singleton().setProp32("NRedImmediateSyncRejected", 1); return;
    }
    KernelPatcher::RouteRequest request{"_shared_region_check_np", wrappedSharedRegionCheck, originalCheck};
    if (!patcher.routeMultipleLong(KernelPatcher::KernelID, &request, 1)) {
        patcher.clearError(); NRed::singleton().setProp32("NRedImmediateSyncRejected", 2); return;
    }
    NRed::singleton().setProp32("NRedImmediateSyncEnabled", 1);
    NRed::singleton().setProp32("NRedImmediateSyncRegionQueryVersion", 1);
    if (srdShared) {
        NRed::singleton().setProp32("NRedSrdSharedEnabled", 1);
        SYSLOG("UserSurfaceSync", "SrdShared-v1 enabled on ImmediateSync baseline: exact cache/code required");
    }
    if (legacyBlend) {
        NRed::singleton().setProp32("NRedLegacyBlendEnabled", 1);
        SYSLOG("UserSurfaceSync", "LegacyBlend-v1 enabled: exact code; target pipeline dual-quad disabled");
    }
    if (computeScratch) {
        NRed::singleton().setProp32("NRedComputeScratchEnabled", 1);
        SYSLOG("UserSurfaceSync", "ComputeScratch-v1 enabled: single-SE scratch sizing; native kernel preserved; "
               "exact cache/code required");
    }
    SYSLOG("UserSurfaceSync", "ImmediateSync-v1 enabled: exact cache/code required, device 0x1638 only");
}
