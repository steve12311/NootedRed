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
    static constexpr UInt32 FunctionCount = sizeof(ResolverFunctions) / sizeof(ResolverFunctions[0]);
    static_assert(PatchCount > 0 && PatchCount <= 32);
    static_assert(MaxPatchSize <= 512);
    static_assert(sizeof(CacheUUID) == 16);

    struct Plan
    {
        UserVideoDecodeData::Patch     patches[PatchCount]{};
        UInt8                          original[PatchCount][MaxPatchSize]{};
        UInt8                          patched[PatchCount][MaxPatchSize]{};
        UserCacheResolver::ImageBounds images[ImageCount]{};
        UserCacheResolver::KnownLocation locations[FunctionCount]{};
    };

    // UUID 仅用于诊断；兼容性由完整函数、引用、数据节和虚函数槽位共同证明。
    inline bool resolve(UserSharedCache::Input& input, UInt64 base, Plan& plan, const UInt32 major = 25)
    {
        if (major != 23 && major != 24 && major != 25) { return false; }
        if (!UserCacheResolver::resolveImages(input, base, ResolverImages, plan.images)) { return false; }
        if (!UserCacheResolver::resolveFunctions(input, base, ResolverImages, ResolverFunctions, Patches,
                                                 ResolverLocations, plan.patches, &plan.original[0][0],
                                                 &plan.patched[0][0], MaxPatchSize, plan.images, true, SectionTargets,
                                                 SectionCount, nullptr, plan.locations))
        {
            return false;
        }
        for (const auto& slot : ABISlots) {
            UInt8      field[4];
            UInt64     table, target;
            const auto address = plan.locations[slot.constructor].address + slot.field;
            if (!input.read(address, field, sizeof(field))
                || !UserCacheResolver::relativeTarget(address, 0, field, table)
                || !input.read(table + slot.slot, &target, sizeof(target))
                || target != plan.locations[slot.function].address + slot.offset)
            {
                return false;
            }
        }
        return true;
    }
}    // namespace UserVP9DecodeResolver
