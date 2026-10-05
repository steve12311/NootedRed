// 按驱动路径与完整函数内容解析兼容的共享缓存；仅生成计划，不写入进程。
#pragma once
#include <UserSharedCache.hpp>
#include <UserSurfaceSyncData.hpp>
#include <string.h>

namespace UserSurfaceSyncResolver
{

    using namespace UserSurfaceSyncData;
    using UserSharedCache::Image;
    using UserSharedCache::Input;
    using UserSharedCache::MaxTextSize;
    using UserSharedCache::Range;
    using UserSharedCache::Reader;
    using UserSharedCache::relativeTarget;
    using UserSharedCache::setRelative;
    using UserSharedCache::UserLimit;
    using UserSharedCache::word;
    struct Plan
    {
        UserSurfaceSyncData::Patch patches[4]{};
        UInt8                      original[4][MaxPatchSize]{};
        UInt8                      patched[4][MaxPatchSize]{};
        UInt32                     reads{0};
    };
    inline bool imageForCache(Input& input, UInt64 base, Image& image)
    {
        return UserSharedCache::imageForCache(input, base, image, DriverPath, sizeof(DriverPath))
               && image.constantCount != 0 && image.text.size <= MaxTextSize;
    }

    inline bool skipped(UInt32 index, const RelativeField* fields, UInt32 fieldCount)
    {
        for (UInt32 i = 0; i < fieldCount; ++i) {
            if (index >= fields[i].offset && index - fields[i].offset < 4) { return true; }
        }
        return false;
    }

    inline bool matches(Input& input, UInt64 address, const UInt8* expected, UInt32 size,
                        const RelativeField* fields = nullptr, UInt32 fieldCount = 0, bool blend = false)
    {
        return UserCacheResolver::matchBytes(input, address, size,
                                             [&](UInt32 index, UInt8 value)
                                             {
                                                 return skipped(index, fields, fieldCount)
                                                        || (blend && index >= BlendGateInConstructor
                                                            && index - BlendGateInConstructor < 2)
                                                        || value == expected[index];
                                             });
    }

    inline bool findFunction(Input& input, const Range& text, const UInt8* expected, UInt32 size, UInt64& found,
                             bool blend = false, UInt64 hint = 0)
    {
        if (size < 32) { return false; }
        return UserCacheResolver::scanFunction(
            input, text, size, 32, [&](const UInt8* bytes) { return memcmp(bytes, expected, 32) == 0; },
            [&](UInt64 address) { return matches(input, address, expected, size, nullptr, 0, blend); }, found, hint);
    }

    inline bool resolve(Reader reader, UInt64 base, const DevicePatchSet& device, bool shared, bool blend, Plan& plan,
                        bool scratchMode = false)
    {
        Input input{reader, {}};
        Image image;
        if (!imageForCache(input, base, image)) { return false; }
        // Scratch 与撤回的 VT 策略按完整函数和实际相对引用核验。
        if (scratchMode) {
            using namespace ScratchResolverData;
            if (!UserCacheResolver::resolve(input, base, ResolverImages, ResolverFunctions, device.scratch + 3,
                                            ResolverLocations, plan.patches + 3, &plan.original[3][0],
                                            &plan.patched[3][0], MaxPatchSize))
            {
                return false;
            }
        }
        UInt64 settings{0}, scratch{0}, constructor{0};
        if (!findFunction(input, image.text, NativeOverride, sizeof(NativeOverride), settings, false,
                          image.address + NativeOverrideRVA)
            || (!scratchMode
                && !findFunction(input, image.text, ScratchOriginal, sizeof(ScratchOriginal), scratch, false,
                                 image.address + ScratchRVA))
            || !findFunction(input, image.text, BlendConstructor, sizeof(BlendConstructor), constructor, true,
                             image.address + ConstructorRVA))
        {
            return false;
        }
        const UInt8*         variants[] = {Original0, device.immediate[0].patched, device.shared[0].patched};
        const RelativeField* links[]    = {OriginalInitLinks, ImmediateInitLinks, SharedInitLinks};
        UInt64               init{0}, targets[3]{};
        const auto           matchInitAt = [&](UInt64 address, UInt64* result)
        {
            if (!image.text.contains(address, sizeof(Original0))) { return false; }
            UInt8 initBytes[sizeof(Original0)];
            for (UInt32 variant = 0; variant < 3; ++variant) {
                if (!matches(input, address, variants[variant], sizeof(Original0), links[variant], 3)
                    || !input.read(address, initBytes, sizeof(initBytes)))
                {
                    continue;
                }
                UInt64 candidateTargets[3]{};
                bool   valid = true;
                for (UInt32 k = 0; k < 3; ++k) {
                    valid &= relativeTarget(address, links[variant][k].offset, initBytes,
                                            candidateTargets[links[variant][k].target]);
                }
                UInt8 constant[4];
                valid = valid && candidateTargets[1] == settings && image.constant(candidateTargets[0])
                        && input.read(candidateTargets[0], constant, sizeof(constant)) && word(constant) == 0x40400000
                        && image.text.contains(candidateTargets[2], sizeof(NativeDump))
                        && matches(input, candidateTargets[2], NativeDump, sizeof(NativeDump));
                if (valid) {
                    memcpy(result, candidateTargets, sizeof(candidateTargets));
                    return true;
                }
            }
            return false;
        };
        if (!UserCacheResolver::scanFunction(
                input, image.text, sizeof(Original0), 32,
                [&](const UInt8* bytes)
                {
                    for (auto* variant : variants) {
                        if (memcmp(bytes, variant, 32) == 0) { return true; }
                    }
                    return false;
                },
                [&](UInt64 address) { return matchInitAt(address, targets); }, init, image.address + InitRVA))
        {
            return false;
        }
        UInt64     overrideAddress{0};
        const auto matchOverrideAt = [&](UInt64 address)
        {
            UInt8 bytes[sizeof(Original1)];
            if (!image.text.contains(address, sizeof(bytes)) || !input.read(address, bytes, sizeof(bytes))) {
                return false;
            }
            const bool original = memcmp(bytes, Original1, 6) == 0;
            const bool patched  = bytes[0] == 0xE9 && memcmp(bytes + 5, Patched1 + 5, 5) == 0;
            UInt64     target{0};
            return (original || patched) && relativeTarget(address, original ? 6 : 1, bytes, target)
                   && target == (original ? settings : init + OverrideEntryInInit);
        };
        if (!UserCacheResolver::scanFunction(
                input, image.text, sizeof(Original1), sizeof(Original1),
                [&](const UInt8* bytes)
                {
                    return memcmp(bytes, Original1, 6) == 0
                           || (bytes[0] == 0xE9 && memcmp(bytes + 5, Patched1 + 5, 5) == 0);
                },
                matchOverrideAt, overrideAddress, image.address + OverrideRVA))
        {
            return false;
        }
        const auto* source =
            scratchMode ? device.scratch : (blend ? device.blend : (shared ? device.shared : device.immediate));
        const auto* patchedLinks = shared || blend ? SharedInitLinks : ImmediateInitLinks;
        memcpy(plan.original[0], Original0, sizeof(Original0));
        memcpy(plan.patched[0], source[0].patched, sizeof(Original0));
        for (UInt32 k = 0; k < 3; ++k) {
            if (!setRelative(plan.original[0], OriginalInitLinks[k].offset, init, targets[OriginalInitLinks[k].target])
                || !setRelative(plan.patched[0], patchedLinks[k].offset, init, targets[patchedLinks[k].target]))
            {
                return false;
            }
        }
        memcpy(plan.original[1], Original1, sizeof(Original1));
        memcpy(plan.patched[1], Patched1, sizeof(Patched1));
        if (!setRelative(plan.original[1], 6, overrideAddress, settings)
            || !setRelative(plan.patched[1], 1, overrideAddress, init + OverrideEntryInInit))
        {
            return false;
        }
        memcpy(plan.original[2], BlendGateOriginal, sizeof(BlendGateOriginal));
        memcpy(plan.patched[2], BlendGatePatched, sizeof(BlendGatePatched));
        const UInt64 addresses[] = {init, overrideAddress, constructor + BlendGateInConstructor};
        for (UInt32 i = 0; i < 3; ++i) {
            const auto length = i == 0 ? sizeof(Original0) : (i == 1 ? sizeof(Original1) : sizeof(BlendGateOriginal));
            if (!input.cache.contains(addresses[i], length)) { return false; }
            plan.patches[i] = {addresses[i] - base, plan.original[i], plan.patched[i], static_cast<UInt32>(length)};
        }
        plan.reads = input.reads;
        return true;
    }

}    // namespace UserSurfaceSyncResolver
