// 按驱动路径与完整函数内容解析兼容的共享缓存；仅生成计划，不写入进程。
#pragma once
#include <UserPatchProfiles.hpp>
#include <UserSharedCache.hpp>
#include <UserSurfaceConstructorData.hpp>
#include <UserSurfaceSyncData.hpp>
#include <VPF/FastResolution.hpp>
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
        static constexpr UInt32 BufferSize = UserSequoiaSurfaceData::MaxPatchSize;
        static_assert(BufferSize >= UserSonomaSurfaceData::MaxPatchSize);
        static_assert(BufferSize >= UserSurfaceSyncData::MaxPatchSize && BufferSize <= 512);
        UserSurfaceSyncData::Patch patches[4]{};
        UInt8                      original[4][BufferSize]{};
        UInt8                      patched[4][BufferSize]{};
        UInt32                     reads{0};
        Input                      input{nullptr, {}};
        Image                      image;
        UInt8                      scan[Input::ScanSize];
        static constexpr UInt32    ImageCapacity =
            sizeof(UserPatchProfiles::Sequoia::SurfaceImages) / sizeof(UserPatchProfiles::Sequoia::SurfaceImages[0]);
        static_assert(ImageCapacity >= sizeof(UserPatchProfiles::Tahoe::SurfaceImages)
                                           / sizeof(UserPatchProfiles::Tahoe::SurfaceImages[0]));
        static_assert(ImageCapacity >= sizeof(UserPatchProfiles::Sonoma::SurfaceImages)
                                           / sizeof(UserPatchProfiles::Sonoma::SurfaceImages[0]));
        UserCacheResolver::ImageBounds scratchImages[ImageCapacity]{};
        UserCacheResolver::ImageBounds constructorImages[ImageCapacity]{};
    };

    struct InputBudget
    {
        UInt32 reads, bytes, candidates;
        bool   failed;
    };

    inline InputBudget saveBudget(const Input& input)
    {
        return {input.reads, input.bytes, input.candidates, input.failed};
    }

    inline void restoreBudget(Input& input, const InputBudget& budget)
    {
        input.reads      = budget.reads;
        input.bytes      = budget.bytes;
        input.candidates = budget.candidates;
        input.failed     = budget.failed;
    }

    inline bool imageBounds(const Image& image, UserCacheResolver::ImageBounds& bounds)
    {
        if (image.segmentCount > 8) { return false; }
        bounds.address      = image.address;
        bounds.text         = image.text;
        bounds.segmentCount = image.segmentCount;
        for (UInt32 i = 0; i < image.segmentCount; ++i) { bounds.segments[i] = image.segments[i]; }
        return true;
    }

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
                        const RelativeField* fields = nullptr, UInt32 fieldCount = 0, bool blend = false,
                        UInt32 blendOffset = BlendGateInConstructor)
    {
        return UserCacheResolver::matchBytes(input, address, size,
                                             [&](UInt32 index, UInt8 value)
                                             {
                                                 return skipped(index, fields, fieldCount)
                                                        || (blend && index >= blendOffset && index - blendOffset < 2)
                                                        || value == expected[index];
                                             });
    }

    inline bool findFunction(Input& input, const Range& text, const UInt8* expected, UInt32 size, UInt64& found,
                             bool blend = false, UInt64 hint = 0, UInt32 blendOffset = BlendGateInConstructor)
    {
        if (size < 32) { return false; }
        UserCacheResolver::Prefix prefix;
        for (UInt32 i = 0; i < 32; ++i) { prefix.set(i, expected[i], false); }
        UserCacheResolver::ScanAnchor anchor{};
        const bool                    anchored = prefix.commonAnchor(prefix, anchor);
        return UserCacheResolver::scanFunction(
            input, text, size, 32, [&](const UInt8* bytes) { return memcmp(bytes, expected, 32) == 0; },
            [&](UInt64 address) { return matches(input, address, expected, size, nullptr, 0, blend, blendOffset); },
            found, hint, anchored ? &anchor : nullptr);
    }

    template<size_t Images, size_t Functions, size_t Locations>
    inline bool resolvePart(Input& input, UInt64 base, const UserCacheResolver::ImageReference (&images)[Images],
                            const UserCacheResolver::Function (&functions)[Functions],
                            const UserCacheResolver::Location (&locations)[Locations],
                            const UserSurfaceSyncData::Patch* patches, Plan& plan, UInt32 slot,
                            UserCacheResolver::ImageBounds (&bounds)[Plan::ImageCapacity], VPF::FunctionGroup group,
                            VPF::FastResolution* fast, const UserCacheSectionData::Target* sectionTargets,
                            UInt32 sectionTargetCount)
    {
        static_assert(Locations == 1 && Functions <= 32);
        if (slot >= 4 || !UserCacheResolver::resolveImages(input, base, images, bounds)) { return false; }
        UInt32 functionImages[Functions]{};
        for (UInt32 i = 0; i < Functions; ++i) { functionImages[i] = functions[i].image; }
        UserCacheResolver::KnownLocation known[Functions]{}, located[Functions]{};
        const auto                       budget = saveBudget(input);
        if (fast) {
            fast->restart();
            if (fast->observeImages(input.cache.address, input.cache.size, bounds, images, Images)
                && fast->lookup(group, functionImages, Functions, known))
            {
                if (UserCacheResolver::resolveFunctions(input, base, images, functions, patches, locations,
                                                        plan.patches + slot, &plan.original[slot][0],
                                                        &plan.patched[slot][0], Plan::BufferSize, bounds, true,
                                                        sectionTargets, sectionTargetCount, known, nullptr))
                {
                    return true;
                }
                restoreBudget(input, budget);
                fast->rejectHot();
            }
        }
        if (!UserCacheResolver::resolveFunctions(input, base, images, functions, patches, locations,
                                                 plan.patches + slot, &plan.original[slot][0], &plan.patched[slot][0],
                                                 Plan::BufferSize, bounds, true, sectionTargets, sectionTargetCount,
                                                 nullptr, located))
        {
            return false;
        }
        if (fast) { fast->publish(group, located, functionImages, Functions); }
        return true;
    }

    template<typename Data>
    inline bool resolveConstructor(Input& input, UInt64 base, Plan& plan, VPF::FastResolution* fast)
    {
        return resolvePart(input, base, Data::ConstructorImages, Data::ConstructorFunctions, Data::ConstructorLocations,
                           Data::ConstructorPatches, plan, 2, plan.constructorImages,
                           VPF::FunctionGroup::DisplayConstructor, fast, Data::SurfaceTargets,
                           Data::SurfaceTargetCount);
    }

    template<typename Data>
    inline bool verifyCoreReferences(Input& input, const Plan& plan, UInt64 address,
                                     const UserCacheResolver::Function& fn, UInt32 firstRef = 0)
    {
        for (UInt32 i = firstRef; i < fn.originalCount; ++i) {
            const auto& ref = fn.originalRefs[i];
            UInt8       field[4];
            UInt64      actual, expected;
            if (!input.read(address + ref.offset, field, sizeof(field))
                || !relativeTarget(address + ref.offset, 0, field, actual)
                || !UserCacheResolver::target(input, ref, plan.scratchImages,
                                              sizeof(Data::SurfaceImages) / sizeof(Data::SurfaceImages[0]),
                                              Data::SurfaceImages, nullptr, nullptr, 0, expected, true,
                                              Data::SurfaceTargets, Data::SurfaceTargetCount)
                || (ref.indirect ? !UserCacheResolver::binding(input, actual, ref.indirect, expected) :
                                   actual != expected))
            {
                return false;
            }
        }
        return true;
    }

    template<typename Data>
    inline bool resolveCore(Input& input, UInt64 base, const DevicePatchSet& device, bool shared, bool blend,
                            Plan& plan, bool scratchMode = false, VPF::FastResolution* fast = nullptr)
    {
        static_assert(sizeof(Data::Original0) <= Plan::BufferSize && sizeof(Data::Original1) == 10);
        auto& image = plan.image;
        memset(&image, 0, sizeof(image));
        // Scratch 按完整函数和实际相对引用核验。
        if (scratchMode
            && !resolvePart(input, base, Data::SurfaceImages, Data::ScratchFunctions, Data::ScratchLocations,
                            device.scratch + 3, plan, 3, plan.scratchImages, VPF::FunctionGroup::DisplayScratch, fast,
                            Data::SurfaceTargets, Data::SurfaceTargetCount))
        {
            return false;
        }
        if (!imageForCache(input, base, image)) { return false; }

        constexpr UInt32 MaxCoreFunctions       = 5;
        const UInt32     TotalFunctions         = scratchMode ? 4 : 5;
        const UInt32     SettingsIndex          = 0;
        const UInt32     StandaloneScratchIndex = 1;
        const UInt32     InitIndex              = scratchMode ? 1 : 2;
        const UInt32     OverrideIndex          = scratchMode ? 2 : 3;
        const UInt32     ConstructorIndex       = scratchMode ? 3 : 4;

        UInt64               settings{0}, scratch{0}, constructor{0}, init{0}, overrideAddress{0}, targets[3]{};
        bool                 constructorDirect{false};
        const UInt8* const   variants[]  = {Data::Original0, device.immediate[0].patched, device.shared[0].patched};
        const RelativeField* links[]     = {Data::OriginalInitLinks, Data::ImmediateInitLinks, Data::SharedInitLinks};
        const auto           matchInitAt = [&](UInt64 address, UInt64* result)
        {
            if (!image.text.contains(address, sizeof(Data::Original0))) { return false; }
            UInt8 initBytes[sizeof(Data::Original0)];
            for (UInt32 variant = 0; variant < 3; ++variant) {
                if (!matches(input, address, variants[variant], sizeof(Data::Original0), links[variant], 3)
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
        const auto matchSettingsAt = [&](UInt64 address)
        {
            if constexpr (Data::FullDisplayReferences) {
                const auto& fn = Data::SurfaceFunctions[4];
                using Patch    = UserSurfaceSyncData::Patch;
                return UserCacheResolver::matches(input, address, fn, 0, false, static_cast<const Patch*>(nullptr),
                                                  nullptr, 0)
                       && verifyCoreReferences<Data>(input, plan, address, fn);
            }
            return matches(input, address, Data::NativeOverride, sizeof(Data::NativeOverride));
        };
        const auto findSettings = [&]
        {
            if constexpr (Data::FullDisplayReferences) {
                const auto&               fn = Data::SurfaceFunctions[4];
                UserCacheResolver::Prefix prefix;
                for (UInt32 i = 0; i < 32; ++i) {
                    prefix.set(i, fn.original[i], UserCacheResolver::skipped(i, fn.originalRefs, fn.originalCount));
                }
                UserCacheResolver::ScanAnchor anchor{};
                return UserCacheResolver::scanFunction(
                    input, image.text, fn.size, 32, [&](const UInt8* bytes) { return prefix.matches(bytes); },
                    matchSettingsAt, settings, image.address + Data::NativeOverrideRVA,
                    prefix.commonAnchor(prefix, anchor) ? &anchor : nullptr);
            }
            return findFunction(input, image.text, Data::NativeOverride, sizeof(Data::NativeOverride), settings, false,
                                image.address + Data::NativeOverrideRVA);
        };
        const auto matchOverrideAt = [&](UInt64 address)
        {
            UInt8 bytes[sizeof(Data::Original1)];
            if (!image.text.contains(address, sizeof(bytes)) || !input.read(address, bytes, sizeof(bytes))) {
                return false;
            }
            const bool original = memcmp(bytes, Data::Original1, 6) == 0;
            const bool patched  = bytes[0] == 0xE9 && memcmp(bytes + 5, Data::Patched1 + 5, 5) == 0;
            UInt64     target{0};
            if ((!original && !patched) || !relativeTarget(address, original ? 6 : 1, bytes, target)
                || target != (original ? settings : init + Data::OverrideEntryInInit))
            {
                return false;
            }
            if constexpr (Data::FullDisplayReferences) {
                const auto& fn = Data::SurfaceFunctions[1];
                if (!image.text.contains(address, fn.size)) { return false; }
                return UserCacheResolver::matchBytes(input, address, fn.size,
                                                     [&](UInt32 i, UInt8 value)
                                                     {
                                                         return i < sizeof(bytes) || value == fn.original[i]
                                                                || UserCacheResolver::skipped(i, fn.originalRefs + 1,
                                                                                              fn.originalCount - 1);
                                                     })
                       && verifyCoreReferences<Data>(input, plan, address, fn, 1);
            }
            return true;
        };

        UInt32                           functionImages[MaxCoreFunctions]{};
        UserCacheResolver::KnownLocation known[MaxCoreFunctions]{};
        const auto                       budget = saveBudget(input);
        bool                             hot    = false;
        if (fast) {
            fast->restart();
            UserCacheResolver::ImageBounds          bounds{};
            const UserCacheResolver::ImageReference refs[1] = {{DriverPath, sizeof(DriverPath)}};
            if (imageBounds(image, bounds)
                && fast->observeImages(input.cache.address, input.cache.size, &bounds, refs, 1)
                && fast->lookup(VPF::FunctionGroup::DisplayCore, functionImages, TotalFunctions, known))
            {
                hot                       = true;
                const auto& settingsKnown = known[SettingsIndex];
                if (settingsKnown.address == 0 || settingsKnown.patched
                    || !image.text.contains(settingsKnown.address, sizeof(Data::NativeOverride))
                    || !matchSettingsAt(settingsKnown.address))
                {
                    hot = false;
                }
                else {
                    settings = settingsKnown.address;
                }
                if (hot && !scratchMode) {
                    const auto& scratchKnown = known[StandaloneScratchIndex];
                    if (scratchKnown.address == 0 || scratchKnown.patched
                        || !image.text.contains(scratchKnown.address, sizeof(ScratchOriginal))
                        || !matches(input, scratchKnown.address, ScratchOriginal, sizeof(ScratchOriginal)))
                    {
                        hot = false;
                    }
                    else {
                        scratch = scratchKnown.address;
                    }
                }
                if (hot) {
                    const auto& initKnown = known[InitIndex];
                    init                  = initKnown.address;
                    if (initKnown.address == 0 || initKnown.patched
                        || !image.text.contains(initKnown.address, sizeof(Data::Original0))
                        || !matchInitAt(init, targets))
                    {
                        hot = false;
                    }
                }
                if (hot) {
                    const auto& overrideKnown = known[OverrideIndex];
                    overrideAddress           = overrideKnown.address;
                    if (overrideKnown.address == 0 || overrideKnown.patched
                        || !image.text.contains(overrideKnown.address, sizeof(Data::Original1))
                        || !matchOverrideAt(overrideAddress))
                    {
                        hot = false;
                    }
                }
                if (hot && !Data::FullDisplayReferences) {
                    const auto& constructorKnown = known[ConstructorIndex];
                    if (constructorKnown.address != 0) {
                        if (constructorKnown.patched
                            || !image.text.contains(constructorKnown.address, sizeof(Data::BlendConstructor))
                            || !matches(input, constructorKnown.address, Data::BlendConstructor,
                                        sizeof(Data::BlendConstructor), nullptr, 0, true, Data::BlendGateInConstructor))
                        {
                            hot = false;
                        }
                        else {
                            constructor       = constructorKnown.address;
                            constructorDirect = true;
                        }
                    }
                }
                if (!hot) {
                    restoreBudget(input, budget);
                    fast->rejectHot();
                }
            }
        }

        if (!hot) {
            if (!findSettings()
                || (!scratchMode
                    && !findFunction(input, image.text, ScratchOriginal, sizeof(ScratchOriginal), scratch, false,
                                     image.address + ScratchRVA)))
            {
                return false;
            }
            if (!UserCacheResolver::scanFunction(
                    input, image.text, sizeof(Data::Original0), 32,
                    [&](const UInt8* bytes)
                    {
                        for (auto* variant : variants) {
                            if (memcmp(bytes, variant, 32) == 0) { return true; }
                        }
                        return false;
                    },
                    [&](UInt64 address) { return matchInitAt(address, targets); }, init, image.address + Data::InitRVA))
            {
                return false;
            }
            if (!UserCacheResolver::scanFunction(
                    input, image.text, sizeof(Data::Original1), sizeof(Data::Original1),
                    [&](const UInt8* bytes)
                    {
                        return memcmp(bytes, Data::Original1, 6) == 0
                               || (bytes[0] == 0xE9 && memcmp(bytes + 5, Data::Patched1 + 5, 5) == 0);
                    },
                    matchOverrideAt, overrideAddress, image.address + Data::OverrideRVA))
            {
                return false;
            }
            constructorDirect =
                !Data::FullDisplayReferences
                && findFunction(input, image.text, Data::BlendConstructor, sizeof(Data::BlendConstructor), constructor,
                                true, image.address + Data::ConstructorRVA, Data::BlendGateInConstructor);
            if (fast) {
                UserCacheResolver::KnownLocation locations[MaxCoreFunctions]{};
                locations[SettingsIndex] = {settings, false};
                if (!scratchMode) { locations[StandaloneScratchIndex] = {scratch, false}; }
                locations[InitIndex]     = {init, false};
                locations[OverrideIndex] = {overrideAddress, false};
                const UInt32 count       = constructorDirect ? TotalFunctions : TotalFunctions - 1;
                if (constructorDirect) { locations[ConstructorIndex] = {constructor, false}; }
                fast->publish(VPF::FunctionGroup::DisplayCore, locations, functionImages, count);
            }
        }
        if (!constructorDirect) {
            if (!resolveConstructor<Data>(input, base, plan, fast)) { return false; }
            constructor = base + plan.patches[2].offset - Data::BlendGateInConstructor;
        }

        const auto* source =
            scratchMode ? device.scratch : (blend ? device.blend : (shared ? device.shared : device.immediate));
        const auto* patchedLinks = shared || blend ? Data::SharedInitLinks : Data::ImmediateInitLinks;
        memcpy(plan.original[0], Data::Original0, sizeof(Data::Original0));
        memcpy(plan.patched[0], source[0].patched, sizeof(Data::Original0));
        for (UInt32 k = 0; k < 3; ++k) {
            if (!setRelative(plan.original[0], Data::OriginalInitLinks[k].offset, init,
                             targets[Data::OriginalInitLinks[k].target])
                || !setRelative(plan.patched[0], patchedLinks[k].offset, init, targets[patchedLinks[k].target]))
            {
                return false;
            }
        }
        memcpy(plan.original[1], Data::Original1, sizeof(Data::Original1));
        memcpy(plan.patched[1], Data::Patched1, sizeof(Data::Patched1));
        if (!setRelative(plan.original[1], 6, overrideAddress, settings)
            || !setRelative(plan.patched[1], 1, overrideAddress, init + Data::OverrideEntryInInit))
        {
            return false;
        }
        memcpy(plan.original[2], Data::BlendGateOriginal, sizeof(Data::BlendGateOriginal));
        memcpy(plan.patched[2], Data::BlendGatePatched, sizeof(Data::BlendGatePatched));
        const UInt64 addresses[] = {init, overrideAddress, constructor + Data::BlendGateInConstructor};
        for (UInt32 i = 0; i < 3; ++i) {
            const auto length =
                i == 0 ? sizeof(Data::Original0) : (i == 1 ? sizeof(Data::Original1) : sizeof(Data::BlendGateOriginal));
            if (!input.cache.contains(addresses[i], length)) { return false; }
            plan.patches[i] = {addresses[i] - base, plan.original[i], plan.patched[i], static_cast<UInt32>(length)};
        }
        plan.reads = input.reads;
        return true;
    }

    inline bool resolve(Input& input, UInt64 base, const DevicePatchSet& device, bool shared, bool blend, Plan& plan,
                        bool scratchMode = false, VPF::FastResolution* fast = nullptr)
    {
        return resolveCore<UserPatchProfiles::Tahoe>(input, base, device, shared, blend, plan, scratchMode, fast);
    }

    inline bool resolve(Reader reader, UInt64 base, const DevicePatchSet& device, bool shared, bool blend, Plan& plan,
                        bool scratchMode = false, VPF::FastResolution* fast = nullptr)
    {
        plan.input            = {reader, {}};
        plan.input.scanBuffer = plan.scan;
        return resolve(plan.input, base, device, shared, blend, plan, scratchMode, fast);
    }

    inline bool selectMetalImages(Input& input, UInt64 base, UInt32 major)
    {
        return UserPatchProfiles::dispatch(major,
                                           [&](auto profile)
                                           {
                                               using Data = decltype(profile);
                                               input.selectImages(base, Data::SurfaceImages,
                                                                  sizeof(Data::SurfaceImages)
                                                                      / sizeof(Data::SurfaceImages[0]));
                                               return true;
                                           });
    }

    inline bool resolveMetal(Input& input, UInt64 base, UInt32 deviceID, Plan& plan, UInt32 major,
                             VPF::FastResolution* fast = nullptr)
    {
        return UserPatchProfiles::dispatch(major,
                                           [&](auto profile)
                                           {
                                               using Data = decltype(profile);
                                               DevicePatchSet device{};
                                               return Data::surfaceDevice(deviceID, device)
                                                      && resolveCore<Data>(input, base, device, true, true, plan, true,
                                                                           fast);
                                           });
    }

    inline bool resolveSequoia(Input& input, UInt64 base, UInt32 deviceID, Plan& plan,
                               VPF::FastResolution* fast = nullptr)
    {
        return resolveMetal(input, base, deviceID, plan, 24, fast);
    }

    inline bool resolveSequoia(Reader reader, UInt64 base, UInt32 deviceID, Plan& plan,
                               VPF::FastResolution* fast = nullptr)
    {
        plan.input            = {reader, {}};
        plan.input.scanBuffer = plan.scan;
        return resolveSequoia(plan.input, base, deviceID, plan, fast);
    }

    inline bool resolveSubmitProfile(Input& input, UInt64 base, UInt64& offset, const UserCacheResolver::Function& fn,
                                     const UserCacheResolver::ImageReference* ResolverImages, bool sectionRelative,
                                     VPF::FastResolution*                fast,
                                     const UserCacheSectionData::Target* sectionTargets     = nullptr,
                                     UInt32                              sectionTargetCount = 0)
    {
        Image image;
        if (!imageForCache(input, base, image)) { return false; }
        if (image.segmentCount > 8 || fn.size < 32) { return false; }
        UserCacheResolver::ImageBounds bounds{};
        if (!imageBounds(image, bounds)) { return false; }
        const UserCacheResolver::ImageReference refs[1]           = {{DriverPath, sizeof(DriverPath)}};
        UInt32                                  functionImages[1] = {0};
        UserCacheResolver::KnownLocation        known[1]{};
        const auto                              budget = saveBudget(input);
        UserCacheResolver::Prefix               prefixes[2];
        for (UInt32 variant = 0; variant < 2; ++variant) {
            for (UInt32 i = 0; i < 32; ++i) {
                static constexpr UInt8 jump[6]   = {0xFF, 0x25, 0, 0, 0, 0};
                const bool             entryByte = variant && i < 15;
                const bool relocated = i >= 15 && UserCacheResolver::skipped(i, fn.originalRefs, fn.originalCount);
                const bool masked    = entryByte ? i >= 6 && i < 14 : relocated;
                prefixes[variant].set(i, entryByte ? (i < 6 ? jump[i] : 0x90) : fn.original[i], masked);
            }
        }
        UserCacheResolver::ScanAnchor anchor{};
        const bool                    anchored = prefixes[0].commonAnchor(prefixes[1], anchor);
        UInt64                        found{0};
        const auto                    candidate = [&](UInt64 address)
        {
            UInt8 entry[15];
            if (!input.read(address, entry, sizeof(entry))) { return false; }
            const bool installed = memcmp(entry, "\xff\x25\0\0\0\0", 6) == 0 && entry[14] == 0x90;
            if (!UserCacheResolver::matchBytes(input, address, fn.size,
                                               [&](UInt32 i, UInt8 value)
                                               {
                                                   return (installed && i < sizeof(entry))
                                                          || UserCacheResolver::skipped(i, fn.originalRefs,
                                                                                        fn.originalCount)
                                                          || value == fn.original[i];
                                               }))
            {
                return false;
            }
            for (UInt32 r = 0; r < fn.originalCount; ++r) {
                const auto& ref = fn.originalRefs[r];
                UInt8       field[4];
                UInt64      target{0}, expected{0};
                if (ref.indirect || ref.image != 0 || !input.read(address + ref.offset, field, 4)
                    || !relativeTarget(address + ref.offset, 0, field, target) || !image.contains(target, 4)
                    || !UserCacheResolver::target(input, ref, &bounds, 1, ResolverImages, &fn, &address, 1, expected,
                                                  sectionRelative, sectionTargets, sectionTargetCount)
                    || target != expected)
                {
                    return false;
                }
            }
            return true;
        };
        bool hot = false;
        if (fast) {
            fast->restart();
            hot = fast->observeImages(input.cache.address, input.cache.size, &bounds, refs, 1)
                  && fast->lookup(VPF::FunctionGroup::DisplaySubmit, functionImages, 1, known);
        }
        if (hot) {
            if (known[0].address != 0 && !known[0].patched && image.text.contains(known[0].address, fn.size)
                && candidate(known[0].address))
            {
                offset = known[0].address - base;
                return true;
            }
            restoreBudget(input, budget);
            fast->rejectHot();
        }
        if (!UserCacheResolver::scanFunction(
                input, image.text, fn.size, 32,
                [&](const UInt8* bytes) { return prefixes[0].matches(bytes) || prefixes[1].matches(bytes); }, candidate,
                found, 0, anchored ? &anchor : nullptr))
        {
            return false;
        }
        offset = found - base;
        if (fast) {
            UserCacheResolver::KnownLocation located[1] = {{found, false}};
            fast->publish(VPF::FunctionGroup::DisplaySubmit, located, functionImages, 1);
        }
        return true;
    }

    inline bool resolveSubmitForOS(Input& input, UInt64 base, UInt64& offset, UInt32 major,
                                   VPF::FastResolution* fast = nullptr)
    {
        return UserPatchProfiles::dispatch(major,
                                           [&](auto profile)
                                           {
                                               using Data = decltype(profile);
                                               return resolveSubmitProfile(
                                                   input, base, offset, Data::SubmitFunction, Data::SubmitImages, true,
                                                   fast, Data::SubmitTargets, Data::SubmitTargetCount);
                                           });
    }

    inline bool resolveSubmit(Input& input, UInt64 base, UInt64& offset, bool tahoe,
                              VPF::FastResolution* fast = nullptr)
    {
        return resolveSubmitForOS(input, base, offset, tahoe ? 25 : 24, fast);
    }

    inline bool resolveSubmit(Reader reader, UInt64 base, UInt64& offset, bool tahoe,
                              VPF::FastResolution* fast = nullptr)
    {
        Input input{reader, {}};
        return resolveSubmit(input, base, offset, tahoe, fast);
    }

    inline bool resolveSequoiaSubmit(Reader reader, UInt64 base, UInt64& offset)
    {
        return resolveSubmit(reader, base, offset, false);
    }

    inline bool resolveTahoeSubmit(Reader reader, UInt64 base, UInt64& offset)
    {
        return resolveSubmit(reader, base, offset, true);
    }

}    // namespace UserSurfaceSyncResolver
