// DVPF：Metal 共享缓存计划执行器；只接收已验证/可读回核验的计划。
// 页面收集、COW、W^X、读回、回滚和权限恢复继续由 UserPatchMemory 公共实现完成。
#pragma once
#include <VPF/TransactionExecutor.hpp>
#include <VPF/VerifiedPatchPlan.hpp>

namespace VPF
{

    // 把显示解析器实际选出的补丁装入计划；站点字节仍由执行器安装前读回核验。
    // 不绑定视频 FeatureManifest，调用方必须在读回后 seal(PlanState)。
    template<typename Patch>
    inline bool adaptDisplayPatches(VerifiedPatchPlan& out, const UInt64 base, const UserSharedCache::Range& cache,
                                    const Patch* patches, const UInt32 patchCount)
    {
        if (!patches || patchCount == 0 || patchCount > MaxPlanSites || base == 0 || cache.size == 0) { return false; }
        out.reset(base, cache);
        for (UInt32 i = 0; i < patchCount; ++i) {
            const auto& patch = patches[i];
            if (patch.size == 0 || patch.size > MaxPlanPatchSize || patch.offset >= UserSharedCache::UserLimit - base
                || patch.size >= UserSharedCache::UserLimit - base - patch.offset)
            {
                return false;
            }
            if (!out.addSite(base + patch.offset, patch.offset, patch.size, NoFunction, 0, patch.original,
                             patch.patched))
            {
                return false;
            }
        }
        return true;
    }

    enum class MetalPrepareState : UInt8
    {
        Invalid,
        AllOriginal,
        AllPatched,
        Mixed,
    };

    enum class MetalPrepareFault : UInt8
    {
        None,
        InvalidPlan,
        ReadFailed,
        Mismatch,
        PageLimit,
        SealFailed,
    };

    struct MetalPrepareResult
    {
        MetalPrepareState state{MetalPrepareState::Invalid};
        MetalPrepareFault fault{MetalPrepareFault::None};
        UInt32            pageCount{0};
        UInt32            originals{0};
        UInt32            patched{0};
    };

    struct MetalPatchView
    {
        UInt64       offset;
        const UInt8* original;
        const UInt8* patched;
        UInt32       size;
    };

    // MaxPages 必须由调用方按每次事务的实际栈/堆预算给出；不在这里分配页面。
    template<typename Ops, typename Page, UInt32 MaxPatchSize, UInt32 MaxPages, UInt32 MaxSites = MaxPlanSites>
    class MetalTransaction
    {
        Ops&               ops_;
        VerifiedPatchPlan& plan_;
        UInt64             base_;
        UInt8 (*saved_)[MaxPatchSize];
        Page (&pages_)[MaxPages];
        MetalPatchView patches_[MaxSites]{};
        UInt32         count_{0};
        UInt32         pageCount_{0};
        MetalPrepareState state_{MetalPrepareState::Invalid};
        bool              prepared_{false};

    public:
        MetalTransaction(Ops& ops, VerifiedPatchPlan& plan, const UInt64 base, UInt8 (*saved)[MaxPatchSize],
                         Page (&pages)[MaxPages])
            : ops_(ops), plan_(plan), base_(base), saved_(saved), pages_(pages)
        {
        }

        MetalTransaction(const MetalTransaction&)            = delete;
        MetalTransaction& operator=(const MetalTransaction&) = delete;

        // 读取每个站点并按 original/replacement 判定状态；全部读取、页面收集和
        // 计划封存成功后才允许进入写阶段。
        bool prepare(MetalPrepareResult& result)
        {
            result = {};
            count_ = 0;
            pageCount_ = 0;
            state_ = MetalPrepareState::Invalid;
            prepared_ = false;
            count_ = plan_.siteCount();
            if (!saved_ || count_ == 0 || count_ > MaxSites || base_ == 0) {
                result.fault = MetalPrepareFault::InvalidPlan;
                return false;
            }
            UInt32 originals{0}, patched{0};
            for (UInt32 i = 0; i < count_; ++i) {
                const auto& site = plan_.site(i);
                if (site.size == 0 || site.size > MaxPatchSize || site.address < base_
                    || site.address - base_ != site.offset || site.address >= UserSharedCache::UserLimit
                    || site.size > UserSharedCache::UserLimit - site.address)
                {
                    result.fault = MetalPrepareFault::InvalidPlan;
                    return false;
                }
                if (ops_.read(site.address, saved_[i], site.size) != 0) {
                    result.fault = MetalPrepareFault::ReadFailed;
                    return false;
                }
                if (memcmp(saved_[i], site.original, site.size) == 0) { ++originals; }
                else if (memcmp(saved_[i], site.replacement, site.size) == 0) { ++patched; }
                else {
                    result.fault = MetalPrepareFault::Mismatch;
                    return false;
                }
                if (!ops_.collectPages(pages_, pageCount_, site.address, site.size)) {
                    result.fault = MetalPrepareFault::PageLimit;
                    return false;
                }
                patches_[i] = {site.offset, site.original, site.replacement, site.size};
            }
            if (originals == count_) { state_ = MetalPrepareState::AllOriginal; }
            else if (patched == count_) { state_ = MetalPrepareState::AllPatched; }
            else { state_ = MetalPrepareState::Mixed; }

            const auto planState = state_ == MetalPrepareState::AllOriginal ? PlanState::AllOriginal :
                                   (state_ == MetalPrepareState::AllPatched ? PlanState::AllPatched :
                                                                              PlanState::Mixed);
            if (!plan_.seal(planState)) {
                result.fault = MetalPrepareFault::SealFailed;
                return false;
            }
            prepared_ = true;
            result.state = state_;
            result.pageCount = pageCount_;
            result.originals = originals;
            result.patched = patched;
            return true;
        }

        // 仅 AllOriginal 进入写入；AllPatched/Mixed 由调用方按幂等或拒绝路径处理。
        template<typename BeforeWrite>
        typename SharedRegionTransaction<Ops, MetalPatchView, Page, MaxPatchSize>::Result
        commit(BeforeWrite beforeWrite)
        {
            using Executor = SharedRegionTransaction<Ops, MetalPatchView, Page, MaxPatchSize>;
            if (!prepared_ || state_ != MetalPrepareState::AllOriginal) return {};
            Executor executor(ops_, base_, patches_, count_, saved_, pages_, pageCount_, 0);
            return executor.commit(beforeWrite);
        }

        MetalPrepareState state() const { return state_; }
        UInt32            siteCount() const { return count_; }
        UInt32            pageCount() const { return pageCount_; }
        const VerifiedPatchPlan& plan() const { return plan_; }
    };

}    // namespace VPF
