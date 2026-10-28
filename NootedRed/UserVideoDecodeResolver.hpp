// 仅生成当前视频缓存的补丁计划；UUID 不作为动态路径的兼容依据。
#pragma once
#include <UserSequoiaVideoData.hpp>
#include <UserVideoDecodeData.hpp>
#include <VPF/FastResolution.hpp>
namespace UserVideoDecodeResolver
{

    using namespace UserVideoDecodeData;
    static constexpr auto ImageCount    = sizeof(ResolverImages) / sizeof(ResolverImages[0]);
    static constexpr auto FunctionCount = sizeof(ResolverFunctions) / sizeof(ResolverFunctions[0]);
    static constexpr auto SequoiaFunctionCount =
        sizeof(UserSequoiaVideoData::ResolverFunctions) / sizeof(UserSequoiaVideoData::ResolverFunctions[0]);
    static constexpr auto MaxFunctionCount =
        FunctionCount > SequoiaFunctionCount ? FunctionCount : SequoiaFunctionCount;
    static constexpr auto PatchCount = sizeof(Patches) / sizeof(Patches[0]);
    static_assert(ImageCount
                  == sizeof(UserSequoiaVideoData::ResolverImages) / sizeof(UserSequoiaVideoData::ResolverImages[0]));
    static_assert(PatchCount == sizeof(UserSequoiaVideoData::Patches) / sizeof(UserSequoiaVideoData::Patches[0]));
    struct Plan
    {
        UserVideoDecodeData::Patch       patches[PatchCount]{};
        UInt8                            original[PatchCount][MaxPatchSize]{};
        UInt8                            patched[PatchCount][MaxPatchSize]{};
        UInt32                           reads{0};
        UserCacheResolver::ImageBounds   images[ImageCount]{};
        UserCacheResolver::KnownLocation locations[MaxFunctionCount]{};
    };

    template<size_t Images, size_t Functions, size_t Locations>
    inline bool resolveSet(UserSharedCache::Input& input, UInt64 base, const UserVideoDecodeData::Patch* patches,
                           Plan& plan, const UserCacheResolver::ImageReference (&imageRefs)[Images],
                           const UserCacheResolver::Function (&functions)[Functions],
                           const UserCacheResolver::Location (&locations)[Locations], const bool sectionRelative,
                           VPF::FastResolution* fast)
    {
        static_assert(Images == ImageCount && Functions <= MaxFunctionCount && Locations == PatchCount);
        if (!patches) { return false; }
        if (!UserCacheResolver::resolveImages(input, base, imageRefs, plan.images)) { return false; }
        UInt32 functionImages[Functions]{};
        for (UInt32 i = 0; i < Functions; ++i) { functionImages[i] = static_cast<UInt32>(functions[i].image); }
        const UInt32 savedReads = input.reads, savedBytes = input.bytes, savedCandidates = input.candidates;
        const bool   savedFailed = input.failed;
        if (fast && fast->observeImages(input.cache.address, input.cache.size, plan.images, imageRefs, ImageCount)
            && fast->lookup(VPF::FunctionGroup::VideoDecode, functionImages, Functions, plan.locations))
        {
            if (UserCacheResolver::resolveFunctions(input, base, imageRefs, functions, patches, locations, plan.patches,
                                                    &plan.original[0][0], &plan.patched[0][0], MaxPatchSize,
                                                    plan.images, sectionRelative, nullptr, 0, plan.locations, nullptr))
            {
                plan.reads = input.reads;
                return true;
            }
            input.reads      = savedReads;
            input.bytes      = savedBytes;
            input.candidates = savedCandidates;
            input.failed     = savedFailed;
            fast->rejectHot();
        }
        if (!UserCacheResolver::resolveFunctions(input, base, imageRefs, functions, patches, locations, plan.patches,
                                                 &plan.original[0][0], &plan.patched[0][0], MaxPatchSize, plan.images,
                                                 sectionRelative, nullptr, 0, nullptr, plan.locations))
        {
            return false;
        }
        plan.reads = input.reads;
        if (fast) { fast->publish(VPF::FunctionGroup::VideoDecode, plan.locations, functionImages, Functions); }
        return true;
    }

    inline bool resolve(UserSharedCache::Input& input, UInt64 base, const UserVideoDecodeData::Patch* patches,
                        Plan& plan, bool sequoia = false, VPF::FastResolution* fast = nullptr)
    {
        if (!patches) { return false; }
        return sequoia ? resolveSet(input, base, patches, plan, UserSequoiaVideoData::ResolverImages,
                                    UserSequoiaVideoData::ResolverFunctions, UserSequoiaVideoData::ResolverLocations,
                                    false, fast) :
                         resolveSet(input, base, patches, plan, ResolverImages, ResolverFunctions, ResolverLocations,
                                    true, fast);
    }

    inline bool resolve(UserSharedCache::Reader reader, UInt64 base, const UserVideoDecodeData::Patch* patches,
                        Plan& plan, bool sequoia = false, VPF::FastResolution* fast = nullptr)
    {
        UserSharedCache::Input input{reader, {}};
        return resolve(input, base, patches, plan, sequoia, fast);
    }

}    // namespace UserVideoDecodeResolver
