// 按驱动路径与完整函数内容解析兼容的共享缓存；仅生成计划，不写入进程。
#pragma once
#include <UserSequoiaSurfaceData.hpp>
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
        static_assert(BufferSize >= UserSurfaceSyncData::MaxPatchSize && BufferSize <= 512);
        UserSurfaceSyncData::Patch     patches[4]{};
        UInt8                          original[4][BufferSize]{};
        UInt8                          patched[4][BufferSize]{};
        UInt32                         reads{0};
        Input                          input{nullptr, {}};
        Image                          image;
        UInt8                          scan[Input::ScanSize];
        UserCacheResolver::ImageBounds scratchImages[sizeof(ScratchResolverData::ResolverImages)
                                                     / sizeof(ScratchResolverData::ResolverImages[0])]{};
        UserCacheResolver::ImageBounds constructorImages[sizeof(UserSurfaceConstructorData::ResolverImages)
                                                         / sizeof(UserSurfaceConstructorData::ResolverImages[0])]{};
        UserCacheResolver::ImageBounds sequoiaImages[sizeof(UserSequoiaSurfaceData::ResolverImages)
                                                     / sizeof(UserSequoiaSurfaceData::ResolverImages[0])]{};
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

    // Scratch 子解析器：10 个映像、2 个完整函数、1 个补丁位置。
    inline bool resolveScratch(Input& input, UInt64 base, const DevicePatchSet& device, Plan& plan,
                               VPF::FastResolution* fast)
    {
        using namespace ScratchResolverData;
        constexpr auto Images    = sizeof(ResolverImages) / sizeof(ResolverImages[0]);
        constexpr auto Functions = sizeof(ResolverFunctions) / sizeof(ResolverFunctions[0]);
        static_assert(Images <= 16 && Functions <= 32);
        auto& bounds = plan.scratchImages;
        if (!UserCacheResolver::resolveImages(input, base, ResolverImages, bounds)) { return false; }
        UInt32 functionImages[Functions]{};
        for (UInt32 i = 0; i < Functions; ++i) { functionImages[i] = ResolverFunctions[i].image; }
        UserCacheResolver::KnownLocation known[Functions]{};
        const auto                       budget = saveBudget(input);
        if (fast) {
            fast->restart();
            if (fast->observeImages(input.cache.address, input.cache.size, bounds, ResolverImages, Images)
                && fast->lookup(VPF::FunctionGroup::DisplayScratch, functionImages, Functions, known))
            {
                if (UserCacheResolver::resolveFunctions(input, base, ResolverImages, ResolverFunctions,
                                                        device.scratch + 3, ResolverLocations, plan.patches + 3,
                                                        &plan.original[3][0], &plan.patched[3][0], Plan::BufferSize,
                                                        bounds, true, nullptr, 0, known, nullptr))
                {
                    return true;
                }
                restoreBudget(input, budget);
                fast->rejectHot();
            }
        }
        UserCacheResolver::KnownLocation located[Functions]{};
        if (!UserCacheResolver::resolveFunctions(input, base, ResolverImages, ResolverFunctions, device.scratch + 3,
                                                 ResolverLocations, plan.patches + 3, &plan.original[3][0],
                                                 &plan.patched[3][0], Plan::BufferSize, bounds, true, nullptr, 0,
                                                 nullptr, located))
        {
            return false;
        }
        if (fast) { fast->publish(VPF::FunctionGroup::DisplayScratch, located, functionImages, Functions); }
        return true;
    }

    // 构造器子解析器：4 个映像、1 个完整函数、1 个补丁位置。
    inline bool resolveConstructor(Input& input, UInt64 base, Plan& plan, VPF::FastResolution* fast)
    {
        using namespace UserSurfaceConstructorData;
        constexpr auto Images    = sizeof(ResolverImages) / sizeof(ResolverImages[0]);
        constexpr auto Functions = sizeof(ResolverFunctions) / sizeof(ResolverFunctions[0]);
        static_assert(Images <= 16 && Functions <= 32);
        auto& bounds = plan.constructorImages;
        if (!UserCacheResolver::resolveImages(input, base, ResolverImages, bounds)) { return false; }
        UInt32 functionImages[Functions]{};
        for (UInt32 i = 0; i < Functions; ++i) { functionImages[i] = ResolverFunctions[i].image; }
        UserCacheResolver::KnownLocation known[Functions]{};
        const auto                       budget = saveBudget(input);
        if (fast) {
            fast->restart();
            if (fast->observeImages(input.cache.address, input.cache.size, bounds, ResolverImages, Images)
                && fast->lookup(VPF::FunctionGroup::DisplayConstructor, functionImages, Functions, known))
            {
                if (UserCacheResolver::resolveFunctions(input, base, ResolverImages, ResolverFunctions,
                                                        UserSurfaceConstructorData::Patches, ResolverLocations,
                                                        plan.patches + 2, &plan.original[2][0], &plan.patched[2][0],
                                                        Plan::BufferSize, bounds, true, nullptr, 0, known, nullptr))
                {
                    return true;
                }
                restoreBudget(input, budget);
                fast->rejectHot();
            }
        }
        UserCacheResolver::KnownLocation located[Functions]{};
        if (!UserCacheResolver::resolveFunctions(input, base, ResolverImages, ResolverFunctions,
                                                 UserSurfaceConstructorData::Patches, ResolverLocations,
                                                 plan.patches + 2, &plan.original[2][0], &plan.patched[2][0],
                                                 Plan::BufferSize, bounds, true, nullptr, 0, nullptr, located))
        {
            return false;
        }
        if (fast) { fast->publish(VPF::FunctionGroup::DisplayConstructor, located, functionImages, Functions); }
        return true;
    }

    inline bool resolve(Input& input, UInt64 base, const DevicePatchSet& device, bool shared, bool blend, Plan& plan,
                        bool scratchMode = false, VPF::FastResolution* fast = nullptr)
    {
        auto& image = plan.image;
        memset(&image, 0, sizeof(image));
        // Scratch 与撤回的 VT 策略按完整函数和实际相对引用核验。
        if (scratchMode && !resolveScratch(input, base, device, plan, fast)) { return false; }
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
        const UInt8* const   variants[]  = {Original0, device.immediate[0].patched, device.shared[0].patched};
        const RelativeField* links[]     = {OriginalInitLinks, ImmediateInitLinks, SharedInitLinks};
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
                    || !image.text.contains(settingsKnown.address, sizeof(NativeOverride))
                    || !matches(input, settingsKnown.address, NativeOverride, sizeof(NativeOverride)))
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
                        || !image.text.contains(initKnown.address, sizeof(Original0)) || !matchInitAt(init, targets))
                    {
                        hot = false;
                    }
                }
                if (hot) {
                    const auto& overrideKnown = known[OverrideIndex];
                    overrideAddress           = overrideKnown.address;
                    if (overrideKnown.address == 0 || overrideKnown.patched
                        || !image.text.contains(overrideKnown.address, sizeof(Original1))
                        || !matchOverrideAt(overrideAddress))
                    {
                        hot = false;
                    }
                }
                if (hot) {
                    const auto& constructorKnown = known[ConstructorIndex];
                    if (constructorKnown.address != 0) {
                        if (constructorKnown.patched
                            || !image.text.contains(constructorKnown.address, sizeof(BlendConstructor))
                            || !matches(input, constructorKnown.address, BlendConstructor, sizeof(BlendConstructor),
                                        nullptr, 0, true))
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
            if (!findFunction(input, image.text, NativeOverride, sizeof(NativeOverride), settings, false,
                              image.address + NativeOverrideRVA)
                || (!scratchMode
                    && !findFunction(input, image.text, ScratchOriginal, sizeof(ScratchOriginal), scratch, false,
                                     image.address + ScratchRVA)))
            {
                return false;
            }
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
            constructorDirect = findFunction(input, image.text, BlendConstructor, sizeof(BlendConstructor), constructor,
                                             true, image.address + ConstructorRVA);
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
            if (!constructorDirect) {
                if (!resolveConstructor(input, base, plan, fast)) { return false; }
                constructor = base + plan.patches[2].offset - BlendGateInConstructor;
            }
        }
        else if (!constructorDirect) {
            if (!resolveConstructor(input, base, plan, fast)) { return false; }
            constructor = base + plan.patches[2].offset - BlendGateInConstructor;
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

    inline bool resolve(Reader reader, UInt64 base, const DevicePatchSet& device, bool shared, bool blend, Plan& plan,
                        bool scratchMode = false, VPF::FastResolution* fast = nullptr)
    {
        plan.input            = {reader, {}};
        plan.input.scanBuffer = plan.scan;
        return resolve(plan.input, base, device, shared, blend, plan, scratchMode, fast);
    }

    inline bool resolveSequoia(Input& input, UInt64 base, UInt32 deviceID, Plan& plan,
                               VPF::FastResolution* fast = nullptr)
    {
        using namespace UserSequoiaSurfaceData;
        const auto* patches = UserSequoiaSurfaceData::findDevice(deviceID);
        if (!patches) { return false; }
        constexpr auto Images    = sizeof(ResolverImages) / sizeof(ResolverImages[0]);
        constexpr auto Functions = sizeof(ResolverFunctions) / sizeof(ResolverFunctions[0]);
        static_assert(Images <= 16 && Functions <= 32);
        auto& bounds = plan.sequoiaImages;
        if (!UserCacheResolver::resolveImages(input, base, ResolverImages, bounds)) { return false; }
        UInt32 functionImages[Functions]{};
        for (UInt32 i = 0; i < Functions; ++i) { functionImages[i] = ResolverFunctions[i].image; }
        UserCacheResolver::KnownLocation known[Functions]{};
        UserCacheResolver::KnownLocation located[Functions]{};
        const auto                       budget = saveBudget(input);
        bool                             hot    = false;
        if (fast) {
            fast->restart();
            hot = fast->observeImages(input.cache.address, input.cache.size, bounds, ResolverImages, Images)
                  && fast->lookup(VPF::FunctionGroup::DisplaySequoia, functionImages, Functions, known);
        }
        bool resolved = hot
                        && UserCacheResolver::resolveFunctions(input, base, ResolverImages, ResolverFunctions, patches,
                                                               ResolverLocations, plan.patches, &plan.original[0][0],
                                                               &plan.patched[0][0], Plan::BufferSize, bounds, false,
                                                               nullptr, 0, known, nullptr);
        if (!resolved && hot) {
            restoreBudget(input, budget);
            fast->rejectHot();
            hot = false;
        }
        if (!resolved) {
            resolved = UserCacheResolver::resolveFunctions(input, base, ResolverImages, ResolverFunctions, patches,
                                                           ResolverLocations, plan.patches, &plan.original[0][0],
                                                           &plan.patched[0][0], Plan::BufferSize, bounds, false,
                                                           nullptr, 0, nullptr, located);
        }
        if (!resolved) { return false; }
        const auto& init = ResolverFunctions[0];
        UInt64      constant{0}, dump{0};
        UInt8       value[4], dumpBytes[sizeof(NativeDump)];
        const auto  address   = base + plan.patches[0].offset;
        const auto  postCheck = [&]
        {
            return relativeTarget(address, init.originalRefs[0].offset, plan.original[0], constant)
                   && input.read(constant, value, sizeof(value)) && word(value) == 0x40400000
                   && relativeTarget(address, init.originalRefs[2].offset, plan.original[0], dump)
                   && input.read(dump, dumpBytes, sizeof(dumpBytes))
                   && memcmp(dumpBytes, NativeDump, sizeof(NativeDump)) == 0;
        };
        if (!postCheck()) {
            if (!hot) { return false; }
            restoreBudget(input, budget);
            fast->rejectHot();
            hot = false;
            if (!UserCacheResolver::resolveFunctions(input, base, ResolverImages, ResolverFunctions, patches,
                                                     ResolverLocations, plan.patches, &plan.original[0][0],
                                                     &plan.patched[0][0], Plan::BufferSize, bounds, false, nullptr, 0,
                                                     nullptr, located)
                || !postCheck())
            {
                return false;
            }
        }
        if (!hot && fast) { fast->publish(VPF::FunctionGroup::DisplaySequoia, located, functionImages, Functions); }
        plan.reads = input.reads;
        return true;
    }

    inline bool resolveSequoia(Reader reader, UInt64 base, UInt32 deviceID, Plan& plan,
                               VPF::FastResolution* fast = nullptr)
    {
        plan.input            = {reader, {}};
        plan.input.scanBuffer = plan.scan;
        return resolveSequoia(plan.input, base, deviceID, plan, fast);
    }

    inline bool resolveSubmit(Input& input, UInt64 base, UInt64& offset, bool sectionRelative,
                              VPF::FastResolution* fast = nullptr)
    {
        using namespace UserSequoiaSurfaceData::Submit;
        Image image;
        if (!imageForCache(input, base, image)) { return false; }
        const auto& fn =
            sectionRelative ? UserSurfaceConstructorData::Submit::ResolverFunctions[0] : ResolverFunctions[0];
        if (image.segmentCount > 8 || fn.size < 15) { return false; }
        UserCacheResolver::ImageBounds bounds{};
        if (!imageBounds(image, bounds)) { return false; }
        const UserCacheResolver::ImageReference refs[1]           = {{DriverPath, sizeof(DriverPath)}};
        UInt32                                  functionImages[1] = {0};
        UserCacheResolver::KnownLocation        known[1]{};
        const auto                              budget = saveBudget(input);
        UInt64                                  nativeHead, nativeTail;
        __builtin_memcpy(&nativeHead, fn.original, sizeof(nativeHead));
        __builtin_memcpy(&nativeTail, fn.original + 7, sizeof(nativeTail));
        UInt64     found{0};
        const auto candidate = [&](UInt64 address)
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
                                                  sectionRelative)
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
                input, image.text, fn.size, 15,
                [&](const UInt8* bytes)
                {
                    UInt64 head;
                    __builtin_memcpy(&head, bytes, sizeof(head));
                    if (head == nativeHead) {
                        UInt64 tail;
                        __builtin_memcpy(&tail, bytes + 7, sizeof(tail));
                        if (tail == nativeTail) { return true; }
                    }
                    return (head & 0x0000FFFFFFFFFFFFULL) == 0x25FF && bytes[14] == 0x90;
                },
                candidate, found))
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

    inline bool resolveSubmit(Reader reader, UInt64 base, UInt64& offset, bool sectionRelative,
                              VPF::FastResolution* fast = nullptr)
    {
        Input input{reader, {}};
        return resolveSubmit(input, base, offset, sectionRelative, fast);
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
