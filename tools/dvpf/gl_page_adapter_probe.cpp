// DVPF：对照旧 resolvePage/写入路径与新 ValidatedPageBackend。
// 只使用合成 4 KiB 页和生成表，不读取驱动文件，也不触碰内核锁。
#include <VPF/DisplayManifestData.hpp>
#include <VPF/ValidatedPageBackend.hpp>
#include <array>
#include <cstdio>
#include <cstring>
#include <functional>

using VPF::DisplayManifest;
using VPF::OpenGLPageContext;
using VPF::PagePatchOutcome;
using VPF::ValidatedPageWorkspace;

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

    struct Writer
    {
        unsigned              begins{0}, ends{0};
        unsigned              failBegin{0}, failEnd{0};
        std::function<void()> onBegin{};

        bool begin()
        {
            ++begins;
            if (begins == failBegin) { return false; }
            if (onBegin) { onBegin(); }
            return true;
        }

        bool end()
        {
            ++ends;
            return ends != failEnd;
        }
    };

    enum class OldOutcome
    {
        AlreadyInstalled,
        Delivered,
        ChangedBeforeWrite,
        WriteFailed,
        RestoreFailed,
    };

    OldOutcome oldApply(const UserOpenGLResolver::Resolved& plan, UInt8* bytes, Writer& writer)
    {
        if (plan.alreadyPatched) { return OldOutcome::AlreadyInstalled; }
        if (!writer.begin()) { return OldOutcome::WriteFailed; }
        const bool fresh = memcmp(bytes + plan.offset, plan.original, plan.size) == 0;
        if (fresh) { memcpy(bytes + plan.offset, plan.patched, plan.size); }
        if (!writer.end()) { return OldOutcome::RestoreFailed; }
        return fresh ? OldOutcome::Delivered : OldOutcome::ChangedBeforeWrite;
    }

    PagePatchOutcome newApply(const ValidatedPageWorkspace& workspace, UInt8* bytes, Writer& writer)
    {
        return VPF::applyValidatedPage(workspace, bytes, [&] { return writer.begin(); }, [&] { return writer.end(); });
    }

    OldOutcome newAsOld(const PagePatchOutcome outcome)
    {
        switch (outcome) {
            case PagePatchOutcome::AlreadyInstalled  : return OldOutcome::AlreadyInstalled;
            case PagePatchOutcome::Delivered         : return OldOutcome::Delivered;
            case PagePatchOutcome::ChangedBeforeWrite: return OldOutcome::ChangedBeforeWrite;
            case PagePatchOutcome::WriteFailed       : return OldOutcome::WriteFailed;
            case PagePatchOutcome::RestoreFailed     : return OldOutcome::RestoreFailed;
            default                                  : return OldOutcome::WriteFailed;
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

    void compareApply(const UserOpenGLData::Device& device, const UInt32 kind, const DisplayManifest& manifest,
                      const std::array<UInt8, VPF::DisplayPageSize>& input, const UInt64 fileOffset, const bool patched,
                      const unsigned failBegin, const unsigned failEnd, const bool changeBeforeWrite, const char* label)
    {
        UserOpenGLResolver::Resolved oldPlan{};
        check(UserOpenGLResolver::resolvePage(device, input.data(), fileOffset, kind, oldPlan), label);
        OpenGLPageContext      context{1, 0, 0, fileOffset, true};
        ValidatedPageWorkspace workspace;
        check(VPF::inspectValidatedPage(manifest, device, kind, input.data(), fileOffset, context, workspace), label);
        check(workspace.evidenceVerified && workspace.plan.valid(), label);
        check(oldPlan.offset == workspace.plan.patchFileOffset() - fileOffset, label);
        check(oldPlan.size == workspace.plan.size(), label);
        check(oldPlan.dynamic == workspace.plan.dynamic(), label);
        check(oldPlan.alreadyPatched == workspace.plan.alreadyInstalled(), label);
        check(memcmp(oldPlan.original, workspace.plan.original(), oldPlan.size) == 0, label);
        check(memcmp(oldPlan.patched, workspace.plan.replacement(), oldPlan.size) == 0, label);
        check(workspace.plan.state() == (patched ? VPF::PlanState::AllPatched : VPF::PlanState::AllOriginal), label);
        check(
            workspace.plan.evidence()
                == (oldPlan.dynamic ? VPF::DisplayEvidence::FullPageLocalBlock : VPF::DisplayEvidence::ExactKnownPage),
            label);

        auto   oldBytes = input;
        auto   newBytes = input;
        Writer oldWriter;
        oldWriter.failBegin = failBegin;
        oldWriter.failEnd   = failEnd;
        Writer newWriter;
        newWriter.failBegin = failBegin;
        newWriter.failEnd   = failEnd;
        const auto offset   = oldPlan.offset;
        if (changeBeforeWrite) {
            oldWriter.onBegin = [&] { oldBytes[offset] ^= 1; };
            newWriter.onBegin = [&] { newBytes[offset] ^= 1; };
        }
        const auto oldOutcome = oldApply(oldPlan, oldBytes.data(), oldWriter);
        const auto newOutcome = newApply(workspace, newBytes.data(), newWriter);
        check(newAsOld(newOutcome) == oldOutcome, label);
        check(oldBytes == newBytes, label);
        check(oldWriter.begins == newWriter.begins && oldWriter.ends == newWriter.ends, label);
        if (oldOutcome == OldOutcome::AlreadyInstalled) { check(newWriter.begins == 0 && newWriter.ends == 0, label); }
        else if (oldOutcome == OldOutcome::WriteFailed) {
            check(newWriter.ends == 0, label);
        }
        else {
            check(newWriter.ends == 1, label);
        }
    }

    void testDevice(const UserOpenGLData::Device& device)
    {
        for (UInt32 kind = 0; kind < 2; ++kind) {
            const auto* manifest = VPF::DisplayManifestData::findOpenGL(device.major, device.deviceID, kind);
            check(manifest != nullptr, "manifest lookup");
            if (!manifest) { continue; }
            const auto& patch      = device.patches[kind];
            const auto& page       = device.pages[kind];
            const auto  fileOffset = patch.offset - page.offset;

            std::array<UInt8, VPF::DisplayPageSize> exact;
            memcpy(exact.data(), page.original, VPF::DisplayPageSize);
            compareApply(device, kind, *manifest, exact, fileOffset, false, 0, 0, false, "exact");

            auto patchedPage = exact;
            memcpy(patchedPage.data() + page.offset, patch.patched, patch.size);
            compareApply(device, kind, *manifest, patchedPage, fileOffset, true, 0, 0, false, "patched");

            const UInt32 offset =
                0x200
                + (static_cast<UInt32>(device.deviceID) + kind * 257) % (VPF::DisplayPageSize - patch.size - 0x200);
            const UInt64 pageBase = 0x40000;
            const auto   dynamic  = moved(device, kind, pageBase, offset, false);
            compareApply(device, kind, *manifest, dynamic, pageBase, false, 0, 0, false, "dynamic");

            const auto dynamicPatched = moved(device, kind, pageBase, offset, true);
            compareApply(device, kind, *manifest, dynamicPatched, pageBase, true, 0, 0, false, "dynamic patched");

            compareApply(device, kind, *manifest, exact, fileOffset, false, 1, 0, false, "write failed");
            compareApply(device, kind, *manifest, exact, fileOffset, false, 0, 1, false, "restore failed");
            compareApply(device, kind, *manifest, exact, fileOffset, false, 0, 0, true, "changed before write");

            // 原生验证、taint、NX、vnode、偏移和错误设备清单必须拒绝。
            const OpenGLPageContext rejected[] = {
                {0, 0, 0, fileOffset, true}, {2, 0, 0, fileOffset, true},  {0xF, 1, 0, fileOffset, true},
                {1, 0, 1, fileOffset, true}, {1, 0, 0, fileOffset, false}, {1, 0, 0, fileOffset + 1, true},
            };
            for (const auto& context : rejected) {
                ValidatedPageWorkspace workspace;
                check(!VPF::inspectValidatedPage(*manifest, device, kind, exact.data(), fileOffset, context, workspace),
                      "reject context");
                check(!workspace.plan.valid() && !workspace.evidenceVerified, "reject leaves invalid plan");
            }
            const auto  other         = device.deviceID == 0x15D8 ? 0x15DD : 0x15D8;
            const auto* wrongManifest = VPF::DisplayManifestData::findOpenGL(device.major, other, kind);
            if (wrongManifest) {
                ValidatedPageWorkspace workspace;
                OpenGLPageContext      context{1, 0, 0, fileOffset, true};
                check(!VPF::inspectValidatedPage(*wrongManifest, device, kind, exact.data(), fileOffset, context,
                                                 workspace),
                      "reject wrong manifest");
            }

            // 同页两个候选、页末无法完整容纳的块和错误 kind 都必须拒绝。
            auto       ambiguous  = moved(device, kind, pageBase, 0x200, false);
            const auto otherBlock = moved(device, kind, pageBase, 0x800, true);
            memcpy(ambiguous.data() + 0x800, otherBlock.data() + 0x800, patch.size);
            UserOpenGLResolver::Resolved duplicate{};
            check(!UserOpenGLResolver::resolvePage(device, ambiguous.data(), pageBase, kind, duplicate),
                  "reject ambiguous");
            std::array<UInt8, VPF::DisplayPageSize> truncated;
            truncated.fill(0xCC);
            memcpy(truncated.data() + VPF::DisplayPageSize - (patch.size - 1), patch.original, patch.size - 1);
            check(!UserOpenGLResolver::resolvePage(device, truncated.data(), pageBase, kind, duplicate),
                  "reject cross page");
            check(!UserOpenGLResolver::resolvePage(device, exact.data(), fileOffset, 2, duplicate), "reject kind");
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
    std::printf("PASS %u OpenGL VPF page adapter checks: exact/dynamic/patched, write failures and rejections\n",
                checks);
    return 0;
}
