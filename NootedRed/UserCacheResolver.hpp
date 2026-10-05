// 完整函数匹配与 rel32 重建；不认识的实现或外部目标布局一律拒绝。
#pragma once
#include <UserSharedCache.hpp>

namespace UserCacheResolver
{

    using namespace UserSharedCache;

    // 模块只提供字节约束与候选核验；读取、预算、扫描边界及歧义拒绝统一处理。
    template<typename ByteMatches>
    inline bool matchBytes(Input& input, UInt64 address, UInt32 size, ByteMatches byteMatches)
    {
        UInt8 bytes[256];
        for (UInt32 offset = 0; offset < size;) {
            const auto length = size - offset < sizeof(bytes) ? size - offset : UInt32(sizeof(bytes));
            if (!input.read(address + offset, bytes, length)) { return false; }
            for (UInt32 i = 0; i < length; ++i) {
                if (!byteMatches(offset + i, bytes[i])) { return false; }
            }
            offset += length;
        }
        return !input.failed;
    }

    template<typename PrefixMatches, typename CandidateMatches>
    inline bool scanFunction(Input& input, const Range& text, UInt32 size, UInt32 prefixSize,
                             PrefixMatches prefixMatches, CandidateMatches candidateMatches, UInt64& found,
                             UInt64 hint = 0)
    {
        found = 0;
        if (prefixSize == 0 || prefixSize > 32 || size < prefixSize || size > text.size || text.size > MaxTextSize) {
            return false;
        }
        // 渲染基线保留完整核验后的参考 RVA 快路径；其他调用默认验证扫描唯一性。
        if (hint && text.contains(hint, size) && candidateMatches(hint)) {
            found = hint;
            return !input.failed;
        }
        UInt8 scan[1024];
        for (UInt64 offset = 0; offset <= text.size - size;) {
            const auto length = text.size - offset < sizeof(scan) ? UInt32(text.size - offset) : UInt32(sizeof(scan));
            if (!input.read(text.address + offset, scan, length)) { return false; }
            const auto positions = length - prefixSize + 1;
            for (UInt32 j = 0; j < positions && offset + j <= text.size - size; ++j) {
                if (!prefixMatches(scan + j)) { continue; }
                if (++input.candidates > 128) { return false; }
                const auto address = text.address + offset + j;
                if (!candidateMatches(address)) { continue; }
                if (found) { return false; }
                found = address;
            }
            offset += positions;
        }
        return found != 0 && !input.failed;
    }
    using UserSharedCache::ImageReference;
    struct Reference
    {
        UInt32 offset, image;
        UInt64 rva;
        UInt32 indirect;
    };
    struct Function
    {
        UInt32           image;
        UInt64           rva;
        const UInt8*     original;
        UInt32           size;
        const Reference* originalRefs;
        UInt32           originalCount;
        const Reference* patchedRefs;
        UInt32           patchedCount;
    };
    struct Location
    {
        UInt32 function, offset;
    };
    struct ImageBounds
    {
        UInt64 address;
        Range  text, segments[8];
        UInt32 segmentCount;
        bool   contains(UInt64 value, UInt32 size) const
        {
            for (UInt32 i = 0; i < segmentCount; ++i) {
                if (segments[i].contains(value, size)) { return true; }
            }
            return false;
        }
    };

    inline bool skipped(UInt32 index, const Reference* refs, UInt32 count)
    {
        for (UInt32 i = 0; i < count; ++i) {
            if (index >= refs[i].offset && index - refs[i].offset < 4) { return true; }
        }
        return false;
    }

    template<typename Patch>
    inline UInt8 expected(const Function& function, UInt32 index, UInt32 f, bool patched, const Patch* patches,
                          const Location* locations, UInt32 count)
    {
        if (patched) {
            for (UInt32 i = 0; i < count; ++i) {
                if (locations[i].function == f && index >= locations[i].offset
                    && index - locations[i].offset < patches[i].size)
                {
                    return patches[i].patched[index - locations[i].offset];
                }
            }
        }
        return function.original[index];
    }

    template<typename Patch>
    inline bool matches(Input& input, UInt64 address, const Function& function, UInt32 f, bool patched,
                        const Patch* patches, const Location* locations, UInt32 count)
    {
        const auto* refs     = patched ? function.patchedRefs : function.originalRefs;
        const auto  refCount = patched ? function.patchedCount : function.originalCount;
        return matchBytes(input, address, function.size,
                          [&](UInt32 index, UInt8 value)
                          {
                              return skipped(index, refs, refCount)
                                     || value == expected(function, index, f, patched, patches, locations, count);
                          });
    }

    template<typename Patch>
    inline bool find(Input& input, const Range& text, const Function& function, UInt32 f, const Patch* patches,
                     const Location* locations, UInt32 count, UInt64& address, bool& patched)
    {
        if (function.size < 32) { return false; }
        UInt8 prefixes[2][32];
        bool  masks[2][32];
        // 扫描热点只比较已准备的前缀，避免对每个文本字节重新遍历整份补丁表。
        for (UInt32 variant = 0; variant < 2; ++variant) {
            const auto* refs      = variant ? function.patchedRefs : function.originalRefs;
            const auto  countRefs = variant ? function.patchedCount : function.originalCount;
            for (UInt32 i = 0; i < 32; ++i) {
                prefixes[variant][i] = expected(function, i, f, variant != 0, patches, locations, count);
                masks[variant][i]    = skipped(i, refs, countRefs);
            }
        }
        const auto prefixMatches = [&](const UInt8* bytes)
        {
            for (UInt32 variant = 0; variant < 2; ++variant) {
                bool valid = true;
                for (UInt32 i = 0; i < 32; ++i) {
                    if (!masks[variant][i] && bytes[i] != prefixes[variant][i]) {
                        valid = false;
                        break;
                    }
                }
                if (valid) { return true; }
            }
            return false;
        };
        const auto candidateMatches = [&](UInt64 candidate)
        {
            for (UInt32 variant = 0; variant < 2; ++variant) {
                if (matches(input, candidate, function, f, variant != 0, patches, locations, count)) {
                    patched = variant != 0;
                    return true;
                }
            }
            return false;
        };
        return scanFunction(input, text, function.size, 32, prefixMatches, candidateMatches, address);
    }

    inline bool target(const Reference& ref, const ImageBounds* images, UInt32 imageCount, const Function* functions,
                       const UInt64* addresses, UInt32 functionCount, UInt64& address)
    {
        if (ref.image >= imageCount || ref.rva >= UserLimit - images[ref.image].address) { return false; }
        address = images[ref.image].address + ref.rva;
        // 已完整核验的目标函数也可搬迁；其他目标必须保持映像内参考 RVA。
        for (UInt32 i = 0; i < functionCount; ++i) {
            if (functions[i].image == ref.image && ref.rva >= functions[i].rva
                && ref.rva - functions[i].rva < functions[i].size)
            {
                address = addresses[i] + ref.rva - functions[i].rva;
                break;
            }
        }
        return images[ref.image].contains(address, 4);
    }

    inline bool binding(Input& input, UInt64 address, UInt32 kind, UInt64 expected)
    {
        if (kind == 2) {
            UInt8  stub[6];
            UInt64 slot;
            if (!input.read(address, stub, 6) || stub[0] != 0xFF || stub[1] != 0x25
                || !relativeTarget(address, 2, stub, slot))
            {
                return false;
            }
            address = slot;
        }
        UInt8 pointer[8];
        return (kind == 1 || kind == 2) && input.read(address, pointer, 8) && wide(pointer) == expected;
    }

    // 使用已匹配函数实际引用的 GOT / 调用桩位置，按绑定目标身份关联两份载荷。
    inline bool observedTarget(Input& input, const Reference* requested, UInt32 index, UInt32 f,
                               const Function* functions, const UInt64* addresses, const bool* variants,
                               UInt64& address)
    {
        const auto& ref     = requested[index];
        UInt32      ordinal = 0;
        for (UInt32 i = 0; i < index; ++i) {
            if (requested[i].image == ref.image && requested[i].rva == ref.rva && requested[i].indirect == ref.indirect)
            {
                ++ordinal;
            }
        }
        const auto& fn    = functions[f];
        const auto* refs  = variants[f] ? fn.patchedRefs : fn.originalRefs;
        const auto  count = variants[f] ? fn.patchedCount : fn.originalCount;
        for (UInt32 i = 0; i < count; ++i) {
            if (refs[i].image != ref.image || refs[i].rva != ref.rva || refs[i].indirect != ref.indirect) { continue; }
            if (ordinal) {
                --ordinal;
                continue;
            }
            UInt8 field[4];
            return input.read(addresses[f] + refs[i].offset, field, 4)
                   && relativeTarget(addresses[f] + refs[i].offset, 0, field, address);
        }
        return false;
    }

    template<typename Patch, size_t ImageCount, size_t FunctionCount, size_t PatchCount>
    inline bool resolve(Input& input, UInt64 base, const ImageReference (&imageRefs)[ImageCount],
                        const Function (&functions)[FunctionCount], const Patch* patches,
                        const Location (&locations)[PatchCount], Patch* output, UInt8* originals, UInt8* replacements,
                        UInt32 stride)
    {
        static_assert(ImageCount > 0 && ImageCount <= 16 && FunctionCount > 0 && FunctionCount <= 32);
        UInt64      addresses[FunctionCount]{};
        bool        variants[FunctionCount]{};
        ImageBounds images[ImageCount]{};
        input.imageRefs  = imageRefs;
        input.imageCount = ImageCount;
        input.tableRead  = false;
        memset(input.imageAddresses, 0, sizeof(input.imageAddresses));
        for (UInt32 i = 0; i < ImageCount; ++i) {
            Image image;
            if (!imageForCache(input, base, image, imageRefs[i].path, imageRefs[i].size) || image.segmentCount > 8) {
                return false;
            }
            images[i].address      = image.address;
            images[i].text         = image.text;
            images[i].segmentCount = image.segmentCount;
            memcpy(images[i].segments, image.segments, image.segmentCount * sizeof(Range));
        }
        for (UInt32 i = 0; i < FunctionCount; ++i) {
            if (functions[i].image >= ImageCount
                || !find(input, images[functions[i].image].text, functions[i], i, patches, locations, PatchCount,
                         addresses[i], variants[i]))
            {
                return false;
            }
            for (UInt32 j = 0; j < i; ++j) {
                if (Range{addresses[j], functions[j].size}.contains(addresses[i], 1)
                    || Range{addresses[i], functions[i].size}.contains(addresses[j], 1))
                {
                    return false;
                }
            }
        }
        for (UInt32 i = 0; i < FunctionCount; ++i) {
            const auto& fn    = functions[i];
            const auto* refs  = variants[i] ? fn.patchedRefs : fn.originalRefs;
            const auto  count = variants[i] ? fn.patchedCount : fn.originalCount;
            for (UInt32 r = 0; r < count; ++r) {
                UInt8  field[4];
                UInt64 actual, destination;
                if (refs[r].offset > fn.size || fn.size - refs[r].offset < 4
                    || !input.read(addresses[i] + refs[r].offset, field, 4)
                    || !relativeTarget(addresses[i] + refs[r].offset, 0, field, actual)
                    || !target(refs[r], images, ImageCount, functions, addresses, FunctionCount, destination)
                    || (refs[r].indirect ? !binding(input, actual, refs[r].indirect, destination) :
                                           actual != destination))
                {
                    return false;
                }
            }
        }
        for (UInt32 p = 0; p < PatchCount; ++p) {
            const auto loc = locations[p];
            if (loc.function >= FunctionCount || patches[p].size == 0 || patches[p].size > stride) { return false; }
            const auto& fn = functions[loc.function];
            if (loc.offset > fn.size || patches[p].size > fn.size - loc.offset) { return false; }
            const auto address = addresses[loc.function] + loc.offset;
            if (!input.cache.contains(address, patches[p].size)) { return false; }
            auto* original    = originals + p * stride;
            auto* replacement = replacements + p * stride;
            memcpy(original, patches[p].original, patches[p].size);
            memcpy(replacement, patches[p].patched, patches[p].size);
            for (UInt32 variant = 0; variant < 2; ++variant) {
                const auto* refs  = variant ? fn.patchedRefs : fn.originalRefs;
                const auto  count = variant ? fn.patchedCount : fn.originalCount;
                for (UInt32 r = 0; r < count; ++r) {
                    if (refs[r].offset + 4 <= loc.offset || refs[r].offset >= loc.offset + patches[p].size) {
                        continue;
                    }
                    UInt64 destination;
                    if (refs[r].offset < loc.offset || refs[r].offset + 4 > loc.offset + patches[p].size
                        || (refs[r].indirect ?
                                !observedTarget(input, refs, r, loc.function, functions, addresses, variants,
                                                destination) :
                                !target(refs[r], images, ImageCount, functions, addresses, FunctionCount, destination))
                        || !setRelative(variant ? replacement : original, refs[r].offset - loc.offset, address,
                                        destination))
                    {
                        return false;
                    }
                }
            }
            output[p] = {address - base, original, replacement, patches[p].size};
            for (UInt32 j = 0; j < p; ++j) {
                if (Range{base + output[j].offset, output[j].size}.contains(address, 1)
                    || Range{address, patches[p].size}.contains(base + output[j].offset, 1))
                {
                    return false;
                }
            }
        }
        return true;
    }

}    // namespace UserCacheResolver
