// 在进程私有代码页应用精确版本的 Metal 修复，并保留原生调用与页面保护。
#include <Headers/kern_util.hpp>
#include <IOKit/IOLib.h>
#include <NRed.hpp>
#include <PenguinWizardry/KernelVersion.hpp>
#include <UserLegacyData.hpp>
#include <UserPatchCoordinator.hpp>
#include <UserPatchMemory.hpp>
#include <UserSubmitGuardData.hpp>
#include <UserSurfaceSync.hpp>
#include <UserSurfaceSyncData.hpp>
#include <UserSurfaceSyncResolver.hpp>
#include <VPF/DisplayRuntime.hpp>
#include <VPF/ExecutableTrampolineResource.hpp>
#include <VPF/MetalTransactionExecutor.hpp>
#include <VPF/ResolutionDiagnostics.hpp>
#include <VPF/TransactionExecutor.hpp>
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

struct MappingProbeState
{
    vm_map_t      map;
    RegionRecurse query;
};

// VPF 层不包含 Mach 头；只查询映射属性，避免完整查询逐页统计整个共享缓存区域。
bool probeMapping(void* context, UInt64 address, UInt32 depth, VPF::MappingInfo& info)
{
    const auto&                           state  = *static_cast<MappingProbeState*>(context);
    mach_vm_address_t                     region = address;
    mach_vm_size_t                        regionSize{0};
    natural_t                             regionDepth = depth;
    vm_region_submap_short_info_data_64_t raw{};
    mach_msg_type_number_t                count = VM_REGION_SUBMAP_SHORT_INFO_COUNT_64;
    if (!state.query
        || state.query(state.map, &region, &regionSize, &regionDepth, reinterpret_cast<vm_region_recurse_info_t>(&raw),
                       &count)
               != KERN_SUCCESS
        || count < VM_REGION_SUBMAP_SHORT_INFO_COUNT_64)
    {
        return false;
    }
    info = {region,         regionSize,       regionDepth,        raw.object_id,
            raw.share_mode, raw.shadow_depth, raw.is_submap != 0, (raw.protection & VM_PROT_WRITE) != 0};
    return true;
}

mach_vm_address_t originalCheck{0};
GetTaskMap getTaskMap{nullptr};
RegionRecurse queryRegion{nullptr};
CopyIn readUser{nullptr};
CopyOut writeUser{nullptr};
UInt32 seenStages{0}, appliedCount{0}, errorCount{0}, windowServerCount{0}, weatherCount{0};
UInt32 submitGuardCount{0};
struct Published {
    UInt64 uuid[4], matched, reads, planState, planSites, planVerified;
    UInt64 fvrcDisplay, fvrcDisplayCold, fvrcSubmit, fvrcSubmitCold;
    UInt64 groupsEnabled, groupsRejected, renderState, scratchState, submitState;
} published{};
bool              srdShared{false}, legacyBlend{false}, computeScratch{false}, submitGuard{false};
bool              renderRequested{false};
bool              sequoia{false};
const UserLegacyCompat::Profile*           legacyProfile{nullptr};
const UserSurfaceSyncData::DevicePatchSet* devicePatches{&UserSurfaceSyncData::DevicePatches[0]};

constexpr auto PatchCount = arrsize(UserSurfaceSyncData::ComputeScratchPatches);
constexpr auto LegacyPatchCount = arrsize(UserSurfaceSyncData::LegacyBlendPatches);
constexpr auto BaselinePatchCount = arrsize(UserSurfaceSyncData::Patches);
static_assert(BaselinePatchCount == arrsize(UserSurfaceSyncData::SrdSharedPatches));
static_assert(PatchCount <= 16);
static_assert(UserSurfaceSyncData::MaxPatchSize <= 512);
static_assert(UserSequoiaSurfaceData::MaxPatchSize <= UserSurfaceSyncResolver::Plan::BufferSize);
constexpr UInt32 MaxPageCount = 8;
static_assert(PatchCount * 2 <= MaxPageCount);

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
    // 已记录阶段只读检查，避免每次系统调用都对共享缓存行做原子写。
    if ((__atomic_load_n(&seenStages, __ATOMIC_RELAXED) & (1U << index)) != 0) { return; }
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

// 值不变时跳过 IORegistry 写入；并发写入后复查，保证最终发布的是槽位中的最新值。
void publish(UInt64& last, const char* key, const UInt32 value)
{
    // 高位标记已发布，使首次的 0 值也会写入。
    UInt64 tagged = (1ULL << 32) | value;
    if (__atomic_exchange_n(&last, tagged, __ATOMIC_RELAXED) == tagged) { return; }
    for (;;) {
        NRed::singleton().setProp32(key, static_cast<UInt32>(tagged));
        const auto latest = __atomic_load_n(&last, __ATOMIC_RELAXED);
        if (latest == tagged) { return; }
        tagged = latest;
    }
}

bool leafProtection(vm_map_t map, const mach_vm_address_t page, vm_prot_t& protection)
{
    return UserPatchMemory::leafProtection(map, page, protection, queryRegion, error);
}

// 统一执行器只接收已核验补丁与已收集页面；VM 权限与回滚仍由 UserPatchMemory 完成。
using ExecutorOps = UserPatchMemory::ExecutorOps<CopyIn, CopyOut>;

// 提交守卫的计划/读回缓冲随调用工作区分配，避免在同步 Hook 栈上放完整计划。
struct SubmitGuardWorkspace
{
    VPF::VerifiedPatchPlan plan;
    UInt8                   saved[1][UserSurfaceSyncResolver::Plan::BufferSize];
    UInt8                   observed[sizeof(UserSubmitGuardData::Payload)];
    Page                    pages[1];
};

// 在进程内分配一页可执行守卫，并把 AMD 命令队列的提交方法入口改为绝对跳转。
// 守卫把“已用尾..容量尾”的空闲区整段填成 PM4 filler（池复用后尾部常混有旧命令），
// 随后执行原入口前 15 字节并跳回。
kern_return_t installSubmitGuard(vm_map_t map, const UInt64 base, bool& fresh, SubmitGuardWorkspace& workspace,
                                 VPF::DisplayRuntime<>& runtime, UInt64 offset)
{
    using namespace UserSubmitGuardData;
    fresh = false;
    if (offset == 0 && legacyProfile) { offset = UserLegacyData::submitOffset(legacyProfile->major); }
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
        if (readUser(target, existing, sizeof(existing)) != 0 || memcmp(existing, expected, sizeof(existing)) != 0
            || !leafProtection(map, target, protection) || protection != (VM_PROT_READ | VM_PROT_EXECUTE))
        {
            return KERN_INVALID_ARGUMENT;
        }
        if (!runtime.verify(VPF::featureBit(VPF::FeatureId::DisplaySubmitGuard)
                                | VPF::featureBit(VPF::FeatureId::DisplaySubmit),
                            VPF::ExternalSubmitResolver)
            || !runtime.begin()
            || !runtime.installed(VPF::featureBit(VPF::FeatureId::DisplaySubmitGuard)
                                  | VPF::featureBit(VPF::FeatureId::DisplaySubmit)))
        {
            return KERN_INVALID_ARGUMENT;
        }
        return KERN_SUCCESS;
    }
    if (memcmp(entry, MethodOriginal, sizeof(entry)) != 0) { return KERN_INVALID_ARGUMENT; }

    // 入口补丁由统一执行器完成 COW/读回/RX 恢复；守卫页只作为写前资源创建。
    UInt8 patch[MethodPatchSize];
    memset(patch, 0x90, sizeof(patch));
    memcpy(patch, patchedPrefix, sizeof(patchedPrefix));
    auto& plan = workspace.plan;
    plan.reset(base, UserSharedCache::Range{base, UserSharedCache::UserLimit - base});
    if (!plan.addSite(method, offset, MethodPatchSize, VPF::NoFunction, 0, MethodOriginal, patch)) {
        return KERN_INVALID_ARGUMENT;
    }
    ExecutorOps ops{map, queryRegion, error, readUser, writeUser};
    VPF::MetalTransaction<ExecutorOps, Page, UserSurfaceSyncResolver::Plan::BufferSize, 1, 1> transaction(
        ops, plan, base, workspace.saved, workspace.pages);
    VPF::MetalPrepareResult prepared;
    if (!transaction.prepare(prepared) || prepared.state != VPF::MetalPrepareState::AllOriginal) {
        return KERN_INVALID_ARGUMENT;
    }
    if (!runtime.verify(VPF::featureBit(VPF::FeatureId::DisplaySubmitGuard)
                            | VPF::featureBit(VPF::FeatureId::DisplaySubmit),
                        VPF::ExternalSubmitResolver)
        || !runtime.begin())
    {
        return KERN_INVALID_ARGUMENT;
    }

    struct GuardOwner
    {
        vm_map_t      map;
        vm_address_t  page{0};
        bool          retained{false};
        ~GuardOwner()
        {
            if (page && !retained) { vm_deallocate(map, page, PAGE_SIZE); }
        }
    } owner{map};
    VPF::ExecutableTrampolineResource resource;
    UInt8             payload[sizeof(Payload)];
    kern_return_t     guardFailure{KERN_FAILURE};
    const auto        committed = transaction.commit(
        [&]
        {
            auto status = vm_allocate(map, &owner.page, PAGE_SIZE, VM_FLAGS_ANYWHERE);
            if (status != KERN_SUCCESS) {
                guardFailure = status;
                return false;
            }
            if (!resource.allocate(owner.page)) { return false; }
            memcpy(payload, Payload, sizeof(payload));
            const UInt64 continuation = method + MethodPatchSize;
            memcpy(payload + PayloadJumpOffset, &continuation, sizeof(continuation));
            if (writeUser(payload, owner.page, sizeof(payload)) != 0) { return false; }
            // 入口链接前读回守卫载荷，避免把只写成功但内容错误的页设为可执行入口。
            if (readUser(owner.page, workspace.observed, sizeof(workspace.observed)) != 0
                || memcmp(workspace.observed, payload, sizeof(payload)) != 0)
            {
                return false;
            }
            if (!resource.fill(sizeof(payload))) { return false; }
            status = vm_protect(map, owner.page, PAGE_SIZE, FALSE, VM_PROT_READ | VM_PROT_EXECUTE);
            if (status == KERN_SUCCESS) {
                status = vm_protect(map, owner.page, PAGE_SIZE, TRUE, VM_PROT_READ | VM_PROT_EXECUTE);
            }
            if (status != KERN_SUCCESS || !resource.makeExecutable()) {
                if (status != KERN_SUCCESS) {
                    guardFailure = status;
                    error(status);
                }
                return false;
            }
            // FF 25 00 00 00 00 <目标地址>：RIP 相对取 8 字节绝对目标，14 字节，补 1 字节 NOP。
            const UInt64 target = owner.page;
            memcpy(patch + 6, &target, sizeof(target));
            return resource.linkEntry(method);
        });

    if (committed.status != VPF::TransactionStatus::Installed) {
        runtime.failed(committed.status == VPF::TransactionStatus::CheckFailed
                       || committed.status == VPF::TransactionStatus::BeforeWriteFailed
                       || (committed.status == VPF::TransactionStatus::ProtectionFailed ? committed.restoreOk :
                                                                                          committed.rollbackOk));
        // 只有未写入或已确认回滚的入口才能释放守卫页；否则保留页等待后续核验。
        const bool entryRestored =
            committed.status == VPF::TransactionStatus::CheckFailed
            || committed.status == VPF::TransactionStatus::BeforeWriteFailed
            || committed.status == VPF::TransactionStatus::ProtectionFailed
            || (committed.status == VPF::TransactionStatus::WriteFailed && committed.rollbackOk)
            || (committed.status == VPF::TransactionStatus::ReadbackFailed && committed.rollbackOk)
            || (committed.status == VPF::TransactionStatus::RestoreFailed && committed.rollbackOk);
        if (resource.linked()) { resource.restoreEntry(entryRestored); }
        if (!entryRestored || resource.pageRetained()) {
            owner.retained = true;
            NRed::singleton().setProp32("NRedSubmitGuardRollbackFailed", 1);
        }
        if (committed.status == VPF::TransactionStatus::BeforeWriteFailed) { return guardFailure; }
        return committed.status == VPF::TransactionStatus::CheckFailed
                       || committed.status == VPF::TransactionStatus::ProtectionFailed ?
                   KERN_PROTECTION_FAILURE :
                   KERN_FAILURE;
    }
    if (!resource.verify(true)) {
        runtime.failed(false);
        owner.retained = true;
        NRed::singleton().setProp32("NRedSubmitGuardRollbackFailed", 1);
        return KERN_FAILURE;
    }
    owner.retained = true;
    fresh = true;
    runtime.installed(VPF::featureBit(VPF::FeatureId::DisplaySubmitGuard)
                      | VPF::featureBit(VPF::FeatureId::DisplaySubmit));
    return KERN_SUCCESS;
}

// Per-call storage: never shared between concurrent processes or held across the native syscall.
struct PatchWorkspace
{
    UserSurfaceSyncData::Patch combined[PatchCount];
    UserSurfaceSyncResolver::Plan plan;
    UInt8                         saved[PatchCount][UserSurfaceSyncResolver::Plan::BufferSize];
    VPF::VerifiedPatchPlan        requestPlan;
    SubmitGuardWorkspace          guard;
    Page                          pages[MaxPageCount];
    VPF::FastResolution           fast, submitFast;
    VPF::DisplayRuntime<>         runtime;
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
    const bool dynamic         = !legacyProfile;
    auto&      plan            = workspace.value->plan;
    auto&      input           = plan.input;
    input.reader               = readUser;
    input.scanBuffer           = plan.scan;
    const auto        major    = currentKernelVersion().major();
    const auto        deviceID = NRed::singleton().getDeviceID();
    vm_map_t          map      = getTaskMap(current_task());
    if (!map) {
        stage(Stage::NoMap);
        return result;
    }
    MappingProbeState probeState{map, queryRegion};
    if (dynamic && renderRequested) {
        if (sequoia) {
            using namespace UserSequoiaSurfaceData;
            input.selectImages(base, ResolverImages, arrsize(ResolverImages));
        }
        else if (computeScratch) {
            using namespace ScratchResolverData;
            input.selectImages(base, ResolverImages, arrsize(ResolverImages));
        }
    }
    const char* const             uuidKeys[] = {"NRedImmediateSyncCacheUUID0", "NRedImmediateSyncCacheUUID1",
                                                "NRedImmediateSyncCacheUUID2", "NRedImmediateSyncCacheUUID3"};
    for (UInt32 i = 0; i < 4; ++i) {
        UInt32 value;
        memcpy(&value, header + 88 + i * 4, sizeof(value));
        publish(published.uuid[i], uuidKeys[i], value);
    }
    // Darwin 24/25 始终核验完整提交方法；UUID 只用于诊断和已核验位置缓存。
    UInt64               submitOffset{0};
    auto&                submitFast    = workspace.value->submitFast;
    VPF::FastResolution* submitFastPtr = nullptr;
    if (submitGuard && (sequoia || dynamic)) {
        const UInt32 submitFlags = sequoia ? 1U : 2U;
        submitFast.begin(&header[88], major, VPF::resolutionProfile(deviceID, submitFlags), 0, probeMapping,
                         &probeState);
        submitFastPtr = &submitFast;
    }
    const bool knownCache =
        legacyProfile ?
            memcmp(header + 88, legacyProfile->uuid, 16) == 0 :
            (submitGuard && UserSurfaceSyncResolver::resolveSubmit(input, base, submitOffset, !sequoia, submitFastPtr));
    if (submitFastPtr) {
        publish(published.fvrcSubmit, "NRedFVRCSubmitHot", submitFast.hot ? 1 : 0);
        publish(published.fvrcSubmitCold, "NRedFVRCSubmitCold", submitFast.hot ? 0 : 1);
    }
    if (sequoia && submitGuard && !knownCache) { NRed::singleton().setProp32("NRedSubmitGuardDynamicRejected", 1); }
    if (!renderRequested) { return result; }
    if (legacyProfile && !UserLegacyCompat::matches(readUser, base, header + 88, *legacyProfile)) {
        stage(Stage::CacheMismatch);
        NRed::singleton().setProp32("NRedLegacyBlendRejected", 1);
        return result;
    }
    if (dynamic) {
        auto&        fast         = workspace.value->fast;
        UInt32       displayFlags = sequoia ? 1U : 0U;
        if (srdShared) { displayFlags |= 2U; }
        if (legacyBlend) { displayFlags |= 4U; }
        if (computeScratch) { displayFlags |= 8U; }
        fast.begin(&header[88], major, VPF::resolutionProfile(deviceID, displayFlags), 0, probeMapping, &probeState);
        const bool resolved =
            sequoia ? UserSurfaceSyncResolver::resolveSequoia(input, base, deviceID, plan, &fast) :
                      UserSurfaceSyncResolver::resolve(input, base, *devicePatches, srdShared, legacyBlend, plan,
                                                       computeScratch, &fast);
        publish(published.fvrcDisplay, "NRedFVRCDisplayHot", fast.hot ? 1 : 0);
        publish(published.fvrcDisplayCold, "NRedFVRCDisplayCold", fast.hot ? 0 : 1);
        if (!resolved) {
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
        publish(published.matched, "NRedImmediateSyncDynamicMatched", 1);
        publish(published.reads, "NRedImmediateSyncDynamicReads", plan.reads);
    }

    auto& saved = workspace.value->saved;
    auto& pages = workspace.value->pages;
    const auto cache = dynamic ? input.cache : UserSharedCache::Range{base, UserSharedCache::UserLimit - base};
    auto& requestPlan = workspace.value->requestPlan;
    if (!VPF::adaptDisplayPatches(requestPlan, base, cache, patches, patchCount)) {
        stage(Stage::CodeMismatch);
        error(KERN_INVALID_ARGUMENT);
        return result;
    }
    // 所有匹配和保护检查由统一执行器在读阶段完成；任何 Mixed/Unknown 都不进入写入。
    ExecutorOps ops{map, queryRegion, error, readUser, writeUser};
    VPF::MetalTransaction<ExecutorOps, Page, UserSurfaceSyncResolver::Plan::BufferSize, MaxPageCount, PatchCount>
                            transaction(ops, requestPlan, base, saved, pages);
    VPF::MetalPrepareResult prepared;
    if (!transaction.prepare(prepared)) {
        stage(prepared.fault == VPF::MetalPrepareFault::ReadFailed ? Stage::CodeRead : Stage::CodeMismatch);
        if (prepared.fault == VPF::MetalPrepareFault::Mismatch) { error(KERN_INVALID_ARGUMENT); }
        return result;
    }
    publish(published.planState, "NRedDisplayVPFPlanState", static_cast<UInt32>(prepared.state));
    publish(published.planSites, "NRedDisplayVPFPlanSites", transaction.siteCount());
    if (prepared.state != VPF::MetalPrepareState::AllOriginal && prepared.state != VPF::MetalPrepareState::AllPatched)
    {
        stage(Stage::Mixed);
        return result;
    }
    auto&      runtime = workspace.value->runtime;
    const auto groups  = UserPatchCoordinator::requestedDisplayGroups() & VPF::MetalDisplayGroups;
    runtime.reset(reinterpret_cast<UInt64>(map), major, deviceID, groups);
    runtime.verify(
        runtime.features()
            & ~(VPF::featureBit(VPF::FeatureId::DisplaySubmitGuard) | VPF::featureBit(VPF::FeatureId::DisplaySubmit)),
        VPF::ExternalMetalBridge);
    struct RuntimeReport
    {
        VPF::DisplayRuntime<>& runtime;
        ~RuntimeReport()
        {
            publish(published.groupsEnabled, "NRedDisplayPlannerEnabled", runtime.result().enabled);
            publish(published.groupsRejected, "NRedDisplayPlannerRejected", runtime.result().rejected);
            publish(published.renderState, "NRedDisplayRenderGroupState",
                    static_cast<UInt32>(runtime.state(VPF::DisplayGroup::MetalRenderCore)));
            publish(published.scratchState, "NRedDisplayScratchGroupState",
                    static_cast<UInt32>(runtime.state(VPF::DisplayGroup::MetalScratch)));
            publish(published.submitState, "NRedDisplaySubmitGroupState",
                    static_cast<UInt32>(runtime.state(VPF::DisplayGroup::MetalSubmitSafety)));
        }
    } runtimeReport{runtime};
    if (!knownCache || !submitGuard) { return result; }
    if (knownCache && submitGuard) {
        if (map) {
            bool       fresh{false};
            const auto installed = installSubmitGuard(map, base, fresh, workspace.value->guard, runtime, submitOffset);
            if (installed == KERN_SUCCESS && fresh) {
                const auto pid = process != nullptr ? proc_pid(process) : -1;
                char       name[32]{};
                if (pid > 0) { proc_name(pid, name, sizeof(name)); }
                name[sizeof(name) - 1] = '\0';
                if (strcmp(name, "WindowServer") == 0) {
                    NRed::singleton().setProp32("NRedSubmitGuardWindowServerPID", static_cast<UInt32>(pid));
                    NRed::singleton().setProp32("NRedSubmitGuardWindowServerApplied", 1);
                }
                auto count = __atomic_load_n(&submitGuardCount, __ATOMIC_RELAXED);
                while (count < 16) {
                    if (__atomic_compare_exchange_n(&submitGuardCount, &count, count + 1, false, __ATOMIC_RELAXED,
                                                    __ATOMIC_RELAXED))
                    {
                        NRed::singleton().setProp32("NRedSubmitGuardApplied", count + 1);
                        break;
                    }
                }
                SYSLOG("UserSurfaceSync", "SubmitGuard-v1 installed in pid %d", pid);
            }
            else if (installed != KERN_SUCCESS) {
                NRed::singleton().setProp32("NRedSubmitGuardRejected", static_cast<UInt32>(installed));
                SYSLOG("UserSurfaceSync", "SubmitGuard-v1 not installed: error=0x%X", installed);
                return result;
            }
        }
    }
    if (!runtime.ready()) { return result; }
    if (prepared.state == VPF::MetalPrepareState::AllPatched) {
        runtime.installed(runtime.features());
        publish(published.planVerified, "NRedDisplayVPFPlanVerified", 1);
        stage(Stage::Already);
        return result;
    }
    publish(published.planVerified, "NRedDisplayVPFPlanVerified", 1);
    const auto committed = transaction.commit([&] { return runtime.ready(); });
    if (committed.status != VPF::TransactionStatus::Installed) {
        runtime.failed(committed.status == VPF::TransactionStatus::CheckFailed
                       || committed.status == VPF::TransactionStatus::BeforeWriteFailed
                       || (committed.status == VPF::TransactionStatus::ProtectionFailed ? committed.restoreOk :
                                                                                          committed.rollbackOk));
    }
    switch (committed.status) {
        case VPF::TransactionStatus::CheckFailed:
            stage(Stage::Region);
            return result;
        case VPF::TransactionStatus::BeforeWriteFailed:
            return result;
        case VPF::TransactionStatus::ProtectionFailed:
            stage(Stage::Protection);
            if (!committed.restoreOk) { stage(Stage::Rollback); }
            return result;
        case VPF::TransactionStatus::WriteFailed:
        case VPF::TransactionStatus::ReadbackFailed:
            stage(Stage::Write);
            error(KERN_FAILURE);
            if (!committed.rollbackOk) {
                stage(Stage::Rollback);
                error(KERN_FAILURE);
            }
            return result;
        case VPF::TransactionStatus::RestoreFailed:
            stage(Stage::Restore);
            if (!committed.rollbackOk) {
                stage(Stage::Rollback);
                error(KERN_FAILURE);
            }
            return result;
        case VPF::TransactionStatus::Installed:
            break;
    }
    runtime.installed(runtime.features());
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
    if (UserPatchCoordinator::deferModulePatches()) {
        if (trace) { SYSLOG("UserSurfaceSync", "shared-region call=%u pid=%d coordinated", ticket, pid); }
        return result;
    }
    if (trace) { SYSLOG("UserSurfaceSync", "shared-region call=%u pid=%d patch-enter", ticket, pid); }
    const auto patchedResult = patchSharedRegion(process, args, result);
    if (trace) { SYSLOG("UserSurfaceSync", "shared-region call=%u pid=%d patch-return=%d", ticket, pid, patchedResult); }
    return patchedResult;
}

} // namespace

void UserSurfaceSync::init(KernelPatcher& patcher)
{
    UserPatchCoordinator::clearDisplay();
    VPF::ResolutionDiagnostics::setEnabled(!checkKernelArgument("-NRedNoFVRC"));
    NRed::singleton().setProp32("NRedFVRCEnabled", VPF::ResolutionDiagnostics::enabled() ? 1 : 0);
    if (VPF::ResolutionDiagnostics::enabled()) {
        VPF::resolutionCache.init();
        NRed::singleton().setProp32("NRedFVRCCacheReady", VPF::resolutionCache.ready ? 1 : 0);
    }
    NRed::singleton().setProp32("NRedSharedRegionStackRevision", 1);
    NRed::singleton().setProp32("NRedCacheSectionRevision", 1);
    sequoia         = currentKernelVersion().major() == 24;
    legacyProfile   = sequoia ? nullptr : UserLegacyData::metal(currentKernelVersion().major());
    renderRequested = false;
    submitGuard     = (UserPatchCoordinator::requestedDisplayGroups() & VPF::MetalDisplayGroups) != 0;
    legacyBlend = renderRequested = submitGuard;
    computeScratch = srdShared = submitGuard && (sequoia || currentKernelVersion().majorMatches(MACOS_26));
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
    if (!currentKernelVersion().majorMatches(MACOS_26) && !sequoia && !legacyProfile) {
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
    // 另一个共享缓存模块也成功安装时，由最外层协调器持有唯一 map lease 后回调。
    UserPatchCoordinator::registerDisplay(
        [](void* process, void* args, const int result)
        {
            return patchSharedRegion(reinterpret_cast<proc_t>(process),
                                     reinterpret_cast<SharedRegionCheckArgs*>(args), result);
        });
}
