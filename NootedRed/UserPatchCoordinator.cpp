// DVPF：共享缓存 Hook 的最外层协调器；原生调用只发生一次，随后由一个 map lease
// 串行执行显示与视频补丁。单个模块启用时不安装，保持原路径。
#include <Headers/kern_patcher.hpp>
#include <Headers/kern_util.hpp>
#include <IOKit/IOLib.h>
#include <NRed.hpp>
#include <UserPatchCoordinator.hpp>
#include <VPF/DisplayDependencyPlanner.hpp>
#include <kern/task.h>
#include <mach/vm_map.h>
#include <sys/proc.h>

namespace
{

    struct SharedRegionCheckArgs
    {
        UInt64 startAddress;
    };
    static_assert(sizeof(SharedRegionCheckArgs) == 8);

    using SharedRegionCheck = int (*)(proc_t, SharedRegionCheckArgs*, int*);
    using GetTaskMap        = vm_map_t (*)(task_t);

    mach_vm_address_t originalCheck{0};
    GetTaskMap        getTaskMap{nullptr};
    UInt32            displayGroups{0};

    // 已完成两侧补丁的 map：只作为“可以先核验 proof”的提示，从不单独决定跳过。
    struct CompletedMap
    {
        void*                             map{nullptr};
        UInt64                            base{0};
        UInt8                             header[UserPatchCoordinator::HeaderSize]{};
        UserPatchCoordinator::Participant display{}, video{};
    };
    constexpr UInt32 CompletedSlots = UserPatchCoordinator::MaxMapSlots;
    CompletedMap     completed[CompletedSlots]{};
    UInt32           completedNext{0};

    bool settled(const UserPatchCoordinator::Participant& value)
    {
        return value.outcome == UserPatchCoordinator::Outcome::Patched
               || value.outcome == UserPatchCoordinator::Outcome::Idle;
    }

    bool proven(const UserPatchCoordinator::Reader read, const UserPatchCoordinator::Participant& value)
    {
        if (value.outcome == UserPatchCoordinator::Outcome::Idle) { return true; }
        const auto* proof = value.proof;
        if (value.outcome != UserPatchCoordinator::Outcome::Patched || !proof || proof->siteCount == 0
            || proof->siteCount > UserPatchCoordinator::MaxProofSites)
        {
            return false;
        }
        UInt8 bytes[UserPatchCoordinator::MaxProofBytes];
        for (UInt32 i = 0; i < proof->siteCount; ++i) {
            const auto& site = proof->sites[i];
            if (site.size == 0 || site.size > sizeof(bytes) || read(site.address, bytes, site.size) != 0
                || memcmp(bytes, site.bytes, site.size) != 0)
            {
                return false;
            }
        }
        return true;
    }

    void releaseCompleted(const CompletedMap& value)
    {
        UserPatchCoordinator::releaseProof(value.display.proof);
        UserPatchCoordinator::releaseProof(value.video.proof);
    }

    struct CompletedSnapshot
    {
        CompletedMap value{};
        ~CompletedSnapshot() { releaseCompleted(value); }
    };

    // 锁内只复制或改写表项；copyin 一律在锁外完成。
    bool lookupCompleted(void* map, CompletedMap& out)
    {
        bool found = false;
        IOLockLock(UserPatchCoordinator::gateLock());
        for (UInt32 i = 0; i < CompletedSlots; ++i) {
            if (completed[i].map == map) {
                out   = completed[i];
                UserPatchCoordinator::retainProof(out.display.proof);
                UserPatchCoordinator::retainProof(out.video.proof);
                found = true;
                break;
            }
        }
        IOLockUnlock(UserPatchCoordinator::gateLock());
        return found;
    }

    void storeCompleted(const CompletedMap& value)
    {
        IOLockLock(UserPatchCoordinator::gateLock());
        UInt32 slot = CompletedSlots;
        for (UInt32 i = 0; i < CompletedSlots; ++i) {
            if (completed[i].map == value.map) {
                slot = i;
                break;
            }
            if (!completed[i].map && slot == CompletedSlots) { slot = i; }
        }
        if (slot == CompletedSlots) {
            slot          = completedNext;
            completedNext = (completedNext + 1) % CompletedSlots;
        }
        const auto previous = completed[slot];
        completed[slot] = value;
        UserPatchCoordinator::retainProof(value.display.proof);
        UserPatchCoordinator::retainProof(value.video.proof);
        IOLockUnlock(UserPatchCoordinator::gateLock());
        releaseCompleted(previous);
    }

    __attribute__((noinline))
    int wrappedSharedRegionCheck(proc_t process, SharedRegionCheckArgs* args, int* returnValue)
    {
        if (!originalCheck) { return KERN_FAILURE; }
        const auto result = reinterpret_cast<SharedRegionCheck>(originalCheck)(process, args, returnValue);
        __atomic_fetch_add(&UserPatchCoordinator::nativeCalls(), 1U, __ATOMIC_RELAXED);
        if (result != 0 || !UserPatchCoordinator::active()) { return result; }
        if (!UserPatchCoordinator::processAllowed(process)) { return result; }
        auto mapKey = getTaskMap ? static_cast<void*>(getTaskMap(current_task())) :
                                   static_cast<void*>(current_task());
        if (!mapKey) { return result; }
        return UserPatchCoordinator::coordinate(process, args, result, mapKey);
    }

}    // namespace

namespace UserPatchCoordinator
{

    void configureDisplay(const bool enabled, const UInt32 major)
    {
        displayGroups = VPF::requestedGroupsForDisplayFix(enabled, major);
        NRed::singleton().setProp32("NRedDisplayPlannerRevision", 1);
        NRed::singleton().setProp32("NRedDisplayPlannerRequested", displayGroups);
    }

    UInt32 requestedDisplayGroups() { return displayGroups; }

    PatchCallback& displayCallback()
    {
        static PatchCallback value{nullptr};
        return value;
    }

    PatchCallback& videoCallback()
    {
        static PatchCallback value{nullptr};
        return value;
    }

    ProcessPredicate& processFilter()
    {
        static ProcessPredicate value{nullptr};
        return value;
    }

    UInt8& activeFlag()
    {
        static UInt8 value{0};
        return value;
    }

    IOLock*& gateLock()
    {
        static IOLock* value{nullptr};
        return value;
    }

    UserPatchMapGate::Registry<MaxMapSlots>& registry()
    {
        static UserPatchMapGate::Registry<MaxMapSlots> value{};
        return value;
    }

    UInt32& nativeCalls()
    {
        static UInt32 value{0};
        return value;
    }

    UInt32& coordinatedRuns()
    {
        static UInt32 value{0};
        return value;
    }

    UInt32& leaseRejections()
    {
        static UInt32 value{0};
        return value;
    }

    UInt32& reportedPeak()
    {
        static UInt32 value{0};
        return value;
    }

    UInt32& skippedRuns()
    {
        static UInt32 value{0};
        return value;
    }

    Reader& reader()
    {
        static Reader value{nullptr};
        return value;
    }

    Proof* allocateProof()
    {
        auto* proof = static_cast<Proof*>(IOMalloc(sizeof(Proof)));
        if (proof) {
            proof->references = 1;
            proof->siteCount  = 0;
        }
        return proof;
    }

    void retainProof(Proof* proof)
    {
        if (proof) { __atomic_fetch_add(&proof->references, 1U, __ATOMIC_RELAXED); }
    }

    void releaseProof(Proof* proof)
    {
        if (proof && __atomic_sub_fetch(&proof->references, 1U, __ATOMIC_ACQ_REL) == 0) {
            IOFree(proof, sizeof(Proof));
        }
    }

    void forgetMap(void* mapKey)
    {
        if (!mapKey || !gateLock()) { return; }
        CompletedMap previous{};
        IOLockLock(gateLock());
        for (UInt32 i = 0; i < CompletedSlots; ++i) {
            if (completed[i].map == mapKey) {
                previous     = completed[i];
                completed[i] = {};
                break;
            }
        }
        IOLockUnlock(gateLock());
        releaseCompleted(previous);
    }

    int coordinate(void* process, void* args, const int result, void* mapKey)
    {
        if (!active() || !mapKey) { return result; }
        // 同一线程嵌套进入时不重复执行补丁；等待其他 map 时只睡眠，不持有注册表锁。
        auto patched = result;
        {
            UserPatchMapGate::Lease<MaxMapSlots> lease(registry(), gateLock(), mapKey);
            if (!lease) {
                __atomic_fetch_add(&leaseRejections(), 1U, __ATOMIC_RELAXED);
                return result;
            }
            // 缓存头只读一次，两个参与者共用；读失败时参与者各自按旧路径读取并报告。
            Transaction transaction{};
            const auto  read = reader();
            const auto  startAddress = args ? static_cast<const SharedRegionCheckArgs*>(args)->startAddress : 0ULL;
            if (read && startAddress && read(startAddress, &transaction.base, sizeof(transaction.base)) == 0
                && transaction.base != 0 && (transaction.base & 4095) == 0
                && read(transaction.base, transaction.header, HeaderSize) == 0)
            {
                transaction.cacheRead = true;
            }
            // 同一 map 已完成过：同一缓存基址且两侧 proof 仍在内存中才跳过；任何不确定都走完整搜索。
            CompletedSnapshot snapshot{};
            auto&             previous = snapshot.value;
            if (transaction.cacheRead && lookupCompleted(mapKey, previous) && previous.base == transaction.base
                && memcmp(previous.header, transaction.header, HeaderSize) == 0 && proven(read, previous.display)
                && proven(read, previous.video))
            {
                const auto skipped = __atomic_add_fetch(&skippedRuns(), 1U, __ATOMIC_RELAXED);
                if (skipped <= 16 || (skipped & 0xFF) == 0) {
                    NRed::singleton().setProp32("NRedPatchCoordinatorSkippedRuns", skipped);
                }
                return result;
            }
            if (const auto callback = displayCallback()) { patched = callback(process, args, patched, transaction); }
            if (const auto callback = videoCallback()) { patched = callback(process, args, patched, transaction); }
            __atomic_fetch_add(&coordinatedRuns(), 1U, __ATOMIC_RELAXED);
            const bool provable = transaction.display.outcome == Outcome::Patched
                                  || transaction.video.outcome == Outcome::Patched;
            if (transaction.cacheRead && settled(transaction.display) && settled(transaction.video) && provable) {
                CompletedMap value{};
                value.map  = mapKey;
                value.base = transaction.base;
                memcpy(value.header, transaction.header, HeaderSize);
                value.display = transaction.display;
                value.video   = transaction.video;
                storeCompleted(value);
            }
            else if (previous.map) {
                forgetMap(mapKey);
            }
        }
        const auto peak = __atomic_load_n(&registry().peak, __ATOMIC_RELAXED);
        auto       reported = __atomic_load_n(&reportedPeak(), __ATOMIC_RELAXED);
        while (peak > reported) {
            if (__atomic_compare_exchange_n(&reportedPeak(), &reported, peak, false, __ATOMIC_RELAXED,
                                            __ATOMIC_RELAXED))
            {
                NRed::singleton().setProp32("NRedPatchCoordinatorPeak", peak);
                break;
            }
        }
        return patched;
    }

    void init(KernelPatcher& patcher)
    {
        if (active()) { return; }
        setActive(false);
        NRed::singleton().setProp32("NRedPatchCoordinatorRevision", 3);
        // 只有两个共享缓存模块同时安装时才接管；单模块保持各自旧路径。
        if (!displayCallback() || !videoCallback()) {
            NRed::singleton().setProp32("NRedPatchCoordinatorSkipped", 1);
            return;
        }
        if (!gateLock()) { gateLock() = IOLockAlloc(); }
        if (!gateLock()) {
            NRed::singleton().setProp32("NRedPatchCoordinatorRejected", 1);
            return;
        }
        getTaskMap = reinterpret_cast<GetTaskMap>(patcher.solveSymbol(KernelPatcher::KernelID, "_get_task_map"));
        patcher.clearError();
        KernelPatcher::RouteRequest request{"_shared_region_check_np", wrappedSharedRegionCheck, originalCheck};
        if (!patcher.routeMultipleLong(KernelPatcher::KernelID, &request, 1)) {
            patcher.clearError();
            IOLockFree(gateLock());
            gateLock() = nullptr;
            NRed::singleton().setProp32("NRedPatchCoordinatorRejected", 2);
            return;
        }
        setActive(true);
        NRed::singleton().setProp32("NRedPatchCoordinatorEnabled", 1);
        NRed::singleton().setProp32("NRedPatchCoordinatorDisplay", 1);
        NRed::singleton().setProp32("NRedPatchCoordinatorVideo", 1);
        SYSLOG("UserPatchCoordinator", "display/video shared-cache transactions serialized per vm_map");
    }

}    // namespace UserPatchCoordinator
