// 只匹配当前校验页内的完整修复块；不读取文件、相邻页或相对引用的目标内存。
#pragma once
#include <UserOpenGLData.hpp>

namespace UserOpenGLResolver
{

    constexpr UInt32 PageSize    = 4096;
    constexpr UInt64 MaxFileSize = 32 * 1024 * 1024;
    struct Resolved
    {
        UInt8  original[256], patched[256];
        UInt32 offset, size;
        bool   dynamic, alreadyPatched;
    };

    inline bool exactPage(const UInt8* bytes, const UserOpenGLData::Page& page, bool patched)
    {
        if (!patched) { return memcmp(bytes, page.original, PageSize) == 0; }
        const auto end = page.offset + page.size;
        return memcmp(bytes, page.original, page.offset) == 0
               && memcmp(bytes + page.offset, page.patched, page.size) == 0
               && memcmp(bytes + end, page.original + end, PageSize - end) == 0;
    }

    inline bool matches(const UserOpenGLData::Device& device, UInt32 kind, const UInt8* bytes, UInt64 address,
                        bool patched)
    {
        const auto& patch    = device.patches[kind];
        const auto& fn       = device.resolver->functions[kind];
        const auto  start    = device.resolver->locations[kind].offset;
        const auto* expected = patched ? patch.patched : patch.original;
        const auto* refs     = patched ? fn.patchedRefs : fn.originalRefs;
        const auto  count    = patched ? fn.patchedCount : fn.originalCount;
        // 两套已核验载荷的前八字节均不含 rel32，先快速排除绝大多数位置。
        if (memcmp(bytes, expected, sizeof(UInt64)) != 0) { return false; }
        for (UInt32 i = sizeof(UInt64); i < patch.size; ++i) {
            if (!UserCacheResolver::skipped(start + i, refs, count) && bytes[i] != expected[i]) { return false; }
        }
        for (UInt32 r = 0; r < count; ++r) {
            UInt64 target;
            if (refs[r].indirect || refs[r].offset < start || refs[r].offset - start > patch.size - 4
                || !UserSharedCache::relativeTarget(address, refs[r].offset - start, bytes, target)
                || target != refs[r].rva)
            {
                return false;
            }
        }
        return true;
    }

    __attribute__((noinline)) inline bool resolvePage(const UserOpenGLData::Device& device, const UInt8* bytes,
                                                      UInt64 fileOffset, UInt32 kind, Resolved& output)
    {
        if (!bytes || kind >= 2 || (fileOffset & (PageSize - 1)) || fileOffset > MaxFileSize - PageSize) {
            return false;
        }
        const auto& patch = device.patches[kind];
        const auto& page  = device.pages[kind];
        if (patch.size < sizeof(UInt64) || patch.size > sizeof(output.patched) || page.size != patch.size
            || page.offset > PageSize - page.size)
        {
            return false;
        }
        UInt32     found{0};
        bool       seen{false}, alreadyPatched{false};
        const bool exact = patch.offset >= page.offset && fileOffset == patch.offset - page.offset
                           && (exactPage(bytes, page, false) || exactPage(bytes, page, true));
        if (exact) {
            found          = page.offset;
            alreadyPatched = memcmp(bytes + found, page.patched, page.size) == 0;
        }
        else {
            for (UInt32 offset = 0; offset <= PageSize - patch.size; ++offset) {
                const auto address = fileOffset + offset;
                const bool native  = matches(device, kind, bytes + offset, address, false);
                const bool patched = !native && matches(device, kind, bytes + offset, address, true);
                if (!native && !patched) { continue; }
                if (seen) { return false; }
                seen           = true;
                found          = offset;
                alreadyPatched = patched;
            }
            if (!seen) { return false; }
        }
        output.offset         = found;
        output.size           = patch.size;
        output.dynamic        = !exact;
        output.alreadyPatched = alreadyPatched;
        memcpy(output.original, bytes + found, patch.size);
        memcpy(output.patched, patch.patched, patch.size);
        const auto& fn    = device.resolver->functions[kind];
        const auto  start = device.resolver->locations[kind].offset;
        for (UInt32 r = 0; r < fn.patchedCount; ++r) {
            const auto& ref = fn.patchedRefs[r];
            if (ref.indirect || ref.offset < start || ref.offset - start > patch.size - 4
                || !UserSharedCache::setRelative(output.patched, ref.offset - start, fileOffset + found, ref.rva))
            {
                return false;
            }
        }
        return true;
    }

}    // namespace UserOpenGLResolver
