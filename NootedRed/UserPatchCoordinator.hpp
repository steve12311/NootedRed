// DVPF：同一 vm_map 的显示/视频共享缓存事务协调器。
// 本头只保存参与者和门状态；真正的 Hook 安装与原生调用在 UserPatchCoordinator.cpp。
#pragma once
#include <IOKit/IOLocks.h>
#include <IOKit/IOTypes.h>
#include <UserPatchMapGate.hpp>

class KernelPatcher;

namespace UserPatchCoordinator
{

    constexpr UInt32 MaxMapSlots = 64;

    // 回调只接收已成功返回的原生结果；参数保持调用者自己的结构布局。
    using PatchCallback = int (*)(void* process, void* args, int result);

    inline PatchCallback& displayCallback()
    {
        static PatchCallback value{nullptr};
        return value;
    }

    inline PatchCallback& videoCallback()
    {
        static PatchCallback value{nullptr};
        return value;
    }

    inline UInt8& activeFlag()
    {
        static UInt8 value{0};
        return value;
    }

    inline IOLock*& gateLock()
    {
        static IOLock* value{nullptr};
        return value;
    }

    inline UserPatchMapGate::Registry<MaxMapSlots>& registry()
    {
        static UserPatchMapGate::Registry<MaxMapSlots> value{};
        return value;
    }

    inline UInt32& nativeCalls()
    {
        static UInt32 value{0};
        return value;
    }

    inline UInt32& coordinatedRuns()
    {
        static UInt32 value{0};
        return value;
    }

    inline UInt32& leaseRejections()
    {
        static UInt32 value{0};
        return value;
    }

    inline UInt32& reportedPeak()
    {
        static UInt32 value{0};
        return value;
    }

    inline void clearDisplay() { displayCallback() = nullptr; }

    inline void clearVideo() { videoCallback() = nullptr; }

    inline void registerDisplay(const PatchCallback callback)
    {
        if (callback) { displayCallback() = callback; }
    }

    inline void registerVideo(const PatchCallback callback)
    {
        if (callback) { videoCallback() = callback; }
    }

    inline bool active() { return __atomic_load_n(&activeFlag(), __ATOMIC_RELAXED) != 0; }

    inline void setActive(const bool value)
    {
        __atomic_store_n(&activeFlag(), static_cast<UInt8>(value ? 1 : 0), __ATOMIC_RELEASE);
    }

    // 协调器拥有 Hook 时，模块 Hook 只调用原生链并让出补丁执行权。
    inline bool deferModulePatches() { return active(); }

    // 只在 native 成功返回后调用；mapKey 必须是当前 task 的确切 vm_map 或 task 标识。
    int coordinate(void* process, void* args, int result, void* mapKey);

    // 两个参与模块都成功安装后才启用；只安装一个时保持旧路径。
    void init(KernelPatcher& patcher);

}    // namespace UserPatchCoordinator
