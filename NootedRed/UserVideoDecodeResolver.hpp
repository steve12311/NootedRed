// 仅生成当前视频缓存的补丁计划；UUID 不作为动态路径的兼容依据。
#pragma once
#include <UserVideoDecodeData.hpp>
namespace UserVideoDecodeResolver
{

    using namespace UserVideoDecodeData;
    struct Plan
    {
        UserVideoDecodeData::Patch patches[sizeof(Patches) / sizeof(Patches[0])]{};
        UInt8                      original[sizeof(Patches) / sizeof(Patches[0])][MaxPatchSize]{};
        UInt8                      patched[sizeof(Patches) / sizeof(Patches[0])][MaxPatchSize]{};
        UInt32                     reads{0};
    };
    inline bool resolve(UserSharedCache::Reader reader, UInt64 base, const UserVideoDecodeData::Patch* patches,
                        Plan& plan)
    {
        UserSharedCache::Input input{reader, {}};
        if (!patches
            || !UserCacheResolver::resolve(input, base, ResolverImages, ResolverFunctions, patches, ResolverLocations,
                                           plan.patches, &plan.original[0][0], &plan.patched[0][0], MaxPatchSize))
        {
            return false;
        }
        plan.reads = input.reads;
        return true;
    }

}    // namespace UserVideoDecodeResolver
