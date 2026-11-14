// DVPF：同一 vm_map 的显示/视频共享缓存事务协调器。
// 参与者、门状态与 Hook 的唯一实现在 UserPatchCoordinator.cpp。
#pragma once
#include <IOKit/IOLocks.h>
#include <IOKit/IOTypes.h>
#include <string.h>
#include <UserPatchMapGate.hpp>

class KernelPatcher;

namespace UserPatchCoordinator
{

    constexpr UInt32 MaxMapSlots = 64;

    // 共享缓存头只读一次，供两个参与者复用；与 dyld_cache_header 前 104 字节一致。
    constexpr UInt32 HeaderSize = 104;
    // 证明：一个已安装站点内与原始字节不同的一小段替换字节。
    constexpr UInt32 ProofSize = 16;

    // 与内核 copyin 的签名一致：user_addr_t、void*、size_t。
    using Reader = int (*)(unsigned long long address, void* value, unsigned long size);

    struct Proof
    {
        UInt64 address{0};
        UInt32 size{0};
        UInt8  bytes[ProofSize]{};
    };

    enum class Outcome : UInt8
    {
        Pending,    // 未完成、失败或不确定：下次必须完整搜索
        Idle,       // 由配置决定本模块不写任何代码
        Patched,    // 全部站点已核验为补丁状态，并提供 proof
    };

    struct Participant
    {
        Outcome outcome{Outcome::Pending};
        Proof   proof{};
    };

    // 单次协调事务：输入为一次性读取的缓存头，输出为两个参与者的结果。
    struct Transaction
    {
        UInt64      base{0};
        UInt8       header[HeaderSize]{};
        bool        cacheRead{false};
        Participant display{}, video{};

        // 参与者使用协调器已读的头；没有时（旧路径或读失败）自行读取。
        bool sharedHeader(UInt64& outBase, UInt8* outHeader) const
        {
            if (!cacheRead) { return false; }
            outBase = base;
            memcpy(outHeader, header, HeaderSize);
            return true;
        }
    };

    // 从一个站点取出包含首个差异字节的窗口；original 与 replacement 完全相同时失败。
    inline bool makeProof(Proof& proof, const UInt64 address, const UInt8* original, const UInt8* replacement,
                          const UInt32 size)
    {
        if (!original || !replacement || size == 0 || address == 0) { return false; }
        UInt32 first = 0;
        while (first < size && original[first] == replacement[first]) { ++first; }
        if (first == size) { return false; }
        const auto length = size < ProofSize ? size : ProofSize;
        const auto start  = first + length > size ? size - length : first;
        proof.address     = address + start;
        proof.size        = length;
        memcpy(proof.bytes, replacement + start, length);
        return true;
    }

    // 用计划中第一个可区分的站点作为 proof；没有可用站点时保持 Pending。
    template<typename Plan>
    inline void settlePatched(Participant& participant, const Plan& plan)
    {
        participant.outcome = Outcome::Pending;
        for (UInt32 i = 0; i < plan.siteCount(); ++i) {
            const auto& site = plan.site(i);
            if (makeProof(participant.proof, site.address, site.original, site.replacement, site.size)) {
                participant.outcome = Outcome::Patched;
                return;
            }
        }
    }

    // 回调只接收已成功返回的原生结果；参数保持调用者自己的结构布局。
    using PatchCallback = int (*)(void* process, void* args, int result, Transaction& transaction);

    // 进程过滤：返回 false 表示该进程不加载 GPU 用户态驱动，可跳过全部共享缓存补丁。
    using ProcessPredicate = bool (*)(void* process);

    PatchCallback&                           displayCallback();
    PatchCallback&                           videoCallback();
    UInt8&                                   activeFlag();
    IOLock*&                                 gateLock();
    UserPatchMapGate::Registry<MaxMapSlots>& registry();
    UInt32&                                  nativeCalls();
    UInt32&                                  coordinatedRuns();
    UInt32&                                  leaseRejections();
    UInt32&                                  reportedPeak();
    UInt32&                                  skippedRuns();
    Reader&                                  reader();

    void   configureDisplay(bool enabled, UInt32 major);
    UInt32 requestedDisplayGroups();

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

    // 未注册读取器时不预读缓存头，也不跳过任何搜索。
    inline void registerReader(const Reader value)
    {
        if (value) { reader() = value; }
    }

    // 已完成补丁的 map 记录；vm_map 销毁时清除，避免地址复用后误用旧结果。
    void forgetMap(void* mapKey);

    inline bool active() { return __atomic_load_n(&activeFlag(), __ATOMIC_RELAXED) != 0; }

    inline void setActive(const bool value)
    {
        __atomic_store_n(&activeFlag(), static_cast<UInt8>(value ? 1 : 0), __ATOMIC_RELEASE);
    }

    ProcessPredicate& processFilter();

    // 未注册过滤器时保持原行为：所有进程都进入补丁路径。
    inline bool processAllowed(void* process)
    {
        const auto filter = processFilter();
        return filter == nullptr || filter(process);
    }

    // 协调器拥有 Hook 时，模块 Hook 只调用原生链并让出补丁执行权。
    inline bool deferModulePatches() { return active(); }

    // 只在 native 成功返回后调用；mapKey 必须是当前 task 的确切 vm_map 或 task 标识。
    int coordinate(void* process, void* args, int result, void* mapKey);

    // 两个参与模块都成功安装后才启用；只安装一个时保持旧路径。
    void init(KernelPatcher& patcher);

}    // namespace UserPatchCoordinator
