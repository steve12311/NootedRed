// DVPF 显示计划探针：在合成 OpenGL 页和现有生成表上核对 VerifiedPagePlan。
// 不读取系统驱动；--json 输出规范化计划供 compare_display_plan.py 比较。
#include <VPF/DisplayManifestData.hpp>
#include <VPF/DisplayPlanAdapters.hpp>
#include <array>
#include <cstdio>
#include <cstring>

using VPF::DisplayManifest;
using VPF::OpenGLPageContext;
using VPF::VerifiedPagePlan;

static unsigned failures  = 0;
static bool     jsonMode  = false;
static bool     firstPlan = true;
static unsigned emitted   = 0;

static void check(const bool condition, const char* label)
{
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", label);
        ++failures;
    }
}

static std::array<UInt8, VPF::DisplayPageSize> moved(const UserOpenGLData::Device& device, const UInt32 kind,
                                                     const UInt64 page, const UInt32 offset, const bool patched)
{
    std::array<UInt8, VPF::DisplayPageSize> bytes;
    bytes.fill(0xCC);
    const auto& patch = device.patches[kind];
    const auto& fn    = device.resolver->functions[kind];
    const auto  start = device.resolver->locations[kind].offset;
    memcpy(bytes.data() + offset, patched ? patch.patched : patch.original, patch.size);
    const auto* refs  = patched ? fn.patchedRefs : fn.originalRefs;
    const auto  count = patched ? fn.patchedCount : fn.originalCount;
    for (UInt32 r = 0; r < count; ++r) {
        check(UserSharedCache::setRelative(bytes.data() + offset, refs[r].offset - start, page + offset, refs[r].rva),
              "rel32 rebuild");
    }
    return bytes;
}

static void printHex(const UInt8* bytes, const UInt32 size)
{
    std::putchar('"');
    for (UInt32 i = 0; i < size; ++i) { std::printf("%02X", bytes[i]); }
    std::putchar('"');
}

static const char* stateName(const VPF::PlanState state)
{
    switch (state) {
        case VPF::PlanState::AllOriginal: return "AllOriginal";
        case VPF::PlanState::AllPatched : return "AllPatched";
        case VPF::PlanState::Mixed      : return "Mixed";
        default                         : return "Unknown";
    }
}

static const char* featureName(const VPF::DisplayFeature feature)
{
    switch (feature) {
        case VPF::DisplayFeature::OpenGLHwInfo  : return "OpenGLHwInfo";
        case VPF::DisplayFeature::OpenGLDualQuad: return "OpenGLDualQuad";
        default                                 : return "Unknown";
    }
}

static void emitPage(const char* mode, const UserOpenGLData::Device& device, const DisplayManifest& manifest,
                     const VerifiedPagePlan& plan)
{
    ++emitted;
    if (!jsonMode) { return; }
    if (!firstPlan) { std::putchar(','); }
    firstPlan = false;
    std::printf("{\"feature\":\"%s\",\"os\":\"Darwin%u\",\"deviceID\":%u,\"mode\":\"%s\",\"state\":\"%s\","
                "\"exact\":%s,\"dynamic\":%s,\"pageFileOffset\":\"0x%016llX\",\"patchFileOffset\":\"0x%016llX\","
                "\"referenceOffset\":\"0x%016llX\",\"size\":%u,\"function\":%u,\"instructionOffset\":%u,"
                "\"validated\":%u,\"tainted\":%u,\"nx\":%u,\"original\":",
                featureName(manifest.feature), device.major, device.deviceID, mode, stateName(plan.state()),
                plan.exactPage() ? "true" : "false", plan.dynamic() ? "true" : "false",
                static_cast<unsigned long long>(plan.pageFileOffset()),
                static_cast<unsigned long long>(plan.patchFileOffset()),
                static_cast<unsigned long long>(plan.referenceOffset()), plan.size(), plan.functionIndex(),
                plan.instructionOffset(), plan.validated(), plan.tainted(), plan.nx());
    printHex(plan.original(), plan.size());
    std::printf(",\"replacement\":");
    printHex(plan.replacement(), plan.size());
    std::putchar('}');
}

static void testPage(const UserOpenGLData::Device& device, const UInt32 kind)
{
    const auto* manifest = VPF::DisplayManifestData::findOpenGL(device.major, device.deviceID, kind);
    check(manifest != nullptr, "manifest lookup");
    if (!manifest) { return; }
    const auto& patch      = device.patches[kind];
    const auto& page       = device.pages[kind];
    const auto  fileOffset = patch.offset - page.offset;

    std::array<UInt8, VPF::DisplayPageSize> bytes;
    memcpy(bytes.data(), page.original, VPF::DisplayPageSize);
    UserOpenGLResolver::Resolved resolved{};
    check(UserOpenGLResolver::resolvePage(device, bytes.data(), fileOffset, kind, resolved), "exact resolve");
    OpenGLPageContext context{1, 0, 0, fileOffset, true};
    VerifiedPagePlan  plan;
    check(VPF::adaptOpenGLPage(plan, *manifest, device, resolved, context), "exact adapt");
    check(plan.valid() && plan.installable() && plan.state() == VPF::PlanState::AllOriginal, "exact plan state");
    check(plan.patchFileOffset() == patch.offset, "exact patch offset");
    check(plan.referenceOffset() == patch.offset, "exact reference offset");
    check(plan.size() == patch.size && memcmp(plan.original(), patch.original, patch.size) == 0, "exact original");
    check(memcmp(plan.replacement(), patch.patched, patch.size) == 0, "exact replacement");
    check(plan.functionIndex() == kind && plan.instructionOffset() == device.resolver->locations[kind].offset,
          "exact function evidence");
    emitPage("exact", device, *manifest, plan);

    memcpy(bytes.data() + page.offset, patch.patched, patch.size);
    check(UserOpenGLResolver::resolvePage(device, bytes.data(), fileOffset, kind, resolved), "patched resolve");
    check(VPF::adaptOpenGLPage(plan, *manifest, device, resolved, context), "patched adapt");
    check(plan.alreadyInstalled() && !plan.installable(), "patched plan state");
    emitPage("patched", device, *manifest, plan);

    const UInt32 offset =
        0x180 + (static_cast<UInt32>(device.deviceID) + kind * 257) % (VPF::DisplayPageSize - patch.size - 0x180);
    const UInt64 pageBase = 0x40000;
    bytes                 = moved(device, kind, pageBase, offset, false);
    check(UserOpenGLResolver::resolvePage(device, bytes.data(), pageBase, kind, resolved), "moved resolve");
    check(resolved.dynamic, "moved dynamic");
    OpenGLPageContext movedContext{0xF, 0, 0, pageBase, true};
    check(VPF::adaptOpenGLPage(plan, *manifest, device, resolved, movedContext), "moved adapt");
    check(plan.valid() && plan.installable() && plan.dynamic() && !plan.exactPage(), "moved plan state");
    check(plan.patchFileOffset() == pageBase + offset, "moved patch offset");
    check(plan.referenceOffset() == patch.offset, "moved reference offset");
    check(plan.size() == patch.size, "moved size");
    emitPage("moved", device, *manifest, plan);

    // 原生验证失败、taint、NX 和错误 vnode/偏移都必须拒绝。
    OpenGLPageContext rejected = {2, 0, 0, fileOffset, true};
    check(!VPF::adaptOpenGLPage(plan, *manifest, device, resolved, rejected), "reject invalid validated");
    rejected = {1, 1, 0, fileOffset, true};
    check(!VPF::adaptOpenGLPage(plan, *manifest, device, resolved, rejected), "reject tainted");
    rejected = {1, 0, 1, fileOffset, true};
    check(!VPF::adaptOpenGLPage(plan, *manifest, device, resolved, rejected), "reject nx");
    rejected = {1, 0, 0, fileOffset, false};
    check(!VPF::adaptOpenGLPage(plan, *manifest, device, resolved, rejected), "reject vnode");
    rejected = {1, 0, 0, fileOffset + 1, true};
    check(!VPF::adaptOpenGLPage(plan, *manifest, device, resolved, rejected), "reject misaligned");

    // 同页两个候选、页末无法完整容纳的块都必须拒绝。
    auto       ambiguous = moved(device, kind, pageBase, 0x200, false);
    const auto other     = moved(device, kind, pageBase, 0x800, true);
    memcpy(ambiguous.data() + 0x800, other.data() + 0x800, patch.size);
    UserOpenGLResolver::Resolved duplicate{};
    check(!UserOpenGLResolver::resolvePage(device, ambiguous.data(), pageBase, kind, duplicate), "reject ambiguous");

    std::array<UInt8, VPF::DisplayPageSize> truncated;
    truncated.fill(0xCC);
    memcpy(truncated.data() + VPF::DisplayPageSize - (patch.size - 1), patch.original, patch.size - 1);
    check(!UserOpenGLResolver::resolvePage(device, truncated.data(), pageBase, kind, duplicate), "reject cross page");
}

static void testStaticMetal()
{
    for (UInt32 i = 0; i < VPF::DisplayManifestData::FeatureCount; ++i) {
        const auto& manifest = VPF::DisplayManifestData::Features[i];
        if (manifest.sharedSites == nullptr) { continue; }
        if (manifest.feature == VPF::DisplayFeature::MetalLegacyBlend && manifest.osMask == VPF::OsDarwin23) {
            check(manifest.sharedSiteCount == 1 && manifest.sharedSites[0].size == 2
                      && memcmp(manifest.sharedSites[0].original, UserLegacyData::OS23Gate, 2) == 0
                      && memcmp(manifest.sharedSites[0].replacement, UserLegacyData::OS23GatePatched, 2) == 0,
                  "darwin23 legacy site");
            continue;
        }
        const auto* shared = VPF::ManifestData::find(manifest.sharedFeature, manifest.osMask, manifest.deviceID);
        check(shared != nullptr, "shared manifest");
        if (!shared) { continue; }
        check(shared->resolver.sites == manifest.sharedSites, "shared site pointer");
        check(shared->resolver.siteCount == manifest.sharedSiteCount, "shared site count");
        for (UInt32 site = 0; site < manifest.sharedSiteCount; ++site) {
            check(manifest.sharedSites[site].size == shared->resolver.sites[site].size, "shared site size");
            check(memcmp(manifest.sharedSites[site].original, shared->resolver.sites[site].original,
                         manifest.sharedSites[site].size)
                      == 0,
                  "shared site original");
            check(memcmp(manifest.sharedSites[site].replacement, shared->resolver.sites[site].replacement,
                         manifest.sharedSites[site].size)
                      == 0,
                  "shared site replacement");
        }
    }
}

int main(int argc, char** argv)
{
    jsonMode = argc > 1 && strcmp(argv[1], "--json") == 0;
    if (jsonMode) { std::printf("{\"schemaVersion\":1,\"kind\":\"dvpf.plans\",\"plans\":["); }
    for (const auto& device : UserOpenGLData::Devices) {
        for (UInt32 kind = 0; kind < 2; ++kind) { testPage(device, kind); }
    }
    testStaticMetal();
    if (jsonMode) { std::printf("]}\n"); }
    if (failures) {
        std::fprintf(stderr, "FAILED: %u checks\n", failures);
        return 1;
    }
    if (!jsonMode) { std::printf("PASS DVPF display plan probe: %u plans\n", emitted); }
    return 0;
}
