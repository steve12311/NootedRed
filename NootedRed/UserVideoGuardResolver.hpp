// 完整核验原生函数、入口跳转及私有守卫页；未验证的入口不参与覆盖或回退。
#pragma once
#include <UserVideoGuard24Data.hpp>
#include <UserVideoGuard25Data.hpp>
#include <VPF/FastResolution.hpp>

namespace UserVideoGuardResolver
{

    using namespace UserVideoGuardData;
    using Patch = UserVideoDecodeData::Patch;
    struct Plan
    {
        Patch  patches[MaxEntries]{};
        UInt8  original[MaxEntries][EntrySize]{}, patched[MaxEntries][EntrySize]{};
        UInt8  expected[PageSize]{}, observed[PageSize]{};
        Patch  resolved[MaxFunctions]{};
        UInt8  nativePrefixes[MaxFunctions][IdentitySize]{}, unchanged[MaxFunctions][IdentitySize]{};
        UInt32 count{0}, length{0}, features{0}, reads{0};
        UInt64 page{0};
        bool   installed{false};
        UserCacheResolver::KnownLocation locations[MaxFunctions]{};
    };
    struct Overlay
    {
        UserSharedCache::Reader        reader;
        UserSharedCache::ContextReader parent;
        void*                          parentContext;
        Plan&                          plan;
        UInt64                         base;
    };
    inline int overlayReader(void* context, UInt64 address, void* bytes, size_t size)
    {
        const auto& overlay = *static_cast<Overlay*>(context);
        const auto  status  = overlay.parent ? overlay.parent(overlay.parentContext, address, bytes, size) :
                                               overlay.reader(address, bytes, size);
        if (status) { return status; }
        for (UInt32 i = 0; i < overlay.plan.count; ++i) {
            const auto start  = overlay.base + overlay.plan.patches[i].offset;
            const auto length = overlay.plan.patches[i].size;
            if (start >= address ? start - address >= size : address - start >= length) { continue; }
            for (UInt32 j = 0; j < length; ++j) {
                if (start + j >= address && start + j - address < size) {
                    static_cast<UInt8*>(bytes)[start + j - address] = overlay.plan.original[i][j];
                }
            }
        }
        return 0;
    }

    // 已验证位置只用于入口与完整函数核验；守卫页、绑定和 installed 仍由当前 map 重建。
    template<size_t ImageCount, size_t FunctionCount, size_t EntryCount, size_t BindingCount, size_t PayloadSize,
             size_t TargetCount>
    inline bool resolveProfileBody(UserSharedCache::Input& input, UInt64 base, Plan& plan,
                                   const UserCacheResolver::ImageReference (&images)[ImageCount],
                                   UserCacheResolver::ImageBounds          (&bounds)[ImageCount],
                                   const UserCacheResolver::Function       (&functions)[FunctionCount],
                                   const Patch                             (&constraints)[FunctionCount],
                                   const UserCacheResolver::Location       (&locations)[FunctionCount],
                                   const UserCacheSectionData::Target      (&targets)[TargetCount],
                                   const Entry (&entries)[EntryCount], const Binding (&bindings)[BindingCount],
                                   const UInt8 (&payload)[PayloadSize], UInt32 features, bool hot)
    {
        static_assert(FunctionCount <= MaxFunctions && EntryCount <= MaxEntries && PayloadSize <= PageSize);
        UInt32 installedCount{0};
        for (UInt32 i = 0; i < EntryCount; ++i) {
            const auto& entry = entries[i];
            if (entry.function >= FunctionCount || entry.size < 14 || entry.size > EntrySize
                || entry.offset < HeaderSize || entry.offset >= PayloadSize)
            {
                return false;
            }
            const auto& fn = functions[entry.function];
            if (fn.image >= ImageCount || fn.size < entry.size || fn.size < 32) { return false; }
            const auto& image = bounds[fn.image];
            bool        candidateInstalled{false};
            UInt64      candidatePage{0};
            const auto  byteMatches = [&](UInt32 offset, UInt8 value, bool installed)
            {
                if (installed && offset < entry.size) {
                    static constexpr UInt8 prefix[6] = {0xFF, 0x25, 0, 0, 0, 0};
                    return offset < 6 ? value == prefix[offset] : offset < 14 || value == 0x90;
                }
                return UserCacheResolver::skipped(offset, fn.originalRefs, fn.originalCount)
                       || value == fn.original[offset];
            };
            UserCacheResolver::Prefix prefixes[2];
            for (UInt32 variant = 0; variant < 2; ++variant) {
                for (UInt32 j = 0; j < 32; ++j) {
                    static constexpr UInt8 jump[6]   = {0xFF, 0x25, 0, 0, 0, 0};
                    const bool             entryByte = variant && j < entry.size;
                    const bool             masked =
                        entryByte ? j >= 6 && j < 14 : UserCacheResolver::skipped(j, fn.originalRefs, fn.originalCount);
                    prefixes[variant].set(j, entryByte ? (j < 6 ? jump[j] : 0x90) : fn.original[j], masked);
                }
            }
            const auto prefixMatches = [&](const UInt8* bytes)
            { return prefixes[0].matches(bytes) || prefixes[1].matches(bytes); };
            const auto candidateMatches = [&](UInt64 address)
            {
                for (UInt32 variant = 0; variant < 2; ++variant) {
                    if (!UserCacheResolver::matchBytes(input, address, fn.size, [&](UInt32 offset, UInt8 value)
                                                       { return byteMatches(offset, value, variant != 0); }))
                    {
                        continue;
                    }
                    candidateInstalled = variant != 0;
                    candidatePage      = 0;
                    if (candidateInstalled) {
                        UInt8 field[8];
                        if (!input.read(address + 6, field, sizeof(field))) { return false; }
                        const auto target = UserSharedCache::wide(field);
                        if (target < entry.offset) { return false; }
                        candidatePage = target - entry.offset;
                        if (candidatePage < 0x10000 || (candidatePage & (PageSize - 1)) != 0
                            || candidatePage >= UserSharedCache::UserLimit - PageSize)
                        {
                            return false;
                        }
                    }
                    return true;
                }
                return false;
            };
            UInt64 address{0};
            if (hot) {
                const auto known = plan.locations[entry.function];
                if (known.address == 0 || known.patched || !image.text.contains(known.address, fn.size)) {
                    return false;
                }
                address = known.address;
                if (!candidateMatches(address) || candidateInstalled) { return false; }
            }
            else if (!UserCacheResolver::scanFunction(input, image.text, fn.size, 32, prefixMatches, candidateMatches,
                                                      address))
            {
                return false;
            }
            if (address < base) { return false; }
            memcpy(plan.original[i], fn.original, entry.size);
            plan.patches[i] = {address - base, plan.original[i], plan.patched[i], entry.size};
            if (candidateInstalled) {
                ++installedCount;
                if (plan.page && plan.page != candidatePage) { return false; }
                plan.page = candidatePage;
            }
        }
        plan.count = EntryCount;
        if (installedCount && installedCount != EntryCount) { return false; }
        auto*   resolved = plan.resolved;
        Overlay overlay{input.reader, input.contextReader, input.readerContext, plan, base};
        input.readerContext = &overlay;
        input.contextReader = overlayReader;
        const bool matched  = UserCacheResolver::resolveFunctions(
            input, base, images, functions, constraints, locations, resolved, &plan.nativePrefixes[0][0],
            &plan.unchanged[0][0], IdentitySize, bounds, true, targets, TargetCount, hot ? plan.locations : nullptr,
            hot ? nullptr : plan.locations);
        input.contextReader = overlay.parent;
        input.readerContext = overlay.parentContext;
        if (!matched) { return false; }
        memcpy(plan.expected, payload, PayloadSize);
        for (const auto& binding : bindings) {
            if (binding.function >= FunctionCount || binding.offset > PayloadSize
                || PayloadSize - binding.offset < sizeof(UInt64))
            {
                return false;
            }
            const auto function = base + resolved[binding.function].offset;
            UInt64     value{0};
            if (binding.reference) {
                bool        typed = false;
                const auto& fn    = functions[binding.function];
                for (UInt32 r = 0; r < fn.originalCount; ++r) {
                    if (fn.originalRefs[r].offset == binding.value && fn.originalRefs[r].indirect == 0) {
                        typed = true;
                    }
                }
                if (!typed || binding.value > IdentitySize || IdentitySize - binding.value < 4
                    || !UserSharedCache::relativeTarget(function + binding.value, 0,
                                                        plan.nativePrefixes[binding.function] + binding.value, value))
                {
                    return false;
                }
            }
            else {
                if (binding.value >= functions[binding.function].size
                    || binding.value >= UserSharedCache::UserLimit - function)
                {
                    return false;
                }
                value = function + binding.value;
            }
            if (!value || value >= UserSharedCache::UserLimit) { return false; }
            memcpy(plan.expected + binding.offset, &value, sizeof(value));
        }
        plan.length   = PayloadSize;
        plan.features = features;
        if (installedCount) {
            if (input.reader(plan.page, plan.observed, PayloadSize) != 0
                || memcmp(plan.expected, plan.observed, PayloadSize) != 0)
            {
                return false;
            }
            plan.installed = true;
        }
        for (UInt32 i = 0; i < EntryCount; ++i) {
            const auto target = plan.page + entries[i].offset;
            memset(plan.patched[i], 0x90, entries[i].size);
            static constexpr UInt8 jump[6] = {0xFF, 0x25, 0, 0, 0, 0};
            memcpy(plan.patched[i], jump, sizeof(jump));
            memcpy(plan.patched[i] + 6, &target, sizeof(target));
        }
        return true;
    }

    template<size_t ImageCount, size_t FunctionCount, size_t EntryCount, size_t BindingCount, size_t PayloadSize,
             size_t TargetCount>
    inline bool resolveProfile(UserSharedCache::Input& input, UInt64 base, Plan& plan,
                               const UserCacheResolver::ImageReference (&images)[ImageCount],
                               const UserCacheResolver::Function       (&functions)[FunctionCount],
                               const Patch                             (&constraints)[FunctionCount],
                               const UserCacheResolver::Location       (&locations)[FunctionCount],
                               const UserCacheSectionData::Target      (&targets)[TargetCount],
                               const Entry (&entries)[EntryCount], const Binding (&bindings)[BindingCount],
                               const UInt8 (&payload)[PayloadSize], UInt32 features, VPF::FastResolution* fast)
    {
        if (!input.reader || input.failed || plan.count != 0 || base >= UserSharedCache::UserLimit) { return false; }
        UserCacheResolver::ImageBounds bounds[ImageCount]{};
        if (!UserCacheResolver::resolveImages(input, base, images, bounds)) { return false; }
        UInt32 functionImages[FunctionCount]{};
        for (UInt32 i = 0; i < FunctionCount; ++i) { functionImages[i] = static_cast<UInt32>(functions[i].image); }
        const UInt32 savedReads = input.reads, savedBytes = input.bytes, savedCandidates = input.candidates;
        const bool   savedFailed = input.failed;
        if (fast && fast->observeImages(input.cache.address, input.cache.size, bounds, images, ImageCount)
            && fast->lookup(VPF::FunctionGroup::VideoGuard, functionImages, FunctionCount, plan.locations))
        {
            if (resolveProfileBody(input, base, plan, images, bounds, functions, constraints, locations, targets,
                                   entries, bindings, payload, features, true))
            {
                plan.reads = input.reads;
                return true;
            }
            input.reads      = savedReads;
            input.bytes      = savedBytes;
            input.candidates = savedCandidates;
            input.failed     = savedFailed;
            fast->rejectHot();
            plan.count = plan.length = plan.features = 0;
            plan.page = plan.installed = false;
        }
        if (!resolveProfileBody(input, base, plan, images, bounds, functions, constraints, locations, targets, entries,
                                bindings, payload, features, false))
        {
            return false;
        }
        plan.reads = input.reads;
        if (fast) { fast->publish(VPF::FunctionGroup::VideoGuard, plan.locations, functionImages, FunctionCount); }
        return true;
    }

    inline bool resolve(UserSharedCache::Input& input, UInt64 base, UInt32 major, Plan& plan,
                        VPF::FastResolution* fast = nullptr)
    {
        if (major == 24) {
            using namespace UserVideoGuard24Data;
            return resolveProfile(input, base, plan, ResolverImages, ResolverFunctions, IdentityPatches,
                                  ResolverLocations, SectionTargets, Entries, Bindings, Payload, Features, fast);
        }
        if (major == 25) {
            using namespace UserVideoGuard25Data;
            return resolveProfile(input, base, plan, ResolverImages, ResolverFunctions, IdentityPatches,
                                  ResolverLocations, SectionTargets, Entries, Bindings, Payload, Features, fast);
        }
        return false;
    }

    inline bool setPage(Plan& plan, UInt64 page, UInt32 major)
    {
        if (page < 0x10000 || (page & (PageSize - 1)) != 0 || page >= UserSharedCache::UserLimit - PageSize) {
            return false;
        }
        if (major != 24 && major != 25) { return false; }
        const auto  count   = major == 24 ?
                                  sizeof(UserVideoGuard24Data::Entries) / sizeof(UserVideoGuard24Data::Entries[0]) :
                                  sizeof(UserVideoGuard25Data::Entries) / sizeof(UserVideoGuard25Data::Entries[0]);
        const auto* entries = major == 24 ? UserVideoGuard24Data::Entries : UserVideoGuard25Data::Entries;
        if (plan.count != count || plan.count > MaxEntries) { return false; }
        plan.page = page;
        for (UInt32 i = 0; i < plan.count; ++i) {
            const UInt64 target = page + entries[i].offset;
            memcpy(plan.patched[i] + 6, &target, sizeof(target));
        }
        return true;
    }

}    // namespace UserVideoGuardResolver
