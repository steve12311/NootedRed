// DVPF：把既有 OpenGL/Metal Resolver 输出规范化为只读显示计划；不改变旧执行路径。
#pragma once
#include <VPF/DisplayPlan.hpp>

namespace VPF
{

    inline bool adaptOpenGLPage(VerifiedPagePlan& out, const DisplayManifest& manifest,
                                const UserOpenGLData::Device& device, const UserOpenGLResolver::Resolved& resolved,
                                const OpenGLPageContext& context)
    {
        return out.reset(manifest, device, resolved, context);
    }

    // 共享缓存计划已由 adaptSurface/adaptPatches 核验站点；这里只核对显示清单绑定。
    inline bool adaptDisplayShared(DisplayPlanView& out, const DisplayManifest& manifest, const VerifiedPatchPlan& plan)
    {
        out          = {};
        out.manifest = &manifest;
        out.shared   = &plan;
        return out.valid();
    }

    inline bool adaptDisplayPage(DisplayPlanView& out, const VerifiedPagePlan& page)
    {
        out          = {};
        out.manifest = page.manifest();
        out.page     = &page;
        return out.valid();
    }

}    // namespace VPF
