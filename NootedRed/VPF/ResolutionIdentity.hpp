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

    // mach/vm_region.h 的 SM_TRUESHARED；VPF 层不包含内核 VM 头，由调用方适配。
    constexpr UInt8 MappingShareTrueShared = 5;

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

    // 要求整个范围由同一非私有只读共享对象覆盖；任何 COW/私有页或混合对象都拒绝。
    inline bool inspectMapping(const MappingProbe probe, void* context, const UInt64 address, const UInt64 size,
                               MappingFacts& facts)
    {
        if (!probe || address == 0 || size == 0 || address >= UserSharedCache::UserLimit
            || size > UserSharedCache::UserLimit - address)
        {
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
        UInt32 objectId = 0;
        bool   found    = false;
        while (cursor < end) {
            if (++regions > 4096) { return false; }
            MappingInfo info{};
            if (!probe(context, cursor, depth, info) || info.start > cursor || info.size == 0
                || info.size > UserSharedCache::UserLimit - info.start)
            {
                return false;
            }
            const UInt64 regionEnd = info.start + info.size;
            if (regionEnd <= cursor) { return false; }
            if (info.isSubmap) {
                // 子映射只用于找到叶子区域；实际证据必须来自叶子条目。
                if (levelCount == MaxLevels || info.depth >= MaxLevels) { return false; }
                levels[levelCount++] = {depth, regionEnd};
                depth                = info.depth + 1;
                continue;
            }
            if (info.shareMode != MappingShareTrueShared || info.shadowDepth != 0 || info.writable
                || info.objectId == 0)
            {
                return false;
            }
            if (!found) {
                objectId = info.objectId;
                found    = true;
            }
            else if (info.objectId != objectId) {
                return false;
            }
            cursor = regionEnd;
            while (levelCount != 0 && cursor >= levels[levelCount - 1].end) { depth = levels[--levelCount].depth; }
        }
        if (!found) { return false; }
        facts = {objectId, MappingShareTrueShared, 0, false};
        return true;
    }

}    // namespace VPF
