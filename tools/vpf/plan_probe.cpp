// VPF 计划探针：在合成缓存上同时运行旧 Resolver 与 VerifiedPatchPlan 适配器。
// 默认模式逐字节自检；--json 输出规范化计划供 tools/vpf/compare_plan.py 比较。
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>

#include <VPF/PatchManifestData.hpp>
#include <VPF/ResolverAdapters.hpp>

static std::map<std::string, unsigned> properties;
#include "user-cache-dynamic.hpp"

// 生成数据以 namespace 组织，不能直接作为模板参数；用轻量 traits 暴露数组。
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

struct GuardDarwin24
{
    static inline constexpr auto& images         = UserVideoGuard24Data::ResolverImages;
    static inline constexpr auto& functions      = UserVideoGuard24Data::ResolverFunctions;
    static inline constexpr auto& sectionTargets = UserVideoGuard24Data::SectionTargets;
};

struct GuardDarwin25
{
    static inline constexpr auto& images         = UserVideoGuard25Data::ResolverImages;
    static inline constexpr auto& functions      = UserVideoGuard25Data::ResolverFunctions;
    static inline constexpr auto& sectionTargets = UserVideoGuard25Data::SectionTargets;
};

using VPF::FeatureManifest;
using VPF::PlanState;
using VPF::VerifiedPatchPlan;

static unsigned failures = 0;
static bool     jsonMode = false;
static bool     firstPlan = true;
static unsigned emittedPlans = 0;

static void check(const bool condition, const char* label)
{
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", label);
        ++failures;
    }
}

static const char* stateName(const PlanState state)
{
    switch (state) {
        case PlanState::AllOriginal: return "AllOriginal";
        case PlanState::AllPatched: return "AllPatched";
        case PlanState::Mixed: return "Mixed";
        default: return "Unknown";
    }
}

static void printString(const char* value)
{
    std::putchar('"');
    for (const char* cursor = value; cursor && *cursor; ++cursor) {
        if (*cursor == '"' || *cursor == '\\') { std::putchar('\\'); }
        std::putchar(*cursor);
    }
    std::putchar('"');
}

static void printHex(const UInt8* bytes, const UInt32 size)
{
    std::putchar('"');
    for (UInt32 i = 0; i < size; ++i) { std::printf("%02X", bytes[i]); }
    std::putchar('"');
}

static void emitPlan(const char* feature, const char* os, const UInt32 device, const PlanState state,
                     const VerifiedPatchPlan& plan)
{
    ++emittedPlans;
    if (!jsonMode) { return; }
    if (!firstPlan) { std::putchar(','); }
    firstPlan = false;
    std::printf("{\"feature\":\"%s\",\"os\":\"%s\",\"deviceID\":%u,\"state\":\"%s\","
                "\"base\":\"0x%016llX\",\"reads\":%u,\"images\":[",
                feature, os, device, stateName(state), static_cast<unsigned long long>(plan.base()), plan.reads());
    for (UInt32 i = 0; i < plan.imageCount(); ++i) {
        const auto& image = plan.image(i);
        if (i) { std::putchar(','); }
        std::printf("{\"path\":");
        printString(image.path);
        std::printf(",\"address\":\"0x%016llX\",\"textAddress\":\"0x%016llX\",\"textSize\":%llu,\"segments\":[",
                    static_cast<unsigned long long>(image.address),
                    static_cast<unsigned long long>(image.text.address),
                    static_cast<unsigned long long>(image.text.size));
        for (UInt32 j = 0; j < image.segmentCount; ++j) {
            if (j) { std::putchar(','); }
            std::printf("[\"0x%016llX\",%llu]", static_cast<unsigned long long>(image.segments[j].address),
                        static_cast<unsigned long long>(image.segments[j].size));
        }
        std::printf("]}");
    }
    std::printf("],\"sites\":[");
    for (UInt32 i = 0; i < plan.siteCount(); ++i) {
        const auto& site = plan.site(i);
        if (i) { std::putchar(','); }
        std::printf("{\"offset\":\"0x%016llX\",\"address\":\"0x%016llX\",\"size\":%u,\"function\":%u,"
                    "\"instructionOffset\":%u,\"original\":",
                    static_cast<unsigned long long>(site.offset), static_cast<unsigned long long>(site.address),
                    site.size, site.functionIndex, site.instructionOffset);
        printHex(site.original, site.size);
        std::printf(",\"replacement\":");
        printHex(site.replacement, site.size);
        std::putchar('}');
    }
    std::printf("]}");
}

template<typename Patch>
static void checkPlan(const VerifiedPatchPlan& plan, const Patch* patches, const UInt32 count, const UInt32 reads,
                      const char* label)
{
    check(plan.valid(), label);
    check(plan.siteCount() == count, label);
    check(plan.reads() == reads, label);
    for (UInt32 i = 0; i < count && i < plan.siteCount(); ++i) {
        const auto& site   = plan.site(i);
        const auto& source = patches[i];
        check(site.offset == source.offset, label);
        check(site.address == plan.base() + source.offset, label);
        check(site.size == source.size, label);
        check(site.original != nullptr && site.replacement != nullptr, label);
        if (site.original && site.replacement) {
            check(std::memcmp(site.original, source.original, site.size) == 0, label);
            check(std::memcmp(site.replacement, source.patched, site.size) == 0, label);
        }
    }
}

static void checkImages(const VerifiedPatchPlan& plan, const UserCacheResolver::ImageBounds* images,
                        const UInt32 count, const FeatureManifest& manifest, const char* label)
{
    check(plan.imageCount() == count, label);
    for (UInt32 i = 0; i < count && i < plan.imageCount(); ++i) {
        check(plan.image(i).address == images[i].address, label);
        check(plan.image(i).text.size == images[i].text.size, label);
        check(manifest.resolver.images != nullptr && std::strcmp(plan.image(i).path, manifest.resolver.images[i].path) == 0,
              label);
    }
}

template<typename Data>
static void testVideo(const char* os, const UInt32 osMask, const bool sequoia)
{
    for (const auto& device : Data::devices) {
        cache(Data::images, Data::functions, true);
        UserSharedCache::Input input{reader, {}};
        UInt8                    scan[UserSharedCache::Input::ScanSize];
        input.scanBuffer = scan;
        UserVideoDecodeResolver::Plan legacy;
        char                          label[128];
        std::snprintf(label, sizeof(label), "video %s device %04X", os, device.deviceID);
        check(UserVideoDecodeResolver::resolve(input, base, device.patches, legacy, sequoia), label);
        const auto* manifest = VPF::ManifestData::find(VPF::FeatureId::Vcn2Decode, osMask, device.deviceID);
        check(manifest != nullptr, label);
        if (!manifest) { continue; }
        VerifiedPatchPlan plan;
        check(VPF::adaptVideo(plan, *manifest, base, input.cache, legacy, PlanState::AllOriginal), label);
        checkPlan(plan, legacy.patches, manifest->resolver.siteCount, legacy.reads, label);
        checkImages(plan, legacy.images, manifest->resolver.imageCount, *manifest, label);
        emitPlan("Vcn2Decode", os, device.deviceID, PlanState::AllOriginal, plan);
    }
}

template<typename Data>
static void testGuard(const char* os, const UInt32 osMask, const UInt32 major)
{
    constexpr auto targets = sizeof(Data::sectionTargets) / sizeof(Data::sectionTargets[0]);
    cache(Data::images, Data::functions, true, Data::sectionTargets, targets);
    UserSharedCache::Input input{reader, {}};
    UInt8                    scan[UserSharedCache::Input::ScanSize];
    input.scanBuffer = scan;
    UserVideoGuardResolver::Plan legacy;
    char                          label[128];
    std::snprintf(label, sizeof(label), "guard %s", os);
    check(UserVideoGuardResolver::resolve(input, base, major, legacy), label);
    const auto* manifest = VPF::ManifestData::find(VPF::FeatureId::Vcn2SafetyGuard, osMask, 0);
    check(manifest != nullptr, label);
    if (!manifest) { return; }
    VerifiedPatchPlan plan;
    check(VPF::adaptGuard(plan, *manifest, base, input.cache, legacy), label);
    checkPlan(plan, legacy.patches, legacy.count, legacy.reads, label);
    emitPlan("Vcn2SafetyGuard", os, 0, legacy.installed ? PlanState::AllPatched : PlanState::AllOriginal, plan);
}

static void testSequoiaSurface()
{
    using namespace UserSequoiaSurfaceData;
    constexpr auto functionCount = sizeof(ResolverFunctions) / sizeof(ResolverFunctions[0]);
    for (const auto& device : DevicePatches) {
        cache(ResolverImages, ResolverFunctions, false);
        const auto constant = destination(ResolverFunctions[0].originalRefs[0], ResolverFunctions, functionCount);
        blocks[constant].resize(16);
        word32(constant, 0x40400000);
        const auto dump = destination(ResolverFunctions[0].originalRefs[2], ResolverFunctions, functionCount);
        put(dump, UserSurfaceSyncData::NativeDump, sizeof(UserSurfaceSyncData::NativeDump));

        UserSurfaceSyncResolver::Plan legacy;
        char                                label[128];
        std::snprintf(label, sizeof(label), "surface Darwin24 device %04X", device.deviceID);
        check(UserSurfaceSyncResolver::resolveSequoia(reader, base, device.deviceID, legacy), label);
        const auto* manifest = VPF::ManifestData::find(VPF::FeatureId::DisplaySRD, VPF::OsDarwin24, device.deviceID);
        check(manifest != nullptr, label);
        if (!manifest) { continue; }
        VerifiedPatchPlan plan;
        check(VPF::adaptSurface(plan, *manifest, base, legacy.input.cache, legacy, 4, PlanState::AllOriginal), label);
        checkPlan(plan, legacy.patches, 4, legacy.reads, label);
        emitPlan("DisplaySRD", "Darwin24", device.deviceID, PlanState::AllOriginal, plan);
    }
}

static void testScratch()
{
    using namespace UserSurfaceSyncData;
    for (const auto& device : DevicePatches) {
        cache(ScratchResolverData::ResolverImages, ScratchResolverData::ResolverFunctions, true);
        const auto image            = imageAddresses[0];
        const auto initAddress      = image + 0x21f80;
        const auto overrideAddress  = image + 0x23ff8;
        const auto constructorAddress = image + 0x28fe5;
        const auto settingsAddress  = image + 0x26000;
        const auto dumpAddress      = image + 0x27000;
        put(initAddress, Original0, sizeof(Original0));
        put(overrideAddress, Original1, sizeof(Original1));
        put(constructorAddress, BlendConstructor, sizeof(BlendConstructor));
        put(settingsAddress, NativeOverride, sizeof(NativeOverride));
        put(dumpAddress, NativeDump, sizeof(NativeDump));
        word32(image + 0x71064, 0x40400000);
        const UInt64 targets[] = {image + 0x71064, settingsAddress, dumpAddress};
        for (const auto link : OriginalInitLinks) { rel(initAddress, link.offset, targets[link.target]); }
        rel(overrideAddress, 6, settingsAddress);

        UserSurfaceSyncResolver::Plan legacy;
        char                                label[128];
        std::snprintf(label, sizeof(label), "scratch device %04X", device.deviceID);
        check(UserSurfaceSyncResolver::resolve(reader, base, device, true, true, legacy, true), label);
        const auto* manifest = VPF::ManifestData::find(VPF::FeatureId::DisplayComputeScratch, VPF::OsDarwin25, 0);
        check(manifest != nullptr, label);
        if (!manifest) { continue; }
        VerifiedPatchPlan plan;
        check(VPF::adaptSurface(plan, *manifest, base, legacy.input.cache, legacy, 4, PlanState::AllOriginal), label);
        checkPlan(plan, legacy.patches, 4, legacy.reads, label);
        emitPlan("DisplayComputeScratch", "Darwin25", device.deviceID, PlanState::AllOriginal, plan);
    }
}

// 站点按半开区间 [address, address + size) 判定重叠：部分重叠、包含和重复均拒绝，相邻允许。
static void testSiteOverlap()
{
    static const UInt8 bytes[16]{};
    const UInt64       start = 0x180000000ULL;
    VerifiedPatchPlan  plan;
    plan.reset(start, {start, 0x1000});
    check(plan.addSite(start + 0x100, 0x100, 8, VPF::NoFunction, 0, bytes, bytes), "overlap first site");
    check(!plan.addSite(start + 0x104, 0x104, 8, VPF::NoFunction, 0, bytes, bytes), "overlap partial tail");
    check(!plan.addSite(start + 0xFC, 0xFC, 8, VPF::NoFunction, 0, bytes, bytes), "overlap partial head");
    check(!plan.addSite(start + 0x102, 0x102, 2, VPF::NoFunction, 0, bytes, bytes), "overlap contained");
    check(!plan.addSite(start + 0xF8, 0xF8, 16, VPF::NoFunction, 0, bytes, bytes), "overlap containing");
    check(!plan.addSite(start + 0x100, 0x100, 8, VPF::NoFunction, 0, bytes, bytes), "overlap duplicate");
    check(plan.addSite(start + 0x108, 0x108, 4, VPF::NoFunction, 0, bytes, bytes), "overlap adjacent after");
    check(plan.addSite(start + 0xF8, 0xF8, 8, VPF::NoFunction, 0, bytes, bytes), "overlap adjacent before");
    check(plan.siteCount() == 3, "overlap site count");
}

int main(int argc, char** argv)
{
    jsonMode = argc > 1 && std::strcmp(argv[1], "--json") == 0;
    if (jsonMode) { std::printf("{\"schemaVersion\":1,\"kind\":\"vpf.plans\",\"plans\":["); }
    testVideo<VideoDarwin24>("Darwin24", VPF::OsDarwin24, true);
    testVideo<VideoDarwin25>("Darwin25", VPF::OsDarwin25, false);
    testGuard<GuardDarwin24>("Darwin24", VPF::OsDarwin24, 24);
    testGuard<GuardDarwin25>("Darwin25", VPF::OsDarwin25, 25);
    testSequoiaSurface();
    testScratch();
    testSiteOverlap();
    if (jsonMode) { std::printf("]}\n"); }
    if (failures) {
        std::fprintf(stderr, "FAILED: %u checks\n", failures);
        return 1;
    }
    if (!jsonMode) { std::printf("PASS VPF plan probe: %u plans\n", emittedPlans); }
    return 0;
}
