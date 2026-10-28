// DVPF：只读显示计划；页计划引用当前校验页，共享缓存计划引用 VerifiedPatchPlan。
#pragma once
#include <UserOpenGLData.hpp>
#include <UserOpenGLResolver.hpp>
#include <VPF/DisplayManifest.hpp>
#include <VPF/VerifiedPatchPlan.hpp>

namespace VPF
{

    constexpr UInt32 DisplayPageSize    = 4096;
    constexpr UInt64 DisplayMaxFileSize = 32ULL * 1024 * 1024;

    // 原生验证结果；只有 1/0xF、tainted=0、nx=0 和精确 vnode 才能进入页计划。
    struct OpenGLPageContext
    {
        UInt32 validated;
        UInt32 tainted;
        UInt32 nx;
        UInt64 fileOffset;    // 当前页起始文件偏移，必须页对齐
        bool   vnodeMatched;
    };

    // 页内计划只引用解析器缓冲；调用方必须保证 Resolved 在计划使用期间有效。
    class VerifiedPagePlan
    {
        const DisplayManifest*              manifest_{nullptr};
        const UserOpenGLData::Device*       device_{nullptr};
        const UserOpenGLResolver::Resolved* resolved_{nullptr};
        UInt64                              pageFileOffset_{0};
        UInt64                              patchFileOffset_{0};
        UInt64                              targetIdentity_{0};
        UInt64                              referenceOffset_{0};
        UInt32                              functionIndex_{NoFunction};
        UInt32                              instructionOffset_{0};
        UInt32                              validated_{0}, tainted_{0}, nx_{0};
        bool                                valid_{false};
        bool                                exact_{false};
        bool                                dynamic_{false};
        bool                                alreadyPatched_{false};

    public:
        bool reset(const DisplayManifest& manifest, const UserOpenGLData::Device& device,
                   const UserOpenGLResolver::Resolved& resolved, const OpenGLPageContext& context)
        {
            manifest_          = &manifest;
            device_            = &device;
            resolved_          = &resolved;
            valid_             = false;
            pageFileOffset_    = context.fileOffset;
            validated_         = context.validated;
            tainted_           = context.tainted;
            nx_                = context.nx;
            exact_             = !resolved.dynamic;
            dynamic_           = resolved.dynamic;
            alreadyPatched_    = resolved.alreadyPatched;
            referenceOffset_   = 0;
            functionIndex_     = NoFunction;
            instructionOffset_ = 0;
            patchFileOffset_   = 0;
            targetIdentity_    = 0;

            if (manifest.domain != DisplayDomain::OpenGLValidatedPage || !manifest.pageSites
                || manifest.pageSiteCount != 1 || !context.vnodeMatched
                || (context.validated != 1 && context.validated != 0xF) || context.tainted != 0 || context.nx != 0
                || (context.fileOffset & (DisplayPageSize - 1)) != 0
                || context.fileOffset > DisplayMaxFileSize - DisplayPageSize)
            {
                return false;
            }
            if ((manifest.deviceID != 0 && manifest.deviceID != device.deviceID)
                || (manifest.osMask & displayOsMask(device.major)) == 0)
            {
                return false;
            }
            const auto& site = manifest.pageSites[0];
            if (resolved.size != site.size || resolved.offset > DisplayPageSize - resolved.size
                || site.functionIndex >= 2)
            {
                return false;
            }
            patchFileOffset_   = context.fileOffset + resolved.offset;
            referenceOffset_   = site.offset;
            functionIndex_     = site.functionIndex;
            instructionOffset_ = site.instructionOffset;
            // 页面可整体移动，相对引用会变化；只比较非 rel32 字节并重新核验目标 RVA。
            const auto& function = device.resolver->functions[site.functionIndex];
            const auto  start    = device.resolver->locations[site.functionIndex].offset;
            for (UInt32 i = 0; i < site.size; ++i) {
                if (!UserCacheResolver::skipped(site.instructionOffset + i, function.patchedRefs, function.patchedCount)
                    && resolved.patched[i] != site.replacement[i])
                {
                    return false;
                }
            }
            for (UInt32 r = 0; r < function.patchedCount; ++r) {
                const auto& ref    = function.patchedRefs[r];
                UInt64      target = 0;
                if (ref.indirect || ref.offset < start || ref.offset - start > site.size - 4
                    || !UserSharedCache::relativeTarget(patchFileOffset_, ref.offset - start, resolved.patched, target)
                    || target != ref.rva)
                {
                    return false;
                }
            }
            const auto* expected = resolved.alreadyPatched ? site.replacement : site.original;
            const auto& refs     = resolved.alreadyPatched ? function.patchedRefs : function.originalRefs;
            const auto  refCount = resolved.alreadyPatched ? function.patchedCount : function.originalCount;
            for (UInt32 i = 0; i < site.size; ++i) {
                if (!UserCacheResolver::skipped(site.instructionOffset + i, refs, refCount)
                    && resolved.original[i] != expected[i])
                {
                    return false;
                }
            }
            for (UInt32 r = 0; r < refCount; ++r) {
                const auto& ref    = refs[r];
                UInt64      target = 0;
                if (ref.indirect || ref.offset < start || ref.offset - start > site.size - 4
                    || !UserSharedCache::relativeTarget(patchFileOffset_, ref.offset - start, resolved.original, target)
                    || target != ref.rva)
                {
                    return false;
                }
            }
            // 仅作本次解析的诊断标识；不得跨进程或跨 map 复用。
            targetIdentity_ = (static_cast<UInt64>(device.major) << 56) | (static_cast<UInt64>(device.deviceID) << 40)
                              | (patchFileOffset_ & 0xFFFFFFFFFFULL);
            valid_ = true;
            return true;
        }

        bool valid() const { return valid_; }
        bool installable() const { return valid_ && !alreadyPatched_; }
        bool alreadyInstalled() const { return valid_ && alreadyPatched_; }
        // 页交付证明强度由本次匹配决定，不能把动态回退报成精确参考页。
        DisplayEvidence evidence() const
        {
            if (!valid_) { return DisplayEvidence::DescriptiveOnly; }
            return dynamic_ ? DisplayEvidence::FullPageLocalBlock : DisplayEvidence::ExactKnownPage;
        }
        PlanState state() const
        {
            if (!valid_) { return PlanState::Unknown; }
            return alreadyPatched_ ? PlanState::AllPatched : PlanState::AllOriginal;
        }
        const DisplayManifest*              manifest() const { return manifest_; }
        const UserOpenGLData::Device*       device() const { return device_; }
        const UserOpenGLResolver::Resolved* resolved() const { return resolved_; }
        UInt64                              pageFileOffset() const { return pageFileOffset_; }
        UInt64                              patchFileOffset() const { return patchFileOffset_; }
        UInt64                              targetIdentity() const { return targetIdentity_; }
        UInt64                              referenceOffset() const { return referenceOffset_; }
        UInt32                              functionIndex() const { return functionIndex_; }
        UInt32                              instructionOffset() const { return instructionOffset_; }
        UInt32                              validated() const { return validated_; }
        UInt32                              tainted() const { return tainted_; }
        UInt32                              nx() const { return nx_; }
        bool                                exactPage() const { return exact_; }
        bool                                dynamic() const { return dynamic_; }
        const UInt8*                        original() const { return resolved_ ? resolved_->original : nullptr; }
        const UInt8*                        replacement() const { return resolved_ ? resolved_->patched : nullptr; }
        UInt32                              size() const { return resolved_ ? resolved_->size : 0; }
    };

    // 计划视图统一两类后端；调用方不能据此认为 GPU 已正确执行。
    struct DisplayPlanView
    {
        const DisplayManifest*   manifest{nullptr};
        const VerifiedPagePlan*  page{nullptr};
        const VerifiedPatchPlan* shared{nullptr};

        bool valid() const
        {
            if (!manifest || (page == nullptr) == (shared == nullptr)) { return false; }
            if (page) { return page->valid() && page->manifest() == manifest; }
            if (!shared->valid() || manifest->domain != DisplayDomain::MetalSharedCache
                || shared->feature() != manifest->sharedFeature || shared->siteCount() > manifest->maxSites)
            {
                return false;
            }
            // 有静态站点时必须逐项核对顺序、函数位置和字节；仅函数证据的组不套用静态表。
            if (manifest->sharedSites != nullptr) {
                if (shared->siteCount() != manifest->sharedSiteCount) { return false; }
                for (UInt32 i = 0; i < manifest->sharedSiteCount; ++i) {
                    const auto& site     = shared->site(i);
                    const auto& expected = manifest->sharedSites[i];
                    if (site.size != expected.size || site.functionIndex != expected.functionIndex
                        || site.instructionOffset != expected.instructionOffset
                        || memcmp(site.original, expected.original, site.size) != 0
                        || memcmp(site.replacement, expected.replacement, site.size) != 0)
                    {
                        return false;
                    }
                }
                return true;
            }
            return true;
        }

        bool            installable() const { return valid() && state() == PlanState::AllOriginal; }
        bool            alreadyInstalled() const { return valid() && state() == PlanState::AllPatched; }
        DisplayEvidence evidence() const
        {
            if (page) { return page->evidence(); }
            if (shared && manifest) { return manifest->evidence; }
            return DisplayEvidence::DescriptiveOnly;
        }
        PlanState state() const
        {
            if (page) { return page->state(); }
            if (shared) { return shared->state(); }
            return PlanState::Unknown;
        }
        UInt32 siteCount() const
        {
            if (page) { return page->valid() ? 1 : 0; }
            if (shared) { return shared->siteCount(); }
            return 0;
        }
        DisplayDomain domain() const { return manifest ? manifest->domain : DisplayDomain::GPUObservation; }
    };

}    // namespace VPF
