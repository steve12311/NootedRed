// 用既有 Video Resolver 生成只读补丁计划；只读合成夹具，不访问系统缓存。
// 编译时把旧提交的 UserVideoDecodeResolver.hpp 放到 -I 搜索路径首位；生成的补丁数据头来自当前树。
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>

static std::map<std::string, unsigned> properties;
#include "UserSharedCache.hpp"
#include "UserCacheResolver.hpp"
#include "UserCacheSectionData.hpp"
#include "user-cache-dynamic.hpp"
#include "UserSequoiaVideoData.hpp"
#include "UserVideoDecodeData.hpp"
#include "UserVideoDecodeResolver.hpp"

struct VideoDarwin24
{
    static inline constexpr auto& devices   = UserSequoiaVideoData::DevicePatches;
    static inline constexpr auto& images    = UserSequoiaVideoData::ResolverImages;
    static inline constexpr auto& functions = UserSequoiaVideoData::ResolverFunctions;
};

struct VideoDarwin25
{
    static inline constexpr auto& devices   = UserVideoDecodeData::DevicePatches;
    static inline constexpr auto& images    = UserVideoDecodeData::ResolverImages;
    static inline constexpr auto& functions = UserVideoDecodeData::ResolverFunctions;
};

static bool first = true;

static void hex(const UInt8* bytes, UInt32 size)
{
    std::putchar('"');
    for (UInt32 i = 0; i < size; ++i) { std::printf("%02X", bytes[i]); }
    std::putchar('"');
}

template<typename Patch>
static void emit(const char* feature, const char* os, UInt32 device, UInt64 baseAddress, UInt32 reads,
                 const Patch* patches, UInt32 count)
{
    if (!first) { std::putchar(','); }
    first = false;
    std::printf("{\"feature\":\"%s\",\"os\":\"%s\",\"deviceID\":%u,\"base\":\"0x%016llX\",\"reads\":%u,\"sites\":[",
                feature, os, device, static_cast<unsigned long long>(baseAddress), reads);
    for (UInt32 i = 0; i < count; ++i) {
        const auto& patch = patches[i];
        if (i) { std::putchar(','); }
        std::printf("{\"offset\":\"0x%016llX\",\"size\":%u,\"original\":", static_cast<unsigned long long>(patch.offset),
                    patch.size);
        hex(patch.original, patch.size);
        std::printf(",\"replacement\":");
        hex(patch.patched, patch.size);
        std::putchar('}');
    }
    std::printf("]}");
}

template<typename Data>
static void video(const char* os, bool sequoia)
{
    for (const auto& device : Data::devices) {
        cache(Data::images, Data::functions, true);
        UserSharedCache::Input input{reader, {}};
        UInt8                    scan[UserSharedCache::Input::ScanSize];
        input.scanBuffer = scan;
        UserVideoDecodeResolver::Plan plan;
        assert(UserVideoDecodeResolver::resolve(input, base, device.patches, plan, sequoia));
        emit("Vcn2Decode", os, device.deviceID, base, plan.reads, plan.patches,
             sizeof(plan.patches) / sizeof(plan.patches[0]));
    }
}

int main()
{
    std::printf("{\"schemaVersion\":1,\"kind\":\"vpf.video-legacy\",\"plans\":[");
    video<VideoDarwin24>("Darwin24", true);
    video<VideoDarwin25>("Darwin25", false);
    std::printf("]}\n");
    return 0;
}
