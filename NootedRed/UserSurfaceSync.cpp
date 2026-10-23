// 在进程私有代码页应用精确版本的 Metal 修复，并保留原生调用与页面保护。
#include <Headers/kern_util.hpp>
#include <IOKit/IOLib.h>
#include <NRed.hpp>
#include <PenguinWizardry/KernelVersion.hpp>
#include <UserLegacyData.hpp>
#include <UserPatchMemory.hpp>
#include <UserSubmitGuardData.hpp>
#include <UserSurfaceSync.hpp>
#include <UserSurfaceSyncData.hpp>
#include <UserSurfaceSyncResolver.hpp>
#include <kern/task.h>
#include <mach/i386/vm_param.h>
#include <mach/vm_map.h>
#include <mach/vm_region.h>
#include <sys/proc.h>

namespace {

struct SharedRegionCheckArgs
{
    user_addr_t startAddress;
};
static_assert(sizeof(SharedRegionCheckArgs) == 8);
using SharedRegionCheck = int (*)(proc_t, SharedRegionCheckArgs*, int*);
using GetTaskMap = vm_map_t (*)(task_t);
using RegionRecurse     = UserPatchMemory::RegionQuery;
using CopyIn = int (*)(user_addr_t, void*, size_t);
using CopyOut = int (*)(const void*, user_addr_t, size_t);

mach_vm_address_t originalCheck{0};
GetTaskMap getTaskMap{nullptr};
RegionRecurse queryRegion{nullptr};
CopyIn readUser{nullptr};
CopyOut writeUser{nullptr};
UInt32 seenStages{0}, appliedCount{0}, errorCount{0}, windowServerCount{0}, weatherCount{0};
UInt32 submitGuardCount{0};
bool              srdShared{false}, legacyBlend{false}, computeScratch{false}, submitGuard{false};
bool              renderRequested{false};
const UserLegacyCompat::Profile*           legacyProfile{nullptr};
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

using UserPatchMemory::Page;

bool leafProtection(vm_map_t map, const mach_vm_address_t page, vm_prot_t& protection)
{
    return UserPatchMemory::leafProtection(map, page, protection, queryRegion, error);
}

bool restorePages(vm_map_t map, Page* pages, const UInt32 count)
{
    return UserPatchMemory::restorePages(map, pages, count, error);
}

// 在进程内分配一页可执行守卫，并把 AMD 命令队列的提交方法入口改为绝对跳转。
// 守卫把“已用尾..容量尾”的空闲区整段填成 PM4 filler（池复用后尾部常混有旧命令），
// 随后执行原入口前 15 字节并跳回。
kern_return_t installSubmitGuard(vm_map_t map, const UInt64 base, bool& fresh)
{
    using namespace UserSubmitGuardData;
    fresh = false;
    const auto offset = legacyProfile ? UserLegacyData::submitOffset(legacyProfile->major) : MethodOffset;
    if (offset == 0 || base >= UserSharedCache::UserLimit || offset >= UserSharedCache::UserLimit - base
        || MethodPatchSize > UserSharedCache::UserLimit - base - offset)
    {
        return KERN_INVALID_ADDRESS;
    }
    const UInt64 method = base + offset;
    UInt8 entry[MethodPatchSize];
    if (readUser(method, entry, sizeof(entry)) != 0) { return KERN_INVALID_ADDRESS; }
    static constexpr UInt8 patchedPrefix[6] = {0xFF, 0x25, 0x00, 0x00, 0x00, 0x00};
    if (memcmp(entry, patchedPrefix, sizeof(patchedPrefix)) == 0) {
        UInt64 target{0};
        memcpy(&target, entry + 6, sizeof(target));
        if (entry[14] != 0x90 || target == 0 || (target & (PAGE_SIZE - 1)) != 0
            || target >= UserSharedCache::UserLimit - sizeof(Payload))
        {
            return KERN_INVALID_ARGUMENT;
        }
        UInt8 existing[sizeof(Payload)], expected[sizeof(Payload)];
        memcpy(expected, Payload, sizeof(expected));
        const UInt64 continuation = method + MethodPatchSize;
        memcpy(expected + PayloadJumpOffset, &continuation, sizeof(continuation));
        vm_prot_t protection{VM_PROT_NONE};
        return readUser(target, existing, sizeof(existing)) == 0 && memcmp(existing, expected, sizeof(existing)) == 0
                       && leafProtection(map, target, protection) && protection == (VM_PROT_READ | VM_PROT_EXECUTE) ?
                   KERN_SUCCESS :
                   KERN_INVALID_ARGUMENT;
    }
    if (memcmp(entry, MethodOriginal, sizeof(entry)) != 0) { return KERN_INVALID_ARGUMENT; }
    const UInt64 methodPage = method & ~static_cast<UInt64>(PAGE_SIZE - 1);
    vm_prot_t    protection{VM_PROT_NONE};
    if ((method & (PAGE_SIZE - 1)) + sizeof(entry) > PAGE_SIZE || !leafProtection(map, methodPage, protection)
        || protection != (VM_PROT_READ | VM_PROT_EXECUTE))
    {
        return KERN_PROTECTION_FAILURE;
    }

    vm_address_t page{0};
    auto result = vm_allocate(map, &page, PAGE_SIZE, VM_FLAGS_ANYWHERE);
    if (result != KERN_SUCCESS) { return result; }

    UInt8 payload[sizeof(Payload)];
    memcpy(payload, Payload, sizeof(payload));
    const UInt64 continuation = method + MethodPatchSize;
    memcpy(payload + PayloadJumpOffset, &continuation, sizeof(continuation));
    if (writeUser(payload, page, sizeof(payload)) != 0) {
        vm_deallocate(map, page, PAGE_SIZE);
        return KERN_FAILURE;
    }
    result = vm_protect(map, page, PAGE_SIZE, FALSE, VM_PROT_READ | VM_PROT_EXECUTE);
    if (result == KERN_SUCCESS) { result = vm_protect(map, page, PAGE_SIZE, TRUE, VM_PROT_READ | VM_PROT_EXECUTE); }
    if (result != KERN_SUCCESS) {
        vm_deallocate(map, page, PAGE_SIZE);
        return result;
    }

    // FF 25 00 00 00 00 <目标地址>：RIP 相对取 8 字节绝对目标，14 字节，补 1 字节 NOP。
    UInt8 patch[MethodPatchSize];
    memset(patch, 0x90, sizeof(patch));
    memcpy(patch, patchedPrefix, sizeof(patchedPrefix));
    const UInt64 target = page;
    memcpy(patch + 6, &target, sizeof(target));

    result = vm_protect(map, methodPage, PAGE_SIZE, FALSE, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
    if (result != KERN_SUCCESS) {
        vm_deallocate(map, page, PAGE_SIZE);
        return result;
    }
    const auto written = writeUser(patch, method, sizeof(patch));
    bool       rolledBack = false;
    if (written != 0) { rolledBack = writeUser(entry, method, sizeof(entry)) == 0; }
    auto restored = vm_protect(map, methodPage, PAGE_SIZE, FALSE, protection);
    if (restored != KERN_SUCCESS) {
        restored = vm_protect(map, methodPage, PAGE_SIZE, FALSE, protection);
    }
    if (restored == KERN_SUCCESS) {
        const auto maximum = vm_protect(map, methodPage, PAGE_SIZE, TRUE, protection);
        if (maximum != KERN_SUCCESS) {
            error(maximum);
            restored = maximum;
        }
    }
    if (written != 0 || restored != KERN_SUCCESS) {
        if (!rolledBack
            && vm_protect(map, methodPage, PAGE_SIZE, FALSE, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY)
                   == KERN_SUCCESS)
        {
            rolledBack = writeUser(entry, method, sizeof(entry)) == 0;
        }
        auto recovered = vm_protect(map, methodPage, PAGE_SIZE, FALSE, protection);
        if (recovered != KERN_SUCCESS) { recovered = vm_protect(map, methodPage, PAGE_SIZE, FALSE, protection); }
        if (recovered == KERN_SUCCESS) { recovered = vm_protect(map, methodPage, PAGE_SIZE, TRUE, protection); }
        if (rolledBack) { vm_deallocate(map, page, PAGE_SIZE); }
        // 无法确认入口恢复时保留目标页，避免残留跳转指向已释放内存。
        if (!rolledBack || recovered != KERN_SUCCESS) {
            NRed::singleton().setProp32("NRedSubmitGuardRollbackFailed", 1);
        }
        return KERN_FAILURE;
    }
    fresh = true;
    return KERN_SUCCESS;
}

bool rollback(vm_map_t map, Page* pages, const UInt32 pageCount, const UInt64 base,
              const UserSurfaceSyncData::Patch* patches,
              const UInt8 saved[PatchCount][UserSurfaceSyncData::MaxPatchSize], const UInt32 touched)
{
    using namespace UserSurfaceSyncData;
    bool ok = true;
    // 最后安装的调用跳转先恢复，再撤回其目标代码。
    for (UInt32 remaining = touched; remaining > 0; --remaining) {
        const auto i = remaining - 1;
        const auto address = base + patches[i].offset;
        const bool ready   = UserPatchMemory::prepareRollback(map, pages, pageCount, address, patches[i].size, error);
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

// Per-call storage: never shared between concurrent processes or held across the native syscall.
struct PatchWorkspace
{
    UserSurfaceSyncData::Patch combined[PatchCount];
    UserSurfaceSyncResolver::Plan plan;
    UInt8 saved[PatchCount][UserSurfaceSyncData::MaxPatchSize];
    UInt8 context[UserSurfaceSyncData::MaxPatchSize];
    Page pages[MaxPageCount];
};

struct WorkspaceBuffer
{
    PatchWorkspace* value{nullptr};
    WorkspaceBuffer()
    {
        value = IONew(PatchWorkspace, 1);
        if (value) { memset(value, 0, sizeof(*value)); }
    }
    ~WorkspaceBuffer() { if (value) { IODelete(value, PatchWorkspace, 1); } }
    WorkspaceBuffer(const WorkspaceBuffer&) = delete;
    WorkspaceBuffer& operator=(const WorkspaceBuffer&) = delete;
};

// Keep this frame out of wrappedSharedRegionCheck, including in optimized Release builds.
__attribute__((noinline))
int patchSharedRegion(proc_t process, SharedRegionCheckArgs* args, const int result)
{
    if (args == nullptr || args->startAddress == 0) { stage(Stage::Argument); return result; }
    using namespace UserSurfaceSyncData;
    const auto* patches = activePatches();
    UInt32      patchCount =
        legacyProfile ?
                 legacyProfile->patchCount :
                 static_cast<UInt32>(computeScratch ? PatchCount : (legacyBlend ? LegacyPatchCount : BaselinePatchCount));
    WorkspaceBuffer workspace;
    if (!workspace.value) {
        error(KERN_RESOURCE_SHORTAGE);
        return result;
    }
    auto& combined = workspace.value->combined;
    if (legacyProfile) {
        if (patchCount > PatchCount) { return result; }
        for (UInt32 i = 0; i < patchCount; ++i) {
            const auto& patch = legacyProfile->patches[i];
            combined[i]       = {patch.offset, patch.original, patch.patched, patch.size};
        }
        patches = combined;
    }
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
    const bool                    dynamic = !legacyProfile && memcmp(header + 88, CacheUUID, sizeof(CacheUUID)) != 0;
    auto& plan = workspace.value->plan;
    const char* const             uuidKeys[] = {"NRedImmediateSyncCacheUUID0", "NRedImmediateSyncCacheUUID1",
                                                "NRedImmediateSyncCacheUUID2", "NRedImmediateSyncCacheUUID3"};
    for (UInt32 i = 0; i < 4; ++i) {
        UInt32 value;
        memcpy(&value, header + 88 + i * 4, sizeof(value));
        NRed::singleton().setProp32(uuidKeys[i], value);
    }
    // 提交守卫不依赖渲染修复模式，但只在缓存与核验偏移一致的进程里安装。
    const bool knownCache = legacyProfile ? memcmp(header + 88, legacyProfile->uuid, 16) == 0 : !dynamic;
    if (knownCache && submitGuard) {
        if (auto map = getTaskMap(current_task())) {
            bool fresh{false};
            const auto installed = installSubmitGuard(map, base, fresh);
            if (installed == KERN_SUCCESS && fresh) {
                const auto pid = process != nullptr ? proc_pid(process) : -1;
                char name[32]{};
                if (pid > 0) { proc_name(pid, name, sizeof(name)); }
                name[sizeof(name) - 1] = '\0';
                if (strcmp(name, "WindowServer") == 0) {
                    NRed::singleton().setProp32("NRedSubmitGuardWindowServerPID", static_cast<UInt32>(pid));
                    NRed::singleton().setProp32("NRedSubmitGuardWindowServerApplied", 1);
                }
                auto count = __atomic_load_n(&submitGuardCount, __ATOMIC_RELAXED);
                while (count < 16) {
                    if (__atomic_compare_exchange_n(&submitGuardCount, &count, count + 1, false,
                                                    __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
                        NRed::singleton().setProp32("NRedSubmitGuardApplied", count + 1);
                        break;
                    }
                }
                SYSLOG("UserSurfaceSync", "SubmitGuard-v1 installed in pid %d", pid);
            } else if (installed != KERN_SUCCESS) {
                NRed::singleton().setProp32("NRedSubmitGuardRejected", static_cast<UInt32>(installed));
                SYSLOG("UserSurfaceSync", "SubmitGuard-v1 not installed: error=0x%X", installed);
            }
        }
    }
    if (!renderRequested) { return result; }
    if (legacyProfile && !UserLegacyCompat::matches(readUser, base, header + 88, *legacyProfile)) {
        stage(Stage::CacheMismatch);
        NRed::singleton().setProp32("NRedLegacyBlendRejected", 1);
        return result;
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
    if (!legacyProfile && !dynamic && !computeScratch) {
        auto& ctx = workspace.value->context;
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
    auto& saved = workspace.value->saved;
    auto& pages = workspace.value->pages;
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
        if (!UserPatchMemory::collectPages(pages, pageCount, address, patches[i].size)) {
            stage(Stage::CodeMismatch);
            return result;
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

// Only the first eight calls are traced, even in RELEASE; no per-call IORegistry writes.
UInt32 hookTraceCount{0};
__attribute__((noinline))
int wrappedSharedRegionCheck(proc_t process, SharedRegionCheckArgs* args, int* returnValue)
{
    auto ticket = __atomic_load_n(&hookTraceCount, __ATOMIC_RELAXED);
    bool trace{false};
    while (ticket < 8) {
        if (__atomic_compare_exchange_n(&hookTraceCount, &ticket, ticket + 1, false,
                                        __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
            trace = true;
            break;
        }
    }
    const auto pid = trace && process != nullptr ? proc_pid(process) : -1;
    stage(Stage::Called);
    if (trace) { SYSLOG("UserSurfaceSync", "shared-region call=%u pid=%d original-enter", ticket, pid); }
    const auto result = reinterpret_cast<SharedRegionCheck>(originalCheck)(process, args, returnValue);
    if (trace) { SYSLOG("UserSurfaceSync", "shared-region call=%u pid=%d original-return=%d", ticket, pid, result); }
    if (result != 0) { stage(Stage::OriginalError); return result; }
    if (trace) { SYSLOG("UserSurfaceSync", "shared-region call=%u pid=%d patch-enter", ticket, pid); }
    const auto patchedResult = patchSharedRegion(process, args, result);
    if (trace) { SYSLOG("UserSurfaceSync", "shared-region call=%u pid=%d patch-return=%d", ticket, pid, patchedResult); }
    return patchedResult;
}

} // namespace

void UserSurfaceSync::init(KernelPatcher& patcher)
{
    NRed::singleton().setProp32("NRedSharedRegionStackRevision", 1);
    legacyProfile   = UserLegacyData::metal(currentKernelVersion().major());
    renderRequested = false;
    submitGuard     = checkKernelArgument("-NRedDisplayFix");
    legacyBlend = renderRequested = submitGuard;
    computeScratch = srdShared = submitGuard && currentKernelVersion().majorMatches(MACOS_26);
    NRed::singleton().setProp32("NRedDisplayFixVersion", 2);
    NRed::singleton().setProp32("NRedSubmitGuardVersion", 1);
    NRed::singleton().setProp32("NRedSubmitGuardRevision", 6);
    NRed::singleton().setProp32("NRedImmediateSyncVersion", 1);
    NRed::singleton().setProp32("NRedImmediateSyncRevision", 4);
    NRed::singleton().setProp32("NRedSrdSharedVersion", 1);
    NRed::singleton().setProp32("NRedLegacyBlendVersion", 1);
    NRed::singleton().setProp32("NRedComputeScratchVersion", 1);
    if (!renderRequested) {
        NRed::singleton().setProp32("NRedSubmitGuardDisabled", 1);
        return;
    }
    NRed::singleton().setProp32("NRedImmediateSyncRequested", 1);
    const auto deviceID = NRed::singleton().getDeviceID();
    devicePatches       = UserSurfaceSyncData::findDevice(deviceID);
    if (devicePatches == nullptr) {
        NRed::singleton().setProp32("NRedImmediateSyncRejected", 3);
        SYSLOG("UserSurfaceSync", "Unsupported render-fix device: 0x%X", deviceID);
        return;
    }
    if (!currentKernelVersion().majorMatches(MACOS_26) && !legacyProfile) {
        NRed::singleton().setProp32("NRedImmediateSyncRejected", 4);
        SYSLOG("UserSurfaceSync", "Metal fix requires Darwin 23/24/25; found %u.%u", currentKernelVersion().major(),
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
    NRed::singleton().setProp32("NRedDisplayFixEnabled", 1);
    NRed::singleton().setProp32("NRedImmediateSyncDisabled", 1);
    NRed::singleton().setProp32("NRedImmediateSyncRegionQueryVersion", 1);
    if (submitGuard) {
        NRed::singleton().setProp32("NRedSubmitGuardEnabled", 1);
        SYSLOG("UserSurfaceSync", "SubmitGuard-v1 enabled: command-buffer free tails become PM4 fillers");
    }
    if (srdShared) {
        NRed::singleton().setProp32("NRedSrdSharedEnabled", 1);
        SYSLOG("UserSurfaceSync", "SrdShared enabled with native synchronization: exact cache/code required");
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
    SYSLOG("UserSurfaceSync", "Metal hooks enabled: Darwin %u, device 0x%X, render=%u submit=%u",
           currentKernelVersion().major(), deviceID, renderRequested, submitGuard);
}
