// DVPF：OpenGL 已验证页后端；只读计划与页内最小写入分离。
// 本文件不解析 Mach-O、不读取文件、不跨页，也不申请 MapGate。
#pragma once
#include <VPF/DisplayPlan.hpp>
#include <VPF/DisplayPlanAdapters.hpp>
#include <VPF/GLManifestData.hpp>

namespace VPF
{

    // 交付结果只描述当前 4 KiB 验证页，不等同于 Metal 的 per-map 安装状态。
    enum class PagePatchOutcome : UInt8
    {
        Invalid,
        AlreadyInstalled,
        Delivered,
        ChangedBeforeWrite,
        WriteFailed,
        RestoreFailed,
    };

    constexpr const char* pagePatchOutcomeName(const PagePatchOutcome outcome)
    {
        switch (outcome) {
            case PagePatchOutcome::AlreadyInstalled  : return "AlreadyInstalled";
            case PagePatchOutcome::Delivered         : return "Delivered";
            case PagePatchOutcome::ChangedBeforeWrite: return "ChangedBeforeWrite";
            case PagePatchOutcome::WriteFailed       : return "WriteFailed";
            case PagePatchOutcome::RestoreFailed     : return "RestoreFailed";
            default                                  : return "Invalid";
        }
    }

    // 计划引用 workspace.resolved；workspace 不得复制或越过当前页调用生命周期。
    // resolved 由 resolvePage 成功时完整填充；失败路径不会读取，避免每次页入清零 524 字节。
    struct ValidatedPageWorkspace
    {
        UserOpenGLResolver::Resolved resolved;
        VerifiedPagePlan             plan;
        bool                         evidenceVerified;
    };

    // 只读：解析当前页并生成 VPF 页计划；resolvePage 仍是唯一的动态候选扫描器。
    __attribute__((noinline)) inline bool inspectValidatedPage(const DisplayManifest&        manifest,
                                                               const UserOpenGLData::Device& device, const UInt32 kind,
                                                               const UInt8* bytes, const UInt64 fileOffset,
                                                               const OpenGLPageContext& context,
                                                               ValidatedPageWorkspace&  workspace)
    {
        workspace.evidenceVerified = false;
        if (!UserOpenGLResolver::resolvePage(device, bytes, fileOffset, kind, workspace.resolved)) { return false; }
        if (!adaptOpenGLPage(workspace.plan, manifest, device, workspace.resolved, context)) { return false; }
        const auto* evidence = GLManifestData::find(device.major, device.deviceID, kind);
        if (evidence == nullptr || evidence->page == nullptr || evidence->original == nullptr
            || evidence->patched == nullptr || evidence->blockSize != workspace.resolved.size
            || evidence->functionIndex != kind || evidence->instructionOffset != manifest.pageSites[0].instructionOffset
            || (!workspace.resolved.dynamic
                && (fileOffset != evidence->pageFileOffset
                    || workspace.resolved.offset != evidence->blockOffsetInPage)))
        {
            return false;
        }
        const auto& function = device.resolver->functions[kind];
        const auto  start    = device.resolver->locations[kind].offset;
        const bool  patched  = workspace.plan.alreadyInstalled();
        const auto* refs     = patched ? function.patchedRefs : function.originalRefs;
        const auto  count    = patched ? function.patchedCount : function.originalCount;
        const auto  digest =
            GLManifestData::digest(workspace.resolved.original, workspace.resolved.size, refs, count, start);
        if (digest != (patched ? evidence->patchedDigest : evidence->originalDigest)) { return false; }
        workspace.evidenceVerified = true;
        return true;
    }

    // 写入前重新核对当前字节；beginWrite/endWrite 由调用方提供真实的写保护与恢复语义。
    // 返回 ChangedBeforeWrite 时仍已调用 endWrite，且未写入任何字节。
    template<typename BeginWrite, typename EndWrite>
    __attribute__((noinline)) PagePatchOutcome applyValidatedPage(const ValidatedPageWorkspace& workspace, UInt8* bytes,
                                                                  BeginWrite&& beginWrite, EndWrite&& endWrite)
    {
        if (!workspace.plan.valid() || bytes == nullptr) { return PagePatchOutcome::Invalid; }
        if (workspace.plan.alreadyInstalled()) { return PagePatchOutcome::AlreadyInstalled; }
        if (!beginWrite()) { return PagePatchOutcome::WriteFailed; }
        const auto offset = workspace.resolved.offset;
        const auto size   = workspace.resolved.size;
        const bool fresh  = memcmp(bytes + offset, workspace.plan.original(), size) == 0;
        if (fresh) { memcpy(bytes + offset, workspace.plan.replacement(), size); }
        if (!endWrite()) { return PagePatchOutcome::RestoreFailed; }
        return fresh ? PagePatchOutcome::Delivered : PagePatchOutcome::ChangedBeforeWrite;
    }

}    // namespace VPF
