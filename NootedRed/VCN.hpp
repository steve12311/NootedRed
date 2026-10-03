// Cezanne VCN2 接入；保持默认关闭，具体状态由真实初始化路径发布。
#pragma once
#include <Headers/kern_patcher.hpp>

namespace VCN
{

    void  init();
    void  processKext(KernelPatcher& patcher, size_t id, mach_vm_address_t slide, size_t size);
    bool  ready();
    bool  allocated();
    void* allocateEngine();

}    // namespace VCN
