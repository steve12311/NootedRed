// 独立 OpenGL 文件在签名验证后修正内存页；不修改磁盘文件或验证结果。
#include <Headers/kern_mach.hpp>
#include <Headers/kern_util.hpp>
#include <NRed.hpp>
#include <PenguinWizardry/KernelVersion.hpp>
#include <UserOpenGL.hpp>
#include <UserOpenGLResolver.hpp>
#include <kern/thread_call.h>
#include <sys/vnode.h>

namespace
{

    static_assert(sizeof(memory_object_t) == sizeof(void*));
    using PageValidator  = void (*)(vnode_t, memory_object_t, memory_object_offset_t, const void*, int*, int*, int*);
    using RangeValidator = boolean_t (*)(vnode_t, memory_object_t, memory_object_offset_t, const void*, vm_size_t,
                                         unsigned*);

    mach_vm_address_t             originalPage{0}, originalRange{0};
    const UserOpenGLData::Device* device{nullptr};
    thread_call_t                 reportCall{nullptr};
    UInt32                        appliedMask{0}, dynamicMask{0}, errorMask{0};
    constexpr UInt32              PageSize = UserOpenGLResolver::PageSize;

    // 仅在命中或写入失败后排队发布诊断；初始化不启动工作线程。
    __attribute__((noinline)) void publish(thread_call_param_t, thread_call_param_t)
    {
        const auto applied = __atomic_load_n(&appliedMask, __ATOMIC_RELAXED);
        const auto dynamic = __atomic_load_n(&dynamicMask, __ATOMIC_RELAXED);
        const auto errors  = __atomic_load_n(&errorMask, __ATOMIC_RELAXED);
        auto&      nred    = NRed::singleton();
        nred.setProp32("NRedOpenGLSEApplied", (applied & 1) != 0);
        nred.setProp32("NRedOpenGLDualQuadApplied", (applied & 2) != 0);
        nred.setProp32("NRedOpenGLDynamicApplied", dynamic == 3);
        nred.setProp32("NRedOpenGLWriteRejected", (errors & 1) != 0);
        nred.setProp32("NRedOpenGLRestoreFailed", (errors & 2) != 0);
    }

    void report(UInt32& mask, UInt32 bits)
    {
        if ((__atomic_fetch_or(&mask, bits, __ATOMIC_RELAXED) & bits) != bits) { thread_call_enter(reportCall); }
    }

    bool restoreWriting()
    {
        if (MachInfo::setKernelWriting(false, KernelPatcher::kernelWriteLock) == KERN_SUCCESS) { return true; }
        // Lilu 失败时也会释放锁；恢复重试必须重新配对加锁，不能重复解锁。
        if (MachInfo::setKernelWriting(true, KernelPatcher::kernelWriteLock) != KERN_SUCCESS) { return false; }
        return MachInfo::setKernelWriting(false, KernelPatcher::kernelWriteLock) == KERN_SUCCESS;
    }

    __attribute__((noinline)) void patchBlock(const UInt8* bytes, UInt64 offset, UInt32 kind)
    {
        UserOpenGLResolver::Resolved plan;
        if (!UserOpenGLResolver::resolvePage(*device, bytes, offset, kind, plan)) { return; }
        if (!plan.alreadyPatched) {
            if (MachInfo::setKernelWriting(true, KernelPatcher::kernelWriteLock) != KERN_SUCCESS) {
                report(errorMask, 1);
                return;
            }
            const bool fresh = memcmp(bytes + plan.offset, plan.original, plan.size) == 0;
            if (fresh) { memcpy(const_cast<UInt8*>(bytes) + plan.offset, plan.patched, plan.size); }
            if (!restoreWriting()) {
                report(errorMask, 2);
                return;
            }
            if (!fresh) { return; }
        }
        if (plan.dynamic) { report(dynamicMask, 1U << kind); }
        report(appliedMask, 1U << kind);
    }

    __attribute__((noinline)) void patchPage(vnode_t vnode, const void* data, UInt64 offset)
    {
        if (!device || !reportCall || !vnode || !data) { return; }
        const auto* name = vnode_getname(vnode);
        if (!name) { return; }
        const bool driver = strcmp(name, "AMDRadeonX5000GLDriver") == 0;
        vnode_putname(name);
        if (!driver) { return; }
        for (UInt32 kind = 0; kind < 2; ++kind) { patchBlock(static_cast<const UInt8*>(data), offset, kind); }
    }

    __attribute__((noinline)) void validatePage(vnode_t vnode, memory_object_t pager, memory_object_offset_t offset,
                                                const void* data, int* validated, int* tainted, int* nx)
    {
        reinterpret_cast<PageValidator>(originalPage)(vnode, pager, offset, data, validated, tainted, nx);
        // x86 的 4 KiB 整页验证可返回四位全真掩码，旧布尔返回仍为 1。
        if (validated && (*validated == 1 || *validated == 0xF) && tainted && *tainted == 0 && nx && *nx == 0
            && (offset & (PageSize - 1)) == 0)
        {
            patchPage(vnode, data, offset);
        }
    }

    __attribute__((noinline)) boolean_t validateRange(vnode_t vnode, memory_object_t pager,
                                                      memory_object_offset_t offset, const void* data, vm_size_t size,
                                                      unsigned* tainted)
    {
        const auto result = reinterpret_cast<RangeValidator>(originalRange)(vnode, pager, offset, data, size, tainted);
        if (!result || !tainted || *tainted != 0 || !data || (offset & (PageSize - 1)) != 0
            || offset > UserOpenGLResolver::MaxFileSize || size > UserOpenGLResolver::MaxFileSize - offset)
        {
            return result;
        }
        for (vm_size_t position = 0; position < size && PageSize <= size - position; position += PageSize) {
            patchPage(vnode, static_cast<const UInt8*>(data) + position, offset + position);
        }
        return result;
    }

}    // namespace

void UserOpenGL::init(KernelPatcher& patcher)
{
    if (!checkKernelArgument("-NRedDisplayFix")) { return; }
    device = UserOpenGLData::findDevice(currentKernelVersion().major(), NRed::singleton().getDeviceID());
    if (!device) { return; }
    reportCall =
        thread_call_allocate_with_options(publish, nullptr, THREAD_CALL_PRIORITY_KERNEL, THREAD_CALL_OPTIONS_ONCE);
    if (!reportCall) {
        NRed::singleton().setProp32("NRedOpenGLRejected", 1);
        return;
    }
    UInt32                      backends{0};
    KernelPatcher::RouteRequest pageRequest{"_cs_validate_page", validatePage, originalPage};
    if (patcher.routeMultipleLong(KernelPatcher::KernelID, &pageRequest, 1)) { backends |= 1; }
    patcher.clearError();
    KernelPatcher::RouteRequest rangeRequest{"_cs_validate_range", validateRange, originalRange};
    if (patcher.routeMultipleLong(KernelPatcher::KernelID, &rangeRequest, 1)) { backends |= 2; }
    patcher.clearError();
    if (!backends) {
        thread_call_free(reportCall);
        reportCall = nullptr;
        NRed::singleton().setProp32("NRedOpenGLRejected", 2);
        return;
    }
    NRed::singleton().setProp32("NRedOpenGLRevision", 5);
    NRed::singleton().setProp32("NRedOpenGLPageLocal", 1);
    NRed::singleton().setProp32("NRedOpenGLEnabled", 1);
    NRed::singleton().setProp32("NRedOpenGLBackends", backends);
}
