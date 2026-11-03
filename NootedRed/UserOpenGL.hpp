// OpenGL 的 SE/CU 与条件 DualQuad 修复，统一由显示开关启用。
#pragma once
#include <Headers/kern_patcher.hpp>

namespace UserOpenGL
{

    void registerKext();
    void init(KernelPatcher& patcher);
    void processKext(KernelPatcher& patcher, size_t id, mach_vm_address_t slide, size_t size);

}    // namespace UserOpenGL
