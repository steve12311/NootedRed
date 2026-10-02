// 精确版本的 Metal 同步、资源描述符和混合兼容修复。
#pragma once
#include <Headers/kern_patcher.hpp>

namespace UserSurfaceSync {
void init(KernelPatcher& patcher);
}
