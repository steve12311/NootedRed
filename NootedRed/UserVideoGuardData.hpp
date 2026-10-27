// VCN2 私有守卫页的绑定与入口约束；实际地址只在完整缓存核验后写入。
#pragma once
#include <UserCacheResolver.hpp>
#include <UserVideoDecodeData.hpp>

namespace UserVideoGuardData
{
    constexpr UInt32 Revision = 1;
    constexpr UInt32 HeaderSize = 32;
    constexpr UInt32 PageSize = 4096;
    constexpr UInt32 MaxEntries = 4;
    constexpr UInt32 MaxFunctions = 5;
    constexpr UInt32 EntrySize = 32;
    constexpr UInt32 IdentitySize = 64;
    struct Entry { UInt32 function, size, offset; };
    struct Binding { UInt32 offset, function, value; bool reference; };
}
