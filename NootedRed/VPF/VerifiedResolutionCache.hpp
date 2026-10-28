// VPF：有界、不可变的解析位置缓存；条目只保存相对布局，不引用 map 或进程内存。
#pragma once
#include <VPF/ResolutionIdentity.hpp>
#include <string.h>

namespace VPF
{

    struct CachedImageLocation
    {
        UInt32 imageIndex;
        UInt32 mappingObjectId;
        UInt8  mappingShareMode;
        UInt64 relativeStart;
        UInt64 textRelativeStart;
        UInt32 textLength;
    };

    struct CachedFunctionLocation
    {
        UInt32 imageIndex;
        UInt32 functionIndex;
        // 0 为原始函数，1 为生成数据中的已补丁变体；不表示本进程已安装。
        UInt8  variant;
        UInt64 imageRelativeRVA;
    };

    struct CachedResolution
    {
        ResolutionKey          key;
        ProofClass             proof;
        UInt32                 imageCount;
        UInt32                 functionCount;
        UInt32                 flags;
        UInt32                 resolverRevision;
        UInt64                 cacheSize;
        UInt64                 proofFingerprint;
        CachedImageLocation    images[ResolutionMaxImages];
        CachedFunctionLocation functions[ResolutionMaxFunctions];
    };

    enum class PublishResult : UInt8
    {
        Invalid,
        Published,
        Duplicate,
        Conflict,
        Full,
        Disabled,
    };

    enum class ResolutionStat : UInt32
    {
        LookupHit,
        LookupMiss,
        Published,
        Duplicate,
        Conflict,
        Full,
        Invalid,
        Disabled,
        Upgraded,
        Count
    };

    struct ResolutionCacheEntry
    {
        UInt32           state;    // 0 空，1 可用，2 冲突禁用
        CachedResolution value;
    };

    // 固定容量；满表不淘汰、不覆盖。发布与快照复制都在短锁内完成，
    // 临界区只有固定结构复制，不读取用户内存、不等待其它 map。
    struct VerifiedResolutionCache
    {
        static constexpr UInt32 Capacity = 32;

        UInt32               ready;
        UInt32               guard;
        ResolutionCacheEntry entries[Capacity];
        UInt64               stats[static_cast<UInt32>(ResolutionStat::Count)];

        void init() { ready = 1; }

        void record(const ResolutionStat stat, const UInt64 amount = 1)
        {
            __atomic_fetch_add(&stats[static_cast<UInt32>(stat)], amount, __ATOMIC_RELAXED);
        }

        UInt64 stat(const ResolutionStat value) const
        {
            return __atomic_load_n(&stats[static_cast<UInt32>(value)], __ATOMIC_RELAXED);
        }

        // 复制稳定快照后立即释放锁；调用方不得在持锁期间读取用户内存。
        bool lookup(const ResolutionKey& key, CachedResolution& out)
        {
            if (!ready) {
                record(ResolutionStat::LookupMiss);
                return false;
            }
            acquire();
            bool found = false;
            for (UInt32 i = 0; i < Capacity; ++i) {
                const auto& entry = entries[i];
                if (entry.state == 1 && entry.value.key == key) {
                    out   = entry.value;
                    found = true;
                    break;
                }
            }
            release();
            record(found ? ResolutionStat::LookupHit : ResolutionStat::LookupMiss);
            return found;
        }

        PublishResult publish(const CachedResolution& value)
        {
            if (!valid(value)) {
                record(ResolutionStat::Invalid);
                return PublishResult::Invalid;
            }
            if (!ready) {
                record(ResolutionStat::Disabled);
                return PublishResult::Disabled;
            }
            acquire();
            const auto result = insert(value);
            release();
            return result;
        }

    private:
        void acquire()
        {
            while (__atomic_exchange_n(&guard, 1U, __ATOMIC_ACQUIRE) != 0) {
                while (__atomic_load_n(&guard, __ATOMIC_RELAXED) != 0) { }
            }
        }
        void release() { __atomic_store_n(&guard, 0U, __ATOMIC_RELEASE); }

        static bool valid(const CachedResolution& value)
        {
            if (!value.key.valid() || value.resolverRevision == 0
                || static_cast<UInt32>(value.proof) > static_cast<UInt32>(ProofClass::ReusableImmutableIdentity))
            {
                return false;
            }
            if (value.imageCount == 0 || value.imageCount > ResolutionMaxImages || value.functionCount == 0
                || value.functionCount > ResolutionMaxFunctions || value.cacheSize == 0)
            {
                return false;
            }
            for (UInt32 i = 0; i < value.imageCount; ++i) {
                const auto& image = value.images[i];
                if (image.imageIndex != i || image.textLength == 0 || image.relativeStart >= UserSharedCache::UserLimit
                    || image.textRelativeStart < image.relativeStart
                    || image.textLength > UserSharedCache::UserLimit - image.textRelativeStart
                    || image.textRelativeStart + image.textLength > image.relativeStart + value.cacheSize)
                {
                    return false;
                }
                if (value.proof == ProofClass::ReusableImmutableIdentity
                    && (image.mappingObjectId == 0 || image.mappingShareMode == 0))
                {
                    return false;
                }
            }
            for (UInt32 i = 0; i < value.functionCount; ++i) {
                const auto& function = value.functions[i];
                if (function.functionIndex >= value.functionCount || function.imageIndex >= value.imageCount
                    || function.variant > 1 || function.imageRelativeRVA >= UserSharedCache::UserLimit)
                {
                    return false;
                }
                for (UInt32 j = 0; j < i; ++j) {
                    if (value.functions[j].functionIndex == function.functionIndex) { return false; }
                }
            }
            return true;
        }

        // 布局与函数位置一致时才允许同一键继续使用；映射身份作为可复用证明的附加证据。
        static bool sameLayout(const CachedResolution& left, const CachedResolution& right)
        {
            if (left.resolverRevision != right.resolverRevision || left.flags != right.flags
                || left.cacheSize != right.cacheSize || left.imageCount != right.imageCount
                || left.functionCount != right.functionCount)
            {
                return false;
            }
            for (UInt32 i = 0; i < left.imageCount; ++i) {
                const auto& a = left.images[i];
                const auto& b = right.images[i];
                if (a.imageIndex != b.imageIndex || a.relativeStart != b.relativeStart
                    || a.textRelativeStart != b.textRelativeStart || a.textLength != b.textLength)
                {
                    return false;
                }
            }
            for (UInt32 i = 0; i < left.functionCount; ++i) {
                const auto& a = left.functions[i];
                const auto& b = right.functions[i];
                if (a.imageIndex != b.imageIndex || a.functionIndex != b.functionIndex || a.variant != b.variant
                    || a.imageRelativeRVA != b.imageRelativeRVA)
                {
                    return false;
                }
            }
            return true;
        }

        static bool sameEvidence(const CachedResolution& left, const CachedResolution& right)
        {
            for (UInt32 i = 0; i < left.imageCount; ++i) {
                if (left.images[i].mappingObjectId != right.images[i].mappingObjectId
                    || left.images[i].mappingShareMode != right.images[i].mappingShareMode)
                {
                    return false;
                }
            }
            return true;
        }

        PublishResult insert(const CachedResolution& value)
        {
            for (UInt32 i = 0; i < Capacity; ++i) {
                auto& entry = entries[i];
                if (entry.state == 0 || entry.value.key != value.key) { continue; }
                if (entry.state == 1 && sameLayout(entry.value, value)) {
                    if (value.proof == ProofClass::ReusableImmutableIdentity
                        && entry.value.proof == ProofClass::HintOnly)
                    {
                        // HintOnly 不参与热路径；允许同布局的较强证明替换条目。
                        entry.value = value;
                        record(ResolutionStat::Upgraded);
                        record(ResolutionStat::Published);
                        return PublishResult::Published;
                    }
                    if (entry.value.proof == ProofClass::ReusableImmutableIdentity
                        && value.proof == ProofClass::ReusableImmutableIdentity && !sameEvidence(entry.value, value))
                    {
                        entry.state = 2;
                        record(ResolutionStat::Conflict);
                        return PublishResult::Conflict;
                    }
                    record(ResolutionStat::Duplicate);
                    return PublishResult::Duplicate;
                }
                // 相同键出现不同位置证据时禁用条目，后续继续走完整扫描。
                entry.state = 2;
                record(ResolutionStat::Conflict);
                return PublishResult::Conflict;
            }
            for (UInt32 i = 0; i < Capacity; ++i) {
                auto& entry = entries[i];
                if (entry.state != 0) { continue; }
                entry.value = value;
                entry.state = 1;
                record(ResolutionStat::Published);
                return PublishResult::Published;
            }
            record(ResolutionStat::Full);
            return PublishResult::Full;
        }
    };

    inline VerifiedResolutionCache resolutionCache;

}    // namespace VPF
