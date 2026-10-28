// DVPF：显示域只读清单；区分 OpenGL 校验页、Metal 共享缓存与内核显示层。
// 本文件只描述离线证据和运行时准入边界，不保存进程地址，也不执行写入。
#pragma once
#include <VPF/PatchManifest.hpp>

namespace VPF
{

    // 四种显示执行域与只读证据域；不得把用户态 COW 事务套到内核路径。
    enum class DisplayDomain : UInt8
    {
        OpenGLValidatedPage,
        MetalSharedCache,
        KernelGraphics,
        KernelFramebuffer,
        GPUObservation,
    };

    enum class DisplayFeature : UInt16
    {
        OpenGLHwInfo,
        OpenGLDualQuad,
        MetalSrdShared,
        MetalLegacyBlend,
        MetalComputeScratch,
        MetalPipelineConstructor,
        MetalSubmit,
        MetalSubmitGuard,
        KernelGFX9Capabilities,
        KernelDCNScanout,
        KernelFramebuffer,
        HDRResearch,
    };

    // 安装和回滚的组边界；SubmitGuard 不并入普通渲染核心组。
    enum class DisplayGroup : UInt8
    {
        GLHwInfo,
        GLDualQuad,
        MetalRenderCore,
        MetalScratch,
        MetalSubmitSafety,
        KernelGFX9,
        KernelScanout,
        HDRResearch,
    };

    constexpr UInt32 DisplayGroupCount = 8;

    constexpr UInt32 displayGroupBit(const DisplayGroup group) { return 1U << static_cast<UInt32>(group); }

    constexpr UInt32 allDisplayGroupBits = (1U << DisplayGroupCount) - 1;

    // 证明强度随执行域变化；不用短签名替代完整函数或完整页块。
    enum class DisplayEvidence : UInt8
    {
        ExactKnownPage,
        FullPageLocalBlock,
        FullFunctionWithRefs,
        FullFunctionWithScopedSections,
        ExactStaticPayload,
        RuntimeTrampoline,
        KernelSymbolAndABI,
        DescriptiveOnly,
    };

    // 页内/内核路径的静态站点；dynamicOffset 标记由运行时填写的绝对目标。
    struct DisplaySiteTemplate
    {
        UInt64       offset;
        UInt32       size;
        const UInt8* original;
        const UInt8* replacement;
        UInt32       dynamicOffset;
        UInt32       dynamicSize;
        UInt32       functionIndex;
        UInt32       instructionOffset;
    };

    struct DisplayManifest
    {
        DisplayFeature             feature;
        DisplayDomain              domain;
        DisplayGroup               group;
        UInt32                     osMask;
        UInt32                     deviceID;    // 0 表示该组共用同一份证据
        DisplayEvidence            evidence;
        FeatureId                  sharedFeature;    // MetalSharedCache 对应的 VPF 功能
        UInt32                     requiredGroupMask;
        UInt32                     conflictGroupMask;
        UInt32                     maxSites;
        UInt32                     maxPatchedBytes;
        bool                       pageLocalFallback;    // OpenGL：整页快速路径失败后允许页内完整块
        bool                       installable;          // 是否可由用户态计划器纳入安装
        const char*                imagePath;
        const char*                source;
        const char*                cacheUUID;
        const PatchSite*           sharedSites;    // Metal 共享缓存站点
        UInt32                     sharedSiteCount;
        const DisplaySiteTemplate* pageSites;    // OpenGL/SubmitGuard 站点
        UInt32                     pageSiteCount;
        const UInt8*               trampoline;    // SubmitGuard 载荷模板
        UInt32                     trampolineSize;
        UInt32                     trampolineDynamicOffset;
        UInt32                     trampolineDynamicSize;
    };

    constexpr UInt32 displayOsMask(const UInt32 major)
    {
        switch (major) {
            case 23: return OsDarwin23;
            case 24: return OsDarwin24;
            case 25: return OsDarwin25;
            default: return 0;
        }
    }

    constexpr bool valid(const DisplaySiteTemplate& site)
    {
        if (!site.original || !site.replacement || site.size == 0 || site.size > 512 || site.dynamicSize > site.size
            || site.dynamicOffset > site.size - site.dynamicSize)
        {
            return false;
        }
        return site.offset < UserSharedCache::UserLimit && site.size <= UserSharedCache::UserLimit - site.offset;
    }

    constexpr bool valid(const DisplayManifest& manifest)
    {
        if (manifest.osMask == 0 || manifest.osMask & ~OsAll || !manifest.imagePath || !manifest.source) {
            return false;
        }
        if (static_cast<UInt32>(manifest.group) >= DisplayGroupCount || manifest.maxSites > 32
            || manifest.sharedSiteCount > manifest.maxSites || manifest.pageSiteCount > manifest.maxSites)
        {
            return false;
        }
        if ((manifest.sharedSites != nullptr) != (manifest.sharedSiteCount != 0)
            || (manifest.pageSites != nullptr) != (manifest.pageSiteCount != 0)
            || (manifest.sharedSites != nullptr && manifest.pageSites != nullptr))
        {
            return false;
        }
        for (UInt32 i = 0; i < manifest.sharedSiteCount; ++i) {
            const auto& site = manifest.sharedSites[i];
            if (!site.original || !site.replacement || site.size == 0 || site.size > manifest.maxPatchedBytes) {
                return false;
            }
        }
        for (UInt32 i = 0; i < manifest.pageSiteCount; ++i) {
            if (!valid(manifest.pageSites[i]) || manifest.pageSites[i].size > manifest.maxPatchedBytes) {
                return false;
            }
        }
        switch (manifest.domain) {
            case DisplayDomain::OpenGLValidatedPage:
                return manifest.sharedSites == nullptr && manifest.pageSites != nullptr
                       && (manifest.evidence == DisplayEvidence::ExactKnownPage
                           || manifest.evidence == DisplayEvidence::FullPageLocalBlock);
            case DisplayDomain::MetalSharedCache:
                return manifest.sharedFeature != FeatureId::Count
                       && (manifest.sharedSites != nullptr || manifest.feature == DisplayFeature::MetalComputeScratch
                           || manifest.feature == DisplayFeature::MetalSubmit
                           || manifest.feature == DisplayFeature::MetalSubmitGuard);
            case DisplayDomain::KernelGraphics:
            case DisplayDomain::KernelFramebuffer:
                return manifest.sharedSites == nullptr && manifest.pageSites == nullptr
                       && manifest.evidence == DisplayEvidence::KernelSymbolAndABI && !manifest.installable;
            case DisplayDomain::GPUObservation:
                return manifest.sharedSites == nullptr && manifest.pageSites == nullptr
                       && manifest.evidence == DisplayEvidence::DescriptiveOnly && !manifest.installable;
        }
        return false;
    }

    template<size_t N>
    constexpr bool validAll(const DisplayManifest (&manifests)[N])
    {
        for (size_t i = 0; i < N; ++i) {
            if (!valid(manifests[i])) { return false; }
        }
        return true;
    }

    // 设备专用条目优先于共用条目；OS 位必须匹配。
    constexpr const DisplayManifest* find(const DisplayManifest* manifests, const UInt32 count,
                                          const DisplayFeature feature, const UInt32 osMask, const UInt32 deviceID)
    {
        if (!manifests) { return nullptr; }
        const DisplayManifest* fallback = nullptr;
        for (UInt32 i = 0; i < count; ++i) {
            const auto& entry = manifests[i];
            if (entry.feature != feature || (entry.osMask & osMask) == 0) { continue; }
            if (entry.deviceID == deviceID && deviceID != 0) { return &entry; }
            if (entry.deviceID == 0) { fallback = &entry; }
        }
        return fallback;
    }

}    // namespace VPF
