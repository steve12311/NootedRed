// 在进程私有代码页应用已核验的视频解码补丁，并保留原生调用与页面保护。
#include <Headers/kern_util.hpp>
#include <IOKit/IOLib.h>
#include <NRed.hpp>
#include <PenguinWizardry/KernelVersion.hpp>
#include <UserPatchCoordinator.hpp>
#include <UserPatchMapGate.hpp>
#include <UserPatchMemory.hpp>
#include <UserPatchPolicy.hpp>
#include <UserVideoDecode.hpp>
#include <UserVideoDecodeData.hpp>
#include <UserVideoDecodeResolver.hpp>
#include <VCN.hpp>
#include <VCNCapabilities.hpp>
#include <VPF/CapabilityState.hpp>
#include <VPF/DependencyPlanner.hpp>
#include <VPF/PatchManifestData.hpp>
#include <VPF/ResolutionDiagnostics.hpp>
#include <VPF/ResolverAdapters.hpp>
#include <VPF/TransactionExecutor.hpp>
#include <kern/task.h>
#include <mach/i386/vm_param.h>
#include <mach/vm_map.h>
#include <mach/vm_region.h>
#include <sys/proc.h>

namespace
{

    struct SharedRegionCheckArgs
    {
        user_addr_t startAddress;
    };
    static_assert(sizeof(SharedRegionCheckArgs) == 8);
    using SharedRegionCheck = int (*)(proc_t, SharedRegionCheckArgs*, int*);
    using GetTaskMap        = vm_map_t   (*)(task_t);
    using RegionRecurse     = UserPatchMemory::RegionQuery;
    using CopyIn            = int      (*)(user_addr_t, void*, size_t);
    using CopyOut           = int     (*)(const void*, user_addr_t, size_t);
    using MapDestroy        = void (*)(vm_map_t);

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
            || state.query(state.map, &region, &regionSize, &regionDepth,
                           reinterpret_cast<vm_region_recurse_info_t>(&raw), &count)
                   != KERN_SUCCESS
            || count < VM_REGION_SUBMAP_SHORT_INFO_COUNT_64)
        {
            return false;
        }
        info = {region,         regionSize,       regionDepth,        raw.object_id,
                raw.share_mode, raw.shadow_depth, raw.is_submap != 0, (raw.protection & VM_PROT_WRITE) != 0};
        return true;
    }

    mach_vm_address_t              originalCheck{0};
    MapDestroy                     originalMapDestroy{nullptr};
    GetTaskMap                     getTaskMap{nullptr};
    RegionRecurse                  queryRegion{nullptr};
    CopyIn                         readUser{nullptr};
    CopyOut                        writeUser{nullptr};
    IOLock*                        guardLock{nullptr};
    UserPatchMapGate::Registry<64> taskPatchRegistry;
    UInt32                         taskGatePeakReported{0};
    UInt32                         seenStages{0}, appliedCount{0}, errorCount{0};
    struct Published
    {
        UInt64 uuid[4], matched, reads, path, planState, ledgerState, planner;
        UInt64 fvrcDecode, fvrcDecodeCold;
    } published{};
    // 调用方持有 guardLock；每个存活 map 保留独立状态，销毁时释放。
    class VideoLedger
    {
        struct Entry
        {
            UInt64               map;
            VPF::FeatureId       feature;
            VPF::CapabilityState state;
            Entry*               next;
        };
        Entry* entries{nullptr};

    public:
        VideoLedger()                              = default;
        VideoLedger(const VideoLedger&)            = delete;
        VideoLedger& operator=(const VideoLedger&) = delete;
        ~VideoLedger() { clear(); }

        VPF::CapabilityState state(const UInt64 map, const VPF::FeatureId feature) const
        {
            for (auto* entry = entries; entry; entry = entry->next) {
                if (entry->map == map && entry->feature == feature) { return entry->state; }
            }
            return VPF::CapabilityState::Disabled;
        }

        bool set(const UInt64 map, const VPF::FeatureId feature, const VPF::CapabilityState target)
        {
            if (!map) { return false; }
            for (auto* entry = entries; entry; entry = entry->next) {
                if (entry->map == map && entry->feature == feature) {
                    if (!VPF::canTransition(entry->state, target)) { return false; }
                    entry->state = target;
                    return true;
                }
            }
            if (!VPF::canTransition(VPF::CapabilityState::Disabled, target)) { return false; }
            auto* entry = IONew(Entry, 1);
            if (!entry) { return false; }
            // IONew 不调用构造函数，必须完整初始化后才能发布。
            *entry  = {map, feature, target, entries};
            entries = entry;
            return true;
        }

        void removeMap(const UInt64 map)
        {
            auto** link = &entries;
            while (*link) {
                auto* entry = *link;
                if (entry->map == map) {
                    *link = entry->next;
                    IODelete(entry, Entry, 1);
                }
                else {
                    link = &entry->next;
                }
            }
        }

        void clear()
        {
            while (entries) {
                auto* entry = entries;
                entries     = entry->next;
                IODelete(entry, Entry, 1);
            }
        }
    };
    VideoLedger                       videoLedger;
    bool                              sequoia{false};
    const UserVideoDecodeData::Patch* devicePatches{nullptr};
    constexpr auto                    PatchCount   = arrsize(UserVideoDecodeData::Patches);
    constexpr UInt32                  Capacity     = PatchCount;
    constexpr UInt32                  MaxPageCount = Capacity * 2;
    static_assert(PatchCount <= 32);
    static_assert(arrsize(UserSequoiaVideoData::Patches) == PatchCount);
    static_assert(UserSequoiaVideoData::MaxPatchSize <= UserVideoDecodeData::MaxPatchSize);

    UInt32 fairPlayMask()
    {
        return sequoia ? UserSequoiaVideoData::FairPlayDecodeMask : UserVideoDecodeData::FairPlayDecodeMask;
    }

    enum class Stage : UInt32
    {
        Called,
        OriginalError,
        Argument,
        CacheRead,
        CacheMismatch,
        CodeRead,
        CodeMismatch,
        Already,
        Mixed,
        NoMap,
        Region,
        Protection,
        Write,
        Restore,
        Rollback,
        Applied
    };

    void stage(const Stage value, const UInt32 fairPlayPID = 0)
    {
        static const char* const keys[] = {"NRedVCNUserD00", "NRedVCNUserD01", "NRedVCNUserD02", "NRedVCNUserD03",
                                           "NRedVCNUserD04", "NRedVCNUserD05", "NRedVCNUserD06", "NRedVCNUserD07",
                                           "NRedVCNUserD08", "NRedVCNUserD09", "NRedVCNUserD10", "NRedVCNUserD11",
                                           "NRedVCNUserD12", "NRedVCNUserD13", "NRedVCNUserD14", "NRedVCNUserD15"};
        const auto               index  = static_cast<UInt32>(value);
        if (fairPlayPID != 0) {
            NRed::singleton().setProp32("NRedDRMVideoLastPID", fairPlayPID);
            NRed::singleton().setProp32("NRedDRMVideoLastStage", index);
        }
        // 已记录阶段只读检查，避免每次系统调用都对共享缓存行做原子写。
        if ((__atomic_load_n(&seenStages, __ATOMIC_RELAXED) & (1U << index)) != 0) { return; }
        if ((__atomic_fetch_or(&seenStages, 1U << index, __ATOMIC_RELAXED) & (1U << index)) == 0) {
            NRed::singleton().setProp32(keys[index], 1);
            if (value == Stage::Applied) { NRed::singleton().setProp32("NRedVCNGVASelectionFix", 1); }
            SYSLOG("UserVideoDecode", "stage=%u", index);
        }
    }

    void error(const kern_return_t value)
    {
        auto count = __atomic_load_n(&errorCount, __ATOMIC_RELAXED);
        while (count < 8) {
            if (__atomic_compare_exchange_n(&errorCount, &count, count + 1, false, __ATOMIC_RELAXED, __ATOMIC_RELAXED))
            {
                NRed::singleton().setProp32("NRedVCNUserLastError", static_cast<UInt32>(value));
                SYSLOG("UserVideoDecode", "rejected: error=0x%X", value);
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

    constexpr UInt32 darwinMask(const UInt32 major)
    {
        return major == 24 ? VPF::OsDarwin24 : (major == 25 ? VPF::OsDarwin25 : 0);
    }

    bool addPlanImages(VPF::VerifiedPatchPlan& out, const VPF::VerifiedPatchPlan& source)
    {
        for (UInt32 i = 0; i < source.imageCount(); ++i) {
            const auto& image = source.image(i);
            if (!out.addImage(image.path, image.address, image.text, image.segments, image.segmentCount)) {
                return false;
            }
        }
        return true;
    }

    bool appendPlanSites(VPF::VerifiedPatchPlan& out, const VPF::VerifiedPatchPlan& source)
    {
        if (out.siteCount() + source.siteCount() > VPF::MaxPlanSites) { return false; }
        for (UInt32 i = 0; i < source.siteCount(); ++i) {
            const auto& site = source.site(i);
            if (!out.addSite(site.address, site.offset, site.size, site.functionIndex, site.instructionOffset,
                             site.original, site.replacement))
            {
                return false;
            }
        }
        return true;
    }

    // 规划器只判断功能组前提；补丁选择仍由现有策略和 FairPlay 掩码给出。
    bool planVideoFeatures(const UInt32 osMask, const UInt32 deviceID, const bool fairPlay, const bool decodeVerified,
                           VPF::PlannerResult& result)
    {
        VPF::PlannerInput input{};
        input.osMask   = osMask;
        input.deviceID = deviceID;
        input.requested =
            VPF::featureBit(VPF::FeatureId::Vcn2Decode)
            | (fairPlay ?
                   VPF::featureBit(VPF::FeatureId::FairPlayNativeDecodeSubset) :
                   (VPF::featureBit(VPF::FeatureId::Vcn2Encode) | VPF::featureBit(VPF::FeatureId::AppleGVASelection)));
        input.verified = decodeVerified ? (VPF::featureBit(VPF::FeatureId::Vcn2Decode)
                                           | VPF::featureBit(VPF::FeatureId::Vcn2Encode)
                                           | VPF::featureBit(VPF::FeatureId::AppleGVASelection)
                                           | VPF::featureBit(VPF::FeatureId::FairPlayNativeDecodeSubset)) :
                                          0;
        input.external = VPF::ExternalVcnBridge | (fairPlay ? VPF::ExternalFairPlay : 0);
        result         = VPF::plan(input);
        return !result.cycle && (result.enabled & input.requested) == input.requested;
    }

    VPF::CapabilityState ledgerState(const UInt64 map)
    {
        if (!guardLock || map == 0) { return VPF::CapabilityState::Disabled; }
        IOLockLock(guardLock);
        const auto state = videoLedger.state(map, VPF::FeatureId::Vcn2Decode);
        IOLockUnlock(guardLock);
        return state;
    }

    void publishLedger(const UInt64 map)
    {
        publish(published.ledgerState, "NRedVCNVPFLedgerState", static_cast<UInt32>(ledgerState(map)));
    }

    // 返回 false 时不得再读取或写入目标页面：隔离状态和分配失败均拒绝安装。
    bool prepareLedger(const UInt64 map)
    {
        if (!guardLock || map == 0) { return false; }
        constexpr auto feature = VPF::FeatureId::Vcn2Decode;
        IOLockLock(guardLock);
        auto state = videoLedger.state(map, feature);
        if (state == VPF::CapabilityState::Quarantined) {
            IOLockUnlock(guardLock);
            publishLedger(map);
            return false;
        }
        // 同一 map 可能因缓存重建再次进入安装阶段；已安装/上次中断的状态先回到 Disabled。
        if ((state == VPF::CapabilityState::Installed || state == VPF::CapabilityState::RuntimeObserved)
            && videoLedger.set(map, feature, VPF::CapabilityState::Disabled))
        {
            state = VPF::CapabilityState::Disabled;
        }
        if (state == VPF::CapabilityState::Installing
            && videoLedger.set(map, feature, VPF::CapabilityState::CommitFailed))
        {
            state = VPF::CapabilityState::CommitFailed;
        }
        if (state == VPF::CapabilityState::CommitFailed
            && videoLedger.set(map, feature, VPF::CapabilityState::RollbackVerified))
        {
            state = VPF::CapabilityState::RollbackVerified;
        }
        if (state == VPF::CapabilityState::RollbackVerified
            && videoLedger.set(map, feature, VPF::CapabilityState::Disabled))
        {
            state = VPF::CapabilityState::Disabled;
        }
        if (state == VPF::CapabilityState::Disabled && videoLedger.set(map, feature, VPF::CapabilityState::Eligible)) {
            state = VPF::CapabilityState::Eligible;
        }
        if (state == VPF::CapabilityState::Eligible
            && videoLedger.set(map, feature, VPF::CapabilityState::ImageResolved))
        {
            state = VPF::CapabilityState::ImageResolved;
        }
        if (state == VPF::CapabilityState::ImageResolved
            && videoLedger.set(map, feature, VPF::CapabilityState::Verified))
        {
            state = VPF::CapabilityState::Verified;
        }
        if (state == VPF::CapabilityState::Verified && videoLedger.set(map, feature, VPF::CapabilityState::Prepared)) {
            state = VPF::CapabilityState::Prepared;
        }
        if (state == VPF::CapabilityState::RollbackFailed) {
            videoLedger.set(map, feature, VPF::CapabilityState::Quarantined);
            state = VPF::CapabilityState::Quarantined;
        }
        const bool ready = state == VPF::CapabilityState::Prepared || state == VPF::CapabilityState::Installing
                           || state == VPF::CapabilityState::Installed
                           || state == VPF::CapabilityState::RuntimeObserved;
        IOLockUnlock(guardLock);
        publishLedger(map);
        return ready;
    }

    bool markLedger(const UInt64 map, const VPF::CapabilityState target)
    {
        if (!guardLock || map == 0) { return false; }
        IOLockLock(guardLock);
        const bool changed = videoLedger.set(map, VPF::FeatureId::Vcn2Decode, target);
        IOLockUnlock(guardLock);
        publishLedger(map);
        return changed;
    }

    void markFailure(const UInt64 map, const bool rollbackOk)
    {
        if (!guardLock || map == 0) { return; }
        constexpr auto feature = VPF::FeatureId::Vcn2Decode;
        bool           quarantined{false};
        IOLockLock(guardLock);
        auto state = videoLedger.state(map, feature);
        if (state == VPF::CapabilityState::Installing
            && videoLedger.set(map, feature, VPF::CapabilityState::CommitFailed))
        {
            state = VPF::CapabilityState::CommitFailed;
        }
        if (state == VPF::CapabilityState::CommitFailed) {
            if (rollbackOk) { videoLedger.set(map, feature, VPF::CapabilityState::RollbackVerified); }
            else if (videoLedger.set(map, feature, VPF::CapabilityState::RollbackFailed)) {
                videoLedger.set(map, feature, VPF::CapabilityState::Quarantined);
                quarantined = true;
            }
        }
        IOLockUnlock(guardLock);
        if (quarantined) { NRed::singleton().setProp32("NRedVCNVPFQuarantined", 1); }
        publishLedger(map);
    }

    void observeLedger(const UInt64 map)
    {
        markLedger(map, VPF::CapabilityState::Installing);
        markLedger(map, VPF::CapabilityState::Installed);
        markLedger(map, VPF::CapabilityState::RuntimeObserved);
    }

    void installedLedger(const UInt64 map)
    {
        markLedger(map, VPF::CapabilityState::Installing);
        markLedger(map, VPF::CapabilityState::Installed);
    }

    void removeLedgerMap(vm_map_t map)
    {
        if (!guardLock || !map) { return; }
        IOLockLock(guardLock);
        videoLedger.removeMap(reinterpret_cast<UInt64>(map));
        IOLockUnlock(guardLock);
    }

    // 统一执行器只接收已核验补丁与已收集页面；VM 权限与回滚仍由 UserPatchMemory 完成。
    using ExecutorOps = UserPatchMemory::ExecutorOps<CopyIn, CopyOut>;

    struct SavedRow
    {
        UInt8 bytes[UserVideoDecodeData::MaxPatchSize];
    };
    static_assert(sizeof(SavedRow) == UserVideoDecodeData::MaxPatchSize);
    struct TransactionState
    {
        UserVideoDecodeData::Patch selected[PatchCount]{}, install[Capacity]{};
        VPF::VerifiedPatchPlan     decodePlan{}, installPlan{};
        VPF::PlannerResult         planner{};
        Page                       pages[MaxPageCount]{};
        UserSharedCache::Input     input{nullptr, {}};
        UInt8                      scan[UserSharedCache::Input::ScanSize];
        VPF::FastResolution        fast;
    };
    struct TransactionBuffer
    {
        TransactionState* state{nullptr};
        TransactionBuffer()
        {
            state = IONew(TransactionState, 1);
            if (state) { memset(state, 0, sizeof(*state)); }
        }
        ~TransactionBuffer()
        {
            if (state) { IODelete(state, TransactionState, 1); }
        }
    };
    struct SavedBuffer
    {
        SavedRow* rows{nullptr};
        SavedBuffer() { rows = IONew(SavedRow, Capacity); }
        ~SavedBuffer()
        {
            if (rows) { IODelete(rows, SavedRow, Capacity); }
        }
    };

    // 已安装页面的权限检查；与编解码拦截无关，只验证 RX 安装状态。
    bool executablePage(vm_map_t map, UInt64 page)
    {
        vm_prot_t current{0}, maximum{0};
        return UserPatchMemory::leafProtection(map, page, current, queryRegion, error, &maximum)
               && current == (VM_PROT_READ | VM_PROT_EXECUTE) && maximum == current;
    }

    struct DynamicBuffer
    {
        UserVideoDecodeResolver::Plan* plan{nullptr};
        DynamicBuffer()
        {
            plan = IONew(UserVideoDecodeResolver::Plan, 1);
            if (plan) { memset(plan, 0, sizeof(*plan)); }
        }
        ~DynamicBuffer()
        {
            if (plan) { IODelete(plan, UserVideoDecodeResolver::Plan, 1); }
        }
    };

    bool selectFairPlayDecode(const UInt64 base, const UserVideoDecodeData::Patch* patches, UInt32& count,
                              UserVideoDecodeData::Patch* selected, const UInt32 mask)
    {
        using namespace UserVideoDecodeData;
        if (count == 0 || count > PatchCount || count > 32 || mask == 0 || (count < 32 && (mask >> count) != 0)) {
            return false;
        }
        UInt32 selectedCount{0};
        UInt8  bytes[MaxPatchSize];
        for (UInt32 i = 0; i < count; ++i) {
            const auto& patch = patches[i];
            if (patch.size == 0 || patch.size > MaxPatchSize || patch.offset >= UserSharedCache::UserLimit - base
                || patch.size >= UserSharedCache::UserLimit - base - patch.offset)
            {
                return false;
            }
            if ((mask & (1U << i)) != 0) { selected[selectedCount++] = patch; }
            else if (readUser(base + patch.offset, bytes, patch.size) != 0
                     || memcmp(bytes, patch.original, patch.size) != 0)
            {
                return false;
            }
        }
        count = selectedCount;
        return selectedCount != 0;
    }

    __attribute__((noinline)) int patchSharedRegion(proc_t process, SharedRegionCheckArgs* args, const int result)
    {
        // 按 FairPlay 权限统一选择策略；客户端只允许 AMD 驱动侧解码项，
        // 保留 AppleGVA 和编码代码原样。
        bool   fairPlayDecode{false};
        UInt32 fairPlayPID{0};
        if (UserPatchPolicy::isFairPlayClient(current_task())) {
            const auto pid = process != nullptr ? proc_pid(process) : -1;
            fairPlayDecode = true;
            if (pid > 0) { fairPlayPID = static_cast<UInt32>(pid); }
        }
        if (!VCN::allocated() || !devicePatches) {
            stage(Stage::NoMap, fairPlayPID);
            return result;
        }
        if (args == nullptr || args->startAddress == 0) {
            stage(Stage::Argument, fairPlayPID);
            return result;
        }
        using namespace UserVideoDecodeData;
        TransactionBuffer transaction;
        if (!transaction.state) {
            error(KERN_RESOURCE_SHORTAGE);
            return result;
        }
        UInt32      patchCount      = static_cast<UInt32>(PatchCount);
        auto*       selectedPatches = transaction.state->selected;
        UInt64      base{0};
        UInt8       header[104];
        if (readUser(args->startAddress, &base, sizeof(base)) != 0 || base == 0
            || base >= UserSharedCache::UserLimit - sizeof(header) || (base & (PAGE_SIZE - 1)) != 0
            || readUser(base, header, sizeof(header)) != 0)
        {
            stage(Stage::CacheRead, fairPlayPID);
            return result;
        }
        if (memcmp(header, CacheMagic, sizeof(CacheMagic)) != 0) {
            stage(Stage::CacheMismatch, fairPlayPID);
            return result;
        }

        if (fairPlayDecode) {
            NRed::singleton().setProp32("NRedDRMVideoPID", fairPlayPID);
            NRed::singleton().setProp32("NRedDRMVideoState", 1);
        }

        const char* const uuidKeys[] = {"NRedVCNUserCacheUUID0", "NRedVCNUserCacheUUID1", "NRedVCNUserCacheUUID2",
                                        "NRedVCNUserCacheUUID3"};
        for (UInt32 i = 0; i < 4; ++i) {
            UInt32 value;
            memcpy(&value, header + 88 + i * 4, sizeof(value));
            publish(published.uuid[i], uuidKeys[i], value);
        }
        DynamicBuffer dynamicBuffer;
        auto&         input = transaction.state->input;
        input.reader        = readUser;
        input.scanBuffer    = transaction.state->scan;
        const auto major    = currentKernelVersion().major();
        const auto mask     = darwinMask(major);
        const auto deviceID = NRed::singleton().getDeviceID();
        vm_map_t   map      = getTaskMap(current_task());
        if (!map) {
            stage(Stage::NoMap, fairPlayPID);
            return result;
        }
        const auto        mapKey = reinterpret_cast<UInt64>(map);
        MappingProbeState probeState{map, queryRegion};
        if (!dynamicBuffer.plan) {
            error(KERN_RESOURCE_SHORTAGE);
            return result;
        }
        auto& fast = transaction.state->fast;
        fast.begin(&header[88], major, VPF::resolutionProfile(deviceID, sequoia ? 1U : 0U), 0, probeMapping,
                   &probeState);
        if (!UserVideoDecodeResolver::resolve(input, base, devicePatches, *dynamicBuffer.plan, sequoia, &fast)) {
            stage(Stage::CacheMismatch, fairPlayPID);
            NRed::singleton().setProp32("NRedVCNUserDynamicRejected", 1);
            return result;
        }
        const auto* patches = dynamicBuffer.plan->patches;
        publish(published.matched, "NRedVCNUserDynamicMatched", 1);
        publish(published.reads, "NRedVCNUserDynamicReads", dynamicBuffer.plan->reads);
        publish(published.fvrcDecode, "NRedFVRCDecodeHot", fast.hot ? 1 : 0);
        publish(published.fvrcDecodeCold, "NRedFVRCDecodeCold", fast.hot ? 0 : 1);

        if (fairPlayDecode) {
            if (!selectFairPlayDecode(base, patches, patchCount, selectedPatches, fairPlayMask())) {
                stage(Stage::CodeMismatch, fairPlayPID);
                return result;
            }
            patches = selectedPatches;
            NRed::singleton().setProp32("NRedDRMVideoPatchCount", patchCount);
        }

        const auto* decodeManifest = VPF::ManifestData::find(VPF::FeatureId::Vcn2Decode, mask, deviceID);
        if (!decodeManifest) {
            stage(Stage::CodeMismatch, fairPlayPID);
            error(KERN_INVALID_ARGUMENT);
            return result;
        }
        const auto cache     = input.cache;
        auto&      workspace = *transaction.state;
        // FairPlay 只选择解码子集，站点顺序不再与 Manifest 一一对应。
        const bool aligned = !fairPlayDecode;
        if (!VPF::adaptPatches(workspace.decodePlan, *decodeManifest, base, cache, patches, patchCount,
                               dynamicBuffer.plan->images, UserVideoDecodeResolver::ImageCount,
                               dynamicBuffer.plan->reads, VPF::PlanState::AllOriginal, aligned)
            || !planVideoFeatures(mask, deviceID, fairPlayDecode, workspace.decodePlan.valid(), workspace.planner))
        {
            NRed::singleton().setProp32("NRedVCNVPFPlannerRejected", 1);
            stage(Stage::CodeMismatch, fairPlayPID);
            error(KERN_INVALID_ARGUMENT);
            return result;
        }
        publish(published.path, "NRedVCNVPFPath", 1);
        publish(published.planner, "NRedVCNVPFPlannerEnabled", workspace.planner.enabled);

        auto& installPlan = workspace.installPlan;
        installPlan.reset(*decodeManifest, base, cache);
        if (!addPlanImages(installPlan, workspace.decodePlan)
            || !appendPlanSites(installPlan, workspace.decodePlan) || installPlan.siteCount() == 0
            || installPlan.siteCount() > Capacity || !installPlan.seal(VPF::PlanState::AllOriginal))
        {
            stage(Stage::CodeMismatch, fairPlayPID);
            error(KERN_INVALID_ARGUMENT);
            return result;
        }
        const auto installCount = installPlan.siteCount();
        for (UInt32 i = 0; i < installCount; ++i) {
            const auto& site = installPlan.site(i);
            if (site.size == 0 || site.size > MaxPatchSize || site.address < base
                || site.address >= UserSharedCache::UserLimit)
            {
                stage(Stage::CodeMismatch, fairPlayPID);
                return result;
            }
            workspace.install[i] = {site.offset, site.original, site.replacement, site.size};
        }
        if (!prepareLedger(mapKey)) {
            NRed::singleton().setProp32("NRedVCNVPFLedgerRejected", 1);
            if (ledgerState(mapKey) == VPF::CapabilityState::Quarantined) {
                NRed::singleton().setProp32("NRedVCNVPFQuarantined", 1);
            }
            stage(Stage::Mixed, fairPlayPID);
            return result;
        }

        SavedBuffer buffer;
        if (!buffer.rows) {
            error(KERN_RESOURCE_SHORTAGE);
            return result;
        }
        auto*  saved = buffer.rows;
        auto&  pages = workspace.pages;
        UInt32 pageCount{0}, originals{0}, patched{0};
        for (UInt32 i = 0; i < installCount; ++i) {
            const auto& site = installPlan.site(i);
            if (readUser(site.address, saved[i].bytes, site.size) != 0) {
                stage(Stage::CodeRead, fairPlayPID);
                return result;
            }
            if (memcmp(saved[i].bytes, site.original, site.size) == 0) { ++originals; }
            else if (memcmp(saved[i].bytes, site.replacement, site.size) == 0) {
                ++patched;
            }
            else {
                stage(Stage::CodeMismatch, fairPlayPID);
                error(KERN_INVALID_ARGUMENT);
                return result;
            }
            if (!UserPatchMemory::collectPages(pages, pageCount, site.address, site.size)) {
                stage(Stage::CodeMismatch, fairPlayPID);
                return result;
            }
        }
        const auto planState = originals == installCount ?
                                   VPF::PlanState::AllOriginal :
                                   (patched == installCount ? VPF::PlanState::AllPatched : VPF::PlanState::Mixed);
        if (!installPlan.seal(planState)) {
            stage(Stage::CodeMismatch, fairPlayPID);
            return result;
        }
        publish(published.planState, "NRedVCNVPFPlanState", static_cast<UInt32>(planState));
        if (planState == VPF::PlanState::AllPatched) {
            for (UInt32 p = 0; p < pageCount; ++p) {
                if (!executablePage(map, pages[p].address)) {
                    stage(Stage::Region, fairPlayPID);
                    return result;
                }
            }
            stage(Stage::Already, fairPlayPID);
            observeLedger(mapKey);
            if (fairPlayDecode) { NRed::singleton().setProp32("NRedDRMVideoState", 3); }
            return result;
        }
        if (planState != VPF::PlanState::AllOriginal) {
            stage(Stage::Mixed, fairPlayPID);
            return result;
        }

        ExecutorOps ops{map, queryRegion, error, readUser, writeUser};
        using Executor = VPF::SharedRegionTransaction<ExecutorOps, UserVideoDecodeData::Patch, Page,
                                                      UserVideoDecodeData::MaxPatchSize>;
        Executor executor(ops, base, workspace.install, installCount,
                          reinterpret_cast<UInt8(*)[UserVideoDecodeData::MaxPatchSize]>(saved), pages, pageCount, 0);
        markLedger(mapKey, VPF::CapabilityState::Installing);
        const auto committed = executor.commit([] { return true; });
        switch (committed.status) {
            case VPF::TransactionStatus::CheckFailed      : stage(Stage::Region, fairPlayPID); return result;
            case VPF::TransactionStatus::BeforeWriteFailed: return result;
            case VPF::TransactionStatus::ProtectionFailed:
                stage(Stage::Protection, fairPlayPID);
                markFailure(mapKey, committed.restoreOk);
                if (!committed.restoreOk) { stage(Stage::Rollback, fairPlayPID); }
                return result;
            case VPF::TransactionStatus::WriteFailed:
            case VPF::TransactionStatus::ReadbackFailed:
                stage(Stage::Write, fairPlayPID);
                error(KERN_FAILURE);
                markFailure(mapKey, committed.rollbackOk);
                if (!committed.rollbackOk) {
                    stage(Stage::Rollback, fairPlayPID);
                    error(KERN_FAILURE);
                }
                return result;
            case VPF::TransactionStatus::RestoreFailed:
                stage(Stage::Restore, fairPlayPID);
                markFailure(mapKey, committed.rollbackOk);
                if (!committed.rollbackOk) {
                    stage(Stage::Rollback, fairPlayPID);
                    error(KERN_FAILURE);
                }
                return result;
            case VPF::TransactionStatus::Installed: break;
        }
        installedLedger(mapKey);
        stage(Stage::Applied, fairPlayPID);
        if (fairPlayDecode) { NRed::singleton().setProp32("NRedDRMVideoState", 2); }
        NRed::singleton().setProp32("NRedVCNUserDynamicApplied", 1);
        if (process != nullptr) {
            const auto pid = proc_pid(process);
            char       name[32]{};
            if (pid > 0) { proc_name(pid, name, sizeof(name)); }
            name[sizeof(name) - 1] = '\0';
            if (strncmp(name, "VTDecoderXPC", 12) == 0) {
                NRed::singleton().setProp32("NRedVCNDecoderPID", static_cast<UInt32>(pid));
                SYSLOG("UserVideoDecode", "Decoder patched and RX restored: pid=%d", pid);
            }
        }
        auto count = __atomic_fetch_add(&appliedCount, 1U, __ATOMIC_RELAXED);
        if (count < 16) { NRed::singleton().setProp32("NRedVCNUserApplied", count + 1); }
        return result;
    }

    // 峰值只增不减；无锁读取，仅抢到更大值的调用发布，常态路径只有一次原子读。
    void recordTaskGatePeak()
    {
        const auto peak     = __atomic_load_n(&taskPatchRegistry.peak, __ATOMIC_RELAXED);
        auto       reported = __atomic_load_n(&taskGatePeakReported, __ATOMIC_RELAXED);
        while (peak > reported) {
            if (__atomic_compare_exchange_n(&taskGatePeakReported, &reported, peak, false, __ATOMIC_RELAXED,
                                            __ATOMIC_RELAXED))
            {
                // 较小值可能晚于较大值写入，写完复查直到发布值不再落后。
                for (auto value = peak;;) {
                    NRed::singleton().setProp32("NRedVCNUserMapGatePeak", value);
                    const auto latest = __atomic_load_n(&taskGatePeakReported, __ATOMIC_RELAXED);
                    if (latest == value) { break; }
                    value = latest;
                }
                break;
            }
        }
    }

    // 最后一个 map 引用释放后、对象地址可复用前清理；不依赖 proc 指针或退出原因。
    void wrappedMapDestroy(vm_map_t map)
    {
        removeLedgerMap(map);
        originalMapDestroy(map);
    }

    mach_vm_address_t mapDestroyTarget(const mach_vm_address_t address)
    {
        if (!address) { return 0; }
        // 部分内核将公开入口编成尾跳 thunk；deallocate 可直接调用实际销毁函数。
        static constexpr UInt8 thunk[] = {0x55, 0x48, 0x89, 0xE5, 0x5D, 0xE9};
        const auto*            entry   = reinterpret_cast<const UInt8*>(address);
        if (memcmp(entry, thunk, sizeof(thunk)) != 0) { return address; }
        int displacement;
        memcpy(&displacement, entry + sizeof(thunk), sizeof(displacement));
        return address + sizeof(thunk) + sizeof(displacement)
               + static_cast<UInt64>(static_cast<long long>(displacement));
    }

    // Only the first eight calls are traced, even in RELEASE; no per-call IORegistry writes.
    UInt32                        hookTraceCount{0};
    __attribute__((noinline)) int wrappedSharedRegionCheck(proc_t process, SharedRegionCheckArgs* args,
                                                           int* returnValue)
    {
        auto ticket = __atomic_load_n(&hookTraceCount, __ATOMIC_RELAXED);
        bool trace{false};
        while (ticket < 8) {
            if (__atomic_compare_exchange_n(&hookTraceCount, &ticket, ticket + 1, false, __ATOMIC_RELAXED,
                                            __ATOMIC_RELAXED))
            {
                trace = true;
                break;
            }
        }
        const auto pid = trace && process != nullptr ? proc_pid(process) : -1;
        stage(Stage::Called);
        if (trace) { SYSLOG("UserVideoDecode", "shared-region call=%u pid=%d original-enter", ticket, pid); }
        const auto result = reinterpret_cast<SharedRegionCheck>(originalCheck)(process, args, returnValue);
        if (trace) {
            SYSLOG("UserVideoDecode", "shared-region call=%u pid=%d original-return=%d", ticket, pid, result);
        }
        if (result != 0) {
            stage(Stage::OriginalError);
            return result;
        }
        if (UserPatchCoordinator::deferModulePatches()) {
            if (trace) { SYSLOG("UserVideoDecode", "shared-region call=%u pid=%d coordinated", ticket, pid); }
            return result;
        }
        if (trace) { SYSLOG("UserVideoDecode", "shared-region call=%u pid=%d patch-enter", ticket, pid); }
        if (!guardLock) { return result; }
        auto* identity = getTaskMap ? static_cast<void*>(getTaskMap(current_task())) : nullptr;
        if (!identity) { identity = current_task(); }
        UserPatchMapGate::Lease lease(taskPatchRegistry, guardLock, identity);
        if (!lease) { return result; }
        const auto patchedResult = patchSharedRegion(process, args, result);
        recordTaskGatePeak();
        if (trace) {
            SYSLOG("UserVideoDecode", "shared-region call=%u pid=%d patch-return=%d", ticket, pid, patchedResult);
        }
        return patchedResult;
    }

}    // namespace

void UserVideoDecode::init(KernelPatcher& patcher)
{
    UserPatchCoordinator::clearVideo();
    VPF::ResolutionDiagnostics::setEnabled(!checkKernelArgument("-NRedNoFVRC"));
    NRed::singleton().setProp32("NRedFVRCEnabled", VPF::ResolutionDiagnostics::enabled() ? 1 : 0);
    if (VPF::ResolutionDiagnostics::enabled()) {
        VPF::resolutionCache.init();
        NRed::singleton().setProp32("NRedFVRCCacheReady", VPF::resolutionCache.ready ? 1 : 0);
    }
    NRed::singleton().setProp32("NRedVCNSharedRegionStackRevision", 1);
    devicePatches = nullptr;
    if (!checkKernelArgument("-NRedVideoFix")) { return; }
    NRed::singleton().setProp32("NRedVCNUserEnabled", 0);
    NRed::singleton().setProp32("NRedDRMVideoEnabled", 0);
    NRed::singleton().setProp32("NRedVCNVPFLifecycle", 0);
    const auto  deviceID   = NRed::singleton().getDeviceID();
    const auto* capability = VCNCapabilities::findDevice(deviceID);
    const auto  rejected   = VCNCapabilities::rejection(deviceID, currentKernelVersion());
    NRed::singleton().setProp32("NRedVCNRequested", 1);
    NRed::singleton().setProp32("NRedVCNCapabilityVersion", 2);
    NRed::singleton().setProp32("NRedVCNDeviceID", deviceID);
    if (capability) { NRed::singleton().setProp32("NRedVCNGeneration", static_cast<UInt32>(capability->generation)); }
    if (rejected) {
        NRed::singleton().setProp32("NRedVCNRejected", rejected);
        NRed::singleton().setProp32("NRedVCNUserRejected", rejected);
        SYSLOG("UserVideoDecode", "VCN capability rejected: device=0x%X reason=%u", deviceID, rejected);
        return;
    }
    // Darwin 23 没有对应的用户态补丁数据；保持失败关闭，不使用其他版本的补丁。
    if (currentKernelVersion().major() == 23) {
        NRed::singleton().setProp32("NRedVCNUserUnsupportedOS", 1);
        NRed::singleton().setProp32("NRedVCNUserRejected", 6);
        return;
    }
    sequoia = currentKernelVersion().major() == 24;
    const auto* candidates =
        sequoia ? UserSequoiaVideoData::findDevice(deviceID) : UserVideoDecodeData::findDevice(deviceID);
    if (!candidates) {
        NRed::singleton().setProp32("NRedVCNUserRejected", 3);
        return;
    }
    getTaskMap = reinterpret_cast<GetTaskMap>(patcher.solveSymbol(KernelPatcher::KernelID, "_get_task_map"));
    patcher.clearError();
    queryRegion =
        reinterpret_cast<RegionRecurse>(patcher.solveSymbol(KernelPatcher::KernelID, "_mach_vm_region_recurse"));
    patcher.clearError();
    readUser = reinterpret_cast<CopyIn>(patcher.solveSymbol(KernelPatcher::KernelID, "_copyin"));
    patcher.clearError();
    writeUser = reinterpret_cast<CopyOut>(patcher.solveSymbol(KernelPatcher::KernelID, "_copyout"));
    patcher.clearError();
    if (!getTaskMap || !queryRegion || !readUser || !writeUser) {
        devicePatches = nullptr;
        NRed::singleton().setProp32("NRedVCNUserRejected", 1);
        return;
    }
    const auto destroyEntry = patcher.solveSymbol(KernelPatcher::KernelID, "_vm_map_destroy");
    patcher.clearError();
    const auto  destroy       = mapDestroyTarget(destroyEntry);
    const char* destroySymbol = "_vm_map_destroy";
    if (destroy && destroy != destroyEntry) {
        const auto options = patcher.solveSymbol(KernelPatcher::KernelID, "_vm_map_destroy_options");
        patcher.clearError();
        if (options != destroy) {
            NRed::singleton().setProp32("NRedVCNUserRejected", 8);
            return;
        }
        destroySymbol = "_vm_map_destroy_options";
    }
    if (!destroy) {
        NRed::singleton().setProp32("NRedVCNUserRejected", 8);
        return;
    }
    const bool allocatedLock = guardLock == nullptr;
    if (allocatedLock) { guardLock = IOLockAlloc(); }
    if (!guardLock) {
        devicePatches = nullptr;
        NRed::singleton().setProp32("NRedVCNUserRejected", 7);
        return;
    }
    KernelPatcher::RouteRequest lifecycleRequest{destroySymbol, wrappedMapDestroy, originalMapDestroy};
    if (!patcher.routeMultipleLong(KernelPatcher::KernelID, &lifecycleRequest, 1)) {
        patcher.clearError();
        if (allocatedLock) {
            IOLockFree(guardLock);
            guardLock = nullptr;
        }
        NRed::singleton().setProp32("NRedVCNUserRejected", 8);
        return;
    }
    NRed::singleton().setProp32("NRedVCNVPFLifecycle", 1);
    NRed::singleton().setProp32("NRedVCNVPFLifecycleRevision", 2);
    NRed::singleton().setProp32("NRedVCNVideoLedgerRevision", 2);
    KernelPatcher::RouteRequest request{"_shared_region_check_np", wrappedSharedRegionCheck, originalCheck};
    if (!patcher.routeMultipleLong(KernelPatcher::KernelID, &request, 1)) {
        patcher.clearError();
        devicePatches = nullptr;
        NRed::singleton().setProp32("NRedVCNUserRejected", 2);
        return;
    }
    devicePatches = candidates;
    NRed::singleton().setProp32("NRedVCNUserEnabled", 1);
    NRed::singleton().setProp32("NRedVCNUserMapGateRevision", 1);
    NRed::singleton().setProp32("NRedVCNRegionQueryVersion", 1);
    // FairPlay 诊断只在视频 Hook 安装成功后发布。
    NRed::singleton().setProp32("NRedDRMVideoEnabled", 1);
    NRed::singleton().setProp32("NRedDRMVideoRevision", 4);
    NRed::singleton().setProp32("NRedDRMVideoMask", fairPlayMask());
    // 显示模块也成功注册时，协调器成为最外层 Hook；否则继续使用本模块的旧 lease。
    UserPatchCoordinator::registerVideo(
        [](void* process, void* args, const int result)
        {
            return patchSharedRegion(reinterpret_cast<proc_t>(process), reinterpret_cast<SharedRegionCheckArgs*>(args),
                                     result);
        });
}
