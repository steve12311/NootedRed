// VPF：冷/热解析共用的身份观察与缓存查发；不修改用户内存，不宣布功能已安装。
#pragma once
#include <UserCacheResolver.hpp>
#include <VPF/ResolutionDiagnostics.hpp>
#include <VPF/ResolutionIdentity.hpp>
#include <VPF/VerifiedResolutionCache.hpp>

namespace VPF
{

    // 由调用方在每个功能组开始时重置；所有证据只对当前 map 的本次调用有效。
    struct FastResolution
    {
        const UInt8* uuid;
        UInt32       darwinMajor;
        UInt32       profileId;
        UInt32       flags;
        MappingProbe probe;
        void*        probeContext;
        UInt64       cacheBase;
        UInt64       cacheSize;
        UInt32       imageCount;
        UInt64       imageAddresses[ResolutionMaxImages];
        // 布局与查发快照保存在调用方工作区中，避免大数组占用内核栈。
        ImageLayout      layouts[ResolutionMaxImages];
        UInt32           objectIds[ResolutionMaxImages];
        CachedResolution snapshot;
        CachedResolution staging;
        ImageSetIdentity identity;
        bool             mappingClean;
        bool             observed;
        bool             hot;
        bool             enabled;

        void begin(const UInt8* cacheUUID, const UInt32 major, const UInt32 profile, const UInt32 policyFlags,
                   const MappingProbe mappingProbe, void* mappingContext)
        {
            uuid         = cacheUUID;
            darwinMajor  = major;
            profileId    = profile;
            flags        = policyFlags;
            probe        = mappingProbe;
            probeContext = mappingContext;
            enabled =
                ResolutionDiagnostics::enabled() && resolutionCache.ready != 0 && uuid != nullptr && probe != nullptr;
            restart();
        }

        // 同一调用内切换功能组时保留 uuid/profile/探针，只清除当前映像证据。
        void restart()
        {
            cacheBase = cacheSize = 0;
            imageCount            = 0;
            identity              = {0, 0};
            mappingClean = observed = hot = false;
            memset(imageAddresses, 0, sizeof(imageAddresses));
            memset(layouts, 0, sizeof(layouts));
            memset(objectIds, 0, sizeof(objectIds));
        }

        // 只读取映像布局与当前 map 的区域属性；不扫描文本、不写页面。
        bool observeImages(const UInt64 base, const UInt64 size, const UserCacheResolver::ImageBounds* bounds,
                           const UserCacheResolver::ImageReference* refs, const UInt32 count)
        {
            observed = false;
            if (!enabled || !bounds || !refs || count == 0 || count > ResolutionMaxImages || base == 0 || size == 0
                || base >= UserSharedCache::UserLimit || size > UserSharedCache::UserLimit - base)
            {
                return false;
            }
            for (UInt32 i = 0; i < count; ++i) {
                if (!refs[i].path || refs[i].size == 0 || refs[i].size > 256 || bounds[i].address < base
                    || bounds[i].text.address < base || bounds[i].text.size == 0 || bounds[i].text.size > 0xFFFFFFFFULL)
                {
                    return false;
                }
                auto& layout             = layouts[i];
                layout.path              = refs[i].path;
                layout.pathSize          = refs[i].size;
                layout.relativeStart     = bounds[i].address - base;
                layout.textRelativeStart = bounds[i].text.address - base;
                layout.textLength        = static_cast<UInt32>(bounds[i].text.size);
                layout.mappingObjectId   = 0;
                layout.mappingShareMode  = 0;
                if (!layout.valid(size)) { return false; }
                imageAddresses[i] = bounds[i].address;
            }
            identity = imageSetIdentity(layouts, count, size);
            if (identity.low == 0 && identity.high == 0) { return false; }
            cacheBase    = base;
            cacheSize    = size;
            imageCount   = count;
            mappingClean = true;
            for (UInt32 i = 0; i < count; ++i) {
                MappingFacts facts{};
                if (!inspectMapping(probe, probeContext, bounds[i].text.address, bounds[i].text.size, facts)) {
                    mappingClean = false;
                    objectIds[i] = 0;
                    continue;
                }
                objectIds[i] = facts.objectId;
            }
            observed = true;
            return true;
        }

        ResolutionKey key(const FunctionGroup group) const
        {
            ResolutionKey value{};
            memcpy(value.uuid, uuid, sizeof(value.uuid));
            value.darwinMajor      = darwinMajor;
            value.arch             = ResolutionArchX86_64h;
            value.manifestRevision = ResolutionVerifierRevision;
            value.profileId        = profileId;
            value.functionGroupId  = static_cast<UInt32>(group);
            value.imageSet         = identity;
            return value;
        }

        // 仅当快照带可复用证明、布局与内核映射身份都匹配时才填充已知位置。
        bool lookup(const FunctionGroup group, const UInt32* functionImages, const UInt32 functionCount,
                    UserCacheResolver::KnownLocation* known)
        {
            hot = false;
            if (!enabled || !observed || !mappingClean || !functionImages || !known || functionCount == 0
                || functionCount > ResolutionMaxFunctions)
            {
                return false;
            }
            memset(known, 0, sizeof(UserCacheResolver::KnownLocation) * functionCount);
            auto& cached = snapshot;
            if (!resolutionCache.lookup(key(group), cached)) { return false; }
            if (cached.proof != ProofClass::ReusableImmutableIdentity || cached.imageCount != imageCount
                || cached.functionCount == 0 || cached.functionCount > functionCount || cached.cacheSize != cacheSize)
            {
                ResolutionDiagnostics::counters.record(ResolutionEvent::Fallback);
                return false;
            }
            for (UInt32 i = 0; i < imageCount; ++i) {
                const auto& image = cached.images[i];
                if (image.relativeStart != layouts[i].relativeStart
                    || image.textRelativeStart != layouts[i].textRelativeStart
                    || image.textLength != layouts[i].textLength || image.mappingObjectId != objectIds[i]
                    || image.mappingShareMode != MappingShareTrueShared)
                {
                    ResolutionDiagnostics::counters.record(ResolutionEvent::Fallback);
                    return false;
                }
            }
            for (UInt32 i = 0; i < cached.functionCount; ++i) {
                const auto& function = cached.functions[i];
                if (function.functionIndex >= functionCount || function.imageIndex >= imageCount
                    || functionImages[function.functionIndex] != function.imageIndex || function.variant > 1)
                {
                    ResolutionDiagnostics::counters.record(ResolutionEvent::Fallback);
                    return false;
                }
                const auto& image   = layouts[function.imageIndex];
                const auto  address = imageAddresses[function.imageIndex] + function.imageRelativeRVA;
                const auto  text    = cacheBase + image.textRelativeStart;
                if (address < text || address - text >= image.textLength) {
                    ResolutionDiagnostics::counters.record(ResolutionEvent::Fallback);
                    return false;
                }
                known[function.functionIndex] = {address, function.variant != 0};
            }
            hot = true;
            ResolutionDiagnostics::counters.record(ResolutionEvent::Hot);
            ResolutionDiagnostics::counters.record(ResolutionEvent::Hit);
            return true;
        }

        // 完整冷扫描成功后发布；只有整段文本都由同一可信共享对象提供时才可升级为 Reusable。
        void publish(const FunctionGroup group, const UserCacheResolver::KnownLocation* locations,
                     const UInt32* functionImages, const UInt32 functionCount)
        {
            if (!enabled || !observed || !locations || !functionImages || functionCount == 0
                || functionCount > ResolutionMaxFunctions || imageCount == 0)
            {
                return;
            }
            auto& value = staging;
            memset(&value, 0, sizeof(value));
            value.key              = key(group);
            value.proof            = mappingClean ? ProofClass::ReusableImmutableIdentity : ProofClass::HintOnly;
            value.imageCount       = imageCount;
            value.functionCount    = functionCount;
            value.flags            = flags;
            value.resolverRevision = ResolutionVerifierRevision;
            value.cacheSize        = cacheSize;
            UInt64 fingerprint     = identity.low ^ identity.high;
            for (UInt32 i = 0; i < imageCount; ++i) {
                value.images[i].imageIndex        = static_cast<UInt32>(i);
                value.images[i].mappingObjectId   = mappingClean ? objectIds[i] : 0;
                value.images[i].mappingShareMode  = static_cast<UInt8>(mappingClean ? MappingShareTrueShared : 0);
                value.images[i].relativeStart     = layouts[i].relativeStart;
                value.images[i].textRelativeStart = layouts[i].textRelativeStart;
                value.images[i].textLength        = layouts[i].textLength;
                fingerprint                       = identityMix(fingerprint, objectIds[i]);
            }
            for (UInt32 i = 0; i < functionCount; ++i) {
                const auto image = functionImages[i];
                if (image >= imageCount || locations[i].address == 0) { return; }
                const auto start =
                    imageAddresses[image] + (layouts[image].textRelativeStart - layouts[image].relativeStart);
                if (locations[i].address < start || locations[i].address - start >= layouts[image].textLength) {
                    return;
                }
                const auto rva     = locations[i].address - imageAddresses[image];
                value.functions[i] = {static_cast<UInt32>(image), static_cast<UInt32>(i),
                                      static_cast<UInt8>(locations[i].patched ? 1 : 0), rva};
                fingerprint        = identityMix(fingerprint, rva);
            }
            value.proofFingerprint = fingerprint;
            const auto result      = resolutionCache.publish(value);
            if (result == PublishResult::Published || result == PublishResult::Duplicate) {
                ResolutionDiagnostics::counters.record(mappingClean ? ResolutionEvent::Reusable :
                                                                      ResolutionEvent::HintOnly);
            }
        }

        void rejectHot()
        {
            hot = false;
            ResolutionDiagnostics::counters.record(ResolutionEvent::Fallback);
        }
    };

}    // namespace VPF
