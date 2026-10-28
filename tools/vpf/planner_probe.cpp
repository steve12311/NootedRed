// VPF 依赖规划探针：依赖闭包、冲突消解、安装顺序与能力状态机。
#include <VPF/CapabilityState.hpp>
#include <VPF/DependencyPlanner.hpp>

#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>

using namespace VPF;

static constexpr UInt32 bits(std::initializer_list<FeatureId> features)
{
    UInt32 value = 0;
    for (const auto feature : features) { value |= featureBit(feature); }
    return value;
}

static PlannerInput input(const UInt32 os, const UInt32 device, const UInt32 requested, const UInt32 external,
                          const UInt32 verified = allFeatureBits)
{
    return PlannerInput{os, device, requested, verified, external};
}

static void expect(const char* label, const PlannerResult& result, const UInt32 enabled, const UInt32 rejected = 0,
                   const UInt32 conflicts = 0)
{
    if (result.enabled != enabled || (result.rejected & rejected) != rejected
        || (result.conflicts & conflicts) != conflicts)
    {
        std::fprintf(stderr, "FAIL %s: enabled=%08X rejected=%08X conflicts=%08X cycle=%d\n", label, result.enabled,
                     result.rejected, result.conflicts, result.cycle);
        std::abort();
    }
}

int main()
{
    constexpr UInt32 videoRequested = bits({FeatureId::Vcn2Decode, FeatureId::Vcn2Encode});
    constexpr UInt32 videoEnabled =
        bits({FeatureId::Vcn2SafetyGuard, FeatureId::Vcn2Decode, FeatureId::Vcn2Encode});
    for (const UInt32 os : {static_cast<UInt32>(OsDarwin24), static_cast<UInt32>(OsDarwin25)}) {
        for (const UInt32 device : {0x15E7U, 0x1636U, 0x1638U, 0x164CU}) {
            const auto result = plan(input(os, device, videoRequested, ExternalVcnBridge));
            expect("video", result, videoEnabled);
            assert(orderedBefore(result, FeatureId::Vcn2SafetyGuard, FeatureId::Vcn2Decode));
            assert(orderedBefore(result, FeatureId::Vcn2SafetyGuard, FeatureId::Vcn2Encode));
        }
    }
    // Darwin 23 的 VCN 用户态安全守卫证据缺失：全部拒绝。
    expect("video darwin23", plan(input(OsDarwin23, 0x1638, videoRequested, ExternalVcnBridge)), 0);
    // 缺少内核桥接或非 VCN 设备：拒绝。
    expect("video no bridge", plan(input(OsDarwin25, 0x1638, videoRequested, 0)), 0);
    expect("video display device", plan(input(OsDarwin25, 0x15D8, videoRequested, ExternalVcnBridge)), 0);
    // FairPlay：解码子集保留，编码与 AppleGVA 选择被互斥规则拒绝。
    const auto fairPlay = plan(input(OsDarwin25, 0x1638,
                                     bits({FeatureId::Vcn2Decode, FeatureId::Vcn2Encode,
                                           FeatureId::AppleGVASelection, FeatureId::FairPlayNativeDecodeSubset}),
                                     ExternalVcnBridge | ExternalFairPlay));
    expect("fairplay", fairPlay,
           bits({FeatureId::Vcn2SafetyGuard, FeatureId::Vcn2Decode, FeatureId::FairPlayNativeDecodeSubset}),
           bits({FeatureId::Vcn2Encode, FeatureId::AppleGVASelection}),
           bits({FeatureId::Vcn2Encode, FeatureId::AppleGVASelection}));

    // 显示组：SubmitGuard 先于 SRD；SRD 先于 LegacyBlend/Scratch。
    constexpr UInt32 displayRequested =
        bits({FeatureId::DisplaySubmitGuard, FeatureId::DisplaySubmit, FeatureId::DisplaySRD,
              FeatureId::DisplayComputeScratch});
    constexpr UInt32 displayEnabled = displayRequested;
    for (const UInt32 os : {static_cast<UInt32>(OsDarwin24), static_cast<UInt32>(OsDarwin25)}) {
        const auto result = plan(input(os, 0x1638, displayRequested, ExternalMetalBridge | ExternalSubmitResolver));
        expect("display", result, displayEnabled);
        assert(orderedBefore(result, FeatureId::DisplaySubmitGuard, FeatureId::DisplaySRD));
        assert(orderedBefore(result, FeatureId::DisplaySRD, FeatureId::DisplayComputeScratch));
    }
    const auto legacy = plan(input(OsDarwin25, 0x1638,
                                   displayRequested | featureBit(FeatureId::DisplayLegacyBlend),
                                   ExternalMetalBridge | ExternalSubmitResolver));
    expect("display legacy", legacy, displayEnabled | featureBit(FeatureId::DisplayLegacyBlend));
    assert(orderedBefore(legacy, FeatureId::DisplaySRD, FeatureId::DisplayLegacyBlend));
    // LegacyBlend 不在 Darwin 24 的当前路径启用。
    expect("legacy darwin24",
           plan(input(OsDarwin24, 0x1638, featureBit(FeatureId::DisplayLegacyBlend),
                      ExternalMetalBridge | ExternalSubmitResolver)),
           0);
    // 缺少 SubmitResolver 不阻止 SRD；SubmitGuard 单独拒绝。
    const auto noSubmit = plan(input(OsDarwin25, 0x1638,
                                     bits({FeatureId::DisplaySubmitGuard, FeatureId::DisplaySRD}),
                                     ExternalMetalBridge));
    expect("display no submit", noSubmit, featureBit(FeatureId::DisplaySRD),
           featureBit(FeatureId::DisplaySubmitGuard));
    expect("display no metal", plan(input(OsDarwin25, 0x1638, displayRequested, 0)), 0);

    // 自定义环必须报告 cycle，而不是部分成功。
    static constexpr FeatureId cycleA[] = {FeatureId::DisplaySubmitGuard};
    static constexpr FeatureId cycleB[] = {FeatureId::DisplaySRD};
    constexpr PlannerFeature cycleFeatures[] = {
        {FeatureId::DisplaySRD, OsAll, 0, 0, cycleA, 1, nullptr, 0},
        {FeatureId::DisplaySubmitGuard, OsAll, 0, 0, cycleB, 1, nullptr, 0},
    };
    const auto cycle = evaluate(cycleFeatures, 2, nullptr, 0,
                                input(OsDarwin25, 0, featureBit(FeatureId::DisplaySRD), 0));
    if (!cycle.cycle || cycle.enabled != 0) {
        std::fprintf(stderr, "FAIL cycle: cycle=%d enabled=%08X\n", cycle.cycle, cycle.enabled);
        std::abort();
    }

    // 能力状态机：合法路径、非法跳转、隔离后不自动重试、global 与 per-map 区分。
    CapabilityLedger ledger;
    constexpr UInt64 mapA = 0x1000, mapB = 0x2000;
    assert(ledger.set(mapA, FeatureId::Vcn2Decode, CapabilityState::Eligible));
    assert(ledger.set(mapA, FeatureId::Vcn2Decode, CapabilityState::ImageResolved));
    assert(!ledger.set(mapA, FeatureId::Vcn2Decode, CapabilityState::Installing));
    assert(ledger.set(mapA, FeatureId::Vcn2Decode, CapabilityState::Verified));
    assert(ledger.set(mapA, FeatureId::Vcn2Decode, CapabilityState::Prepared));
    assert(ledger.set(mapA, FeatureId::Vcn2Decode, CapabilityState::Installing));
    assert(ledger.set(mapA, FeatureId::Vcn2Decode, CapabilityState::Installed));
    assert(ledger.set(mapA, FeatureId::Vcn2Decode, CapabilityState::RuntimeObserved));
    assert(ledger.installedMaps(FeatureId::Vcn2Decode) == 1);
    assert(ledger.anyInstalled(FeatureId::Vcn2Decode));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::Eligible));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::ImageResolved));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::Verified));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::Prepared));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::Installing));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::CommitFailed));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::RollbackVerified));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::Disabled));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::Eligible));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::ImageResolved));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::Verified));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::Prepared));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::Installing));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::RollbackFailed));
    assert(ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::Quarantined));
    assert(ledger.quarantined(FeatureId::Vcn2Decode));
    assert(!ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::Eligible));
    assert(!ledger.set(mapB, FeatureId::Vcn2Decode, CapabilityState::Disabled));
    assert(ledger.installedMaps(FeatureId::Vcn2Decode) == 1);
    ledger.removeMap(mapA);
    assert(ledger.installedMaps(FeatureId::Vcn2Decode) == 0);
    assert(!ledger.anyInstalled(FeatureId::Vcn2Decode));
    assert(ledger.quarantined(FeatureId::Vcn2Decode));
    ledger.removeMap(mapB);
    assert(!ledger.quarantined(FeatureId::Vcn2Decode));
    assert(ledger.state(mapB, FeatureId::Vcn2Decode) == CapabilityState::Disabled);

    std::printf("PASS VPF planner/state probe: dependency closure, conflicts, ordering, quarantine\n");
    return 0;
}
