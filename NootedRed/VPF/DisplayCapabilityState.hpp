// DVPF：共享 CapabilityLedger 的显示组视图；不另建第二套地址空间账本。
#pragma once
#include <VPF/CapabilityState.hpp>
#include <VPF/DisplayDependencyPlanner.hpp>

namespace VPF
{

    // HardwareValidated 只能由实机报告给出，运行时状态机不产生该值。
    enum class DisplayGroupState : UInt8
    {
        Disabled,
        Candidate,
        Resolved,
        Verified,
        InstallEligible,
        Installing,
        Installed,
        RuntimeObserved,
        CommitFailed,
        Rejected,
        Unsupported,
        RollbackComplete,
        RollbackFailed,
        Quarantined,
    };

    constexpr const char* displayGroupStateName(const DisplayGroupState state)
    {
        switch (state) {
            case DisplayGroupState::Disabled        : return "Disabled";
            case DisplayGroupState::Candidate       : return "Candidate";
            case DisplayGroupState::Resolved        : return "Resolved";
            case DisplayGroupState::Verified        : return "Verified";
            case DisplayGroupState::InstallEligible : return "InstallEligible";
            case DisplayGroupState::Installing      : return "Installing";
            case DisplayGroupState::Installed       : return "Installed";
            case DisplayGroupState::RuntimeObserved : return "RuntimeObserved";
            case DisplayGroupState::CommitFailed    : return "CommitFailed";
            case DisplayGroupState::Rejected        : return "Rejected";
            case DisplayGroupState::Unsupported     : return "Unsupported";
            case DisplayGroupState::RollbackComplete: return "RollbackComplete";
            case DisplayGroupState::RollbackFailed  : return "RollbackFailed";
            case DisplayGroupState::Quarantined     : return "Quarantined";
        }
        return "Unknown";
    }

    namespace DisplayStateDetail
    {

        // 失败/不支持状态不参与“全部达到某阶段”的判断。
        constexpr UInt32 progress(const CapabilityState state)
        {
            switch (state) {
                case CapabilityState::Disabled       : return 0;
                case CapabilityState::Eligible       : return 1;
                case CapabilityState::ImageResolved  : return 2;
                case CapabilityState::Verified       : return 3;
                case CapabilityState::Prepared       : return 4;
                case CapabilityState::Installing     : return 5;
                case CapabilityState::Installed      : return 6;
                case CapabilityState::RuntimeObserved: return 7;
                default                              : return 0;
            }
        }

    }    // namespace DisplayStateDetail

    // features 限定当前 OS/设备适用的成员；缺少证据的适用成员仍参与聚合。
    template<UInt32 Capacity>
    inline DisplayGroupState displayGroupState(const CapabilityLedgerStorage<Capacity>& ledger, const UInt64 map,
                                               const DisplayGroup group, const UInt32 features = allFeatureBits)
    {
        const auto* spec = DisplayPlannerDetail::findGroup(
            DefaultDisplayGroups, static_cast<UInt32>(sizeof(DefaultDisplayGroups) / sizeof(DefaultDisplayGroups[0])),
            group);
        if (!spec) { return DisplayGroupState::Disabled; }

        bool any = false, allDisabled = true;
        bool quarantined = false, rollbackFailed = false, rollbackVerified = false;
        bool unsupported = false, rejected = false, commitFailed = false, installing = false;
        bool anyInstalled = false, allInstalled = true, anyRuntime = false;
        bool allPrepared = true, allVerified = true, allResolved = true;

        for (UInt32 i = 0; i < FeatureCount; ++i) {
            const auto feature = static_cast<FeatureId>(i);
            if ((spec->features & features & featureBit(feature)) == 0) { continue; }
            any              = true;
            const auto state = ledger.state(map, feature);
            if (state != CapabilityState::Disabled) { allDisabled = false; }
            quarantined          |= state == CapabilityState::Quarantined;
            rollbackFailed       |= state == CapabilityState::RollbackFailed;
            rollbackVerified     |= state == CapabilityState::RollbackVerified;
            unsupported          |= state == CapabilityState::Unsupported;
            rejected             |= state == CapabilityState::Rejected;
            commitFailed         |= state == CapabilityState::CommitFailed;
            installing           |= state == CapabilityState::Installing;
            const auto stage      = DisplayStateDetail::progress(state);
            const bool installed  = stage >= DisplayStateDetail::progress(CapabilityState::Installed);
            anyInstalled         |= installed;
            allInstalled         &= installed;
            anyRuntime           |= stage >= DisplayStateDetail::progress(CapabilityState::RuntimeObserved);
            allPrepared          &= stage >= DisplayStateDetail::progress(CapabilityState::Prepared);
            allVerified          &= stage >= DisplayStateDetail::progress(CapabilityState::Verified);
            allResolved          &= stage >= DisplayStateDetail::progress(CapabilityState::ImageResolved);
        }

        if (!any || allDisabled) { return DisplayGroupState::Disabled; }
        if (quarantined) { return DisplayGroupState::Quarantined; }
        if (rollbackFailed) { return DisplayGroupState::RollbackFailed; }
        if (rollbackVerified) { return DisplayGroupState::RollbackComplete; }
        if (unsupported) { return DisplayGroupState::Unsupported; }
        if (rejected) { return DisplayGroupState::Rejected; }
        if (commitFailed) { return DisplayGroupState::CommitFailed; }
        if (installing) { return DisplayGroupState::Installing; }
        if (anyRuntime && allInstalled) { return DisplayGroupState::RuntimeObserved; }
        if (anyInstalled && allInstalled) { return DisplayGroupState::Installed; }
        if (allPrepared) { return DisplayGroupState::InstallEligible; }
        if (allVerified) { return DisplayGroupState::Verified; }
        if (allResolved) { return DisplayGroupState::Resolved; }
        return DisplayGroupState::Candidate;
    }

    // 安装门与诊断共享同一份 FeatureId 账本。
    template<UInt32 Capacity>
    inline bool markDisplayGroup(CapabilityLedgerStorage<Capacity>& ledger, const UInt64 map, const DisplayGroup group,
                                 const CapabilityState state, const UInt32 features = allFeatureBits)
    {
        const auto* spec = DisplayPlannerDetail::findGroup(
            DefaultDisplayGroups, static_cast<UInt32>(sizeof(DefaultDisplayGroups) / sizeof(DefaultDisplayGroups[0])),
            group);
        if (!spec) { return false; }
        bool ok = true;
        for (UInt32 i = 0; i < FeatureCount; ++i) {
            const auto feature = static_cast<FeatureId>(i);
            if ((spec->features & features & featureBit(feature)) == 0) { continue; }
            ok &= ledger.set(map, feature, state);
        }
        return ok;
    }

}    // namespace VPF
