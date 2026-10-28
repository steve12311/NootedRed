// DVPF：SubmitGuard 等可执行跳板的显式生命周期；入口仍可能引用时禁止释放页面。
#pragma once
#include <VPF/PatchManifest.hpp>

namespace VPF
{

    enum class TrampolineState : UInt8
    {
        Empty,
        Allocated,
        Filled,
        Executable,
        EntryLinked,
        Verified,
        Retained,
        Released,
    };

    enum class TrampolineFault : UInt8
    {
        None,
        InvalidTransition,
        AllocationFailed,
        FillFailed,
        ProtectionFailed,
        EntryLinkFailed,
        EntryVerifyFailed,
        EntryRestoreFailed,
        ReleaseRejected,
    };

    struct TrampolineRecord
    {
        UInt64          page{0};
        UInt64          entry{0};
        UInt32          payloadSize{0};
        TrampolineState state{TrampolineState::Empty};
        bool            entryLinked{false};
        bool            pageRetained{false};
        TrampolineFault fault{TrampolineFault::None};
    };

    constexpr const char* trampolineStateName(const TrampolineState state)
    {
        switch (state) {
            case TrampolineState::Empty      : return "Empty";
            case TrampolineState::Allocated  : return "Allocated";
            case TrampolineState::Filled     : return "Filled";
            case TrampolineState::Executable : return "Executable";
            case TrampolineState::EntryLinked: return "EntryLinked";
            case TrampolineState::Verified   : return "Verified";
            case TrampolineState::Retained   : return "Retained";
            case TrampolineState::Released   : return "Released";
        }
        return "Unknown";
    }

    // 纯状态机：不分配内存、不写页；真实 VM 操作由既有安装器执行。
    class ExecutableTrampolineResource
    {
        TrampolineRecord record_{};

        bool invalid(const TrampolineFault fault)
        {
            record_.fault = fault;
            return false;
        }

    public:
        const TrampolineRecord& record() const { return record_; }

        bool allocate(const UInt64 page)
        {
            if (record_.state != TrampolineState::Empty || page == 0) {
                return invalid(TrampolineFault::AllocationFailed);
            }
            record_.page         = page;
            record_.state        = TrampolineState::Allocated;
            record_.entryLinked  = false;
            record_.pageRetained = false;
            record_.fault        = TrampolineFault::None;
            return true;
        }

        bool fill(const UInt32 payloadSize)
        {
            if (record_.state != TrampolineState::Allocated) { return invalid(TrampolineFault::FillFailed); }
            record_.payloadSize = payloadSize;
            record_.state       = TrampolineState::Filled;
            record_.fault       = TrampolineFault::None;
            return true;
        }

        bool makeExecutable()
        {
            if (record_.state != TrampolineState::Filled) { return invalid(TrampolineFault::ProtectionFailed); }
            record_.state = TrampolineState::Executable;
            record_.fault = TrampolineFault::None;
            return true;
        }

        bool linkEntry(const UInt64 entry)
        {
            if (record_.state != TrampolineState::Executable || entry == 0) {
                return invalid(TrampolineFault::EntryLinkFailed);
            }
            record_.entry       = entry;
            record_.entryLinked = true;
            record_.state       = TrampolineState::EntryLinked;
            record_.fault       = TrampolineFault::None;
            return true;
        }

        // 读回载荷/入口一致后才算 Verified；否则保留页面等待外部恢复入口。
        bool verify(const bool entryMatches)
        {
            if (record_.state != TrampolineState::EntryLinked) { return invalid(TrampolineFault::EntryVerifyFailed); }
            if (!entryMatches) {
                record_.state        = TrampolineState::Retained;
                record_.pageRetained = true;
                return invalid(TrampolineFault::EntryVerifyFailed);
            }
            record_.state = TrampolineState::Verified;
            record_.fault = TrampolineFault::None;
            return true;
        }

        // 回滚或卸载时确认入口已恢复；未恢复则保留页面，不自动重试。
        bool restoreEntry(const bool restored)
        {
            if (record_.state != TrampolineState::EntryLinked && record_.state != TrampolineState::Retained
                && record_.state != TrampolineState::Verified)
            {
                return invalid(TrampolineFault::EntryRestoreFailed);
            }
            if (!restored) {
                record_.entryLinked  = true;
                record_.pageRetained = true;
                record_.state        = TrampolineState::Retained;
                return invalid(TrampolineFault::EntryRestoreFailed);
            }
            record_.entryLinked  = false;
            record_.pageRetained = false;
            record_.state        = TrampolineState::Verified;
            record_.fault        = TrampolineFault::None;
            return true;
        }

        // 只有入口未链接或已确认恢复时才允许释放；Retained 不得直接释放。
        bool release()
        {
            if (record_.state == TrampolineState::Released) { return true; }
            if (record_.state == TrampolineState::EntryLinked || record_.state == TrampolineState::Retained
                || record_.entryLinked)
            {
                return invalid(TrampolineFault::ReleaseRejected);
            }
            if (record_.state != TrampolineState::Allocated && record_.state != TrampolineState::Filled
                && record_.state != TrampolineState::Executable && record_.state != TrampolineState::Verified)
            {
                return invalid(TrampolineFault::ReleaseRejected);
            }
            record_.state        = TrampolineState::Released;
            record_.pageRetained = false;
            record_.entryLinked  = false;
            record_.fault        = TrampolineFault::None;
            return true;
        }

        TrampolineRecord rollback(const bool entryRestored)
        {
            if (record_.state == TrampolineState::Empty || record_.state == TrampolineState::Released) {
                return record_;
            }
            if (record_.state == TrampolineState::EntryLinked || record_.state == TrampolineState::Retained) {
                if (!restoreEntry(entryRestored)) { return record_; }
            }
            release();
            return record_;
        }

        bool linked() const
        {
            return record_.entryLinked
                   && (record_.state == TrampolineState::EntryLinked || record_.state == TrampolineState::Verified
                       || record_.state == TrampolineState::Retained);
        }

        bool pageRetained() const { return record_.pageRetained; }

        // 测试用不变式：Released 不得同时保留入口跳转。
        bool invariant() const { return !(record_.state == TrampolineState::Released && record_.entryLinked); }
    };

}    // namespace VPF
