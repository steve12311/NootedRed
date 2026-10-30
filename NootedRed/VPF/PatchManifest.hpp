// VPF：只读补丁清单类型；描述离线证据，不持有运行时地址，也不参与写入。
#pragma once
#include <UserCacheResolver.hpp>

namespace VPF
{

    // 功能组标识；系统和设备准入仍由运行时策略控制。
    enum class FeatureId : UInt32
    {
        DisplaySRD,
        DisplayLegacyBlend,
        DisplayComputeScratch,
        DisplaySubmitGuard,
        DisplayPipelineConstructor,
        DisplaySubmit,
        Vcn2Decode,
        Vcn2Encode,
        AppleGVASelection,
        FairPlayNativeDecodeSubset,
        Vcn2HEVCMain10,
        // DVPF 显示功能组；不改变上面视频功能的序号。
        DisplayOpenGLHwInfo,
        DisplayOpenGLDualQuad,
        KernelGFX9Capabilities,
        KernelDCNScanout,
        KernelFramebuffer,
        DisplayHDRResearch,
        Count,
    };

    // 证据等级；离线语义证据不自动生成可安装补丁。
    enum class EvidenceKind : UInt8
    {
        ExactValidatedBuild,
        RelocationAwareFullFunction,
        OfflineProvenSemanticVariant,
    };

    // 系统位掩码按 Darwin 主版本编号，不使用 macOS 市场版本号。
    constexpr UInt32 OsDarwin23 = 1U << 0;
    constexpr UInt32 OsDarwin24 = 1U << 1;
    constexpr UInt32 OsDarwin25 = 1U << 2;
    constexpr UInt32 OsAll      = OsDarwin23 | OsDarwin24 | OsDarwin25;

    constexpr UInt32 NoFunction = 0xFFFFFFFFU;

    struct PatchSite
    {
        UInt32       functionIndex;
        UInt32       instructionOffset;
        UInt32       size;
        const UInt8* original;
        const UInt8* replacement;
    };

    struct ResolverView
    {
        const UserCacheResolver::ImageReference* images;
        UInt32                                   imageCount;
        const UserCacheResolver::Function*       functions;
        UInt32                                   functionCount;
        const UserCacheResolver::Location*       locations;
        UInt32                                   locationCount;
        const PatchSite*                         sites;
        UInt32                                   siteCount;
    };

    struct FeatureManifest
    {
        FeatureId     feature;
        UInt32        osMask;
        UInt32        deviceID;    // 0 表示该功能组共用同一份证据
        EvidenceKind  evidence;
        ResolverView  resolver;
        const char*   source;      // 生成数据头
        const char*   cacheUUID;   // 参考缓存 UUID，仅作溯源，不作兼容证明
    };

    // makeSites 在编译期把生成的 Patch[] 与 Location[] 对齐；不复制字节。
    template<typename Patch, size_t N>
    struct SiteArray
    {
        PatchSite sites[N];
    };

    template<typename Patch, size_t N>
    constexpr SiteArray<Patch, N> makeSites(const Patch (&patches)[N],
                                            const UserCacheResolver::Location (&locations)[N])
    {
        SiteArray<Patch, N> result{};
        for (size_t i = 0; i < N; ++i) {
            result.sites[i] = {locations[i].function, locations[i].offset, patches[i].size, patches[i].original,
                               patches[i].patched};
        }
        return result;
    }

    // 没有完整函数证据的静态表（例如 SRD / LegacyBlend）只记录补丁位置。
    template<typename Patch, size_t N>
    constexpr SiteArray<Patch, N> makeSites(const Patch (&patches)[N])
    {
        SiteArray<Patch, N> result{};
        for (size_t i = 0; i < N; ++i) {
            result.sites[i] = {NoFunction, 0, patches[i].size, patches[i].original, patches[i].patched};
        }
        return result;
    }

    constexpr bool valid(const ResolverView& view)
    {
        if (view.imageCount > 16 || view.functionCount > 32 || view.locationCount > 32 || view.siteCount > 32) {
            return false;
        }
        if ((view.imageCount != 0 && view.images == nullptr)
            || (view.functionCount != 0 && view.functions == nullptr)
            || (view.siteCount != 0 && view.sites == nullptr)
            || (view.locationCount != 0 && view.locations == nullptr))
        {
            return false;
        }
        // Scratch/Submit 只有函数证据、没有静态补丁表；其余功能组的站点不能多于位置表。
        if (view.locations != nullptr && view.sites != nullptr && view.siteCount > view.locationCount) { return false; }
        return true;
    }

    constexpr bool valid(const FeatureManifest& manifest)
    {
        return valid(manifest.resolver) && manifest.source != nullptr;
    }

    template<size_t N>
    constexpr bool validAll(const FeatureManifest (&features)[N])
    {
        for (size_t i = 0; i < N; ++i) {
            if (!valid(features[i])) { return false; }
        }
        return true;
    }

    constexpr const FeatureManifest* find(const FeatureManifest* features, UInt32 count, FeatureId feature,
                                          UInt32 osMask, UInt32 deviceID = 0)
    {
        if (!features) { return nullptr; }
        for (UInt32 i = 0; i < count; ++i) {
            const auto& entry = features[i];
            if (entry.feature == feature && (entry.osMask & osMask) != 0
                && (entry.deviceID == 0 || entry.deviceID == deviceID))
            {
                return &entry;
            }
        }
        return nullptr;
    }

}    // namespace VPF
