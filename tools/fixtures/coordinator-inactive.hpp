// 测试夹具：模拟协调器未接管时的旧路径；真实协调器由 test_display_cross_module_gate 编译。
#pragma once
namespace UserPatchCoordinator
{
    using PatchCallback = int (*)(void*, void*, int);
    inline bool deferModulePatches() { return false; }
    inline void clearDisplay() {}
    inline void clearVideo() {}
    inline void registerDisplay(const PatchCallback) {}
    inline void registerVideo(const PatchCallback) {}
}    // namespace UserPatchCoordinator
