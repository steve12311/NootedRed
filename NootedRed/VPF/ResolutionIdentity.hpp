// VPF：共享缓存解析的跨进程身份描述；只处理相对布局，不保存进程地址。
#pragma once
#include <IOKit/IOTypes.h>
#include <UserSharedCache.hpp>
#include <string.h>

namespace VPF
{

    constexpr UInt32 ResolutionMaxImages    = 16;
    constexpr UInt32 ResolutionMaxFunctions = 32;
    constexpr UInt32 ResolutionArchX86_64h  = 1;
    // 匹配算法或生成数据语义变化时递增，旧条目自动失效。
    constexpr UInt32 ResolutionVerifierRevision = 1;

    enum class FunctionGroup : UInt32
    {
        VideoDecode = 1,
        VideoGuard,
        DisplayCore,
        DisplayConstructor,
        DisplayScratch,
        DisplaySequoia,
        DisplaySubmit,
        Count
    };

    enum class ProofClass : UInt8
    {
        HintOnly = 0,
        // 仅当当前映射由同一可信只读对象提供且没有私有页时才可使用。
        ReusableImmutableIdentity = 1,
    };

    struct ImageSetIdentity
    {
        UInt64 low;
        UInt64 high;

        bool operator==(const ImageSetIdentity& other) const { return low == other.low && high == other.high; }
        bool operator!=(const ImageSetIdentity& other) const { return !(*this == other); }
    };

    struct ResolutionKey
    {
        UInt8            uuid[16];
        UInt32           darwinMajor;
        UInt32           arch;
        UInt32           manifestRevision;
        UInt32           profileId;
        UInt32           functionGroupId;
        ImageSetIdentity imageSet;

        bool valid() const
        {
            return darwinMajor != 0 && arch != 0 && manifestRevision != 0 && functionGroupId != 0
                   && functionGroupId < static_cast<UInt32>(FunctionGroup::Count);
        }

        bool operator==(const ResolutionKey& other) const
        {
            return memcmp(uuid, other.uuid, sizeof(uuid)) == 0 && darwinMajor == other.darwinMajor && arch == other.arch
                   && manifestRevision == other.manifestRevision && profileId == other.profileId
                   && functionGroupId == other.functionGroupId && imageSet == other.imageSet;
        }
        bool operator!=(const ResolutionKey& other) const { return !(*this == other); }
    };

    // 以缓存基址相对值描述映像；同一缓存在不同 ASLR 进程中必须得到相同值。
    struct ImageLayout
    {
        const char* path;
        UInt32      pathSize;
        UInt64      relativeStart;        // image.address - cache base
        UInt64      textRelativeStart;    // image.text.address - cache base
        UInt32      textLength;
        UInt32      mappingObjectId;    // 冷路径由内核区域查询记录
        UInt8       mappingShareMode;

        bool valid(const UInt64 cacheSize) const
        {
            return path != nullptr && pathSize != 0 && pathSize <= 256 && relativeStart < UserSharedCache::UserLimit
                   && textRelativeStart >= relativeStart && textLength != 0
                   && textLength <= UserSharedCache::UserLimit - textRelativeStart
                   && textRelativeStart + textLength <= relativeStart + cacheSize;
        }
    };

    inline UInt64 identityMix(UInt64 hash, UInt64 value)
    {
        // FNV-1a 64；仅用于稳定身份摘要，不单独承担内容真实性证明。
        for (UInt32 i = 0; i < 8; ++i) {
            hash ^= (value >> (i * 8)) & 0xFF;
            hash *= 0x00000100000001B3ULL;
        }
        return hash;
    }

    inline UInt64 identityBytes(UInt64 hash, const void* bytes, UInt32 size)
    {
        const auto* value = static_cast<const UInt8*>(bytes);
        for (UInt32 i = 0; i < size; ++i) {
            hash ^= value[i];
            hash *= 0x00000100000001B3ULL;
        }
        return hash;
    }

    inline ImageSetIdentity imageSetIdentity(const ImageLayout* images, const UInt32 count, const UInt64 cacheSize)
    {
        ImageSetIdentity identity{0x00000100000001B3ULL, 0xCBF29CE484222325ULL ^ 0x9E3779B97F4A7C15ULL};
        if (!images || count == 0 || count > ResolutionMaxImages) { return {0, 0}; }
        identity.low  = identityMix(identity.low, count);
        identity.high = identityMix(identity.high, count ^ 0xA5A5A5A5A5A5A5A5ULL);
        identity.low  = identityMix(identity.low, cacheSize);
        identity.high = identityMix(identity.high, cacheSize);
        for (UInt32 i = 0; i < count; ++i) {
            const auto& image = images[i];
            if (!image.valid(cacheSize)) { return {0, 0}; }
            identity.low           = identityBytes(identity.low, image.path, image.pathSize);
            identity.high          = identityBytes(identity.high, image.path, image.pathSize);
            const UInt64 fields[4] = {image.relativeStart, image.textRelativeStart, image.textLength,
                                      static_cast<UInt64>(image.pathSize)};
            for (const auto field : fields) {
                identity.low  = identityMix(identity.low, field);
                identity.high = identityMix(identity.high, field ^ 0x5DEECE66DULL);
            }
        }
        return identity;
    }

    // PCI 设备低 16 位与策略标志共同选择补丁规则。
    inline UInt32 resolutionProfile(const UInt32 deviceID, const UInt32 flags)
    {
        return (deviceID & 0xFFFFU) | (flags << 16);
    }

    // mach/vm_region.h 的 SM_TRUESHARED / SM_COW；VPF 层不包含内核 VM 头，由调用方适配。
    constexpr UInt8 MappingShareTrueShared = 5;
    // 共享缓存 __TEXT 在 Darwin 23-25 的机器级共享子映射中报告为 SM_COW，且原始对象自带
    // shadow 链（Tahoe 实测 shadow_depth=2，所有进程同一 object_id）。进程私自写入的页会被
    // 拆分为私有条目并换成新的顶层对象，因此“同一 object_id + 不可写”才是原始内容的证据；
    // COW 只接受不超过 MappingCowMaxShadow 的基线 shadow 深度，TRUESHARED 仍要求 0。
    constexpr UInt8  MappingShareCow     = 1;
    constexpr UInt32 MappingCowMaxShadow = 4;

    // 最近一次 inspectMapping 拒绝的原因，仅供诊断：低 8 位为原因码，8-15 位为 share_mode，
    // 16-23 位为 shadow_depth（截断）。0 表示最近一次检查通过。
    enum MappingReject : UInt32
    {
        MappingRejectNone = 0,
        MappingRejectArgument,
        MappingRejectRegions,
        MappingRejectProbe,
        MappingRejectSubmap,
        MappingRejectShareMode,
        MappingRejectShadow,
        MappingRejectWritable,
        MappingRejectObject,
        MappingRejectMixed,
        MappingRejectEmpty,
    };
    inline UInt32 lastMappingReject{0};

    inline void recordMappingReject(const UInt32 code, const UInt8 shareMode = 0, const UInt32 shadowDepth = 0)
    {
        const UInt32 shadow = shadowDepth > 0xFF ? 0xFF : shadowDepth;
        __atomic_store_n(&lastMappingReject, code | (static_cast<UInt32>(shareMode) << 8) | (shadow << 16),
                         __ATOMIC_RELAXED);
    }

    inline bool cleanShareMode(const UInt8 mode) { return mode == MappingShareTrueShared || mode == MappingShareCow; }

    struct MappingInfo
    {
        UInt64 start;
        UInt64 size;
        UInt32 depth;
        UInt32 objectId;
        UInt8  shareMode;
        UInt32 shadowDepth;
        bool   isSubmap;
        bool   writable;
    };

    // 调用方提供的当前 map 区域查询；只返回属性，不读取用户内容。
    using MappingProbe = bool (*)(void* context, UInt64 address, UInt32 depth, MappingInfo& info);

    struct MappingFacts
    {
        UInt32 objectId;
        UInt8  shareMode;
        UInt32 shadowDepth;
        bool   writable;
    };

    // 要求整个范围由同一只读共享对象覆盖（TRUESHARED 且无 shadow，或基线 shadow 的 COW）；
    // 可写条目、对象或 shadow 深度不一致（私有写入产生的新顶层对象）都拒绝。
    inline bool inspectMapping(const MappingProbe probe, void* context, const UInt64 address, const UInt64 size,
                               MappingFacts& facts)
    {
        if (!probe || address == 0 || size == 0 || address >= UserSharedCache::UserLimit
            || size > UserSharedCache::UserLimit - address)
        {
            recordMappingReject(MappingRejectArgument);
            return false;
        }
        constexpr UInt32 MaxLevels = 8;
        struct Level
        {
            UInt32 depth;
            UInt64 end;
        } levels[MaxLevels];
        UInt32 levelCount = 0, depth = 0, regions = 0;
        UInt64 cursor = address, end = address + size;
        UInt32 objectId  = 0;
        UInt8  shareMode   = 0;
        UInt32 shadowDepth = 0;
        bool   found       = false;
        while (cursor < end) {
            if (++regions > 4096) {
                recordMappingReject(MappingRejectRegions);
                return false;
            }
            MappingInfo info{};
            if (!probe(context, cursor, depth, info) || info.start > cursor || info.size == 0
                || info.size > UserSharedCache::UserLimit - info.start)
            {
                recordMappingReject(MappingRejectProbe);
                return false;
            }
            const UInt64 regionEnd = info.start + info.size;
            if (regionEnd <= cursor) {
                recordMappingReject(MappingRejectProbe);
                return false;
            }
            if (info.isSubmap) {
                // 子映射只用于找到叶子区域；实际证据必须来自叶子条目。
                if (levelCount == MaxLevels || info.depth >= MaxLevels) {
                    recordMappingReject(MappingRejectSubmap);
                    return false;
                }
                levels[levelCount++] = {depth, regionEnd};
                depth                = info.depth + 1;
                continue;
            }
            if (!cleanShareMode(info.shareMode)) {
                recordMappingReject(MappingRejectShareMode, info.shareMode, info.shadowDepth);
                return false;
            }
            if (info.shareMode == MappingShareCow ? info.shadowDepth > MappingCowMaxShadow : info.shadowDepth != 0) {
                recordMappingReject(MappingRejectShadow, info.shareMode, info.shadowDepth);
                return false;
            }
            if (info.writable) {
                recordMappingReject(MappingRejectWritable, info.shareMode, info.shadowDepth);
                return false;
            }
            if (info.objectId == 0) {
                recordMappingReject(MappingRejectObject, info.shareMode, info.shadowDepth);
                return false;
            }
            if (!found) {
                objectId    = info.objectId;
                shareMode   = info.shareMode;
                shadowDepth = info.shadowDepth;
                found       = true;
            }
            else if (info.objectId != objectId || info.shadowDepth != shadowDepth) {
                recordMappingReject(MappingRejectMixed, info.shareMode, info.shadowDepth);
                return false;
            }
            cursor = regionEnd;
            while (levelCount != 0 && cursor >= levels[levelCount - 1].end) { depth = levels[--levelCount].depth; }
        }
        if (!found) {
            recordMappingReject(MappingRejectEmpty);
            return false;
        }
        recordMappingReject(MappingRejectNone, shareMode, shadowDepth);
        facts = {objectId, shareMode, shadowDepth, false};
        return true;
    }

}    // namespace VPF
