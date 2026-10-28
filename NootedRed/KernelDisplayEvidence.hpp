// DVPF：内核 GFX9 / Framebuffer / DCN 只读证据；不改变显示行为。
#pragma once
#include <IOKit/IOTypes.h>

namespace KernelDisplayEvidence
{

    enum class Kext : UInt8
    {
        X5000,
        X6000FB,
    };

    // 只读解析 kext Mach-O 头；地址/大小来自 processKext，不写 kext 内存。
    void captureKext(Kext kext, UInt64 address, UInt64 size);
    void recordGFX9(UInt32 deviceID, UInt8 pciRevision, UInt16 devRevision, UInt16 enumRevision, UInt8 attributes,
                    UInt64 fbOffset);
    void recordFramebuffer(UInt32 darwinMajor, bool renoir, bool picasso, bool renoirE);
    void recordComputeVTable(bool matched);

    // 返回 true 表示状态发生变化；调用方只在变化时发布属性，避免逐帧 IORegistry 写入。
    bool recordScanout(UInt32 fbIndex, UInt32 bitsPerPixel, UInt32 bitsPerComponent, UInt32 hwPixelFormat,
                       UInt32 swizzleMode, UInt32 rotation);
    bool recordFlipParameters(UInt32 fbIndex, UInt32 format, UInt32 pitch, UInt32 rotation);
    bool recordFlipWait(UInt32 fbIndex, UInt32 iterations);
    void publish();

#if defined(KERNEL_DISPLAY_EVIDENCE_HOST)
    void resetForTest();
#endif

}    // namespace KernelDisplayEvidence
