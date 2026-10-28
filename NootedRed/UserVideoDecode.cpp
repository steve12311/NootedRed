// 在进程私有代码页应用已核验的视频解码补丁，并保留原生调用与页面保护。
#include <Headers/kern_util.hpp>
#include <IOKit/IOLib.h>
#include <NRed.hpp>
#include <PenguinWizardry/KernelVersion.hpp>
#include <UserLegacyData.hpp>
#include <UserPatchMemory.hpp>
#include <UserPatchPolicy.hpp>
#include <UserVideoDecode.hpp>
#include <UserVideoDecodeData.hpp>
#include <UserVideoDecodeResolver.hpp>
#include <UserVideoGuardResolver.hpp>
#include <VCN.hpp>
#include <VCNCapabilities.hpp>
#include <kern/task.h>
#include <mach/i386/vm_param.h>
#include <mach/vm_map.h>
#include <mach/vm_region.h>
#include <sys/proc.h>
#include <UserPatchMapGate.hpp>

namespace
{

    struct SharedRegionCheckArgs
    {
        user_addr_t startAddress;
    };
    static_assert(sizeof(SharedRegionCheckArgs) == 8);
    using SharedRegionCheck = int       (*)(proc_t, SharedRegionCheckArgs*, int*);
    using GetTaskMap        = vm_map_t         (*)(task_t);
    using RegionRecurse     = UserPatchMemory::RegionQuery;
    using CopyIn            = int                  (*)(user_addr_t, void*, size_t);
    using CopyOut           = int                 (*)(const void*, user_addr_t, size_t);

    mach_vm_address_t                 originalCheck{0};
    GetTaskMap                        getTaskMap{nullptr};
    RegionRecurse                     queryRegion{nullptr};
    CopyIn                            readUser{nullptr};
    CopyOut                           writeUser{nullptr};
    IOLock*                           guardLock{nullptr};
    UserPatchMapGate::Registry<64>     taskPatchRegistry;
    UInt32                            taskGatePeakReported{0};
    UInt32                            seenStages{0}, appliedCount{0}, errorCount{0};
    struct Published { UInt64 uuid[4], matched, reads, guardReads; } published{};
    bool                              fairPlayDriverOnly{false};
    bool                              sequoia{false};
    const UserVideoDecodeData::Patch* devicePatches{nullptr};
    const UserLegacyCompat::Profile*  legacyProfile{nullptr};
    constexpr auto                    PatchCount   = arrsize(UserVideoDecodeData::Patches);
    constexpr UInt32                  Capacity = PatchCount + UserVideoGuardData::MaxEntries;
    constexpr UInt32                  MaxPageCount = Capacity * 2;
    static_assert(PatchCount <= 32 && UserVideoDecodeData::PageCount <= 16);
    static_assert(arrsize(UserSequoiaVideoData::Patches) == PatchCount);
    static_assert(UserSequoiaVideoData::MaxPatchSize <= UserVideoDecodeData::MaxPatchSize);

    UInt32 fairPlayMask()
    {
        if (legacyProfile) { return legacyProfile->fairPlayMask; }
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

    bool leafProtection(vm_map_t map, const mach_vm_address_t page, vm_prot_t& protection)
    {
        return UserPatchMemory::leafProtection(map, page, protection, queryRegion, error);
    }

    bool restorePages(vm_map_t map, Page* pages, const UInt32 count)
    {
        return UserPatchMemory::restorePages(map, pages, count, error);
    }

    struct SavedRow
    {
        UInt8 bytes[UserVideoDecodeData::MaxPatchSize];
    };
    struct TransactionState
    {
        UserVideoDecodeData::Patch legacy[PatchCount]{}, combined[Capacity]{};
        Page pages[MaxPageCount]{};
        UserSharedCache::Input input{nullptr, {}};
        UInt8 scan[UserSharedCache::Input::ScanSize];
    };
    struct TransactionBuffer
    {
        TransactionState* state{nullptr};
        TransactionBuffer() { state = IONew(TransactionState, 1); }
        ~TransactionBuffer() { if (state) { IODelete(state, TransactionState, 1); } }
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

    bool rollback(vm_map_t map, Page* pages, const UInt32 pageCount, const UInt64 base,
                  const UserVideoDecodeData::Patch* patches, const SavedRow* saved, const UInt32 touched,
                  const UInt32 guardCount)
    {
        using namespace UserVideoDecodeData;
        bool ok = true;
        for (UInt32 remaining = touched; remaining > 0; --remaining) {
            const UInt32 i = remaining - 1;
            if (!ok && i < guardCount) { continue; }
            const auto address = base + patches[i].offset;
            const bool ready = UserPatchMemory::prepareRollback(map, pages, pageCount, address, patches[i].size, error);
            if (!ready) {
                ok = false;
                continue;
            }
            if (writeUser(saved[i].bytes, address, patches[i].size) != 0) { ok = false; }
        }
        if (!restorePages(map, pages, pageCount)) { ok = false; }
        if (!ok) {
            stage(Stage::Rollback);
            error(KERN_FAILURE);
        }
        return ok;
    }

    struct GuardBuffer
    {
        UserVideoGuardResolver::Plan* plan{nullptr};
        GuardBuffer() { plan = IONew(UserVideoGuardResolver::Plan, 1); }
        ~GuardBuffer() { if (plan) { IODelete(plan, UserVideoGuardResolver::Plan, 1); } }
    };

    struct GuardPage
    {
        vm_map_t map;
        vm_address_t page{0};
        bool retained{false};
        ~GuardPage()
        {
            if (page && !retained) {
                const auto status = vm_deallocate(map, page, PAGE_SIZE);
                if (status != KERN_SUCCESS) { error(status); }
            }
        }
    };

    bool executableGuard(vm_map_t map, UInt64 page)
    {
        vm_prot_t current{0}, maximum{0};
        return UserPatchMemory::leafProtection(map, page, current, queryRegion, error, &maximum)
               && current == (VM_PROT_READ | VM_PROT_EXECUTE) && maximum == current;
    }

    bool createGuard(GuardPage& owner, UserVideoGuardResolver::Plan& plan)
    {
        auto status = vm_allocate(owner.map, &owner.page, PAGE_SIZE, VM_FLAGS_ANYWHERE);
        if (status != KERN_SUCCESS) { error(status); return false; }
        if (!UserVideoGuardResolver::setPage(plan, owner.page, currentKernelVersion().major())
            || writeUser(plan.expected, owner.page, plan.length) != 0) { return false; }
        status = vm_protect(owner.map, owner.page, PAGE_SIZE, FALSE, VM_PROT_READ | VM_PROT_EXECUTE);
        if (status == KERN_SUCCESS) {
            status = vm_protect(owner.map, owner.page, PAGE_SIZE, TRUE, VM_PROT_READ | VM_PROT_EXECUTE);
        }
        if (status != KERN_SUCCESS) { error(status); return false; }
        return executableGuard(owner.map, owner.page)
               && readUser(owner.page, plan.observed, plan.length) == 0
               && memcmp(plan.expected, plan.observed, plan.length) == 0;
    }

    void recordSafety(proc_t process)
    {
        NRed::singleton().setProp32("NRedVCNEncodeGuardRevision", UserVideoGuardData::Revision);
        NRed::singleton().setProp32("NRedVCNDecodeGuardRevision", UserVideoGuardData::Revision);
        if (!process) { return; }
        const auto pid = proc_pid(process);
        char name[32]{};
        if (pid > 0) { proc_name(pid, name, sizeof(name)); }
        if (strncmp(name, "VTEncoderXPC", 12) == 0) {
            NRed::singleton().setProp32("NRedVCNGuardedEncoderPID", static_cast<UInt32>(pid));
        }
        else if (strncmp(name, "VTDecoderXPC", 12) == 0) {
            NRed::singleton().setProp32("NRedVCNGuardedDecoderPID", static_cast<UInt32>(pid));
        }
    }

    struct DynamicBuffer
    {
        UserVideoDecodeResolver::Plan* plan{nullptr};
        explicit DynamicBuffer(bool enabled)
        {
            if (enabled) { plan = IONew(UserVideoDecodeResolver::Plan, 1); }
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

    __attribute__((noinline))
    int patchSharedRegion(proc_t process, SharedRegionCheckArgs* args, const int result)
    {
        // 按 FairPlay 权限统一选择策略；客户端只允许 AMD 驱动侧解码项，
        // 保留 AppleGVA 和编码代码原样。
        bool   fairPlayDecode{false};
        UInt32 fairPlayPID{0};
        if (UserPatchPolicy::isFairPlayClient(current_task())) {
            const auto pid = process != nullptr ? proc_pid(process) : -1;
            if (!fairPlayDriverOnly) {
                if (pid > 0) { NRed::singleton().setProp32("NRedVCNFairPlaySkipPID", static_cast<UInt32>(pid)); }
                NRed::singleton().setProp32("NRedVCNFairPlaySkipped", 1);
                return result;
            }
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
        if (!transaction.state) { error(KERN_RESOURCE_SHORTAGE); return result; }
        const auto* patches    = devicePatches;
        UInt32      patchCount = legacyProfile ? legacyProfile->patchCount : static_cast<UInt32>(PatchCount);
        auto* legacyPatches = transaction.state->legacy;
        if (legacyProfile) {
            if (patchCount > PatchCount) { return result; }
            for (UInt32 i = 0; i < patchCount; ++i) {
                const auto& patch = legacyProfile->patches[i];
                legacyPatches[i]  = {patch.offset, patch.original, patch.patched, patch.size};
            }
            patches = legacyPatches;
        }
        UInt64 base{0};
        UInt8  header[104];
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
        if (legacyProfile && !UserLegacyCompat::matches(readUser, base, header + 88, *legacyProfile)) {
            stage(Stage::CacheMismatch, fairPlayPID);
            NRed::singleton().setProp32("NRedVCNUserLegacyRejected", 1);
            return result;
        }
        const bool    dynamic = sequoia || (!legacyProfile && memcmp(header + 88, CacheUUID, sizeof(CacheUUID)) != 0);
        DynamicBuffer dynamicBuffer(dynamic);
        auto& input = transaction.state->input;
        input.reader = readUser;
        input.scanBuffer = transaction.state->scan;
        if (dynamic) {
            if (!dynamicBuffer.plan) {
                error(KERN_RESOURCE_SHORTAGE);
                return result;
            }
            if (!UserVideoDecodeResolver::resolve(input, base, devicePatches, *dynamicBuffer.plan, sequoia)) {
                stage(Stage::CacheMismatch, fairPlayPID);
                NRed::singleton().setProp32("NRedVCNUserDynamicRejected", 1);
                return result;
            }
            patches = dynamicBuffer.plan->patches;
            publish(published.matched, "NRedVCNUserDynamicMatched", 1);
            publish(published.reads, "NRedVCNUserDynamicReads", dynamicBuffer.plan->reads);
        }

        if (fairPlayDecode) {
            if (!selectFairPlayDecode(base, patches, patchCount, legacyPatches, fairPlayMask())) {
                stage(Stage::CodeMismatch, fairPlayPID);
                return result;
            }
            patches = legacyPatches;
            NRed::singleton().setProp32("NRedDRMVideoPatchCount", patchCount);
        }

        GuardBuffer guard;
        if (!guard.plan || !UserVideoGuardResolver::resolve(input, base, currentKernelVersion().major(), *guard.plan)) {
            NRed::singleton().setProp32("NRedVCNUserGuardRejected", 1);
            return result;
        }
        publish(published.guardReads, "NRedVCNUserGuardReads", guard.plan->reads);
        vm_map_t map = getTaskMap(current_task());
        if (!map) { stage(Stage::NoMap, fairPlayPID); return result; }
        SavedBuffer buffer;
        if (!buffer.rows) {
            error(KERN_RESOURCE_SHORTAGE);
            return result;
        }
        auto*  saved = buffer.rows;
        auto&  pages = transaction.state->pages;
        UInt32 pageCount{0}, originals{0}, patched{0};
        for (UInt32 i = 0; i < patchCount; ++i) {
            const auto address = base + patches[i].offset;
            if (patches[i].size == 0 || patches[i].size > MaxPatchSize
                || patches[i].offset >= UserSharedCache::UserLimit - base
                || patches[i].size >= UserSharedCache::UserLimit - base - patches[i].offset
                || readUser(address, saved[i].bytes, patches[i].size) != 0)
            {
                stage(Stage::CodeRead, fairPlayPID);
                return result;
            }
            if (memcmp(saved[i].bytes, patches[i].original, patches[i].size) == 0) { ++originals; }
            else if (memcmp(saved[i].bytes, patches[i].patched, patches[i].size) == 0) {
                ++patched;
            }
            else {
                stage(Stage::CodeMismatch, fairPlayPID);
                error(KERN_INVALID_ARGUMENT);
                return result;
            }
            if (!UserPatchMemory::collectPages(pages, pageCount, address, patches[i].size)) {
                stage(Stage::CodeMismatch, fairPlayPID);
                return result;
            }
        }
        if (patched == patchCount && guard.plan->installed) {
            if (!executableGuard(map, guard.plan->page)) { stage(Stage::Region, fairPlayPID); return result; }
            for (UInt32 i = 0; i < guard.plan->count; ++i) {
                const auto& entry = guard.plan->patches[i];
                if (!UserPatchMemory::collectPages(pages, pageCount, base + entry.offset, entry.size)) { return result; }
            }
            for (UInt32 p = 0; p < pageCount; ++p) {
                if (!executableGuard(map, pages[p].address)) { stage(Stage::Region, fairPlayPID); return result; }
            }
            stage(Stage::Already, fairPlayPID);
            recordSafety(process);
            if (fairPlayDecode) { NRed::singleton().setProp32("NRedDRMVideoState", 3); }
            return result;
        }
        if (originals != patchCount || guard.plan->installed) {
            stage(Stage::Mixed, fairPlayPID);
            return result;
        }
        auto* combined = transaction.state->combined;
        if (patchCount + guard.plan->count > Capacity) { return result; }
        memcpy(combined, patches, patchCount * sizeof(combined[0]));
        for (UInt32 i = 0; i < guard.plan->count; ++i) {
            const auto& entry = guard.plan->patches[i];
            if (readUser(base + entry.offset, saved[patchCount + i].bytes, entry.size) != 0
                || memcmp(saved[patchCount + i].bytes, entry.original, entry.size) != 0
                || !UserPatchMemory::collectPages(pages, pageCount, base + entry.offset, entry.size)) { return result; }
        }
        for (UInt32 p = 0; p < pageCount; ++p) {
            if (!leafProtection(map, pages[p].address, pages[p].protection)
                || pages[p].protection != (VM_PROT_READ | VM_PROT_EXECUTE))
            {
                stage(Stage::Region, fairPlayPID);
                return result;
            }
        }
        GuardPage owner{map};
        if (!createGuard(owner, *guard.plan)) {
            NRed::singleton().setProp32("NRedVCNUserGuardAllocationRejected", 1);
            return result;
        }
        // 先安装守卫，再启用设备适配；回滚按相反顺序撤销，避免适配先于安全检查生效。
        memmove(combined + guard.plan->count, combined, patchCount * sizeof(combined[0]));
        memmove(saved + guard.plan->count, saved, patchCount * sizeof(saved[0]));
        for (UInt32 i = 0; i < guard.plan->count; ++i) {
            combined[i] = guard.plan->patches[i];
            memcpy(saved[i].bytes, guard.plan->original[i], combined[i].size);
        }
        patchCount += guard.plan->count;
        patches = combined;
        // 所有匹配和保护检查完成后才进入写入；各页均先 COW、去掉 EXECUTE。
        for (UInt32 p = 0; p < pageCount; ++p) {
            const auto protection =
                vm_protect(map, pages[p].address, PAGE_SIZE, FALSE, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
            if (protection != KERN_SUCCESS) {
                stage(Stage::Protection, fairPlayPID);
                error(protection);
                if (!restorePages(map, pages, pageCount)) { stage(Stage::Rollback, fairPlayPID); }
                return result;
            }
            pages[p].writable = true;
        }
        for (UInt32 i = 0; i < patchCount; ++i) {
            if (writeUser(patches[i].patched, base + patches[i].offset, patches[i].size) != 0) {
                stage(Stage::Write, fairPlayPID);
                error(KERN_FAILURE);
                if (!rollback(map, pages, pageCount, base, patches, saved, i + 1, guard.plan->count)) {
                    owner.retained = true;
                    NRed::singleton().setProp32("NRedVCNGuardRollbackRetainedPage", 1);
                }
                return result;
            }
        }
        if (!restorePages(map, pages, pageCount)) {
            stage(Stage::Restore, fairPlayPID);
            if (!rollback(map, pages, pageCount, base, patches, saved, patchCount, guard.plan->count)) {
                owner.retained = true;
                NRed::singleton().setProp32("NRedVCNGuardRollbackRetainedPage", 1);
            }
            return result;
        }
        owner.retained = true;
        recordSafety(process);
        stage(Stage::Applied, fairPlayPID);
        if (fairPlayDecode) { NRed::singleton().setProp32("NRedDRMVideoState", 2); }
        if (dynamic) { NRed::singleton().setProp32("NRedVCNUserDynamicApplied", 1); }
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
        if (trace) { SYSLOG("UserVideoDecode", "shared-region call=%u pid=%d original-enter", ticket, pid); }
        const auto result = reinterpret_cast<SharedRegionCheck>(originalCheck)(process, args, returnValue);
        if (trace) { SYSLOG("UserVideoDecode", "shared-region call=%u pid=%d original-return=%d", ticket, pid, result); }
        if (result != 0) { stage(Stage::OriginalError); return result; }
        if (trace) { SYSLOG("UserVideoDecode", "shared-region call=%u pid=%d patch-enter", ticket, pid); }
        if (!guardLock) { return result; }
        auto* identity = getTaskMap ? static_cast<void*>(getTaskMap(current_task())) : nullptr;
        if (!identity) { identity = current_task(); }
        UserPatchMapGate::Lease lease(taskPatchRegistry, guardLock, identity);
        if (!lease) { return result; }
        const auto patchedResult = patchSharedRegion(process, args, result);
        recordTaskGatePeak();
        if (trace) { SYSLOG("UserVideoDecode", "shared-region call=%u pid=%d patch-return=%d", ticket, pid, patchedResult); }
        return patchedResult;
    }

}    // namespace

void UserVideoDecode::init(KernelPatcher& patcher)
{
    NRed::singleton().setProp32("NRedVCNSharedRegionStackRevision", 1);
    fairPlayDriverOnly = false;
    devicePatches = nullptr;
    legacyProfile = nullptr;
    if (!checkKernelArgument("-NRedVideoFix")) { return; }
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
    if (currentKernelVersion().major() == 23) {
        NRed::singleton().setProp32("NRedVCNUserSafetyProfileMissing", 1);
        NRed::singleton().setProp32("NRedVCNUserRejected", 6);
        return;
    }
    sequoia       = currentKernelVersion().major() == 24;
    devicePatches = sequoia ? UserSequoiaVideoData::findDevice(deviceID) : UserVideoDecodeData::findDevice(deviceID);
    legacyProfile = sequoia ? nullptr : UserLegacyData::video(currentKernelVersion().major(), deviceID);
    if (!devicePatches) {
        NRed::singleton().setProp32("NRedVCNUserRejected", 3);
        return;
    }
    fairPlayDriverOnly = true;
    getTaskMap         = reinterpret_cast<GetTaskMap>(patcher.solveSymbol(KernelPatcher::KernelID, "_get_task_map"));
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
    const bool allocatedLock = guardLock == nullptr;
    if (allocatedLock) { guardLock = IOLockAlloc(); }
    if (!guardLock) {
        devicePatches = nullptr;
        NRed::singleton().setProp32("NRedVCNUserRejected", 7);
        return;
    }
    KernelPatcher::RouteRequest request{"_shared_region_check_np", wrappedSharedRegionCheck, originalCheck};
    if (!patcher.routeMultipleLong(KernelPatcher::KernelID, &request, 1)) {
        patcher.clearError();
        devicePatches = nullptr;
        if (allocatedLock) { IOLockFree(guardLock); guardLock = nullptr; }
        NRed::singleton().setProp32("NRedVCNUserRejected", 2);
        return;
    }
    NRed::singleton().setProp32("NRedVCNUserEnabled", 1);
    NRed::singleton().setProp32("NRedVCNUserMapGateRevision", 1);
    NRed::singleton().setProp32("NRedVCNRegionQueryVersion", 1);
    if (fairPlayDriverOnly) {
        NRed::singleton().setProp32("NRedDRMVideoEnabled", 1);
        NRed::singleton().setProp32("NRedDRMVideoRevision", 4);
        NRed::singleton().setProp32("NRedDRMVideoMask", fairPlayMask());
    }
}
