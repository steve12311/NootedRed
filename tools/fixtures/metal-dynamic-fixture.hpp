// 完整的重排 Metal 缓存，用于生产 Hook 的事务故障注入。
#include <algorithm>
namespace MetalFixture
{

#include "user-cache-dynamic.hpp"
    static UserSurfaceSyncResolver::Plan         plan;
    static constexpr UserCacheResolver::Function functions[] = {
        UserSurfaceSyncData::ScratchResolverData::ResolverFunctions[0],
        UserSurfaceConstructorData::Submit::ResolverFunctions[0],
    };
    static void setup(unsigned device, bool shared, bool blend, bool scratch)
    {
        using namespace UserSurfaceSyncData;
        cache(ScratchResolverData::ResolverImages, functions, true);
        const auto image = imageAddresses[0];
        const auto init = image + 0x21000, overrideAddress = image + 0x21100;
        const auto constructor = image + 0x28000, settings = image + 0x26000, dump = image + 0x27000;
        put(init, Original0, sizeof(Original0));
        put(overrideAddress, Original1, sizeof(Original1));
        put(constructor, BlendConstructor, sizeof(BlendConstructor));
        put(settings, NativeOverride, sizeof(NativeOverride));
        put(dump, NativeDump, sizeof(NativeDump));
        word32(image + 0x71064, 0x40400000);
        const uint64_t targets[] = {image + 0x71064, settings, dump};
        for (auto link : OriginalInitLinks) { rel(init, link.offset, targets[link.target]); }
        rel(overrideAddress, 6, settings);
        UserSurfaceSyncResolver::Plan extra;
        if (!scratch) {
            assert(UserSurfaceSyncResolver::resolve(reader, base, *findDevice(device), true, true, extra, true));
            put(functionAddresses[0], ScratchOriginal, sizeof(ScratchOriginal));
        }
        plan = {};
        assert(UserSurfaceSyncResolver::resolve(reader, base, *findDevice(device), shared, blend, plan, scratch));
        // 非 Scratch 策略也保留第四个站点，供残留策略拒绝测试使用。
        if (!scratch) {
            plan.patches[3] = extra.patches[3];
            memcpy(plan.original[3], ScratchOriginal, sizeof(ScratchOriginal));
            memcpy(plan.patched[3], extra.patched[3], sizeof(plan.patched[3]));
            plan.patches[3].original = plan.original[3];
            plan.patches[3].patched  = plan.patched[3];
        }
    }

    template<size_t Images, size_t Functions>
    static void setupProfile(unsigned device, unsigned major,
                             const UserCacheResolver::ImageReference (&images)[Images],
                             const UserCacheResolver::Function (&functions)[Functions])
    {
        cache(images, functions);
        const auto constant = destination(functions[0].originalRefs[0], functions, Functions);
        blocks[constant].resize(16);
        word32(constant, 0x40400000);
        put(destination(functions[0].originalRefs[2], functions, Functions), UserSurfaceSyncData::NativeDump,
            sizeof(UserSurfaceSyncData::NativeDump));
        plan = {};
        plan.input = {reader, {}};
        plan.input.scanBuffer = plan.scan;
        assert(UserSurfaceSyncResolver::resolveMetal(plan.input, base, device, plan, major));
    }

    static void setupOS(unsigned device, unsigned major)
    {
        if (major == 25) { setup(device, true, true, true); return; }
        static constexpr UserCacheResolver::Function sonoma[] = {
            UserSonomaSurfaceData::ResolverFunctions[0], UserSonomaSurfaceData::ResolverFunctions[1],
            UserSonomaSurfaceData::ResolverFunctions[2], UserSonomaSurfaceData::ResolverFunctions[3],
            UserSonomaSurfaceData::ResolverFunctions[4], UserSonomaSurfaceData::Submit::ResolverFunctions[0],
        };
        static constexpr UserCacheResolver::Function sequoia[] = {
            UserSequoiaSurfaceData::ResolverFunctions[0], UserSequoiaSurfaceData::ResolverFunctions[1],
            UserSequoiaSurfaceData::ResolverFunctions[2], UserSequoiaSurfaceData::ResolverFunctions[3],
            UserSequoiaSurfaceData::ResolverFunctions[4], UserSequoiaSurfaceData::Submit::ResolverFunctions[0],
        };
        if (major == 23) { setupProfile(device, major, UserSonomaSurfaceData::ResolverImages, sonoma); }
        else { assert(major == 24); setupProfile(device, major, UserSequoiaSurfaceData::ResolverImages, sequoia); }
    }

}    // namespace MetalFixture
