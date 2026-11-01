// 由 tools/vpf/generate_manifest.py 生成，不手工编辑。
#pragma once
#include <VPF/PatchManifest.hpp>
#include <UserVideoDecodeData.hpp>
#include <UserSequoiaVideoData.hpp>
#include <UserSonomaVideoData.hpp>
#include <UserSurfaceSyncData.hpp>
#include <UserSurfaceConstructorData.hpp>
#include <UserSequoiaSurfaceData.hpp>
#include <UserSonomaSurfaceData.hpp>

namespace VPF
{
namespace ManifestData
{

inline constexpr auto Sites_Vcn2Decode_Darwin23_15E7 = makeSites(UserSonomaVideoData::Patches15E7, UserSonomaVideoData::ResolverLocations);
inline constexpr auto Sites_Vcn2Decode_Darwin23_1636 = makeSites(UserSonomaVideoData::Patches1636, UserSonomaVideoData::ResolverLocations);
inline constexpr auto Sites_Vcn2Decode_Darwin23_1638 = makeSites(UserSonomaVideoData::Patches, UserSonomaVideoData::ResolverLocations);
inline constexpr auto Sites_Vcn2Decode_Darwin23_164C = makeSites(UserSonomaVideoData::Patches164C, UserSonomaVideoData::ResolverLocations);
inline constexpr auto Sites_Vcn2Decode_Darwin24_15E7 = makeSites(UserSequoiaVideoData::Patches15E7, UserSequoiaVideoData::ResolverLocations);
inline constexpr auto Sites_Vcn2Decode_Darwin24_1636 = makeSites(UserSequoiaVideoData::Patches1636, UserSequoiaVideoData::ResolverLocations);
inline constexpr auto Sites_Vcn2Decode_Darwin24_1638 = makeSites(UserSequoiaVideoData::Patches, UserSequoiaVideoData::ResolverLocations);
inline constexpr auto Sites_Vcn2Decode_Darwin24_164C = makeSites(UserSequoiaVideoData::Patches164C, UserSequoiaVideoData::ResolverLocations);
inline constexpr auto Sites_Vcn2Decode_Darwin25_15E7 = makeSites(UserVideoDecodeData::Patches15E7, UserVideoDecodeData::ResolverLocations);
inline constexpr auto Sites_Vcn2Decode_Darwin25_1636 = makeSites(UserVideoDecodeData::Patches1636, UserVideoDecodeData::ResolverLocations);
inline constexpr auto Sites_Vcn2Decode_Darwin25_1638 = makeSites(UserVideoDecodeData::Patches, UserVideoDecodeData::ResolverLocations);
inline constexpr auto Sites_Vcn2Decode_Darwin25_164C = makeSites(UserVideoDecodeData::Patches164C, UserVideoDecodeData::ResolverLocations);
inline constexpr auto Sites_DisplaySRD_Darwin23_1638 = makeSites(UserSonomaSurfaceData::Patches1638, UserSonomaSurfaceData::ResolverLocations);
inline constexpr auto Sites_DisplaySRD_Darwin23_15D8 = makeSites(UserSonomaSurfaceData::Patches15D8, UserSonomaSurfaceData::ResolverLocations);
inline constexpr auto Sites_DisplaySRD_Darwin23_15DD = makeSites(UserSonomaSurfaceData::Patches15DD, UserSonomaSurfaceData::ResolverLocations);
inline constexpr auto Sites_DisplaySRD_Darwin23_15E7 = makeSites(UserSonomaSurfaceData::Patches15E7, UserSonomaSurfaceData::ResolverLocations);
inline constexpr auto Sites_DisplaySRD_Darwin23_1636 = makeSites(UserSonomaSurfaceData::Patches1636, UserSonomaSurfaceData::ResolverLocations);
inline constexpr auto Sites_DisplaySRD_Darwin23_164C = makeSites(UserSonomaSurfaceData::Patches164C, UserSonomaSurfaceData::ResolverLocations);
inline constexpr auto Sites_DisplaySRD_Darwin24_1638 = makeSites(UserSequoiaSurfaceData::Patches1638, UserSequoiaSurfaceData::ResolverLocations);
inline constexpr auto Sites_DisplaySRD_Darwin24_15D8 = makeSites(UserSequoiaSurfaceData::Patches15D8, UserSequoiaSurfaceData::ResolverLocations);
inline constexpr auto Sites_DisplaySRD_Darwin24_15DD = makeSites(UserSequoiaSurfaceData::Patches15DD, UserSequoiaSurfaceData::ResolverLocations);
inline constexpr auto Sites_DisplaySRD_Darwin24_15E7 = makeSites(UserSequoiaSurfaceData::Patches15E7, UserSequoiaSurfaceData::ResolverLocations);
inline constexpr auto Sites_DisplaySRD_Darwin24_1636 = makeSites(UserSequoiaSurfaceData::Patches1636, UserSequoiaSurfaceData::ResolverLocations);
inline constexpr auto Sites_DisplaySRD_Darwin24_164C = makeSites(UserSequoiaSurfaceData::Patches164C, UserSequoiaSurfaceData::ResolverLocations);
inline constexpr SiteArray<PatchSite, 1> Sites_DisplayPipelineConstructor_Darwin23 = {{makeSites(UserSonomaSurfaceData::Patches1638, UserSonomaSurfaceData::ResolverLocations).sites[2]}};
inline constexpr UserCacheResolver::Location Locations_Sites_DisplayPipelineConstructor_Darwin23[] = {UserSonomaSurfaceData::ResolverLocations[2]};
inline constexpr auto Sites_DisplayPipelineConstructor_Darwin24_25 = makeSites(UserSurfaceConstructorData::Patches, UserSurfaceConstructorData::ResolverLocations);
inline constexpr auto Sites_DisplaySRD_Darwin25 = makeSites(UserSurfaceSyncData::SrdSharedPatches);
inline constexpr auto Sites_DisplayLegacyBlend_Darwin25 = makeSites(UserSurfaceSyncData::LegacyBlendPatches);

inline constexpr FeatureManifest Features[] = {
    {FeatureId::Vcn2Decode, OsDarwin23, 0x15E7, EvidenceKind::RelocationAwareFullFunction,
     {UserSonomaVideoData::ResolverImages, 3, UserSonomaVideoData::ResolverFunctions, 19, UserSonomaVideoData::ResolverLocations, 23, Sites_Vcn2Decode_Darwin23_15E7.sites, 23},
     "UserSonomaVideoData.hpp", "4C1441FD8A543DC2863D49110B946A25"},
    {FeatureId::Vcn2Decode, OsDarwin23, 0x1636, EvidenceKind::RelocationAwareFullFunction,
     {UserSonomaVideoData::ResolverImages, 3, UserSonomaVideoData::ResolverFunctions, 19, UserSonomaVideoData::ResolverLocations, 23, Sites_Vcn2Decode_Darwin23_1636.sites, 23},
     "UserSonomaVideoData.hpp", "4C1441FD8A543DC2863D49110B946A25"},
    {FeatureId::Vcn2Decode, OsDarwin23, 0x1638, EvidenceKind::RelocationAwareFullFunction,
     {UserSonomaVideoData::ResolverImages, 3, UserSonomaVideoData::ResolverFunctions, 19, UserSonomaVideoData::ResolverLocations, 23, Sites_Vcn2Decode_Darwin23_1638.sites, 23},
     "UserSonomaVideoData.hpp", "4C1441FD8A543DC2863D49110B946A25"},
    {FeatureId::Vcn2Decode, OsDarwin23, 0x164C, EvidenceKind::RelocationAwareFullFunction,
     {UserSonomaVideoData::ResolverImages, 3, UserSonomaVideoData::ResolverFunctions, 19, UserSonomaVideoData::ResolverLocations, 23, Sites_Vcn2Decode_Darwin23_164C.sites, 23},
     "UserSonomaVideoData.hpp", "4C1441FD8A543DC2863D49110B946A25"},
    {FeatureId::Vcn2Decode, OsDarwin24, 0x15E7, EvidenceKind::RelocationAwareFullFunction,
     {UserSequoiaVideoData::ResolverImages, 3, UserSequoiaVideoData::ResolverFunctions, 19, UserSequoiaVideoData::ResolverLocations, 23, Sites_Vcn2Decode_Darwin24_15E7.sites, 23},
     "UserSequoiaVideoData.hpp", "42EC2890BC40317EBB2745142BFF7F26"},
    {FeatureId::Vcn2Decode, OsDarwin24, 0x1636, EvidenceKind::RelocationAwareFullFunction,
     {UserSequoiaVideoData::ResolverImages, 3, UserSequoiaVideoData::ResolverFunctions, 19, UserSequoiaVideoData::ResolverLocations, 23, Sites_Vcn2Decode_Darwin24_1636.sites, 23},
     "UserSequoiaVideoData.hpp", "42EC2890BC40317EBB2745142BFF7F26"},
    {FeatureId::Vcn2Decode, OsDarwin24, 0x1638, EvidenceKind::RelocationAwareFullFunction,
     {UserSequoiaVideoData::ResolverImages, 3, UserSequoiaVideoData::ResolverFunctions, 19, UserSequoiaVideoData::ResolverLocations, 23, Sites_Vcn2Decode_Darwin24_1638.sites, 23},
     "UserSequoiaVideoData.hpp", "42EC2890BC40317EBB2745142BFF7F26"},
    {FeatureId::Vcn2Decode, OsDarwin24, 0x164C, EvidenceKind::RelocationAwareFullFunction,
     {UserSequoiaVideoData::ResolverImages, 3, UserSequoiaVideoData::ResolverFunctions, 19, UserSequoiaVideoData::ResolverLocations, 23, Sites_Vcn2Decode_Darwin24_164C.sites, 23},
     "UserSequoiaVideoData.hpp", "42EC2890BC40317EBB2745142BFF7F26"},
    {FeatureId::Vcn2Decode, OsDarwin25, 0x15E7, EvidenceKind::RelocationAwareFullFunction,
     {UserVideoDecodeData::ResolverImages, 3, UserVideoDecodeData::ResolverFunctions, 19, UserVideoDecodeData::ResolverLocations, 23, Sites_Vcn2Decode_Darwin25_15E7.sites, 23},
     "UserVideoDecodeData.hpp", "21FB8708FC8E3765BE8C65D612CDF049"},
    {FeatureId::Vcn2Decode, OsDarwin25, 0x1636, EvidenceKind::RelocationAwareFullFunction,
     {UserVideoDecodeData::ResolverImages, 3, UserVideoDecodeData::ResolverFunctions, 19, UserVideoDecodeData::ResolverLocations, 23, Sites_Vcn2Decode_Darwin25_1636.sites, 23},
     "UserVideoDecodeData.hpp", "21FB8708FC8E3765BE8C65D612CDF049"},
    {FeatureId::Vcn2Decode, OsDarwin25, 0x1638, EvidenceKind::RelocationAwareFullFunction,
     {UserVideoDecodeData::ResolverImages, 3, UserVideoDecodeData::ResolverFunctions, 19, UserVideoDecodeData::ResolverLocations, 23, Sites_Vcn2Decode_Darwin25_1638.sites, 23},
     "UserVideoDecodeData.hpp", "21FB8708FC8E3765BE8C65D612CDF049"},
    {FeatureId::Vcn2Decode, OsDarwin25, 0x164C, EvidenceKind::RelocationAwareFullFunction,
     {UserVideoDecodeData::ResolverImages, 3, UserVideoDecodeData::ResolverFunctions, 19, UserVideoDecodeData::ResolverLocations, 23, Sites_Vcn2Decode_Darwin25_164C.sites, 23},
     "UserVideoDecodeData.hpp", "21FB8708FC8E3765BE8C65D612CDF049"},
    {FeatureId::DisplaySRD, OsDarwin23, 0x1638, EvidenceKind::RelocationAwareFullFunction,
     {UserSonomaSurfaceData::ResolverImages, 5, UserSonomaSurfaceData::ResolverFunctions, 5, UserSonomaSurfaceData::ResolverLocations, 4, Sites_DisplaySRD_Darwin23_1638.sites, 4},
     "UserSonomaSurfaceData.hpp", "4C1441FD8A543DC2863D49110B946A25"},
    {FeatureId::DisplaySRD, OsDarwin23, 0x15D8, EvidenceKind::RelocationAwareFullFunction,
     {UserSonomaSurfaceData::ResolverImages, 5, UserSonomaSurfaceData::ResolverFunctions, 5, UserSonomaSurfaceData::ResolverLocations, 4, Sites_DisplaySRD_Darwin23_15D8.sites, 4},
     "UserSonomaSurfaceData.hpp", "4C1441FD8A543DC2863D49110B946A25"},
    {FeatureId::DisplaySRD, OsDarwin23, 0x15DD, EvidenceKind::RelocationAwareFullFunction,
     {UserSonomaSurfaceData::ResolverImages, 5, UserSonomaSurfaceData::ResolverFunctions, 5, UserSonomaSurfaceData::ResolverLocations, 4, Sites_DisplaySRD_Darwin23_15DD.sites, 4},
     "UserSonomaSurfaceData.hpp", "4C1441FD8A543DC2863D49110B946A25"},
    {FeatureId::DisplaySRD, OsDarwin23, 0x15E7, EvidenceKind::RelocationAwareFullFunction,
     {UserSonomaSurfaceData::ResolverImages, 5, UserSonomaSurfaceData::ResolverFunctions, 5, UserSonomaSurfaceData::ResolverLocations, 4, Sites_DisplaySRD_Darwin23_15E7.sites, 4},
     "UserSonomaSurfaceData.hpp", "4C1441FD8A543DC2863D49110B946A25"},
    {FeatureId::DisplaySRD, OsDarwin23, 0x1636, EvidenceKind::RelocationAwareFullFunction,
     {UserSonomaSurfaceData::ResolverImages, 5, UserSonomaSurfaceData::ResolverFunctions, 5, UserSonomaSurfaceData::ResolverLocations, 4, Sites_DisplaySRD_Darwin23_1636.sites, 4},
     "UserSonomaSurfaceData.hpp", "4C1441FD8A543DC2863D49110B946A25"},
    {FeatureId::DisplaySRD, OsDarwin23, 0x164C, EvidenceKind::RelocationAwareFullFunction,
     {UserSonomaSurfaceData::ResolverImages, 5, UserSonomaSurfaceData::ResolverFunctions, 5, UserSonomaSurfaceData::ResolverLocations, 4, Sites_DisplaySRD_Darwin23_164C.sites, 4},
     "UserSonomaSurfaceData.hpp", "4C1441FD8A543DC2863D49110B946A25"},
    {FeatureId::DisplaySRD, OsDarwin24, 0x1638, EvidenceKind::RelocationAwareFullFunction,
     {UserSequoiaSurfaceData::ResolverImages, 5, UserSequoiaSurfaceData::ResolverFunctions, 5, UserSequoiaSurfaceData::ResolverLocations, 4, Sites_DisplaySRD_Darwin24_1638.sites, 4},
     "UserSequoiaSurfaceData.hpp", "42EC2890BC40317EBB2745142BFF7F26"},
    {FeatureId::DisplaySRD, OsDarwin24, 0x15D8, EvidenceKind::RelocationAwareFullFunction,
     {UserSequoiaSurfaceData::ResolverImages, 5, UserSequoiaSurfaceData::ResolverFunctions, 5, UserSequoiaSurfaceData::ResolverLocations, 4, Sites_DisplaySRD_Darwin24_15D8.sites, 4},
     "UserSequoiaSurfaceData.hpp", "42EC2890BC40317EBB2745142BFF7F26"},
    {FeatureId::DisplaySRD, OsDarwin24, 0x15DD, EvidenceKind::RelocationAwareFullFunction,
     {UserSequoiaSurfaceData::ResolverImages, 5, UserSequoiaSurfaceData::ResolverFunctions, 5, UserSequoiaSurfaceData::ResolverLocations, 4, Sites_DisplaySRD_Darwin24_15DD.sites, 4},
     "UserSequoiaSurfaceData.hpp", "42EC2890BC40317EBB2745142BFF7F26"},
    {FeatureId::DisplaySRD, OsDarwin24, 0x15E7, EvidenceKind::RelocationAwareFullFunction,
     {UserSequoiaSurfaceData::ResolverImages, 5, UserSequoiaSurfaceData::ResolverFunctions, 5, UserSequoiaSurfaceData::ResolverLocations, 4, Sites_DisplaySRD_Darwin24_15E7.sites, 4},
     "UserSequoiaSurfaceData.hpp", "42EC2890BC40317EBB2745142BFF7F26"},
    {FeatureId::DisplaySRD, OsDarwin24, 0x1636, EvidenceKind::RelocationAwareFullFunction,
     {UserSequoiaSurfaceData::ResolverImages, 5, UserSequoiaSurfaceData::ResolverFunctions, 5, UserSequoiaSurfaceData::ResolverLocations, 4, Sites_DisplaySRD_Darwin24_1636.sites, 4},
     "UserSequoiaSurfaceData.hpp", "42EC2890BC40317EBB2745142BFF7F26"},
    {FeatureId::DisplaySRD, OsDarwin24, 0x164C, EvidenceKind::RelocationAwareFullFunction,
     {UserSequoiaSurfaceData::ResolverImages, 5, UserSequoiaSurfaceData::ResolverFunctions, 5, UserSequoiaSurfaceData::ResolverLocations, 4, Sites_DisplaySRD_Darwin24_164C.sites, 4},
     "UserSequoiaSurfaceData.hpp", "42EC2890BC40317EBB2745142BFF7F26"},
    {FeatureId::DisplayComputeScratch, OsDarwin23, 0x0000, EvidenceKind::RelocationAwareFullFunction,
     {UserSonomaSurfaceData::ResolverImages, 5, UserSonomaSurfaceData::ResolverFunctions, 5, UserSonomaSurfaceData::ResolverLocations, 4, nullptr, 0},
     "UserSonomaSurfaceData.hpp", "4C1441FD8A543DC2863D49110B946A25"},
    {FeatureId::DisplayPipelineConstructor, OsDarwin23, 0x0000, EvidenceKind::RelocationAwareFullFunction,
     {UserSonomaSurfaceData::ResolverImages, 5, UserSonomaSurfaceData::ResolverFunctions, 5, Locations_Sites_DisplayPipelineConstructor_Darwin23, 1, Sites_DisplayPipelineConstructor_Darwin23.sites, 1},
     "UserSonomaSurfaceData.hpp", "4C1441FD8A543DC2863D49110B946A25"},
    {FeatureId::DisplaySubmit, OsDarwin23, 0x0000, EvidenceKind::RelocationAwareFullFunction,
     {UserSonomaSurfaceData::Submit::ResolverImages, 1, UserSonomaSurfaceData::Submit::ResolverFunctions, 1, nullptr, 0, nullptr, 0},
     "UserSonomaSurfaceData.hpp", "4C1441FD8A543DC2863D49110B946A25"},
    {FeatureId::DisplayComputeScratch, OsDarwin24 | OsDarwin25, 0x0000, EvidenceKind::RelocationAwareFullFunction,
     {UserSurfaceSyncData::ScratchResolverData::ResolverImages, 4, UserSurfaceSyncData::ScratchResolverData::ResolverFunctions, 1, UserSurfaceSyncData::ScratchResolverData::ResolverLocations, 1, nullptr, 0},
     "UserSurfaceSyncData.hpp", "21FB8708FC8E3765BE8C65D612CDF049"},
    {FeatureId::DisplayPipelineConstructor, OsDarwin24 | OsDarwin25, 0x0000, EvidenceKind::RelocationAwareFullFunction,
     {UserSurfaceConstructorData::ResolverImages, 4, UserSurfaceConstructorData::ResolverFunctions, 1, UserSurfaceConstructorData::ResolverLocations, 1, Sites_DisplayPipelineConstructor_Darwin24_25.sites, 1},
     "UserSurfaceConstructorData.hpp", nullptr},
    {FeatureId::DisplaySubmit, OsDarwin24 | OsDarwin25, 0x0000, EvidenceKind::RelocationAwareFullFunction,
     {UserSurfaceConstructorData::Submit::ResolverImages, 1, UserSurfaceConstructorData::Submit::ResolverFunctions, 1, nullptr, 0, nullptr, 0},
     "UserSurfaceConstructorData.hpp", nullptr},
    {FeatureId::DisplaySRD, OsDarwin25, 0x0000, EvidenceKind::ExactValidatedBuild,
     {nullptr, 0, nullptr, 0, nullptr, 0, Sites_DisplaySRD_Darwin25.sites, 2},
     "UserSurfaceSyncData.hpp", "21FB8708FC8E3765BE8C65D612CDF049"},
    {FeatureId::DisplayLegacyBlend, OsDarwin25, 0x0000, EvidenceKind::ExactValidatedBuild,
     {nullptr, 0, nullptr, 0, nullptr, 0, Sites_DisplayLegacyBlend_Darwin25.sites, 3},
     "UserSurfaceSyncData.hpp", "21FB8708FC8E3765BE8C65D612CDF049"},
};
inline constexpr UInt32 FeatureCount = sizeof(Features) / sizeof(Features[0]);
static_assert(FeatureCount <= 64);
static_assert(validAll(Features));

inline constexpr const FeatureManifest* find(FeatureId feature, UInt32 osMask, UInt32 deviceID = 0)
{
    return VPF::find(Features, FeatureCount, feature, osMask, deviceID);
}

}    // namespace ManifestData
}    // namespace VPF
