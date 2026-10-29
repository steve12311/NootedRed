// 每次页交付/共享缓存事务的显示准入；证据和状态不跨调用、进程或地址空间复用。
#pragma once
#include <VPF/DisplayCapabilityState.hpp>

namespace VPF
{

    constexpr UInt32 MetalDisplayGroups = displayGroupBit(DisplayGroup::MetalRenderCore)
                                          | displayGroupBit(DisplayGroup::MetalScratch)
                                          | displayGroupBit(DisplayGroup::MetalSubmitSafety);
    constexpr UInt32 OpenGLDisplayGroups =
        displayGroupBit(DisplayGroup::GLHwInfo) | displayGroupBit(DisplayGroup::GLDualQuad);

    inline UInt32 displayFeaturesForGroups(const UInt32 groups, const UInt32 osMask, const UInt32 deviceID)
    {
        UInt32 features = 0;
        for (const auto& group : DefaultDisplayGroups) {
            if ((groups & displayGroupBit(group.group)) == 0) { continue; }
            for (const auto& feature : DefaultFeatures) {
                if ((group.features & featureBit(feature.feature)) != 0
                    && DisplayPlannerDetail::applicable(feature, osMask, deviceID))
                {
                    features |= featureBit(feature.feature);
                }
            }
        }
        return features;
    }

    template<UInt32 Capacity = FeatureCount>
    class DisplayRuntime
    {
        CapabilityLedgerStorage<Capacity> ledger_{};
        DisplayPlannerInput               input_{};
        DisplayGroupResult                result_{};
        UInt64                            map_{0};
        UInt32                            features_{0};
        bool                              started_{false};

    public:
        DisplayRuntime()                                 = default;
        DisplayRuntime(const DisplayRuntime&)            = delete;
        DisplayRuntime& operator=(const DisplayRuntime&) = delete;

        __attribute__((noinline)) void reset(const UInt64 map, const UInt32 major, const UInt32 deviceID,
                                             const UInt32 requestedGroups)
        {
            ledger_.clear();
            map_   = map;
            input_ = {displayOsMask(major), deviceID, requestedGroups, 0, 0, false};
            DisplayPlannerDetail::GroupClosure closure{DefaultDisplayGroups,
                                                       sizeof(DefaultDisplayGroups) / sizeof(DefaultDisplayGroups[0])};
            for (UInt32 i = 0; i < DisplayGroupCount; ++i) {
                if ((requestedGroups & displayGroupBit(static_cast<DisplayGroup>(i))) != 0) {
                    closure.resolve(static_cast<DisplayGroup>(i));
                }
            }
            features_ = displayFeaturesForGroups(closure.closed, input_.osMask, deviceID);
            result_   = {};
            started_  = false;
        }

        // 只在对应后端完成完整计划/入口读回后提供证据。
        __attribute__((noinline)) bool verify(const UInt32 features, const UInt32 external)
        {
            if (started_) { return false; }
            input_.verified |= features & features_;
            input_.external |= external;
            result_          = evaluateDisplayGroups(input_, DefaultDisplayGroups,
                                                     sizeof(DefaultDisplayGroups) / sizeof(DefaultDisplayGroups[0]));
            return !result_.cycle && input_.requestedGroups != 0
                   && (result_.enabled & input_.requestedGroups) == input_.requestedGroups;
        }

        __attribute__((noinline)) bool begin()
        {
            if (started_ || !map_ || result_.cycle || input_.requestedGroups == 0
                || (result_.enabled & input_.requestedGroups) != input_.requestedGroups)
            {
                return false;
            }
            // 安装前按规划器顺序推进；同一 Metal 原子事务可涵盖多个组。
            for (UInt32 i = 0; i < result_.orderCount; ++i) {
                const auto                group    = result_.order[i];
                constexpr CapabilityState stages[] = {CapabilityState::Eligible, CapabilityState::ImageResolved,
                                                      CapabilityState::Verified, CapabilityState::Prepared};
                for (const auto state : stages) {
                    if (!markDisplayGroup(ledger_, map_, group, state, features_)) { return false; }
                }
                if (state(group) != DisplayGroupState::InstallEligible) { return false; }
                if (!markDisplayGroup(ledger_, map_, group, CapabilityState::Installing, features_)) { return false; }
            }
            started_ = true;
            return true;
        }

        bool installed(const UInt32 features)
        {
            if (!started_) { return false; }
            bool ok = true;
            for (UInt32 i = 0; i < FeatureCount; ++i) {
                const auto feature = static_cast<FeatureId>(i);
                if ((features & features_ & featureBit(feature)) != 0) {
                    ok &= ledger_.set(map_, feature, CapabilityState::Installed);
                }
            }
            return ok;
        }

        void failed(const bool rollbackOk)
        {
            for (UInt32 i = 0; i < FeatureCount; ++i) {
                const auto feature = static_cast<FeatureId>(i);
                if (ledger_.state(map_, feature) != CapabilityState::Installing) { continue; }
                ledger_.set(map_, feature, CapabilityState::CommitFailed);
                ledger_.set(map_, feature,
                            rollbackOk ? CapabilityState::RollbackVerified : CapabilityState::RollbackFailed);
                if (!rollbackOk) { ledger_.set(map_, feature, CapabilityState::Quarantined); }
            }
        }

        DisplayGroupState state(const DisplayGroup group) const
        {
            return displayGroupState(ledger_, map_, group, features_);
        }
        bool ready() const
        {
            if (!started_) { return false; }
            for (UInt32 i = 0; i < result_.orderCount; ++i) {
                const auto current = state(result_.order[i]);
                if (current != DisplayGroupState::Installing && current != DisplayGroupState::Installed) {
                    return false;
                }
            }
            return true;
        }
        UInt32                    features() const { return features_; }
        const DisplayGroupResult& result() const { return result_; }
    };

}    // namespace VPF
