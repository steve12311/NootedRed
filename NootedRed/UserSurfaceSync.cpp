// 在进程私有代码页应用精确版本的 Metal 修复，并保留原生调用与页面保护。
#include <Headers/kern_util.hpp>
#include <NRed.hpp>
#include <PenguinWizardry/KernelVersion.hpp>
#include <UserSurfaceSync.hpp>
#include <UserSurfaceSyncData.hpp>
#include <UserSurfaceSyncResolver.hpp>
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
const UserSurfaceSyncData::DevicePatchSet* devicePatches{&UserSurfaceSyncData::DevicePatches[0]};

constexpr auto PatchCount = arrsize(UserSurfaceSyncData::ComputeScratchPatches);
constexpr auto LegacyPatchCount = arrsize(UserSurfaceSyncData::LegacyBlendPatches);
constexpr auto BaselinePatchCount = arrsize(UserSurfaceSyncData::Patches);
static_assert(BaselinePatchCount == arrsize(UserSurfaceSyncData::SrdSharedPatches));
static_assert(PatchCount <= 16 && UserSurfaceSyncData::PageCount <= 8);
static_assert(UserSurfaceSyncData::MaxPatchSize <= 512);
constexpr UInt32 MaxPageCount = 8;

const UserSurfaceSyncData::Patch* activePatches()
{
    return computeScratch ?
               devicePatches->scratch :
               (legacyBlend ? devicePatches->blend : (srdShared ? devicePatches->shared : devicePatches->immediate));
}

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
              const UserSurfaceSyncData::Patch* patches,
              const UInt8 saved[PatchCount][UserSurfaceSyncData::MaxPatchSize], const UInt32 touched)
{
    using namespace UserSurfaceSyncData;
    bool ok = true;
    for (UInt32 i = 0; i < touched; ++i) {
        const auto address = base + patches[i].offset;
        const auto first   = address & ~static_cast<UInt64>(PAGE_SIZE - 1);
        const auto last    = (address + patches[i].size - 1) & ~static_cast<UInt64>(PAGE_SIZE - 1);
        bool       ready   = true;
        for (auto pageAddress = first; pageAddress <= last; pageAddress += PAGE_SIZE) {
            Page* page = nullptr;
            for (UInt32 p = 0; p < pageCount; ++p) {
                if (pages[p].address == pageAddress) {
                    page = &pages[p];
                    break;
                }
            }
            if (page == nullptr) {
                ready = false;
                continue;
            }
            if (!page->writable) {
                const auto result =
                    vm_protect(map, pageAddress, PAGE_SIZE, FALSE, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
                if (result != KERN_SUCCESS) {
                    error(result);
                    ready = false;
                    continue;
                }
                page->writable = true;
            }
        }
        if (!ready) {
            ok = false;
            continue;
        }
        if (writeUser(saved[i], address, patches[i].size) != 0) { ok = false; }
    }
    if (!restorePages(map, pages, pageCount)) { ok = false; }
    if (!ok) {
        stage(Stage::Rollback);
        error(KERN_FAILURE);
    }
    return ok;
}

int wrappedSharedRegionCheck(proc_t process, SharedRegionCheckArgs* args, int* returnValue)
{
    stage(Stage::Called);
    const auto result = reinterpret_cast<SharedRegionCheck>(originalCheck)(process, args, returnValue);
    if (result != 0) { stage(Stage::OriginalError); return result; }
    if (args == nullptr || args->startAddress == 0) { stage(Stage::Argument); return result; }
    using namespace UserSurfaceSyncData;
    const auto* patches = activePatches();
    const auto  patchCount = computeScratch ? PatchCount : (legacyBlend ? LegacyPatchCount : BaselinePatchCount);
    UInt64      base{0};
    UInt8       header[104];
    if (readUser(args->startAddress, &base, sizeof(base)) != 0 || base == 0
        || base >= UserSurfaceSyncResolver::UserLimit - sizeof(header) || (base & (PAGE_SIZE - 1)) != 0
        || readUser(base, header, sizeof(header)) != 0)
    {
        stage(Stage::CacheRead);
        return result;
    }
    if (memcmp(header, CacheMagic, sizeof(CacheMagic)) != 0) {
        stage(Stage::CacheMismatch);
        return result;
    }
    const bool                    dynamic = memcmp(header + 88, CacheUUID, sizeof(CacheUUID)) != 0;
    UserSurfaceSyncResolver::Plan plan;
    const char* const             uuidKeys[] = {"NRedImmediateSyncCacheUUID0", "NRedImmediateSyncCacheUUID1",
                                                "NRedImmediateSyncCacheUUID2", "NRedImmediateSyncCacheUUID3"};
    for (UInt32 i = 0; i < 4; ++i) {
        UInt32 value;
        memcpy(&value, header + 88 + i * 4, sizeof(value));
        NRed::singleton().setProp32(uuidKeys[i], value);
    }
    if (dynamic) {
        if (!UserSurfaceSyncResolver::resolve(readUser, base, *devicePatches, srdShared, legacyBlend, plan,
                                              computeScratch))
        {
            stage(Stage::CacheMismatch);
            NRed::singleton().setProp32("NRedImmediateSyncDynamicRejected", 1);
            return result;
        }
        patches = plan.patches;
        if (!legacyBlend) {
            UInt8 gate[sizeof(BlendGateOriginal)];
            if (readUser(base + plan.patches[2].offset, gate, sizeof(gate)) != 0
                || memcmp(gate, BlendGateOriginal, sizeof(gate)) != 0)
            {
                stage(Stage::CodeMismatch);
                return result;
            }
        }
        NRed::singleton().setProp32("NRedImmediateSyncDynamicMatched", 1);
        NRed::singleton().setProp32("NRedImmediateSyncDynamicReads", plan.reads);
    }

    // 禁止在残留另一策略的进程中把旧模式误报为已生效；切换参数仍需重启。
    if (!dynamic && !computeScratch) {
        UInt8 ctx[MaxPatchSize];
        for (UInt32 i = patchCount; i < PatchCount; ++i) {
            const auto& extra = ComputeScratchPatches[i];
            if (readUser(base + extra.offset, ctx, extra.size) != 0 || memcmp(ctx, extra.original, extra.size) != 0)
            {
                stage(Stage::CodeMismatch);
                return result;
            }
        }
    }
    // 旧候选的两处全局 VT 跳转已撤回；不把混合策略误报为新模式已生效。
    if (computeScratch && !dynamic) {
        for (const auto& withdrawn : WithdrawnTransferGuards) {
            UInt8 bytes[6];
            if (readUser(base + withdrawn.offset, bytes, withdrawn.size) != 0
                || memcmp(bytes, withdrawn.original, withdrawn.size) != 0)
            {
                stage(Stage::CodeMismatch);
                return result;
            }
        }
    }
    UInt8  saved[PatchCount][MaxPatchSize];
    Page   pages[MaxPageCount];
    UInt32 pageCount{0}, originals{0}, patched{0};
    for (UInt32 i = 0; i < patchCount; ++i) {
        if (patches[i].size == 0 || patches[i].size > MaxPatchSize
            || patches[i].offset >= UserSurfaceSyncResolver::UserLimit - base
            || patches[i].size >= UserSurfaceSyncResolver::UserLimit - base - patches[i].offset)
        {
            stage(Stage::CodeRead);
            return result;
        }
        const auto address = base + patches[i].offset;
        const auto page    = address & ~static_cast<UInt64>(PAGE_SIZE - 1);
        if (readUser(address, saved[i], patches[i].size) != 0) {
            stage(Stage::CodeRead);
            return result;
        }
        if (memcmp(saved[i], patches[i].original, patches[i].size) == 0) { ++originals; }
        else if (memcmp(saved[i], patches[i].patched, patches[i].size) == 0) {
            ++patched;
        }
        else {
            stage(Stage::CodeMismatch);
            error(KERN_INVALID_ARGUMENT);
            return result;
        }
        const auto lastPage = (address + patches[i].size - 1) & ~static_cast<UInt64>(PAGE_SIZE - 1);
        for (auto pageAddress = page; pageAddress <= lastPage; pageAddress += PAGE_SIZE) {
            bool found = false;
            for (UInt32 p = 0; p < pageCount; ++p) {
                if (pages[p].address == pageAddress) {
                    found = true;
                    break;
                }
            }
            if (!found) {
                if (pageCount == MaxPageCount) {
                    stage(Stage::CodeMismatch);
                    return result;
                }
                pages[pageCount++].address = pageAddress;
            }
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
            rollback(map, pages, pageCount, base, patches, saved, i + 1);
            return result;
        }
    }
    if (!restorePages(map, pages, pageCount)) {
        stage(Stage::Restore);
        rollback(map, pages, pageCount, base, patches, saved, patchCount);
        return result;
    }
    stage(Stage::Applied);
    if (dynamic) { NRed::singleton().setProp32("NRedImmediateSyncDynamicApplied", 1); }
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
    NRed::singleton().setProp32("NRedImmediateSyncRevision", 4);
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
    NRed::singleton().setProp32("NRedImmediateSyncRequested", 1);
    const auto deviceID = NRed::singleton().getDeviceID();
    devicePatches       = UserSurfaceSyncData::findDevice(deviceID);
    if (devicePatches == nullptr) {
        NRed::singleton().setProp32("NRedImmediateSyncRejected", 3);
        SYSLOG("UserSurfaceSync", "Unsupported render-fix device: 0x%X", deviceID);
        return;
    }
    // Tahoe 小版本只决定是否进入核验；未知 UUID 必须通过完整驱动内容与引用目标核验。
    if (!currentKernelVersion().majorMatches(MACOS_26)) {
        NRed::singleton().setProp32("NRedImmediateSyncRejected", 4);
        SYSLOG("UserSurfaceSync", "Render fix requires Darwin 25; found %u.%u", currentKernelVersion().major(),
               currentKernelVersion().minor());
        return;
    }
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
    SYSLOG("UserSurfaceSync", "ImmediateSync-v1 revision 4 enabled: verified driver content required, device 0x%X",
           deviceID);
}
