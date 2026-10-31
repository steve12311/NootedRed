// VPF：依赖规划器；纯函数，不读取或写入进程地址空间。
#pragma once
#include <VPF/PatchManifest.hpp>

namespace VPF
{

    constexpr UInt32 FeatureCount = static_cast<UInt32>(FeatureId::Count);
    static_assert(FeatureCount <= 32);

    constexpr UInt32 featureBit(const FeatureId feature)
    {
        return 1U << static_cast<UInt32>(feature);
    }

    constexpr UInt32 allFeatureBits = (1U << FeatureCount) - 1;

    // 外部前提由内核侧桥接/权限决定；规划器只判断是否具备，不自行初始化。
    constexpr UInt32 ExternalVcnBridge         = 1U << 0;
    constexpr UInt32 ExternalMetalBridge      = 1U << 1;
    constexpr UInt32 ExternalFairPlay         = 1U << 2;
    constexpr UInt32 ExternalSubmitResolver   = 1U << 3;
    constexpr UInt32 ExternalOpenGLPageHook   = 1U << 4;
    constexpr UInt32 ExternalKernelDisplay    = 1U << 5;

    constexpr UInt32 deviceBit(const UInt32 deviceID)
    {
        switch (deviceID) {
            case 0x15D8: return 1U << 0;
            case 0x15DD: return 1U << 1;
            case 0x15E7: return 1U << 2;
            case 0x1636: return 1U << 3;
            case 0x1638: return 1U << 4;
            case 0x164C: return 1U << 5;
            default: return 0;
        }
    }

    constexpr UInt32 DisplayDeviceMask = (1U << 6) - 1;
    constexpr UInt32 VcnDeviceMask     = (1U << 2) | (1U << 3) | (1U << 4) | (1U << 5);

    struct PlannerFeature
    {
        FeatureId        feature;
        UInt32           osMask;
        UInt32           deviceMask;
        UInt32           external;
        const FeatureId* required;
        UInt32           requireCount;
        const FeatureId* before;
        UInt32           beforeCount;
        // 该 OS 位上 required 不成立；用于 Darwin23 的独立 legacy gate。
        UInt32           skipRequirementsForOs{0};
    };

    struct ConflictRule
    {
        FeatureId loser;
        FeatureId winner;
    };

    struct PlannerInput
    {
        UInt32 osMask{0};      // 单个 Darwin 位
        UInt32 deviceID{0};
        UInt32 requested{0};   // 由 boot-args/策略得出的候选功能
        UInt32 verified{0};    // 已具备映像/完整函数证据的功能
        UInt32 external{0};
    };

    struct PlannerResult
    {
        UInt32   enabled{0};
        UInt32   rejected{0};
        UInt32   missing{0};
        UInt32   conflicts{0};
        UInt32   missingExternal{0};
        FeatureId order[FeatureCount]{};
        UInt32   orderCount{0};
        bool     cycle{false};
    };

    inline constexpr FeatureId RequiresDecode[]      = {FeatureId::Vcn2Decode};
    inline constexpr FeatureId RequiresSRD[]         = {FeatureId::DisplaySRD};
    inline constexpr FeatureId BeforeSubmitGuard[]   = {FeatureId::DisplaySubmitGuard};

    // 运行时实际安装顺序由 required 与 before 共同表达；before 只排序，不构成硬依赖。
    inline constexpr PlannerFeature DefaultFeatures[] = {
        {FeatureId::DisplaySubmitGuard, OsDarwin23 | OsDarwin24 | OsDarwin25, DisplayDeviceMask,
         ExternalMetalBridge | ExternalSubmitResolver, nullptr, 0, nullptr, 0},
        {FeatureId::DisplaySubmit, OsDarwin24 | OsDarwin25, DisplayDeviceMask,
         ExternalMetalBridge | ExternalSubmitResolver, nullptr, 0, nullptr, 0},
        {FeatureId::DisplayPipelineConstructor, OsDarwin24 | OsDarwin25, DisplayDeviceMask, ExternalMetalBridge,
         nullptr, 0, nullptr, 0},
        {FeatureId::DisplaySRD, OsDarwin24 | OsDarwin25, DisplayDeviceMask, ExternalMetalBridge, nullptr, 0,
         BeforeSubmitGuard, 1},
        {FeatureId::DisplayLegacyBlend, OsDarwin23 | OsDarwin25, DisplayDeviceMask, ExternalMetalBridge, RequiresSRD, 1,
         nullptr, 0, OsDarwin23},
        {FeatureId::DisplayComputeScratch, OsDarwin24 | OsDarwin25, DisplayDeviceMask, ExternalMetalBridge,
         RequiresSRD, 1, nullptr, 0},
        {FeatureId::Vcn2Decode, OsDarwin24 | OsDarwin25, VcnDeviceMask, ExternalVcnBridge, nullptr, 0, nullptr, 0},
        {FeatureId::Vcn2Encode, OsDarwin24 | OsDarwin25, VcnDeviceMask, ExternalVcnBridge, nullptr, 0, nullptr, 0},
        {FeatureId::AppleGVASelection, OsDarwin24 | OsDarwin25, VcnDeviceMask, ExternalVcnBridge, RequiresDecode, 1,
         nullptr, 0},
        {FeatureId::FairPlayNativeDecodeSubset, OsDarwin24 | OsDarwin25, VcnDeviceMask, ExternalFairPlay,
         RequiresDecode, 1, nullptr, 0},
        {FeatureId::Vcn2HEVCMain10, OsDarwin24 | OsDarwin25, VcnDeviceMask, ExternalVcnBridge, RequiresDecode, 1,
         nullptr, 0},
        // DVPF：OpenGL 页内组与内核显示组独立于 Metal 共享缓存事务。
        {FeatureId::DisplayOpenGLHwInfo, OsAll, DisplayDeviceMask, ExternalOpenGLPageHook, nullptr, 0, nullptr, 0},
        {FeatureId::DisplayOpenGLDualQuad, OsAll, DisplayDeviceMask, ExternalOpenGLPageHook, nullptr, 0, nullptr, 0},
        {FeatureId::KernelGFX9Capabilities, OsAll, DisplayDeviceMask, ExternalKernelDisplay, nullptr, 0, nullptr, 0},
        {FeatureId::KernelDCNScanout, OsAll, DisplayDeviceMask, ExternalKernelDisplay, nullptr, 0, nullptr, 0},
        {FeatureId::KernelFramebuffer, OsAll, DisplayDeviceMask, ExternalKernelDisplay, nullptr, 0, nullptr, 0},
    };

    inline constexpr ConflictRule DefaultConflicts[] = {
        {FeatureId::Vcn2Encode, FeatureId::FairPlayNativeDecodeSubset},
        {FeatureId::AppleGVASelection, FeatureId::FairPlayNativeDecodeSubset},
    };

    constexpr const PlannerFeature* findFeature(const PlannerFeature* features, const UInt32 featureCount,
                                                const FeatureId feature)
    {
        if (!features) { return nullptr; }
        for (UInt32 i = 0; i < featureCount; ++i) {
            if (features[i].feature == feature) { return &features[i]; }
        }
        return nullptr;
    }

    namespace Detail
    {
        constexpr UInt32 indexOf(const FeatureId feature)
        {
            return static_cast<UInt32>(feature);
        }

        constexpr bool isEnabled(const UInt32 bits, const FeatureId feature)
        {
            return (bits & featureBit(feature)) != 0;
        }

        constexpr void set(UInt32& bits, const FeatureId feature)
        {
            bits |= featureBit(feature);
        }

        constexpr bool gated(const PlannerFeature& entry, const PlannerInput& input)
        {
            if ((entry.osMask & input.osMask) == 0) { return false; }
            if (entry.deviceMask != 0 && (deviceBit(input.deviceID) & entry.deviceMask) == 0) { return false; }
            return true;
        }

        constexpr bool conflictEnabled(const ConflictRule* rules, const UInt32 ruleCount, const FeatureId feature,
                                       const UInt32 enabled)
        {
            for (UInt32 i = 0; i < ruleCount; ++i) {
                if (rules[i].loser == feature && isEnabled(enabled, rules[i].winner)) { return true; }
                if (rules[i].winner == feature && isEnabled(enabled, rules[i].loser)) { return true; }
            }
            return false;
        }
    }    // namespace Detail

    // 依赖闭包 + 冲突消解 + 拓扑排序。任何前提缺失都 fail closed。
    inline PlannerResult evaluate(const PlannerFeature* features, const UInt32 featureCount,
                                  const ConflictRule* conflicts, const UInt32 conflictCount, const PlannerInput& input)
    {
        PlannerResult result;
        if (!features || featureCount == 0 || featureCount > FeatureCount) {
            result.cycle = true;
            return result;
        }
        UInt32 requested = input.requested;
        // 冲突对在闭包前按 winner 优先消解；被拒绝项仍记录原因。
        for (UInt32 i = 0; i < conflictCount; ++i) {
            const auto loser = conflicts[i].loser, winner = conflicts[i].winner;
            if (Detail::isEnabled(requested, loser) && Detail::isEnabled(requested, winner)) {
                requested &= ~featureBit(loser);
                Detail::set(result.rejected, loser);
                Detail::set(result.conflicts, loser);
            }
        }

        constexpr UInt32 MaxFeatures = FeatureCount;
        UInt8  state[MaxFeatures]{};    // 0=未访问 1=访问中 2=已启用 3=已拒绝
        UInt32 enabled = 0;

        // 递归闭包；返回 false 时调用方标记拒绝，缺失原因由 visited 状态体现。
        struct Resolver
        {
            const PlannerFeature* features;
            UInt32                count;
            const ConflictRule*   conflicts;
            UInt32                conflictCount;
            const PlannerInput&   input;
            UInt8*                state;
            UInt32&               enabled;
            PlannerResult&        result;

            bool resolve(const FeatureId feature)
            {
                const auto index = Detail::indexOf(feature);
                if (index >= FeatureCount) { return false; }
                if (state[index] == 1) {
                    result.cycle = true;
                    return false;
                }
                if (state[index] == 2) { return true; }
                if (state[index] == 3) { return false; }
                const PlannerFeature* entry = nullptr;
                for (UInt32 i = 0; i < count; ++i) {
                    if (features[i].feature == feature) {
                        entry = &features[i];
                        break;
                    }
                }
                if (!entry) {
                    state[index] = 3;
                    Detail::set(result.rejected, feature);
                    return false;
                }
                state[index] = 1;
                if (!Detail::gated(*entry, input)) {
                    state[index] = 3;
                    Detail::set(result.rejected, feature);
                    return false;
                }
                if ((entry->external & ~input.external) != 0) {
                    state[index] = 3;
                    Detail::set(result.rejected, feature);
                    Detail::set(result.missing, feature);
                    result.missingExternal |= entry->external & ~input.external;
                    return false;
                }
                if ((input.verified & featureBit(feature)) == 0) {
                    state[index] = 3;
                    Detail::set(result.rejected, feature);
                    Detail::set(result.missing, feature);
                    return false;
                }
                if (Detail::conflictEnabled(conflicts, conflictCount, feature, enabled)) {
                    state[index] = 3;
                    Detail::set(result.rejected, feature);
                    Detail::set(result.conflicts, feature);
                    return false;
                }
                if ((entry->skipRequirementsForOs & input.osMask) == 0) {
                    for (UInt32 i = 0; i < entry->requireCount; ++i) {
                        if (!resolve(entry->required[i])) {
                            state[index] = 3;
                            Detail::set(result.rejected, feature);
                            Detail::set(result.missing, feature);
                            return false;
                        }
                    }
                }
                state[index] = 2;
                Detail::set(enabled, feature);
                return true;
            }
        } resolver{features, featureCount, conflicts, conflictCount, input, state, enabled, result};

        for (UInt32 i = 0; i < featureCount; ++i) {
            if (Detail::isEnabled(requested, features[i].feature) && !resolver.resolve(features[i].feature)) {
                Detail::set(result.rejected, features[i].feature);
            }
        }

        result.enabled = enabled;
        // Kahn 拓扑排序：required 与 before 都产生顺序边；排序失败即报告环。
        UInt32 indegree[MaxFeatures]{};
        for (UInt32 i = 0; i < featureCount; ++i) {
            if ((enabled & featureBit(features[i].feature)) == 0) { continue; }
            const auto& entry = features[i];
            for (UInt32 r = 0; r < entry.requireCount; ++r) {
                if (Detail::isEnabled(enabled, entry.required[r])) { ++indegree[i]; }
            }
            for (UInt32 b = 0; b < entry.beforeCount; ++b) {
                if (Detail::isEnabled(enabled, entry.before[b])) { ++indegree[i]; }
            }
        }
        UInt32 remaining = enabled;
        // 用特性表的稳定顺序选择入度为 0 的节点，避免依赖哈希/遍历顺序。
        while (remaining) {
            bool progressed = false;
            for (UInt32 i = 0; i < featureCount; ++i) {
                if ((remaining & featureBit(features[i].feature)) == 0 || indegree[i] != 0) { continue; }
                result.order[result.orderCount++] = features[i].feature;
                remaining &= ~featureBit(features[i].feature);
                progressed = true;
                // 删除该节点指向后继的边。
                for (UInt32 j = 0; j < featureCount; ++j) {
                    if ((remaining & featureBit(features[j].feature)) == 0) { continue; }
                    const auto& entry = features[j];
                    for (UInt32 r = 0; r < entry.requireCount; ++r) {
                        if (entry.required[r] == features[i].feature) { --indegree[j]; }
                    }
                    for (UInt32 b = 0; b < entry.beforeCount; ++b) {
                        if (entry.before[b] == features[i].feature) { --indegree[j]; }
                    }
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

    inline PlannerResult plan(const PlannerInput& input)
    {
        return evaluate(DefaultFeatures, static_cast<UInt32>(sizeof(DefaultFeatures) / sizeof(DefaultFeatures[0])),
                        DefaultConflicts, static_cast<UInt32>(sizeof(DefaultConflicts) / sizeof(DefaultConflicts[0])),
                        input);
    }

    constexpr bool orderedBefore(const PlannerResult& result, const FeatureId first, const FeatureId second)
    {
        UInt32 firstIndex = FeatureCount, secondIndex = FeatureCount;
        for (UInt32 i = 0; i < result.orderCount; ++i) {
            if (result.order[i] == first) { firstIndex = i; }
            if (result.order[i] == second) { secondIndex = i; }
        }
        return firstIndex != FeatureCount && secondIndex != FeatureCount && firstIndex < secondIndex;
    }

}    // namespace VPF
