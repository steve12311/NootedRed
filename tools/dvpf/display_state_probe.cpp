// DVPF 状态探针：显示组依赖、拒绝原因、共享账本视图和跳板生命周期。
#include <VPF/DisplayCapabilityState.hpp>
#include <VPF/DisplayDependencyPlanner.hpp>
#include <VPF/DisplayRuntime.hpp>
#include <VPF/ExecutableTrampolineResource.hpp>
#include <cstdio>
#include <cstdlib>

using namespace VPF;

static unsigned failures = 0;

static void check(const bool condition, const char* label)
{
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", label);
        ++failures;
    }
}

static DisplayPlannerInput input(const UInt32 os, const UInt32 device, const UInt32 groups, const UInt32 external,
                                 const UInt32 verified = allFeatureBits, const bool experimental = false)
{
    return DisplayPlannerInput{os, device, groups, verified, external, experimental};
}

static void expectReason(const DisplayGroupResult& result, const DisplayGroup group, const DisplayRejectReason reason,
                         const char* label)
{
    check(result.reasons[static_cast<UInt32>(group)] == reason, label);
}

static void testPlanner()
{
    constexpr UInt32 allGroups = allDisplayGroupBits & ~displayGroupBit(DisplayGroup::HDRResearch);
    check(requestedGroupsForDisplayFix(false, 25) == 0, "policy disabled");
    check((requestedGroupsForDisplayFix(true, 25) & allGroups) == allGroups, "policy darwin25");
    check((requestedGroupsForDisplayFix(true, 23) & displayGroupBit(DisplayGroup::MetalScratch)) == 0,
          "policy darwin23 scratch");
    check((requestedGroupsForDisplayFix(true, 25) & displayGroupBit(DisplayGroup::HDRResearch)) == 0,
          "policy no hdr");
    constexpr UInt32 displayExternal =
        ExternalOpenGLPageHook | ExternalMetalBridge | ExternalSubmitResolver | ExternalKernelDisplay;

    const auto darwin25 =
        planDisplay(input(OsDarwin25, 0x1638, allGroups | displayGroupBit(DisplayGroup::HDRResearch), displayExternal));
    check(!darwin25.cycle, "darwin25 cycle");
    check((darwin25.enabled & allGroups) == allGroups, "darwin25 enabled");
    check((darwin25.rejected & displayGroupBit(DisplayGroup::HDRResearch)) != 0, "hdr rejected");
    expectReason(darwin25, DisplayGroup::HDRResearch, DisplayRejectReason::ExperimentalDisabled, "hdr reason");
    check(orderedBefore(darwin25, DisplayGroup::MetalRenderCore, DisplayGroup::MetalScratch), "render before scratch");
    check(orderedBefore(darwin25, DisplayGroup::MetalSubmitSafety, DisplayGroup::MetalRenderCore),
          "submit before render");

    const auto darwin24 = planDisplay(input(OsDarwin24, 0x1638, allGroups, displayExternal));
    check((darwin24.enabled & allGroups) == allGroups, "darwin24 enabled");
    check((darwin24.rejected & allGroups) == 0, "darwin24 rejected");
    check(orderedBefore(darwin24, DisplayGroup::MetalRenderCore, DisplayGroup::MetalScratch), "24 render scratch");
    check(orderedBefore(darwin24, DisplayGroup::MetalSubmitSafety, DisplayGroup::MetalRenderCore),
          "24 submit before render");

    const auto darwin23 = planDisplay(input(OsDarwin23, 0x1638, allGroups, displayExternal));
    constexpr UInt32 legacyGroups =
        displayGroupBit(DisplayGroup::GLHwInfo) | displayGroupBit(DisplayGroup::GLDualQuad)
        | displayGroupBit(DisplayGroup::MetalRenderCore) | displayGroupBit(DisplayGroup::MetalSubmitSafety)
        | displayGroupBit(DisplayGroup::KernelGFX9) | displayGroupBit(DisplayGroup::KernelScanout);
    check((darwin23.enabled & legacyGroups) == legacyGroups, "darwin23 legacy groups");
    expectReason(darwin23, DisplayGroup::MetalScratch, DisplayRejectReason::UnsupportedOperatingSystem,
                 "darwin23 scratch");
    check(orderedBefore(darwin23, DisplayGroup::MetalSubmitSafety, DisplayGroup::MetalRenderCore),
          "23 submit before render");

    const auto noMetal =
        planDisplay(input(OsDarwin25, 0x1638, allGroups, ExternalOpenGLPageHook | ExternalKernelDisplay));
    check((noMetal.enabled
           & (displayGroupBit(DisplayGroup::MetalRenderCore) | displayGroupBit(DisplayGroup::MetalScratch)
              | displayGroupBit(DisplayGroup::MetalSubmitSafety)))
              == 0,
          "no metal enabled");
    expectReason(noMetal, DisplayGroup::MetalRenderCore, DisplayRejectReason::MissingExternalPrerequisite,
                 "no metal reason");
    check((noMetal.enabled & displayGroupBit(DisplayGroup::GLHwInfo)) != 0, "no metal gl");

    const auto noScratch = planDisplay(input(OsDarwin25, 0x1638, allGroups, displayExternal,
                                             allFeatureBits & ~featureBit(FeatureId::DisplayComputeScratch)));
    expectReason(noScratch, DisplayGroup::MetalScratch, DisplayRejectReason::MissingEvidence, "scratch evidence");
    check((noScratch.enabled & displayGroupBit(DisplayGroup::MetalRenderCore)) != 0, "scratch render core");

    const auto scratchOnly =
        planDisplay(input(OsDarwin25, 0x1638, displayGroupBit(DisplayGroup::MetalScratch), displayExternal));
    check((scratchOnly.enabled & displayGroupBit(DisplayGroup::MetalRenderCore)) != 0, "scratch closure render");
    check((scratchOnly.enabled & displayGroupBit(DisplayGroup::MetalScratch)) != 0, "scratch closure scratch");
    check(orderedBefore(scratchOnly, DisplayGroup::MetalRenderCore, DisplayGroup::MetalScratch),
          "scratch closure order");

    const auto hdr = planDisplay(input(OsDarwin25, 0x1638, displayGroupBit(DisplayGroup::HDRResearch),
                                       displayExternal | ExternalHDRResearch, allFeatureBits, true));
    check((hdr.enabled & displayGroupBit(DisplayGroup::HDRResearch)) != 0, "hdr experimental enabled");

    // 组级环必须失败闭合，不允许部分成功。
    constexpr DisplayGroupSpec cyclic[] = {
        {DisplayGroup::GLHwInfo, "A", featureBit(FeatureId::DisplayOpenGLHwInfo),
         displayGroupBit(DisplayGroup::GLDualQuad), 0, true},
        {DisplayGroup::GLDualQuad, "B", featureBit(FeatureId::DisplayOpenGLDualQuad),
         displayGroupBit(DisplayGroup::GLHwInfo), 0, true},
    };
    const auto cycle = evaluateDisplayGroups(
        input(OsDarwin25, 0x1638, displayGroupBit(DisplayGroup::GLHwInfo), displayExternal), cyclic, 2);
    check(cycle.cycle && cycle.enabled == 0, "group cycle");

    constexpr DisplayGroupSpec missingDependency[] = {
        {DisplayGroup::GLHwInfo, "A", featureBit(FeatureId::DisplayOpenGLHwInfo),
         displayGroupBit(DisplayGroup::GLDualQuad), 0, true},
    };
    const auto missing = evaluateDisplayGroups(
        input(OsDarwin25, 0x1638, displayGroupBit(DisplayGroup::GLHwInfo), displayExternal), missingDependency, 1);
    check((missing.rejected & displayGroupBit(DisplayGroup::GLHwInfo)) != 0, "missing group dependency");
    expectReason(missing, DisplayGroup::GLHwInfo, DisplayRejectReason::MissingDependency, "missing group reason");

    // 组级冲突：loser 被拒绝，winner 保留。
    constexpr DisplayGroupConflict conflict[] = {{DisplayGroup::GLDualQuad, DisplayGroup::GLHwInfo}};
    const auto                     conflicted = evaluateDisplayGroups(
        input(OsDarwin25, 0x1638, displayGroupBit(DisplayGroup::GLHwInfo) | displayGroupBit(DisplayGroup::GLDualQuad),
                                  displayExternal),
        DefaultDisplayGroups, 8, conflict, 1);
    check((conflicted.enabled & displayGroupBit(DisplayGroup::GLHwInfo)) != 0, "conflict winner");
    check((conflicted.rejected & displayGroupBit(DisplayGroup::GLDualQuad)) != 0, "conflict loser");
    expectReason(conflicted, DisplayGroup::GLDualQuad, DisplayRejectReason::GroupConflict, "conflict reason");
}

static void testLedger()
{
    CapabilityLedger ledger;
    constexpr UInt64 map   = 0x1000;
    constexpr auto   group = DisplayGroup::GLHwInfo;
    check(displayGroupState(ledger, map, group) == DisplayGroupState::Disabled, "ledger disabled");
    check(markDisplayGroup(ledger, map, group, CapabilityState::Eligible), "ledger eligible");
    check(displayGroupState(ledger, map, group) == DisplayGroupState::Candidate, "ledger candidate");
    check(markDisplayGroup(ledger, map, group, CapabilityState::ImageResolved), "ledger resolved");
    check(displayGroupState(ledger, map, group) == DisplayGroupState::Resolved, "ledger resolved state");
    check(markDisplayGroup(ledger, map, group, CapabilityState::Verified), "ledger verified");
    check(displayGroupState(ledger, map, group) == DisplayGroupState::Verified, "ledger verified state");
    check(markDisplayGroup(ledger, map, group, CapabilityState::Prepared), "ledger prepared");
    check(displayGroupState(ledger, map, group) == DisplayGroupState::InstallEligible, "ledger eligible install");
    check(markDisplayGroup(ledger, map, group, CapabilityState::Installing), "ledger installing");
    check(displayGroupState(ledger, map, group) == DisplayGroupState::Installing, "ledger installing state");
    check(markDisplayGroup(ledger, map, group, CapabilityState::Installed), "ledger installed");
    check(displayGroupState(ledger, map, group) == DisplayGroupState::Installed, "ledger installed state");
    check(markDisplayGroup(ledger, map, group, CapabilityState::RuntimeObserved), "ledger observed");
    check(displayGroupState(ledger, map, group) == DisplayGroupState::RuntimeObserved, "ledger observed state");

    constexpr UInt64 rejectedMap = 0x2000;
    check(markDisplayGroup(ledger, rejectedMap, group, CapabilityState::Unsupported), "ledger unsupported");
    check(displayGroupState(ledger, rejectedMap, group) == DisplayGroupState::Unsupported, "ledger unsupported state");

    constexpr UInt64 rejected = 0x3000;
    check(markDisplayGroup(ledger, rejected, group, CapabilityState::Rejected), "ledger rejected");
    check(displayGroupState(ledger, rejected, group) == DisplayGroupState::Rejected, "ledger rejected state");

    constexpr UInt64 rollback = 0x4000;
    constexpr auto   render   = DisplayGroup::MetalRenderCore;
    check(markDisplayGroup(ledger, rollback, render, CapabilityState::Eligible), "ledger rollback eligible");
    check(markDisplayGroup(ledger, rollback, render, CapabilityState::ImageResolved), "ledger rollback resolved");
    check(markDisplayGroup(ledger, rollback, render, CapabilityState::Verified), "ledger rollback verified");
    check(markDisplayGroup(ledger, rollback, render, CapabilityState::Prepared), "ledger rollback prepared");
    check(markDisplayGroup(ledger, rollback, render, CapabilityState::Installing), "ledger rollback installing");
    // 一个功能组回滚失败必须让整个组失败闭合。
    check(ledger.set(rollback, FeatureId::DisplaySRD, CapabilityState::RollbackFailed), "ledger rollback fail");
    check(displayGroupState(ledger, rollback, render) == DisplayGroupState::RollbackFailed, "ledger rollback state");
    check(ledger.set(rollback, FeatureId::DisplaySRD, CapabilityState::Quarantined), "ledger quarantine");
    check(displayGroupState(ledger, rollback, render) == DisplayGroupState::Quarantined, "ledger quarantine state");
    check(!ledger.set(rollback, FeatureId::DisplaySRD, CapabilityState::Eligible), "ledger no retry");
}

static void testTrampoline()
{
    ExecutableTrampolineResource resource;
    check(!resource.fill(64), "fill before allocate");
    check(resource.record().fault == TrampolineFault::FillFailed, "fill fault");
    check(!resource.allocate(0), "allocate zero");
    check(resource.allocate(0x1000), "allocate");
    check(resource.fill(64), "fill");
    check(!resource.linkEntry(0x2000), "link before executable");
    check(resource.makeExecutable(), "executable");
    check(resource.release(), "release executable page");
    check(resource.record().state == TrampolineState::Released, "released");
    check(resource.invariant(), "released invariant");

    ExecutableTrampolineResource linked;
    check(linked.allocate(0x3000) && linked.fill(64) && linked.makeExecutable(), "linked setup");
    check(linked.linkEntry(0x4000), "link entry");
    check(linked.linked() && !linked.release(), "linked release rejected");
    check(linked.record().fault == TrampolineFault::ReleaseRejected, "release fault");
    check(!linked.verify(false), "verify mismatch");
    check(linked.record().state == TrampolineState::Retained && linked.pageRetained(), "retained");
    check(!linked.release(), "retained release rejected");
    check(linked.restoreEntry(true), "restore entry");
    check(linked.release() && linked.record().state == TrampolineState::Released, "restored release");
    check(linked.invariant(), "restored invariant");

    ExecutableTrampolineResource rollback;
    check(rollback.allocate(0x5000) && rollback.fill(64) && rollback.makeExecutable(), "rollback setup");
    check(rollback.linkEntry(0x6000), "rollback link");
    auto record = rollback.rollback(false);
    check(record.state == TrampolineState::Retained && record.pageRetained && record.entryLinked, "rollback retain");
    check(rollback.invariant(), "rollback invariant");
    record = rollback.rollback(true);
    check(record.state == TrampolineState::Released && !record.pageRetained && !record.entryLinked, "rollback restore");

    ExecutableTrampolineResource verified;
    check(verified.allocate(0x7000) && verified.fill(64) && verified.makeExecutable(), "verified setup");
    check(verified.linkEntry(0x8000) && verified.verify(true), "verified path");
    check(verified.record().state == TrampolineState::Verified && verified.record().entryLinked, "verified state");
    check(!verified.release(), "verified linked release rejected");
    check(verified.restoreEntry(true) && verified.release() && verified.invariant(), "verified release");
}

static void testRuntime()
{
    DisplayRuntime runtime;
    for (UInt32 major = 23; major <= 25; ++major) {
        const auto groups = requestedGroupsForDisplayFix(true, major) & MetalDisplayGroups;
        runtime.reset(0x1000, major, 0x1638, groups);
        check(!runtime.begin() && !runtime.ready(), "runtime no evidence no write");
        const auto features = runtime.features();
        const auto guard    = featureBit(FeatureId::DisplaySubmitGuard);
        check(!runtime.verify(features & ~guard, ExternalMetalBridge), "runtime missing guard");
        check(!runtime.begin(), "runtime cannot bypass guard rejection");
        check(runtime.verify(guard, ExternalSubmitResolver), "runtime complete evidence");
        check(runtime.begin() && runtime.ready(), "runtime eligible allows write");
        check(runtime.installed(guard), "runtime guard delivered");
        check(runtime.state(DisplayGroup::MetalSubmitSafety)
                  == (major == 23 ? DisplayGroupState::Installed : DisplayGroupState::Installing),
              "runtime partial safety group is not installed");
        check(runtime.state(DisplayGroup::MetalRenderCore) == DisplayGroupState::Installing,
              "runtime guard does not install render");
        check(runtime.installed(features), "runtime all delivered");
        check(runtime.state(DisplayGroup::MetalRenderCore) == DisplayGroupState::Installed,
              "runtime applicable render members installed");
        check(runtime.state(DisplayGroup::MetalSubmitSafety) == DisplayGroupState::Installed,
              "runtime safety installed");
        check(runtime.state(DisplayGroup::MetalScratch)
                  == (major == 23 ? DisplayGroupState::Disabled : DisplayGroupState::Installed),
              "runtime applicable scratch members installed");
    }
    runtime.reset(0x2000, 25, 0x1638, MetalDisplayGroups);
    check(!runtime.verify(runtime.features() & ~featureBit(FeatureId::DisplaySRD),
                          ExternalMetalBridge | ExternalSubmitResolver),
          "runtime missing render member");
    check(!runtime.begin(), "runtime missing dependency blocks scratch transaction");
    check(runtime.result().reasons[static_cast<UInt32>(DisplayGroup::MetalScratch)]
              == DisplayRejectReason::MissingDependency,
          "runtime scratch dependency rejected");
    check(runtime.verify(featureBit(FeatureId::DisplaySRD), 0) && runtime.begin(), "runtime resolve dependency");
    runtime.installed(featureBit(FeatureId::DisplaySubmitGuard));
    runtime.failed(false);
    check(!runtime.ready(), "runtime quarantine prevents continuation");
    check(runtime.state(DisplayGroup::MetalRenderCore) == DisplayGroupState::Quarantined,
          "runtime rollback failure quarantines render");
    check(runtime.state(DisplayGroup::MetalSubmitSafety) == DisplayGroupState::Quarantined,
          "runtime partial safety failure is quarantined");
    check(!runtime.installed(runtime.features()), "runtime cannot promote failed install");

    const auto gl = displayGroupBit(DisplayGroup::GLHwInfo);
    runtime.reset(0x3000, 25, 0x1638, gl);
    check(!runtime.ready(), "runtime new page cannot reuse prior state");
    check(!runtime.verify(runtime.features(), 0) && !runtime.begin(), "runtime missing page hook");
    check(runtime.verify(0, ExternalOpenGLPageHook) && runtime.begin(), "runtime page ready");
    runtime.failed(true);
    check(runtime.state(DisplayGroup::GLHwInfo) == DisplayGroupState::RollbackComplete && !runtime.ready(),
          "runtime recovered failure cannot continue");
    runtime.reset(0x4000, 25, 0xFFFF, gl);
    check(!runtime.verify(allFeatureBits, ExternalOpenGLPageHook) && !runtime.begin(), "runtime unsupported device");
    runtime.reset(0x5000, 26, 0x1638, gl);
    check(!runtime.verify(allFeatureBits, ExternalOpenGLPageHook) && !runtime.begin(), "runtime unsupported OS");
    runtime.reset(0x6000, 25, 0x1638, 0);
    check(!runtime.verify(allFeatureBits, ExternalOpenGLPageHook) && !runtime.begin(), "runtime disabled policy");
    runtime.reset(0, 25, 0x1638, gl);
    check(runtime.verify(allFeatureBits, ExternalOpenGLPageHook) && !runtime.begin(), "runtime missing address space");
}

int main()
{
    testPlanner();
    testLedger();
    testTrampoline();
    testRuntime();
    if (failures) {
        std::fprintf(stderr, "FAILED: %u checks\n", failures);
        return 1;
    }
    std::printf("PASS DVPF display state probe\n");
    return 0;
}
