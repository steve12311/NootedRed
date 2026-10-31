// DVPF：显示组级依赖规划；在共享 FeatureId 规划器之上提供组闭包和拒绝原因。
// 纯决策层：不读取 boot-args、不访问地址空间，安装后端必须执行准入结果。
#pragma once
#include <VPF/DependencyPlanner.hpp>
#include <VPF/DisplayManifest.hpp>

namespace VPF
{

    struct DisplayGroupSpec
    {
        DisplayGroup group;
        const char*  name;
        UInt32       features;          // 该组包含的 FeatureId 位
        UInt32       requiredGroups;    // 组级硬依赖
        UInt32       afterGroups;       // 这些组必须先于本组，只排序不构成依赖
        bool         installable;
    };

    // Metal 组由 SRD/LegacyBlend/Pipeline/Scratch/Submit 细分，SubmitGuard 独立成组。
    inline constexpr DisplayGroupSpec DefaultDisplayGroups[] = {
        {DisplayGroup::GLHwInfo, "GL_HwInfo", featureBit(FeatureId::DisplayOpenGLHwInfo), 0, 0, true},
        {DisplayGroup::GLDualQuad, "GL_DualQuad", featureBit(FeatureId::DisplayOpenGLDualQuad), 0, 0, true},
        {DisplayGroup::MetalRenderCore, "Metal_RenderCore",
         featureBit(FeatureId::DisplaySRD) | featureBit(FeatureId::DisplayLegacyBlend)
             | featureBit(FeatureId::DisplayPipelineConstructor),
         0, displayGroupBit(DisplayGroup::MetalSubmitSafety), true},
        {DisplayGroup::MetalScratch, "Metal_Scratch", featureBit(FeatureId::DisplayComputeScratch),
         displayGroupBit(DisplayGroup::MetalRenderCore), 0, true},
        {DisplayGroup::MetalSubmitSafety, "Metal_SubmitSafety",
         featureBit(FeatureId::DisplaySubmitGuard) | featureBit(FeatureId::DisplaySubmit), 0, 0, true},
        {DisplayGroup::KernelGFX9, "Kernel_GFX9", featureBit(FeatureId::KernelGFX9Capabilities), 0, 0, true},
        {DisplayGroup::KernelScanout, "Kernel_Scanout",
         featureBit(FeatureId::KernelDCNScanout) | featureBit(FeatureId::KernelFramebuffer), 0, 0, true},
    };

    enum class DisplayRejectReason : UInt8
    {
        None,
        UnsupportedOperatingSystem,
        UnsupportedDevice,
        MissingExternalPrerequisite,
        MissingEvidence,
        MissingDependency,
        GroupConflict,
        ExperimentalDisabled,
    };

    constexpr const char* displayRejectReasonName(const DisplayRejectReason reason)
    {
        switch (reason) {
            case DisplayRejectReason::None                       : return "None";
            case DisplayRejectReason::UnsupportedOperatingSystem : return "UnsupportedOperatingSystem";
            case DisplayRejectReason::UnsupportedDevice          : return "UnsupportedDevice";
            case DisplayRejectReason::MissingExternalPrerequisite: return "MissingExternalPrerequisite";
            case DisplayRejectReason::MissingEvidence            : return "MissingEvidence";
            case DisplayRejectReason::MissingDependency          : return "MissingDependency";
            case DisplayRejectReason::GroupConflict              : return "GroupConflict";
            case DisplayRejectReason::ExperimentalDisabled       : return "ExperimentalDisabled";
        }
        return "Unknown";
    }

    struct DisplayPlannerInput
    {
        UInt32 osMask{0};    // 单个 Darwin 位
        UInt32 deviceID{0};
        UInt32 requestedGroups{0};
        UInt32 verified{0};    // 已具备映像/完整函数/页块证据的 FeatureId
        UInt32 external{0};    // boot-arg、桥接和符号解析结果
        bool   allowExperimental{false};
    };

    struct DisplayGroupConflict
    {
        DisplayGroup loser;
        DisplayGroup winner;
    };

    struct DisplayGroupResult
    {
        UInt32              enabled{0};
        UInt32              rejected{0};
        UInt32              missing{0};
        UInt32              conflicts{0};
        DisplayGroup        order[DisplayGroupCount]{};
        UInt32              orderCount{0};
        DisplayRejectReason reasons[DisplayGroupCount]{};
        bool                cycle{false};
    };

    constexpr UInt32 requestedGroupsForDisplayFix(const bool displayFix, const UInt32 major)
    {
        if (!displayFix) { return 0; }
        UInt32 groups = displayGroupBit(DisplayGroup::GLHwInfo) | displayGroupBit(DisplayGroup::GLDualQuad)
                        | displayGroupBit(DisplayGroup::KernelGFX9) | displayGroupBit(DisplayGroup::KernelScanout);
        // Darwin 23 的 LegacyBlend/SubmitGuard 仍由旧精确 profile 提供证据。
        if (major >= 24) {
            groups |= displayGroupBit(DisplayGroup::MetalRenderCore) | displayGroupBit(DisplayGroup::MetalScratch)
                      | displayGroupBit(DisplayGroup::MetalSubmitSafety);
        }
        else if (major == 23) {
            groups |= displayGroupBit(DisplayGroup::MetalRenderCore) | displayGroupBit(DisplayGroup::MetalSubmitSafety);
        }
        return groups;
    }

    namespace DisplayPlannerDetail
    {

        constexpr const DisplayGroupSpec* findGroup(const DisplayGroupSpec* groups, const UInt32 count,
                                                    const DisplayGroup group)
        {
            for (UInt32 i = 0; i < count; ++i) {
                if (groups[i].group == group) { return &groups[i]; }
            }
            return nullptr;
        }

        constexpr bool applicable(const PlannerFeature& feature, const UInt32 osMask, const UInt32 deviceID)
        {
            if ((feature.osMask & osMask) == 0) { return false; }
            if (feature.deviceMask != 0 && (deviceBit(deviceID) & feature.deviceMask) == 0) { return false; }
            return true;
        }

        constexpr DisplayRejectReason featureReason(const PlannerFeature& feature, const PlannerInput& input,
                                                    const PlannerResult& result)
        {
            if ((feature.osMask & input.osMask) == 0) { return DisplayRejectReason::UnsupportedOperatingSystem; }
            if (feature.deviceMask != 0 && (deviceBit(input.deviceID) & feature.deviceMask) == 0) {
                return DisplayRejectReason::UnsupportedDevice;
            }
            if ((feature.external & ~input.external) != 0) { return DisplayRejectReason::MissingExternalPrerequisite; }
            if ((input.verified & featureBit(feature.feature)) == 0) { return DisplayRejectReason::MissingEvidence; }
            if ((result.conflicts & featureBit(feature.feature)) != 0) { return DisplayRejectReason::GroupConflict; }
            return DisplayRejectReason::MissingDependency;
        }

        constexpr UInt32 reasonRank(const DisplayRejectReason reason)
        {
            switch (reason) {
                case DisplayRejectReason::None                       : return 0;
                case DisplayRejectReason::UnsupportedOperatingSystem : return 1;
                case DisplayRejectReason::UnsupportedDevice          : return 2;
                case DisplayRejectReason::MissingExternalPrerequisite: return 3;
                case DisplayRejectReason::MissingEvidence            : return 4;
                case DisplayRejectReason::GroupConflict              : return 5;
                case DisplayRejectReason::ExperimentalDisabled       : return 6;
                case DisplayRejectReason::MissingDependency          : return 7;
            }
            return 7;
        }

        // 组闭包：请求 Scratch 会拉入 RenderCore；环直接失败闭合。
        struct GroupClosure
        {
            const DisplayGroupSpec* groups;
            UInt32                  count;
            UInt8                   state[DisplayGroupCount]{};
            UInt32                  closed{0};
            bool                    cycle{false};

            void resolve(const DisplayGroup group)
            {
                const auto index = static_cast<UInt32>(group);
                if (index >= DisplayGroupCount) { return; }
                if (state[index] == 1) {
                    cycle = true;
                    return;
                }
                if (state[index] == 2) { return; }
                const auto* spec = findGroup(groups, count, group);
                if (!spec) { return; }
                state[index] = 1;
                for (UInt32 i = 0; i < DisplayGroupCount; ++i) {
                    if ((spec->requiredGroups & displayGroupBit(static_cast<DisplayGroup>(i))) != 0) {
                        resolve(static_cast<DisplayGroup>(i));
                    }
                }
                state[index]  = 2;
                closed       |= displayGroupBit(group);
            }
        };

    }    // namespace DisplayPlannerDetail

    inline DisplayGroupResult evaluateDisplayGroups(const DisplayPlannerInput& input, const DisplayGroupSpec* groups,
                                                    const UInt32                groupCount,
                                                    const DisplayGroupConflict* conflicts     = nullptr,
                                                    const UInt32                conflictCount = 0)
    {
        using namespace DisplayPlannerDetail;
        DisplayGroupResult result;
        if (!groups || groupCount == 0 || groupCount > DisplayGroupCount) {
            result.cycle = true;
            return result;
        }

        UInt32 requested = input.requestedGroups & allDisplayGroupBits;
        // 不可安装组只在显式实验模式下进入闭包；默认组不得被无意启用。
        for (UInt32 i = 0; i < groupCount; ++i) {
            const auto bit = displayGroupBit(groups[i].group);
            if ((requested & bit) != 0 && !groups[i].installable && !input.allowExperimental) {
                requested                                            &= ~bit;
                result.rejected                                      |= bit;
                result.reasons[static_cast<UInt32>(groups[i].group)]  = DisplayRejectReason::ExperimentalDisabled;
            }
        }

        GroupClosure closure{groups, groupCount};
        for (UInt32 i = 0; i < groupCount; ++i) {
            if ((requested & displayGroupBit(groups[i].group)) != 0) { closure.resolve(groups[i].group); }
        }
        if (closure.cycle) {
            result.cycle = true;
            return result;
        }

        const UInt32 unresolved = requested & ~closure.closed;
        if (unresolved != 0) {
            result.rejected |= unresolved;
            result.missing |= unresolved;
            for (UInt32 i = 0; i < groupCount; ++i) {
                const auto bit = displayGroupBit(groups[i].group);
                if ((unresolved & bit) != 0) {
                    result.reasons[static_cast<UInt32>(groups[i].group)] = DisplayRejectReason::MissingDependency;
                }
            }
        }

        // 组冲突在请求闭包之后消解；依赖被拒绝组的其它组会在下面的依赖检查中失败闭合。
        for (UInt32 i = 0; conflicts && i < conflictCount; ++i) {
            const auto loser  = displayGroupBit(conflicts[i].loser);
            const auto winner = displayGroupBit(conflicts[i].winner);
            if ((closure.closed & loser) != 0 && (closure.closed & winner) != 0) {
                closure.closed                                          &= ~loser;
                result.rejected                                         |= loser;
                result.conflicts                                        |= loser;
                result.reasons[static_cast<UInt32>(conflicts[i].loser)]  = DisplayRejectReason::GroupConflict;
            }
        }

        UInt32 requestedFeatures = 0;
        for (UInt32 i = 0; i < groupCount; ++i) {
            const auto bit = displayGroupBit(groups[i].group);
            if ((closure.closed & bit) == 0) { continue; }
            for (UInt32 f = 0; f < FeatureCount; ++f) {
                const auto feature = static_cast<FeatureId>(f);
                if ((groups[i].features & featureBit(feature)) == 0) { continue; }
                const auto* spec = findFeature(DefaultFeatures, FeatureCount, feature);
                if (spec && applicable(*spec, input.osMask, input.deviceID)) {
                    requestedFeatures |= featureBit(feature);
                }
            }
        }

        PlannerInput featureInput{input.osMask, input.deviceID, requestedFeatures, input.verified, input.external};
        const auto   featureResult = plan(featureInput);

        for (UInt32 i = 0; i < groupCount; ++i) {
            const auto group = groups[i].group;
            const auto index = static_cast<UInt32>(group);
            const auto bit   = displayGroupBit(group);
            if ((closure.closed & bit) == 0) { continue; }
            UInt32              applicableCount = 0, enabledCount = 0;
            DisplayRejectReason worst = DisplayRejectReason::None;
            for (UInt32 f = 0; f < FeatureCount; ++f) {
                const auto feature = static_cast<FeatureId>(f);
                if ((groups[i].features & featureBit(feature)) == 0) { continue; }
                const auto* spec = findFeature(DefaultFeatures, FeatureCount, feature);
                if (!spec || !applicable(*spec, input.osMask, input.deviceID)) { continue; }
                ++applicableCount;
                if ((featureResult.enabled & featureBit(feature)) != 0) {
                    ++enabledCount;
                    continue;
                }
                const auto reason = featureReason(*spec, featureInput, featureResult);
                if (worst == DisplayRejectReason::None || reasonRank(reason) < reasonRank(worst)) { worst = reason; }
            }
            if (applicableCount == 0) {
                bool osMatch = false;
                for (UInt32 f = 0; f < FeatureCount; ++f) {
                    const auto feature = static_cast<FeatureId>(f);
                    if ((groups[i].features & featureBit(feature)) == 0) { continue; }
                    const auto* spec = findFeature(DefaultFeatures, FeatureCount, feature);
                    if (spec && (spec->osMask & input.osMask) != 0) { osMatch = true; }
                }
                result.rejected |= bit;
                result.reasons[index] =
                    osMatch ? DisplayRejectReason::UnsupportedDevice : DisplayRejectReason::UnsupportedOperatingSystem;
                continue;
            }
            if (enabledCount == applicableCount) {
                result.enabled |= bit;
                continue;
            }
            result.rejected |= bit;
            result.reasons[index] = worst == DisplayRejectReason::None ? DisplayRejectReason::MissingDependency : worst;
            if (worst == DisplayRejectReason::MissingEvidence) { result.missing |= bit; }
            if (worst == DisplayRejectReason::GroupConflict) { result.conflicts |= bit; }
        }

        // 组级硬依赖：被依赖组未启用时，依赖组必须失败闭合（可能发生多轮）。
        bool changed = true;
        while (changed) {
            changed = false;
            for (UInt32 i = 0; i < groupCount; ++i) {
                const auto bit = displayGroupBit(groups[i].group);
                if ((result.enabled & bit) == 0) { continue; }
                const auto required = groups[i].requiredGroups;
                if (required == 0 || (required & ~result.enabled) == 0) { continue; }
                result.enabled                                       &= ~bit;
                result.rejected                                      |= bit;
                result.missing                                       |= bit;
                result.reasons[static_cast<UInt32>(groups[i].group)]  = DisplayRejectReason::MissingDependency;
                changed                                               = true;
            }
        }

        // Kahn 拓扑排序：required 与 before 都产生边；排序失败报告环而不是部分成功。
        UInt32 indegree[DisplayGroupCount]{};
        for (UInt32 i = 0; i < groupCount; ++i) {
            const auto bit = displayGroupBit(groups[i].group);
            if ((result.enabled & bit) == 0) { continue; }
            const auto edges = groups[i].requiredGroups | groups[i].afterGroups;
            for (UInt32 j = 0; j < groupCount; ++j) {
                if (i == j) { continue; }
                const auto other = displayGroupBit(groups[j].group);
                if ((edges & other) != 0 && (result.enabled & other) != 0) { ++indegree[i]; }
            }
        }
        UInt32 remaining = result.enabled;
        while (remaining) {
            bool progressed = false;
            for (UInt32 i = 0; i < groupCount; ++i) {
                const auto bit = displayGroupBit(groups[i].group);
                if ((remaining & bit) == 0 || indegree[i] != 0) { continue; }
                result.order[result.orderCount++]  = groups[i].group;
                remaining                         &= ~bit;
                progressed                         = true;
                for (UInt32 j = 0; j < groupCount; ++j) {
                    if (i == j || (remaining & displayGroupBit(groups[j].group)) == 0) { continue; }
                    if ((groups[j].requiredGroups & bit) != 0) { --indegree[j]; }
                    if ((groups[j].afterGroups & bit) != 0) { --indegree[j]; }
                }
                break;
            }
            if (!progressed) {
                result.cycle = true;
                break;
            }
        }
        return result;
    }

    inline DisplayGroupResult planDisplay(const DisplayPlannerInput& input)
    {
        return evaluateDisplayGroups(
            input, DefaultDisplayGroups,
            static_cast<UInt32>(sizeof(DefaultDisplayGroups) / sizeof(DefaultDisplayGroups[0])));
    }

    constexpr bool orderedBefore(const DisplayGroupResult& result, const DisplayGroup first, const DisplayGroup second)
    {
        UInt32 firstIndex = DisplayGroupCount, secondIndex = DisplayGroupCount;
        for (UInt32 i = 0; i < result.orderCount; ++i) {
            if (result.order[i] == first) { firstIndex = i; }
            if (result.order[i] == second) { secondIndex = i; }
        }
        return firstIndex != DisplayGroupCount && secondIndex != DisplayGroupCount && firstIndex < secondIndex;
    }

}    // namespace VPF
