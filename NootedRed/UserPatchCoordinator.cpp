// DVPF：共享缓存 Hook 的最外层协调器；原生调用只发生一次，随后由一个 map lease
// 串行执行显示与视频补丁。单个模块启用时不安装，保持原路径。
#include <Headers/kern_patcher.hpp>
#include <Headers/kern_util.hpp>
#include <IOKit/IOLib.h>
#include <NRed.hpp>
#include <UserPatchCoordinator.hpp>
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

    __attribute__((noinline))
    int wrappedSharedRegionCheck(proc_t process, SharedRegionCheckArgs* args, int* returnValue)
    {
        if (!originalCheck) { return KERN_FAILURE; }
        const auto result = reinterpret_cast<SharedRegionCheck>(originalCheck)(process, args, returnValue);
        __atomic_fetch_add(&UserPatchCoordinator::nativeCalls(), 1U, __ATOMIC_RELAXED);
        if (result != 0 || !UserPatchCoordinator::active()) { return result; }
        auto mapKey = getTaskMap ? static_cast<void*>(getTaskMap(current_task())) :
                                   static_cast<void*>(current_task());
        if (!mapKey) { return result; }
        return UserPatchCoordinator::coordinate(process, args, result, mapKey);
    }

}    // namespace

namespace UserPatchCoordinator
{

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
            if (const auto callback = displayCallback()) { patched = callback(process, args, patched); }
            if (const auto callback = videoCallback()) { patched = callback(process, args, patched); }
            __atomic_fetch_add(&coordinatedRuns(), 1U, __ATOMIC_RELAXED);
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
        NRed::singleton().setProp32("NRedPatchCoordinatorRevision", 1);
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
