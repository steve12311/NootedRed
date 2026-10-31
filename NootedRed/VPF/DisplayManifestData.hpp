// 由 tools/dvpf/generate_display_manifest.py 生成，不手工编辑。
#pragma once
#include <VPF/DisplayManifest.hpp>
#include <VPF/PatchManifestData.hpp>
#include <UserOpenGLData.hpp>
#include <UserSubmitGuardData.hpp>
#include <UserLegacyData.hpp>

namespace VPF
{
namespace DisplayManifestData
{

inline constexpr UInt8 SubmitGuardEntryTemplate[] = {
    0xFF, 0x25, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x90,
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin23_15D8[] = {
    {UserOpenGLData::OS23PCI15D8Patches[0].offset, UserOpenGLData::OS23PCI15D8Patches[0].size, UserOpenGLData::OS23PCI15D8Patches[0].original, UserOpenGLData::OS23PCI15D8Patches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver23.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin23_15DD[] = {
    {UserOpenGLData::OS23PCI15DDPatches[0].offset, UserOpenGLData::OS23PCI15DDPatches[0].size, UserOpenGLData::OS23PCI15DDPatches[0].original, UserOpenGLData::OS23PCI15DDPatches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver23.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin23_15E7[] = {
    {UserOpenGLData::OS23PCI15E7Patches[0].offset, UserOpenGLData::OS23PCI15E7Patches[0].size, UserOpenGLData::OS23PCI15E7Patches[0].original, UserOpenGLData::OS23PCI15E7Patches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver23.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin23_1636[] = {
    {UserOpenGLData::OS23PCI1636Patches[0].offset, UserOpenGLData::OS23PCI1636Patches[0].size, UserOpenGLData::OS23PCI1636Patches[0].original, UserOpenGLData::OS23PCI1636Patches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver23.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin23_1638[] = {
    {UserOpenGLData::OS23PCI1638Patches[0].offset, UserOpenGLData::OS23PCI1638Patches[0].size, UserOpenGLData::OS23PCI1638Patches[0].original, UserOpenGLData::OS23PCI1638Patches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver23.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin23_164C[] = {
    {UserOpenGLData::OS23PCI164CPatches[0].offset, UserOpenGLData::OS23PCI164CPatches[0].size, UserOpenGLData::OS23PCI164CPatches[0].original, UserOpenGLData::OS23PCI164CPatches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver23.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin24_15D8[] = {
    {UserOpenGLData::OS24PCI15D8Patches[0].offset, UserOpenGLData::OS24PCI15D8Patches[0].size, UserOpenGLData::OS24PCI15D8Patches[0].original, UserOpenGLData::OS24PCI15D8Patches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver24.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin24_15DD[] = {
    {UserOpenGLData::OS24PCI15DDPatches[0].offset, UserOpenGLData::OS24PCI15DDPatches[0].size, UserOpenGLData::OS24PCI15DDPatches[0].original, UserOpenGLData::OS24PCI15DDPatches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver24.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin24_15E7[] = {
    {UserOpenGLData::OS24PCI15E7Patches[0].offset, UserOpenGLData::OS24PCI15E7Patches[0].size, UserOpenGLData::OS24PCI15E7Patches[0].original, UserOpenGLData::OS24PCI15E7Patches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver24.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin24_1636[] = {
    {UserOpenGLData::OS24PCI1636Patches[0].offset, UserOpenGLData::OS24PCI1636Patches[0].size, UserOpenGLData::OS24PCI1636Patches[0].original, UserOpenGLData::OS24PCI1636Patches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver24.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin24_1638[] = {
    {UserOpenGLData::OS24PCI1638Patches[0].offset, UserOpenGLData::OS24PCI1638Patches[0].size, UserOpenGLData::OS24PCI1638Patches[0].original, UserOpenGLData::OS24PCI1638Patches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver24.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin24_164C[] = {
    {UserOpenGLData::OS24PCI164CPatches[0].offset, UserOpenGLData::OS24PCI164CPatches[0].size, UserOpenGLData::OS24PCI164CPatches[0].original, UserOpenGLData::OS24PCI164CPatches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver24.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin25_15D8[] = {
    {UserOpenGLData::OS25PCI15D8Patches[0].offset, UserOpenGLData::OS25PCI15D8Patches[0].size, UserOpenGLData::OS25PCI15D8Patches[0].original, UserOpenGLData::OS25PCI15D8Patches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver25.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin25_15DD[] = {
    {UserOpenGLData::OS25PCI15DDPatches[0].offset, UserOpenGLData::OS25PCI15DDPatches[0].size, UserOpenGLData::OS25PCI15DDPatches[0].original, UserOpenGLData::OS25PCI15DDPatches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver25.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin25_15E7[] = {
    {UserOpenGLData::OS25PCI15E7Patches[0].offset, UserOpenGLData::OS25PCI15E7Patches[0].size, UserOpenGLData::OS25PCI15E7Patches[0].original, UserOpenGLData::OS25PCI15E7Patches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver25.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin25_1636[] = {
    {UserOpenGLData::OS25PCI1636Patches[0].offset, UserOpenGLData::OS25PCI1636Patches[0].size, UserOpenGLData::OS25PCI1636Patches[0].original, UserOpenGLData::OS25PCI1636Patches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver25.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin25_1638[] = {
    {UserOpenGLData::OS25PCI1638Patches[0].offset, UserOpenGLData::OS25PCI1638Patches[0].size, UserOpenGLData::OS25PCI1638Patches[0].original, UserOpenGLData::OS25PCI1638Patches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver25.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLHwInfo_Darwin25_164C[] = {
    {UserOpenGLData::OS25PCI164CPatches[0].offset, UserOpenGLData::OS25PCI164CPatches[0].size, UserOpenGLData::OS25PCI164CPatches[0].original, UserOpenGLData::OS25PCI164CPatches[0].patched,
     0, 0, 0, UserOpenGLData::Resolver25.locations[0].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin23_15D8[] = {
    {UserOpenGLData::OS23PCI15D8Patches[1].offset, UserOpenGLData::OS23PCI15D8Patches[1].size, UserOpenGLData::OS23PCI15D8Patches[1].original, UserOpenGLData::OS23PCI15D8Patches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver23.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin23_15DD[] = {
    {UserOpenGLData::OS23PCI15DDPatches[1].offset, UserOpenGLData::OS23PCI15DDPatches[1].size, UserOpenGLData::OS23PCI15DDPatches[1].original, UserOpenGLData::OS23PCI15DDPatches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver23.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin23_15E7[] = {
    {UserOpenGLData::OS23PCI15E7Patches[1].offset, UserOpenGLData::OS23PCI15E7Patches[1].size, UserOpenGLData::OS23PCI15E7Patches[1].original, UserOpenGLData::OS23PCI15E7Patches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver23.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin23_1636[] = {
    {UserOpenGLData::OS23PCI1636Patches[1].offset, UserOpenGLData::OS23PCI1636Patches[1].size, UserOpenGLData::OS23PCI1636Patches[1].original, UserOpenGLData::OS23PCI1636Patches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver23.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin23_1638[] = {
    {UserOpenGLData::OS23PCI1638Patches[1].offset, UserOpenGLData::OS23PCI1638Patches[1].size, UserOpenGLData::OS23PCI1638Patches[1].original, UserOpenGLData::OS23PCI1638Patches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver23.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin23_164C[] = {
    {UserOpenGLData::OS23PCI164CPatches[1].offset, UserOpenGLData::OS23PCI164CPatches[1].size, UserOpenGLData::OS23PCI164CPatches[1].original, UserOpenGLData::OS23PCI164CPatches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver23.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin24_15D8[] = {
    {UserOpenGLData::OS24PCI15D8Patches[1].offset, UserOpenGLData::OS24PCI15D8Patches[1].size, UserOpenGLData::OS24PCI15D8Patches[1].original, UserOpenGLData::OS24PCI15D8Patches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver24.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin24_15DD[] = {
    {UserOpenGLData::OS24PCI15DDPatches[1].offset, UserOpenGLData::OS24PCI15DDPatches[1].size, UserOpenGLData::OS24PCI15DDPatches[1].original, UserOpenGLData::OS24PCI15DDPatches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver24.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin24_15E7[] = {
    {UserOpenGLData::OS24PCI15E7Patches[1].offset, UserOpenGLData::OS24PCI15E7Patches[1].size, UserOpenGLData::OS24PCI15E7Patches[1].original, UserOpenGLData::OS24PCI15E7Patches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver24.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin24_1636[] = {
    {UserOpenGLData::OS24PCI1636Patches[1].offset, UserOpenGLData::OS24PCI1636Patches[1].size, UserOpenGLData::OS24PCI1636Patches[1].original, UserOpenGLData::OS24PCI1636Patches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver24.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin24_1638[] = {
    {UserOpenGLData::OS24PCI1638Patches[1].offset, UserOpenGLData::OS24PCI1638Patches[1].size, UserOpenGLData::OS24PCI1638Patches[1].original, UserOpenGLData::OS24PCI1638Patches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver24.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin24_164C[] = {
    {UserOpenGLData::OS24PCI164CPatches[1].offset, UserOpenGLData::OS24PCI164CPatches[1].size, UserOpenGLData::OS24PCI164CPatches[1].original, UserOpenGLData::OS24PCI164CPatches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver24.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin25_15D8[] = {
    {UserOpenGLData::OS25PCI15D8Patches[1].offset, UserOpenGLData::OS25PCI15D8Patches[1].size, UserOpenGLData::OS25PCI15D8Patches[1].original, UserOpenGLData::OS25PCI15D8Patches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver25.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin25_15DD[] = {
    {UserOpenGLData::OS25PCI15DDPatches[1].offset, UserOpenGLData::OS25PCI15DDPatches[1].size, UserOpenGLData::OS25PCI15DDPatches[1].original, UserOpenGLData::OS25PCI15DDPatches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver25.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin25_15E7[] = {
    {UserOpenGLData::OS25PCI15E7Patches[1].offset, UserOpenGLData::OS25PCI15E7Patches[1].size, UserOpenGLData::OS25PCI15E7Patches[1].original, UserOpenGLData::OS25PCI15E7Patches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver25.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin25_1636[] = {
    {UserOpenGLData::OS25PCI1636Patches[1].offset, UserOpenGLData::OS25PCI1636Patches[1].size, UserOpenGLData::OS25PCI1636Patches[1].original, UserOpenGLData::OS25PCI1636Patches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver25.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin25_1638[] = {
    {UserOpenGLData::OS25PCI1638Patches[1].offset, UserOpenGLData::OS25PCI1638Patches[1].size, UserOpenGLData::OS25PCI1638Patches[1].original, UserOpenGLData::OS25PCI1638Patches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver25.locations[1].offset},
};

inline constexpr DisplaySiteTemplate PageSites_OpenGLDualQuad_Darwin25_164C[] = {
    {UserOpenGLData::OS25PCI164CPatches[1].offset, UserOpenGLData::OS25PCI164CPatches[1].size, UserOpenGLData::OS25PCI164CPatches[1].original, UserOpenGLData::OS25PCI164CPatches[1].patched,
     0, 0, 1, UserOpenGLData::Resolver25.locations[1].offset},
};

inline constexpr PatchSite SharedSites_MetalLegacyBlend_Darwin23[] = {
    {NoFunction, 0, 2, UserLegacyData::OS23Gate, UserLegacyData::OS23GatePatched},
};

inline constexpr DisplaySiteTemplate PageSites_MetalSubmitGuard_Darwin23[] = {
    {0x30355E210ULL, 15, UserSubmitGuardData::MethodOriginal, SubmitGuardEntryTemplate, 6, 8, NoFunction, 0},
};

inline constexpr DisplaySiteTemplate PageSites_MetalSubmitGuard_Darwin24[] = {
    {0x0ULL, 15, UserSubmitGuardData::MethodOriginal, SubmitGuardEntryTemplate, 6, 8, NoFunction, 0},
};

inline constexpr DisplaySiteTemplate PageSites_MetalSubmitGuard_Darwin25[] = {
    {0x0ULL, 15, UserSubmitGuardData::MethodOriginal, SubmitGuardEntryTemplate, 6, 8, NoFunction, 0},
};

inline constexpr DisplayManifest Features[] = {
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin23, 0x15D8, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin23_15D8, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin23, 0x15DD, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin23_15DD, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin23, 0x15E7, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin23_15E7, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin23, 0x1636, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin23_1636, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin23, 0x1638, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin23_1638, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin23, 0x164C, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin23_164C, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin24, 0x15D8, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin24_15D8, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin24, 0x15DD, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin24_15DD, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin24, 0x15E7, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin24_15E7, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin24, 0x1636, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin24_1636, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin24, 0x1638, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin24_1638, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin24, 0x164C, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin24_164C, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin25, 0x15D8, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin25_15D8, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin25, 0x15DD, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin25_15DD, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin25, 0x15E7, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin25_15E7, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin25, 0x1636, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin25_1636, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin25, 0x1638, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin25_1638, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLHwInfo, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLHwInfo,
     OsDarwin25, 0x164C, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 256,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLHwInfo_Darwin25_164C, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin23, 0x15D8, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin23_15D8, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin23, 0x15DD, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin23_15DD, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin23, 0x15E7, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin23_15E7, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin23, 0x1636, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin23_1636, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin23, 0x1638, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin23_1638, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin23, 0x164C, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin23_164C, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin24, 0x15D8, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin24_15D8, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin24, 0x15DD, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin24_15DD, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin24, 0x15E7, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin24_15E7, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin24, 0x1636, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin24_1636, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin24, 0x1638, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin24_1638, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin24, 0x164C, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin24_164C, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin25, 0x15D8, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin25_15D8, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin25, 0x15DD, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin25_15DD, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin25, 0x15E7, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin25_15E7, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin25, 0x1636, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin25_1636, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin25, 0x1638, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin25_1638, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::OpenGLDualQuad, DisplayDomain::OpenGLValidatedPage, DisplayGroup::GLDualQuad,
     OsDarwin25, 0x164C, DisplayEvidence::ExactKnownPage, FeatureId::Count,
     0x0, 0x0, 1, 142,
     true, true, "AMDRadeonX5000GLDriver", "UserOpenGLData.hpp", nullptr,
     nullptr, 0, PageSites_OpenGLDualQuad_Darwin25_164C, 1,
     nullptr, 0, 0, 0},
    {DisplayFeature::MetalSrdShared, DisplayDomain::MetalSharedCache, DisplayGroup::MetalRenderCore,
     OsDarwin24, 0x15D8, DisplayEvidence::FullFunctionWithRefs, FeatureId::DisplaySRD,
     0x0, 0x0, 4, 456,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSequoiaSurfaceData.hpp", "42EC2890BC40317EBB2745142BFF7F26",
     VPF::ManifestData::Sites_DisplaySRD_Darwin24_15D8.sites, 4, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::MetalSrdShared, DisplayDomain::MetalSharedCache, DisplayGroup::MetalRenderCore,
     OsDarwin24, 0x15DD, DisplayEvidence::FullFunctionWithRefs, FeatureId::DisplaySRD,
     0x0, 0x0, 4, 456,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSequoiaSurfaceData.hpp", "42EC2890BC40317EBB2745142BFF7F26",
     VPF::ManifestData::Sites_DisplaySRD_Darwin24_15DD.sites, 4, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::MetalSrdShared, DisplayDomain::MetalSharedCache, DisplayGroup::MetalRenderCore,
     OsDarwin24, 0x15E7, DisplayEvidence::FullFunctionWithRefs, FeatureId::DisplaySRD,
     0x0, 0x0, 4, 456,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSequoiaSurfaceData.hpp", "42EC2890BC40317EBB2745142BFF7F26",
     VPF::ManifestData::Sites_DisplaySRD_Darwin24_15E7.sites, 4, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::MetalSrdShared, DisplayDomain::MetalSharedCache, DisplayGroup::MetalRenderCore,
     OsDarwin24, 0x1636, DisplayEvidence::FullFunctionWithRefs, FeatureId::DisplaySRD,
     0x0, 0x0, 4, 456,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSequoiaSurfaceData.hpp", "42EC2890BC40317EBB2745142BFF7F26",
     VPF::ManifestData::Sites_DisplaySRD_Darwin24_1636.sites, 4, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::MetalSrdShared, DisplayDomain::MetalSharedCache, DisplayGroup::MetalRenderCore,
     OsDarwin24, 0x1638, DisplayEvidence::FullFunctionWithRefs, FeatureId::DisplaySRD,
     0x0, 0x0, 4, 456,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSequoiaSurfaceData.hpp", "42EC2890BC40317EBB2745142BFF7F26",
     VPF::ManifestData::Sites_DisplaySRD_Darwin24_1638.sites, 4, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::MetalSrdShared, DisplayDomain::MetalSharedCache, DisplayGroup::MetalRenderCore,
     OsDarwin24, 0x164C, DisplayEvidence::FullFunctionWithRefs, FeatureId::DisplaySRD,
     0x0, 0x0, 4, 456,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSequoiaSurfaceData.hpp", "42EC2890BC40317EBB2745142BFF7F26",
     VPF::ManifestData::Sites_DisplaySRD_Darwin24_164C.sites, 4, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::MetalSrdShared, DisplayDomain::MetalSharedCache, DisplayGroup::MetalRenderCore,
     OsDarwin25, 0x0000, DisplayEvidence::ExactStaticPayload, FeatureId::DisplaySRD,
     0x0, 0x0, 4, 215,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSurfaceSyncData.hpp", "21FB8708FC8E3765BE8C65D612CDF049",
     VPF::ManifestData::Sites_DisplaySRD_Darwin25.sites, 2, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::MetalLegacyBlend, DisplayDomain::MetalSharedCache, DisplayGroup::MetalRenderCore,
     OsDarwin23, 0x0000, DisplayEvidence::ExactStaticPayload, FeatureId::DisplayLegacyBlend,
     0x0, 0x0, 1, 2,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserLegacyData.hpp", "4C1441FD8A543DC2863D49110B946A25",
     SharedSites_MetalLegacyBlend_Darwin23, 1, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::MetalLegacyBlend, DisplayDomain::MetalSharedCache, DisplayGroup::MetalRenderCore,
     OsDarwin25, 0x0000, DisplayEvidence::ExactStaticPayload, FeatureId::DisplayLegacyBlend,
     0x0, 0x0, 3, 215,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSurfaceSyncData.hpp", "21FB8708FC8E3765BE8C65D612CDF049",
     VPF::ManifestData::Sites_DisplayLegacyBlend_Darwin25.sites, 3, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::MetalComputeScratch, DisplayDomain::MetalSharedCache, DisplayGroup::MetalScratch,
     OsDarwin24 | OsDarwin25, 0x0000, DisplayEvidence::FullFunctionWithRefs, FeatureId::DisplayComputeScratch,
     0x0, 0x0, 4, 512,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSurfaceSyncData.hpp", "21FB8708FC8E3765BE8C65D612CDF049",
     nullptr, 0, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::MetalPipelineConstructor, DisplayDomain::MetalSharedCache, DisplayGroup::MetalRenderCore,
     OsDarwin24 | OsDarwin25, 0x0000, DisplayEvidence::FullFunctionWithRefs, FeatureId::DisplayPipelineConstructor,
     0x0, 0x0, 1, 2,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSurfaceConstructorData.hpp", nullptr,
     VPF::ManifestData::Sites_DisplayPipelineConstructor_Darwin24_25.sites, 1, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::MetalSubmit, DisplayDomain::MetalSharedCache, DisplayGroup::MetalSubmitSafety,
     OsDarwin24 | OsDarwin25, 0x0000, DisplayEvidence::FullFunctionWithRefs, FeatureId::DisplaySubmit,
     0x0, 0x0, 0, 0,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSurfaceConstructorData.hpp", nullptr,
     nullptr, 0, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::MetalSubmitGuard, DisplayDomain::MetalSharedCache, DisplayGroup::MetalSubmitSafety,
     OsDarwin23, 0x0000, DisplayEvidence::RuntimeTrampoline, FeatureId::DisplaySubmitGuard,
     0x0, 0x0, 1, 15,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSubmitGuardData.hpp", nullptr,
     nullptr, 0, PageSites_MetalSubmitGuard_Darwin23, 1,
     UserSubmitGuardData::Payload, 307, 296, 8},
    {DisplayFeature::MetalSubmitGuard, DisplayDomain::MetalSharedCache, DisplayGroup::MetalSubmitSafety,
     OsDarwin24, 0x0000, DisplayEvidence::RuntimeTrampoline, FeatureId::DisplaySubmitGuard,
     0x0, 0x0, 1, 15,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSubmitGuardData.hpp", nullptr,
     nullptr, 0, PageSites_MetalSubmitGuard_Darwin24, 1,
     UserSubmitGuardData::Payload, 307, 296, 8},
    {DisplayFeature::MetalSubmitGuard, DisplayDomain::MetalSharedCache, DisplayGroup::MetalSubmitSafety,
     OsDarwin25, 0x0000, DisplayEvidence::RuntimeTrampoline, FeatureId::DisplaySubmitGuard,
     0x0, 0x0, 1, 15,
     false, true, "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver", "UserSubmitGuardData.hpp", nullptr,
     nullptr, 0, PageSites_MetalSubmitGuard_Darwin25, 1,
     UserSubmitGuardData::Payload, 307, 296, 8},
    {DisplayFeature::KernelGFX9Capabilities, DisplayDomain::KernelGraphics, DisplayGroup::KernelGFX9,
     OsDarwin23 | OsDarwin24 | OsDarwin25, 0x0000, DisplayEvidence::KernelSymbolAndABI, FeatureId::Count,
     0x0, 0x0, 0, 0,
     false, false, "AMDRadeonX5000", "X5000.cpp", nullptr,
     nullptr, 0, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::KernelDCNScanout, DisplayDomain::KernelFramebuffer, DisplayGroup::KernelScanout,
     OsDarwin23 | OsDarwin24 | OsDarwin25, 0x0000, DisplayEvidence::KernelSymbolAndABI, FeatureId::Count,
     0x0, 0x0, 0, 0,
     false, false, "AMDRadeonX6000Framebuffer", "X6000FB.cpp", nullptr,
     nullptr, 0, nullptr, 0,
     nullptr, 0, 0, 0},
    {DisplayFeature::KernelFramebuffer, DisplayDomain::KernelFramebuffer, DisplayGroup::KernelScanout,
     OsDarwin23 | OsDarwin24 | OsDarwin25, 0x0000, DisplayEvidence::KernelSymbolAndABI, FeatureId::Count,
     0x0, 0x0, 0, 0,
     false, false, "AMDRadeonX5000", "AMDGFX9DCNDisplay.cpp", nullptr,
     nullptr, 0, nullptr, 0,
     nullptr, 0, 0, 0},
};
inline constexpr UInt32 FeatureCount = sizeof(Features) / sizeof(Features[0]);
static_assert(FeatureCount <= 64);
static_assert(validAll(Features));

inline constexpr const DisplayManifest* find(DisplayFeature feature, UInt32 osMask, UInt32 deviceID = 0)
{
    return VPF::find(Features, FeatureCount, feature, osMask, deviceID);
}

inline constexpr const DisplayManifest* findOpenGL(UInt32 major, UInt32 deviceID, UInt32 kind)
{
    const auto feature = kind == 0 ? DisplayFeature::OpenGLHwInfo : DisplayFeature::OpenGLDualQuad;
    return find(feature, displayOsMask(major), deviceID);
}

}    // namespace DisplayManifestData
}    // namespace VPF
