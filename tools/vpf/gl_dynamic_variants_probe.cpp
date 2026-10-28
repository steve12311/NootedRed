// DVPF：参考页迁移后仍走同页完整块动态匹配；不接受固定偏移 hint。
#include <VPF/DisplayManifestData.hpp>
#include <VPF/GLManifestData.hpp>
#include <VPF/ValidatedPageBackend.hpp>
#include <array>
#include <cstdio>
#include <cstring>

namespace
{

    unsigned failures = 0;
    unsigned checks   = 0;

    void check(const bool condition, const char* label)
    {
        ++checks;
        if (!condition) {
            std::fprintf(stderr, "FAIL: %s\n", label);
            ++failures;
        }
    }

    std::array<UInt8, VPF::DisplayPageSize> moved(const UserOpenGLData::Device& device, const UInt32 kind,
                                                  const UInt64 page, const UInt32 offset, const bool patched)
    {
        std::array<UInt8, VPF::DisplayPageSize> bytes;
        bytes.fill(0xCC);
        const auto& patch = device.patches[kind];
        const auto& fn    = device.resolver->functions[kind];
        const auto  start = device.resolver->locations[kind].offset;
        memcpy(bytes.data() + offset, patched ? patch.patched : patch.original, patch.size);
        const auto* refs  = patched ? fn.patchedRefs : fn.originalRefs;
        const auto  count = patched ? fn.patchedCount : fn.originalCount;
        for (UInt32 r = 0; r < count; ++r) {
            check(
                UserSharedCache::setRelative(bytes.data() + offset, refs[r].offset - start, page + offset, refs[r].rva),
                "rel32 rebuild");
        }
        return bytes;
    }

    void testDevice(const UserOpenGLData::Device& device)
    {
        if (device.major != 24) { return; }
        for (UInt32 kind = 0; kind < 2; ++kind) {
            const auto* manifest = VPF::DisplayManifestData::findOpenGL(device.major, device.deviceID, kind);
            const auto* evidence = VPF::GLManifestData::find(device.major, device.deviceID, kind);
            check(manifest != nullptr && evidence != nullptr, "manifest/evidence lookup");
            if (!manifest || !evidence) { continue; }
            const auto& page       = device.pages[kind];
            const auto& fn         = device.resolver->functions[kind];
            const auto  start      = device.resolver->locations[kind].offset;
            const auto  fileOffset = evidence->pageFileOffset;

            std::array<UInt8, VPF::DisplayPageSize> exact;
            memcpy(exact.data(), page.original, VPF::DisplayPageSize);
            VPF::ValidatedPageWorkspace workspace;
            VPF::OpenGLPageContext      context{1, 0, 0, fileOffset, true};
            check(VPF::inspectValidatedPage(*manifest, device, kind, exact.data(), fileOffset, context, workspace),
                  "exact evidence plan");
            check(workspace.plan.exactPage() && !workspace.plan.dynamic() && workspace.evidenceVerified,
                  "exact plan identity");
            check(workspace.plan.evidence() == VPF::DisplayEvidence::ExactKnownPage, "exact evidence kind");
            check(VPF::GLManifestData::digest(workspace.resolved.original, workspace.resolved.size, fn.originalRefs,
                                              fn.originalCount, start)
                      == evidence->originalDigest,
                  "exact evidence digest");

            const UInt64 pageBase = 0x40000;
            const UInt32 offset   = 0x300 + kind * 0x180;
            const auto   dynamic  = moved(device, kind, pageBase, offset, false);
            context               = {0xF, 0, 0, pageBase, true};
            check(VPF::inspectValidatedPage(*manifest, device, kind, dynamic.data(), pageBase, context, workspace),
                  "dynamic evidence plan");
            check(workspace.plan.dynamic() && !workspace.plan.exactPage(), "dynamic plan identity");
            check(workspace.plan.evidence() == VPF::DisplayEvidence::FullPageLocalBlock, "dynamic evidence kind");
            check(workspace.plan.patchFileOffset() == pageBase + offset, "dynamic plan offset");
            check(workspace.plan.patchFileOffset() != evidence->pageFileOffset + evidence->blockOffsetInPage,
                  "hint is not a fixed offset");
            check(VPF::GLManifestData::digest(workspace.resolved.original, workspace.resolved.size, fn.originalRefs,
                                              fn.originalCount, start)
                      == evidence->originalDigest,
                  "dynamic evidence digest");

            const auto patched = moved(device, kind, pageBase, offset, true);
            check(VPF::inspectValidatedPage(*manifest, device, kind, patched.data(), pageBase, context, workspace),
                  "dynamic patched plan");
            check(workspace.plan.alreadyInstalled() && workspace.plan.dynamic(), "dynamic patched state");
            check(VPF::GLManifestData::digest(workspace.resolved.original, workspace.resolved.size, fn.patchedRefs,
                                              fn.patchedCount, start)
                      == evidence->patchedDigest,
                  "dynamic patched digest");

            auto mutated     = dynamic;
            mutated[offset] ^= 0x01;
            check(!VPF::inspectValidatedPage(*manifest, device, kind, mutated.data(), pageBase, context, workspace),
                  "reject changed block");
            auto       duplicate = dynamic;
            const auto second    = moved(device, kind, pageBase, 0x800, false);
            memcpy(duplicate.data() + 0x800, second.data() + 0x800, device.patches[kind].size);
            check(!VPF::inspectValidatedPage(*manifest, device, kind, duplicate.data(), pageBase, context, workspace),
                  "reject duplicate candidates");
            auto        badRef         = dynamic;
            const auto& ref            = fn.originalRefs[0];
            const auto  relative       = ref.offset - start;
            badRef[offset + relative] ^= 0x7F;
            check(!VPF::inspectValidatedPage(*manifest, device, kind, badRef.data(), pageBase, context, workspace),
                  "reject bad rel32");
            std::array<UInt8, VPF::DisplayPageSize> truncated;
            truncated.fill(0xCC);
            memcpy(truncated.data() + VPF::DisplayPageSize - (device.patches[kind].size - 1),
                   device.patches[kind].original, device.patches[kind].size - 1);
            check(!VPF::inspectValidatedPage(*manifest, device, kind, truncated.data(), pageBase, context, workspace),
                  "reject cross page");
        }
    }

}    // namespace

int main()
{
    for (const auto& device : UserOpenGLData::Devices) { testDevice(device); }
    if (failures) {
        std::fprintf(stderr, "FAILED: %u / %u checks\n", failures, checks);
        return 1;
    }
    std::printf("PASS %u GL dynamic variant checks: reference, moved, patched, duplicate/rel32/cross-page rejection\n",
                checks);
    return 0;
}
