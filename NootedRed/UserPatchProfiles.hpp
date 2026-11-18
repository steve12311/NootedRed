// Darwin 23/24/25 仅在此选择参考数据；解析、核验和安装流程由公共实现负责。
#pragma once
#include <UserSequoiaSurfaceData.hpp>
#include <UserSequoiaVideoData.hpp>
#include <UserSonomaSurfaceData.hpp>
#include <UserSonomaVideoData.hpp>
#include <UserSurfaceConstructorData.hpp>
#include <UserSurfaceSyncData.hpp>
#include <UserVTRDDCandidateData.hpp>
#include <UserVideoDecodeData.hpp>

namespace UserPatchProfiles
{

    struct SonomaData
    {
        static inline constexpr auto& Original0          = UserSonomaSurfaceData::Original0;
        static inline constexpr auto& Original1          = UserSonomaSurfaceData::Original1;
        static inline constexpr auto& Patched1           = UserSonomaSurfaceData::Patched1;
        static inline constexpr auto& NativeOverride     = UserSonomaSurfaceData::ResolverOriginal4;
        static inline constexpr auto& BlendConstructor   = UserSonomaSurfaceData::ResolverOriginal2;
        static inline constexpr auto& BlendGateOriginal  = UserSonomaSurfaceData::Original2;
        static inline constexpr auto& BlendGatePatched   = UserSonomaSurfaceData::Patched2;
        static inline constexpr auto& SurfaceImages      = UserSonomaSurfaceData::ResolverImages;
        static inline constexpr auto& SurfaceFunctions   = UserSonomaSurfaceData::ResolverFunctions;
        static inline constexpr auto& SurfaceTargets     = UserSonomaSurfaceData::SectionTargets;
        static inline constexpr auto& SurfaceLocations   = UserSonomaSurfaceData::ResolverLocations;
        static inline constexpr auto& ConstructorPatch   = UserSonomaSurfaceData::Patches1638[2];
        static inline constexpr auto& VideoImages        = UserSonomaVideoData::ResolverImages;
        static inline constexpr auto& VideoFunctions     = UserSonomaVideoData::ResolverFunctions;
        static inline constexpr auto& VideoLocations     = UserSonomaVideoData::ResolverLocations;
        static inline constexpr auto& VideoTargets       = UserSonomaVideoData::SectionTargets;
        static inline constexpr auto  FairPlayDecodeMask = UserSonomaVideoData::FairPlayDecodeMask;
        static inline constexpr auto& SubmitImages       = UserSonomaSurfaceData::Submit::ResolverImages;
        static inline constexpr auto& SubmitFunction     = UserSonomaSurfaceData::Submit::ResolverFunctions[0];
        static inline constexpr auto& SubmitTargets      = UserSonomaSurfaceData::Submit::SectionTargets;
        static constexpr auto         videoDevice        = UserSonomaVideoData::findDevice;
        static constexpr auto         findSurfaceDevice  = UserSonomaSurfaceData::findDevice;
        using VTRDD                                      = UserVTRDDCandidateData::Sonoma::Profile;
    };

    struct SequoiaData
    {
        static inline constexpr auto& Original0          = UserSequoiaSurfaceData::Original0;
        static inline constexpr auto& Original1          = UserSequoiaSurfaceData::Original1;
        static inline constexpr auto& Patched1           = UserSequoiaSurfaceData::Patched1;
        static inline constexpr auto& NativeOverride     = UserSequoiaSurfaceData::ResolverOriginal4;
        static inline constexpr auto& BlendConstructor   = UserSequoiaSurfaceData::ResolverOriginal2;
        static inline constexpr auto& BlendGateOriginal  = UserSequoiaSurfaceData::Original2;
        static inline constexpr auto& BlendGatePatched   = UserSequoiaSurfaceData::Patched2;
        static inline constexpr auto& SurfaceImages      = UserSequoiaSurfaceData::ResolverImages;
        static inline constexpr auto& SurfaceFunctions   = UserSequoiaSurfaceData::ResolverFunctions;
        static inline constexpr auto& SurfaceTargets     = UserSequoiaSurfaceData::SectionTargets;
        static inline constexpr auto& SurfaceLocations   = UserSequoiaSurfaceData::ResolverLocations;
        static inline constexpr auto& ConstructorPatch   = UserSequoiaSurfaceData::Patches1638[2];
        static inline constexpr auto& VideoImages        = UserSequoiaVideoData::ResolverImages;
        static inline constexpr auto& VideoFunctions     = UserSequoiaVideoData::ResolverFunctions;
        static inline constexpr auto& VideoLocations     = UserSequoiaVideoData::ResolverLocations;
        static inline constexpr auto& VideoTargets       = UserSequoiaVideoData::SectionTargets;
        static inline constexpr auto  FairPlayDecodeMask = UserSequoiaVideoData::FairPlayDecodeMask;
        static inline constexpr auto& SubmitImages       = UserSequoiaSurfaceData::Submit::ResolverImages;
        static inline constexpr auto& SubmitFunction     = UserSequoiaSurfaceData::Submit::ResolverFunctions[0];
        static inline constexpr auto& SubmitTargets      = UserSequoiaSurfaceData::Submit::SectionTargets;
        static constexpr auto         videoDevice        = UserSequoiaVideoData::findDevice;
        static constexpr auto         findSurfaceDevice  = UserSequoiaSurfaceData::findDevice;
        using VTRDD                                      = UserVTRDDCandidateData::Sequoia::Profile;
    };

    template<typename Data>
    struct SectionProfile : Data
    {
        static constexpr bool                               FullDisplayReferences = true;
        static constexpr auto&                              Functions             = Data::SurfaceFunctions;
        static constexpr UserSurfaceSyncData::RelativeField OriginalInitLinks[]   = {
            {Functions[0].originalRefs[0].offset, 0},
            {Functions[0].originalRefs[1].offset, 1},
            {Functions[0].originalRefs[2].offset, 2},
        };
        static constexpr UserSurfaceSyncData::RelativeField SharedInitLinks[] = {
            {Functions[0].patchedRefs[0].offset, 0},
            {Functions[0].patchedRefs[1].offset, 2},
            {Functions[0].patchedRefs[2].offset, 1},
        };
        static constexpr auto& ImmediateInitLinks     = SharedInitLinks;
        static constexpr auto& ConstructorImages      = Data::SurfaceImages;
        static constexpr auto  OverrideEntryInInit    = Functions[1].patchedRefs[0].rva - Functions[0].rva;
        static constexpr auto  BlendGateInConstructor = Data::SurfaceLocations[2].offset;
        static constexpr auto  InitRVA                = Functions[0].rva;
        static constexpr auto  OverrideRVA            = Functions[1].rva;
        static constexpr auto  NativeOverrideRVA      = Functions[4].rva;
        static constexpr auto  ConstructorRVA         = Functions[2].rva;
        static constexpr UserCacheResolver::Function ScratchFunctions[]     = {Functions[3]};
        static constexpr UserCacheResolver::Function ConstructorFunctions[] = {Functions[2]};
        static constexpr UserCacheResolver::Location ScratchLocations[]     = {{0, 0}};
        static constexpr UserCacheResolver::Location ConstructorLocations[] = {{0, BlendGateInConstructor}};
        static constexpr UserSurfaceSyncData::Patch  ConstructorPatches[]   = {Data::ConstructorPatch};
        static constexpr UInt32 SurfaceTargetCount = sizeof(Data::SurfaceTargets) / sizeof(Data::SurfaceTargets[0]);
        static constexpr UInt32 VideoTargetCount   = sizeof(Data::VideoTargets) / sizeof(Data::VideoTargets[0]);
        static constexpr UInt32 SubmitTargetCount  = sizeof(Data::SubmitTargets) / sizeof(Data::SubmitTargets[0]);

        static bool surfaceDevice(UInt32 id, UserSurfaceSyncData::DevicePatchSet& device)
        {
            const auto* source = Data::findSurfaceDevice(id);
            if (!source) { return false; }
            device = {id, source, source, source, source};
            return true;
        }
    };

    using Sonoma  = SectionProfile<SonomaData>;
    using Sequoia = SectionProfile<SequoiaData>;

    struct Tahoe
    {
        static constexpr bool FullDisplayReferences          = false;
        using VTRDD                                          = UserVTRDDCandidateData::Tahoe::Profile;
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
        if (major == 23) { return callback(Sonoma{}); }
        if (major == 24) { return callback(Sequoia{}); }
        if (major == 25) { return callback(Tahoe{}); }
        return false;
    }

}    // namespace UserPatchProfiles
