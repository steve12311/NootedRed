// Vega 核显的视频能力；VCN1 不能复用当前 VCN2 引擎接入。
#pragma once
#include <IOKit/IOTypes.h>

namespace VCNCapabilities
{

    enum class Generation : UInt32
    {
        VCN1  = 0x0100,
        VCN22 = 0x0202
    };
    struct Capability
    {
        UInt32     deviceID;
        Generation generation;
        UInt32     vaFamily;
    };

    // vaFamily 是 VA 驱动的解码能力分类，不能当作 GPU 的 GFX/AddrLib 家族。
    // Raven/Picasso 保留分类，但尚未实现 VCN1 桥接；Renoir 与 Cezanne 共用 VCN 2.2 接入。
    inline constexpr Capability Devices[] = {
        {0x15D8, Generation::VCN1, 0},  {0x15DD, Generation::VCN1, 0},  {0x15E7, Generation::VCN22, 5},
        {0x1636, Generation::VCN22, 5}, {0x1638, Generation::VCN22, 5}, {0x164C, Generation::VCN22, 5},
    };

    inline constexpr const Capability* findDevice(UInt32 deviceID)
    {
        for (const auto& capability : Devices) {
            if (capability.deviceID == deviceID) { return &capability; }
        }
        return nullptr;
    }

    // 3=未知设备，4=未核实内核布局，5=未实现的视频代际；0 才允许进入驱动核验。
    inline constexpr UInt32 rejection(UInt32 deviceID, UInt32 major, UInt32 minor)
    {
        const auto* capability = findDevice(deviceID);
        if (!capability) { return 3; }
        if (capability->generation != Generation::VCN22 || capability->vaFamily == 0) { return 5; }
        if (major != 25 || minor != 6) { return 4; }
        return 0;
    }

}    // namespace VCNCapabilities
