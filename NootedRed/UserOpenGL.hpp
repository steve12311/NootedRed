// OpenGL 的 SE/CU 与条件 DualQuad 修复，统一由显示开关启用。
#pragma once
#include <Headers/kern_patcher.hpp>

namespace UserOpenGL { void init(KernelPatcher& patcher); }
