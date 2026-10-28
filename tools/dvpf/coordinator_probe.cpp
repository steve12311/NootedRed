// DVPF 协调器探针：原生调用一次、同 map 串行、异 map 并行、等待与嵌套语义。
#include <Headers/kern_patcher.hpp>
#include <NRed.hpp>
#include <UserPatchCoordinator.hpp>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>

thread_local void*    currentMap = reinterpret_cast<void*>(0x1000);
thread_local char     threadToken{};
std::atomic<unsigned> nativeCalls{0}, nativeResult{0};

void* current_thread() { return &threadToken; }

void* current_task() { return reinterpret_cast<void*>(0x1); }

void* mapForTask(void*) { return currentMap; }

int nativeStub(void*, void*, int* out)
{
    ++nativeCalls;
    *out = 123;
    return static_cast<int>(nativeResult.load());
}

namespace
{
    struct Args
    {
        UInt64 startAddress{0x1000};
    };

    using Hook = int (*)(void*, void*, int*);

    Hook                  wrapped{nullptr};
    std::atomic<unsigned> activeCallbacks{0}, peakCallbacks{0};
    std::atomic<unsigned> displayCalls{0}, videoCalls{0};
    std::atomic<bool>     holdCallbacks{false};
    std::atomic<bool>     nestedInvoked{false};
    std::mutex            orderLock;
    std::vector<int>      order;

    void updatePeak(const unsigned value)
    {
        auto peak = peakCallbacks.load();
        while (value > peak && !peakCallbacks.compare_exchange_weak(peak, value)) {}
    }

    int displayCallback(void*, void*, const int result)
    {
        const auto current = ++activeCallbacks;
        updatePeak(current);
        if (holdCallbacks.load()) {
            while (holdCallbacks.load()) { std::this_thread::yield(); }
        }
        {
            std::lock_guard<std::mutex> guard(orderLock);
            order.push_back(1);
        }
        ++displayCalls;
        --activeCallbacks;
        return result;
    }

    int videoCallback(void*, void*, const int result)
    {
        {
            std::lock_guard<std::mutex> guard(orderLock);
            order.push_back(2);
        }
        ++videoCalls;
        return result;
    }

    void resetCounters()
    {
        nativeCalls.store(0);
        nativeResult.store(0);
        activeCallbacks.store(0);
        peakCallbacks.store(0);
        displayCalls.store(0);
        videoCalls.store(0);
        holdCallbacks.store(false);
        order.clear();
    }

    int callHook()
    {
        Args args{};
        int  value{0};
        return wrapped(reinterpret_cast<void*>(0x1), &args, &value);
    }

    bool waitFor(const std::atomic<unsigned>& value, const unsigned expected)
    {
        for (unsigned i = 0; i < 2000; ++i) {
            if (value.load() >= expected) { return true; }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        return false;
    }
}    // namespace

int main()
{
    UserPatchCoordinator::registerDisplay(displayCallback);
    UserPatchCoordinator::registerVideo(videoCallback);
    KernelPatcher patcher;
    UserPatchCoordinator::init(patcher);
    if (!UserPatchCoordinator::active() || !KernelPatcher::captured || NRed::value("NRedPatchCoordinatorEnabled") != 1) {
        std::fprintf(stderr, "coordinator not active\n");
        return 1;
    }
    wrapped = reinterpret_cast<Hook>(KernelPatcher::captured);

    // 单次 Hook：原生只一次，补丁顺序固定为显示后视频。
    resetCounters();
    for (unsigned i = 0; i < 20; ++i) {
        if (callHook() != 0 || nativeCalls != i + 1 || displayCalls != i + 1 || videoCalls != i + 1) {
            std::fprintf(stderr, "single-call mismatch at %u\n", i);
            return 1;
        }
    }
    if (order.size() != 40 || order[0] != 1 || order[1] != 2) {
        std::fprintf(stderr, "callback order mismatch\n");
        return 1;
    }

    // 原生失败：直接返回，不执行任何补丁回调。
    resetCounters();
    nativeResult.store(37);
    if (callHook() != 37 || nativeCalls != 1 || displayCalls != 0 || videoCalls != 0) {
        std::fprintf(stderr, "native error propagated incorrectly\n");
        return 1;
    }

    // 同 map 多线程：一个 map lease 串行执行完整显示+视频回调。
    resetCounters();
    std::vector<std::thread> serial;
    for (unsigned i = 0; i < 8; ++i) {
        serial.emplace_back(
            []
            {
                currentMap = reinterpret_cast<void*>(0x2000);
                for (unsigned j = 0; j < 20; ++j) { callHook(); }
            });
    }
    for (auto& thread : serial) { thread.join(); }
    if (peakCallbacks != 1 || nativeCalls != 160 || displayCalls != 160 || videoCalls != 160) {
        std::fprintf(stderr, "same-map serialization failed: peak=%u\n", peakCallbacks.load());
        return 1;
    }

    // 异 map 并行：两个 map 可同时进入回调，不能被全局锁串行化。
    resetCounters();
    holdCallbacks.store(true);
    std::thread first(
        []
        {
            currentMap = reinterpret_cast<void*>(0x3000);
            callHook();
        });
    std::thread second(
        []
        {
            currentMap = reinterpret_cast<void*>(0x4000);
            callHook();
        });
    if (!waitFor(activeCallbacks, 2)) {
        std::fprintf(stderr, "different maps did not overlap\n");
        return 1;
    }
    holdCallbacks.store(false);
    first.join();
    second.join();
    if (peakCallbacks != 2 || nativeCalls != 2 || displayCalls != 2 || videoCalls != 2) {
        std::fprintf(stderr, "different-map parallel path failed\n");
        return 1;
    }

    // 64 槽饱和：第 65 个 map 等待，释放后继续；不得丢失或重复执行。
    resetCounters();
    holdCallbacks.store(true);
    std::vector<std::thread> blocked;
    for (unsigned i = 0; i < 65; ++i) {
        blocked.emplace_back(
            [i]
            {
                currentMap = reinterpret_cast<void*>(0x5000 + i * 0x100);
                callHook();
            });
    }
    if (!waitFor(activeCallbacks, 64)) {
        std::fprintf(stderr, "registry did not reach 64 slots\n");
        return 1;
    }
    holdCallbacks.store(false);
    for (auto& thread : blocked) { thread.join(); }
    if (peakCallbacks != 64 || nativeCalls != 65 || displayCalls != 65 || videoCalls != 65
        || UserPatchCoordinator::leaseRejections() != 0 || NRed::value("NRedPatchCoordinatorPeak") != 64)
    {
        std::fprintf(stderr, "64-slot saturation failed: peak=%u calls=%u reject=%u\n", peakCallbacks.load(),
                     displayCalls.load(), UserPatchCoordinator::leaseRejections());
        return 1;
    }

    // 同线程嵌套：已有 map lease 时不再重入回调，保持 fail closed。
    resetCounters();
    nestedInvoked.store(false);
    UserPatchCoordinator::registerDisplay(
        [](void* process, void* args, const int result)
        {
            if (!nestedInvoked.exchange(true)) {
                UserPatchCoordinator::coordinate(process, args, result, currentMap);
            }
            return displayCallback(process, args, result);
        });
    callHook();
    if (!nestedInvoked.load() || displayCalls != 1 || videoCalls != 1) {
        std::fprintf(stderr, "nested coordinate was not rejected\n");
        return 1;
    }
    UserPatchCoordinator::registerDisplay(displayCallback);

    // 只有一个模块注册时必须跳过协调器，保留旧路径。
    UserPatchCoordinator::setActive(false);
    UserPatchCoordinator::clearVideo();
    KernelPatcher single;
    UserPatchCoordinator::init(single);
    if (UserPatchCoordinator::active() || NRed::value("NRedPatchCoordinatorSkipped") != 1) {
        std::fprintf(stderr, "single-module coordinator was not skipped\n");
        return 1;
    }

    std::printf("PASS DVPF coordinator probe: native once; display/video order; same-map serialization; different-map parallelism; "
                "64-slot saturation and wakeup; nested fail-closed; single-module fallback\n");
    return 0;
}
