// 仅生成当前视频缓存的补丁计划；UUID 不作为动态路径的兼容依据。
#pragma once
#include <UserSequoiaVideoData.hpp>
#include <UserVideoDecodeData.hpp>
namespace UserVideoDecodeResolver
{

    using namespace UserVideoDecodeData;
    static constexpr auto ImageCount = sizeof(ResolverImages) / sizeof(ResolverImages[0]);
    static_assert(ImageCount == sizeof(UserSequoiaVideoData::ResolverImages)
                                    / sizeof(UserSequoiaVideoData::ResolverImages[0]));
    struct Plan
    {
        UserVideoDecodeData::Patch     patches[sizeof(Patches) / sizeof(Patches[0])]{};
        UInt8                          original[sizeof(Patches) / sizeof(Patches[0])][MaxPatchSize]{};
        UInt8                          patched[sizeof(Patches) / sizeof(Patches[0])][MaxPatchSize]{};
        UInt32                         reads{0};
        UserCacheResolver::ImageBounds images[ImageCount]{};
    };
    inline bool resolve(UserSharedCache::Input& input, UInt64 base, const UserVideoDecodeData::Patch* patches,
                        Plan& plan, bool sequoia = false)
    {
        const auto             resolve = [&]<typename Images, typename Functions, typename Locations>(
                                 const Images& images, const Functions& functions, const Locations& locations)
        {
            return patches
                   && UserCacheResolver::resolveWithImages(input, base, images, functions, patches, locations,
                                                           plan.patches, &plan.original[0][0], &plan.patched[0][0],
                                                           MaxPatchSize, plan.images, !sequoia);
        };
        if (!(sequoia ? resolve(UserSequoiaVideoData::ResolverImages, UserSequoiaVideoData::ResolverFunctions,
                                UserSequoiaVideoData::ResolverLocations) :
                        resolve(ResolverImages, ResolverFunctions, ResolverLocations)))
        {
            return false;
        }
        plan.reads = input.reads;
        return true;
    }

    inline bool resolve(UserSharedCache::Reader reader, UInt64 base, const UserVideoDecodeData::Patch* patches,
                        Plan& plan, bool sequoia = false)
    {
        UserSharedCache::Input input{reader, {}};
        return resolve(input, base, patches, plan, sequoia);
    }

}    // namespace UserVideoDecodeResolver
