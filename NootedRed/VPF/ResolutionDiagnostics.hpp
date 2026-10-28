// VPF：共享缓存解析的冷/热路径计数；只做诊断，不参与匹配或写入决策。
#pragma once
#include <IOKit/IOTypes.h>

namespace VPF
{

    enum class ResolutionEvent : UInt32
    {
        Cold,
        Hot,
        Hit,
        Miss,
        Reusable,
        HintOnly,
        Fallback,
        Collision,
        Full,
        VerifyFailure,
        ScanBytes,
        VerifyBytes,
        Candidates,
        Count
    };

    struct ResolutionCounters
    {
        UInt64 values[static_cast<UInt32>(ResolutionEvent::Count)]{};

        void record(const ResolutionEvent event, const UInt64 amount = 1)
        {
            if (event == ResolutionEvent::Count) { return; }
            __atomic_fetch_add(&values[static_cast<UInt32>(event)], amount, __ATOMIC_RELAXED);
        }

        UInt64 load(const ResolutionEvent event) const
        {
            if (event == ResolutionEvent::Count) { return 0; }
            return __atomic_load_n(&values[static_cast<UInt32>(event)], __ATOMIC_RELAXED);
        }
    };

    namespace ResolutionDiagnostics
    {

        inline ResolutionCounters counters{};
        inline bool               resolutionCacheEnabled{false};

        // 默认关闭；只有显式启动参数才允许解析器使用跨进程缓存。
        inline void setEnabled(const bool value) { resolutionCacheEnabled = value; }
        inline bool enabled() { return resolutionCacheEnabled; }

    }    // namespace ResolutionDiagnostics

}    // namespace VPF
