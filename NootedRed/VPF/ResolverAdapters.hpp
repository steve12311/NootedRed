// VPF：把现有 Resolver 的输出规范化为只读 VerifiedPatchPlan；不改变旧执行路径。
#pragma once
#include <VPF/VerifiedPatchPlan.hpp>
#include <UserSurfaceSyncResolver.hpp>
#include <UserVideoDecodeResolver.hpp>

namespace VPF
{

    // 计划只引用旧 Plan 的字节缓冲；调用方必须保证旧 Plan 在计划使用期间不被改写。
    template<typename Patch>
    inline bool adaptPatches(VerifiedPatchPlan& out, const FeatureManifest& manifest, UInt64 base,
                             const UserSharedCache::Range& cache, const Patch* patches, UInt32 patchCount,
                             const UserCacheResolver::ImageBounds* images, UInt32 imageCount, UInt32 reads,
                             PlanState state, bool manifestSitesAligned)
    {
        if (!patches || patchCount == 0 || patchCount > MaxPlanSites || imageCount > manifest.resolver.imageCount) {
            return false;
        }
        out.reset(manifest, base, cache);
        for (UInt32 i = 0; i < imageCount; ++i) {
            if (!out.addImage(manifest.resolver.images[i].path, images[i].address, images[i].text, images[i].segments,
                              images[i].segmentCount))
            {
                return false;
            }
        }
        for (UInt32 i = 0; i < patchCount; ++i) {
            const auto& patch = patches[i];
            if (patch.size == 0 || patch.size > UserSharedCache::UserLimit - base
                || patch.offset >= UserSharedCache::UserLimit - base)
            {
                return false;
            }
            UInt32 functionIndex = NoFunction, instructionOffset = 0;
            // 只有生成表的补丁顺序与位置表一一对应时才引用函数证据；守卫入口补丁不套用。
            if (manifestSitesAligned && i < manifest.resolver.siteCount) {
                functionIndex      = manifest.resolver.sites[i].functionIndex;
                instructionOffset  = manifest.resolver.sites[i].instructionOffset;
            }
            if (!out.addSite(base + patch.offset, patch.offset, patch.size, functionIndex, instructionOffset,
                             patch.original, patch.patched))
            {
                return false;
            }
        }
        out.setReads(reads);
        return out.seal(state);
    }

    inline bool adaptVideo(VerifiedPatchPlan& out, const FeatureManifest& manifest, UInt64 base,
                           const UserSharedCache::Range& cache, const UserVideoDecodeResolver::Plan& legacy,
                           PlanState state)
    {
        return adaptPatches(out, manifest, base, cache, legacy.patches, manifest.resolver.siteCount, legacy.images,
                            manifest.resolver.imageCount, legacy.reads, state, true);
    }

    inline bool adaptSurface(VerifiedPatchPlan& out, const FeatureManifest& manifest, UInt64 base,
                             const UserSharedCache::Range& cache, const UserSurfaceSyncResolver::Plan& legacy,
                             UInt32 patchCount, PlanState state)
    {
        UserCacheResolver::ImageBounds image{};
        UInt32                         imageCount = 0;
        if (legacy.image.address != 0) {
            image.address      = legacy.image.address;
            image.text         = legacy.image.text;
            image.segmentCount = legacy.image.segmentCount;
            for (UInt32 i = 0; i < image.segmentCount; ++i) { image.segments[i] = legacy.image.segments[i]; }
            imageCount = 1;
        }
        // 组合显示计划包含多个功能组时，不把位置表按索引套到补丁上。
        const bool aligned =
            manifest.resolver.functions != nullptr && manifest.resolver.siteCount == patchCount;
        return adaptPatches(out, manifest, base, cache, legacy.patches, patchCount, imageCount ? &image : nullptr,
                            imageCount, legacy.reads, state, aligned);
    }

}    // namespace VPF
