// 按驱动路径与完整函数内容解析兼容的共享缓存；仅生成计划，不写入进程。
#pragma once
#include <UserSurfaceSyncData.hpp>
#include <string.h>

namespace UserSurfaceSyncResolver
{

    using namespace UserSurfaceSyncData;
    using Reader                   = int (*)(UInt64, void*, size_t);
    constexpr UInt64   UserLimit   = 0x0000800000000000ULL;
    constexpr UInt32   MaxTextSize = 8 * 1024 * 1024;

    struct Range
    {
        UInt64 address{0}, size{0};
        bool   contains(UInt64 value, UInt64 length) const
        {
            return value >= address && value - address <= size && length <= size - (value - address);
        }
    };

    struct Plan
    {
        UserSurfaceSyncData::Patch patches[3]{};
        UInt8                      original[3][sizeof(Original0)]{};
        UInt8                      patched[3][sizeof(Original0)]{};
        UInt32                     reads{0};
    };

    struct Input
    {
        Reader reader;
        Range  cache;
        UInt32 reads{0}, bytes{0}, candidates{0};
        bool   read(UInt64 address, void* value, UInt32 size)
        {
            if (!cache.contains(address, size) || reads >= 32768 || size > 32 * 1024 * 1024 - bytes) { return false; }
            ++reads;
            bytes += size;
            return reader(address, value, size) == 0;
        }
    };

    inline UInt32 word(const UInt8* value)
    {
        UInt32 result;
        memcpy(&result, value, sizeof(result));
        return result;
    }

    inline UInt64 wide(const UInt8* value)
    {
        UInt64 result;
        memcpy(&result, value, sizeof(result));
        return result;
    }

    inline bool relativeTarget(UInt64 address, UInt32 offset, const UInt8* bytes, UInt64& target)
    {
        if (bytes == nullptr || address >= UserLimit - 4 || offset >= UserLimit - address - 4) { return false; }
        int displacement;
        memcpy(&displacement, bytes + offset, sizeof(displacement));
        const auto next = address + offset + 4;
        if (displacement < 0) {
            const auto magnitude = static_cast<UInt64>(-static_cast<long long>(displacement));
            if (next < magnitude) { return false; }
            target = next - magnitude;
        }
        else {
            if (next >= UserLimit || static_cast<UInt64>(displacement) >= UserLimit - next) { return false; }
            target = next + static_cast<UInt64>(displacement);
        }
        return true;
    }

    inline bool setRelative(UInt8* bytes, UInt32 offset, UInt64 address, UInt64 target)
    {
        if (bytes == nullptr || address >= UserLimit - 4 || offset >= UserLimit - address - 4) { return false; }
        const auto next = address + offset + 4;
        if (next >= UserLimit || target >= UserLimit) { return false; }
        const auto magnitude = target >= next ? target - next : next - target;
        if (magnitude > (target >= next ? 0x7FFFFFFFULL : 0x80000000ULL)) { return false; }
        const auto signedValue =
            target >= next ? static_cast<long long>(magnitude) : -static_cast<long long>(magnitude);
        const auto value = static_cast<int>(signedValue);
        memcpy(bytes + offset, &value, sizeof(value));
        return true;
    }

    struct Image
    {
        UInt64 address{0};
        Range  text;
        Range  constants[8]{};
        UInt32 constantCount{0};
        bool   constant(UInt64 value) const
        {
            for (UInt32 i = 0; i < constantCount; ++i) {
                if (constants[i].contains(value, 4)) { return true; }
            }
            return false;
        }
    };

    inline bool imageForCache(Input& input, UInt64 base, Image& image)
    {
        UInt8 header[456];
        if (!input.reader || base >= UserLimit - sizeof(header) || (base & 4095) != 0
            || input.reader(base, header, sizeof(header)) != 0 || memcmp(header, CacheMagic, 16) != 0)
        {
            return false;
        }
        // Darwin 25 的现代 image table；所有元数据都必须落在主缓存的首个只读映射内。
        const auto mappingOffset = word(header + 16), mappingCount = word(header + 20);
        const auto unslid = wide(header + 224), size = wide(header + 232), maxSlide = wide(header + 240);
        if (mappingOffset < sizeof(header) || mappingOffset > 65536 || mappingCount == 0 || mappingCount > 64
            || unslid == 0 || unslid > base || base - unslid > maxSlide || size == 0 || size > 0x1000000000ULL
            || size > UserLimit - base)
        {
            return false;
        }
        input.cache = {base, size};
        UInt8 mapping[32];
        if (!input.read(base + mappingOffset, mapping, sizeof(mapping)) || wide(mapping) != unslid
            || wide(mapping + 16) != 0 || (word(mapping + 28) & 1) == 0)
        {
            return false;
        }
        const auto metadataSize = wide(mapping + 8);
        if (metadataSize == 0 || metadataSize > size) { return false; }
        const Range metadata{base, metadataSize < 16 * 1024 * 1024 ? metadataSize : 16 * 1024 * 1024};
        const auto  imagesOffset = word(header + 448), imageCount = word(header + 452);
        if (imageCount == 0 || imageCount > 8192 || imagesOffset < sizeof(header)
            || !metadata.contains(base + imagesOffset, static_cast<UInt64>(imageCount) * 32))
        {
            return false;
        }
        const auto slide = base - unslid;
        UInt64     imageAddress{0};
        UInt8      entries[512];
        for (UInt32 i = 0; i < imageCount;) {
            const auto count = imageCount - i < 16 ? imageCount - i : 16;
            if (!input.read(base + imagesOffset + static_cast<UInt64>(i) * 32, entries, count * 32)) { return false; }
            for (UInt32 j = 0; j < count; ++j) {
                const auto* entry      = entries + j * 32;
                const auto  pathOffset = word(entry + 24);
                if (!metadata.contains(base + pathOffset, sizeof(DriverPath))) { return false; }
                char path[sizeof(DriverPath)];
                if (!input.read(base + pathOffset, path, sizeof(path))) { return false; }
                if (memcmp(path, DriverPath, sizeof(path)) != 0) { continue; }
                const auto address = wide(entry);
                if (imageAddress != 0 || address < unslid || address - unslid >= size) { return false; }
                imageAddress = address + slide;
            }
            i += count;
        }
        if (imageAddress == 0) { return false; }
        image.address = imageAddress;
        UInt8 mach[32];
        if (!input.read(imageAddress, mach, sizeof(mach)) || word(mach) != 0xFEEDFACF || word(mach + 4) != 0x01000007
            || (word(mach + 12) != 6 && word(mach + 12) != 8))
        {
            return false;
        }
        const auto commands = word(mach + 16), commandSize = word(mach + 20);
        if (commands == 0 || commands > 128 || commandSize > 65536
            || !input.cache.contains(imageAddress + 32, commandSize))
        {
            return false;
        }
        UInt32 cursor{0};
        for (UInt32 i = 0; i < commands; ++i) {
            UInt8 command[72];
            if (cursor > commandSize || commandSize - cursor < 8 || !input.read(imageAddress + 32 + cursor, command, 8))
            {
                return false;
            }
            const auto kind = word(command), length = word(command + 4);
            if (length < 8 || (length & 7) != 0 || length > commandSize - cursor) { return false; }
            if (kind == 0x19) {
                if (length < sizeof(command) || !input.read(imageAddress + 32 + cursor, command, sizeof(command))) {
                    return false;
                }
                const auto sections = word(command + 64);
                if (sections > 64 || length != 72 + sections * 80) { return false; }
                const auto segmentStart = wide(command + 24), segmentSize = wide(command + 32);
                if (segmentStart < unslid || segmentStart - unslid >= size
                    || segmentSize > size - (segmentStart - unslid))
                {
                    return false;
                }
                const Range segment{segmentStart + slide, segmentSize};
                for (UInt32 j = 0; j < sections; ++j) {
                    UInt8 section[80];
                    if (!input.read(imageAddress + 32 + cursor + 72 + j * 80, section, sizeof(section))) {
                        return false;
                    }
                    const auto address = wide(section + 32), sectionSize = wide(section + 40);
                    if (address < unslid || address - unslid >= size || !segment.contains(address + slide, sectionSize))
                    {
                        return false;
                    }
                    if (memcmp(section, "__text\0", 7) == 0 && (word(command + 60) & 5) == 5) {
                        if (image.text.size != 0 || sectionSize == 0 || sectionSize > MaxTextSize) { return false; }
                        image.text = {address + slide, sectionSize};
                    }
                    if (memcmp(section, "__const\0", 8) == 0 && (word(command + 60) & 1) != 0) {
                        if (image.constantCount == 8) { return false; }
                        image.constants[image.constantCount++] = {address + slide, sectionSize};
                    }
                }
            }
            cursor += length;
        }
        return cursor == commandSize && image.text.size != 0 && image.constantCount != 0;
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
        UInt8 bytes[256];
        for (UInt32 offset = 0; offset < size;) {
            const auto length = size - offset < sizeof(bytes) ? size - offset : static_cast<UInt32>(sizeof(bytes));
            if (!input.read(address + offset, bytes, length)) { return false; }
            for (UInt32 j = 0; j < length; ++j) {
                const auto index = offset + j;
                if (skipped(index, fields, fieldCount)
                    || (blend && index >= BlendGateInConstructor && index - BlendGateInConstructor < 2))
                {
                    continue;
                }
                if (bytes[j] != expected[index]) { return false; }
            }
            offset += length;
        }
        return true;
    }

    // 一次扫描只读 __text；候选数、读取次数与字节数有固定上限，重复匹配一律拒绝。
    inline bool findFunction(Input& input, const Range& text, const UInt8* expected, UInt32 size, UInt64& found,
                             bool blend = false, UInt64 hint = 0)
    {
        UInt8 bytes[1024];
        found = 0;
        if (size < 32 || size > text.size) { return false; }
        // 参考 RVA 只是候选位置；完整内容不符时才扫描，避免每个进程重复读取整段代码。
        if (text.contains(hint, size) && matches(input, hint, expected, size, nullptr, 0, blend)) {
            found = hint;
            return true;
        }
        for (UInt64 offset = 0; offset <= text.size - size;) {
            const auto remaining = text.size - offset;
            const auto length =
                remaining < sizeof(bytes) ? static_cast<UInt32>(remaining) : static_cast<UInt32>(sizeof(bytes));
            if (!input.read(text.address + offset, bytes, length)) { return false; }
            const auto positions = length - 31;
            for (UInt32 j = 0; j < positions && offset + j <= text.size - size; ++j) {
                if (memcmp(bytes + j, expected, 32) != 0) { continue; }
                if (++input.candidates > 128) { return false; }
                const auto address = text.address + offset + j;
                if (!matches(input, address, expected, size, nullptr, 0, blend)) { continue; }
                if (found != 0) { return false; }
                found = address;
            }
            offset += positions;
        }
        return found != 0;
    }

    inline bool resolve(Reader reader, UInt64 base, const DevicePatchSet& device, bool shared, bool blend, Plan& plan)
    {
        Input input{reader, {}};
        Image image;
        if (!imageForCache(input, base, image)) { return false; }
        // 完整原生配置函数与 scratch 必须保持原样；未知缓存不推广 scratch 实验。
        UInt64 settings{0}, scratch{0}, constructor{0};
        if (!findFunction(input, image.text, NativeOverride, sizeof(NativeOverride), settings, false,
                          image.address + NativeOverrideRVA)
            || !findFunction(input, image.text, ScratchOriginal, sizeof(ScratchOriginal), scratch, false,
                             image.address + ScratchRVA)
            || !findFunction(input, image.text, BlendConstructor, sizeof(BlendConstructor), constructor, true,
                             image.address + ConstructorRVA))
        {
            return false;
        }
        const UInt8*         variants[] = {Original0, device.immediate[0].patched, device.shared[0].patched};
        const RelativeField* links[]    = {OriginalInitLinks, ImmediateInitLinks, SharedInitLinks};
        UInt64               init{0}, targets[3]{};
        UInt8                scan[1024];
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
        const bool referenceInit = matchInitAt(image.address + InitRVA, targets);
        if (referenceInit) { init = image.address + InitRVA; }
        for (UInt64 offset = 0; !referenceInit && offset <= image.text.size - sizeof(Original0);) {
            const auto remaining = image.text.size - offset;
            const auto length =
                remaining < sizeof(scan) ? static_cast<UInt32>(remaining) : static_cast<UInt32>(sizeof(scan));
            if (!input.read(image.text.address + offset, scan, length)) { return false; }
            const auto positions = length - 31;
            for (UInt32 j = 0; j < positions && offset + j <= image.text.size - sizeof(Original0); ++j) {
                for (UInt32 variant = 0; variant < 3; ++variant) {
                    if (memcmp(scan + j, variants[variant], 32) != 0) { continue; }
                    if (++input.candidates > 128) { return false; }
                    const auto address = image.text.address + offset + j;
                    UInt64     candidateTargets[3]{};
                    if (!matchInitAt(address, candidateTargets)) { continue; }
                    if (init != 0) { return false; }
                    init = address;
                    memcpy(targets, candidateTargets, sizeof(targets));
                    break;
                }
            }
            offset += positions;
        }
        if (init == 0) { return false; }
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
        const bool referenceOverride = matchOverrideAt(image.address + OverrideRVA);
        if (referenceOverride) { overrideAddress = image.address + OverrideRVA; }
        for (UInt64 offset = 0; !referenceOverride && offset <= image.text.size - sizeof(Original1);) {
            const auto remaining = image.text.size - offset;
            const auto length =
                remaining < sizeof(scan) ? static_cast<UInt32>(remaining) : static_cast<UInt32>(sizeof(scan));
            if (!input.read(image.text.address + offset, scan, length)) { return false; }
            const auto positions = length - 9;
            for (UInt32 j = 0; j < positions && offset + j <= image.text.size - sizeof(Original1); ++j) {
                const bool original = memcmp(scan + j, Original1, 6) == 0;
                const bool patched  = scan[j] == 0xE9 && memcmp(scan + j + 5, Patched1 + 5, 5) == 0;
                if (!original && !patched) { continue; }
                const auto address = image.text.address + offset + j;
                if (!matchOverrideAt(address)) { continue; }
                if (overrideAddress != 0) { return false; }
                overrideAddress = address;
            }
            offset += positions;
        }
        if (overrideAddress == 0) { return false; }
        const auto* source       = blend ? device.blend : (shared ? device.shared : device.immediate);
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
