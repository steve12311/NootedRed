// VP9 (piano V3, -NRedVP9): risolve i siti del backend VP9 nella cache condivisa corrente.
// Tutte le funzioni toccate sono verificate per intero (byte e riferimenti rel32); i bersagli dati
// fuori da __TEXT passano dalla tabella SectionTargets generata insieme alle patch.
#pragma once
#include <UserCacheResolver.hpp>
#include <UserSharedCache.hpp>
#include <UserVP9DecodeData.hpp>

namespace UserVP9DecodeResolver
{
    using namespace UserVP9DecodeData;
    static constexpr UInt32 ImageCount   = sizeof(ResolverImages) / sizeof(ResolverImages[0]);
    static constexpr UInt32 PatchCount   = sizeof(Patches) / sizeof(Patches[0]);
    static constexpr UInt32 SectionCount = sizeof(SectionTargets) / sizeof(SectionTargets[0]);
    static_assert(PatchCount > 0 && PatchCount <= 32);
    static_assert(MaxPatchSize == UserVideoDecodeData::MaxPatchSize);
    static_assert(sizeof(CacheUUID) == 16);

    struct Plan
    {
        UserVideoDecodeData::Patch     patches[PatchCount]{};
        UInt8                          original[PatchCount][MaxPatchSize]{};
        UInt8                          patched[PatchCount][MaxPatchSize]{};
        UserCacheResolver::ImageBounds images[ImageCount]{};
    };

    // uuid: i 16 byte dell'header della cache (offset 88). Il backend è verificato solo su questa cache.
    inline bool resolve(UserSharedCache::Input& input, UInt64 base, const UInt8* uuid, Plan& plan)
    {
        if (!uuid || memcmp(uuid, CacheUUID, sizeof(CacheUUID)) != 0) { return false; }
        if (!UserCacheResolver::resolveImages(input, base, ResolverImages, plan.images)) { return false; }
        return UserCacheResolver::resolveFunctions(input, base, ResolverImages, ResolverFunctions, Patches,
                                                   ResolverLocations, plan.patches, &plan.original[0][0],
                                                   &plan.patched[0][0], MaxPatchSize, plan.images, true,
                                                   SectionTargets, SectionCount, nullptr, nullptr);
    }
}    // namespace UserVP9DecodeResolver
