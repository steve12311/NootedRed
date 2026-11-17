// Darwin 24/25 仅在此选择参考数据；解析、核验和安装流程由公共实现负责。
#pragma once
#include <UserSequoiaSurfaceData.hpp>
#include <UserSequoiaVideoData.hpp>
#include <UserSurfaceConstructorData.hpp>
#include <UserSurfaceSyncData.hpp>
#include <UserVideoDecodeData.hpp>

namespace UserPatchProfiles
{

    struct Sequoia
    {
        static inline constexpr auto& Original0         = UserSequoiaSurfaceData::Original0;
        static inline constexpr auto& Original1         = UserSequoiaSurfaceData::Original1;
        static inline constexpr auto& Patched1          = UserSequoiaSurfaceData::Patched1;
        static inline constexpr auto& NativeOverride    = UserSequoiaSurfaceData::ResolverOriginal4;
        static inline constexpr auto& BlendConstructor  = UserSequoiaSurfaceData::ResolverOriginal2;
        static inline constexpr auto& BlendGateOriginal = UserSequoiaSurfaceData::Original2;
        static inline constexpr auto& BlendGatePatched  = UserSequoiaSurfaceData::Patched2;
        static inline constexpr UserSurfaceSyncData::RelativeField OriginalInitLinks[] = {
            {UserSequoiaSurfaceData::ResolverOriginalRefs0[0].offset, 0},
            {UserSequoiaSurfaceData::ResolverOriginalRefs0[1].offset, 1},
            {UserSequoiaSurfaceData::ResolverOriginalRefs0[2].offset, 2},
        };
        static inline constexpr UserSurfaceSyncData::RelativeField SharedInitLinks[] = {
            {UserSequoiaSurfaceData::ResolverPatchedRefs0[0].offset, 0},
            {UserSequoiaSurfaceData::ResolverPatchedRefs0[1].offset, 2},
            {UserSequoiaSurfaceData::ResolverPatchedRefs0[2].offset, 1},
        };
        static inline constexpr auto& ImmediateInitLinks = SharedInitLinks;
        static inline constexpr auto& SurfaceImages      = UserSequoiaSurfaceData::ResolverImages;
        static inline constexpr auto& ConstructorImages  = UserSequoiaSurfaceData::ResolverImages;
        static inline constexpr auto& VideoImages        = UserSequoiaVideoData::ResolverImages;
        static inline constexpr auto& VideoFunctions     = UserSequoiaVideoData::ResolverFunctions;
        static inline constexpr auto& VideoLocations     = UserSequoiaVideoData::ResolverLocations;
        static inline constexpr auto& SubmitImages       = UserSequoiaSurfaceData::Submit::ResolverImages;
        static inline constexpr auto& SubmitFunction     = UserSequoiaSurfaceData::Submit::ResolverFunctions[0];
        static inline constexpr auto  OverrideEntryInInit =
            UserSequoiaSurfaceData::ResolverPatchedRefs1[0].rva - UserSequoiaSurfaceData::ResolverFunctions[0].rva;
        static inline constexpr auto BlendGateInConstructor = UserSequoiaSurfaceData::ResolverLocations[2].offset;
        static inline constexpr auto InitRVA                = UserSequoiaSurfaceData::ResolverFunctions[0].rva;
        static inline constexpr auto OverrideRVA            = UserSequoiaSurfaceData::ResolverFunctions[1].rva;
        static inline constexpr auto NativeOverrideRVA      = UserSequoiaSurfaceData::ResolverFunctions[4].rva;
        static inline constexpr auto ConstructorRVA         = UserSequoiaSurfaceData::ResolverFunctions[2].rva;
        static inline constexpr auto FairPlayDecodeMask     = UserSequoiaVideoData::FairPlayDecodeMask;
        static inline constexpr UserCacheResolver::Function ScratchFunctions[] = {
            UserSequoiaSurfaceData::ResolverFunctions[3]};
        static inline constexpr UserCacheResolver::Function ConstructorFunctions[] = {
            UserSequoiaSurfaceData::ResolverFunctions[2]};
        static inline constexpr UserCacheResolver::Location ScratchLocations[]     = {{0, 0}};
        static inline constexpr UserCacheResolver::Location ConstructorLocations[] = {{0, BlendGateInConstructor}};
        static inline constexpr UserSurfaceSyncData::Patch  ConstructorPatches[]   = {
            UserSequoiaSurfaceData::Patches1638[2]};
        static inline constexpr const UserCacheSectionData::Target* SurfaceTargets =
            UserSequoiaSurfaceData::SectionTargets;
        static inline constexpr UInt32 SurfaceTargetCount =
            sizeof(UserSequoiaSurfaceData::SectionTargets) / sizeof(UserSequoiaSurfaceData::SectionTargets[0]);
        static inline constexpr const UserCacheSectionData::Target* VideoTargets = UserSequoiaVideoData::SectionTargets;
        static inline constexpr UInt32                              VideoTargetCount =
            sizeof(UserSequoiaVideoData::SectionTargets) / sizeof(UserSequoiaVideoData::SectionTargets[0]);
        static inline constexpr const UserCacheSectionData::Target* SubmitTargets =
            UserSequoiaSurfaceData::Submit::SectionTargets;
        static inline constexpr UInt32 SubmitTargetCount = sizeof(UserSequoiaSurfaceData::Submit::SectionTargets)
                                                           / sizeof(UserSequoiaSurfaceData::Submit::SectionTargets[0]);
        static const UserVideoDecodeData::Patch* videoDevice(UInt32 id) { return UserSequoiaVideoData::findDevice(id); }
        static bool                              surfaceDevice(UInt32 id, UserSurfaceSyncData::DevicePatchSet& device)
        {
            const auto* source = UserSequoiaSurfaceData::findDevice(id);
            if (!source) { return false; }
            device = {id, source, source, source, source};
            return true;
        }
    };

    struct Tahoe
    {
        static inline constexpr auto& Original0              = UserSurfaceSyncData::Original0;
        static inline constexpr auto& Original1              = UserSurfaceSyncData::Original1;
        static inline constexpr auto& Patched1               = UserSurfaceSyncData::Patched1;
        static inline constexpr auto& NativeOverride         = UserSurfaceSyncData::NativeOverride;
        static inline constexpr auto& BlendConstructor       = UserSurfaceSyncData::BlendConstructor;
        static inline constexpr auto& BlendGateOriginal      = UserSurfaceSyncData::BlendGateOriginal;
        static inline constexpr auto& BlendGatePatched       = UserSurfaceSyncData::BlendGatePatched;
        static inline constexpr auto& OriginalInitLinks      = UserSurfaceSyncData::OriginalInitLinks;
        static inline constexpr auto& ImmediateInitLinks     = UserSurfaceSyncData::ImmediateInitLinks;
        static inline constexpr auto& SharedInitLinks        = UserSurfaceSyncData::SharedInitLinks;
        static inline constexpr auto& SurfaceImages          = UserSurfaceSyncData::ScratchResolverData::ResolverImages;
        static inline constexpr auto& ConstructorImages      = UserSurfaceConstructorData::ResolverImages;
        static inline constexpr auto& VideoImages            = UserVideoDecodeData::ResolverImages;
        static inline constexpr auto& VideoFunctions         = UserVideoDecodeData::ResolverFunctions;
        static inline constexpr auto& VideoLocations         = UserVideoDecodeData::ResolverLocations;
        static inline constexpr auto& SubmitImages           = UserSurfaceConstructorData::Submit::ResolverImages;
        static inline constexpr auto& SubmitFunction         = UserSurfaceConstructorData::Submit::ResolverFunctions[0];
        static inline constexpr auto  OverrideEntryInInit    = UserSurfaceSyncData::OverrideEntryInInit;
        static inline constexpr auto  BlendGateInConstructor = UserSurfaceSyncData::BlendGateInConstructor;
        static inline constexpr auto  InitRVA                = UserSurfaceSyncData::InitRVA;
        static inline constexpr auto  OverrideRVA            = UserSurfaceSyncData::OverrideRVA;
        static inline constexpr auto  NativeOverrideRVA      = UserSurfaceSyncData::NativeOverrideRVA;
        static inline constexpr auto  ConstructorRVA         = UserSurfaceSyncData::ConstructorRVA;
        static inline constexpr auto  FairPlayDecodeMask     = UserVideoDecodeData::FairPlayDecodeMask;
        static inline constexpr UserCacheResolver::Function ScratchFunctions[] = {
            UserSurfaceSyncData::ScratchResolverData::ResolverFunctions[0]};
        static inline constexpr UserCacheResolver::Function ConstructorFunctions[] = {
            UserSurfaceConstructorData::ResolverFunctions[0]};
        static inline constexpr UserCacheResolver::Location ScratchLocations[]     = {{0, 0}};
        static inline constexpr UserCacheResolver::Location ConstructorLocations[] = {{0, BlendGateInConstructor}};
        static inline constexpr UserSurfaceSyncData::Patch  ConstructorPatches[]   = {
            UserSurfaceConstructorData::Patches[0]};
        static inline constexpr const UserCacheSectionData::Target* SurfaceTargets     = nullptr;
        static inline constexpr UInt32                              SurfaceTargetCount = 0;
        static inline constexpr const UserCacheSectionData::Target* VideoTargets       = nullptr;
        static inline constexpr UInt32                              VideoTargetCount   = 0;
        static inline constexpr const UserCacheSectionData::Target* SubmitTargets      = nullptr;
        static inline constexpr UInt32                              SubmitTargetCount  = 0;
        static const UserVideoDecodeData::Patch* videoDevice(UInt32 id) { return UserVideoDecodeData::findDevice(id); }
        static bool                              surfaceDevice(UInt32 id, UserSurfaceSyncData::DevicePatchSet& device)
        {
            const auto* source = UserSurfaceSyncData::findDevice(id);
            if (!source) { return false; }
            device = *source;
            return true;
        }
    };

    template<typename Callback>
    inline bool dispatch(UInt32 major, const Callback& callback)
    {
        if (major == 24) { return callback(Sequoia{}); }
        if (major == 25) { return callback(Tahoe{}); }
        return false;
    }

}    // namespace UserPatchProfiles
