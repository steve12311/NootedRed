// 仅生成当前视频缓存的补丁计划；UUID 不作为动态路径的兼容依据。
#pragma once
#include <UserPatchProfiles.hpp>
#include <UserVideoDecodeData.hpp>
#include <VPF/FastResolution.hpp>
namespace UserVideoDecodeResolver
{

    using namespace UserVideoDecodeData;
    static constexpr auto ImageCount    = sizeof(ResolverImages) / sizeof(ResolverImages[0]);
    static constexpr auto FunctionCount = sizeof(ResolverFunctions) / sizeof(ResolverFunctions[0]);
    static constexpr auto SequoiaFunctionCount =
        sizeof(UserSequoiaVideoData::ResolverFunctions) / sizeof(UserSequoiaVideoData::ResolverFunctions[0]);
    static constexpr auto SonomaFunctionCount =
        sizeof(UserSonomaVideoData::ResolverFunctions) / sizeof(UserSonomaVideoData::ResolverFunctions[0]);
    static constexpr auto LegacyFunctionCount =
        SonomaFunctionCount > SequoiaFunctionCount ? SonomaFunctionCount : SequoiaFunctionCount;
    static constexpr auto MaxFunctionCount = FunctionCount > LegacyFunctionCount ? FunctionCount : LegacyFunctionCount;
    static_assert(UserSonomaVideoData::MaxPatchSize <= MaxPatchSize);
    static constexpr auto PatchCount = sizeof(Patches) / sizeof(Patches[0]);
    static_assert(ImageCount
                  == sizeof(UserSequoiaVideoData::ResolverImages) / sizeof(UserSequoiaVideoData::ResolverImages[0]));
    static_assert(PatchCount == sizeof(UserSequoiaVideoData::Patches) / sizeof(UserSequoiaVideoData::Patches[0]));
    static_assert(ImageCount
                  == sizeof(UserSonomaVideoData::ResolverImages) / sizeof(UserSonomaVideoData::ResolverImages[0]));
    static_assert(PatchCount == sizeof(UserSonomaVideoData::Patches) / sizeof(UserSonomaVideoData::Patches[0]));
    template<size_t Images, size_t Functions, size_t Locations>
    struct ResolvedPlan
    {
        UserVideoDecodeData::Patch       patches[Locations]{};
        UInt8                            original[Locations][MaxPatchSize]{};
        UInt8                            patched[Locations][MaxPatchSize]{};
        UInt32                           reads{0}, count{0};
        UserCacheResolver::ImageBounds   images[Images]{};
        UserCacheResolver::KnownLocation locations[Functions]{};
    };

    using Plan                          = ResolvedPlan<ImageCount, MaxFunctionCount, PatchCount>;
    using TahoeRDD                      = UserPatchProfiles::Tahoe::VTRDD;
    static constexpr auto RDDPatchCount = sizeof(TahoeRDD::Sites) / sizeof(TahoeRDD::Sites[0]);
    static constexpr auto RDDImageCount = sizeof(TahoeRDD::Images) / sizeof(TahoeRDD::Images[0]);
    using RDDPlan                       = ResolvedPlan<RDDImageCount, RDDPatchCount, RDDPatchCount>;

    template<typename Output, size_t Images, size_t Functions, size_t Locations>
    inline bool resolveSet(UserSharedCache::Input& input, UInt64 base, const UserVideoDecodeData::Patch* patches,
                           Output& plan, const UserCacheResolver::ImageReference (&imageRefs)[Images],
                           const UserCacheResolver::Function (&functions)[Functions],
                           const UserCacheResolver::Location (&locations)[Locations], const bool sectionRelative,
                           VPF::FastResolution* fast, const UserCacheSectionData::Target* sectionTargets = nullptr,
                           UInt32 sectionTargetCount = 0, VPF::FunctionGroup group = VPF::FunctionGroup::VideoDecode)
    {
        static_assert(Images <= sizeof(plan.images) / sizeof(plan.images[0]));
        static_assert(Functions <= sizeof(plan.locations) / sizeof(plan.locations[0]));
        static_assert(Locations <= sizeof(plan.patches) / sizeof(plan.patches[0]));
        plan.count = 0;
        if (!patches) { return false; }
        if (!UserCacheResolver::resolveImages(input, base, imageRefs, plan.images)) { return false; }
        UInt32 functionImages[Functions]{};
        for (UInt32 i = 0; i < Functions; ++i) { functionImages[i] = static_cast<UInt32>(functions[i].image); }
        const UInt32 savedReads = input.reads, savedBytes = input.bytes, savedCandidates = input.candidates;
        const bool   savedFailed = input.failed;
        if (fast && fast->observeImages(input.cache.address, input.cache.size, plan.images, imageRefs, Images)
            && fast->lookup(group, functionImages, Functions, plan.locations))
        {
            if (UserCacheResolver::resolveFunctions(input, base, imageRefs, functions, patches, locations, plan.patches,
                                                    &plan.original[0][0], &plan.patched[0][0], MaxPatchSize,
                                                    plan.images, sectionRelative, sectionTargets, sectionTargetCount,
                                                    plan.locations, nullptr))
            {
                plan.reads = input.reads;
                plan.count = Locations;
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
                                                 sectionRelative, sectionTargets, sectionTargetCount, nullptr,
                                                 plan.locations))
        {
            return false;
        }
        plan.reads = input.reads;
        plan.count = Locations;
        if (fast) { fast->publish(group, plan.locations, functionImages, Functions); }
        return true;
    }

    template<typename Data>
    inline bool resolveRDDSet(UserSharedCache::Input& input, UInt64 base, RDDPlan& plan, VPF::FastResolution* fast)
    {
        if (fast) { fast->restart(); }
        return resolveSet(input, base, Data::Sites, plan, Data::Images, Data::Functions, Data::Locations, true, fast,
                          Data::Targets, sizeof(Data::Targets) / sizeof(Data::Targets[0]),
                          VPF::FunctionGroup::VTRDDCandidates);
    }

    inline bool resolveRDD(UserSharedCache::Input& input, UInt64 base, RDDPlan& plan, UInt32 major,
                           VPF::FastResolution* fast = nullptr)
    {
        return UserPatchProfiles::dispatch(
            major,
            [&](auto profile) { return resolveRDDSet<typename decltype(profile)::VTRDD>(input, base, plan, fast); });
    }

    inline const UserVideoDecodeData::Patch* findDevice(UInt32 deviceID, UInt32 major)
    {
        const UserVideoDecodeData::Patch* patches = nullptr;
        UserPatchProfiles::dispatch(major,
                                    [&](auto profile)
                                    {
                                        patches = decltype(profile)::videoDevice(deviceID);
                                        return patches != nullptr;
                                    });
        return patches;
    }

    inline UInt32 fairPlayMask(UInt32 major)
    {
        UInt32 mask = 0;
        UserPatchProfiles::dispatch(major,
                                    [&](auto profile)
                                    {
                                        mask = decltype(profile)::FairPlayDecodeMask;
                                        return true;
                                    });
        return mask;
    }

    inline bool resolve(UserSharedCache::Input& input, UInt64 base, const UserVideoDecodeData::Patch* patches,
                        Plan& plan, UInt32 major = 25, VPF::FastResolution* fast = nullptr)
    {
        if (!patches) { return false; }
        return UserPatchProfiles::dispatch(major,
                                           [&](auto profile)
                                           {
                                               using Data = decltype(profile);
                                               return resolveSet(input, base, patches, plan, Data::VideoImages,
                                                                 Data::VideoFunctions, Data::VideoLocations, true, fast,
                                                                 Data::VideoTargets, Data::VideoTargetCount);
                                           });
    }

    inline bool resolve(UserSharedCache::Reader reader, UInt64 base, const UserVideoDecodeData::Patch* patches,
                        Plan& plan, UInt32 major = 25, VPF::FastResolution* fast = nullptr)
    {
        UserSharedCache::Input input{reader, {}};
        return resolve(input, base, patches, plan, major, fast);
    }

}    // namespace UserVideoDecodeResolver
