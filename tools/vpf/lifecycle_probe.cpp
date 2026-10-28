// CapabilityLedger 的 Quarantined、removeMap 与 64 槽上限。
#include <cassert>
#include <cstdio>

#include <VPF/CapabilityState.hpp>
#include <VPF/VerifiedPatchPlan.hpp>

using VPF::CapabilityLedger;
using VPF::CapabilityState;
using VPF::FeatureId;

static bool advance(CapabilityLedger& ledger, UInt64 map, CapabilityState target)
{
    constexpr auto feature = FeatureId::Vcn2Decode;
    const CapabilityState path[] = {CapabilityState::Eligible, CapabilityState::ImageResolved,
                                    CapabilityState::Verified, CapabilityState::Prepared,
                                    CapabilityState::Installing, CapabilityState::Installed,
                                    CapabilityState::RuntimeObserved};
    for (const auto state : path) {
        if (ledger.state(map, feature) == target) { return true; }
        if (!ledger.set(map, feature, state)) { return false; }
        if (state == target) { return true; }
    }
    return ledger.state(map, feature) == target;
}

int main()
{
    constexpr auto feature = FeatureId::Vcn2Decode;
    VPF::FeatureManifest manifest{feature, VPF::OsDarwin25, 0, VPF::EvidenceKind::ExactValidatedBuild,
                                  {nullptr, 0, nullptr, 0, nullptr, 0, nullptr, 0}, "lifecycle-probe", nullptr};
    VPF::VerifiedPatchPlan plan;
    assert(!plan.valid() && !plan.installable());
    plan.reset(manifest, 0x1000, {0x1000, 0x100});
    UInt8 original = 0, replacement = 1;
    assert(plan.addSite(0x1000, 0, 1, VPF::NoFunction, 0, &original, &replacement));
    assert(plan.seal(VPF::PlanState::Mixed) && plan.valid() && !plan.installable());
    assert(plan.seal(VPF::PlanState::AllOriginal) && plan.installable());

    CapabilityLedger ledger;
    constexpr UInt64 mapA = 0x1000, mapB = 0x2000;

    assert(advance(ledger, mapA, CapabilityState::Installed));
    assert(ledger.state(mapA, feature) == CapabilityState::Installed);
    assert(advance(ledger, mapB, CapabilityState::Installing));
    assert(ledger.set(mapB, feature, CapabilityState::RollbackFailed));
    assert(ledger.set(mapB, feature, CapabilityState::Quarantined));
    assert(!ledger.set(mapB, feature, CapabilityState::Eligible));
    assert(!ledger.set(mapB, feature, CapabilityState::Disabled));
    assert(ledger.quarantined(feature));
    ledger.removeMap(mapA);
    assert(ledger.state(mapA, feature) == CapabilityState::Disabled);
    assert(ledger.state(mapB, feature) == CapabilityState::Quarantined);
    ledger.removeMap(mapB);
    assert(ledger.set(mapB, feature, CapabilityState::Eligible));

    CapabilityLedger full;
    for (UInt64 i = 0; i < CapabilityLedger::Capacity; ++i) {
        assert(full.set(0x10000 + i, feature, CapabilityState::Eligible));
    }
    assert(!full.set(0x20000, feature, CapabilityState::Eligible));
    full.removeMap(0x10000);
    assert(full.set(0x20000, feature, CapabilityState::Eligible));
    full.clear();
    assert(full.state(0x20000, feature) == CapabilityState::Disabled);

    std::printf("PASS ledger lifecycle: quarantine, removeMap, map reuse, 64-slot bound\n");
    return 0;
}
