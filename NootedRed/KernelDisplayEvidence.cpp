// DVPF：只读内核显示证据。所有热路径只做原子计数，属性发布由冷路径/状态变化触发。
#include <KernelDisplayEvidence.hpp>
#include <NRed.hpp>
#include <string.h>

namespace
{

    constexpr UInt32 MaxDisplays    = 4;
    constexpr UInt32 KextCount      = 2;
    constexpr UInt32 MachMagic      = 0xFEEDFACF;
    constexpr UInt32 CpuX86_64      = 0x01000007;
    constexpr UInt32 MaxCommands    = 128;
    constexpr UInt32 MaxCommandSize = 64 * 1024;
    constexpr UInt64 MaxKextSize    = 64ULL * 1024 * 1024;

    volatile UInt32 gKextValid{0}, gKextError{0};
    volatile UInt32 gKextSize[KextCount]{};
    volatile UInt32 gKextUUID[KextCount][4]{};
    volatile UInt32 gKextSource[KextCount][2]{};
    volatile UInt32 gGFX9DeviceID{0}, gGFX9Revisions{0}, gGFX9Attributes{0}, gGFX9FbOffset[2]{};
    volatile UInt32 gFramebufferDarwin{0}, gFramebufferFlags{0};
    volatile UInt32 gComputeVTable{0};
    volatile UInt32 gScanoutFormat[MaxDisplays]{}, gScanoutSwizzle[MaxDisplays]{}, gScanoutChanges{0};
    volatile UInt32 gFlipFormat[MaxDisplays]{}, gFlipPitch[MaxDisplays]{}, gFlipRotation[MaxDisplays]{};
    volatile UInt32 gFlipWaitCount{0}, gFlipWaitMax{0}, gFlipWaitTotal{0};

    const char* const KextSizeNames[KextCount]    = {"NRedKernelX5000KextSize", "NRedKernelX6000FBKextSize"};
    const char* const KextUUIDNames[KextCount][4] = {
        {"NRedKernelX5000KextUUID0", "NRedKernelX5000KextUUID1", "NRedKernelX5000KextUUID2",
         "NRedKernelX5000KextUUID3"},
        {"NRedKernelX6000FBKextUUID0", "NRedKernelX6000FBKextUUID1", "NRedKernelX6000FBKextUUID2",
         "NRedKernelX6000FBKextUUID3"},
    };
    const char* const KextSourceNames[KextCount][2] = {
        {"NRedKernelX5000SourceVersionLow", "NRedKernelX5000SourceVersionHigh"},
        {"NRedKernelX6000FBSourceVersionLow", "NRedKernelX6000FBSourceVersionHigh"},
    };
    const char* const ScanoutFormatNames[MaxDisplays]  = {"NRedKernelScanoutFormat0", "NRedKernelScanoutFormat1",
                                                          "NRedKernelScanoutFormat2", "NRedKernelScanoutFormat3"};
    const char* const ScanoutSwizzleNames[MaxDisplays] = {"NRedKernelScanoutSwizzle0", "NRedKernelScanoutSwizzle1",
                                                          "NRedKernelScanoutSwizzle2", "NRedKernelScanoutSwizzle3"};
    const char* const FlipFormatNames[MaxDisplays]     = {"NRedKernelFlipFormat0", "NRedKernelFlipFormat1",
                                                          "NRedKernelFlipFormat2", "NRedKernelFlipFormat3"};
    const char* const FlipPitchNames[MaxDisplays]      = {"NRedKernelFlipPitch0", "NRedKernelFlipPitch1",
                                                          "NRedKernelFlipPitch2", "NRedKernelFlipPitch3"};
    const char* const FlipRotationNames[MaxDisplays]   = {"NRedKernelFlipRotation0", "NRedKernelFlipRotation1",
                                                          "NRedKernelFlipRotation2", "NRedKernelFlipRotation3"};

    UInt32 load(volatile UInt32& value) { return __atomic_load_n(&value, __ATOMIC_RELAXED); }
    void   store(volatile UInt32& value, const UInt32 next) { __atomic_store_n(&value, next, __ATOMIC_RELAXED); }
    void   fetchOr(volatile UInt32& value, const UInt32 bits) { __atomic_fetch_or(&value, bits, __ATOMIC_RELAXED); }
    void   fetchAnd(volatile UInt32& value, const UInt32 bits) { __atomic_fetch_and(&value, bits, __ATOMIC_RELAXED); }
    void fetchAdd(volatile UInt32& value, const UInt32 amount) { __atomic_fetch_add(&value, amount, __ATOMIC_RELAXED); }

    UInt32 word(const UInt8* bytes)
    {
        UInt32 value;
        memcpy(&value, bytes, sizeof(value));
        return value;
    }

    UInt64 wide(const UInt8* bytes)
    {
        UInt64 value;
        memcpy(&value, bytes, sizeof(value));
        return value;
    }

    bool captureMachO(const UInt64 address, const UInt64 size, UInt32& uuid0, UInt32& uuid1, UInt32& uuid2,
                      UInt32& uuid3, UInt64& sourceVersion)
    {
        if (address == 0 || size < 32 || size > MaxKextSize) { return false; }
        const auto* base = reinterpret_cast<const UInt8*>(address);
        UInt8       header[32];
        memcpy(header, base, sizeof(header));
        if (word(header) != MachMagic || word(header + 4) != CpuX86_64) { return false; }
        const auto commands    = word(header + 16);
        const auto commandSize = word(header + 20);
        if (commands == 0 || commands > MaxCommands || commandSize > MaxCommandSize || commandSize > size - 32) {
            return false;
        }
        bool   foundUUID = false, foundSource = false;
        UInt32 cursor = 0;
        for (UInt32 i = 0; i < commands; ++i) {
            UInt8 command[8];
            if (cursor > commandSize || commandSize - cursor < sizeof(command)) { return false; }
            memcpy(command, base + 32 + cursor, sizeof(command));
            const auto kind   = word(command);
            const auto length = word(command + 4);
            if (length < sizeof(command) || length > commandSize - cursor) { return false; }
            if (kind == 0x1B && length >= 24 && !foundUUID) {
                UInt8 uuid[16];
                memcpy(uuid, base + 32 + cursor + 8, sizeof(uuid));
                uuid0     = word(uuid);
                uuid1     = word(uuid + 4);
                uuid2     = word(uuid + 8);
                uuid3     = word(uuid + 12);
                foundUUID = true;
            }
            else if (kind == 0x2A && length >= 16 && !foundSource) {
                UInt8 version[8];
                memcpy(version, base + 32 + cursor + 8, sizeof(version));
                sourceVersion = wide(version);
                foundSource   = true;
            }
            cursor += length;
        }
        return cursor == commandSize && (foundUUID || foundSource);
    }

}    // namespace

namespace KernelDisplayEvidence
{

    void captureKext(const Kext kext, const UInt64 address, const UInt64 size)
    {
        const auto index = static_cast<UInt32>(kext);
        if (index >= KextCount) { return; }
        UInt32     uuid0 = 0, uuid1 = 0, uuid2 = 0, uuid3 = 0;
        UInt64     source = 0;
        const bool valid  = captureMachO(address, size, uuid0, uuid1, uuid2, uuid3, source);
        if (!valid) {
            fetchAnd(gKextValid, ~(1U << index));
            fetchOr(gKextError, 1U << index);
            return;
        }
        store(gKextSize[index], static_cast<UInt32>(size > 0xFFFFFFFFULL ? 0xFFFFFFFFULL : size));
        store(gKextUUID[index][0], uuid0);
        store(gKextUUID[index][1], uuid1);
        store(gKextUUID[index][2], uuid2);
        store(gKextUUID[index][3], uuid3);
        store(gKextSource[index][0], static_cast<UInt32>(source));
        store(gKextSource[index][1], static_cast<UInt32>(source >> 32));
        fetchOr(gKextValid, 1U << index);
        fetchAnd(gKextError, ~(1U << index));
    }

    void recordGFX9(const UInt32 deviceID, const UInt8 pciRevision, const UInt16 devRevision, const UInt16 enumRevision,
                    const UInt8 attributes, const UInt64 fbOffset)
    {
        store(gGFX9DeviceID, deviceID);
        store(gGFX9Revisions, static_cast<UInt32>(pciRevision) | (static_cast<UInt32>(devRevision) << 8)
                                  | (static_cast<UInt32>(enumRevision) << 16));
        store(gGFX9Attributes, attributes);
        store(gGFX9FbOffset[0], static_cast<UInt32>(fbOffset));
        store(gGFX9FbOffset[1], static_cast<UInt32>(fbOffset >> 32));
    }

    void recordFramebuffer(const UInt32 darwinMajor, const bool renoir, const bool picasso, const bool renoirE)
    {
        store(gFramebufferDarwin, darwinMajor);
        store(gFramebufferFlags, (renoir ? 1U : 0U) | (picasso ? 2U : 0U) | (renoirE ? 4U : 0U));
    }

    void recordComputeVTable(const bool matched) { store(gComputeVTable, matched ? 1U : 0U); }

    bool recordScanout(const UInt32 fbIndex, const UInt32 bitsPerPixel, const UInt32 bitsPerComponent,
                       const UInt32 hwPixelFormat, const UInt32 swizzleMode, const UInt32 rotation)
    {
        if (fbIndex >= MaxDisplays) { return false; }
        const auto format = (bitsPerPixel & 0xFFU) | ((bitsPerComponent & 0x3FU) << 8) | ((hwPixelFormat & 0xFFU) << 16)
                            | ((rotation & 0xFFU) << 24);
        if (load(gScanoutFormat[fbIndex]) == format && load(gScanoutSwizzle[fbIndex]) == swizzleMode) { return false; }
        store(gScanoutFormat[fbIndex], format);
        store(gScanoutSwizzle[fbIndex], swizzleMode);
        fetchAdd(gScanoutChanges, 1);
        return true;
    }

    bool recordFlipParameters(const UInt32 fbIndex, const UInt32 format, const UInt32 pitch, const UInt32 rotation)
    {
        if (fbIndex >= MaxDisplays) { return false; }
        const bool changed = load(gFlipFormat[fbIndex]) != format || load(gFlipPitch[fbIndex]) != pitch
                             || load(gFlipRotation[fbIndex]) != rotation;
        if (!changed) { return false; }
        store(gFlipFormat[fbIndex], format);
        store(gFlipPitch[fbIndex], pitch);
        store(gFlipRotation[fbIndex], rotation);
        return true;
    }

    bool recordFlipWait(const UInt32 fbIndex, const UInt32 iterations)
    {
        if (fbIndex >= MaxDisplays) { return false; }
        fetchAdd(gFlipWaitCount, 1);
        fetchAdd(gFlipWaitTotal, iterations);
        UInt32 previous = load(gFlipWaitMax);
        while (iterations > previous) {
            if (__atomic_compare_exchange_n(&gFlipWaitMax, &previous, iterations, false, __ATOMIC_RELAXED,
                                            __ATOMIC_RELAXED))
            {
                break;
            }
        }
        return iterations >= 10 && iterations > previous;
    }

    void publish()
    {
        auto& nred = NRed::singleton();
        nred.setProp32("NRedKernelDisplayEvidenceRevision", 1);
        nred.setProp32("NRedKernelGFX9DeviceID", load(gGFX9DeviceID));
        nred.setProp32("NRedKernelGFX9Revisions", load(gGFX9Revisions));
        nred.setProp32("NRedKernelGFX9Attributes", load(gGFX9Attributes));
        nred.setProp32("NRedKernelGFX9FbOffsetLow", load(gGFX9FbOffset[0]));
        nred.setProp32("NRedKernelGFX9FbOffsetHigh", load(gGFX9FbOffset[1]));
        nred.setProp32("NRedKernelFramebufferDarwin", load(gFramebufferDarwin));
        nred.setProp32("NRedKernelFramebufferFlags", load(gFramebufferFlags));
        nred.setProp32("NRedKernelComputeVTable", load(gComputeVTable));
        nred.setProp32("NRedKernelKextValid", load(gKextValid));
        nred.setProp32("NRedKernelKextError", load(gKextError));
        for (UInt32 i = 0; i < KextCount; ++i) {
            nred.setProp32(KextSizeNames[i], load(gKextSize[i]));
            for (UInt32 wordIndex = 0; wordIndex < 4; ++wordIndex) {
                nred.setProp32(KextUUIDNames[i][wordIndex], load(gKextUUID[i][wordIndex]));
            }
            nred.setProp32(KextSourceNames[i][0], load(gKextSource[i][0]));
            nred.setProp32(KextSourceNames[i][1], load(gKextSource[i][1]));
        }
        for (UInt32 i = 0; i < MaxDisplays; ++i) {
            nred.setProp32(ScanoutFormatNames[i], load(gScanoutFormat[i]));
            nred.setProp32(ScanoutSwizzleNames[i], load(gScanoutSwizzle[i]));
            nred.setProp32(FlipFormatNames[i], load(gFlipFormat[i]));
            nred.setProp32(FlipPitchNames[i], load(gFlipPitch[i]));
            nred.setProp32(FlipRotationNames[i], load(gFlipRotation[i]));
        }
        nred.setProp32("NRedKernelScanoutChanges", load(gScanoutChanges));
        nred.setProp32("NRedKernelFlipWaitCount", load(gFlipWaitCount));
        nred.setProp32("NRedKernelFlipWaitMax", load(gFlipWaitMax));
        nred.setProp32("NRedKernelFlipWaitTotal", load(gFlipWaitTotal));
    }

#if defined(KERNEL_DISPLAY_EVIDENCE_HOST)
    void resetForTest()
    {
        store(gKextValid, 0);
        store(gKextError, 0);
        for (UInt32 i = 0; i < KextCount; ++i) {
            store(gKextSize[i], 0);
            for (UInt32 j = 0; j < 4; ++j) { store(gKextUUID[i][j], 0); }
            store(gKextSource[i][0], 0);
            store(gKextSource[i][1], 0);
        }
        store(gGFX9DeviceID, 0);
        store(gGFX9Revisions, 0);
        store(gGFX9Attributes, 0);
        store(gGFX9FbOffset[0], 0);
        store(gGFX9FbOffset[1], 0);
        store(gFramebufferDarwin, 0);
        store(gFramebufferFlags, 0);
        store(gComputeVTable, 0);
        store(gScanoutChanges, 0);
        store(gFlipWaitCount, 0);
        store(gFlipWaitMax, 0);
        store(gFlipWaitTotal, 0);
        for (UInt32 i = 0; i < MaxDisplays; ++i) {
            store(gScanoutFormat[i], 0);
            store(gScanoutSwizzle[i], 0);
            store(gFlipFormat[i], 0);
            store(gFlipPitch[i], 0);
            store(gFlipRotation[i], 0);
        }
    }
#endif

}    // namespace KernelDisplayEvidence
